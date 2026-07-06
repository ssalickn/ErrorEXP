"""
Device endpoints.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
import pandas as pd
from backend.database import pool
from backend.models import Device

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("", response_model=List[Device])
def list_devices(
    site: Optional[str] = None,
    device_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=1000, le=10000),
):
    """List all devices with optional filters."""
    query = "SELECT * FROM iot.devices WHERE 1=1"
    params = []
    
    if site:
        query += " AND site_id = ?"
        params.append(site)
    if device_type:
        query += " AND device_type = ?"
        params.append(device_type)
    if status:
        query += " AND status = ?"
        params.append(status)
    
    query += " ORDER BY device_id"
    
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql(query, conn, params=params if params else None)
        return df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{device_id}", response_model=Device)
def get_device(device_id: str):
    """Get a specific device by ID."""
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql(
                "SELECT * FROM iot.devices WHERE device_id = ?",
                conn, params=[device_id]
            )
        if df.empty:
            raise HTTPException(status_code=404, detail="Device not found")
        return df.iloc[0].to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{device_id}/events")
def get_device_events(device_id: str, limit: int = Query(default=100, le=1000)):
    """Get recent events for a specific device."""
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql("""
                SELECT TOP (?) log_id, event_time, severity, status_code,
                       status, message, source_system
                FROM iot.device_logs
                WHERE device_id = ?
                ORDER BY event_time DESC
            """, conn, params=[limit, device_id])
        return df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
