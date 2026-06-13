"""Spark Structured Streaming job for the weather monitoring pipeline.

Consumes JSON weather readings from Kafka and runs three streaming queries:

  Q1  raw      -> cassandra.raw_weather_data        (every reading, persisted)
  Q2  windowed -> cassandra.aggregated_weather       (sliding-window aggregates)
  Q3  alerts   -> cassandra.weather_alerts           (threshold breaches)

Thresholds are read from the shared config so the alerting bands are not
hard-coded. Q3 uses foreachBatch so we can both write alerts to Cassandra and
push them to a Kafka alerts topic for any downstream consumer.
"""

import configparser

from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    from_json, col, window, avg, sum as _sum, max as _max, count,
    when, lit, concat, round as _round, to_json, struct, current_timestamp,
)
from pyspark.sql.types import (
    StructType, StructField, StringType, DoubleType, TimestampType,
)


def get_config(filepath="/config/project_config.ini"):
    config = configparser.ConfigParser()
    config.read(filepath)
    return config


def parse_threshold(raw):
    """'warn_low,crit_low,warn_high,crit_high' with '-' as None -> tuple."""
    parts = [p.strip() for p in raw.split(",")]
    return tuple(None if p == "-" else float(p) for p in parts)


def parse_bounds(raw):
    """'min,max' -> (float, float) physical-plausibility bounds."""
    lo, hi = [p.strip() for p in raw.split(",")]
    return float(lo), float(hi)


def valid_condition(bounds):
    """Boolean Column: row is valid iff key fields are non-null and every
    parameter is non-null and within its physical bounds.

    This is the data-quality / validation+filtering stage required of the
    Spark layer: it screens out corrupt or out-of-instrument-range readings
    (e.g. a faulted sensor reporting 55 C) before they pollute the aggregates.
    """
    cond = col("station_id").isNotNull() & col("timestamp").isNotNull()
    for param, (lo, hi) in bounds.items():
        cond = cond & col(param).isNotNull() & (col(param) >= lo) & (col(param) <= hi)
    return cond


def build_alerts(batch_df, thresholds):
    """Return a DataFrame of alert rows for readings breaching thresholds.

    For each parameter we emit at most one alert row per reading, tagged
    'warning' or 'critical'. Implemented as a union of per-parameter selects.
    """
    alert_frames = []
    units = {
        "temperature": "C", "humidity": "%", "pressure": "hPa",
        "precipitation": "mm/h", "wind_speed": "m/s",
    }

    for param, (warn_lo, crit_lo, warn_hi, crit_hi) in thresholds.items():
        severity = lit(None).cast(StringType())

        # critical first, then warning (when() picks the first truthy branch)
        cond_crit = lit(False)
        cond_warn = lit(False)
        if crit_lo is not None:
            cond_crit = cond_crit | (col(param) < crit_lo)
        if crit_hi is not None:
            cond_crit = cond_crit | (col(param) > crit_hi)
        if warn_lo is not None:
            cond_warn = cond_warn | (col(param) < warn_lo)
        if warn_hi is not None:
            cond_warn = cond_warn | (col(param) > warn_hi)

        severity = when(cond_crit, lit("critical")).when(cond_warn, lit("warning"))

        frame = batch_df.select(
            col("station_id"),
            col("timestamp").alias("ts"),
            lit(param).alias("parameter"),
            col(param).cast(DoubleType()).alias("value"),
            severity.alias("severity"),
        ).where(col("severity").isNotNull())

        frame = frame.withColumn(
            "message",
            concat(
                col("severity"), lit(" "), lit(param), lit(": "),
                _round(col("value"), 2).cast(StringType()), lit(" " + units.get(param, "")),
            ),
        )
        alert_frames.append(frame)

    result = alert_frames[0]
    for f in alert_frames[1:]:
        result = result.unionByName(f)
    return result


def main():
    config = get_config()
    kafka_cfg = config["kafka"]
    cass_cfg = config["cassandra"]
    spark_cfg = config["spark"]

    thresholds = {p: parse_threshold(config["thresholds"][p]) for p in config["thresholds"]}
    val_bounds = {p: parse_bounds(config["validation"][p]) for p in config["validation"]}

    spark = (SparkSession.builder
             .appName(spark_cfg["app_name"])
             # Run 3 concurrent streaming queries without starving each other:
             # enough cores + FAIR scheduling so no single query hogs all tasks.
             .master("local[6]")
             .config("spark.scheduler.mode", "FAIR")
             # Default is 200 — far too many for a local windowed aggregation and
             # a frequent cause of micro-batch stalls. Keep it small.
             .config("spark.sql.shuffle.partitions", "4")
             .config("spark.cassandra.connection.host", cass_cfg["hosts"])
             .config("spark.cassandra.connection.port", cass_cfg["port"])
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    print("Spark session created.")

    # Built after the session so the JVM gateway backing col() is available.
    is_valid = valid_condition(val_bounds)

    schema = StructType([
        StructField("station_id", StringType(), True),
        StructField("timestamp", TimestampType(), True),
        StructField("temperature", DoubleType(), True),
        StructField("humidity", DoubleType(), True),
        StructField("pressure", DoubleType(), True),
        StructField("precipitation", DoubleType(), True),
        StructField("wind_speed", DoubleType(), True),
    ])

    kafka_df = (spark.readStream
                .format("kafka")
                .option("kafka.bootstrap.servers", kafka_cfg["bootstrap_servers"])
                .option("subscribe", kafka_cfg["topic_name"])
                .option("startingOffsets", "latest")
                # Cap batch size so a sudden burst of messages can't create one
                # huge micro-batch that stalls the stateful windowed query.
                .option("maxOffsetsPerTrigger", "1000")
                # --- Resilience to transient broker blips ---------------------
                # A brief Kafka unavailability was hard-killing the query with
                # "Timeout ... before the position for partition ... determined".
                # Give the consumer generous timeouts and retries, and don't fail
                # the whole stream on a recoverable data-loss/offset condition.
                .option("kafka.request.timeout.ms", "120000")
                .option("kafka.default.api.timeout.ms", "120000")
                .option("kafka.session.timeout.ms", "30000")
                .option("kafka.fetch.max.wait.ms", "1000")
                .option("kafkaConsumer.pollTimeoutMs", "120000")
                .option("failOnDataLoss", "false")
                .load())

    parsed = (kafka_df
              .select(from_json(col("value").cast("string"), schema).alias("d"))
              .select("d.*"))

    # --- Query A: raw readings + alerts in ONE micro-batch ----------------
    # Running raw and alerting together (rather than as two separate streaming
    # queries) keeps the number of concurrent queries low — multiple concurrent
    # micro-batch queries can starve/deadlock each other in a local driver.
    def write_raw_and_alerts(batch_df, batch_id):
        batch_df = batch_df.persist()
        try:
            n = batch_df.count()
            if n == 0:
                return

            # 1) raw -> Cassandra
            (batch_df.select(
                col("station_id"), col("timestamp").alias("ts"),
                col("temperature"), col("humidity"), col("pressure"),
                col("precipitation"), col("wind_speed"))
             .write
             .format("org.apache.spark.sql.cassandra")
             .options(keyspace=cass_cfg["keyspace"], table=cass_cfg["raw_data_table"])
             .mode("append")
             .save())

            # 2) alerts -> Cassandra (+ Kafka alerts topic)
            alerts = build_alerts(batch_df, thresholds).persist()
            n_alerts = alerts.count()
            if n_alerts:
                (alerts.write
                    .format("org.apache.spark.sql.cassandra")
                    .options(keyspace=cass_cfg["keyspace"], table=cass_cfg["alerts_table"])
                    .mode("append")
                    .save())
                (alerts.select(
                    col("station_id").alias("key"),
                    to_json(struct("station_id", "ts", "parameter", "value",
                                   "severity", "message")).alias("value"))
                 .write
                 .format("kafka")
                 .option("kafka.bootstrap.servers", kafka_cfg["bootstrap_servers"])
                 .option("topic", kafka_cfg["alerts_topic"])
                 .save())
            alerts.unpersist()

            # Data-quality metric: how many readings in this batch fail the
            # physical-plausibility validation (corrupt / out-of-range sensors).
            n_invalid = batch_df.where(~is_valid).count()
            dq = f", {n_invalid} invalid (filtered from aggregates)" if n_invalid else ""
            print(f"Batch {batch_id}: wrote {n} reading(s), {n_alerts} alert(s){dq}.")
        finally:
            batch_df.unpersist()

    raw_query = (parsed.writeStream
                 .foreachBatch(write_raw_and_alerts)
                 .outputMode("append")
                 .trigger(processingTime="5 seconds")
                 .option("checkpointLocation", "/tmp/checkpoints/raw")
                 .start())
    print("Query A (raw + alerts) started.")

    # --- Query B: sliding-window aggregates -> Cassandra -----------------
    # Aggregate ONLY the validated stream so a faulted sensor reading cannot
    # skew the windowed averages (bronze raw keeps everything; silver = valid).
    agg = (parsed
           .where(is_valid)
           .withWatermark("timestamp", spark_cfg["watermark"])
           .groupBy(
               window(col("timestamp"), spark_cfg["window_size"], spark_cfg["slide_interval"]).alias("w"),
               col("station_id"))
           .agg(
               avg("temperature").alias("avg_temperature"),
               avg("humidity").alias("avg_humidity"),
               avg("pressure").alias("avg_pressure"),
               _sum("precipitation").alias("total_precipitation"),
               _max("wind_speed").alias("max_wind_speed"),
               count(lit(1)).alias("reading_count"))
           .select(
               col("station_id"),
               col("w.start").alias("window_start"),
               col("w.end").alias("window_end"),
               col("avg_temperature"), col("avg_humidity"), col("avg_pressure"),
               col("total_precipitation"), col("max_wind_speed"), col("reading_count")))

    def write_agg(batch_df, batch_id):
        if batch_df.rdd.isEmpty():
            return
        (batch_df.write
            .format("org.apache.spark.sql.cassandra")
            .options(keyspace=cass_cfg["keyspace"], table=cass_cfg["aggregated_data_table"])
            .mode("append")
            .save())
        print(f"Query B batch {batch_id}: wrote {batch_df.count()} window aggregate(s).")

    agg_query = (agg.writeStream
                 .foreachBatch(write_agg)
                 .outputMode("append")
                 .trigger(processingTime="15 seconds")
                 .option("checkpointLocation", "/tmp/checkpoints/agg")
                 .start())
    print("Query B (windowed aggregation) started.")

    spark.streams.awaitAnyTermination()


if __name__ == "__main__":
    main()
