/**
 * Real-time IoT Topology Monitor - Frontend
 * Connects via WebSocket for live updates.
 */

// ═══════════════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════════════

const state = {
  connected: false,
  ws: null,
  devices: new Map(),
  edges: [],
  events: [],
  kpis: {},
};

// ═══════════════════════════════════════════════════════════
// WEBSOCKET
// ═══════════════════════════════════════════════════════════

function connectWebSocket() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${location.host}/ws/live`;

  state.ws = new WebSocket(wsUrl);

  state.ws.onopen = () => {
    state.connected = true;
    updateStatusIndicator();
    console.log("WebSocket connected");
  };

  state.ws.onclose = () => {
    state.connected = false;
    updateStatusIndicator();
    console.log("WebSocket disconnected, retrying in 3s...");
    setTimeout(connectWebSocket, 3000);
  };

  state.ws.onerror = (err) => {
    console.error("WebSocket error:", err);
  };

  state.ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    handleMessage(msg);
  };
}

function handleMessage(msg) {
  const ts = new Date().toLocaleTimeString();
  document.getElementById("last-update").textContent = `Last update: ${ts}`;

  if (msg.type === "event") {
    addEvent(msg.data);
  } else if (msg.type === "kpi_update") {
    updateKPIs(msg.data);
  } else if (msg.type === "ai_insight") {
    handleAIInsightMessage(msg.data);
  } else if (msg.type === "connected") {
    console.log(msg.data.message);
    loadInitialData();
  }
}

setInterval(() => {
  if (state.connected) {
    state.ws.send("ping");
  }
}, 30000);

// ═══════════════════════════════════════════════════════════
// INITIAL DATA (REST API)
// ═══════════════════════════════════════════════════════════

async function loadInitialData() {
  try {
    const kpis = await fetch("/api/kpis").then(r => r.json());
    updateKPIs(kpis);

    const devices = await fetch("/api/devices?limit=1000").then(r => r.json());
    devices.forEach(d => state.devices.set(d.device_id, d));
    renderDeviceList();

    const graphData = await fetch("/api/topology/graph").then(r => r.json());
    state.edges = graphData.edges;
    state.devices.clear();
    graphData.nodes.forEach(n => state.devices.set(n.device_id, n));
    renderTopology();

    const events = await fetch("/api/events?limit=50").then(r => r.json());
    state.events = events;
    renderEvents();
  } catch (e) {
    console.error("Failed to load initial data:", e);
  }
}

// ═══════════════════════════════════════════════════════════
// RENDERING
// ═══════════════════════════════════════════════════════════

function updateStatusIndicator() {
  const el = document.getElementById("ws-status");
  if (!el) return;
  if (state.connected) {
    el.textContent = "Connected";
    el.className = "ws-dot connected";
  } else {
    el.textContent = "Disconnected";
    el.className = "ws-dot disconnected";
  }
}

function updateKPIs(kpis) {
  state.kpis = kpis;
  document.getElementById("kpi-total").querySelector(".kpi-value").textContent = kpis.total_devices;
  document.getElementById("kpi-online").querySelector(".kpi-value").textContent = kpis.online;
  document.getElementById("kpi-offline").querySelector(".kpi-value").textContent = kpis.offline;
}

function addEvent(event) {
  state.events.unshift(event);
  if (state.events.length > 100) state.events.pop();
  renderEvents();

  if (event.severity === "critical" || event.severity === "error") {
    flashBrowserNotification(event);
  }
}

function renderEvents() {
  const container = document.getElementById("event-list");
  if (!container) return;
  container.innerHTML = state.events.slice(0, 30).map(e => {
    const time = new Date(e.event_time).toLocaleTimeString();
    const severityClass = ["critical", "error"].includes(e.severity) ? "critical" :
      e.severity === "warning" ? "warning" : "info";
    return `
      <div class="event ${severityClass}">
        <span class="event-time">${time}</span>
        <span class="event-severity">${e.severity}</span>
        <span class="event-device">${e.device_id}</span>
        <span class="event-message">${e.message || ""}</span>
      </div>
    `;
  }).join("");
}

function renderDeviceList() {
  // Could be expanded to show device list
}

function renderTopology() {
  const svg = document.getElementById("graph");
  svg.innerHTML = "";

  const positions = new Map();
  const typeGroups = {};

  state.devices.forEach((d, id) => {
    if (!typeGroups[d.device_type]) typeGroups[d.device_type] = [];
    typeGroups[d.device_type].push(id);
  });

  const types = Object.keys(typeGroups);
  const typesPerRow = Math.ceil(Math.sqrt(types.length));

  types.forEach((type, i) => {
    typeGroups[type].forEach((id, j) => {
      const col = i % typesPerRow;
      const row = Math.floor(i / typesPerRow);
      positions.set(id, {
        x: 100 + col * 200 + (j * 30),
        y: 100 + row * 150 + (j * 20)
      });
    });
  });

  const edgesGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
  state.edges.forEach(edge => {
    const src = positions.get(edge.source_id);
    const tgt = positions.get(edge.target_id);
    if (src && tgt) {
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", src.x);
      line.setAttribute("y1", src.y);
      line.setAttribute("x2", tgt.x);
      line.setAttribute("y2", tgt.y);
      line.setAttribute("stroke", "#2f3833");
      line.setAttribute("stroke-width", "1");
      edgesGroup.appendChild(line);
    }
  });
  svg.appendChild(edgesGroup);

  state.devices.forEach((device, id) => {
    const pos = positions.get(id);
    if (!pos) return;

    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");

    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", pos.x);
    circle.setAttribute("cy", pos.y);
    circle.setAttribute("r", 8);

    const status = device.status || "unknown";
    const color = {
      "online": "#2ea043",
      "offline": "#c0392b",
      "degraded": "#b8860b",
      "unknown": "#7a8580"
    }[status] || "#7a8580";
    circle.setAttribute("fill", color);
    circle.setAttribute("stroke", "#0a0d0c");
    circle.setAttribute("stroke-width", "1.5");

    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", pos.x);
    text.setAttribute("y", pos.y + 22);
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("font-size", "10");
    text.setAttribute("fill", "#b5bcb8");
    text.setAttribute("font-family", "Inter, system-ui, sans-serif");
    text.textContent = id.length > 15 ? id.substring(0, 12) + "..." : id;

    g.appendChild(circle);
    g.appendChild(text);

    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = `${id}\nType: ${device.device_type}\nStatus: ${status}`;
    g.appendChild(title);

    svg.appendChild(g);
  });
}

function flashBrowserNotification(event) {
  if (Notification.permission === "granted") {
    new Notification(`Critical: ${event.device_id}`, {
      body: event.message || "Device alert",
      icon: "/static/alert.png"
    });
  } else if (Notification.permission !== "denied") {
    Notification.requestPermission();
  }
}

// ═══════════════════════════════════════════════════════════
// DRILL-DOWN
// ═══════════════════════════════════════════════════════════

let activeFilter = null;

function applyFilter(filter) {
  if (activeFilter && filter && activeFilter.kind === filter.kind && activeFilter.value === filter.value) {
    activeFilter = null;
  } else {
    activeFilter = filter;
  }

  document.querySelectorAll(".kpi-card").forEach((el) => el.classList.remove("kpi-active"));

  if (!activeFilter) {
    clearDrilldown();
    renderDeviceTypeChips();
    return;
  }

  if (activeFilter.kind === "all" || activeFilter.kind === "status" || activeFilter.kind === "events") {
    const kpi = document.querySelector(`.kpi-card[data-filter="${cssEscapeAttr(activeFilter.kind + (activeFilter.value ? ":" + activeFilter.value : ""))}"]`);
    if (kpi) kpi.classList.add("kpi-active");
  }

  renderDeviceTypeChips();
  renderDrilldownTable();
}

function clearDrilldown() {
  const container = document.getElementById("drilldown-table-container");
  if (!container) return;
  container.innerHTML = '<p class="muted">Click a KPI card above, a device type, or a node in the topology to see the matching devices here.</p>';
}

function renderDeviceTypeChips() {
  const container = document.getElementById("device-type-chips");
  if (!container) return;

  const counts = new Map();
  state.devices.forEach((d) => {
    const t = d.device_type || "unknown";
    counts.set(t, (counts.get(t) || 0) + 1);
  });

  if (counts.size === 0) {
    container.innerHTML = '<span class="muted">No devices loaded yet.</span>';
    return;
  }

  const types = Array.from(counts.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

  container.innerHTML = types
    .map(([type, count]) => {
      const isActive = activeFilter && activeFilter.kind === "type" && activeFilter.value === type;
      return `<button class="type-chip${isActive ? " active" : ""}" data-type="${escapeAttr(type)}">
                <span class="type-name">${escapeHtml(type)}</span>
                <span class="type-count">${count}</span>
              </button>`;
    })
    .join("");

  container.querySelectorAll(".type-chip").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyFilter({ kind: "type", value: btn.getAttribute("data-type") });
    });
  });
}

async function renderDrilldownTable() {
  const container = document.getElementById("drilldown-table-container");
  if (!container || !activeFilter) return;

  let title = "";
  if (activeFilter.kind === "all") title = "All devices";
  else if (activeFilter.kind === "status") title = `Devices with status: ${activeFilter.value}`;
  else if (activeFilter.kind === "type") title = `Devices of type: ${activeFilter.value}`;
  else if (activeFilter.kind === "device") title = `Device: ${activeFilter.value}`;
  else if (activeFilter.kind === "events") title = `Critical events (last ${activeFilter.hoursBack || 24}h)`;

  container.innerHTML = `
    <div class="table-header">
      <strong>${escapeHtml(title)}</strong>
      <button class="clear-btn" id="drilldown-clear">✕ Clear</button>
    </div>
    <p class="muted">Loading…</p>
  `;

  document.getElementById("drilldown-clear")?.addEventListener("click", () => {
    activeFilter = null;
    document.querySelectorAll(".kpi-card").forEach((el) => el.classList.remove("kpi-active"));
    renderDeviceTypeChips();
    clearDrilldown();
  });

  try {
    if (activeFilter.kind === "events") {
      await renderEventsTable(container, title);
    } else {
      await renderDevicesTable(container, title);
    }

    document.getElementById("drilldown").scrollIntoView({
      behavior: "smooth",
      block: "start"
    });

  } catch (e) {
    console.error("Failed to load drill-down:", e);
    container.innerHTML = `<p class="error">Failed to load: ${escapeHtml(String(e))}</p>`;
  }
}

async function renderDevicesTable(container, title) {
  const params = new URLSearchParams();
  params.set("limit", "10000");
  if (activeFilter.kind === "status") params.set("status", activeFilter.value);
  else if (activeFilter.kind === "type") params.set("device_type", activeFilter.value);

  const res = await fetch(`/api/devices?${params.toString()}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  let devices = await res.json();

  if (activeFilter.kind === "device") {
    devices = devices.filter((d) => d.device_id === activeFilter.value);
  }

  if (!devices.length) {
    container.innerHTML = `
      <div class="table-header">
        <strong>${escapeHtml(title)}</strong>
        <button class="clear-btn" id="drilldown-clear">✕ Clear</button>
      </div>
      <p class="muted">No devices match this filter.</p>
    `;
    document.getElementById("drilldown-clear")?.addEventListener("click", () => {
      activeFilter = null;
      document.querySelectorAll(".kpi-card").forEach((el) => el.classList.remove("kpi-active"));
      renderDeviceTypeChips();
      clearDrilldown();
    });
    return;
  }

  const preferred = ["device_id", "device_name", "device_type", "vendor", "model", "status", "ip_address", "mac_address", "site_id", "last_seen"];
  const seen = new Set(preferred);
  const columns = [...preferred];
  devices.forEach((d) => {
    Object.keys(d).forEach((k) => {
      if (!seen.has(k)) {
        seen.add(k);
        columns.push(k);
      }
    });
  });

  const thead = `<thead><tr>${columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}</tr></thead>`;
  const tbody = `<tbody>${devices
    .map(
      (d) =>
        `<tr>${columns
          .map((c) => {
            const v = d[c];
            if (v === null || v === undefined || v === "") return `<td class="muted">—</td>`;
            if (c === "status") return `<td><span class="status-pill status-${escapeAttr(String(v))}">${escapeHtml(String(v))}</span></td>`;
            if (c === "last_seen") {
              const dt = new Date(v);
              return `<td>${isNaN(dt) ? escapeHtml(String(v)) : escapeHtml(dt.toLocaleString())}</td>`;
            }
            return `<td>${escapeHtml(String(v))}</td>`;
          })
          .join("")}</tr>`
    )
    .join("")}</tbody>`;

  container.innerHTML = `
    <div class="table-header">
      <strong>${escapeHtml(title)}</strong>
      <span class="muted">${devices.length} device${devices.length === 1 ? "" : "s"}</span>
      <button class="clear-btn" id="drilldown-clear">✕ Clear</button>
    </div>
    <div class="table-wrap">
      <table class="device-table">${thead}${tbody}</table>
    </div>
  `;

  document.getElementById("drilldown-clear")?.addEventListener("click", () => {
    activeFilter = null;
    document.querySelectorAll(".kpi-card").forEach((el) => el.classList.remove("kpi-active"));
    renderDeviceTypeChips();
    clearDrilldown();
  });
}

async function renderEventsTable(container, title) {
  const params = new URLSearchParams();
  params.set("severity", activeFilter.value);
  params.set("hours_back", String(activeFilter.hoursBack || 24));
  params.set("limit", "200");

  const res = await fetch(`/api/events?${params.toString()}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const events = await res.json();

  if (!events.length) {
    container.innerHTML = `
      <div class="table-header">
        <strong>${escapeHtml(title)}</strong>
        <button class="clear-btn" id="drilldown-clear">✕ Clear</button>
      </div>
      <p class="muted">No ${escapeHtml(activeFilter.value)} events in the last ${activeFilter.hoursBack || 24}h.</p>
    `;
    document.getElementById("drilldown-clear")?.addEventListener("click", () => {
      activeFilter = null;
      document.querySelectorAll(".kpi-card").forEach((el) => el.classList.remove("kpi-active"));
      renderDeviceTypeChips();
      clearDrilldown();
    });
    return;
  }

  const columns = ["event_time", "device_id", "device_type", "severity", "status", "status_code", "message", "source_system"];
  const thead = `<thead><tr>${columns.map((c) => `<th>${escapeHtml(c)}</th>`).join("")}</tr></thead>`;
  const tbody = `<tbody>${events
    .map((e) => {
      return `<tr>${columns
        .map((c) => {
          const v = e[c];
          if (v === null || v === undefined || v === "") return `<td class="muted">—</td>`;
          if (c === "event_time") {
            const dt = new Date(v);
            return `<td>${isNaN(dt) ? escapeHtml(String(v)) : escapeHtml(dt.toLocaleString())}</td>`;
          }
          if (c === "severity") return `<td><span class="status-pill status-${escapeAttr(String(v))}">${escapeHtml(String(v))}</span></td>`;
          return `<td>${escapeHtml(String(v))}</td>`;
        })
        .join("")}</tr>`;
    })
    .join("")}</tbody>`;

  container.innerHTML = `
    <div class="table-header">
      <strong>${escapeHtml(title)}</strong>
      <span class="muted">${events.length} event${events.length === 1 ? "" : "s"}</span>
      <button class="clear-btn" id="drilldown-clear">✕ Clear</button>
    </div>
    <div class="table-wrap">
      <table class="device-table">${thead}${tbody}</table>
    </div>
  `;

  document.getElementById("drilldown-clear")?.addEventListener("click", () => {
    activeFilter = null;
    document.querySelectorAll(".kpi-card").forEach((el) => el.classList.remove("kpi-active"));
    renderDeviceTypeChips();
    clearDrilldown();
  });
}

function cssEscapeAttr(s) {
  return s.replace(/"/g, '\\"');
}

function wireKpiCards() {
  document.querySelectorAll(".kpi-card").forEach((card) => {
    card.addEventListener("click", () => {
      const f = card.getAttribute("data-filter");
      if (!f) return;
      let filter;
      if (f === "all") filter = { kind: "all" };
      else if (f.startsWith("status:")) filter = { kind: "status", value: f.split(":")[1] };
      else if (f.startsWith("events:")) filter = { kind: "events", value: f.split(":")[1], hoursBack: 24 };
      applyFilter(filter);
    });
  });
}

// ═══════════════════════════════════════════════════════════
// PLANT STATISTICS
// ═══════════════════════════════════════════════════════════

const statsState = {
  groupBy: "device_type",
  data: null,
};

async function loadStats() {
  const container = document.getElementById("stats-content");
  if (!container) return;
  container.innerHTML = '<p class="muted">Loading…</p>';

  try {
    const res = await fetch(`/api/stats?group_by=${encodeURIComponent(statsState.groupBy)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    statsState.data = await res.json();
    renderStats();
  } catch (e) {
    console.error("Failed to load stats:", e);
    container.innerHTML = `<p class="error">Failed to load stats: ${escapeHtml(String(e))}</p>`;
  }
}

function renderStats() {
  const container = document.getElementById("stats-content");
  if (!container || !statsState.data) return;
  if (statsState.data.ok === false) {
    container.innerHTML = `<p class="error">Stats error: ${escapeHtml(statsState.data.error || "unknown")}</p>`;
    return;
  }

  // Highlight active group button
  document.querySelectorAll("[data-stats-group]").forEach((b) => {
    b.classList.toggle("active", b.getAttribute("data-stats-group") === statsState.groupBy);
  });

  const t = statsState.data.totals.devices;
  const ev = statsState.data.totals.events_24h;
  const breakdown = statsState.data.breakdown || [];

  container.innerHTML = `
    <div class="stats-summary">
      ${statCardHTML("Total devices", t.total, "muted", "")}
      ${statCardHTML("Online", t.online, "ok", `${t.online_pct}%`)}
      ${statCardHTML("Offline", t.offline, t.offline > 0 ? "bad" : "muted", `${t.offline_pct}%`)}
      ${statCardHTML("Degraded", t.degraded, t.degraded > 0 ? "warn" : "muted", `${t.degraded_pct}%`)}
      ${statCardHTML("Events 24h", ev.total, ev.critical > 0 ? "bad" : ev.error > 0 ? "warn" : "muted", `${ev.critical}c · ${ev.error}e · ${ev.warning}w`)}
    </div>

    <div class="stats-breakdown">
      <table class="device-table">
        <thead>
          <tr>
            <th>${escapeHtml(statsState.groupBy.replace("_", " "))}</th>
            <th>Total</th>
            <th>Online</th>
            <th>Offline</th>
            <th>Degraded</th>
            <th>Unknown</th>
            <th>Online %</th>
            <th>Offline %</th>
            <th>Events (24h)</th>
          </tr>
        </thead>
        <tbody>
          ${breakdown.map(row => `
            <tr>
              <td><strong>${escapeHtml(String(row[statsState.groupBy] ?? "—"))}</strong></td>
              <td>${row.total}</td>
              <td><span class="status-pill status-online">${row.online}</span></td>
              <td>${row.offline > 0 ? `<span class="status-pill status-offline">${row.offline}</span>` : `<span class="muted">${row.offline}</span>`}</td>
              <td>${row.degraded > 0 ? `<span class="status-pill status-degraded">${row.degraded}</span>` : `<span class="muted">${row.degraded}</span>`}</td>
              <td class="muted">${row.unknown}</td>
              <td>${barHTML(row.online_pct, "ok")}</td>
              <td>${barHTML(row.offline_pct, "bad")}</td>
              <td>${row.events_24h ? `${row.events_24h.critical}c / ${row.events_24h.error}e / ${row.events_24h.warning}w` : "—"}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function statCardHTML(label, value, tone, sublabel) {
  return `
    <div class="stat-card stat-${tone}">
      <div class="stat-value">${value}</div>
      <div class="stat-label">${escapeHtml(label)}</div>
      ${sublabel ? `<div class="stat-sub">${escapeHtml(sublabel)}</div>` : ""}
    </div>
  `;
}

function barHTML(pct, tone) {
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  return `
    <div class="stat-bar" title="${p}%">
      <div class="stat-bar-fill stat-bar-${tone}" style="width: ${p}%"></div>
      <span class="stat-bar-label">${p}%</span>
    </div>
  `;
}

// Wire up the group buttons + refresh
document.querySelectorAll("[data-stats-group]").forEach((btn) => {
  btn.addEventListener("click", () => {
    statsState.groupBy = btn.getAttribute("data-stats-group");
    loadStats();
  });
});

document.getElementById("stats-refresh-btn")?.addEventListener("click", () => {
  loadStats();
});

// Hook into initial load
const _origLoadStats = loadInitialData;
loadInitialData = async function () {
  await _origLoadStats();
  await loadStats();
};


// ═══════════════════════════════════════════════════════════
// AI INSIGHTS (Microsoft Foundry) + HITL Feedback
// ═══════════════════════════════════════════════════════════

const aiState = {
  insights: [],
  freshIds: new Set(),
  model: "—",
  endpoint: "—",
  editing: new Set(),   // insight_ids currently being edited
};

async function loadAIInsights() {
  const list = document.getElementById("ai-insight-list");
  if (!list) return;
  try {
    const data = await fetch("/api/ai/insights?limit=20").then((r) => r.json());
    aiState.insights = Array.isArray(data) ? data : [];
  } catch (e) {
    console.error("Failed to load AI insights:", e);
    aiState.insights = [];
  }
  try {
    const h = await fetch("/api/ai/health").then((r) => r.json());
    if (h && h.model) {
      aiState.model = h.model;
      const info = document.getElementById("ai-model-info");
      if (info) info.textContent = `Model: ${h.model}`;
      const badge = document.getElementById("ai-status-badge");
      if (badge) {
        badge.textContent = h.foundry_ok ? "Microsoft Foundry · Online" : "Microsoft Foundry · Unreachable";
        badge.classList.toggle("ai-error", !h.foundry_ok);
      }
    }
  } catch (_) {
    // ignore
  }
  renderAIInsights();
}

function renderAIInsights() {
  const list = document.getElementById("ai-insight-list");
  if (!list) return;
  if (!aiState.insights.length) {
    list.innerHTML = '<div class="ai-empty">No AI insights yet. Insights appear here automatically when a device goes offline.</div>';
    return;
  }
  list.innerHTML = aiState.insights.map(insightCardHTML).join("");

  // Re-analyze buttons
  list.querySelectorAll("[data-reanalyze]").forEach((btn) => {
    btn.addEventListener("click", async (ev) => {
      const devId = ev.currentTarget.getAttribute("data-reanalyze");
      ev.currentTarget.disabled = true;
      ev.currentTarget.textContent = "Analyzing…";
      try {
        const res = await fetch(
          `/api/ai/analyze/${encodeURIComponent(devId)}?force=true&replace=true`,
          { method: "POST" }
        );
        if (res.ok) {
          const insight = await res.json();
          aiState.insights = [
            insight,
            ...aiState.insights.filter((x) => x.device_id !== insight.device_id),
          ].slice(0, 20);
          renderAIInsights();
        } else {
          ev.currentTarget.textContent = "Failed";
        }
      } catch (e) {
        console.error("Re-analyze failed:", e);
        ev.currentTarget.textContent = "Failed";
      }
    });
  });

  // Dismiss (X) buttons
  list.querySelectorAll("[data-dismiss]").forEach((btn) => {
    btn.addEventListener("click", async (ev) => {
      const insightId = ev.currentTarget.getAttribute("data-dismiss");
      if (!insightId) return;
      const card = ev.currentTarget.closest(".ai-insight");
      if (card) card.classList.add("dismissing");
      try {
        const res = await fetch(`/api/ai/insights/${encodeURIComponent(insightId)}`, {
          method: "DELETE",
        });
        if (!res.ok) {
          if (card) card.classList.remove("dismissing");
          console.error("Dismiss failed:", res.status);
          return;
        }
        aiState.insights = aiState.insights.filter((x) => String(x.insight_id) !== String(insightId));
        renderAIInsights();
      } catch (e) {
        console.error("Dismiss failed:", e);
        if (card) card.classList.remove("dismissing");
      }
    });
  });

  // HITL feedback: edit
  list.querySelectorAll("[data-edit-conf]").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      const id = parseInt(ev.currentTarget.getAttribute("data-edit-conf"), 10);
      aiState.editing.add(id);
      renderAIInsights();
    });
  });

  // HITL feedback: cancel
  list.querySelectorAll("[data-cancel-conf]").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      const id = parseInt(ev.currentTarget.getAttribute("data-cancel-conf"), 10);
      aiState.editing.delete(id);
      renderAIInsights();
    });
  });

  // HITL feedback: live slider
  list.querySelectorAll("[data-conf-slider]").forEach((slider) => {
    slider.addEventListener("input", (ev) => {
      const id = ev.currentTarget.getAttribute("data-conf-slider");
      const pct = Number(ev.currentTarget.value);
      const bar = document.getElementById(`ai-conf-bar-${id}`);
      const lbl = document.getElementById(`ai-conf-pct-${id}`);
      if (bar) bar.style.width = `${pct}%`;
      if (lbl) lbl.textContent = `${pct}%`;
    });
  });

  // HITL feedback: save
  list.querySelectorAll("[data-save-conf]").forEach((btn) => {
    btn.addEventListener("click", async (ev) => {
      const id = ev.currentTarget.getAttribute("data-save-conf");
      const slider = document.querySelector(`[data-conf-slider="${id}"]`);
      const notesEl = document.querySelector(`[data-conf-notes="${id}"]`);
      if (!slider) return;
      const human_confidence = Number(slider.value) / 100;
      const notes = notesEl ? notesEl.value.trim() : "";

      ev.currentTarget.disabled = true;
      ev.currentTarget.textContent = "Saving…";
      try {
        const res = await fetch(`/api/ai/insights/${id}/feedback`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ human_confidence, feedback_notes: notes || null }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const updated = await res.json();
        aiState.editing.delete(parseInt(id, 10));
        const idx = aiState.insights.findIndex((x) => String(x.insight_id) === String(id));
        if (idx >= 0) aiState.insights[idx] = updated;
        renderAIInsights();
      } catch (e) {
        console.error("Save feedback failed:", e);
        ev.currentTarget.disabled = false;
        ev.currentTarget.textContent = "Save";
      }
    });
  });

  // HITL feedback: reset
  list.querySelectorAll("[data-clear-conf]").forEach((btn) => {
    btn.addEventListener("click", async (ev) => {
      const id = ev.currentTarget.getAttribute("data-clear-conf");
      if (!confirm("Reset to model confidence? Your rating and notes will be cleared.")) return;
      try {
        const res = await fetch(`/api/ai/insights/${id}/feedback`, { method: "DELETE" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const updated = await fetch(`/api/ai/insights/${id}`).then((r) => r.json());
        const idx = aiState.insights.findIndex((x) => String(x.insight_id) === String(id));
        if (idx >= 0) aiState.insights[idx] = updated;
        renderAIInsights();
      } catch (e) {
        console.error("Reset feedback failed:", e);
      }
    });
  });
}

function confidenceEditorHTML(i) {
  const isEditing = aiState.editing.has(i.insight_id);
  const modelConf = Number(i.confidence || 0);
  const humanConf = i.human_confidence != null ? Number(i.human_confidence) : null;
  const effective = i.effective_confidence != null ? Number(i.effective_confidence) : modelConf;
  const delta = humanConf != null ? humanConf - modelConf : 0;
  const notes = i.feedback_notes || "";

  if (!isEditing) {
    const humanRow = humanConf != null
      ? `<div class="ai-conf-row">
           <span class="muted">Your rating:</span>
           <span class="ai-confidence"><span class="ai-confidence-bar"><span style="width:${Math.round(humanConf * 100)}%"></span></span>${Math.round(humanConf * 100)}%</span>
           <span class="${delta > 0 ? "ai-delta-up" : delta < 0 ? "ai-delta-down" : "muted"}">
             ${delta > 0 ? "+" : ""}${Math.round(delta * 100)}% vs model
           </span>
           ${notes ? `<div class="ai-feedback-notes">"${escapeHtml(notes)}"</div>` : ""}
         </div>`
      : "";

    return `
      <div class="ai-conf-editor">
        <div class="ai-conf-row">
          <span class="muted">Model:</span>
          <span class="ai-confidence"><span class="ai-confidence-bar"><span style="width:${Math.round(modelConf * 100)}%"></span></span>${Math.round(modelConf * 100)}%</span>
        </div>
        ${humanRow}
        <div class="ai-conf-actions">
          <button class="clear-btn" data-edit-conf="${escapeAttr(String(i.insight_id))}" type="button">✎ Adjust confidence</button>
          ${humanConf != null ? `<button class="clear-btn" data-clear-conf="${escapeAttr(String(i.insight_id))}" type="button">Reset</button>` : ""}
        </div>
      </div>
    `;
  }

  return `
    <div class="ai-conf-editor editing">
      <div class="ai-conf-row">
        <span class="muted">Model: ${Math.round(modelConf * 100)}%</span>
        <span class="muted">→</span>
        <span>Your rating:</span>
        <span class="ai-confidence"><span class="ai-confidence-bar"><span id="ai-conf-bar-${i.insight_id}" style="width:${Math.round(effective * 100)}%"></span></span>
          <span id="ai-conf-pct-${i.insight_id}">${Math.round(effective * 100)}%</span>
        </span>
      </div>
      <input
        type="range"
        min="0" max="100" step="1"
        value="${Math.round(effective * 100)}"
        data-conf-slider="${escapeAttr(String(i.insight_id))}"
        class="ai-conf-slider"
      />
      <textarea
        data-conf-notes="${escapeAttr(String(i.insight_id))}"
        class="ai-conf-notes"
        placeholder="Why this rating? (optional — e.g. 'model missed the cascading PoE failure on the upstream switch')"
        rows="2"
      >${escapeHtml(notes)}</textarea>
      <div class="ai-conf-actions">
        <button class="clear-btn primary" data-save-conf="${escapeAttr(String(i.insight_id))}" type="button">Save</button>
        <button class="clear-btn" data-cancel-conf="${escapeAttr(String(i.insight_id))}" type="button">Cancel</button>
      </div>
    </div>
  `;
}

function insightCardHTML(i) {
  const isFresh = aiState.freshIds.has(i.insight_id);
  aiState.freshIds.delete(i.insight_id);
  const sev = (i.severity || "warning").toLowerCase();
  const sevClass = ["critical", "error"].includes(sev) ? "critical" : sev === "warning" ? "warning" : "";
  const okClass = i.ok === false ? "failed" : "";
  const conf = Number(i.effective_confidence != null ? i.effective_confidence : (i.confidence || 0));
  const confPct = Math.round(conf * 100);
  const confTier = conf >= 0.7 ? "" : conf >= 0.4 ? "mid" : "low";

  const created = i.created_at ? new Date(i.created_at).toLocaleString() : "";
  const elapsed = i.elapsed_s ? `${Number(i.elapsed_s).toFixed(1)}s` : "";
  const ratedTag = i.has_feedback ? `<span class="ai-tag-rated">human-rated</span>` : "";

  const actions = (i.recommended_actions || []).map((a) => `<li>${escapeHtml(a)}</li>`).join("");
  const blast = (i.blast_radius || [])
    .slice(0, 12)
    .map((d) => `<span class="ai-blast-chip">${escapeHtml(d)}</span>`)
    .join("");

  const rootCause = i.root_cause_device_id && i.root_cause_device_id !== "unknown"
    ? `<span class="ai-insight-rootcause">
         <span class="ai-insight-device">${escapeHtml(i.root_cause_device_id)}</span>
         ${i.root_cause_device_type && i.root_cause_device_type !== "unknown" ? `<span class="muted">(${escapeHtml(i.root_cause_device_type)})</span>` : ""}
       </span>`
    : `<span class="muted">Unknown — model could not determine a root cause.</span>`;

  const errorBlock = i.error ? `<div class="ai-error">⚠ ${escapeHtml(i.error)}</div>` : "";
  const rationale = i.rationale ? `<div class="ai-insight-rationale">${escapeHtml(i.rationale)}</div>` : "";
  const blastBlock = blast ? `<div class="ai-insight-blast">${blast}</div>` : "";

  return `
    <article class="ai-insight ${sevClass} ${okClass} ${isFresh ? "fresh" : ""}">
      <button
        class="ai-insight-dismiss"
        data-dismiss="${escapeAttr(String(i.insight_id || ""))}"
        type="button"
        title="Dismiss this insight"
        aria-label="Dismiss insight"
      >×</button>
      <div class="ai-insight-header">
        <div class="ai-insight-title">
          <span class="ai-insight-device">${escapeHtml(i.device_id || "unknown")}</span>
          <span class="status-pill status-${escapeAttr(sev)}">${escapeHtml(sev)}</span>
          ${ratedTag}
        </div>
        <div class="ai-insight-meta">
          <span title="created">${escapeHtml(created)}</span>
          ${elapsed ? `<span>· ${escapeHtml(elapsed)}</span>` : ""}
          <span class="ai-confidence ${confTier}">
            confidence
            <span class="ai-confidence-bar"><span style="width:${confPct}%"></span></span>
            <span>${confPct}%</span>
          </span>
          <button class="clear-btn" data-reanalyze="${escapeAttr(i.device_id || "")}" type="button">↻ Re-analyze</button>
        </div>
      </div>
      <div class="ai-insight-summary">${escapeHtml(i.summary || "(no summary returned)")}</div>
      <div class="ai-insight-grid">
        <div class="ai-insight-block">
          <h4>Root cause</h4>
          ${rootCause}
          ${rationale}
        </div>
        <div class="ai-insight-block">
          <h4>Recommended actions</h4>
          ${actions ? `<ol class="ai-actions">${actions}</ol>` : `<div class="muted">No actions provided.</div>`}
          ${blastBlock ? `<h4 style="margin-top:10px">Blast radius</h4>${blastBlock}` : ""}
          ${errorBlock}
          <h4 style="margin-top:12px">Human feedback</h4>
          ${confidenceEditorHTML(i)}
        </div>
      </div>
    </article>
  `;
}

function handleAIInsightMessage(insight) {
  if (!insight || !insight.device_id) return;
  if (insight.insight_id) {
    aiState.freshIds.add(insight.insight_id);
  }
  aiState.insights = [
    insight,
    ...aiState.insights.filter((x) => x.device_id !== insight.device_id),
  ].slice(0, 20);
  renderAIInsights();
}

document.getElementById("ai-refresh-btn")?.addEventListener("click", () => {
  loadAIInsights();
});

const _origLoadInitialData = loadInitialData;
loadInitialData = async function () {
  await _origLoadInitialData();
  renderDeviceTypeChips();
  wireKpiCards();
  await loadAIInsights();
  if (activeFilter) renderDrilldownTable();
};

// ═══════════════════════════════════════════════════════════
// UTIL
// ═══════════════════════════════════════════════════════════

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeAttr(s) {
  return escapeHtml(s);
}

// ═══════════════════════════════════════════════════════════
// STARTUP
// ═══════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
  connectWebSocket();
});
