# IoT Weather Monitoring — Project Checklist

**Course:** Internet of Things — UP / FIEK, 2026  
**Project:** Ndërtimi i një sistemi IoT (Weather Monitoring)  
**Team:** Besmir Sejdiu, Brahim Sylejmani

---

## What the professor requires (from slides)

The full pipeline: **Sensor/Simulator → Apache Kafka → Apache Spark Streaming → Apache Cassandra → Web Dashboard**

Advanced components (exam exemption if all 3 done): **AI + Alerting System + Performance Analysis**

---

## Step 1 — IoT Domain Selection

- [x] Domain chosen: **Weather Monitoring** (temperature, humidity, pressure, precipitation, wind speed)
- [x] Three stations defined: **Prishtina, Peja, Prizren** (Kosovo cities with realistic coordinates/altitude)

---

## Step 2 — Sensor Simulator (Data Collection)

> No physical sensors → build a simulator

- [x] `data_producer/weather_simulator.py` — `WeatherSimulator` class with realistic physics:
  - [x] Seasonal baseline (yearly cycle, Kosovo climate)
  - [x] Diurnal temperature cycle (coldest ~05:00, warmest ~15:00)
  - [x] AR(1) autocorrelated noise for smooth readings
  - [x] Storm events (random onset, correlated drop in pressure, rise in humidity/wind/rain)
  - [x] Injected sensor anomalies (1% chance per reading — simulates faulty sensors)
  - [x] Sea-level-adjusted pressure (QNH-style, comparable across stations)
- [x] Three independent station states (separate RNG seeds)
- [x] Simulator is reusable by AI training history generator

---

## Step 3 — Data Transmission via Apache Kafka

- [x] `data_producer/producer.py` — Kafka producer
  - [x] Reads config from `config/project_config.ini`
  - [x] Connects to Kafka with retry logic (10 attempts)
  - [x] Sends one reading per station every **2 seconds** (configurable)
  - [x] Keyed by `station_id` for ordered per-station partitioning
  - [x] Topic: `weather_data`
- [x] Kafka broker configured in `docker-compose.yml` (Confluent 7.3.0 + Zookeeper)
- [x] Kafka `alerts_topic` (`weather_alerts`) for downstream alert consumers
- [x] Kafka consumer side handled by Spark Streaming (next step)

---

## Step 4 — Real-Time Processing with Apache Spark Streaming

- [x] `spark_processor/streaming_job.py` — Spark Structured Streaming
  - [x] Reads from Kafka topic `weather_data`
  - [x] Parses JSON with defined schema (5 sensor fields + station_id + timestamp)
  - [x] **Query A — Raw readings**: every reading written to Cassandra `raw_weather_data`
  - [x] **Query B — Sliding window aggregates**: 5-min window / 1-min slide → `aggregated_weather`
    - [x] avg temperature, avg humidity, avg pressure, total precipitation, max wind speed, count
    - [x] Watermark: 2 minutes (handles late data)
  - [x] **Query C — Alert detection**: threshold-breach alerts → Cassandra + Kafka (inside Query A's foreachBatch)
  - [x] Config-driven thresholds (warn/critical low/high per parameter)
  - [x] FAIR scheduler + tuned shuffle partitions for stable 3-query concurrency
  - [x] Checkpoint directories for fault tolerance

---

## Step 5 — Data Storage in Apache Cassandra

- [x] `cassandra_setup/setup_cassandra.py` — one-shot schema bootstrap
  - [x] Keyspace: `weather_monitoring` (SimpleStrategy, RF=1)
  - [x] Table: `raw_weather_data` — every reading (PK: station_id, ts DESC)
  - [x] Table: `aggregated_weather` — windowed aggregates (PK: station_id, window_start DESC, window_end DESC)
  - [x] Table: `station_metadata` — sensor/station info (PK: station_id)
  - [x] Table: `weather_alerts` — threshold breaches (PK: station_id, ts DESC, parameter)
  - [x] Station metadata seeded: Prishtina (652m), Peja (520m), Prizren (412m)
  - [x] Sensors listed: DHT22, BMP280, RainSensor, Anemometer
- [x] Cassandra 4.0 in Docker with persistent volume
- [x] Spark-Cassandra connector configured (host/port in SparkSession)

---

## Step 6 — Visualization Web Interface

- [x] `dashboard/app.py` — Flask backend
  - [x] `/` — main dashboard page with station dropdown
  - [x] `/api/stations` — list of station IDs
  - [x] `/api/data/<station_id>` — last 60 raw readings (chronological)
  - [x] `/api/latest/<station_id>` — most recent reading
  - [x] `/api/aggregates/<station_id>` — last 30 windowed aggregates
  - [x] `/api/alerts/<station_id>` — last 20 alerts
  - [x] `/api/insights/<station_id>` — AI forecast + condition + anomaly
- [x] `dashboard/templates/index.html` — live UI
  - [x] Station selector dropdown
  - [x] Current conditions cards (one per parameter)
  - [x] 5 live Chart.js charts (temperature, humidity, pressure, precipitation, wind_speed)
  - [x] Alerts banner (shows active alerts prominently)
  - [x] Recent alerts list
  - [x] AI Insights panel (forecast next temp, condition, anomaly)
  - [x] Station metadata panel (city, lat/lon, altitude, sensors, install date)
- [x] Dashboard runs on port 5001 (avoids macOS AirPlay conflict on 5000)

---

## Advanced Component 1 — Artificial Intelligence (AI)

> Required for exam exemption

- [x] `ai_module/generate_history.py` — generates 1 year of synthetic history CSV (reuses WeatherSimulator)
- [x] `ai_module/train_models.py` — trains 3 models on the history:
  - [x] **Forecaster**: GradientBoostingRegressor → predicts next temperature from lag features + cyclical time features
  - [x] **Anomaly Detector**: IsolationForest (contamination=2%) + per-feature plausible range bounds
  - [x] **Condition Classifier**: RandomForestClassifier → stable / storm / extreme
  - [x] Models saved as `.pkl` files in `ai_module/models/`
- [x] `ai_module/predict.py` — inference helpers used by the dashboard
  - [x] `forecast_next_temperature(reading, target_dt)`
  - [x] `classify_condition(reading)`
  - [x] `anomaly_details(reading)`
  - [x] `models_available()` guard (dashboard still works if models missing)
- [x] AI panel in dashboard shows live results
- [x] No external APIs — fully local, demo-safe

---

## Advanced Component 2 — Alerting System

> Required for exam exemption

- [x] Thresholds defined in `config/project_config.ini` under `[thresholds]`
  - [x] Temperature: warn ±5°C / crit ±12°C / 35°C / 40°C
  - [x] Humidity: warn >85% / crit >95%
  - [x] Pressure: warn <995 or >1030 hPa / crit <980 or >1040 hPa
  - [x] Precipitation: warn >15 mm/h / crit >30 mm/h
  - [x] Wind speed: warn >14 m/s / crit >20 m/s
- [x] Spark evaluates thresholds in real time (inside `write_raw_and_alerts` foreachBatch)
- [x] Alerts written to Cassandra `weather_alerts` table
- [x] Alerts published to Kafka topic `weather_alerts` (for downstream consumers)
- [x] Dashboard polls alerts and displays them live (banner + list)
- [x] Severity levels: `warning` and `critical`

---

## Advanced Component 3 — Performance Analysis & Optimization

> Required for exam exemption

- [x] `performance_analysis/benchmark_pipeline.py` — benchmarks the live stack:
  - [x] Kafka producer throughput (100 / 1000 / 5000 messages)
  - [x] Cassandra write throughput: **sequential vs concurrent** (the optimization)
  - [x] Cassandra read latency (dashboard hot query, 200 samples, avg + p95)
  - [x] End-to-end latency: produce → Kafka → Spark → Cassandra → queryable
- [x] Optimization demonstrated: concurrent pipelined writes vs sequential (significant throughput gain)
- [x] Results exported to CSV (`results/*.csv`)
- [x] `performance_analysis/visualization.py` — generates plots from CSV results
- [x] `performance_analysis/results/optimization_report.md` — auto-generated report

---

## Infrastructure & DevOps

- [x] `docker-compose.yml` — complete orchestration
  - [x] Zookeeper → Kafka → Cassandra startup order with health checks
  - [x] `setup-cassandra` one-shot container (schema + metadata)
  - [x] `ai-trainer` one-shot container (history generation + model training)
  - [x] `data-producer`, `spark-processor`, `dashboard` services
  - [x] Persistent Cassandra volume
  - [x] All Dockerfiles in place
- [x] `config/project_config.ini` — single shared config for all services
- [x] `.gitignore` in place

---

## Verification & Testing (To Do)

- [ ] Run `docker-compose up --build -d` and verify all services start healthy
- [ ] Verify data is flowing: `docker-compose logs -f data-producer`
- [ ] Verify Spark is processing: `docker-compose logs -f spark-processor`
- [ ] Verify Cassandra has data: `cqlsh` queries on all 4 tables
- [ ] Verify dashboard loads at http://localhost:5001 and charts update live
- [ ] Verify alerts appear when thresholds are breached (storm events should trigger them)
- [ ] Verify AI panel shows forecast / condition / anomaly results
- [ ] Run performance benchmarks (with stack running): `python benchmark_pipeline.py`
- [ ] Generate benchmark plots: `python visualization.py`

---

## Final Report / Documentation (To Do)

> Required for project submission (Dorëzimi i projektit)

- [ ] **Introduction** — goal and objectives of the project
- [ ] **Project Infrastructure** — overall system architecture (diagram + description)
- [ ] **Kafka Integration** — config steps, topic setup, producer/consumer details
- [ ] **Spark Streaming Processing** — implementation details of the 3 streaming queries
- [ ] **Cassandra Storage** — keyspace/table schema, Spark-Cassandra integration, config
- [ ] **Visualization Interface** — screenshots + description of technologies used
- [ ] **AI Component** — model descriptions, training approach, results
- [ ] **Alerting System** — threshold design, how alerts flow through the pipeline
- [ ] **Performance Analysis** — benchmark results, optimization explanation
- [ ] **Conclusions & Recommendations** — achievements summary + possible improvements

---

## Screenshots Needed (for Report)

- [ ] Dashboard main view (all charts live)
- [ ] Alerts banner triggered during a storm event
- [ ] AI insights panel (forecast + condition + anomaly)
- [ ] Kafka logs showing messages being produced
- [ ] Spark logs showing micro-batch processing
- [ ] Cassandra CQLSH — `raw_weather_data` sample rows
- [ ] Cassandra CQLSH — `aggregated_weather` sample rows
- [ ] Cassandra CQLSH — `weather_alerts` sample rows
- [ ] Performance benchmark results (charts from `visualization.py`)

---

## Defense Preparation (Mbrojtja)

- [ ] All group members present on defense day
- [ ] System fully functional and running during presentation
- [ ] Live demo prepared:
  - [ ] Show data being collected (simulator logs)
  - [ ] Show Kafka transmission (topic messages)
  - [ ] Show Spark processing (micro-batch logs)
  - [ ] Show Cassandra storage (live queries)
  - [ ] Show dashboard visualization (live charts + alerts + AI)

---

## Summary

| Component | Status |
|---|---|
| Sensor Simulator | ✅ Complete |
| Kafka Producer | ✅ Complete |
| Spark Streaming (raw + windowed + alerts) | ✅ Complete |
| Cassandra Schema + Seeding | ✅ Complete |
| Flask + Chart.js Dashboard | ✅ Complete |
| AI (forecast + anomaly + classify) | ✅ Complete |
| Alerting System | ✅ Complete |
| Performance Analysis + Optimization | ✅ Complete |
| Docker Compose Orchestration | ✅ Complete |
| End-to-end verification / testing | ⏳ Pending |
| Final Report Document | ⏳ Pending |
| Screenshots | ⏳ Pending |
| Defense Preparation | ⏳ Pending |
