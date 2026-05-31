"""Generate a long synthetic weather history for training the AI models.

Reuses the live WeatherSimulator so the training distribution matches the
production data stream. Produces ai_module/data/weather_history.csv with a
configurable number of simulated days at hourly resolution.
"""

import os
import csv
import sys
import argparse
from datetime import datetime, timezone

# Allow importing the simulator from the sibling data_producer package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data_producer"))
from weather_simulator import WeatherSimulator  # noqa: E402

STATIONS = ["STATION_PRISHTINA", "STATION_PEJA", "STATION_PRIZREN"]
FIELDS = ["station_id", "timestamp", "temperature", "humidity",
          "pressure", "precipitation", "wind_speed"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365, help="simulated days of history")
    ap.add_argument("--step-minutes", type=int, default=60, help="minutes between readings")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "data", "weather_history.csv"))
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    # anomaly_probability=0 -> CLEAN training history. The models should learn
    # normal weather dynamics; injected sensor faults at runtime then stand out
    # as genuine out-of-distribution anomalies for the IsolationForest.
    sim = WeatherSimulator(
        station_ids=STATIONS,
        sim_minutes_per_reading=args.step_minutes,
        anomaly_probability=0.0,
        start_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        seed=7,
    )

    steps = int(args.days * 24 * 60 / args.step_minutes)
    rows = 0
    with open(args.out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for _ in range(steps):
            for reading in sim.step():
                reading.pop("_anomaly_injected", None)
                writer.writerow({k: reading[k] for k in FIELDS})
                rows += 1

    print(f"Wrote {rows} rows ({args.days} days) to {args.out}")


if __name__ == "__main__":
    main()
