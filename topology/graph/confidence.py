"""Confidence scoring + graph merge (Stage 4).

Combines the various edge sources into a single canonical edge set in
Postgres (`topology.edges`) and applies Tier 1/Tier 2 conflict resolution.

Conflict policy:
  - If an active (Tier 1) edge contradicts an inferred (Tier 2) edge, the
    active edge wins and the inferred edge is recorded with `review_status=conflict`.
  - If two active edges disagree on the destination, both are kept but flagged.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from topology.config import load_settings

log = logging.getLogger("topology.graph.confidence")


def edge_confidence(*, active_probe: bool, lift: float | None,
                    sequence_support: int | None, source_count: int | None) -> float:
    """Combine multiple signals into a 0..1 confidence.

    0.95 is reserved for confirmed active probes. Inferred edges are clamped to
    0.9 because even perfect statistics don't prove a causal relationship.
    """
    score = 0.0
    if active_probe:
        score = max(score, 0.95)
    if lift is not None and lift > 0:
        score = max(score, min(0.9, 0.4 + 0.05 * min(10, lift)))
    if sequence_support and sequence_support > 50:
        score = max(score, 0.7)
    if source_count and source_count >= 3:
        score = max(score, min(0.85, 0.4 + 0.1 * min(4, source_count)))
    return score


def review_action(confidence: float) -> str:
    cfg = load_settings().confidence
    if confidence >= cfg.auto_accept:
        return "approved"
    if confidence >= cfg.auto_flag:
        return "flagged"
    if confidence >= cfg.suggest:
        return "pending"
    return "rejected"


@dataclass
class Edge:
    src: str
    dst: str
    rel: str
    confidence: float
    source: str
    inferred: bool
    properties: dict


def upsert(edges: Iterable[Edge]) -> int:
    """Upsert edges into Postgres with conflict resolution."""
    cfg = load_settings()
    eng = sa.create_engine(
        f"postgresql+psycopg2://{cfg.postgres.user}:{cfg.postgres.password}@"
        f"{cfg.postgres.host}:{cfg.postgres.port}/{cfg.postgres.database}",
        future=True,
    )
    rows = []
    for e in edges:
        action = review_action(e.confidence)
        # Auto-rejected: don't store
        if action == "rejected":
            continue
        rows.append({
            "src_device_id": e.src,
            "dst_device_id": e.dst,
            "rel_type": e.rel,
            "confidence": e.confidence,
            "source": e.source,
            "inferred": e.inferred,
            "properties": json.dumps(e.properties),
            "first_observed": e.properties.get("first_seen"),
            "last_observed":  e.properties.get("last_seen"),
            "observed_count": e.properties.get("observed_count", 0),
            "review_status": action if action != "approved" else None,
        })
    if not rows:
        return 0

    table = sa.table(
        "edges", schema="topology",
        sa.column("src_device_id"), sa.column("dst_device_id"),
        sa.column("rel_type"), sa.column("confidence"),
        sa.column("source"), sa.column("inferred"),
        sa.column("properties"), sa.column("first_observed"),
        sa.column("last_observed"), sa.column("observed_count"),
        sa.column("review_status"),
    )
    stmt = insert(table).values(rows)
    update_cols = {
        "confidence": stmt.excluded["confidence"],
        "properties": stmt.excluded["properties"],
        "last_observed": stmt.excluded["last_observed"],
        "observed_count": sa.text("topology.edges.observed_count + EXCLUDED.observed_count"),
        "review_status": stmt.excluded["review_status"],
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["src_device_id", "dst_device_id", "rel_type", "source"],
        set_=update_cols,
    )
    with eng.begin() as cx:
        cx.execute(stmt)
    return len(rows)


def resolve_conflicts() -> int:
    """For each (src, dst, rel_type), if an active probe exists and an
    inferred edge disagrees, mark the inferred edge as 'conflict'.
    """
    eng = sa.create_engine(
        f"postgresql+psycopg2://{load_settings().postgres.user}:"
        f"{load_settings().postgres.password}@{load_settings().postgres.host}:"
        f"{load_settings().postgres.port}/{load_settings().postgres.database}",
        future=True,
    )
    sql = sa.text("""
        UPDATE topology.edges e
        SET review_status = 'conflict'
        WHERE e.inferred = TRUE
          AND e.review_status IS DISTINCT FROM 'conflict'
          AND EXISTS (
              SELECT 1 FROM topology.edges a
              WHERE a.src_device_id = e.src_device_id
                AND a.dst_device_id = e.dst_device_id
                AND a.rel_type = e.rel_type
                AND a.inferred = FALSE
                AND a.dst_device_id <> e.dst_device_id
          )
    """)
    with eng.begin() as cx:
        result = cx.execute(sql)
    return result.rowcount or 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="input", type=Path, required=True,
                    help="NDJSON of edges with fields: src, dst, rel, confidence, source, inferred, properties")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    edges = []
    with args.input.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            edges.append(Edge(
                src=d["src"], dst=d["dst"], rel=d["rel"],
                confidence=float(d.get("confidence", 0)),
                source=d.get("source", "manual"),
                inferred=bool(d.get("inferred", False)),
                properties=d.get("properties", {}),
            ))
    n = upsert(edges)
    c = resolve_conflicts()
    log.info("upserted %d edges, resolved %d conflicts", n, c)


if __name__ == "__main__":
    main()
