"use strict";

const REFRESH_MS = 4000;

const PARAMS = [
  { key: "temperature",   label: "Temperature",   unit: "°C",   icon: "🌡️", color: "#f87171" },
  { key: "humidity",      label: "Humidity",      unit: "%",    icon: "💧", color: "#38bdf8" },
  { key: "pressure",      label: "Pressure",      unit: "hPa",  icon: "🌐", color: "#a78bfa" },
  { key: "precipitation", label: "Precipitation", unit: "mm/h", icon: "🌧️", color: "#34d399" },
  { key: "wind_speed",    label: "Wind Speed",    unit: "m/s",  icon: "💨", color: "#fbbf24" },
];

const charts       = {};
let currentStation = null;
let currentHours   = 1;
let pollTimer      = null;
let prevValues     = {};

function $(id) { return document.getElementById(id); }

// ── Charts ────────────────────────────────────────────────

function initCharts() {
  for (const p of PARAMS) {
    const ctx = $("chart-" + p.key);
    charts[p.key] = new Chart(ctx, {
      type: "line",
      data: {
        labels: [],
        datasets: [{
          label: `${p.label} (${p.unit})`,
          data: [],
          borderColor: p.color,
          backgroundColor: p.color + "1a",
          tension: 0.35,
          pointRadius: 0,
          borderWidth: 2,
          fill: true,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { labels: { color: "#94a3b8", font: { size: 11 } } },
          tooltip: {
            backgroundColor: "#1e293b",
            borderColor: "#2d3f58",
            borderWidth: 1,
            titleColor: "#94a3b8",
            bodyColor: "#e2e8f0",
            callbacks: {
              label: ctx => `${ctx.parsed.y?.toFixed(2)} ${p.unit}`,
            },
          },
        },
        scales: {
          x: {
            ticks: { color: "#64748b", maxTicksLimit: 6, font: { size: 10 } },
            grid:  { color: "#1e293b" },
          },
          y: {
            ticks: { color: "#64748b", font: { size: 10 } },
            grid:  { color: "#253047" },
          },
        },
      },
    });
  }
}

function updateCharts(rows) {
  const labels = rows.map(r => {
    const d = new Date(r.timestamp);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  });
  for (const p of PARAMS) {
    const c = charts[p.key];
    c.data.labels            = labels;
    c.data.datasets[0].data  = rows.map(r => r[p.key]);
    c.update("none");
  }
}

// ── Cards ─────────────────────────────────────────────────

function trendArrow(key, current) {
  const prev = prevValues[key];
  if (prev === undefined) return { arrow: "", cls: "" };
  const diff = current - prev;
  if (Math.abs(diff) < 0.05) return { arrow: "→ stable", cls: "" };
  return diff > 0
    ? { arrow: `↑ +${Math.abs(diff).toFixed(1)}`, cls: "up" }
    : { arrow: `↓ −${Math.abs(diff).toFixed(1)}`, cls: "down" };
}

function cardSeverity(key, value, alerts) {
  const recent = alerts.filter(a => {
    const ageMs = Date.now() - new Date(a.timestamp).getTime();
    return a.parameter === key && ageMs < 60000;
  });
  if (recent.some(a => a.severity === "critical")) return "critical";
  if (recent.some(a => a.severity === "warning"))  return "warning";
  return "";
}

function renderCards(latest, alerts) {
  const container = $("cards");
  container.innerHTML = "";
  for (const p of PARAMS) {
    const v = latest[p.key];
    const { arrow, cls } = trendArrow(p.key, v);
    const sev = cardSeverity(p.key, v, alerts);

    const div = document.createElement("div");
    div.className = `card${sev ? " " + sev : ""}`;
    div.innerHTML = `
      <div class="card-icon">${p.icon}</div>
      <div class="card-label">${p.label}</div>
      <div class="card-value">${v != null ? v.toFixed(1) : "—"}<span class="card-unit">${p.unit}</span></div>
      ${arrow ? `<div class="card-trend ${cls}">${arrow}</div>` : ""}
    `;
    container.appendChild(div);
    prevValues[p.key] = v;
  }
}

// ── AI Insights ───────────────────────────────────────────

function renderInsights(ins) {
  const el = $("ai-content");
  if (!ins.ai_available) {
    el.innerHTML = `<p class="muted">AI models not loaded.</p>`;
    return;
  }
  const cond = ins.condition || "stable";
  const anom = ins.anomaly  || { is_anomaly: false };
  const forecast = ins.forecast_next_temp;

  el.innerHTML = `
    <div class="ai-row">
      <span>Forecast (next temp)</span>
      <strong style="color:#f87171">${forecast != null ? forecast.toFixed(1) + " °C" : "—"}</strong>
    </div>
    <div class="ai-row">
      <span>Weather Condition</span>
      <span class="badge ${cond}">${cond}</span>
    </div>
    <div class="ai-row">
      <span>Anomaly Detection</span>
      <span class="badge ${anom.is_anomaly ? "anomaly" : "clean"}">
        ${anom.is_anomaly ? "⚠ " + (anom.reason || "anomaly") : "✓ clean"}
      </span>
    </div>
  `;
}

// ── Metadata ──────────────────────────────────────────────

function renderMetadata(m) {
  const el = $("metadata-content");
  if (m.error) { el.innerHTML = `<p class="muted">${m.error}</p>`; return; }
  el.innerHTML = `
    <table class="meta-table">
      <tr><td>Name</td><td>${m.name}</td></tr>
      <tr><td>City</td><td>${m.city}</td></tr>
      <tr><td>Coordinates</td><td>${m.latitude?.toFixed(4)}, ${m.longitude?.toFixed(4)}</td></tr>
      <tr><td>Altitude</td><td>${m.altitude_m} m</td></tr>
      <tr><td>Sensors</td><td>${(m.sensors || []).join(", ")}</td></tr>
      <tr><td>Installed</td><td>${m.install_date ? m.install_date.slice(0, 10) : "—"}</td></tr>
    </table>
  `;
}

// ── Alerts ────────────────────────────────────────────────

function renderAlerts(alerts) {
  const list   = $("alerts-list");
  const banner = $("alerts-banner");
  const counts = $("alert-counts");

  if (!alerts.length) {
    list.innerHTML = `<li class="muted">No alerts in this period.</li>`;
    banner.className = "alerts-banner hidden";
    counts.innerHTML = "";
    return;
  }

  // Count severities
  const nCrit = alerts.filter(a => a.severity === "critical").length;
  const nWarn = alerts.filter(a => a.severity === "warning").length;
  counts.innerHTML = [
    nCrit ? `<span class="count-badge critical">⚡ ${nCrit} critical</span>` : "",
    nWarn ? `<span class="count-badge warning">⚠ ${nWarn} warning</span>`   : "",
  ].join("");

  // Banner: most recent within 2 minutes
  const newest = alerts[0];
  const ageMs  = Date.now() - new Date(newest.timestamp).getTime();
  if (ageMs < 120000) {
    const icon = newest.severity === "critical" ? "⚡" : "⚠️";
    banner.textContent = `${icon}  ${newest.severity.toUpperCase()} · ${newest.message}`;
    banner.className   = `alerts-banner ${newest.severity}`;
  } else {
    banner.className = "alerts-banner hidden";
  }

  // List items
  list.innerHTML = alerts.map(a => {
    const t = new Date(a.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    return `
      <li>
        <span class="sev-tag ${a.severity}">${a.severity}</span>
        <span class="alert-time">${t}</span>
        <span class="alert-msg">${a.message}</span>
      </li>
    `;
  }).join("");
}

// ── Network ───────────────────────────────────────────────

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

function setStatus(live) {
  $("status-dot").className  = "dot " + (live ? "live" : "stale");
  $("status-text").textContent = live ? "live" : "waiting for data…";
}

// ── Refresh ───────────────────────────────────────────────

async function refresh() {
  const station = currentStation;
  const hours   = currentHours;
  try {
    const [rows, insights, alerts] = await Promise.all([
      getJSON(`/api/data/${station}?hours=${hours}`),
      getJSON(`/api/insights/${station}`),
      getJSON(`/api/alerts/${station}?limit=30`),
    ]);
    if (station !== currentStation) return;
    if (rows.length) {
      renderCards(rows[rows.length - 1], alerts);
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

// ── Station load ──────────────────────────────────────────

async function loadStation(station) {
  currentStation = station;
  prevValues = {};
  try {
    const meta = await getJSON(`/api/metadata/${station}`);
    renderMetadata(meta);
  } catch (e) { console.error(e); }
  await refresh();
}

// ── Time range ────────────────────────────────────────────

function setTimeRange(hours) {
  currentHours = hours;
  document.querySelectorAll(".range-btn").forEach(btn => {
    btn.classList.toggle("active", parseFloat(btn.dataset.hours) === hours);
  });
  refresh();
}

// ── Export PDF ────────────────────────────────────────────

function exportPDF() {
  const url = `/api/export/pdf/${currentStation}?hours=${currentHours}`;
  const btn = $("export-btn");
  btn.textContent = "⏳ Generating…";
  btn.disabled = true;

  fetch(url)
    .then(res => {
      if (!res.ok) throw new Error("PDF generation failed");
      return res.blob();
    })
    .then(blob => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `weather_report_${currentStation}_${new Date().toISOString().slice(0,16).replace("T","_")}.pdf`;
      a.click();
      URL.revokeObjectURL(a.href);
    })
    .catch(err => { console.error(err); alert("PDF export failed. See console."); })
    .finally(() => {
      btn.textContent = "📥 Export PDF";
      btn.disabled = false;
    });
}

// ── Boot ──────────────────────────────────────────────────

function start() {
  initCharts();

  const select = $("station");
  currentStation = select.value;

  select.addEventListener("change", () => loadStation(select.value));

  document.querySelectorAll(".range-btn").forEach(btn => {
    btn.addEventListener("click", () => setTimeRange(parseFloat(btn.dataset.hours)));
  });

  $("export-btn").addEventListener("click", exportPDF);

  loadStation(currentStation);
  pollTimer = setInterval(refresh, REFRESH_MS);
}

document.addEventListener("DOMContentLoaded", start);
