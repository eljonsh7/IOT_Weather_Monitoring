"""Kafka alerts consumer — the notification service.

Subscribes to the `weather_alerts` Kafka topic (produced by the Spark streaming
job) and acts on each alert as a downstream consumer would in production:
critical alerts are "dispatched" (in a real deployment this is where an SMS /
email / push gateway call would go), warnings are logged. Every handled alert is
also written to the `alert_notifications` Cassandra table as a queryable audit
trail of what the notification service did.

This closes the producer/consumer loop required by the project: the producer
feeds `weather_data`, Spark consumes it and re-publishes breaches to
`weather_alerts`, and THIS service is an independent consumer of those alerts —
demonstrating Kafka's publish/subscribe decoupling with two distinct consumers.
"""

import json
import time
import configparser
from collections import Counter
from datetime import datetime, timezone

from kafka import KafkaConsumer
from cassandra.cluster import Cluster, NoHostAvailable


def get_config(filepath="/config/project_config.ini"):
    config = configparser.ConfigParser()
    config.read(filepath)
    return config


def create_consumer(bootstrap_servers, topic, group_id):
    """Create a Kafka consumer, retrying while the broker comes up."""
    for i in range(15):
        try:
            consumer = KafkaConsumer(
                topic,
                bootstrap_servers=bootstrap_servers,
                group_id=group_id,
                auto_offset_reset="latest",
                enable_auto_commit=True,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            )
            print(f"Alerts consumer connected to '{topic}' (group={group_id}).")
            return consumer
        except Exception as e:
            print(f"Waiting for Kafka ({i + 1}/15): {e}")
            time.sleep(10)
    return None


def connect_cassandra(cass_cfg):
    """Connect to Cassandra (for the notification audit trail), with retries."""
    hosts = [h.strip() for h in cass_cfg["hosts"].split(",")]
    port = int(cass_cfg["port"])
    keyspace = cass_cfg["keyspace"]
    for i in range(15):
        try:
            cluster = Cluster(hosts, port=port)
            session = cluster.connect(keyspace)
            print(f"Connected to Cassandra on attempt {i + 1}.")
            return session
        except (NoHostAvailable, Exception) as e:
            print(f"Waiting for Cassandra ({i + 1}/15): {e}")
            time.sleep(10)
    print("Could not connect to Cassandra; audit trail disabled.")
    return None


def main():
    config = get_config()
    kafka_cfg = config["kafka"]
    cass_cfg = config["cassandra"]

    consumer = create_consumer(
        bootstrap_servers=kafka_cfg["bootstrap_servers"],
        topic=kafka_cfg["alerts_topic"],
        group_id="alerts-notifier",
    )
    if consumer is None:
        print("Could not create alerts consumer. Exiting.")
        return

    session = connect_cassandra(cass_cfg)
    insert_stmt = None
    if session is not None:
        table = cass_cfg.get("notifications_table", "alert_notifications")
        insert_stmt = session.prepare(
            f"INSERT INTO {table} "
            f"(station_id, notified_at, parameter, severity, channel, message) "
            f"VALUES (?, ?, ?, ?, ?, ?)"
        )

    counts = Counter()
    last_summary = time.time()
    print("Listening for weather alerts...")

    try:
        for record in consumer:
            alert = record.value
            sev = (alert.get("severity") or "unknown").lower()
            station = alert.get("station_id", "?")
            parameter = alert.get("parameter", "?")
            msg = alert.get("message", "")
            now = datetime.now(timezone.utc)
            ts = now.strftime("%H:%M:%S")

            # Route by severity. 'dispatch' is where SMS/email/push would fire.
            channel = "dispatch" if sev == "critical" else "log"
            if sev == "critical":
                print(f"[{ts}] 🔴 NOTIFY (CRITICAL) station={station} :: {msg} "
                      f"-> dispatch SMS/email/push to on-call operator")
            else:
                print(f"[{ts}] 🟡 LOG    (warning)  station={station} :: {msg}")

            # Persist the notification as a queryable audit record.
            if insert_stmt is not None:
                try:
                    session.execute(insert_stmt,
                                    (station, now, parameter, sev, channel, msg))
                except Exception as e:
                    print(f"  (audit write failed: {e})")

            counts[sev] += 1

            # Periodic dispatch summary (every 60s) — an at-a-glance ops view.
            if time.time() - last_summary >= 60:
                total = sum(counts.values())
                print(f"--- notifier summary: {total} alert(s) handled "
                      f"(critical={counts['critical']}, warning={counts['warning']}) ---")
                last_summary = time.time()
    except KeyboardInterrupt:
        print("Stopping alerts consumer.")
    finally:
        consumer.close()
        print("Alerts consumer closed.")


if __name__ == "__main__":
    main()
