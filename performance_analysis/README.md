# Performance Analysis

Benchmarks the **actual** running pipeline (Kafka + Spark + Cassandra) — not a
standalone pandas script — and demonstrates a concrete optimization.

## What it measures
1. **Kafka producer throughput** (messages/sec) at increasing volumes.
2. **Cassandra write latency** — *unprepared vs prepared* statements (the optimization).
3. **Cassandra read latency** for the dashboard's hot query.
4. **End-to-end latency** — produce → queryable through the live Spark pipeline.

## Usage
With the stack running (`docker-compose up`), from this folder:

```bash
pip install -r requirements.txt
python benchmark_pipeline.py     # writes results/*.csv + optimization_report.md
python visualization.py          # writes results/*.png
```

Kafka is reached on `localhost:29092` and Cassandra on `localhost:9042`
(both exposed by docker-compose). Override with `KAFKA_BOOTSTRAP`,
`CASSANDRA_HOST`, `CASSANDRA_PORT` if needed.

## Optimization shown
Prepared statements skip per-insert query parsing in Cassandra, measurably
lowering average write latency. The report quantifies the improvement and the
Spark sink uses the same principle.
