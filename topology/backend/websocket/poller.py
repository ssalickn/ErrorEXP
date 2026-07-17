"""
Background task that polls the database for new events
and broadcasts them via WebSocket. Also triggers Microsoft Foundry
root-cause analysis when a device goes offline.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from threading import Lock
import pandas as pd
from backend.database import pool
from backend.websocket.manager import ws_manager
from backend.ai.foundry_client import get_client as get_foundry_client
from backend.ai.context import build_context
from backend.api.ai import _persist_insight

logger = logging.getLogger(__name__)

# Track last seen event to detect new ones
_last_event_id: int = 0
_last_kpi_check: datetime = datetime.utcnow()

# Per-device cooldown so we don't call the model for every redundant event
_cooldown_lock = Lock()
_last_analyzed_at: dict[str, float] = {}
AUTO_ANALYZE = os.environ.get("FOUNDRY_AUTO_ANALYZE", "true").lower() in ("1", "true", "yes")
COOLDOWN_S = int(os.environ.get("FOUNDRY_ANALYZE_COOLDOWN_MIN", "15")) * 60
SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}
MIN_SEVERITY = os.environ.get("FOUNDRY_MIN_SEVERITY", "warning")
MIN_SEVERITY_RANK = SEVERITY_RANK.get(MIN_SEVERITY.lower(), 1)


def _should_analyze(device_id: str, status: str | None, severity: str | None) -> bool:
    if not AUTO_ANALYZE:
        return False
    if not device_id:
        return False
    # Only trigger on actual offline-ish transitions
    s = (status or "").lower()
    sev = (severity or "").lower()
    is_offline_event = s in ("offline", "down", "unreachable") or sev in ("error", "critical")
    if not is_offline_event:
        return False
    if SEVERITY_RANK.get(sev, 0) < MIN_SEVERITY_RANK and s != "offline":
        # allow offline status to trigger even if event severity is "info"
        return False
    with _cooldown_lock:
        last = _last_analyzed_at.get(device_id, 0.0)
        if (time.time() - last) < COOLDOWN_S:
            return False
        _last_analyzed_at[device_id] = time.time()
    return True


async def _run_ai_analysis(device_id: str, trigger_event: dict | None = None) -> None:
    """Build context, call Foundry, persist, and broadcast the insight."""
    try:
        loop = asyncio.get_running_loop()
        ctx = await loop.run_in_executor(None, build_context, device_id)
        device = ctx.get("device") or {"device_id": device_id, "status": "offline"}
        client = get_foundry_client()
        result = await loop.run_in_executor(
            None,
            lambda: client.analyze(
                device=device,
                recent_events=ctx.get("recent_events", []),
                upstream=ctx.get("upstream", []),
                downstream=ctx.get("downstream", []),
                offline_siblings=ctx.get("offline_siblings", []),
                site_context=ctx.get("site_context", {}),
            ),
        )
        result["model"] = client.model_deployment
        stored = await loop.run_in_executor(None, _persist_insight, device, result)
        if stored is None:
            # Fall back: synthesize a broadcast payload even if persistence failed
            stored = {
                "device_id": device_id,
                "ok": result.get("ok", False),
                "summary": result.get("summary"),
                "root_cause_device_id": result.get("root_cause_device_id"),
                "root_cause_device_type": result.get("root_cause_device_type"),
                "confidence": result.get("confidence"),
                "severity": result.get("severity"),
                "blast_radius": result.get("blast_radius", []),
                "recommended_actions": result.get("recommended_actions", []),
                "rationale": result.get("rationale"),
                "error": result.get("error"),
                "model": client.model_deployment,
                "elapsed_s": result.get("elapsed_s"),
            }
        await ws_manager.broadcast({
            "type": "ai_insight",
            "data": stored,
            "timestamp": datetime.utcnow().isoformat(),
        })
        logger.info(
            "AI insight for %s: ok=%s confidence=%.2f root_cause=%s",
            device_id, stored.get("ok"), stored.get("confidence") or 0.0,
            stored.get("root_cause_device_id"),
        )
    except Exception as e:
        logger.error("AI analysis failed for %s: %s", device_id, e)


async def poll_for_new_events():
    """Poll database for new events. Broadcasts via WebSocket."""
    global _last_event_id
    
    while True:
        try:
            with pool.get_connection() as conn:
                cur = conn.cursor()
                
                # New events since last poll
                cur.execute("""
                    SELECT TOP 50
                        l.log_id, l.event_time, l.device_id, d.device_type,
                        l.severity, l.status_code, l.status,
                        l.message, l.source_system
                    FROM iot.device_logs l
                    LEFT JOIN iot.devices d ON d.device_id = l.device_id
                    WHERE l.log_id > ?
                    ORDER BY l.log_id DESC
                """, _last_event_id)
                
                rows = cur.fetchall()
                
                if rows:
                    new_max_id = max(row[0] for row in rows)

                    # Broadcast each new event
                    for row in rows:
                        event = {
                            "type": "event",
                            "data": {
                                "log_id": row[0],
                                "event_time": row[1].isoformat() if row[1] else None,
                                "device_id": row[2],
                                "device_type": row[3],
                                "severity": row[4],
                                "status_code": row[5],
                                "status": row[6],
                                "message": row[7],
                                "source_system": row[8],
                            },
                            "timestamp": datetime.utcnow().isoformat(),
                        }
                        await ws_manager.broadcast(event)

                        # Kick off AI RCA in the background when a device goes offline
                        if _should_analyze(row[2], row[6], row[4]):
                            asyncio.create_task(
                                _run_ai_analysis(row[2], {"log_id": row[0], "severity": row[4]})
                            )

                    _last_event_id = new_max_id
                    logger.info(f"Broadcast {len(rows)} new events. Last ID: {_last_event_id}")
        
        except Exception as e:
            logger.error(f"Poller error: {e}")
        
        await asyncio.sleep(2)  # Poll every 2 seconds


async def poll_for_kpis():
    """Poll KPIs less frequently (every 10 seconds)."""
    while True:
        try:
            with pool.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT
                        (SELECT COUNT(*) FROM iot.devices) AS total_devices,
                        (SELECT COUNT(*) FROM iot.devices WHERE status = 'online') AS online,
                        (SELECT COUNT(*) FROM iot.devices WHERE status = 'offline') AS offline,
                        (SELECT COUNT(*) FROM iot.devices WHERE status = 'degraded') AS degraded,
                        (SELECT COUNT(*) FROM iot.v_active_topology) AS total_edges,
                        (SELECT COUNT(*) FROM iot.device_logs
                         WHERE event_time >= DATEADD(HOUR, -24, SYSUTCDATETIME())
                           AND severity IN ('error', 'critical')) AS critical_events
                """)
                row = cur.fetchone()
                
                kpi_msg = {
                    "type": "kpi_update",
                    "data": {
                        "total_devices": row[0] or 0,
                        "online": row[1] or 0,
                        "offline": row[2] or 0,
                        "degraded": row[3] or 0,
                        "total_edges": row[4] or 0,
                        "critical_events_24h": row[5] or 0,
                    },
                    "timestamp": datetime.utcnow().isoformat(),
                }
                await ws_manager.broadcast(kpi_msg)
        
        except Exception as e:
            logger.error(f"KPI poller error: {e}")
        
        await asyncio.sleep(10)


async def start_background_tasks():
    """Start all background polling tasks."""
    asyncio.create_task(poll_for_new_events())
    asyncio.create_task(poll_for_kpis())
    logger.info("Background pollers started")


def initialize_state():
    """Initialize _last_event_id from database on startup."""
    global _last_event_id
    try:
        with pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT ISNULL(MAX(log_id), 0) FROM iot.device_logs")
            _last_event_id = cur.fetchone()[0]
            logger.info(f"Initialized _last_event_id to {_last_event_id}")
    except Exception as e:
        logger.error(f"Failed to initialize state: {e}")
