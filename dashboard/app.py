"""Flask dashboard for the weather monitoring system.

Serves a live multi-station view backed by Cassandra, plus the AI panel
(forecast / condition / anomaly) using the local models from ai_module.
No external APIs, no debug routes — built to be demo-safe.
"""

import os
import sys
import time
import configparser
from datetime import datetime, timezone, timedelta

from cassandra.cluster import Cluster, NoHostAvailable
from flask import Flask, jsonify, render_template, request

# Local AI inference helpers (ai_module is volume-mounted at /ai_module).
sys.path.insert(0, "/ai_module")
try:
    import predict as ai
except Exception as e:  # pragma: no cover - dashboard still works without AI
    print(f"AI module unavailable: {e}")
    ai = None

app = Flask(__name__)

CONFIG_PATH = "/config/project_config.ini"


def get_config():
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)
    return cfg


CONFIG = get_config()
CASS = CONFIG["cassandra"]


def connect_to_cassandra():
    hosts = [h.strip() for h in CASS["hosts"].split(",")]
    port = int(CASS["port"])
    keyspace = CASS["keyspace"]
    for i in range(15):
        try:
            cluster = Cluster(hosts, port=port)
            session = cluster.connect(keyspace)
            print(f"Connected to Cassandra on attempt {i + 1}.")
            return session
        except (NoHostAvailable, Exception) as e:
            print(f"Waiting for Cassandra ({i + 1}/15): {e}")
            time.sleep(10)
    print("Could not connect to Cassandra.")
    return None


session = connect_to_cassandra()


def station_ids():
    return [s.strip() for s in CONFIG["stations"]["ids"].split(",")]


def row_to_reading(row):
    return {
        "timestamp": row.ts.replace(tzinfo=timezone.utc).isoformat(),
        "temperature": row.temperature,
        "humidity": row.humidity,
        "pressure": row.pressure,
        "precipitation": row.precipitation,
        "wind_speed": row.wind_speed,
    }


# --- Pages ------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", stations=station_ids())


# --- API --------------------------------------------------------------------
@app.route("/api/stations")
def api_stations():
    return jsonify(station_ids())


@app.route("/api/metadata/<station_id>")
def api_metadata(station_id):
    if session is None:
        return jsonify({"error": "no database"}), 500
    q = f"SELECT * FROM {CASS['metadata_table']} WHERE station_id=%s"
    row = session.execute(q, [station_id]).one()
    if not row:
        return jsonify({"error": "unknown station"}), 404
    return jsonify({
        "station_id": row.station_id, "name": row.name, "city": row.city,
        "latitude": row.latitude, "longitude": row.longitude,
        "altitude_m": row.altitude_m, "sensors": list(row.sensors or []),
        "install_date": row.install_date.isoformat() if row.install_date else None,
    })


@app.route("/api/data/<station_id>")
def api_data(station_id):
    """Recent raw readings (default: last N points, newest first -> returned oldest first)."""
    if session is None:
        return jsonify({"error": "no database"}), 500
    limit = int(request.args.get("limit", 60))
    q = f"SELECT * FROM {CASS['raw_data_table']} WHERE station_id=%s LIMIT %s"
    rows = list(session.execute(q, [station_id, limit]))
    data = [row_to_reading(r) for r in rows]
    data.reverse()  # chronological for charts
    return jsonify(data)


@app.route("/api/latest/<station_id>")
def api_latest(station_id):
    if session is None:
        return jsonify({"error": "no database"}), 500
    q = f"SELECT * FROM {CASS['raw_data_table']} WHERE station_id=%s LIMIT 1"
    row = session.execute(q, [station_id]).one()
    if not row:
        return jsonify({"error": "no data"}), 404
    return jsonify(row_to_reading(row))


@app.route("/api/aggregates/<station_id>")
def api_aggregates(station_id):
    if session is None:
        return jsonify({"error": "no database"}), 500
    limit = int(request.args.get("limit", 30))
    q = f"SELECT * FROM {CASS['aggregated_data_table']} WHERE station_id=%s LIMIT %s"
    rows = list(session.execute(q, [station_id, limit]))
    data = [{
        "window_start": r.window_start.replace(tzinfo=timezone.utc).isoformat(),
        "window_end": r.window_end.replace(tzinfo=timezone.utc).isoformat(),
        "avg_temperature": r.avg_temperature,
        "avg_humidity": r.avg_humidity,
        "avg_pressure": r.avg_pressure,
        "total_precipitation": r.total_precipitation,
        "max_wind_speed": r.max_wind_speed,
        "reading_count": r.reading_count,
    } for r in rows]
    data.reverse()
    return jsonify(data)


@app.route("/api/alerts/<station_id>")
def api_alerts(station_id):
    if session is None:
        return jsonify({"error": "no database"}), 500
    limit = int(request.args.get("limit", 20))
    q = f"SELECT * FROM {CASS['alerts_table']} WHERE station_id=%s LIMIT %s"
    rows = list(session.execute(q, [station_id, limit]))
    return jsonify([{
        "timestamp": r.ts.replace(tzinfo=timezone.utc).isoformat(),
        "parameter": r.parameter,
        "value": r.value,
        "severity": r.severity,
        "message": r.message,
    } for r in rows])


@app.route("/api/insights/<station_id>")
def api_insights(station_id):
    """AI panel: forecast next temperature + condition class + anomaly flag."""
    if session is None:
        return jsonify({"error": "no database"}), 500
    q = f"SELECT * FROM {CASS['raw_data_table']} WHERE station_id=%s LIMIT 1"
    row = session.execute(q, [station_id]).one()
    if not row:
        return jsonify({"error": "no data"}), 404
    reading = row_to_reading(row)

    result = {"latest": reading, "ai_available": bool(ai and ai.models_available())}
    if result["ai_available"]:
        target = datetime.now(timezone.utc) + timedelta(hours=1)
        result["forecast_next_temp"] = ai.forecast_next_temperature(reading, target)
        result["condition"] = ai.classify_condition(reading)
        result["anomaly"] = ai.anomaly_details(reading)
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
