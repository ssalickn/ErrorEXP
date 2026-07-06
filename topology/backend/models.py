"""
Pydantic models for API request/response validation.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class Device(BaseModel):
    device_id: str
    device_name: Optional[str] = None
    device_type: str
    vendor: Optional[str] = None
    model: Optional[str] = None
    site_id: Optional[str] = None
    status: str = "unknown"
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    last_seen: Optional[datetime] = None


class TopologyEdge(BaseModel):
    source_id: str
    target_id: str
    relationship_type: str
    source_port: Optional[str] = None
    target_port: Optional[str] = None
    confidence: float = 1.0
    source: Optional[str] = None


class LogEvent(BaseModel):
    log_id: int
    event_time: datetime
    device_id: str
    device_type: Optional[str] = None
    severity: str
    status_code: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None
    source_system: str


class KPIs(BaseModel):
    total_devices: int
    online: int
    offline: int
    degraded: int
    total_edges: int
    critical_events_24h: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class WSMessage(BaseModel):
    """WebSocket message envelope."""
    type: str  # "event", "device_update", "kpi_update"
    data: dict
    timestamp: datetime = Field(default_factory=datetime.utcnow)
