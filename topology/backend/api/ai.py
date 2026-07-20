"""
AI / RCA endpoints.

GET    /api/ai/insights                          → list recent stored insights
GET    /api/ai/insights/{id}                     → one insight
DELETE /api/ai/insights/{id}                     → dismiss a single insight
GET    /api/ai/insights/by-device/{device_id}    → most recent insight for a device
DELETE /api/ai/insights/by-device/{device_id}    → dismiss all insights for a device
GET    /api/ai/analyze/{device_id}               → cached insight (no LLM call)
POST   /api/ai/analyze/{device_id}               → trigger fresh analysis (cache-aware)
GET    /api/ai/explain/{insight_id}              → full reasoning chain
PATCH  /api/ai/insights/{id}/feedback            → record HITL confidence adjustment
DELETE /api/ai/insights/{id}/feedback            → clear HITL feedback
GET    /api/ai/stats                             → aggregate AI accuracy stats
GET    /api/ai/health                            → ping the Foundry endpoint
"""
from __future__ import annotations

import json
import logging
import os
import time as _time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.ai.context import build_context
from backend.ai.foundry_client import get_client
from backend.database import pool
import pandas as pd

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])


# Cache TTL — how long an insight is considered fresh. Override via env.
INSIGHT_CACHE_TTL_MIN = int(os.environ.get("INSIGHT_CACHE_TTL_MIN", "15"))


# ─────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────

_INSERT_SQL = """INSERT INTO iot.ai_insights
    (device_id, device_type, summary, root_cause_device_id, root_cause_device_type,
     confidence, severity, blast_radius_json, recommended_actions_json, rationale,
     ok, error, model, elapsed_s, context_json, payload_json)
OUTPUT INSERTED.insight_id, INSERTED.created_at
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

_LIST_SQL = """SELECT TOP (?) insight_id, created_at, device_id, device_type, summary,
       root_cause_device_id, root_cause_device_type, confidence, severity,
       blast_radius_json, recommended_actions_json, rationale, ok, error,
       model, elapsed_s, human_confidence, feedback_notes, feedback_at
FROM iot.ai_insights
ORDER BY created_at DESC, insight_id DESC"""

_BY_ID_SQL = """SELECT insight_id, created_at, device_id, device_type, summary,
       root_cause_device_id, root_cause_device_type, confidence, severity,
       blast_radius_json, recommended_actions_json, rationale, ok, error,
       model, elapsed_s, human_confidence, feedback_notes, feedback_at
FROM iot.ai_insights
WHERE insight_id = ?"""

_BY_ID_FULL_SQL = """SELECT insight_id, created_at, device_id, device_type, summary,
       root_cause_device_id, root_cause_device_type, confidence, severity,
       blast_radius_json, recommended_actions_json, rationale, ok, error,
       model, elapsed_s, payload_json
FROM iot.ai_insights
WHERE insight_id = ?"""

_BY_DEVICE_SQL = """SELECT TOP 1 insight_id, created_at, device_id, device_type, summary,
       root_cause_device_id, root_cause_device_type, confidence, severity,
       blast_radius_json, recommended_actions_json, rationale, ok, error,
       model, elapsed_s, human_confidence, feedback_notes, feedback_at
FROM iot.ai_insights
WHERE device_id = ?
ORDER BY created_at DESC, insight_id DESC"""


def _row_to_insight(row: tuple) -> dict[str, Any]:
    (
        insight_id, created_at, device_id, device_type, summary,
        root_cause_device_id, root_cause_device_type, confidence, severity,
        blast_radius_json, recommended_actions_json, rationale, ok, error,
        model, elapsed_s,
        human_confidence, feedback_notes, feedback_at,
    ) = row
    has_feedback = human_confidence is not None
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
        "human_confidence": float(human_confidence) if has_feedback else None,
        "feedback_notes": feedback_notes,
        "feedback_at": feedback_at.isoformat() if hasattr(feedback_at, "isoformat") else (str(feedback_at) if feedback_at else None),
        "has_feedback": has_feedback,
        "effective_confidence": float(human_confidence) if has_feedback else (float(confidence) if confidence is not None else 0.0),
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


def _is_insight_fresh(insight_row, device_last_seen) -> bool:
    """An insight is fresh if BOTH:
       1. It was created within INSIGHT_CACHE_TTL_MIN minutes
       2. The device hasn't changed state since the insight was created
    """
    if not insight_row:
        return False
    created_at = insight_row[1] if len(insight_row) > 1 else None
    if created_at is None:
        return False

    try:
        age_seconds = _time.time() - created_at.timestamp()
    except (AttributeError, TypeError):
        return False
    if age_seconds > INSIGHT_CACHE_TTL_MIN * 60:
        return False

    if device_last_seen is not None:
        try:
            if device_last_seen.timestamp() > created_at.timestamp():
                return False
        except (AttributeError, TypeError):
            pass

    return True


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


@router.delete("/insights/{insight_id}")
def delete_insight(insight_id: int):
    """Delete a single insight by id. Used by the dismiss (X) button in the UI."""
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM iot.ai_insights WHERE insight_id = ?", insight_id)
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Insight not found")
            conn.commit()
        return {"ok": True, "deleted_insight_id": insight_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_insight failed: %s", e)
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


@router.delete("/insights/by-device/{device_id}")
def delete_insights_by_device(device_id: str):
    """Delete all insights for a device. Used when re-analyzing with replace=true
    or for bulk dismissal from the dashboard."""
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM iot.ai_insights WHERE device_id = ?", device_id)
            deleted = cur.rowcount
            conn.commit()
        return {"ok": True, "device_id": device_id, "deleted_count": deleted}
    except Exception as e:
        logger.error("delete_insights_by_device failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analyze/{device_id}")
def get_cached_analysis(device_id: str):
    """Return the most recent cached insight for a device if fresh, else 404."""
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(_BY_DEVICE_SQL, device_id)
            existing = cur.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="No insight found for device")

            cur.execute("SELECT last_seen FROM iot.devices WHERE device_id = ?", device_id)
            dev_row = cur.fetchone()
            device_last_seen = dev_row[0] if dev_row else None

        if not _is_insight_fresh(existing, device_last_seen):
            raise HTTPException(
                status_code=404,
                detail=f"Latest insight is older than {INSIGHT_CACHE_TTL_MIN} min or device state has changed",
            )
        return _row_to_insight(existing)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/analyze/{device_id}")
def analyze_device(
    device_id: str,
    force: bool = Query(default=False, description="Bypass cache and re-run analysis"),
    replace: bool = Query(
        default=False,
        description="Delete existing insights for this device before running a new one",
    ),
):
    """Run a fresh AI RCA for a single device.

    - ?force=true   → bypass cache
    - ?replace=true → delete any existing insights for this device first
    """
    ctx = build_context(device_id)
    device = ctx["device"]
    if not device or not device.get("device_id"):
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")

    if not force and not replace:
        try:
            with pool.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(_BY_DEVICE_SQL, device_id)
                existing = cur.fetchone()
            if existing and _is_insight_fresh(existing, device.get("last_seen")):
                logger.info("Cache hit for %s, returning insight_id=%s", device_id, existing[0])
                return _row_to_insight(existing)
        except Exception as e:
            logger.warning("Cache check failed for %s: %s", device_id, e)

    if replace:
        try:
            with pool.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM iot.ai_insights WHERE device_id = ?", device_id)
                logger.info("Replaced: deleted %d existing insight(s) for %s", cur.rowcount, device_id)
                conn.commit()
        except Exception as e:
            logger.warning("Failed to clear insights for %s before replace: %s", device_id, e)

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


@router.get("/explain/{insight_id}")
def explain_insight(insight_id: int):
    """Return the full reasoning chain for an insight."""
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(_BY_ID_FULL_SQL, insight_id)
            row = cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not row:
        raise HTTPException(status_code=404, detail="Insight not found")

    cols = [
        "insight_id", "created_at", "device_id", "device_type", "summary",
        "root_cause_device_id", "root_cause_device_type", "confidence", "severity",
        "blast_radius_json", "recommended_actions_json", "rationale", "ok", "error",
        "model", "elapsed_s", "payload_json",
    ]
    record = dict(zip(cols, row))

    payload = {}
    if record.get("payload_json"):
        try:
            payload = json.loads(record["payload_json"])
        except (TypeError, json.JSONDecodeError):
            payload = {}

    local_hypothesis = payload.get("local_hypothesis") or {}
    topology_summary = payload.get("topology_summary") or {}
    blast_radius = (
        json.loads(record["blast_radius_json"]) if record.get("blast_radius_json") else []
    )
    recommended_actions = (
        json.loads(record["recommended_actions_json"]) if record.get("recommended_actions_json") else []
    )

    return {
        "insight_id": int(record["insight_id"]),
        "created_at": record["created_at"].isoformat() if hasattr(record["created_at"], "isoformat") else str(record["created_at"]),
        "device_id": record["device_id"],
        "device_type": record["device_type"],
        "summary": record["summary"],
        "root_cause_device_id": record["root_cause_device_id"],
        "root_cause_device_type": record["root_cause_device_type"],
        "confidence": float(record["confidence"]) if record["confidence"] is not None else 0.0,
        "severity": record["severity"],
        "blast_radius": blast_radius,
        "recommended_actions": recommended_actions,
        "rationale": record["rationale"],
        "ok": bool(record["ok"]) if record["ok"] is not None else True,
        "error": record["error"],
        "model": record["model"],
        "elapsed_s": float(record["elapsed_s"]) if record["elapsed_s"] is not None else 0.0,
        "local_hypothesis": {
            "candidate_device_id": local_hypothesis.get("candidate_device_id"),
            "candidate_device_type": local_hypothesis.get("candidate_device_type"),
            "score": local_hypothesis.get("score", 0.0),
            "reasoning": local_hypothesis.get("reasoning", ""),
            "signal_breakdown": local_hypothesis.get("signal_breakdown", {}),
        },
        "topology_summary": {
            "upstream_count": topology_summary.get("upstream_count", 0),
            "downstream_count": topology_summary.get("downstream_count", 0),
            "offline_sibling_count": topology_summary.get("offline_sibling_count", 0),
            "events_in_last_5m": topology_summary.get("events_in_last_5m", 0),
            "events_in_last_15m": topology_summary.get("events_in_last_15m", 0),
            "events_in_last_60m": topology_summary.get("events_in_last_60m", 0),
            "cascade_suspected": topology_summary.get("cascade_suspected", False),
        },
    }


# ─────────────────────────────────────────────────────────────────
# HITL Feedback
# ─────────────────────────────────────────────────────────────────

class FeedbackPayload(BaseModel):
    human_confidence: float = Query(..., ge=0.0, le=1.0, description="User-adjusted confidence 0.0..1.0")
    feedback_notes: Optional[str] = None


@router.patch("/insights/{insight_id}/feedback")
def submit_feedback(insight_id: int, payload: FeedbackPayload):
    """Record a human-in-the-loop confidence adjustment.

    Stores human_confidence, optional notes, and feedback_at. Does NOT modify
    the original model confidence — keeps both for comparison.
    """
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT confidence FROM iot.ai_insights WHERE insight_id = ?",
                insight_id,
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Insight not found")
            model_conf = float(row[0]) if row[0] is not None else 0.0

            cur.execute(
                """UPDATE iot.ai_insights
                   SET human_confidence = ?, feedback_notes = ?, feedback_at = SYSUTCDATETIME()
                   WHERE insight_id = ?""",
                (payload.human_confidence, payload.feedback_notes, insight_id),
            )
            conn.commit()
            cur.execute(_BY_ID_SQL, insight_id)
            updated = cur.fetchone()

        insight = _row_to_insight(updated)
        return {
            **insight,
            "feedback": {
                "human_confidence": payload.human_confidence,
                "model_confidence": model_conf,
                "delta": round(payload.human_confidence - model_conf, 3),
                "notes": payload.feedback_notes,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("submit_feedback failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/insights/{insight_id}/feedback")
def clear_feedback(insight_id: int):
    """Clear HITL feedback on an insight (revert to model-only)."""
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE iot.ai_insights
                   SET human_confidence = NULL, feedback_notes = NULL, feedback_at = NULL
                   WHERE insight_id = ?""",
                insight_id,
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Insight not found")
            conn.commit()
        return {"ok": True, "insight_id": insight_id, "feedback_cleared": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("clear_feedback failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
def ai_stats(days: int = Query(default=30, ge=1, le=365)):
    """Aggregate stats about AI accuracy and human feedback."""
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT
                       COUNT(*) AS total,
                       SUM(CASE WHEN human_confidence IS NOT NULL THEN 1 ELSE 0 END) AS with_feedback,
                       AVG(confidence) AS mean_model,
                       AVG(human_confidence) AS mean_human
                   FROM iot.ai_insights
                   WHERE created_at >= DATEADD(DAY, -?, SYSUTCDATETIME())""",
                days,
            )
            row = cur.fetchone()
            total, with_fb, mean_model, mean_human = row

            cur.execute(
                """SELECT
                       SUM(CASE WHEN ABS(ISNULL(human_confidence, confidence) - confidence) <= 0.1 THEN 1 ELSE 0 END) AS close_calls,
                       SUM(CASE WHEN human_confidence > confidence + 0.1 THEN 1 ELSE 0 END) AS underrated,
                       SUM(CASE WHEN human_confidence < confidence - 0.1 THEN 1 ELSE 0 END) AS overrated
                   FROM iot.ai_insights
                   WHERE created_at >= DATEADD(DAY, -?, SYSUTCDATETIME())
                     AND human_confidence IS NOT NULL""",
                days,
            )
            close_calls, underrated, overrated = cur.fetchone()
            close_calls = close_calls or 0
            underrated = underrated or 0
            overrated = overrated or 0

        with_fb = with_fb or 0
        total = total or 0
        agreement_pct = round(100.0 * close_calls / with_fb, 1) if with_fb else 0.0

        return {
            "window_days": days,
            "total_insights": int(total),
            "with_feedback": int(with_fb),
            "without_feedback": int(total - with_fb),
            "mean_model_confidence": round(float(mean_model or 0.0), 3),
            "mean_human_confidence": round(float(mean_human or 0.0), 3),
            "agreement_within_0_1_pct": agreement_pct,
            "human_rated_higher": int(underrated),
            "human_rated_lower": int(overrated),
        }
    except Exception as e:
        logger.error("ai_stats failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
def health():
    client = get_client()
    ok = client.ping()
    return {
        "foundry_ok": ok,
        "endpoint": client.endpoint,
        "model": client.model_deployment,
    }
