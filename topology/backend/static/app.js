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
// STARTUP
// ═══════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
  connectWebSocket();
});
