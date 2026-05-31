"""Realistic weather data simulator.

Unlike a pure random generator, this models physically plausible dynamics so that
the downstream AI (forecasting / anomaly detection / classification) has real
structure to learn from:

  * Temperature  : seasonal baseline (day-of-year) + diurnal cycle (hour-of-day)
                   + AR(1) smoothed noise.
  * Humidity     : inversely correlated with temperature, rises during storms.
  * Pressure     : sea-level-adjusted random walk that DROPS before/during storms.
  * Precipitation: ~0 in fair weather, elevated during storm events.
  * Wind speed   : low baseline with gusts, spikes during storms.
  * Anomalies    : occasional injected out-of-range spikes (sensor faults).

Pressure is emitted as sea-level-adjusted (QNH-style) so values are comparable
across stations and consistent with the meteorological thresholds in the config.
Station altitude lives in the metadata table, not in the emitted pressure.

The module is importable: data_producer/producer.py uses it for the live stream,
and ai_module/generate_history.py reuses it to build a long training history.
"""

import math
import random
from datetime import datetime, timedelta, timezone


class StationState:
    """Mutable per-station simulation state."""

    def __init__(self, station_id, seed=None):
        self.station_id = station_id
        self.rng = random.Random(seed)
        # AR(1) temperature noise term (smooth, autocorrelated)
        self.temp_noise = 0.0
        # Sea-level pressure random walk around 1013 hPa
        self.pressure = 1013.0 + self.rng.uniform(-4, 4)
        # Storm state
        self.storm_remaining = 0   # readings left in the current storm
        self.storm_intensity = 0.0  # 0..1


class WeatherSimulator:
    def __init__(self, station_ids, sim_minutes_per_reading=10,
                 storm_probability=0.02, storm_duration_readings=12,
                 anomaly_probability=0.01, start_time=None, seed=42):
        self.sim_step = timedelta(minutes=sim_minutes_per_reading)
        self.storm_probability = storm_probability
        self.storm_duration = storm_duration_readings
        self.anomaly_probability = anomaly_probability
        self.sim_time = start_time or datetime.now(timezone.utc)
        self.states = {
            sid: StationState(sid, seed=(seed + i))
            for i, sid in enumerate(station_ids)
        }

    # --- helpers -----------------------------------------------------------
    @staticmethod
    def _seasonal_baseline(dt):
        """Yearly temperature baseline (deg C) for a Kosovo-like climate.

        Coldest ~ Jan (day 15), warmest ~ Jul. Mean ~12C, amplitude ~13C.
        """
        day_of_year = dt.timetuple().tm_yday
        phase = 2 * math.pi * (day_of_year - 15) / 365.0
        return 12.0 - 13.0 * math.cos(phase)

    @staticmethod
    def _diurnal_offset(dt):
        """Daily temperature swing: coldest ~05:00, warmest ~15:00."""
        hour = dt.hour + dt.minute / 60.0
        phase = 2 * math.pi * (hour - 15.0) / 24.0
        return 6.0 * math.cos(phase)

    def _maybe_start_storm(self, st):
        if st.storm_remaining <= 0 and st.rng.random() < self.storm_probability:
            st.storm_remaining = self.storm_duration + st.rng.randint(-4, 6)
            st.storm_intensity = st.rng.uniform(0.4, 1.0)

    # --- main step ---------------------------------------------------------
    def step(self):
        """Advance simulated time by one step; return a reading per station."""
        self.sim_time += self.sim_step
        readings = []
        for st in self.states.values():
            readings.append(self._reading_for(st))
        return readings

    def _reading_for(self, st):
        rng = st.rng
        dt = self.sim_time

        self._maybe_start_storm(st)
        storm_active = st.storm_remaining > 0
        intensity = st.storm_intensity if storm_active else 0.0

        # Temperature: seasonal + diurnal + AR(1) noise, minus storm cooling
        st.temp_noise = 0.8 * st.temp_noise + 0.2 * rng.gauss(0, 1.5)
        temperature = (self._seasonal_baseline(dt)
                       + self._diurnal_offset(dt)
                       + st.temp_noise
                       - 4.0 * intensity)

        # Humidity: inversely related to temperature, higher in storms
        humidity = 70.0 - 1.1 * (temperature - 12.0) + 25.0 * intensity + rng.gauss(0, 3)
        humidity = max(5.0, min(100.0, humidity))

        # Pressure: random walk, drops with storm intensity
        st.pressure += rng.gauss(0, 0.4) - 6.0 * intensity * (1.0 / max(st.storm_remaining, 1))
        st.pressure = max(960.0, min(1045.0, st.pressure))
        # nudge back toward 1013 in fair weather
        if not storm_active:
            st.pressure += (1013.0 - st.pressure) * 0.02
        pressure = st.pressure - 8.0 * intensity

        # Precipitation: zero in fair weather, scaled by intensity in storms
        if storm_active:
            precipitation = max(0.0, rng.gauss(8.0 * intensity, 4.0))
        else:
            precipitation = 0.0 if rng.random() > 0.05 else round(rng.uniform(0, 1.5), 2)

        # Wind: low baseline + gusts, spikes in storms
        wind_speed = abs(rng.gauss(3.0, 1.5)) + 14.0 * intensity * abs(rng.gauss(0.6, 0.3))

        if storm_active:
            st.storm_remaining -= 1
            if st.storm_remaining <= 0:
                st.storm_intensity = 0.0

        reading = {
            "station_id": st.station_id,
            "timestamp": dt.isoformat(),
            "temperature": round(temperature, 2),
            "humidity": round(humidity, 2),
            "pressure": round(pressure, 2),
            "precipitation": round(precipitation, 2),
            "wind_speed": round(wind_speed, 2),
        }

        # Injected sensor anomaly (rare): push one field out of range
        if rng.random() < self.anomaly_probability:
            field = rng.choice(["temperature", "humidity", "pressure",
                                 "precipitation", "wind_speed"])
            spikes = {
                "temperature": rng.choice([-25.0, 55.0]),
                "humidity": rng.choice([0.0, 100.0]),
                "pressure": rng.choice([955.0, 1050.0]),
                "precipitation": rng.uniform(40.0, 70.0),
                "wind_speed": rng.uniform(30.0, 45.0),
            }
            reading[field] = round(spikes[field], 2)
            reading["_anomaly_injected"] = field

        return reading
