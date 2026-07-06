"""
WebSocket connection manager.
Tracks all connected clients and broadcasts messages.
"""

import asyncio
import json
import logging
from typing import Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSManager:
    """Manages WebSocket connections and broadcasts."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        logger.info(f"Client connected. Total: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self.active_connections.discard(websocket)
        logger.info(f"Client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Send message to all connected clients."""
        if not self.active_connections:
            return
        
        text = json.dumps(message, default=str)
        disconnected = []
        
        for connection in list(self.active_connections):
            try:
                await asyncio.wait_for(connection.send_text(text), timeout=5.0)
            except Exception as e:
                logger.warning(f"Send failed, marking disconnected: {e}")
                disconnected.append(connection)
        
        # Clean up dead connections
        for conn in disconnected:
            await self.disconnect(conn)

    async def send_to(self, websocket: WebSocket, message: dict):
        """Send message to specific client."""
        try:
            await websocket.send_text(json.dumps(message, default=str))
        except Exception as e:
            logger.warning(f"Send to client failed: {e}")

    @property
    def client_count(self) -> int:
        return len(self.active_connections)


# Global manager instance
ws_manager = WSManager()
