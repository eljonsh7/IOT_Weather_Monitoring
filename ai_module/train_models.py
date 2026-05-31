"""Train the three local AI models used by the dashboard.

  1. Forecast      : predict the NEXT temperature from recent readings + time
                     features (GradientBoostingRegressor on lag features).
  2. Anomaly       : IsolationForest over the 5 weather parameters (unsupervised;
                     flags injected sensor faults / physically odd readings).
  3. Classification: label conditions as stable / storm / extreme
                     (RandomForest trained on derived labels).

All models are lightweight and saved as .pkl so inference is instant and fully
local — no external API, so the live demo cannot fail on network/keys.
"""

import os
import math
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestClassifier, IsolationForest
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, classification_report

HERE = os.path.dirname(__file__)
DATA = os.path.join(HERE, "data", "weather_history.csv")
MODELS = os.path.join(HERE, "models")

PARAMS = ["temperature", "humidity", "pressure", "precipitation", "wind_speed"]


def time_features(ts):
    """Cyclical hour + day-of-year features from a pandas Timestamp."""
    hour = ts.dt.hour + ts.dt.minute / 60.0
    doy = ts.dt.dayofyear
    return pd.DataFrame({
        "hour_sin": np.sin(2 * math.pi * hour / 24.0),
        "hour_cos": np.cos(2 * math.pi * hour / 24.0),
        "doy_sin": np.sin(2 * math.pi * doy / 365.0),
        "doy_cos": np.cos(2 * math.pi * doy / 365.0),
    })


def derive_label(row):
    """Rule-derived condition label for supervised classification."""
    if (row.temperature < -12 or row.temperature > 40
            or row.precipitation > 30 or row.wind_speed > 20 or row.pressure < 980):
        return "extreme"
    if (row.precipitation > 5 or row.wind_speed > 12 or row.pressure < 1000):
        return "storm"
    return "stable"


def build_forecast(df):
    """Per-station lag features -> next temperature."""
    frames = []
    for sid, g in df.groupby("station_id"):
        g = g.sort_values("timestamp").reset_index(drop=True)
        tf = time_features(g["timestamp"])
        feat = pd.DataFrame({
            "lag_temp": g["temperature"],
            "lag_humidity": g["humidity"],
            "lag_pressure": g["pressure"],
            "lag_wind": g["wind_speed"],
        })
        feat = pd.concat([feat, tf], axis=1)
        feat["target"] = g["temperature"].shift(-1)  # next reading's temperature
        frames.append(feat.dropna())
    data = pd.concat(frames, ignore_index=True)
    X = data.drop(columns=["target"])
    y = data["target"]
    cols = list(X.columns)
    # Fit on plain arrays (no feature names) so list-based inference in
    # predict.py matches exactly and raises no sklearn warning.
    Xtr, Xte, ytr, yte = train_test_split(X.values, y.values, test_size=0.2, random_state=42)
    model = GradientBoostingRegressor(n_estimators=200, max_depth=3, random_state=42)
    model.fit(Xtr, ytr)
    mae = mean_absolute_error(yte, model.predict(Xte))
    print(f"[forecast] next-temp MAE = {mae:.2f} C   (features: {cols})")
    return model, cols


def build_anomaly(df):
    X = df[PARAMS].values
    model = IsolationForest(n_estimators=150, contamination=0.02, random_state=42)
    model.fit(X)
    # Per-feature plausible bounds from the clean history. A multivariate
    # IsolationForest dilutes single-feature spikes (one extreme dimension among
    # five normal ones), so we pair it with explainable per-feature range checks.
    bounds = {}
    for p in PARAMS:
        lo, hi = float(df[p].min()), float(df[p].max())
        margin = 0.25 * (hi - lo)
        bounds[p] = (lo - margin, hi + margin)
    print(f"[anomaly] IsolationForest + range bounds trained on {len(X)} rows.")
    return model, bounds


def build_classifier(df):
    labels = df.apply(derive_label, axis=1)
    X = df[PARAMS].values
    Xtr, Xte, ytr, yte = train_test_split(X, labels, test_size=0.2, random_state=42)
    model = RandomForestClassifier(n_estimators=120, random_state=42)
    model.fit(Xtr, ytr)
    print("[classifier] condition report:")
    print(classification_report(yte, model.predict(Xte)))
    return model


def main():
    if not os.path.exists(DATA):
        raise SystemExit(f"History not found: {DATA}\nRun: python generate_history.py")
    os.makedirs(MODELS, exist_ok=True)

    df = pd.read_csv(DATA, parse_dates=["timestamp"])
    print(f"Loaded {len(df)} rows.")

    forecast_model, forecast_cols = build_forecast(df)
    anomaly_model, anomaly_bounds = build_anomaly(df)
    classifier_model = build_classifier(df)

    joblib.dump({"model": forecast_model, "columns": forecast_cols},
                os.path.join(MODELS, "forecast.pkl"))
    joblib.dump({"model": anomaly_model, "params": PARAMS, "bounds": anomaly_bounds},
                os.path.join(MODELS, "anomaly.pkl"))
    joblib.dump({"model": classifier_model, "params": PARAMS},
                os.path.join(MODELS, "classifier.pkl"))
    print(f"Saved 3 models to {MODELS}/")


if __name__ == "__main__":
    main()
