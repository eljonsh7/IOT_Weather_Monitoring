"use strict";

const REFRESH_MS = 4000;
const PARAMS = [
  { key: "temperature", label: "Temperature", unit: "°C", color: "#f87171" },
  { key: "humidity", label: "Humidity", unit: "%", color: "#38bdf8" },
  { key: "pressure", label: "Pressure", unit: "hPa", color: "#a78bfa" },
  { key: "precipitation", label: "Precipitation", unit: "mm/h", color: "#34d399" },
  { key: "wind_speed", label: "Wind Speed", unit: "m/s", color: "#fbbf24" },
];

const charts = {};
let currentStation = null;
let pollTimer = null;

function $(id) { return document.getElementById(id); }

function initCharts() {
  for (const p of PARAMS) {
    const ctx = $("chart-" + p.key);
    charts[p.key] = new Chart(ctx, {
      type: "line",
      data: { labels: [], datasets: [{
        label: `${p.label} (${p.unit})`,
        data: [], borderColor: p.color, backgroundColor: p.color + "33",
        tension: 0.3, pointRadius: 0, borderWidth: 2, fill: true,
      }]},
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: { legend: { labels: { color: "#e2e8f0" } } },
        scales: {
          x: { ticks: { color: "#94a3b8", maxTicksLimit: 6 }, grid: { color: "#33415555" } },
          y: { ticks: { color: "#94a3b8" }, grid: { color: "#33415555" } },
        },
      },
    });
  }
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

function fmtTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function renderCards(latest) {
  const cards = $("cards");
  cards.innerHTML = "";
  for (const p of PARAMS) {
    const v = latest[p.key];
    const div = document.createElement("div");
    div.className = "card";
    div.innerHTML = `<div class="label">${p.label}</div>
      <div class="value">${v != null ? v.toFixed(1) : "—"}<span class="unit"> ${p.unit}</span></div>`;
    cards.appendChild(div);
  }
}

function updateCharts(rows) {
  const labels = rows.map(r => fmtTime(r.timestamp));
  for (const p of PARAMS) {
    const c = charts[p.key];
    c.data.labels = labels;
    c.data.datasets[0].data = rows.map(r => r[p.key]);
    c.update();
  }
}

function renderInsights(ins) {
  const el = $("ai-content");
  if (!ins.ai_available) {
    el.innerHTML = `<p class="muted">AI models not loaded. Run ai_module/train_models.py.</p>`;
    return;
  }
  const cond = ins.condition || "stable";
  const anom = ins.anomaly || { is_anomaly: false };
  el.innerHTML = `
    <div class="ai-row"><span>Forecast (next temp)</span>
      <strong>${ins.forecast_next_temp != null ? ins.forecast_next_temp.toFixed(1) + " °C" : "—"}</strong></div>
    <div class="ai-row"><span>Condition</span>
      <span class="badge ${cond}">${cond}</span></div>
    <div class="ai-row"><span>Anomaly</span>
      <span class="badge ${anom.is_anomaly ? "anomaly" : "clean"}">
        ${anom.is_anomaly ? "⚠ " + (anom.reason || "anomaly") : "clean"}</span></div>`;
}

function renderMetadata(m) {
  const el = $("metadata-content");
  if (m.error) { el.innerHTML = `<p class="muted">${m.error}</p>`; return; }
  el.innerHTML = `<table class="meta">
    <tr><td>Name</td><td>${m.name}</td></tr>
    <tr><td>City</td><td>${m.city}</td></tr>
    <tr><td>Coordinates</td><td>${m.latitude}, ${m.longitude}</td></tr>
    <tr><td>Altitude</td><td>${m.altitude_m} m</td></tr>
    <tr><td>Sensors</td><td>${(m.sensors || []).join(", ")}</td></tr>
    <tr><td>Installed</td><td>${m.install_date ? m.install_date.slice(0,10) : "—"}</td></tr>
  </table>`;
}

function renderAlerts(alerts) {
  const list = $("alerts-list");
  const banner = $("alerts-banner");
  if (!alerts.length) {
    list.innerHTML = `<li class="muted">None</li>`;
    banner.classList.add("hidden");
    return;
  }
  // Banner: most recent alert within ~2 minutes
  const newest = alerts[0];
  const ageMs = Date.now() - new Date(newest.timestamp).getTime();
  if (ageMs < 120000) {
    banner.textContent = `${newest.severity.toUpperCase()}: ${newest.message}`;
    banner.className = "alerts-banner" + (newest.severity === "warning" ? " warning" : "");
  } else {
    banner.classList.add("hidden");
  }
  list.innerHTML = alerts.map(a =>
    `<li><span class="sev sev-${a.severity}">${a.severity.toUpperCase()}</span> ·
     ${fmtTime(a.timestamp)} · ${a.message}</li>`).join("");
}

function setStatus(live) {
  $("status-dot").className = "dot " + (live ? "live" : "stale");
  $("status-text").textContent = live ? "live" : "waiting for data…";
}

async function refresh() {
  const station = currentStation;
  try {
    const [rows, insights, alerts] = await Promise.all([
      getJSON(`/api/data/${station}?limit=60`),
      getJSON(`/api/insights/${station}`),
      getJSON(`/api/alerts/${station}?limit=20`),
    ]);
    if (station !== currentStation) return; // station changed mid-flight
    if (rows.length) {
      renderCards(rows[rows.length - 1]);
      updateCharts(rows);
      setStatus(true);
    } else {
      setStatus(false);
    }
    if (!insights.error) renderInsights(insights);
    renderAlerts(alerts);
  } catch (e) {
    console.error(e);
    setStatus(false);
  }
}

async function loadStation(station) {
  currentStation = station;
  try {
    const meta = await getJSON(`/api/metadata/${station}`);
    renderMetadata(meta);
  } catch (e) { console.error(e); }
  await refresh();
}

function start() {
  initCharts();
  const select = $("station");
  currentStation = select.value;
  select.addEventListener("change", () => loadStation(select.value));
  loadStation(currentStation);
  pollTimer = setInterval(refresh, REFRESH_MS);
}

document.addEventListener("DOMContentLoaded", start);
