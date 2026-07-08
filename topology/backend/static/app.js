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
  } else if (msg.type === "connected") {
    console.log(msg.data.message);
    loadInitialData();
  }
}

// Keep-alive ping
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
    // Load KPIs
    const kpis = await fetch("/api/kpis").then(r => r.json());
    updateKPIs(kpis);
    
    // Load devices
    const devices = await fetch("/api/devices?limit=1000").then(r => r.json());
    devices.forEach(d => state.devices.set(d.device_id, d));
    renderDeviceList();
    
    // Load topology
    const graphData = await fetch("/api/topology/graph").then(r => r.json());
    state.edges = graphData.edges;
    state.devices.clear();
    graphData.nodes.forEach(n => state.devices.set(n.device_id, n));
    renderTopology();
    
    // Load recent events
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
  if (state.connected) {
    el.textContent = "● Connected";
    el.className = "connected";
  } else {
    el.textContent = "● Disconnected";
    el.className = "disconnected";
  }
}

function updateKPIs(kpis) {
  state.kpis = kpis;
  document.getElementById("kpi-total").querySelector(".kpi-value").textContent = kpis.total_devices;
  document.getElementById("kpi-online").querySelector(".kpi-value").textContent = kpis.online;
  document.getElementById("kpi-offline").querySelector(".kpi-value").textContent = kpis.offline;
  document.getElementById("kpi-degraded").querySelector(".kpi-value").textContent = kpis.degraded;
  document.getElementById("kpi-edges").querySelector(".kpi-value").textContent = kpis.total_edges;
  document.getElementById("kpi-critical").querySelector(".kpi-value").textContent = kpis.critical_events_24h;
}

function addEvent(event) {
  state.events.unshift(event);
  if (state.events.length > 100) state.events.pop();
  renderEvents();
  
  // Flash if critical
  if (event.severity === "critical" || event.severity === "error") {
    flashBrowserNotification(event);
  }
}

function renderEvents() {
  const container = document.getElementById("event-list");
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
  
  // Simple force-directed-like layout (positions by type)
  const positions = new Map();
  const typeGroups = {};
  
  state.devices.forEach((d, id) => {
    if (!typeGroups[d.device_type]) typeGroups[d.device_type] = [];
    typeGroups[d.device_type].push(id);
  });
  
  const types = Object.keys(typeGroups);
  const typesPerRow = Math.ceil(Math.sqrt(types.length));
  
  let x = 0, y = 0;
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
  
  // Draw edges
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
      line.setAttribute("stroke", "#ccc");
      line.setAttribute("stroke-width", "1");
      edgesGroup.appendChild(line);
    }
  });
  svg.appendChild(edgesGroup);
  
  // Draw nodes
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
      "online": "green",
      "offline": "red",
      "degraded": "orange",
      "unknown": "gray"
    }[status] || "gray";
    circle.setAttribute("fill", color);
    circle.setAttribute("stroke", "black");
    circle.setAttribute("stroke-width", "1");
    
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("x", pos.x);
    text.setAttribute("y", pos.y + 20);
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("font-size", "10");
    text.textContent = id.length > 15 ? id.substring(0, 12) + "..." : id;
    
    g.appendChild(circle);
    g.appendChild(text);
    
    // Tooltip
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
// Unified table that can be populated by:
//   - KPI card clicks       (e.g. "Online" → status:online devices)
//   - Device-type chip clicks (e.g. "switch" → all switches)
//   - Topology node clicks  (a single device)
// Filters are mutually exclusive: clicking a new control replaces the
// current filter. Clicking the same control again clears the filter.
// ═══════════════════════════════════════════════════════════

// Active filter shape: { kind: 'all' | 'status' | 'type' | 'device' | 'events' | 'edges', value?: string }
let activeFilter = null;

function applyFilter(filter) {
  // Toggle off if same filter is clicked again
  if (activeFilter && filter && activeFilter.kind === filter.kind && activeFilter.value === filter.value) {
    activeFilter = null;
  } else {
    activeFilter = filter;
  }

  // Update KPI card "selected" styling
  document.querySelectorAll(".kpi-card").forEach((el) => el.classList.remove("kpi-active"));

  if (!activeFilter) {
    clearDrilldown();
    renderDeviceTypeChips(); // refresh active styling on chips
    return;
  }

  if (activeFilter.kind === "edges") {
    // Edges aren't a device table — scroll to the topology section instead
    document.getElementById("topology")?.scrollIntoView({ behavior: "smooth", block: "start" });
    clearDrilldown();
    renderDeviceTypeChips();
    return;
  }

  // Highlight the matching KPI card
  if (activeFilter.kind === "all" || activeFilter.kind === "status" || activeFilter.kind === "events") {
    const kpi = document.querySelector(`.kpi-card[data-filter="${cssEscapeAttr(activeFilter.kind + (activeFilter.value ? ":" + activeFilter.value : ""))}"]`);
    if (kpi) kpi.classList.add("kpi-active");
  }

  renderDeviceTypeChips(); // refresh chip active styling
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

  // Title for the panel
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
  } catch (e) {
    console.error("Failed to load drill-down:", e);
    container.innerHTML = `<p class="error">Failed to load: ${escapeHtml(String(e))}</p>`;
  }
}

async function renderDevicesTable(container, title) {
  // Build query string from active filter
  const params = new URLSearchParams();
  params.set("limit", "10000");
  if (activeFilter.kind === "status") params.set("status", activeFilter.value);
  else if (activeFilter.kind === "type") params.set("device_type", activeFilter.value);
  // "all" and "device" → no filter (we'll filter client-side for the single device)

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
  // For the [data-filter="..."] attribute selector; safe for the values we use
  return s.replace(/"/g, '\\"');
}

// Wire up KPI card clicks
function wireKpiCards() {
  document.querySelectorAll(".kpi-card").forEach((card) => {
    card.addEventListener("click", () => {
      const f = card.getAttribute("data-filter");
      if (!f) return;
      let filter;
      if (f === "all") filter = { kind: "all" };
      else if (f === "edges") filter = { kind: "edges" };
      else if (f.startsWith("status:")) filter = { kind: "status", value: f.split(":")[1] };
      else if (f.startsWith("events:")) filter = { kind: "events", value: f.split(":")[1], hoursBack: 24 };
      applyFilter(filter);
    });
  });
}

// Hook into the existing data load: when devices/topology finish, refresh the chips
const _origLoadInitialData = loadInitialData;
loadInitialData = async function () {
  await _origLoadInitialData();
  renderDeviceTypeChips();
  wireKpiCards();
  // If a filter is active, re-render the drill-down with the latest data
  if (activeFilter && activeFilter.kind !== "edges") renderDrilldownTable();
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
