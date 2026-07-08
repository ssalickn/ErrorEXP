# Standalone Topology Dashboard (sample)

A self-contained, no-infrastructure-required dashboard for the ErrorDet IoT
topology pipeline. Reads a static `vis-network` JSON file and renders an
interactive graph in the browser.

This is **not** the same as the live FastAPI dashboard under
[`topology/backend/`](../topology/backend/) — this one is a static HTML
file you can open without any backend services. It is useful for sharing
snapshots, demos, and offline review.

## What it shows

- Force-directed graph of all devices and relationships
- Color-coded edges by confidence bucket:
  - **Blue** — active probe (conf ≥ 0.9)
  - **Orange** — inferred (conf 0.6–0.9)
  - **Red** — needs review (conf < 0.6)
- Filter buttons (All / Active / Inferred / Review)
- Live search across device IDs
- Click any node for upstream/downstream neighbors
- Header stats: device count, edge count, pending-review count

## Run

From the repo root:

```bash
python -m http.server 8765 --directory var/dashboard
# then open http://localhost:8765
```

Or in PowerShell:

```powershell
cd var\dashboard
python -m http.server 8765
```

## Files

| File | Purpose |
|------|---------|
| `index.html` | Page chrome + vis-network setup |
| `sample_topology.json` | Demo dataset (ships in the repo) |

## Replacing the sample with real data

The dashboard reads `sample_topology.json` next to the HTML file. Once you
have Neo4j + Postgres running, generate a real export with:

```bash
python -m topology.visualization.export --device SW-CORE-07 --depth 2 --out-dir var/dashboard/
# rename vis_<device>.json to sample_topology.json
```

Or, for live queries against the pipeline, use the FastAPI validation API:

```bash
uvicorn topology.validation.api:app --port 8080
```

> **Tip:** if you want a continuously-updating view backed by real data
> (KPIs, live event feed, devices-by-type table), use the live dashboard at
> [`topology/backend/`](../topology/backend/) instead. See the
> [topology/README.md](../topology/README.md) for how to run it.
