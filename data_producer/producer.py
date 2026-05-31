"""Kafka producer: streams simulated weather readings to the weather_data topic.

Reads stations + simulator parameters from the shared config, builds a
WeatherSimulator, and emits one batch of readings (one per station) every
`emit_interval_seconds`.
"""

import json
import time
import configparser
from datetime import datetime, timezone

from kafka import KafkaProducer

from weather_simulator import WeatherSimulator


def get_config(filepath="/config/project_config.ini"):
    config = configparser.ConfigParser()
    config.read(filepath)
    return config


def create_producer(bootstrap_servers, client_id):
    """Create a Kafka producer, retrying while the broker comes up."""
    for i in range(10):
        try:
            producer = KafkaProducer(
                bootstrap_servers=bootstrap_servers,
                client_id=client_id,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                retries=5,
                linger_ms=10,
            )
            print("Kafka producer created.")
            return producer
        except Exception as e:
            print(f"Error creating producer (attempt {i + 1}/10): {e}")
            time.sleep(10)
    return None


def main():
    config = get_config()
    kafka_cfg = config["kafka"]
    sim_cfg = config["simulator"]

    station_ids = [s.strip() for s in config["stations"]["ids"].split(",")]

    producer = create_producer(kafka_cfg["bootstrap_servers"], kafka_cfg["client_id"])
    if producer is None:
        print("Could not create Kafka producer. Exiting.")
        return

    topic = kafka_cfg["topic_name"]
    emit_interval = float(sim_cfg["emit_interval_seconds"])

    simulator = WeatherSimulator(
        station_ids=station_ids,
        sim_minutes_per_reading=int(sim_cfg["sim_minutes_per_reading"]),
        storm_probability=float(sim_cfg["storm_probability"]),
        storm_duration_readings=int(sim_cfg["storm_duration_readings"]),
        anomaly_probability=float(sim_cfg["anomaly_probability"]),
        start_time=datetime.now(timezone.utc),
    )

    print(f"Streaming weather data for {station_ids} to topic '{topic}' "
          f"every {emit_interval}s.")

    try:
        while True:
            for reading in simulator.step():
                # Use wall-clock time for the live stream so the dashboard shows
                # "now"; keep the simulated dynamics, just stamp current time.
                reading["timestamp"] = datetime.now(timezone.utc).isoformat()
                anomaly = reading.pop("_anomaly_injected", None)
                producer.send(topic, key=reading["station_id"], value=reading)
                tag = f"  [ANOMALY:{anomaly}]" if anomaly else ""
                print(f"-> {reading['station_id']} "
                      f"T={reading['temperature']}C H={reading['humidity']}% "
                      f"P={reading['pressure']}hPa rain={reading['precipitation']}mm "
                      f"wind={reading['wind_speed']}m/s{tag}")
            producer.flush()
            time.sleep(emit_interval)
    except KeyboardInterrupt:
        print("Stopping simulation.")
    finally:
        producer.close()
        print("Producer closed.")


if __name__ == "__main__":
    main()
