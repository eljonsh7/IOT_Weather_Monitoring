"""Flask dashboard for the weather monitoring system."""

import io
import os
import sys
import base64
import time
import statistics
import configparser
from datetime import datetime, timezone, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from cassandra.cluster import Cluster, NoHostAvailable
from flask import Flask, jsonify, render_template, request, Response
from weasyprint import HTML

sys.path.insert(0, "/ai_module")
try:
    import predict as ai
except Exception as e:
    print(f"AI module unavailable: {e}")
    ai = None

app = Flask(__name__)
CONFIG_PATH = "/config/project_config.ini"

PARAM_META = {
    "temperature":   {"label": "Temperature",  "unit": "°C",   "icon": "🌡️",  "color": "#f87171"},
    "humidity":      {"label": "Humidity",     "unit": "%",    "icon": "💧",  "color": "#38bdf8"},
    "pressure":      {"label": "Pressure",     "unit": "hPa",  "icon": "🌐",  "color": "#a78bfa"},
    "precipitation": {"label": "Precipitation","unit": "mm/h", "icon": "🌧️",  "color": "#34d399"},
    "wind_speed":    {"label": "Wind Speed",   "unit": "m/s",  "icon": "💨",  "color": "#fbbf24"},
}

MAX_CHART_POINTS = 300


def get_config():
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)
    return cfg


CONFIG = get_config()
CASS = CONFIG["cassandra"]


def station_list():
    ids = [s.strip() for s in CONFIG["stations"]["ids"].split(",")]
    result = []
    for sid in ids:
        val = CONFIG["stations"].get(sid, "")
        parts = [p.strip() for p in val.split("|")]
        city = parts[1] if len(parts) > 1 else sid
        result.append({"id": sid, "city": city})
    return result


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


def row_to_reading(row):
    return {
        "timestamp":    row.ts.replace(tzinfo=timezone.utc).isoformat(),
        "temperature":  row.temperature,
        "humidity":     row.humidity,
        "pressure":     row.pressure,
        "precipitation":row.precipitation,
        "wind_speed":   row.wind_speed,
    }


def downsample(rows, max_points=MAX_CHART_POINTS):
    if len(rows) <= max_points:
        return rows
    step = len(rows) // max_points
    return rows[::step]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", stations=station_list())


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/api/stations")
def api_stations():
    return jsonify(station_list())


@app.route("/api/metadata/<station_id>")
def api_metadata(station_id):
    if session is None:
        return jsonify({"error": "no database"}), 500
    row = session.execute(
        f"SELECT * FROM {CASS['metadata_table']} WHERE station_id=%s", [station_id]
    ).one()
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
    if session is None:
        return jsonify({"error": "no database"}), 500

    hours = request.args.get("hours")
    limit = int(request.args.get("limit", 60))

    if hours:
        end_t = datetime.now(timezone.utc)
        start_t = end_t - timedelta(hours=float(hours))
        rows = list(session.execute(
            f"SELECT * FROM {CASS['raw_data_table']} WHERE station_id=%s AND ts >= %s AND ts <= %s LIMIT 5000",
            [station_id, start_t, end_t]
        ))
    else:
        rows = list(session.execute(
            f"SELECT * FROM {CASS['raw_data_table']} WHERE station_id=%s LIMIT %s",
            [station_id, limit]
        ))

    data = [row_to_reading(r) for r in rows]
    data.reverse()
    data = downsample(data)
    return jsonify(data)


@app.route("/api/latest/<station_id>")
def api_latest(station_id):
    if session is None:
        return jsonify({"error": "no database"}), 500
    row = session.execute(
        f"SELECT * FROM {CASS['raw_data_table']} WHERE station_id=%s LIMIT 1", [station_id]
    ).one()
    if not row:
        return jsonify({"error": "no data"}), 404
    return jsonify(row_to_reading(row))


@app.route("/api/aggregates/<station_id>")
def api_aggregates(station_id):
    if session is None:
        return jsonify({"error": "no database"}), 500
    limit = int(request.args.get("limit", 30))
    rows = list(session.execute(
        f"SELECT * FROM {CASS['aggregated_data_table']} WHERE station_id=%s LIMIT %s",
        [station_id, limit]
    ))
    data = [{
        "window_start":      r.window_start.replace(tzinfo=timezone.utc).isoformat(),
        "window_end":        r.window_end.replace(tzinfo=timezone.utc).isoformat(),
        "avg_temperature":   r.avg_temperature,
        "avg_humidity":      r.avg_humidity,
        "avg_pressure":      r.avg_pressure,
        "total_precipitation": r.total_precipitation,
        "max_wind_speed":    r.max_wind_speed,
        "reading_count":     r.reading_count,
    } for r in rows]
    data.reverse()
    return jsonify(data)


@app.route("/api/alerts/<station_id>")
def api_alerts(station_id):
    if session is None:
        return jsonify({"error": "no database"}), 500
    limit = int(request.args.get("limit", 20))
    rows = list(session.execute(
        f"SELECT * FROM {CASS['alerts_table']} WHERE station_id=%s LIMIT %s",
        [station_id, limit]
    ))
    return jsonify([{
        "timestamp": r.ts.replace(tzinfo=timezone.utc).isoformat(),
        "parameter": r.parameter, "value": r.value,
        "severity":  r.severity,  "message": r.message,
    } for r in rows])


@app.route("/api/insights/<station_id>")
def api_insights(station_id):
    if session is None:
        return jsonify({"error": "no database"}), 500
    row = session.execute(
        f"SELECT * FROM {CASS['raw_data_table']} WHERE station_id=%s LIMIT 1", [station_id]
    ).one()
    if not row:
        return jsonify({"error": "no data"}), 404
    reading = row_to_reading(row)
    result = {"latest": reading, "ai_available": bool(ai and ai.models_available())}
    if result["ai_available"]:
        target = datetime.now(timezone.utc) + timedelta(hours=1)
        result["forecast_next_temp"] = ai.forecast_next_temperature(reading, target)
        result["condition"]          = ai.classify_condition(reading)
        result["anomaly"]            = ai.anomaly_details(reading)
    return jsonify(result)


# ---------------------------------------------------------------------------
# PDF Export
# ---------------------------------------------------------------------------

def _make_chart_png(timestamps, values, title, unit, color):
    """Render a single chart as a base64 PNG (light theme for print)."""
    fig, ax = plt.subplots(figsize=(9, 2.8))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f8fafc")

    ax.plot(timestamps, values, color=color, linewidth=2, zorder=3)
    ax.fill_between(timestamps, values, alpha=0.15, color=color, zorder=2)

    ax.set_title(f"{title}  ({unit})", fontsize=11, color="#1e293b",
                 pad=8, fontweight="bold")
    ax.tick_params(colors="#64748b", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#e2e8f0")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.grid(True, alpha=0.4, color="#e2e8f0", zorder=1)
    plt.xticks(rotation=30, ha="right", fontsize=7)
    plt.tight_layout(pad=0.5)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


@app.route("/api/export/pdf/<station_id>")
def export_pdf(station_id):
    if session is None:
        return jsonify({"error": "no database"}), 500

    hours = float(request.args.get("hours", 1))

    # --- Metadata ---
    meta_row = session.execute(
        f"SELECT * FROM {CASS['metadata_table']} WHERE station_id=%s", [station_id]
    ).one()
    station = {
        "id":         station_id,
        "name":       meta_row.name if meta_row else station_id,
        "city":       meta_row.city if meta_row else "",
        "latitude":   meta_row.latitude if meta_row else 0,
        "longitude":  meta_row.longitude if meta_row else 0,
        "altitude_m": meta_row.altitude_m if meta_row else 0,
        "sensors":    list(meta_row.sensors or []) if meta_row else [],
        "install_date": meta_row.install_date.strftime("%Y-%m-%d")
                        if meta_row and meta_row.install_date else "—",
    }

    # --- Raw readings for time range ---
    end_t   = datetime.now(timezone.utc)
    start_t = end_t - timedelta(hours=hours)
    raw_rows = list(session.execute(
        f"SELECT * FROM {CASS['raw_data_table']} "
        f"WHERE station_id=%s AND ts >= %s AND ts <= %s LIMIT 10000",
        [station_id, start_t, end_t]
    ))
    raw_rows.reverse()

    if not raw_rows:
        return jsonify({"error": "No data available for this period."}), 404

    data = [row_to_reading(r) for r in raw_rows]

    # --- Latest reading ---
    latest = data[-1] if data else None

    # --- Alerts for time range ---
    alert_rows = list(session.execute(
        f"SELECT * FROM {CASS['alerts_table']} "
        f"WHERE station_id=%s AND ts >= %s AND ts <= %s LIMIT 500",
        [station_id, start_t, end_t]
    ))
    alerts = [{
        "timestamp": r.ts.replace(tzinfo=timezone.utc).strftime("%H:%M:%S"),
        "parameter": r.parameter,
        "value":     round(r.value, 2),
        "severity":  r.severity,
        "message":   r.message,
    } for r in alert_rows]

    # --- AI insights ---
    ai_info = {"available": False}
    if ai and ai.models_available() and latest:
        try:
            target = datetime.now(timezone.utc) + timedelta(hours=1)
            anom   = ai.anomaly_details(latest)
            ai_info = {
                "available":  True,
                "forecast":   ai.forecast_next_temperature(latest, target),
                "condition":  ai.classify_condition(latest),
                "is_anomaly": anom.get("is_anomaly", False),
                "anom_reason":anom.get("reason") or "—",
            }
        except Exception:
            pass

    # --- Statistics ---
    params = list(PARAM_META.keys())
    stats = {}
    for p in params:
        vals = [d[p] for d in data if d.get(p) is not None]
        if vals:
            stats[p] = {
                "avg":   round(statistics.mean(vals), 2),
                "min":   round(min(vals), 2),
                "max":   round(max(vals), 2),
                "count": len(vals),
            }

    # --- Charts ---
    chart_data = downsample(data, 200)
    timestamps = [datetime.fromisoformat(d["timestamp"]) for d in chart_data]

    charts = {}
    for p, meta in PARAM_META.items():
        values = [d[p] for d in chart_data]
        charts[p] = _make_chart_png(
            timestamps, values, meta["label"], meta["unit"], meta["color"]
        )

    # --- Alert counts ---
    n_critical = sum(1 for a in alerts if a["severity"] == "critical")
    n_warning  = sum(1 for a in alerts if a["severity"] == "warning")

    # --- Render & PDF ---
    html_str = render_template(
        "report.html",
        station=station,
        latest=latest,
        stats=stats,
        alerts=alerts[:30],
        ai=ai_info,
        charts=charts,
        hours=hours,
        start_time=start_t.strftime("%Y-%m-%d %H:%M UTC"),
        end_time=end_t.strftime("%Y-%m-%d %H:%M UTC"),
        report_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        params=params,
        param_meta=PARAM_META,
        n_critical=n_critical,
        n_warning=n_warning,
        total_readings=len(data),
    )

    pdf_bytes = HTML(string=html_str, base_url=None).write_pdf()
    filename  = f"weather_report_{station['city']}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
