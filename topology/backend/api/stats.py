"""
Plant-wide statistics endpoint.

GET /api/stats                     → full summary (all metrics)
GET /api/stats?group_by=site       → break everything down by site
GET /api/stats?group_by=device_type→ break everything down by type
GET /api/stats?group_by=vendor     → break everything down by vendor

Each metric includes:
- total
- online / offline / degraded / unknown counts
- online_pct / offline_pct
- by_severity_event_count (last 24h: critical / error / warning / info)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Query
import pandas as pd

from backend.database import pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stats", tags=["stats"])


# ─────────────────────────────────────────────────────────────────
# SQL — one trip per aggregation, no per-device round-trips
# ─────────────────────────────────────────────────────────────────

_DEVICES_AGG_SQL = """
SELECT
    device_type,
    vendor,
    site_id,
    status,
    COUNT(*) AS n
FROM iot.devices
GROUP BY device_type, vendor, site_id, status
"""

_EVENTS_24H_SQL = """
SELECT
    dl.device_type,
    dl.severity,
    COUNT(*) AS n
FROM iot.device_logs dl
WHERE dl.event_time >= DATEADD(HOUR, -24, SYSUTCDATETIME())
GROUP BY dl.device_type, dl.severity
"""


def _build_breakdown(device_df: pd.DataFrame, group_by: str) -> list[dict[str, Any]]:
    """Group a device aggregation DataFrame by the requested dimension."""
    if device_df is None or device_df.empty:
        return []

    grouped = device_df.groupby(group_by, dropna=False)
    rows: list[dict[str, Any]] = []

    for key, sub in grouped:
        # `key` is a scalar if group_by is one column, tuple if multiple
        if not isinstance(key, tuple):
            key = (key,)
        record = dict(zip([group_by] if isinstance(group_by, str) else list(group_by), key))
        # Coerce NaN → None
        for k, v in list(record.items()):
            if v is None or (hasattr(v, "__class__") and v.__class__.__name__ == "float" and pd.isna(v)):
                record[k] = None
                continue
            if hasattr(v, "isoformat"):
                record[k] = v.isoformat()
            elif hasattr(v, "item"):
                try:
                    record[k] = v.item()
                except Exception:
                    pass

        total = int(sub["n"].sum())
        by_status = {str(k).lower(): int(v) for k, v in sub.groupby("status")["n"].sum().items()}
        online = by_status.get("online", 0)
        offline = by_status.get("offline", 0)
        degraded = by_status.get("degraded", 0)
        unknown = by_status.get("unknown", 0)

        record.update({
            "total": total,
            "online": online,
            "offline": offline,
            "degraded": degraded,
            "unknown": unknown,
            "online_pct": round(100.0 * online / total, 1) if total else 0.0,
            "offline_pct": round(100.0 * offline / total, 1) if total else 0.0,
            "degraded_pct": round(100.0 * degraded / total, 1) if total else 0.0,
        })
        rows.append(record)

    rows.sort(key=lambda r: (-r["total"], r.get(group_by) or ""))
    return rows


def _enrich_with_events(rows: list[dict[str, Any]], events_df: pd.DataFrame, join_key: str) -> list[dict[str, Any]]:
    """Attach last-24h event counts to each breakdown row."""
    if not rows or events_df is None or events_df.empty:
        for r in rows:
            r["events_24h"] = {"critical": 0, "error": 0, "warning": 0, "info": 0, "total": 0}
        return rows

    # Build a lookup keyed by (join_key value, severity)
    by_key: dict[Any, dict[str, int]] = {}
    for _, r in events_df.iterrows():
        k = r[join_key]
        sev = str(r["severity"]).lower()
        n = int(r["n"])
        if k not in by_key:
            by_key[k] = {"critical": 0, "error": 0, "warning": 0, "info": 0, "total": 0}
        by_key[k][sev] = by_key[k].get(sev, 0) + n
        by_key[k]["total"] += n

    for row in rows:
        k = row.get(join_key)
        if k is not None and k in by_key:
            row["events_24h"] = by_key[k]
        else:
            row["events_24h"] = {"critical": 0, "error": 0, "warning": 0, "info": 0, "total": 0}

    return rows


# ─────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────


@router.get("")
@router.get("/")
def plant_stats(group_by: Optional[str] = Query(default=None, description="device_type | vendor | site")):
    """Plant-wide statistics.

    If `group_by` is provided, returns a per-bucket breakdown in addition
    to the plant totals. Otherwise returns only the totals.
    """
    valid_groupings = {"device_type", "vendor", "site_id"}
    if group_by is not None and group_by not in valid_groupings:
        return {
            "ok": False,
            "error": f"group_by must be one of {sorted(valid_groupings)}; got '{group_by}'",
        }

    try:
        with pool.get_connection() as conn:
            device_df = pd.read_sql(_DEVICES_AGG_SQL, conn)
            try:
                events_df = pd.read_sql(_EVENTS_24H_SQL, conn)
            except Exception:
                # device_logs may not exist or be empty
                events_df = pd.DataFrame(columns=["device_type", "severity", "n"])

        # ── Plant-wide totals ──
        total = int(device_df["n"].sum()) if not device_df.empty else 0
        by_status = (
            {str(k).lower(): int(v) for k, v in device_df.groupby("status")["n"].sum().items()}
            if not device_df.empty else {}
        )
        online = by_status.get("online", 0)
        offline = by_status.get("offline", 0)
        degraded = by_status.get("degraded", 0)
        unknown = by_status.get("unknown", 0)

        # ── Plant-wide event totals (last 24h) ──
        by_sev = (
            {str(k).lower(): int(v) for k, v in events_df.groupby("severity")["n"].sum().items()}
            if not events_df.empty else {}
        )
        events_24h = {
            "critical": by_sev.get("critical", 0),
            "error": by_sev.get("error", 0),
            "warning": by_sev.get("warning", 0),
            "info": by_sev.get("info", 0),
            "total": int(events_df["n"].sum()) if not events_df.empty else 0,
        }

        response: dict[str, Any] = {
            "ok": True,
            "totals": {
                "devices": {
                    "total": total,
                    "online": online,
                    "offline": offline,
                    "degraded": degraded,
                    "unknown": unknown,
                    "online_pct": round(100.0 * online / total, 1) if total else 0.0,
                    "offline_pct": round(100.0 * offline / total, 1) if total else 0.0,
                    "degraded_pct": round(100.0 * degraded / total, 1) if total else 0.0,
                },
                "events_24h": events_24h,
            },
        }

        # ── Per-bucket breakdown (optional) ──
        if group_by is not None:
            breakdown = _build_breakdown(device_df, group_by)
            # device_type is the only join key for events; for vendor/site we
            # approximate by attaching plant-wide events to each row.
            if group_by == "device_type":
                breakdown = _enrich_with_events(breakdown, events_df, "device_type")
            else:
                for r in breakdown:
                    r["events_24h"] = events_24h
            response["group_by"] = group_by
            response["breakdown"] = breakdown

        return response
    except Exception as e:
        logger.error("plant_stats failed: %s", e)
        return {"ok": False, "error": str(e)}


@router.get("/health")
def stats_health():
    """Quick liveness check that the stats endpoint can reach the DB."""
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM iot.devices")
            n = cur.fetchone()[0]
        return {"ok": True, "device_count": int(n)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
