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
#
# Checkpoints are cleared on every start so a restart (manual, or via the
# restart policy after a rare transient Kafka blip) always resumes cleanly from
# the live tail (startingOffsets=latest) instead of getting wedged on stale
# checkpoint state. The Kafka consumer timeouts in streaming_job.py make the
# crash itself rare; this guarantees clean recovery if it ever happens.
CMD ["bash", "-c", "rm -rf /tmp/checkpoints/* 2>/dev/null; exec spark-submit --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.3.0,com.datastax.spark:spark-cassandra-connector_2.12:3.3.0 streaming_job.py"]
