# cassandra_setup/setup_cassandra.py
"""Creates the weather_monitoring keyspace, all tables, and seeds station metadata.

Runs once as a one-shot container (exits 0 on success) before Spark and the
dashboard start. Includes a retry loop because Cassandra is slow to accept
connections after the container reports healthy.
"""

import sys
import time
import configparser
from datetime import datetime, timezone

from cassandra.cluster import Cluster, NoHostAvailable


def get_config(filepath="/config/project_config.ini"):
    config = configparser.ConfigParser()
    config.read(filepath)
    return config


def parse_stations(config):
    """Returns a list of dicts describing each station from [stations]."""
    station_ids = [s.strip() for s in config["stations"]["ids"].split(",")]
    stations = []
    for sid in station_ids:
        # value format: name|city|lat|lon|altitude_m
        name, city, lat, lon, alt = [v.strip() for v in config["stations"][sid].split("|")]
        stations.append({
            "station_id": sid,
            "name": name,
            "city": city,
            "latitude": float(lat),
            "longitude": float(lon),
            "altitude_m": int(alt),
        })
    return stations


def connect(host, port):
    for i in range(15):
        try:
            cluster = Cluster([host], port=port)
            session = cluster.connect()
            print(f"Connected to Cassandra on attempt {i + 1}.")
            return cluster, session
        except NoHostAvailable:
            print(f"Waiting for Cassandra... attempt {i + 1}/15")
            time.sleep(10)
    print("Could not connect to Cassandra. Exiting.")
    sys.exit(1)


def main():
    print("--- Cassandra setup starting ---")
    config = get_config()
    cc = config["cassandra"]

    host = cc.get("hosts", "cassandra").split(",")[0].strip()
    port = int(cc.get("port", "9042"))
    keyspace = cc.get("keyspace", "weather_monitoring")

    cluster, session = connect(host, port)

    try:
        session.execute(f"""
            CREATE KEYSPACE IF NOT EXISTS {keyspace}
            WITH replication = {{'class': 'SimpleStrategy', 'replication_factor': '1'}}
        """)
        session.set_keyspace(keyspace)

        print("Creating table: raw_weather_data")
        session.execute(f"""
            CREATE TABLE IF NOT EXISTS {cc['raw_data_table']} (
                station_id TEXT,
                ts TIMESTAMP,
                temperature DOUBLE,
                humidity DOUBLE,
                pressure DOUBLE,
                precipitation DOUBLE,
                wind_speed DOUBLE,
                PRIMARY KEY (station_id, ts)
            ) WITH CLUSTERING ORDER BY (ts DESC)
        """)

        print("Creating table: aggregated_weather")
        session.execute(f"""
            CREATE TABLE IF NOT EXISTS {cc['aggregated_data_table']} (
                station_id TEXT,
                window_start TIMESTAMP,
                window_end TIMESTAMP,
                avg_temperature DOUBLE,
                avg_humidity DOUBLE,
                avg_pressure DOUBLE,
                total_precipitation DOUBLE,
                max_wind_speed DOUBLE,
                reading_count INT,
                PRIMARY KEY (station_id, window_start, window_end)
            ) WITH CLUSTERING ORDER BY (window_start DESC, window_end DESC)
        """)

        print("Creating table: station_metadata")
        session.execute(f"""
            CREATE TABLE IF NOT EXISTS {cc['metadata_table']} (
                station_id TEXT PRIMARY KEY,
                name TEXT,
                city TEXT,
                latitude DOUBLE,
                longitude DOUBLE,
                altitude_m INT,
                sensors LIST<TEXT>,
                install_date TIMESTAMP
            )
        """)

        print("Creating table: weather_alerts")
        session.execute(f"""
            CREATE TABLE IF NOT EXISTS {cc['alerts_table']} (
                station_id TEXT,
                ts TIMESTAMP,
                parameter TEXT,
                value DOUBLE,
                severity TEXT,
                message TEXT,
                PRIMARY KEY (station_id, ts, parameter)
            ) WITH CLUSTERING ORDER BY (ts DESC, parameter ASC)
        """)

        print("Creating table: alert_notifications")
        session.execute(f"""
            CREATE TABLE IF NOT EXISTS {cc.get('notifications_table', 'alert_notifications')} (
                station_id TEXT,
                notified_at TIMESTAMP,
                parameter TEXT,
                severity TEXT,
                channel TEXT,
                message TEXT,
                PRIMARY KEY (station_id, notified_at, parameter)
            ) WITH CLUSTERING ORDER BY (notified_at DESC, parameter ASC)
        """)

        # --- Seed station metadata (the reference repo omitted this table) ---
        print("Seeding station metadata...")
        sensors = ["DHT22", "BMP280", "RainSensor", "Anemometer"]
        install_date = datetime(2025, 1, 1, tzinfo=timezone.utc)
        insert = session.prepare(f"""
            INSERT INTO {cc['metadata_table']}
                (station_id, name, city, latitude, longitude, altitude_m, sensors, install_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """)
        for s in parse_stations(config):
            session.execute(insert, (
                s["station_id"], s["name"], s["city"],
                s["latitude"], s["longitude"], s["altitude_m"],
                sensors, install_date,
            ))
            print(f"  seeded {s['station_id']} ({s['city']})")

        print("--- Cassandra setup complete. ---")
    except Exception as e:
        print(f"Error during setup: {e}")
        cluster.shutdown()
        sys.exit(1)

    cluster.shutdown()


if __name__ == "__main__":
    main()
