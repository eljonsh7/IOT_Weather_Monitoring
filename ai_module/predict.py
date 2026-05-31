"""Inference helpers loaded by the dashboard.

Loads the three trained models once and exposes simple functions. All inference
is local and instant. If a model file is missing the helpers degrade gracefully
(return None / rule-based fallback) so the dashboard still runs.
"""

import os
import math
import joblib

HERE = os.path.dirname(__file__)
MODELS = os.path.join(HERE, "models")

_forecast = None
_anomaly = None
_classifier = None


def _load():
    global _forecast, _anomaly, _classifier
    if _forecast is None and os.path.exists(os.path.join(MODELS, "forecast.pkl")):
        _forecast = joblib.load(os.path.join(MODELS, "forecast.pkl"))
    if _anomaly is None and os.path.exists(os.path.join(MODELS, "anomaly.pkl")):
        _anomaly = joblib.load(os.path.join(MODELS, "anomaly.pkl"))
    if _classifier is None and os.path.exists(os.path.join(MODELS, "classifier.pkl")):
        _classifier = joblib.load(os.path.join(MODELS, "classifier.pkl"))


def models_available():
    _load()
    return all(m is not None for m in (_forecast, _anomaly, _classifier))


def _time_feats(dt):
    hour = dt.hour + dt.minute / 60.0
    doy = dt.timetuple().tm_yday
    return {
        "hour_sin": math.sin(2 * math.pi * hour / 24.0),
        "hour_cos": math.cos(2 * math.pi * hour / 24.0),
        "doy_sin": math.sin(2 * math.pi * doy / 365.0),
        "doy_cos": math.cos(2 * math.pi * doy / 365.0),
    }


def forecast_next_temperature(latest_reading, when):
    """Predict the next temperature from the latest reading + target time.

    latest_reading: dict with temperature/humidity/pressure/wind_speed.
    when: datetime of the prediction target.
    """
    _load()
    if _forecast is None:
        return None
    feats = {
        "lag_temp": latest_reading["temperature"],
        "lag_humidity": latest_reading["humidity"],
        "lag_pressure": latest_reading["pressure"],
        "lag_wind": latest_reading["wind_speed"],
        **_time_feats(when),
    }
    cols = _forecast["columns"]
    X = [[feats[c] for c in cols]]
    return round(float(_forecast["model"].predict(X)[0]), 2)


def detect_anomaly(reading):
    """Return True if the reading is anomalous (hybrid detector).

    Anomalous if EITHER the multivariate IsolationForest isolates it OR any
    single feature falls outside its learned plausible range (the range check
    catches single-feature sensor spikes the forest dilutes).
    """
    return anomaly_details(reading)["is_anomaly"]


def anomaly_details(reading):
    """Hybrid anomaly result with an explanation for the dashboard."""
    _load()
    if _anomaly is None:
        return {"is_anomaly": False, "reason": None}

    params = _anomaly["params"]
    bounds = _anomaly.get("bounds", {})

    # per-feature range check
    for p in params:
        lo, hi = bounds.get(p, (float("-inf"), float("inf")))
        if reading[p] < lo or reading[p] > hi:
            return {"is_anomaly": True, "reason": f"{p} out of range ({reading[p]})"}

    # multivariate isolation check
    X = [[reading[p] for p in params]]
    if int(_anomaly["model"].predict(X)[0]) == -1:
        return {"is_anomaly": True, "reason": "unusual combination of readings"}

    return {"is_anomaly": False, "reason": None}


def classify_condition(reading):
    """Return 'stable' | 'storm' | 'extreme' for a reading."""
    _load()
    if _classifier is None:
        # rule-based fallback
        if (reading["temperature"] > 40 or reading["precipitation"] > 30
                or reading["wind_speed"] > 20 or reading["pressure"] < 980):
            return "extreme"
        if reading["precipitation"] > 5 or reading["wind_speed"] > 12:
            return "storm"
        return "stable"
    params = _classifier["params"]
    X = [[reading[p] for p in params]]
    return str(_classifier["model"].predict(X)[0])
