# Topology Dashboard (synthetic sample)

Self-contained, no-infrastructure dashboard for the ErrorDet IoT topology
pipeline. Reads a static vis-network JSON file and renders an interactive
graph in the browser.

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

```bash
# from the repo root
python -m http.server 8765 --directory var/dashboard
# then open http://localhost:8765
```

Or in PowerShell:

```powershell
cd var\dashboard
python -m http.server 8765
```

## Replacing the sample with real data

The dashboard reads `sample_topology.json` next to the HTML file. Once you
have Neo4j + Postgres running, generate a real export with:

```bash
python -m topology.visualization.export --device SW-CORE-07 --depth 2 --out-dir var/dashboard/
# rename vis_<device>.json to sample_topology.json
```

Or call the FastAPI validation API for live queries:

```bash
uvicorn topology.validation.api:app --port 8080
```
