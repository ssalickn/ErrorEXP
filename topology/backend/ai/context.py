"""
Builds the context the AI analyzer needs for a given device:
- recent events
- upstream / downstream dependencies
- other currently-offline devices (likely siblings of the same root cause)
- a thin site summary

All data comes from the SQL Server store via backend.database.pool.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from backend.database import pool

logger = logging.getLogger(__name__)


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    out: list[dict[str, Any]] = []
    for rec in df.to_dict(orient="records"):
        for k, v in list(rec.items()):
            try:
                if pd.isna(v):
                    rec[k] = None
                    continue
            except (TypeError, ValueError):
                pass
            if hasattr(v, "isoformat"):
                rec[k] = v.isoformat()
            elif hasattr(v, "item") and not isinstance(v, (str, bytes, list, dict)):
                try:
                    rec[k] = v.item()
                except Exception:
                    pass
        out.append(rec)
    return out


def get_recent_events(device_id: str, hours_back: int = 24, limit: int = 30) -> list[dict[str, Any]]:
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql(
                """
                SELECT TOP (?) log_id, event_time, severity, status_code, status,
                       message, source_system
                FROM iot.device_logs
                WHERE device_id = ?
                  AND event_time >= DATEADD(HOUR, -?, SYSUTCDATETIME())
                ORDER BY event_time DESC
                """,
                conn,
                params=[limit, device_id, hours_back],
            )
    except Exception as e:
        logger.warning("get_recent_events failed for %s: %s", device_id, e)
        return []
    return _df_to_records(df)


def get_dependencies(device_id: str, depth: int = 3) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (upstream, downstream) using the recursive CTE already used elsewhere."""
    query = """
    WITH deps AS (
        SELECT target_id AS related_id, 1 AS depth, 'downstream' AS direction
        FROM iot.device_relationships
        WHERE source_id = ? AND valid_to IS NULL
        UNION ALL
        SELECT source_id, d.depth + 1, 'upstream'
        FROM iot.device_relationships r
        JOIN deps d ON r.target_id = d.related_id
        WHERE r.valid_to IS NULL AND d.depth < ?
    )
    SELECT related_id, MIN(depth) AS min_depth, direction
    FROM deps
    GROUP BY related_id, direction
    ORDER BY direction, min_depth
    """
    upstream: list[dict[str, Any]] = []
    downstream: list[dict[str, Any]] = []
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql(query, conn, params=[device_id, depth])
    except Exception as e:
        logger.warning("get_dependencies failed for %s: %s", device_id, e)
        return upstream, downstream

    for rec in _df_to_records(df):
        if rec.get("direction") == "upstream":
            upstream.append(rec)
        else:
            downstream.append(rec)
    return upstream, downstream


def get_offline_siblings(limit: int = 30) -> list[dict[str, Any]]:
    """All currently-offline devices — likely sharing a root cause with the target."""
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql(
                """
                SELECT TOP (?) device_id, device_type, vendor, model,
                       site_id, ip_address, status, last_seen
                FROM iot.devices
                WHERE status = 'offline'
                ORDER BY last_seen DESC
                """,
                conn,
                params=[limit],
            )
    except Exception as e:
        logger.warning("get_offline_siblings failed: %s", e)
        return []
    return _df_to_records(df)


def get_device(device_id: str) -> dict[str, Any] | None:
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql(
                "SELECT device_id, device_name, device_type, vendor, model, "
                "       site_id, ip_address, mac_address, status, last_seen "
                "FROM iot.devices WHERE device_id = ?",
                conn,
                params=[device_id],
            )
    except Exception as e:
        logger.warning("get_device failed for %s: %s", device_id, e)
        return None
    rows = _df_to_records(df)
    return rows[0] if rows else None


def get_site_summary() -> dict[str, Any]:
    """Lightweight topology summary so the model has a feel for the estate size."""
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM iot.devices) AS total,
                    (SELECT COUNT(*) FROM iot.devices WHERE status='online')  AS online,
                    (SELECT COUNT(*) FROM iot.devices WHERE status='offline') AS offline,
                    (SELECT COUNT(*) FROM iot.v_active_topology) AS edges
                """
            )
            row = cur.fetchone()
        return {
            "total_devices": row[0] or 0,
            "online": row[1] or 0,
            "offline": row[2] or 0,
            "edges": row[3] or 0,
        }
    except Exception as e:
        logger.warning("get_site_summary failed: %s", e)
        return {}


def build_context(device_id: str) -> dict[str, Any]:
    device = get_device(device_id) or {"device_id": device_id, "status": "offline"}
    recent = get_recent_events(device_id)
    upstream, downstream = get_dependencies(device_id)
    siblings = get_offline_siblings()
    site = get_site_summary()
    return {
        "device": device,
        "recent_events": recent,
        "upstream": upstream,
        "downstream": downstream,
        "offline_siblings": siblings,
        "site_context": site,
    }
