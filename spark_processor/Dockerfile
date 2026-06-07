# Bitnami Spark 3.3.0 (Scala 2.12) — matches the connector packages below.
# Bitnami relocated free legacy tags to the bitnamilegacy/ namespace in 2025.
FROM bitnamilegacy/spark:3.3.0

WORKDIR /app
COPY . .

# Unbuffered Python stdout so our "Batch N: wrote X" prints reach docker logs
# in real time — without this, line-buffering hides batch progress behind
# pipes and debugging streaming becomes guesswork.
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir -r requirements.txt

# spark-submit pulls the Kafka source + Cassandra connector at launch.
# Versions are pinned to Spark 3.3.0 / Scala 2.12 to avoid classpath errors.
CMD ["spark-submit", \
    "--packages", "org.apache.spark:spark-sql-kafka-0-10_2.12:3.3.0,com.datastax.spark:spark-cassandra-connector_2.12:3.3.0", \
    "streaming_job.py"]
