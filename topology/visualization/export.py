"""Export subgraphs (vis.js / Cytoscape / D3 JSON) for visualization.

Two outputs are produced:

  1. `var/vis/<device_id>.json` — vis-network compatible dataset showing the
     focused device, all 1-hop neighbors, and a 1-hop neighborhood.

  2. `var/grafana/site.json` — Grafana table panel JSON summarizing fanout
     per device for the operations dashboard.

These can be served statically or imported by a front-end of your choice.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Iterable

from neo4j import GraphDatabase

from topology.config import load_settings
from topology.queries.rca import (
    upstream_blast, downstream_dependencies, vlan_members, site_criticality,
)

log = logging.getLogger("topology.visualization.export")


def _driver():
    s = load_settings()
    return GraphDatabase.driver(s.neo4j.uri, auth=(s.neo4j.user, s.neo4j.password))


def vis_subgraph(device_id: str, *, depth: int = 2) -> dict:
    """Produce a vis-network dataset centered on `device_id`."""
    s = load_settings()
    driver = _driver()
    out = {"nodes": [], "edges": []}
    with driver.session(database=s.neo4j.database) as sess:
        nodes = sess.run(
            """
            MATCH (c:Device {device_id: $did})
            OPTIONAL MATCH p = (c)-[*1..$depth]-(n:Device)
            WITH collect(DISTINCT c) + collect(DISTINCT n) AS ns
            UNWIND ns AS n
            RETURN DISTINCT n
            """, did=device_id, depth=depth,
        )
        for r in nodes:
            n = r["n"]
            if n is None:
                continue
            out["nodes"].append({
                "id": n["device_id"],
                "label": n["device_id"],
                "group": n.get("device_type", "unknown"),
                "title": (f"{n.get('device_type','?')} • {n.get('vendor','')} {n.get('model','')}"
                          f"<br/>status: {n.get('status','?')}<br/>ip: {n.get('ip','?')}"),
            })
        edges = sess.run(
            """
            MATCH (c:Device {device_id: $did})-[r*1..$depth]-(n:Device)
            UNWIND r AS e
            WITH DISTINCT e
            MATCH (a)-[e]-(b)
            RETURN a.device_id AS src, b.device_id AS dst, type(e) AS rel,
                   e.confidence AS confidence, e.source AS source
            """, did=device_id, depth=depth,
        )
        for r in edges:
            out["edges"].append({
                "from": r["src"], "to": r["dst"],
                "label": r["rel"],
                "arrows": "to",
                "color": _edge_color(r["rel"], r["confidence"], r["source"]),
                "title": f"{r['rel']} (conf={r['confidence']}, source={r['source']})",
            })
    return out


def _edge_color(rel: str, confidence: float | None, source: str | None) -> dict:
    if not confidence or confidence >= 0.9:
        return {"color": "#2c7be5"}
    if confidence >= 0.6:
        return {"color": "#f5a623"}
    return {"color": "#d9534f"}


def grafana_site_table(site_id: str) -> dict:
    """Build a Grafana table panel JSON summarizing criticality for `site_id`."""
    q = site_criticality(site_id)
    s = load_settings()
    driver = _driver()
    with driver.session(database=s.neo4j.database) as sess:
        rows = sess.run(q.cypher, **q.params).data()
    return {
        "title": f"Topology criticality — {site_id}",
        "type": "table",
        "datasource": {"type": "neo4j", "uid": "neo4j"},
        "targets": [{
            "refId": "A",
            "query": q.cypher,
            "parameters": q.params,
        }],
        "transformations": [
            {"id": "organize", "options": {
                "excludeByName": {},
                "indexByName": {"device_id": 0, "type": 1, "status": 2, "fanout": 3},
            }},
        ],
        "fieldConfig": {
            "overrides": [
                {"matcher": {"id": "byName", "options": "fanout"},
                 "properties": [{"id": "custom.displayMode", "value": "color-background"}]},
            ],
        },
        "options": {"showHeader": True, "sortBy": [{"displayName": "fanout", "desc": True}]},
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", required=False, help="Focused device for vis export")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--site", required=False, help="Site for Grafana criticality panel")
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.device:
        path = args.out_dir / f"vis_{args.device}.json"
        path.write_text(json.dumps(vis_subgraph(args.device, depth=args.depth), indent=2))
        log.info("wrote %s", path)
    if args.site:
        path = args.out_dir / f"grafana_{args.site}.json"
        path.write_text(json.dumps(grafana_site_table(args.site), indent=2))
        log.info("wrote %s", path)


if __name__ == "__main__":
    main()
