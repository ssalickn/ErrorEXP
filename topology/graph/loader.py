"""Load Postgres devices + edges into Neo4j.

The loader is idempotent: it uses MERGE on device_id and on the relationship
key (src, dst, rel, source). This is the source of truth for the operational
graph used by RCA and the visualization layer.
"""
from __future__ import annotations

import argparse
import json
import logging
from typing import Iterable

import sqlalchemy as sa
from neo4j import GraphDatabase, Driver

from topology.config import load_settings

log = logging.getLogger("topology.graph.loader")


def _pg_engine():
    s = load_settings()
    url = f"postgresql+psycopg2://{s.postgres.user}:{s.postgres.password}@{s.postgres.host}:{s.postgres.port}/{s.postgres.database}"
    return sa.create_engine(url, future=True)


def _neo4j_driver() -> Driver:
    s = load_settings()
    return GraphDatabase.driver(s.neo4j.uri, auth=(s.neo4j.user, s.neo4j.password))


def _fetch_devices() -> list[dict]:
    eng = _pg_engine()
    with eng.connect() as cx:
        rows = cx.execute(sa.text("""
            SELECT device_id, device_type, vendor, model,
                   host(ip_address) AS ip, mac_address::text AS mac,
                   site, floor, rack, status, source_system, raw
            FROM topology.devices
        """)).fetchall()
    out = []
    for r in rows:
        out.append({k: r[i] for i, k in enumerate([
            "device_id", "device_type", "vendor", "model", "ip", "mac",
            "site", "floor", "rack", "status", "source_system", "raw",
        ])})
    return out


def _fetch_edges(only_approved: bool = False) -> list[dict]:
    eng = _pg_engine()
    sql = """
        SELECT src_device_id, dst_device_id, rel_type, confidence, source, inferred,
               properties::text AS properties, last_observed, observed_count,
               review_status
        FROM topology.edges
    """
    if only_approved:
        sql += " WHERE review_status IS NULL OR review_status IN ('approved', 'flagged')"
    with eng.connect() as cx:
        rows = cx.execute(sa.text(sql)).fetchall()
    out = []
    for r in rows:
        out.append({k: r[i] for i, k in enumerate([
            "src", "dst", "rel", "confidence", "source", "inferred",
            "properties", "last_observed", "observed_count", "review_status",
        ])})
    return out


def _device_props(d: dict) -> dict:
    raw = d.get("raw")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {"_raw": raw}
    return {
        "device_id": d["device_id"],
        "device_type": d.get("device_type") or "unknown",
        "vendor": d.get("vendor"),
        "model": d.get("model"),
        "ip": d.get("ip"),
        "mac": d.get("mac"),
        "site": d.get("site"),
        "floor": d.get("floor"),
        "rack": d.get("rack"),
        "status": d.get("status") or "unknown",
        "source_system": d.get("source_system"),
        "raw": raw or {},
    }


def _edge_props(e: dict) -> dict:
    props_raw = e.get("properties")
    if isinstance(props_raw, (bytes, bytearray)):
        props_raw = props_raw.decode("utf-8", "ignore")
    try:
        props = json.loads(props_raw) if props_raw else {}
    except json.JSONDecodeError:
        props = {"_raw": props_raw}
    return {
        "confidence": float(e.get("confidence") or 0),
        "source": e.get("source"),
        "inferred": bool(e.get("inferred")),
        "observed_count": int(e.get("observed_count") or 0),
        "last_observed": str(e.get("last_observed") or "") if e.get("last_observed") else None,
        "review_status": e.get("review_status"),
        **props,
    }


_REL_CLAUSE = """
MERGE (a:Device {device_id: $src})
MERGE (b:Device {device_id: $dst})
WITH a, b
CALL apoc.merge.relationship(a, $rel, $props, {}, b, {})
YIELD rel
RETURN rel
"""


def load_into_neo4j(*, only_approved: bool = False, batch: int = 500) -> dict:
    """Stream devices then edges into Neo4j with batched UNWIND."""
    s = load_settings()
    driver = _neo4j_driver()
    counts = {"devices": 0, "edges": 0}

    with driver.session(database=s.neo4j.database) as sess:
        # Devices
        devices = _fetch_devices()
        for i in range(0, len(devices), batch):
            chunk = devices[i:i + batch]
            sess.execute_write(_write_devices, chunk)
            counts["devices"] += len(chunk)
        log.info("wrote %d devices to Neo4j", counts["devices"])

        # Edges
        edges = _fetch_edges(only_approved=only_approved)
        for i in range(0, len(edges), batch):
            chunk = edges[i:i + batch]
            sess.execute_write(_write_edges, chunk)
            counts["edges"] += len(chunk)
        log.info("wrote %d edges to Neo4j", counts["edges"])

    driver.close()
    return counts


def _write_devices(tx, devices: list[dict]) -> None:
    tx.run(
        """
        UNWIND $devices AS d
        MERGE (n:Device {device_id: d.device_id})
        SET n.device_type   = d.device_type,
            n.vendor        = d.vendor,
            n.model         = d.model,
            n.ip            = d.ip,
            n.mac           = d.mac,
            n.site          = d.site,
            n.floor         = d.floor,
            n.rack          = d.rack,
            n.status        = d.status,
            n.source_system = d.source_system,
            n.last_loaded   = datetime()
        """,
        devices=[_device_props(d) for d in devices],
    )


def _write_edges(tx, edges: list[dict]) -> None:
    rows = [{
        "src": e["src"],
        "dst": e["dst"],
        "rel": e["rel"],
        "props": _edge_props(e),
    } for e in edges]
    # Use APOC's apoc.merge.relationship for dynamic relationship types.
    tx.run(
        """
        UNWIND $rows AS row
        MERGE (a:Device {device_id: row.src})
        MERGE (b:Device {device_id: row.dst})
        WITH a, b, row
        CALL apoc.merge.relationship(a, row.rel, row.props, {}, b, {})
        YIELD rel
        RETURN count(rel)
        """,
        rows=rows,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-approved", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    counts = load_into_neo4j(only_approved=args.only_approved)
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
