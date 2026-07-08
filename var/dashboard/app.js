// ═══════════════════════════════════════════════════════════
// CONFIG
// ═══════════════════════════════════════════════════════════
const API_BASE = "http://localhost:8000";
const WS_URL   = "ws://localhost:8000/ws/live";

const GROUP_COLORS = {
  cisco_switch:   { background: "#1e3a8a", border: "#3b82f6" },
  cisco_ap:       { background: "#0e7490", border: "#22d3ee" },
  cisco_wlc:      { background: "#5b21b6", border: "#8b5cf6" },
  camera:         { background: "#9a3412", border: "#f97316" },
  fixed_camera:   { background: "#9a3412", border: "#f97316" },
  ptz_camera:     { background: "#9a3412", border: "#fb923c" },
  facial_recognition_camera: { background: "#7c2d12", border: "#fb923c" },
  nvr:            { background: "#854d0e", border: "#facc15" },
  vms:            { background: "#7c2d12", border: "#fb923c" },
  biostar_door:   { background: "#134e4a", border: "#14b8a6" },
  biostar_server: { background: "#115e59", border: "#2dd4bf" },
  honeywell_panel:{ background: "#7f1d1d", border: "#ef4444" },
  perimeter_fence:{ background: "#1f2937", border: "#6b7280" },
  gate_booth:     { background: "#374151", border: "#9ca3af" },
  server:         { background: "#1f2937", border: "#9ca3af" },
  unknown:        { background: "#1f2937", border: "#6b7280" },
};

// ═══════════════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════════════
let allDevices = [];
let allEdges = [];
let network = null;
let activeFilter = "all";        // current drill filter: all | online | offline | degraded | critical | pending | type:X
let selectedNodeId = null;
let ws = null;

// ═══════════════════════════════════════════════════════════
// DATA LOADING
// ═══════════════════════════════════════════════════════════

async function loadInitial() {
  try {
    const [devResp, topResp] = await Promise.all([
      fetch(`${API_BASE}/api/devices?limit=1000`),
      fetch(`${API_BASE}/api/topology`),
    ]);
    if (!devResp.ok) throw new Error(`Devices HTTP ${devResp.status}`);
    if (!topResp.ok) throw new Error(`Topology HTTP ${topResp.status}`);

    allDevices = await devResp.json();
    allEdges = await topResp.json();

    updateKPIs();
    buildTypeChips();
    renderGraph();
    applyFilter(activeFilter);  // initial render of the list
    connectWebSocket();
  } catch (e) {
    console.error("Load failed:", e);
    showListError(e.message);
  }
}

function updateKPIs() {
  document.getElementById("stat-devices").textContent = allDevices.length;

  const counts = { online: 0, degraded: 0, offline: 0, unknown: 0 };
  allDevices.forEach(d => {
    const s = (d.status || "unknown").toLowerCase();
    if (counts[s] !== undefined) counts[s]++;
  });
  document.getElementById("stat-online").textContent    = counts.online;
  document.getElementById("stat-degraded").textContent = counts.degraded;
  document.getElementById("stat-offline").textContent  = counts.offline;

  const pending = allEdges.filter(e => (e.confidence || 0) < 0.6).length;
  document.getElementById("stat-pending").textContent = pending;
}

function buildTypeChips() {
  const container = document.getElementById("type-chips");
  container.innerHTML = "";

  const groups = [...new Set(allDevices.map(d => d.device_type))].sort();
  groups.forEach(group => {
    const count = allDevices.filter(d => d.device_type === group).length;
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.dataset.type = group;
    chip.textContent = `${group} (${count})`;
    chip.addEventListener("click", () => {
      const target = `type:${group}`;
      applyFilter(activeFilter === target ? "all" : target);
    });
    container.appendChild(chip);
  });
}

// ═══════════════════════════════════════════════════════════
// VIS.JS GRAPH
// ═══════════════════════════════════════════════════════════

function visStatusColor(status) {
  const s = (status || "unknown").toLowerCase();
  return s === "online" ? "#29b574"
       : s === "degraded" ? "#f5a623"
       : s === "offline" ? "#d9534f"
       : "#8aa1c1";
}

function edgeColor(conf) {
  const c = conf || 0;
  if (c < 0.6) return "#d9534f";
  if (c < 0.9) return "#f5a623";
  return "#2c7be5";
}

function renderGraph() {
  const visNodes = allDevices.map(d => {
    const g = GROUP_COLORS[d.device_type] || GROUP_COLORS.unknown;
    const status = (d.status || "unknown").toLowerCase();
    return {
      id: d.device_id,
      label: d.device_id,
      title: `<b>${d.device_id}</b><br>Type: ${d.device_type}<br>Status: ${status}<br>Vendor: ${d.vendor || "n/a"}<br>Model: ${d.model || "n/a"}<br>Site: ${d.site_id || "n/a"}`,
      color: {
        background: g.background,
        border: visStatusColor(status),
        highlight: { background: g.background, border: "#fff" },
      },
      font: { color: "#e6edf7", size: 12, face: "system-ui" },
      shape: (d.device_type === "cisco_switch" || d.device_type === "cisco_wlc") ? "box"
            : (d.device_type || "").includes("camera") ? "ellipse"
            : (d.device_type || "").includes("biostar") ? "diamond"
            : "dot",
      size: d.device_type === "cisco_switch" ? 22 : 14,
      borderWidth: 2,
    };
  });

  const visEdges = allEdges.map(e => ({
    from: e.source_id,
    to: e.target_id,
    label: e.source_port ? `${e.relationship_type}` : e.relationship_type,
    color: { color: edgeColor(e.confidence), highlight: "#fff" },
    width: (e.confidence || 0) < 0.6 ? 2 : 1,
    smooth: { type: "continuous" },
    title: `${e.source_id} → ${e.target_id}<br>Type: ${e.relationship_type}<br>Confidence: ${(e.confidence || 0).toFixed(2)}<br>Source: ${e.source || "n/a"}`,
  }));

  const data = {
    nodes: new vis.DataSet(visNodes),
    edges: new vis.DataSet(visEdges),
  };
  const options = {
    physics: { stabilization: { iterations: 200 }, barnesHut: { gravitationalConstant: -8000 } },
    interaction: { hover: true, tooltipDelay: 120 },
    edges: { font: { color: "#8aa1c1", size: 10, strokeWidth: 0, align: "middle" } },
  };

  if (network) network.destroy();
  network = new vis.Network(document.getElementById("graph"), data, options);

  network.on("selectNode", p => {
    if (p.nodes && p.nodes[0]) selectDevice(p.nodes[0]);
  });
  network.on("deselectNode", () => selectDevice(null));
}

// ═══════════════════════════════════════════════════════════
// FILTER + LIST (single function, no drill-down pages)
// ═══════════════════════════════════════════════════════════

async function applyFilter(filter) {
  activeFilter = filter;

  // Visual sync: highlight the active button/chip
  document.querySelectorAll(".kpi-card").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.filter === filter);
  });
  document.querySelectorAll(".chip").forEach(chip => {
    const t = `type:${chip.dataset.type}`;
    chip.classList.toggle("active", t === filter);
  });

  const heading = document.getElementById("drilldown-heading");
  const ul = document.getElementById("device-list");
  ul.innerHTML = `<li style="color:#8aa1c1;cursor:default">Loading...</li>`;

  // Critical events uses a different data source
  if (filter === "critical") {
    heading.textContent = "Critical Events (last 24h)";
    await loadCriticalEventsInto(ul);
    document.getElementById("detail").style.display = "none";
    document.getElementById("search").value = "";
    return;
  }

  // All other filters: filter the local devices list
  let matched = [...allDevices];
  if (filter === "online") {
    matched = matched.filter(d => (d.status || "").toLowerCase() === "online");
    heading.textContent = `Devices (Online) — ${matched.length}`;
  } else if (filter === "offline") {
    matched = matched.filter(d => (d.status || "").toLowerCase() === "offline");
    heading.textContent = `Devices (Offline) — ${matched.length}`;
  } else if (filter === "degraded") {
    matched = matched.filter(d => (d.status || "").toLowerCase() === "degraded");
    heading.textContent = `Devices (Degraded) — ${matched.length}`;
  } else if (filter === "pending") {
    const ids = new Set();
    allEdges.forEach(e => {
      if ((e.confidence || 0) < 0.6) {
        ids.add(e.source_id);
        ids.add(e.target_id);
      }
    });
    matched = matched.filter(d => ids.has(d.device_id));
    heading.textContent = `Devices with Pending-Review Edges — ${matched.length}`;
  } else if (filter.startsWith("type:")) {
    const t = filter.split(":")[1];
    matched = matched.filter(d => d.device_type === t);
    heading.textContent = `Devices (${t}) — ${matched.length}`;
  } else {
    heading.textContent = `Devices (All) — ${matched.length}`;
  }

  renderList(matched);
  document.getElementById("search").value = "";
  document.getElementById("detail").style.display = "none";
  selectedNodeId = null;
}

function renderList(devices) {
  const ul = document.getElementById("device-list");
  ul.innerHTML = "";

  if (devices.length === 0) {
    ul.innerHTML = `<li style="color:#8aa1c1;cursor:default">No devices match this filter.</li>`;
    return;
  }

  devices
    .slice()
    .sort((a, b) => (a.device_id || "").localeCompare(b.device_id || ""))
    .forEach(d => {
      const li = document.createElement("li");
      const status = (d.status || "unknown").toLowerCase();
      li.innerHTML = `<span class="dot ${status}"></span>${d.device_id}`;
      li.dataset.id = d.device_id;
      if (selectedNodeId === d.device_id) li.classList.add("selected");
      li.onclick = () => {
        if (network) network.selectNodes([d.device_id]);
        selectDevice(d.device_id);
      };
      ul.appendChild(li);
    });
}

function showListError(msg) {
  const ul = document.getElementById("device-list");
  ul.innerHTML = `<li style="color:#d9534f;cursor:default">⚠ ${msg}<br><small>Is FastAPI running on ${API_BASE}?</small></li>`;
}

// ═══════════════════════════════════════════════════════════
// DEVICE DETAIL (in-place, no modal/page)
// ═══════════════════════════════════════════════════════════

function selectDevice(id) {
  selectedNodeId = id;
  document.querySelectorAll("#device-list li").forEach(li => {
    li.classList.toggle("selected", li.dataset.id === id);
  });

  const detail = document.getElementById("detail");
  if (!id) { detail.style.display = "none"; return; }

  const node = allDevices.find(d => d.device_id === id);
  if (!node) { detail.style.display = "none"; return; }

  const inE  = allEdges.filter(e => e.target_id === id);
  const outE = allEdges.filter(e => e.source_id === id);

  const upstream = inE.map(e =>
    `<li>${e.source_id} <span style="color:#8aa1c1">— ${e.relationship_type}${e.source_port ? " (" + e.source_port + ")" : ""}</span></li>`
  ).join("");
  const downstream = outE.map(e =>
    `<li>${e.target_id} <span style="color:#8aa1c1">— ${e.relationship_type}${e.source_port ? " (" + e.source_port + ")" : ""}</span></li>`
  ).join("");

  detail.style.display = "block";
  document.getElementById("detail-title").textContent = node.device_id;
  document.getElementById("detail-body").innerHTML = `
    <div class="row"><span>Status</span><b>${node.status || "unknown"}</b></div>
    <div class="row"><span>Type</span><b>${node.device_type || "n/a"}</b></div>
    <div class="row"><span>Vendor</span><b>${node.vendor || "n/a"}</b></div>
    <div class="row"><span>Model</span><b>${node.model || "n/a"}</b></div>
    <div class="row"><span>Site</span><b>${node.site_id || "n/a"}</b></div>
    <div class="row"><span>Last seen</span><b style="font-weight:400;font-size:11px">${node.last_seen || "n/a"}</b></div>
    <div class="neighbors">
      <b style="color:#8aa1c1">Upstream (${inE.length})</b>
      <ul>${upstream || "<li style='color:#8aa1c1'>none</li>"}</ul>
    </div>
    <div class="neighbors">
      <b style="color:#8aa1c1">Downstream (${outE.length})</b>
      <ul>${downstream || "<li style='color:#8aa1c1'>none</li>"}</ul>
    </div>
  `;
}

// ═══════════════════════════════════════════════════════════
// CRITICAL EVENTS (inline list, same panel)
// ═══════════════════════════════════════════════════════════

async function loadCriticalEventsInto(ul) {
  try {
    const r = await fetch(`${API_BASE}/api/events?severity=critical&hours_back=24&limit=50`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const events = await r.json();
    ul.innerHTML = "";

    if (events.length === 0) {
      ul.innerHTML = `<li style="color:#8aa1c1;cursor:default">No critical events in last 24h 🎉</li>`;
      return;
    }
    events.forEach(ev => {
      const li = document.createElement("li");
      const ts = ev.event_time ? new Date(ev.event_time).toLocaleString() : "";
      li.innerHTML = `<span class="dot offline"></span>
        <div style="flex:1;min-width:0">
          <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${ev.device_id}</div>
          <div style="font-size:10px;color:#8aa1c1">${ts} · ${ev.message || ev.status_code || ""}</div>
        </div>`;
      li.onclick = () => {
        if (network) network.selectNodes([ev.device_id]);
        selectDevice(ev.device_id);
      };
      ul.appendChild(li);
    });
  } catch (e) {
    ul.innerHTML = `<li style="color:#d9534f;cursor:default">⚠ ${e.message}</li>`;
  }
}

// ═══════════════════════════════════════════════════════════
// WEBSOCKET (live KPI updates)
// ═══════════════════════════════════════════════════════════

function connectWebSocket() {
  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    updateWSStatus(false);
    return;
  }
  ws.onopen = () => updateWSStatus(true);
  ws.onclose = () => {
    updateWSStatus(false);
    setTimeout(connectWebSocket, 5000);
  };
  ws.onerror = () => updateWSStatus(false);
  ws.onmessage = ev => {
    try {
      const msg = JSON.parse(ev.data);
      handleWSMessage(msg);
    } catch (e) { /* ignore */ }
  };
}

function updateWSStatus(connected) {
  const el = document.getElementById("ws-status");
  if (connected) { el.textContent = "● Live"; el.className = "ws-status connected"; }
  else          { el.textContent = "● Polling"; el.className = "ws-status disconnected"; }
}

function handleWSMessage(msg) {
  document.getElementById("last-update").textContent =
    "Updated: " + new Date().toLocaleTimeString();

  if (msg.type === "kpi_update" && msg.data) {
    const d = msg.data;
    document.getElementById("stat-devices").textContent = d.total_devices ?? "—";
    document.getElementById("stat-online").textContent   = d.online ?? "—";
    document.getElementById("stat-degraded").textContent= d.degraded ?? "—";
    document.getElementById("stat-offline").textContent = d.offline ?? "—";
    document.getElementById("stat-critical").textContent= d.critical_events_24h ?? "—";
  }
  if (msg.type === "event" && msg.data) {
    const ev = msg.data;
    if (ev.severity === "critical" || ev.severity === "error") {
      const node = allDevices.find(d => d.device_id === ev.device_id);
      if (node) {
        node.status = ev.status || "offline";
        updateKPIs();
        renderGraph();
        if (selectedNodeId === ev.device_id) selectDevice(ev.device_id);
        // If the current view is filtered to a status, re-apply
        if (["online", "offline", "degraded"].includes(activeFilter)) {
          applyFilter(activeFilter);
        }
      }
    }
  }
}

// ═══════════════════════════════════════════════════════════
// EVENT BINDINGS
// ═══════════════════════════════════════════════════════════

document.querySelectorAll(".kpi-card").forEach(btn => {
  btn.addEventListener("click", () => applyFilter(btn.dataset.filter));
});

document.getElementById("search").addEventListener("input", e => {
  const q = e.target.value.toLowerCase();
  document.querySelectorAll("#device-list li").forEach(li => {
    li.style.display = li.textContent.toLowerCase().includes(q) ? "" : "none";
  });
});

// ═══════════════════════════════════════════════════════════
// BOOT
// ═══════════════════════════════════════════════════════════

loadInitial();
