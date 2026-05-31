"""Render performance plots from the CSVs produced by benchmark_pipeline.py."""

import os
import csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(__file__)
RESULTS = os.path.join(HERE, "results")


def read_csv(name):
    path = os.path.join(RESULTS, name)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return list(csv.DictReader(f))


def plot_kafka(rows):
    if not rows:
        return
    x = [int(r["messages"]) for r in rows]
    y = [float(r["msgs_per_sec"]) for r in rows]
    plt.figure(figsize=(7, 4))
    plt.plot(x, y, marker="o", color="#2563eb")
    plt.title("Kafka Producer Throughput")
    plt.xlabel("Messages sent")
    plt.ylabel("Messages / second")
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(RESULTS, "kafka_throughput.png"), dpi=120)
    plt.close()


def plot_writes(rows):
    if not rows:
        return
    methods = [r["method"] for r in rows]
    rps = [float(r["rows_per_sec"]) for r in rows]
    colors = ["#94a3b8", "#16a34a"]
    plt.figure(figsize=(7, 4))
    bars = plt.bar(methods, rps, color=colors[:len(methods)], width=0.5)
    for b, v in zip(bars, rps):
        plt.text(b.get_x() + b.get_width() / 2, v, f"{v:,.0f}",
                 ha="center", va="bottom", fontsize=10)
    plt.title("Cassandra Write Throughput: Sequential vs Concurrent")
    plt.ylabel("Rows / second")
    plt.grid(True, axis="y", linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(RESULTS, "cassandra_writes.png"), dpi=120)
    plt.close()


def main():
    plot_kafka(read_csv("kafka_throughput.csv"))
    plot_writes(read_csv("cassandra_writes.csv"))
    print(f"Plots saved to {RESULTS}/")


if __name__ == "__main__":
    main()
