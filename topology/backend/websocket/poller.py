"""
Background task that polls the database for new events
and broadcasts them via WebSocket.
"""

import asyncio
import logging
from datetime import datetime, timedelta
import pandas as pd
from backend.database import pool
from backend.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

# Track last seen event to detect new ones
_last_event_id: int = 0
_last_kpi_check: datetime = datetime.utcnow()


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
