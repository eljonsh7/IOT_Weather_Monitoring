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
   ├─ Q1 raw        → Cassandra.raw_weather_data
   ├─ Q2 windowed   → Cassandra.aggregated_weather   (5-min sliding window)
   └─ Q3 alerts     → Cassandra.weather_alerts  (+ Kafka topic: weather_alerts)
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
| `spark_processor/` | Spark Structured Streaming: raw + windowed aggregates + alerting |
| `cassandra_setup/` | Creates keyspace + 4 tables, seeds station metadata |
| `ai_module/` | Local models: temperature forecast, anomaly detection, condition classification |
| `dashboard/` | Flask + Chart.js live UI (charts, alerts banner, AI panel, metadata) |
| `performance_analysis/` | Benchmarks the real pipeline + optimization report |
| `config/project_config.ini` | Single shared config for every service |

## Cassandra schema (`weather_monitoring`)
- `raw_weather_data` — every reading (PK: station_id, ts)
- `aggregated_weather` — sliding-window aggregates
- `station_metadata` — sensor/station metadata (seeded)
- `weather_alerts` — threshold-breach alerts

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
docker-compose logs -f spark-processor   # 3 streaming queries, micro-batches
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

## Stop
```bash
docker-compose down       # stop containers
docker-compose down -v    # also wipe Cassandra data
```

## Advanced components (exam-exemption)
1. **AI** — `ai_module/`: a temperature **forecaster** (gradient boosting on
   lag + time features), an **anomaly detector** (IsolationForest + per-feature
   range checks), and a **condition classifier** (stable / storm / extreme).
   Trained locally on a year of synthetic history — no external API.
2. **Alerting** — Spark evaluates config-driven thresholds in real time and
   writes alerts to Cassandra and a Kafka topic; the dashboard shows them live.
3. **Performance analysis** — `performance_analysis/` benchmarks Kafka
   throughput, Cassandra read/write latency, and end-to-end latency, and
   demonstrates a prepared-statement optimization.

## Notes
- Thresholds live in `config/project_config.ini` under `[thresholds]` and are
  fully configurable (climate-aware hazard bands, not hard-coded comfort ranges).
- Pressure is emitted sea-level-adjusted so values are comparable across
  stations and consistent with the thresholds; altitude is kept in metadata.
