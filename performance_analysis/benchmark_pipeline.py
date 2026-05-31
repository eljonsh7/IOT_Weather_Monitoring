"""Benchmarks the ACTUAL pipeline (not pandas).

Run from the host against the running stack (Kafka on localhost:29092,
Cassandra on localhost:9042):

    python benchmark_pipeline.py

Measures:
  1. Kafka producer throughput (messages/sec) at increasing volumes.
  2. Cassandra write latency — UNPREPARED vs PREPARED statements (the optimization).
  3. Cassandra read latency for the dashboard's hot query.
  4. End-to-end produce -> queryable latency through the live pipeline.

Outputs results/*.csv and results/optimization_report.md. Plots are produced
separately by visualization.py.
"""

import os
import csv
import json
import time
import uuid
import statistics
from datetime import datetime, timezone

from kafka import KafkaProducer
from cassandra.cluster import Cluster
from cassandra.concurrent import execute_concurrent_with_args

KAFKA = os.getenv("KAFKA_BOOTSTRAP", "localhost:29092")
CASS_HOST = os.getenv("CASSANDRA_HOST", "localhost")
CASS_PORT = int(os.getenv("CASSANDRA_PORT", "9042"))
KEYSPACE = "weather_monitoring"
TOPIC = "weather_data"

HERE = os.path.dirname(__file__)
RESULTS = os.path.join(HERE, "results")


def sample_reading(station_id):
    return {
        "station_id": station_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "temperature": 18.0, "humidity": 55.0, "pressure": 1013.0,
        "precipitation": 0.0, "wind_speed": 3.0,
    }


# Dedicated topic for the throughput test so it never floods the live pipeline.
BENCH_TOPIC = "benchmark_throughput"


# --- 1. Kafka throughput ----------------------------------------------------
def bench_kafka_throughput(volumes=(100, 1000, 5000)):
    print("\n[1] Kafka producer throughput")
    producer = KafkaProducer(
        bootstrap_servers=KAFKA,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        linger_ms=5,
    )
    rows = []
    for n in volumes:
        msg = sample_reading("BENCH")
        t0 = time.perf_counter()
        for _ in range(n):
            producer.send(BENCH_TOPIC, value=msg)
        producer.flush()
        dt = time.perf_counter() - t0
        rate = n / dt
        rows.append({"messages": n, "seconds": round(dt, 4), "msgs_per_sec": round(rate, 1)})
        print(f"    {n:>5} msgs in {dt:.3f}s -> {rate:,.0f} msg/s")
    producer.close()
    return rows


# --- 2 & 3. Cassandra write/read -------------------------------------------
def bench_cassandra(session, n=3000):
    print("\n[2] Cassandra write throughput: sequential vs concurrent")
    table = "raw_weather_data"
    stmt = session.prepare(
        f"INSERT INTO {table} (station_id, ts, temperature, humidity, pressure, "
        f"precipitation, wind_speed) VALUES (?, ?, ?, ?, ?, ?, ?)")

    # SEQUENTIAL: one synchronous write at a time (the naive approach)
    t0 = time.perf_counter()
    for i in range(n):
        session.execute(stmt, ("BENCH_SEQ", datetime.now(timezone.utc),
                               18.0, 55.0, 1013.0, 0.0, 3.0))
    seq_s = time.perf_counter() - t0

    # CONCURRENT: pipelined async writes (the optimization)
    params = [("BENCH_CON", datetime.now(timezone.utc), 18.0, 55.0, 1013.0, 0.0, 3.0)
              for _ in range(n)]
    t0 = time.perf_counter()
    execute_concurrent_with_args(session, stmt, params, concurrency=64)
    con_s = time.perf_counter() - t0

    write_rows = [
        {"method": "sequential", "rows": n, "seconds": round(seq_s, 3),
         "rows_per_sec": round(n / seq_s, 1)},
        {"method": "concurrent", "rows": n, "seconds": round(con_s, 3),
         "rows_per_sec": round(n / con_s, 1)},
    ]
    for r in write_rows:
        print(f"    {r['method']:>11}: {r['rows']} rows in {r['seconds']}s "
              f"-> {r['rows_per_sec']:,} rows/s")

    print("\n[3] Cassandra read latency (dashboard hot query)")
    read_q = session.prepare(f"SELECT * FROM {table} WHERE station_id=? LIMIT 60")
    reads = []
    for _ in range(200):
        t0 = time.perf_counter()
        list(session.execute(read_q, ("BENCH_CON",)))
        reads.append((time.perf_counter() - t0) * 1000)
    read_row = {"query": "latest_60", "samples": 200,
                "avg_ms": round(statistics.mean(reads), 3),
                "p95_ms": round(sorted(reads)[int(0.95 * 200)], 3)}
    print(f"    avg {read_row['avg_ms']} ms, p95 {read_row['p95_ms']} ms")

    session.execute("DELETE FROM raw_weather_data WHERE station_id='BENCH_SEQ'")
    session.execute("DELETE FROM raw_weather_data WHERE station_id='BENCH_CON'")
    return write_rows, [read_row]


# --- 4. End-to-end latency --------------------------------------------------
def bench_end_to_end(session, samples=10):
    print("\n[4] End-to-end produce -> queryable latency")
    producer = KafkaProducer(
        bootstrap_servers=KAFKA,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"))
    station = "BENCH_E2E"
    read_q = session.prepare(
        "SELECT ts FROM raw_weather_data WHERE station_id=? LIMIT 1")
    latencies = []
    for _ in range(samples):
        reading = sample_reading(station)
        t0 = time.perf_counter()
        producer.send(TOPIC, value=reading)
        producer.flush()
        # poll Cassandra until the row appears (Spark must consume + write it)
        deadline = time.perf_counter() + 30
        while time.perf_counter() < deadline:
            row = session.execute(read_q, (station,)).one()
            if row is not None:
                latencies.append(time.perf_counter() - t0)
                break
            time.sleep(0.2)
        session.execute("DELETE FROM raw_weather_data WHERE station_id=%s", (station,))
        time.sleep(0.3)
    producer.close()
    if not latencies:
        print("    (no rows arrived — is spark-processor running?)")
        return []
    row = {"samples": len(latencies),
           "avg_s": round(statistics.mean(latencies), 2),
           "min_s": round(min(latencies), 2),
           "max_s": round(max(latencies), 2)}
    print(f"    avg {row['avg_s']}s (min {row['min_s']}s, max {row['max_s']}s)")
    return [row]


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_report(kafka_rows, write_rows, read_rows, e2e_rows):
    path = os.path.join(RESULTS, "optimization_report.md")
    imp = ""
    if len(write_rows) == 2:
        seq = write_rows[0]["rows_per_sec"]
        con = write_rows[1]["rows_per_sec"]
        if seq > 0:
            imp = f"{con / seq:.1f}x higher write throughput ({seq:,.0f} -> {con:,.0f} rows/s)"
    with open(path, "w") as f:
        f.write("# Pipeline Performance & Optimization Report\n\n")
        f.write(f"_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n")
        f.write("## 1. Kafka producer throughput\n\n")
        f.write("| Messages | Seconds | Msgs/sec |\n|---|---|---|\n")
        for r in kafka_rows:
            f.write(f"| {r['messages']} | {r['seconds']} | {r['msgs_per_sec']} |\n")
        f.write("\n## 2. Cassandra write throughput (optimization)\n\n")
        f.write("| Method | Rows | Seconds | Rows/sec |\n|---|---|---|---|\n")
        for r in write_rows:
            f.write(f"| {r['method']} | {r['rows']} | {r['seconds']} | {r['rows_per_sec']} |\n")
        f.write(f"\n**Optimization — concurrent (pipelined) writes:** {imp}.\n")
        f.write("Sequential synchronous inserts pay a full network round-trip each;\n")
        f.write("pipelining many in-flight async writes (execute_concurrent) keeps the\n")
        f.write("connection saturated and dramatically raises throughput.\n")
        f.write("\n## 3. Cassandra read latency (dashboard query)\n\n")
        f.write("| Query | Avg ms | p95 ms |\n|---|---|---|\n")
        for r in read_rows:
            f.write(f"| {r['query']} | {r['avg_ms']} | {r['p95_ms']} |\n")
        f.write("\n## 4. End-to-end latency (produce -> queryable)\n\n")
        if e2e_rows:
            r = e2e_rows[0]
            f.write(f"Average **{r['avg_s']}s** over {r['samples']} samples "
                    f"(min {r['min_s']}s, max {r['max_s']}s).\n")
            f.write("\nThis is dominated by the Spark micro-batch trigger interval; "
                    "reducing the trigger interval lowers latency at the cost of more overhead.\n")
        else:
            f.write("No end-to-end samples (spark-processor not running during benchmark).\n")
    print(f"\nReport written to {path}")


def main():
    os.makedirs(RESULTS, exist_ok=True)
    cluster = Cluster([CASS_HOST], port=CASS_PORT)
    session = cluster.connect(KEYSPACE)

    # End-to-end first, on a quiet topic, before the throughput test floods Kafka
    # and backlogs Spark.
    e2e_rows = bench_end_to_end(session)
    write_rows, read_rows = bench_cassandra(session)
    kafka_rows = bench_kafka_throughput()

    write_csv(os.path.join(RESULTS, "kafka_throughput.csv"), kafka_rows)
    write_csv(os.path.join(RESULTS, "cassandra_writes.csv"), write_rows)
    write_csv(os.path.join(RESULTS, "cassandra_reads.csv"), read_rows)
    write_csv(os.path.join(RESULTS, "end_to_end.csv"), e2e_rows)
    write_report(kafka_rows, write_rows, read_rows, e2e_rows)

    cluster.shutdown()
    print("Done. Run visualization.py to render plots.")


if __name__ == "__main__":
    main()
