# IoT Weather Monitoring System

A complete, real-time IoT pipeline for **weather monitoring**, built for the
Internet of Things course (UP / FIEK, 2026). Simulated weather sensors stream
data through **Apache Kafka**, processed in real time by **Apache Spark
Streaming**, stored in **Apache Cassandra**, and visualized on a live
**Flask + Chart.js** dashboard. Everything runs with one `docker-compose` command.

The project also implements the three advanced components:
**AI**, a real-time **alerting system**, and **performance analysis & optimization**.

## Architecture

```
 data_producer (realistic weather simulator, 3 stations)
        │  JSON readings
        ▼
   Apache Kafka  (topic: weather_data)
        │
        ▼
 Apache Spark Streaming
   ├─ Query A (foreachBatch) → Cassandra.raw_weather_data
   │                         → Cassandra.weather_alerts
   │                         → Kafka topic: weather_alerts
   │                                │
   │                                ▼
   │                         alerts_consumer (severity routing + audit trail)
   │                                → Cassandra.alert_notifications
   └─ Query B (windowed)     → Cassandra.aggregated_weather (5-min sliding window)
        │
        ▼
 Apache Cassandra  (keyspace: weather_monitoring)
        │
        ▼
 Flask + Chart.js dashboard  ──uses──▶  ai_module (local forecast / anomaly / classify)
        http://localhost:5001
```

### Stations & parameters
Three simulated stations — **Prishtina, Peja, Prizren** — each reporting
**temperature, humidity, pressure, precipitation, wind speed** with realistic
dynamics (diurnal + seasonal cycles, correlated humidity/pressure, storm events,
and injected sensor anomalies). Station altitude is recorded in metadata.

## Components

| Folder | Role |
|---|---|
| `data_producer/` | Realistic weather simulator → Kafka producer |
| `spark_processor/` | Spark Structured Streaming: validation + raw + windowed aggregates + alerting |
| `alerts_consumer/` | Second independent Kafka consumer: routes alerts by severity (notification service) |
| `cassandra_setup/` | Creates keyspace + 5 tables, seeds station metadata |
| `ai_module/` | Local models: temperature forecast, anomaly detection, condition classification |
| `dashboard/` | Flask + Chart.js live UI (charts, aggregated-trends panel, alerts banner, AI panel, metadata, dark/light theme, PDF export) |
| `performance_analysis/` | Benchmarks the real pipeline + optimization report |
| `tests/` | pytest suite (15 tests) for the simulator physics and alert/threshold logic |
| `config/project_config.ini` | Single shared config for every service (incl. `[validation]` bounds) |

## Cassandra schema (`weather_monitoring`)
- `raw_weather_data` — every reading (PK: station_id, ts)
- `aggregated_weather` — Spark 5-minute sliding-window aggregates per station
- `station_metadata` — sensor/station metadata (seeded)
- `weather_alerts` — threshold-breach alerts (written by Spark)
- `alert_notifications` — audit trail of alerts handled by the consumer (dispatch/log)

## Prerequisites
- [Docker](https://www.docker.com/get-started) + Docker Compose
- ~6–8 GB RAM available to Docker (Kafka + Spark + Cassandra are memory-hungry)

## Run

```bash
docker-compose up --build -d
```

Startup order is handled automatically: Zookeeper → Kafka → Cassandra →
`setup-cassandra` (schema + metadata) and `ai-trainer` (trains AI models) →
producer / Spark / dashboard.

Then open the dashboard:

**http://localhost:5001**

Pick a station from the dropdown to watch its metrics update live. Storms and
anomalies trigger the alerts banner and show up in the AI panel.

### Watch it work
```bash
docker-compose logs -f data-producer    # readings being sent to Kafka
docker-compose logs -f spark-processor   # 2 streaming queries, micro-batches
```

Inspect the database:
```bash
docker exec -it weather-cassandra cqlsh -e \
  "SELECT * FROM weather_monitoring.raw_weather_data LIMIT 5;"
docker exec -it weather-cassandra cqlsh -e \
  "SELECT * FROM weather_monitoring.station_metadata;"
docker exec -it weather-cassandra cqlsh -e \
  "SELECT * FROM weather_monitoring.weather_alerts LIMIT 10;"
```

## Performance benchmarks
With the stack running:
```bash
cd performance_analysis
pip install -r requirements.txt
python benchmark_pipeline.py   # results/*.csv + optimization_report.md
python visualization.py        # results/*.png
```

## Tests
```bash
pip install pytest
PYTHONPATH=data_producer pytest tests/ -v   # 15 tests: simulator + thresholds
```

## Watch the second consumer
```bash
docker-compose logs -f alerts-consumer   # alerts routed by severity (critical → dispatch)
```

## Stop
```bash
docker-compose down       # stop containers
docker-compose down -v    # also wipe Cassandra data + Spark checkpoints
```

## Advanced components (exam-exemption)
1. **AI** — `ai_module/`: a temperature **forecaster** (gradient boosting on
   lag + time features), an **anomaly detector** (IsolationForest + per-feature
   range checks), and a **condition classifier** (stable / storm / extreme).
   Trained locally on a year of synthetic history — no external API.
2. **Alerting** — Spark evaluates config-driven thresholds in real time and
   writes alerts to Cassandra and a Kafka topic; a second independent consumer
   (`alerts_consumer/`) subscribes to that topic and routes by severity; the
   dashboard shows alerts live.
3. **Performance analysis** — `performance_analysis/` benchmarks Kafka
   throughput, Cassandra read/write latency, and end-to-end latency, and
   demonstrates a concurrent-write optimization (28.9× over sequential).

## Data quality (medallion pattern)
Spark validates every reading against physical-plausibility bounds
(`[validation]` in the config). Raw storage keeps everything (bronze/audit);
the windowed aggregates are computed **only on validated data** (silver) so a
faulted sensor reading can't skew the averages. Streaming checkpoints persist on
a Docker volume so offsets/state survive restarts.

## Notes
- Thresholds live in `config/project_config.ini` under `[thresholds]` and are
  fully configurable (climate-aware hazard bands, not hard-coded comfort ranges).
- Pressure is emitted sea-level-adjusted so values are comparable across
  stations and consistent with the thresholds; altitude is kept in metadata.
