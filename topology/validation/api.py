"""Lightweight HITL review service (FastAPI).

Exposes:
  GET  /api/topology/edges?status=pending&limit=200
  POST /api/topology/edges/{edge_id}/review {decision: approved|rejected, reviewer: str, note: str}
  GET  /api/topology/edges/{edge_id}
  GET  /api/topology/drift?days=7

All endpoints write their decisions back into topology.edges and forward them
to Neo4j on the next loader run.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from topology.config import load_settings

log = logging.getLogger("topology.validation.api")

app = FastAPI(title="Topology Validation API", version="0.1.0")


def _engine():
    s = load_settings()
    return sa.create_engine(
        f"postgresql+psycopg2://{s.postgres.user}:{s.postgres.password}@"
        f"{s.postgres.host}:{s.postgres.port}/{s.postgres.database}",
        future=True,
    )


class ReviewDecision(BaseModel):
    decision: str            # approved | rejected
    reviewer: str
    note: str = ""


@app.get("/api/topology/edges")
def list_edges(status: Optional[str] = Query(None),
               limit: int = 200, offset: int = 0):
    sql = "SELECT edge_id, src_device_id, dst_device_id, rel_type, confidence, source, inferred, review_status, properties FROM topology.edges"
    args: dict = {"limit": limit, "offset": offset}
    if status:
        sql += " WHERE review_status = :status"
        args["status"] = status
    sql += " ORDER BY confidence DESC LIMIT :limit OFFSET :offset"
    with _engine().connect() as cx:
        rows = [dict(r._mapping) for r in cx.execute(sa.text(sql), args)]
    return {"edges": rows, "count": len(rows)}


@app.get("/api/topology/edges/{edge_id}")
def get_edge(edge_id: int):
    with _engine().connect() as cx:
        row = cx.execute(sa.text(
            "SELECT edge_id, src_device_id, dst_device_id, rel_type, confidence, source, inferred, properties, review_status, reviewed_by, reviewed_at FROM topology.edges WHERE edge_id = :eid"
        ), {"eid": edge_id}).first()
    if not row:
        raise HTTPException(404, "edge not found")
    return dict(row._mapping)


@app.post("/api/topology/edges/{edge_id}/review")
def review_edge(edge_id: int, decision: ReviewDecision):
    if decision.decision not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved|rejected")
    sql = sa.text("""
        UPDATE topology.edges
        SET review_status = :decision,
            reviewed_by   = :reviewer,
            reviewed_at   = :ts,
            properties    = properties || jsonb_build_object('review_note', :note)
        WHERE edge_id = :eid
        RETURNING edge_id
    """)
    with _engine().begin() as cx:
        r = cx.execute(sql, {
            "decision": decision.decision,
            "reviewer": decision.reviewer,
            "ts": datetime.now(timezone.utc),
            "note": decision.note,
            "eid": edge_id,
        }).first()
    if not r:
        raise HTTPException(404, "edge not found")
    return {"edge_id": edge_id, "status": decision.decision}


@app.get("/api/topology/drift")
def drift(days: int = 7, limit: int = 200):
    with _engine().connect() as cx:
        rows = cx.execute(sa.text("""
            SELECT drift_id, detected_at, change_kind, subject, payload
            FROM topology.drift_log
            WHERE detected_at > now() - (:days || ' days')::interval
            ORDER BY detected_at DESC
            LIMIT :limit
        """), {"days": days, "limit": limit}).fetchall()
    out = []
    for r in rows:
        out.append({
            "drift_id": r[0], "detected_at": r[1].isoformat() if r[1] else None,
            "change_kind": r[2], "subject": r[3],
            "payload": r[4] if isinstance(r[4], dict) else json.loads(r[4] or "{}"),
        })
    return {"events": out, "count": len(out)}


@app.get("/api/topology/stats")
def stats():
    """Top-level metrics for the operations dashboard."""
    with _engine().connect() as cx:
        devices = cx.execute(sa.text("SELECT count(*) FROM topology.devices")).scalar()
        edges = cx.execute(sa.text("SELECT count(*) FROM topology.edges")).scalar()
        inferred = cx.execute(sa.text("SELECT count(*) FROM topology.edges WHERE inferred = true")).scalar()
        pending = cx.execute(sa.text("SELECT count(*) FROM topology.edges WHERE review_status IN ('pending','flagged')")).scalar()
        drift_24h = cx.execute(sa.text(
            "SELECT count(*) FROM topology.drift_log WHERE detected_at > now() - interval '1 day'"
        )).scalar()
    return {
        "devices": devices, "edges": edges, "inferred_edges": inferred,
        "pending_review": pending, "drift_24h": drift_24h,
    }
