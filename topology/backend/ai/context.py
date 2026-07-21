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
import ipaddress

logger = logging.getLogger(__name__)

def _subnet_of(ip: str | None, prefix_len: int = 24) -> str | None:
    """Return '10.36.9.0/24' style subnet for an IP. Returns None if invalid."""
    if not ip:
        return None
    try:
        iface = ipaddress.ip_interface(f"{ip}/255.255.255.0")
        network = ipaddress.ip_network(f"{iface.ip}/{prefix_len}", strict=False)
        return str(network)
    except (ValueError, TypeError):
        return None


def get_subnet_summary() -> dict[str, Any]:
    """Group currently-offline devices by their /24 subnet.

    Returns:
        {
            "by_subnet": [
                {
                    "subnet": "10.36.9.0/24",
                    "total": 32,           # devices with IPs in this subnet
                    "offline": 8,
                    "offline_device_ids": ["NI-MPNVR-03-CH09", ...],
                    "cascade_suspected": True  # >=50% offline
                }
            ],
            "subnets_with_isolated_offline": ["10.36.10.0/24"],  # exactly 1 offline
        }
    """
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql("""
                SELECT
                    device_id,
                    device_name,
                    device_type,
                    ip_address,
                    status
                FROM iot.devices
                WHERE ip_address IS NOT NULL AND ip_address <> ''
            """, conn)
    except Exception as e:
        logger.warning("get_subnet_summary failed: %s", e)
        return {"by_subnet": [], "subnets_with_isolated_offline": []}

    if df.empty:
        return {"by_subnet": [], "subnets_with_isolated_offline": []}

    df["subnet"] = df["ip_address"].apply(_subnet_of)
    df = df.dropna(subset=["subnet"])

    by_subnet = []
    isolated_subnets = []
    for subnet, sub in df.groupby("subnet", dropna=False):
        total = int(len(sub))
        offline_mask = sub["status"].fillna("").str.lower() == "offline"
        offline_n = int(offline_mask.sum())
        offline_ids = sub.loc[offline_mask, "device_id"].tolist()
        cascade = offline_n >= max(2, total * 0.5)

        by_subnet.append({
            "subnet": subnet,
            "total": total,
            "offline": offline_n,
            "offline_device_ids": offline_ids,
            "cascade_suspected": cascade,
        })

        if offline_n == 1 and total >= 5:
            isolated_subnets.append(subnet)

    by_subnet.sort(key=lambda r: (-r["cascade_suspected"], -r["offline"], r["subnet"]))
    return {"by_subnet": by_subnet, "subnets_with_isolated_offline": isolated_subnets}
    
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
    """Returns (upstream, downstream) using a recursive CTE.

    Note: SQL Server is strict about UNION types in recursive CTEs. Both
    the anchor and recursive parts MUST cast string literals to the same
    type/length, otherwise you get:
        "Types don't match between the anchor and the recursive part in column 'direction'"
    """
    query = """
    WITH deps AS (
        SELECT
            CAST(target_id AS NVARCHAR(128)) AS related_id,
            1 AS depth,
            CAST('downstream' AS NVARCHAR(16)) AS direction
        FROM iot.device_relationships
        WHERE source_id = ? AND valid_to IS NULL
        UNION ALL
        SELECT
            CAST(r.source_id AS NVARCHAR(128)),
            d.depth + 1,
            CAST('upstream' AS NVARCHAR(16))
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
    # Normalize device_type to the closed set before handing to the LLM
    if device.get("device_type"):
        device["device_type"] = _normalize_device_type_inplace(device["device_type"])
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


def _normalize_device_type_inplace(t):
    """Apply closed-set aliases to a single device_type string."""
    aliases = {
        "biostar_reader": "biometric_reader",
        "biostar_panel": "access_control_panel",
        "biostar_ap": "access_point",
        "honeywell_camera": "camera",
        "hikvision_nvr": "nvr",
        "genetec_nvr": "nvr",
        "avigilon_nvr": "nvr",
        "cisco_cat": "cisco_switch",
        "cisco_nexus": "cisco_switch",
        "switch": "cisco_switch",
    }
    if not t:
        return t
    return aliases.get(str(t).strip().lower(), str(t).strip().lower())
