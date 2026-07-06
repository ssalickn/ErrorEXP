"""KPI summary endpoints."""

from fastapi import APIRouter
from backend.database import pool
from backend.models import KPIs

router = APIRouter(prefix="/api/kpis", tags=["kpis"])


@router.get("", response_model=KPIs)
def get_kpis():
    """Get system-wide KPIs."""
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
            
            return KPIs(
                total_devices=row[0] or 0,
                online=row[1] or 0,
                offline=row[2] or 0,
                degraded=row[3] or 0,
                total_edges=row[4] or 0,
                critical_events_24h=row[5] or 0,
            )
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))
