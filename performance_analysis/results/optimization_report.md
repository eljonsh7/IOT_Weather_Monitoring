# Pipeline Performance & Optimization Report

_Generated: 2026-05-31 17:02_

## 1. Kafka producer throughput

| Messages | Seconds | Msgs/sec |
|---|---|---|
| 100 | 0.2465 | 405.7 |
| 1000 | 0.0466 | 21468.5 |
| 5000 | 0.1082 | 46193.8 |

## 2. Cassandra write throughput (optimization)

| Method | Rows | Seconds | Rows/sec |
|---|---|---|---|
| sequential | 3000 | 5.86 | 511.9 |
| concurrent | 3000 | 0.203 | 14798.0 |

**Optimization — concurrent (pipelined) writes:** 28.9x higher write throughput (512 -> 14,798 rows/s).
Sequential synchronous inserts pay a full network round-trip each;
pipelining many in-flight async writes (execute_concurrent) keeps the
connection saturated and dramatically raises throughput.

## 3. Cassandra read latency (dashboard query)

| Query | Avg ms | p95 ms |
|---|---|---|
| latest_60 | 1.197 | 2.196 |

## 4. End-to-end latency (produce -> queryable)

Average **7.72s** over 7 samples (min 2.49s, max 29.55s).

This is dominated by the Spark micro-batch trigger interval; reducing the trigger interval lowers latency at the cost of more overhead.
