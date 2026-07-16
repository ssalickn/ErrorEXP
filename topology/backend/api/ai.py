"""
AI RCA endpoints.

Exposes:
  GET /api/ai/findings/{log_id}     -- per-event LLM verdict
  GET /api/ai/cascades               -- recent detected root causes
  GET /api/ai/cascades/{cascade_id}  -- single cascade detail
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
import pandas as pd

from backend.database import pool

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _safe_json(value: Any) -> Any:
    """Parse JSON columns that may have been returned as raw strings."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


@router.get("/findings/{log_id}")
def get_finding(log_id: int):
    with pool.get_connection() as conn:
        df = pd.read_sql(
            """
            SELECT finding_id, log_id, device_id, root_cause, actions,
                   confidence, citations, raw, created_at
            FROM ai.rca_findings
            WHERE log_id = ?
            """,
            conn, params=[log_id],
        )
    if df.empty:
        raise HTTPException(status_code=404, detail="No finding for that log_id")
    rec = df.iloc[0].to_dict()
    rec["actions"] = _safe_json(rec.get("actions")) or []
    rec["citations"] = _safe_json(rec.get("citations")) or []
    rec["raw"] = _safe_json(rec.get("raw")) or {}
    return rec


@router.get("/cascades")
def list_cascades(limit: int = Query(default=50, le=500)):
    with pool.get_connection() as conn:
        df = pd.read_sql(
            """
            SELECT TOP (?) cascade_id, detected_at, window_start, window_end,
                   cluster_size, root_cause_device_id, root_cause_confidence,
                   candidates, affected_device_ids, explanation,
                   contributing_log_ids
            FROM ai.cascades
            ORDER BY detected_at DESC
            """,
            conn, params=[limit],
        )
    records = []
    for _, r in df.iterrows():
        rec = r.to_dict()
        rec["candidates"] = _safe_json(rec.get("candidates")) or []
        rec["affected_device_ids"] = _safe_json(rec.get("affected_device_ids")) or []
        rec["contributing_log_ids"] = _safe_json(rec.get("contributing_log_ids")) or []
        records.append(rec)
    return records


@router.get("/cascades/{cascade_id}")
def get_cascade(cascade_id: int):
    with pool.get_connection() as conn:
        df = pd.read_sql(
            """
            SELECT cascade_id, detected_at, window_start, window_end,
                   cluster_size, root_cause_device_id, root_cause_confidence,
                   candidates, affected_device_ids, explanation,
                   contributing_log_ids
            FROM ai.cascades
            WHERE cascade_id = ?
            """,
            conn, params=[cascade_id],
        )
    if df.empty:
        raise HTTPException(status_code=404, detail="Cascade not found")
    rec = df.iloc[0].to_dict()
    rec["candidates"] = _safe_json(rec.get("candidates")) or []
    rec["affected_device_ids"] = _safe_json(rec.get("affected_device_ids")) or []
    rec["contributing_log_ids"] = _safe_json(rec.get("contributing_log_ids")) or []
    return rec
