"""
AI / RCA endpoints.

GET  /api/ai/insights            → list recent stored insights
GET  /api/ai/insights/{id}       → one insight
GET  /api/ai/insights/by-device/{device_id}  → most recent insight for a device
POST /api/ai/analyze/{device_id} → trigger a fresh analysis for a device
GET  /api/ai/health              → ping the Foundry endpoint
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.ai.context import build_context
from backend.ai.foundry_client import get_client
from backend.database import pool
import pandas as pd

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])


# ─────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────

_INSERT_SQL = """
INSERT INTO iot.ai_insights
    (device_id, device_type, summary, root_cause_device_id, root_cause_device_type,
     confidence, severity, blast_radius_json, recommended_actions_json, rationale,
     ok, error, model, elapsed_s, context_json, payload_json)
OUTPUT INSERTED.insight_id, INSERTED.created_at
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_LIST_SQL = """
SELECT TOP (?) insight_id, created_at, device_id, device_type, summary,
       root_cause_device_id, root_cause_device_type, confidence, severity,
       blast_radius_json, recommended_actions_json, rationale, ok, error,
       model, elapsed_s
FROM iot.ai_insights
ORDER BY created_at DESC, insight_id DESC
"""

_BY_ID_SQL = """
SELECT insight_id, created_at, device_id, device_type, summary,
       root_cause_device_id, root_cause_device_type, confidence, severity,
       blast_radius_json, recommended_actions_json, rationale, ok, error,
       model, elapsed_s
FROM iot.ai_insights
WHERE insight_id = ?
"""

_BY_DEVICE_SQL = """
SELECT TOP 1 insight_id, created_at, device_id, device_type, summary,
       root_cause_device_id, root_cause_device_type, confidence, severity,
       blast_radius_json, recommended_actions_json, rationale, ok, error,
       model, elapsed_s
FROM iot.incidents
WHERE device_id = ?
ORDER BY created_at DESC, insight_id DESC
"""


def _row_to_insight(row: tuple) -> dict[str, Any]:
    (
        insight_id, created_at, device_id, device_type, summary,
        root_cause_device_id, root_cause_device_type, confidence, severity,
        blast_radius_json, recommended_actions_json, rationale, ok, error,
        model, elapsed_s,
    ) = row
    return {
        "insight_id": int(insight_id),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        "device_id": device_id,
        "device_type": device_type,
        "summary": summary,
        "root_cause_device_id": root_cause_device_id,
        "root_cause_device_type": root_cause_device_type,
        "confidence": float(confidence) if confidence is not None else 0.0,
        "severity": severity,
        "blast_radius": json.loads(blast_radius_json) if blast_radius_json else [],
        "recommended_actions": json.loads(recommended_actions_json) if recommended_actions_json else [],
        "rationale": rationale,
        "ok": bool(ok) if ok is not None else True,
        "error": error,
        "model": model,
        "elapsed_s": float(elapsed_s) if elapsed_s is not None else 0.0,
    }


def _persist_insight(device: dict[str, Any], result: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Insert an insight and return the stored row. Returns None on DB error."""
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            row = (
                device.get("device_id"),
                device.get("device_type"),
                result.get("summary"),
                result.get("root_cause_device_id"),
                result.get("root_cause_device_type"),
                float(result.get("confidence") or 0.0),
                result.get("severity"),
                json.dumps(result.get("blast_radius") or []),
                json.dumps(result.get("recommended_actions") or []),
                result.get("rationale"),
                1 if result.get("ok") else 0,
                result.get("error"),
                result.get("model") or get_client().model_deployment,
                float(result.get("elapsed_s") or 0.0),
                None,  # context_json — could store the full request context
                json.dumps(result, default=str),
            )
            cur.execute(_INSERT_SQL, row)
            out = cur.fetchone()
            insight_id, created_at = out[0], out[1]
            return {
                "insight_id": int(insight_id),
                "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
                "device_id": device.get("device_id"),
                "device_type": device.get("device_type"),
                "summary": result.get("summary"),
                "root_cause_device_id": result.get("root_cause_device_id"),
                "root_cause_device_type": result.get("root_cause_device_type"),
                "confidence": float(result.get("confidence") or 0.0),
                "severity": result.get("severity"),
                "blast_radius": result.get("blast_radius") or [],
                "recommended_actions": result.get("recommended_actions") or [],
                "rationale": result.get("rationale"),
                "ok": bool(result.get("ok")),
                "error": result.get("error"),
                "model": row[12],
                "elapsed_s": float(result.get("elapsed_s") or 0.0),
            }
    except Exception as e:
        logger.error("Failed to persist AI insight: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────


@router.get("/insights")
def list_insights(limit: int = Query(default=20, le=200)):
    """List the most recent AI insights (most recent first)."""
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql(_LIST_SQL, conn, params=[limit])
        return [_row_to_insight(tuple(r)) for r in df.itertuples(index=False, name=None)]
    except Exception as e:
        # Table may not exist yet — degrade gracefully so the dashboard still loads
        logger.warning("list_insights failed: %s", e)
        return []


@router.get("/insights/{insight_id}")
def get_insight(insight_id: int):
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(_BY_ID_SQL, insight_id)
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Insight not found")
        return _row_to_insight(row)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/insights/by-device/{device_id}")
def get_insight_by_device(device_id: str):
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(_BY_DEVICE_SQL, device_id)
            row = cur.fetchone()
        if not row:
            return None
        return _row_to_insight(row)
    except Exception as e:
        logger.warning("get_insight_by_device failed: %s", e)
        return None


@router.post("/analyze/{device_id}")
def analyze_device(device_id: str):
    """Run a fresh AI RCA for a single device. Stores and returns the result."""
    ctx = build_context(device_id)
    device = ctx["device"]
    if not device or not device.get("device_id"):
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")

    client = get_client()
    result = client.analyze(
        device=device,
        recent_events=ctx["recent_events"],
        upstream=ctx["upstream"],
        downstream=ctx["downstream"],
        offline_siblings=ctx["offline_siblings"],
        site_context=ctx["site_context"],
    )
    result["model"] = client.model_deployment
    stored = _persist_insight(device, result)
    return stored or {
        "device_id": device_id,
        "ok": result.get("ok", False),
        "summary": result.get("summary"),
        "root_cause_device_id": result.get("root_cause_device_id"),
        "confidence": result.get("confidence"),
        "severity": result.get("severity"),
        "blast_radius": result.get("blast_radius"),
        "recommended_actions": result.get("recommended_actions"),
        "rationale": result.get("rationale"),
        "error": result.get("error"),
        "model": client.model_deployment,
        "elapsed_s": result.get("elapsed_s"),
    }


@router.get("/health")
def health():
    client = get_client()
    ok = client.ping()
    return {
        "foundry_ok": ok,
        "endpoint": client.endpoint,
        "model": client.model_deployment,
    }
