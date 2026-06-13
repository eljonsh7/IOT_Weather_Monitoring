"""Unit tests for the alerting thresholds and validation bounds.

These test the configuration contract and the severity-decision rule that the
Spark job implements with when(). They run without pyspark so they can execute
in any CI environment.

Run:  pytest tests/test_thresholds.py -v
"""

import os
import configparser

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "project_config.ini")
PARAMS = ["temperature", "humidity", "pressure", "precipitation", "wind_speed"]


def _config():
    c = configparser.ConfigParser()
    c.read(CONFIG)
    return c


def parse_threshold(raw):
    """Mirror of streaming_job.parse_threshold (pure Python, no pyspark)."""
    parts = [p.strip() for p in raw.split(",")]
    return tuple(None if p == "-" else float(p) for p in parts)


def severity(value, warn_lo, crit_lo, warn_hi, crit_hi):
    """Pure-Python replica of the Spark alert rule (critical wins over warning)."""
    if (crit_lo is not None and value < crit_lo) or (crit_hi is not None and value > crit_hi):
        return "critical"
    if (warn_lo is not None and value < warn_lo) or (warn_hi is not None and value > warn_hi):
        return "warning"
    return None


# --- Config integrity ------------------------------------------------------

def test_all_params_have_thresholds():
    c = _config()
    for p in PARAMS:
        assert p in c["thresholds"], f"missing threshold for {p}"


def test_thresholds_are_monotonic():
    """Where present: crit_low <= warn_low and warn_high <= crit_high."""
    c = _config()
    for p in PARAMS:
        warn_lo, crit_lo, warn_hi, crit_hi = parse_threshold(c["thresholds"][p])
        if crit_lo is not None and warn_lo is not None:
            assert crit_lo <= warn_lo, f"{p}: crit_low must be <= warn_low"
        if warn_hi is not None and crit_hi is not None:
            assert warn_hi <= crit_hi, f"{p}: warn_high must be <= crit_high"


def test_validation_bounds_min_less_than_max():
    c = _config()
    for p in PARAMS:
        lo, hi = [float(x) for x in c["validation"][p].split(",")]
        assert lo < hi, f"{p}: validation min must be < max"


# --- Severity rule ---------------------------------------------------------

def test_temperature_severity_levels():
    # config: temperature = -5,-12,35,40  (warn_lo,crit_lo,warn_hi,crit_hi)
    t = parse_threshold("-5,-12,35,40")
    assert severity(20, *t) is None        # comfortable
    assert severity(37, *t) == "warning"   # above warn_high
    assert severity(42, *t) == "critical"  # above crit_high
    assert severity(-8, *t) == "warning"   # below warn_low
    assert severity(-15, *t) == "critical" # below crit_low


def test_critical_takes_priority_over_warning():
    t = parse_threshold("-5,-12,35,40")
    # 42 is both > warn_high(35) and > crit_high(40): must be critical
    assert severity(42, *t) == "critical"


def test_one_sided_threshold_humidity():
    # config: humidity = -,-,85,95  (no low bounds)
    h = parse_threshold("-,-,85,95")
    assert severity(50, *h) is None
    assert severity(90, *h) == "warning"
    assert severity(97, *h) == "critical"
    assert severity(0, *h) is None  # no low threshold -> never low-alerts


def test_boundary_values_are_not_alerts():
    """A reading exactly on the threshold is not yet a breach (strict >/<)."""
    t = parse_threshold("-5,-12,35,40")
    assert severity(35, *t) is None   # exactly warn_high, not over
    assert severity(40, *t) == "warning"  # over warn_high but == crit_high (not over)
