# ErrorDet — IoT Topology Discovery & Live Dashboard

A production-grade pipeline for discovering, validating, and maintaining a
unified device-topology graph across an enterprise IoT/OT estate, plus a
real-time FastAPI dashboard for monitoring the result.

The repo has two distinct halves that share a schema:

| Half | What it does | Primary store |
|------|--------------|---------------|
| **Pipeline** (under `topology/`) | Discovers devices, infers edges, merges them into a graph, refreshes on a schedule | **PostgreSQL** + **Neo4j** |
| **Live Dashboard** (`topology/backend/`) | Serves the FastAPI app + WebSocket + static UI that operators watch | **SQL Server** (`thtrdinfradb1` / `InfrastructureMonitorDB`) |

> **Note:** the dashboard hits SQL Server — not the Postgres instance used by
> the pipeline. They share the same conceptual schema (`iot.devices`,
> `iot.device_logs`, `iot.v_active_topology`, `iot.device_relationships`).
> See [Architecture](#architecture) for details.

---

## Table of contents

- [Quickstart — run the dashboard](#quickstart--run-the-dashboard)
- [What the dashboard does](#what-the-dashboard-does)
- [Dashboard features](#dashboard-features)
- [REST + WebSocket API](#rest--websocket-api)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [Pipeline (discovery + ingest)](#pipeline-discovery--ingest)
- [Layout](#layout)
- [Troubleshooting](#troubleshooting)

---

## Quickstart — run the dashboard

The dashboard is a single FastAPI process that serves the REST API,
WebSocket, and the static UI.

### 1. Install

```bash
cd topology
pip install -r requirements.txt
```

Or, if you only want the dashboard (no pipeline deps):

```bash
pip install fastapi uvicorn jinja2 python-multipart pydantic pyyaml python-dotenv
```

### 2. Configure

The dashboard's SQL Server connection is hard-coded with sensible defaults
in [backend/database.py](backend/database.py). To override, set environment
variables before starting uvicorn (or extend `database.py` to read them):

| Variable | Default | Purpose |
|----------|---------|---------|
| `DB_SERVER` | `thtrdinfradb1` | SQL Server host |
| `DB_NAME` | `InfrastructureMonitorDB` | Database name |
| `ODBC_DRIVER` | `ODBC Driver 17 for SQL Server` | ODBC driver name |
| `DB_TRUSTED` | `yes` | Use Windows auth when `yes` |

Make sure the `iot` schema exists and contains:

- `iot.devices` — `device_id`, `device_name`, `device_type`, `vendor`, `model`, `site_id`, `status`, `ip_address`, `mac_address`, `last_seen`
- `iot.device_logs` — `log_id`, `event_time`, `device_id`, `severity`, `status_code`, `status`, `message`, `source_system`
- `iot.v_active_topology` — view of `iot.device_relationships` where `valid_to IS NULL`
- `iot.device_relationships` — `source_id`, `target_id`, `relationship_type`, `source_port`, `target_port`, `confidence`, `source`, `valid_from`, `valid_to`

### 3. Start the server

```bash
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

### 4. Open the UI

| URL | Purpose |
|-----|---------|
| <http://127.0.0.1:8000/> | Dashboard UI |
| <http://127.0.0.1:8000/docs> | OpenAPI / Swagger |
| <http://127.0.0.1:8000/health> | Liveness check |
| `ws://127.0.0.1:8000/ws/live` | WebSocket for live events / KPIs |

On startup the app:

1. Logs to stdout at `INFO` level.
2. Initializes `_last_event_id = MAX(log_id)` from `iot.device_logs` so it
   only broadcasts new events.
3. Starts two background pollers (see [Polling](#polling)).
4. Mounts static assets at `/static`.

---

## What the dashboard does

A single-page app (`backend/static/`) with no build step and no front-end
framework. It is pure HTML + vanilla JS + CSS, served by FastAPI.

```
┌──────────────────────────────────────────────────────┐
│ Header:  ● Connected  |  Last update: 10:42:18       │
├──────────────────────────────────────────────────────┤
│ KPI strip: Devices / Online / Offline / Degraded /   │
│            Edges / Critical (24h)                    │
├──────────────────────────────────────────────────────┤
│ 🚨 Live Events   (auto-updating from WebSocket)      │
├──────────────────────────────────────────────────────┤
│ 📦 Devices by Type  (chips → click → table)          │
├──────────────────────────────────────────────────────┤
│ 🕸️ Topology  (force-style SVG layout)               │
└──────────────────────────────────────────────────────┘
```

State is held in a single in-memory `state` object (`backend/static/app.js`)
and updated by:

- `loadInitialData()` — fired on first WebSocket "connected" message
- `handleMessage(msg)` — fired for every WebSocket frame

---

## Dashboard features

### KPI cards

Pulled from `GET /api/kpis` and updated every 10s via the `kpi_update`
WebSocket message. Counts come from `iot.devices` and `iot.device_logs`.

### Live event feed

Append-only list of the most recent events. Severity drives the row color:

| Severity | Style |
|----------|-------|
| `critical` / `error` | Red border, may trigger a browser `Notification` |
| `warning` | Amber border |
| `info` | Green border |

Capped at 100 events in memory (newest first).

### Devices by Type *(new)*

A row of clickable chips, one per distinct `device_type`. The active chip is
highlighted; clicking a chip fetches `GET /api/devices?device_type=<type>`
and renders every device of that type in a table with all of its
attributes.

- Chips are sorted by device count (most numerous first), then alpha.
- Table columns: `device_id, device_name, device_type, vendor, model, status, ip_address, mac_address, site_id, last_seen`, then any extra columns from the DB.
- `status` is shown as a colored pill (green / red / amber / gray).
- `last_seen` is rendered as a localized date/time.
- Empty values render as `—`.
- The table has a sticky header and a 500px-tall scrollable body.

Click the same chip twice to deselect and clear the table.

### Topology SVG

A simple group-by-type layout that draws circles for each device and lines
for each active edge. Hover any node for a tooltip. Status drives the
node color (`online` / `offline` / `degraded` / `unknown`).

---

## REST + WebSocket API

All endpoints are under `/api/…` and return JSON.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/kpis` | System-wide counts |
| GET | `/api/devices` | List devices; filters: `site`, `device_type`, `status`, `limit` (≤ 10 000) |
| GET | `/api/devices/{id}` | One device |
| GET | `/api/devices/{id}/events` | Recent events for a device |
| GET | `/api/events` | Recent events; filters: `severity`, `device_id`, `hours_back`, `limit` |
| GET | `/api/events/cascades` | Pairs of devices that failed within `window_minutes` |
| GET | `/api/topology` | List of active edges |
| GET | `/api/topology/graph` | `{ nodes: [...], edges: [...] }` for the SVG view |
| GET | `/api/topology/dependencies/{id}` | Upstream + downstream neighbors, recursive CTE up to `depth` |
| GET | `/health` | `{ "status": "healthy", "websocket_clients": N }` |
| WS | `/ws/live` | Live updates (see below) |

### WebSocket protocol

Client → server:

| Payload | Effect |
|---------|--------|
| `"ping"` | Server replies `{"type":"pong"}` |

Server → client (JSON):

| `type` | `data` shape | Source |
|--------|--------------|--------|
| `connected` | `{ "message": "..." }` | On connect |
| `event` | one `iot.device_logs` row joined to `iot.devices.device_type` | Background poller, every 2s |
| `kpi_update` | full `KPIs` object | Background poller, every 10s |
| `device_update` | *(reserved)* | Reserved for future device-mutation push |

The client also sends an automatic `ping` every 30s to keep the connection
alive.

### Polling

Two `asyncio` tasks started in [backend/websocket/poller.py](backend/websocket/poller.py):

| Task | Cadence | Query |
|------|---------|-------|
| `poll_for_new_events` | 2 s | New `iot.device_logs` rows with `log_id > _last_event_id` |
| `poll_for_kpis` | 10 s | The same aggregate as `GET /api/kpis` |

`_last_event_id` is initialized on startup to `ISNULL(MAX(log_id), 0)` so a
restart does not re-broadcast the entire history.

---

## Architecture

```
                          ┌─────────────────────────┐
                          │  Browser (static UI)    │
                          │  HTML + JS + CSS        │
                          └──────┬──────────┬───────┘
                                 │ REST     │ WebSocket /ws/live
                                 ▼          ▼
                          ┌─────────────────────────┐
                          │  FastAPI (backend/)     │
                          │  ┌───────────────────┐  │
                          │  │ REST routers      │  │
                          │  │  /api/kpis        │  │
                          │  │  /api/devices     │  │
                          │  │  /api/events      │  │
                          │  │  /api/topology    │  │
                          │  └─────────┬─────────┘  │
                          │  ┌─────────▼─────────┐  │
                          │  │ WebSocket manager │  │
                          │  │  + pollers (2s,   │  │
                          │  │    10s)           │  │
                          │  └─────────┬─────────┘  │
                          └────────────┼────────────┘
                                       │ pyodbc (Trusted Connection)
                                       ▼
                          ┌─────────────────────────┐
                          │  SQL Server             │
                          │  thtrdinfradb1          │
                          │  InfrastructureMonitorDB│
                          │  schema: iot            │
                          └─────────────────────────┘
```

The **pipeline** (separate concern, runs out-of-band) writes to Postgres
(`iot.*`) and Neo4j, then either:

- exports Neo4j → `iot.device_relationships` rows that the dashboard reads, or
- is replicated into the same SQL Server schema by your own ETL.

Either way, the dashboard only ever talks to SQL Server.

---

## Configuration

`config/settings.yaml` and `config/settings.env` are used by the **pipeline
modules** (`inventory/load.py`, `graph/loader.py`, `graph/confidence.py`,
`refresh/diff.py`, `validation/api.py`, `discovery/tier2/*`). The dashboard
does not read them.

If you want the dashboard to honor the same `POSTGRES_*` env vars for a
Postgres-backed deployment, see [Switching the dashboard to Postgres](#switching-the-dashboard-to-postgres).

---

## Pipeline (discovery + ingest)

These scripts populate the upstream stores. They are **not** required to run
the dashboard — the dashboard only needs SQL Server to be populated.

```bash
# Inventory normalization
python -m topology.inventory.load --source netbox

# Tier 1 active probing (SNMP/LLDP/CDP, ONVIF, BioStar, Honeywell)
python -m topology.discovery.tier1.run --targets config/cisco_switches.yaml
python -m topology.discovery.tier1.onvif --subnet 10.10.5.0/24

# Tier 2 log inference (Spark co-occurrence, PrefixSpan)
python -m topology.discovery.tier2.sequence
python -m topology.discovery.tier2.cooccurrence
python -m topology.discovery.tier2.ip_extraction

# Merge into the graph + apply confidence scoring
python -m topology.graph.merge --since 24h

# Continuous refresh + drift detection
python -m topology.refresh.diff --alert
```

Or the all-in-one Outlook log pipeline at the repo root:

```bash
python run_pipeline.py
# flags: --skip-export | --skip-parse | --skip-cascade
```

---

## Layout

```
topology/
├── backend/                # FastAPI dashboard
│   ├── main.py             # App + lifespan + static mount + WS endpoint
│   ├── database.py         # pyodbc connection pool
│   ├── models.py           # Pydantic models
│   ├── api/                # REST routers (devices, events, kpis, topology)
│   ├── websocket/          # WS manager + background pollers
│   └── static/             # index.html, app.js, style.css  (no build step)
├── config/                 # Pipeline settings (YAML + .env) + Postgres schema.sql
├── inventory/              # Stage 1: normalize vendor inventories → Postgres
├── discovery/
│   ├── tier1/              # SNMP/LLDP/CDP, ONVIF, BioStar, Honeywell
│   └── tier2/              # Spark co-occurrence, PrefixSpan, IP extraction
├── graph/                  # Neo4j schema, loader, merge, confidence
├── validation/             # HITL review endpoints
├── refresh/                # diff + drift jobs
├── queries/                # RCA traversal Cypher library
├── visualization/          # Neo4j Browser / Grafana JSON
└── tests/
```

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|---------------------|
| `error while attempting to bind on address ('127.0.0.1', 8000)` | Another uvicorn (or other process) is already on 8000. Either `Stop-Process -Id <pid>` (find it with `Get-NetTCPConnection -LocalPort 8000 -State Listen`) or run on a different port: `python -m uvicorn backend.main:app --port 8001`. |
| `500 Internal Server Error` on `/api/devices` with `PydanticSerializationError: 'float' object cannot be interpreted as an integer` | `pandas.read_sql` returns `numpy.float64`; Pydantic v2 strict mode rejects it. Apply the same `_sanitize_records` helper from [backend/api/devices.py](backend/api/devices.py) to any other endpoint that returns pandas rows. |
| WebSocket shows "● Disconnected" and never reconnects | Backend is not running, or a proxy is stripping the upgrade. Check `/health` first. |
| KPIs all show `—` | SQL Server is reachable but the `iot` schema/tables don't exist or are empty. |
| `pandas only supports SQLAlchemy connectable…` warning | The `pd.read_sql(..., conn, params=...)` call in [backend/api/devices.py](backend/api/devices.py) uses a `pyodbc` connection directly. The endpoint still works; the warning is benign. |
| `'NoneType' object has no attribute 'cursor'` on startup | The `pyodbc` driver can't be loaded, or the SQL Server is unreachable. Confirm the driver is installed: `python -c "import pyodbc; print(pyodbc.drivers())"`. |

### Switching the dashboard to Postgres

If you want the live dashboard to read from the same Postgres instance the
pipeline writes to (instead of SQL Server):

1. Add a `psycopg2`/`sqlalchemy` connection helper alongside the existing
   `pyodbc` pool in [backend/database.py](backend/database.py).
2. In each of the five files that import `pool`
   (`backend/api/{devices,events,kpis,topology}.py`,
   `backend/websocket/poller.py`), switch to the new pool. The SQL strings
   will need light edits because they use T-SQL syntax
   (`TOP (n)`, `?` placeholders, `DATEADD`, `DATEDIFF`).
3. Run `psql -f config/schema.sql` to provision the schema.

---

## See also

- [var/dashboard/README.md](../var/dashboard/README.md) — the standalone
  vis-network sample dashboard (no FastAPI required).
- [queries/README.md](queries/README.md) — RCA Cypher query library.
