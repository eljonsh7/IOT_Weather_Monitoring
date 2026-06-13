"""Unit tests for the weather simulator.

Run from the repository root:

    pip install pytest
    PYTHONPATH=data_producer pytest tests/ -v

These tests verify the simulator's physical plausibility and determinism — the
properties the downstream AI and aggregation rely on.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data_producer"))

from weather_simulator import WeatherSimulator  # noqa: E402

STATIONS = ["STATION_A", "STATION_B", "STATION_C"]

# Generous absolute physical bounds (match config [validation]); the simulator's
# NORMAL output (excluding the rare injected fault) must stay well inside these.
PHYSICAL_BOUNDS = {
    "temperature": (-50, 54),
    "humidity": (0, 100),
    "pressure": (870, 1085),
    "precipitation": (0, 250),
    "wind_speed": (0, 115),
}


def _fresh_sim(seed=42):
    return WeatherSimulator(
        station_ids=STATIONS,
        start_time=datetime(2025, 7, 1, 12, 0, tzinfo=timezone.utc),
        seed=seed,
    )


def test_one_reading_per_station():
    sim = _fresh_sim()
    readings = sim.step()
    assert len(readings) == len(STATIONS)
    assert {r["station_id"] for r in readings} == set(STATIONS)


def test_all_fields_present():
    sim = _fresh_sim()
    fields = {"station_id", "timestamp", "temperature", "humidity",
              "pressure", "precipitation", "wind_speed"}
    for r in sim.step():
        assert fields.issubset(r.keys())


def test_humidity_clamped_for_normal_readings():
    """Normal (non-injected) humidity is clamped to [5, 100].

    Injected sensor faults deliberately bypass the clamp (e.g. 0 %), which is
    exactly the corruption the validation layer and anomaly detector exist to
    catch — so they are excluded here.
    """
    sim = _fresh_sim()
    for _ in range(2000):
        for r in sim.step():
            if "_anomaly_injected" in r:
                continue
            assert 5.0 <= r["humidity"] <= 100.0


def test_non_anomalous_readings_are_physically_plausible():
    """Excluding the rare injected fault, every value stays within physical bounds."""
    sim = _fresh_sim()
    for _ in range(3000):
        for r in sim.step():
            if "_anomaly_injected" in r:
                continue  # injected faults are deliberately out of range
            for param, (lo, hi) in PHYSICAL_BOUNDS.items():
                assert lo <= r[param] <= hi, f"{param}={r[param]} out of bounds"


def test_precipitation_non_negative():
    sim = _fresh_sim()
    for _ in range(2000):
        for r in sim.step():
            assert r["precipitation"] >= 0.0


def test_determinism_same_seed():
    """Same seed + same start time must reproduce identical readings."""
    a = _fresh_sim(seed=7)
    b = _fresh_sim(seed=7)
    for _ in range(200):
        ra, rb = a.step(), b.step()
        for x, y in zip(ra, rb):
            assert x["temperature"] == y["temperature"]
            assert x["pressure"] == y["pressure"]


def test_different_seeds_diverge():
    a = _fresh_sim(seed=1)
    b = _fresh_sim(seed=2)
    diffs = 0
    for _ in range(50):
        ra, rb = a.step(), b.step()
        for x, y in zip(ra, rb):
            if x["temperature"] != y["temperature"]:
                diffs += 1
    assert diffs > 0, "different seeds should produce different readings"


def test_seasonal_baseline_summer_warmer_than_winter():
    """July baseline must be warmer than January baseline (Kosovo climate)."""
    jan = WeatherSimulator._seasonal_baseline(datetime(2025, 1, 15))
    jul = WeatherSimulator._seasonal_baseline(datetime(2025, 7, 15))
    assert jul > jan
    assert jul - jan > 15  # meaningful seasonal amplitude
