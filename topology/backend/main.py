"""
FastAPI application entry point.
Serves API + WebSocket + static frontend.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from pathlib import Path

from backend.api import devices, topology, events, kpis, ai as ai_api
from backend.websocket.manager import ws_manager
from backend.websocket.poller import start_background_tasks, initialize_state

# AI-assisted RCA (optional, starts only if the package is available).
# Uses an external LLM (e.g. GPT-5) via the OpenAI-compatible API.
# Configure with env vars: LLM_PROVIDER, LLM_MODEL, LLM_API_KEY, LLM_BASE_URL.
try:
    from ai import (
        CascadeCorrelator,
        KnowledgeBase,
        RCAAnalyzer,
        RCATrigger,
        get_default_client,
    )
    from backend.database import pool as _db_pool
    _ai_llm = get_default_client()
    # Pre-load the topology graph so the correlator has data on the
    # first iteration instead of only after the first poll.
    _ai_graph = CascadeCorrelator.load_graph(_db_pool)
    _ai_correlator = CascadeCorrelator(_ai_graph, cluster_threshold=3, window_minutes=5)
    _ai_trigger = RCATrigger(
        RCAAnalyzer(
            KnowledgeBase(Path(__file__).resolve().parent.parent / "ai" / "kb"),
            llm=_ai_llm,
        ),
        _db_pool,
        correlator=_ai_correlator,
        broadcast=ws_manager.broadcast,
    )
    logging.getLogger(__name__).info(
        "AI RCA enabled (provider=%s, model=%s).",
        type(_ai_llm).__name__, getattr(_ai_llm, "model", "?"),
    )
except Exception as _ai_err:  # pragma: no cover
    logging.getLogger(__name__).warning("AI RCA disabled: %s", _ai_err)
    _ai_trigger = None

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Lifespan (startup/shutdown)
# ═══════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up...")
    initialize_state()
    await start_background_tasks()
    if _ai_trigger is not None:
        _ai_trigger.start()
    yield
    # Shutdown
    if _ai_trigger is not None:
        _ai_trigger.stop()
    logger.info("Shutting down...")


# ═══════════════════════════════════════════════════════════
# App
# ═══════════════════════════════════════════════════════════

app = FastAPI(
    title="IoT Topology Monitor API",
    version="1.0.0",
    description="Real-time IoT device monitoring and topology discovery",
    lifespan=lifespan,
)

# API routes
app.include_router(devices.router)
app.include_router(topology.router)
app.include_router(events.router)
app.include_router(kpis.router)
app.include_router(ai_api.router)


# ═══════════════════════════════════════════════════════════
# WebSocket endpoint
# ═══════════════════════════════════════════════════════════

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # Send initial state
        await ws_manager.send_to(websocket, {
            "type": "connected",
            "data": {"message": "Connected to IoT Topology Monitor"},
            "timestamp": websocket.headers.get("date", ""),
        })
        
        # Keep connection alive, handle client messages
        while True:
            data = await websocket.receive_text()
            # Echo back or handle commands
            if data == "ping":
                await ws_manager.send_to(websocket, {"type": "pong"})
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)


# ═══════════════════════════════════════════════════════════
# Health check
# ═══════════════════════════════════════════════════════════

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "websocket_clients": ws_manager.client_count,
    }


# ═══════════════════════════════════════════════════════════
# Static frontend
# ═══════════════════════════════════════════════════════════

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
async def root():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "IoT Topology Monitor API. See /docs for endpoints."}
