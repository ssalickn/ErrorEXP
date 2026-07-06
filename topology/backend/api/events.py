"""Event log endpoints."""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from datetime import datetime
import pandas as pd
from backend.database import pool
from backend.models import LogEvent

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=List[LogEvent])
def list_events(
    severity: Optional[str] = None,
    device_id: Optional[str] = None,
    hours_back: int = Query(default=24, le=8760),
    limit: int = Query(default=100, le=10000),
):
    """Get recent events with optional filters."""
    query = f"""
        SELECT TOP ({limit})
            l.log_id, l.event_time, l.device_id, d.device_type,
            l.severity, l.status_code, l.status, l.message, l.source_system
        FROM iot.device_logs l
        LEFT JOIN iot.devices d ON d.device_id = l.device_id
        WHERE l.event_time >= DATEADD(HOUR, -?, SYSUTCDATETIME())
    """
    params = [hours_back]
    
    if severity:
        query += " AND l.severity = ?"
        params.append(severity)
    if device_id:
        query += " AND l.device_id = ?"
        params.append(device_id)
    
    query += " ORDER BY l.event_time DESC"
    
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql(query, conn, params=params)
        return df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cascades")
def get_cascades(hours_back: int = Query(default=24, le=8760), 
                 window_minutes: int = Query(default=5, le=60)):
    """
    Detect cascade patterns: devices that fail close together.
    Returns pairs of devices that failed within window_minutes of each other.
    """
    query = f"""
    SELECT 
        l1.device_id AS first_device,
        l1.event_time AS first_at,
        l2.device_id AS second_device,
        l2.event_time AS second_at,
        DATEDIFF(SECOND, l1.event_time, l2.event_time) AS seconds_apart
    FROM iot.device_logs l1
    INNER JOIN iot.device_logs l2 
        ON l1.device_id <> l2.device_id
        AND l2.event_time > l1.event_time
        AND l2.event_time <= DATEADD(MINUTE, ?, l1.event_time)
    WHERE l1.severity IN ('critical', 'error')
      AND l2.severity IN ('critical', 'error')
      AND l1.event_time >= DATEADD(HOUR, -?, SYSUTCDATETIME())
    ORDER BY l1.event_time DESC
    """
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql(query, conn, params=[window_minutes, hours_back])
        return df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
