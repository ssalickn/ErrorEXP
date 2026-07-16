"""
Database trigger for AI-assisted RCA.

Watches `iot.device_logs` for new rows that look like real problems
(severity error/critical, or a status of 'offline' / 'down'). For each
new row, calls RCAAnalyzer and writes the result into `ai.rca_findings`.

The dashboard's WebSocket poller continues to fan events out to the UI;
this module runs in parallel and adds the AI verdict to each event.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .analyzer import Finding, KnowledgeBase, RCAAnalyzer
from .correlator import (
    Cascade,
    CascadeCorrelator,
    FailingDevice,
    TopologyGraph,
)
logger = logging.getLogger(__name__)

# Default KB lives at topology/ai/kb/ next to this file.
DEFAULT_KB_ROOT = Path(__file__).resolve().parent / "kb"

# SQL Server is the production target (matches backend/database.py).
# Postgres variant is provided for the schema.sql path.
SQL_SERVER_DDL = """
IF NOT EXISTS (
    SELECT 1 FROM sys.schemas WHERE name = 'ai'
) EXEC('CREATE SCHEMA ai');

IF NOT EXISTS (
    SELECT 1 FROM sys.tables WHERE name = 'rca_findings' AND schema_id = SCHEMA_ID('ai')
)
BEGIN
    CREATE TABLE ai.rca_findings (
        finding_id     BIGINT IDENTITY(1,1) PRIMARY KEY,
        log_id         BIGINT NOT NULL UNIQUE,
        device_id      NVARCHAR(128) NOT NULL,
        root_cause     NVARCHAR(MAX) NOT NULL,
        actions        NVARCHAR(MAX) NOT NULL,   -- JSON array
        confidence     FLOAT NOT NULL,
        citations      NVARCHAR(MAX) NULL,        -- JSON array
        raw            NVARCHAR(MAX) NULL,
        created_at     DATETIME2 DEFAULT SYSUTCDATETIME(),
        INDEX idx_rca_device (device_id),
        INDEX idx_rca_created (created_at DESC)
    );
END

IF NOT EXISTS (
    SELECT 1 FROM sys.tables WHERE name = 'cascades' AND schema_id = SCHEMA_ID('ai')
)
BEGIN
    CREATE TABLE ai.cascades (
        cascade_id            BIGINT IDENTITY(1,1) PRIMARY KEY,
        detected_at           DATETIME2 DEFAULT SYSUTCDATETIME(),
        window_start          DATETIME2 NOT NULL,
        window_end            DATETIME2 NOT NULL,
        cluster_size          INT NOT NULL,
        root_cause_device_id  NVARCHAR(128) NULL,
        root_cause_confidence FLOAT NULL,
        candidates            NVARCHAR(MAX) NULL,    -- JSON
        affected_device_ids   NVARCHAR(MAX) NULL,    -- JSON array
        explanation           NVARCHAR(MAX) NULL,
        contributing_log_ids  NVARCHAR(MAX) NULL,    -- JSON array
        INDEX idx_cascades_detected (detected_at DESC),
        INDEX idx_cascades_root     (root_cause_device_id)
    );
END
"""

POSTGRES_DDL = """
CREATE SCHEMA IF NOT EXISTS ai;
CREATE TABLE IF NOT EXISTS ai.rca_findings (
    finding_id   BIGSERIAL PRIMARY KEY,
    log_id       BIGINT NOT NULL UNIQUE,
    device_id    TEXT NOT NULL,
    root_cause   TEXT NOT NULL,
    actions      JSONB NOT NULL,
    confidence   DOUBLE PRECISION NOT NULL,
    citations    JSONB,
    raw          JSONB,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rca_device  ON ai.rca_findings (device_id);
CREATE INDEX IF NOT EXISTS idx_rca_created ON ai.rca_findings (created_at DESC);

CREATE TABLE IF NOT EXISTS ai.cascades (
    cascade_id            BIGSERIAL PRIMARY KEY,
    detected_at           TIMESTAMPTZ DEFAULT now(),
    window_start          TIMESTAMPTZ NOT NULL,
    window_end            TIMESTAMPTZ NOT NULL,
    cluster_size          INT NOT NULL,
    root_cause_device_id  TEXT,
    root_cause_confidence DOUBLE PRECISION,
    candidates            JSONB,
    affected_device_ids   JSONB,
    explanation           TEXT,
    contributing_log_ids  JSONB
);
CREATE INDEX IF NOT EXISTS idx_cascades_detected ON ai.cascades (detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_cascades_root     ON ai.cascades (root_cause_device_id);
"""


# --------------------------------------------------------------------------- #
# Trigger
# --------------------------------------------------------------------------- #

class RCATrigger:
    """Polls iot.device_logs and runs RCA on new 'problem' rows."""

    def __init__(
        self,
        analyzer: RCAAnalyzer,
        db_pool,                       # backend.database.pool (pyodbc pool)
        *,
        poll_interval_s: float = 3.0,
        severity_filter: tuple[str, ...] = ("error", "critical"),
        status_filter: tuple[str, ...] = ("offline", "down", "failed"),
        ensure_schema: bool = True,
        is_postgres: bool = False,
        correlator: Optional[CascadeCorrelator] = None,
        cluster_min: int = 3,
        cluster_window_minutes: int = 5,
        broadcast=None,                # optional async callable(message: dict)
    ):
        self.analyzer = analyzer
        self.pool = db_pool
        self.poll_interval_s = poll_interval_s
        self.severity_filter = {s.lower() for s in severity_filter}
        self.status_filter = {s.lower() for s in status_filter}
        self.is_postgres = is_postgres
        self._last_log_id: int = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.broadcast = broadcast

        # Correlation: use a shared correlator if provided, else build one.
        if correlator is not None:
            self.correlator = correlator
        else:
            graph = TopologyGraph([])
            self.correlator = CascadeCorrelator(
                graph,
                cluster_threshold=cluster_min,
                window_minutes=cluster_window_minutes,
            )
        self._graph_loaded = isinstance(correlator, CascadeCorrelator) and bool(
            correlator.graph.node_count
        )

        if ensure_schema:
            self._ensure_schema()

    # -- schema ------------------------------------------------------------- #

    def _ensure_schema(self) -> None:
        ddl = POSTGRES_DDL if self.is_postgres else SQL_SERVER_DDL
        try:
            with self.pool.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(ddl)
                conn.commit()
            logger.info("ai.rca_findings schema ensured.")
        except Exception as e:
            logger.error("Could not ensure ai.rca_findings schema: %s", e)

    # -- predicates --------------------------------------------------------- #

    def _is_problem(self, row: dict) -> bool:
        sev = (row.get("severity") or "").lower()
        status = (row.get("status") or "").lower()
        return sev in self.severity_filter or status in self.status_filter

    # -- DB I/O ------------------------------------------------------------- #

    def _fetch_new_events(self) -> list[dict]:
        """Return new rows from iot.device_logs since _last_log_id."""
        with self.pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT TOP 50
                    l.log_id, l.event_time, l.device_id, d.device_type,
                    l.severity, l.status_code, l.status, l.message, l.source_system
                FROM iot.device_logs l
                LEFT JOIN iot.devices d ON d.device_id = l.device_id
                WHERE l.log_id > ?
                ORDER BY l.log_id ASC
            """, self._last_log_id)
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        if rows:
            self._last_log_id = max(r["log_id"] for r in rows)
        return rows

    def _fetch_device(self, device_id: str) -> Optional[dict]:
        with self.pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT TOP 1
                    device_id, device_name, device_type, vendor, model,
                    site_id, status, ip_address, mac_address, last_seen
                FROM iot.devices
                WHERE device_id = ?
            """, device_id)
            row = cur.fetchone()
            if not row:
                return None
            cols = [c[0] for c in cur.description]
            return dict(zip(cols, row))

    def _persist_finding(self, finding: Finding) -> None:
        with self.pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                IF NOT EXISTS (SELECT 1 FROM ai.rca_findings WHERE log_id = ?)
                BEGIN
                    INSERT INTO ai.rca_findings
                        (log_id, device_id, root_cause, actions, confidence, citations, raw)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                END
            """,
                finding.log_id,
                finding.log_id, finding.device_id, finding.root_cause,
                json.dumps(finding.actions),
                finding.confidence,
                json.dumps(finding.citations),
                json.dumps(finding.raw or {}, default=str),
            )
            conn.commit()

    # -- correlation -------------------------------------------------------- #

    def _maybe_reload_graph(self) -> None:
        """Refresh the in-memory topology graph (cheap; do it every minute)."""
        if self._graph_loaded:
            return
        graph = CascadeCorrelator.load_graph(self.pool)
        # Replace the correlator's graph in place.
        self.correlator.graph = graph
        self._graph_loaded = graph.node_count > 0

    def _fetch_failing_cluster(
        self, now: datetime, window: timedelta
    ) -> list[FailingDevice]:
        """All currently-failing devices in the recent window.

        Aggregates `iot.device_logs` so that one device with 50 events
        still counts once. Joins `iot.devices` for the type.
        """
        sev_placeholders = ",".join("?" for _ in self.severity_filter) or "''"
        status_placeholders = ",".join("?" for _ in self.status_filter) or "''"
        cutoff = now - window

        sql = f"""
            SELECT
                l.device_id,
                MAX(d.device_type)        AS device_type,
                MIN(l.event_time)         AS first_seen,
                MAX(l.event_time)         AS last_seen,
                MAX(l.severity)           AS severity,
                MAX(l.status)             AS status,
                COUNT(*)                  AS event_count
            FROM iot.device_logs l
            LEFT JOIN iot.devices d ON d.device_id = l.device_id
            WHERE l.event_time >= ?
              AND (
                    LOWER(l.severity) IN ({sev_placeholders})
                 OR LOWER(COALESCE(l.status, '')) IN ({status_placeholders})
              )
            GROUP BY l.device_id
        """
        params: list = [cutoff]
        params.extend(self.severity_filter)
        params.extend(self.status_filter)

        with self.pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        out: list[FailingDevice] = []
        for r in rows:
            out.append(FailingDevice(
                device_id=str(r["device_id"]),
                device_type=r.get("device_type"),
                first_seen=r.get("first_seen"),
                last_seen=r.get("last_seen"),
                severity=str(r.get("severity") or "error"),
                status=r.get("status"),
                event_count=int(r.get("event_count") or 1),
            ))
        return out

    def _persist_cascade(self, cascade: Cascade, log_ids: list[int]) -> int:
        cascade.contributing_log_ids = log_ids
        with self.pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO ai.cascades
                    (window_start, window_end, cluster_size,
                     root_cause_device_id, root_cause_confidence,
                     candidates, affected_device_ids, explanation,
                     contributing_log_ids)
                OUTPUT INSERTED.cascade_id
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                cascade.window_start, cascade.window_end, cascade.cluster_size,
                cascade.root_cause_device_id, cascade.root_cause_confidence,
                json.dumps([asdict(c) for c in cascade.candidates], default=str),
                json.dumps(cascade.affected_device_ids),
                cascade.explanation,
                json.dumps(cascade.contributing_log_ids),
            )
            row = cur.fetchone()
            conn.commit()
            return int(row[0]) if row else 0

    async def _broadcast_cascade(self, cascade: Cascade) -> None:
        if not self.broadcast:
            return
        try:
            msg = {"type": "cascade", "data": cascade.to_dict(),
                   "timestamp": datetime.utcnow().isoformat()}
            await self.broadcast(msg)
        except Exception as e:
            logger.warning("Cascade broadcast failed: %s", e)

    def detect_cascade(self, *, now: Optional[datetime] = None) -> Optional[Cascade]:
        """One-shot: scan the recent window and return a Cascade if found."""
        self._maybe_reload_graph()
        now = now or datetime.utcnow()
        cluster = self._fetch_failing_cluster(now, self.correlator.window)
        if len(cluster) < self.correlator.cluster_threshold:
            return None
        cascade = self.correlator.evaluate(cluster, now=now)
        if not cascade or not cascade.root_cause_device_id:
            return None
        # Gather the log_ids that contributed.
        cutoff = now - self.correlator.window
        with self.pool.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT l.log_id
                FROM iot.device_logs l
                WHERE l.event_time >= ?
                  AND l.device_id = ?
            """, cutoff, cascade.root_cause_device_id)
            log_ids = [int(r[0]) for r in cur.fetchall()]
        cascade.cascade_id = self._persist_cascade(cascade, log_ids)
        logger.info(
            "Cascade detected: root=%s confidence=%.2f cluster=%d",
            cascade.root_cause_device_id, cascade.root_cause_confidence,
            cascade.cluster_size,
        )
        return cascade

    # -- main loop ---------------------------------------------------------- #

    def _run_once(self) -> int:
        new_events = self._fetch_new_events()
        analyzed = 0
        for ev in new_events:
            if not self._is_problem(ev):
                continue
            try:
                device = self._fetch_device(ev["device_id"])
                finding = self.analyzer.analyze(ev, device)
                self._persist_finding(finding)
                analyzed += 1
                logger.info(
                    "RCA log_id=%s device=%s confidence=%.2f cause=%s",
                    finding.log_id, finding.device_id, finding.confidence,
                    finding.root_cause[:80],
                )
            except Exception as e:
                logger.exception("RCA failed for log_id=%s: %s", ev.get("log_id"), e)

        # After per-event analysis, check whether we are inside a cascade.
        try:
            cascade = self.detect_cascade()
            if cascade and self.broadcast is not None:
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self._broadcast_cascade(cascade))
                except RuntimeError:
                    # No loop in this thread (e.g. CLI mode); skip broadcast.
                    pass
        except Exception:
            logger.exception("Cascade detection failed")

        return analyzed

    def start(self) -> None:
        """Run the poller in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="rca-trigger", daemon=True
        )
        self._thread.start()
        logger.info("RCA trigger started.")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_once()
            except Exception:
                logger.exception("RCA loop iteration failed")
            self._stop.wait(self.poll_interval_s)

    # -- one-shot helper ---------------------------------------------------- #

    def run_until_drained(self, max_iterations: int = 10) -> int:
        """For tests / batch backfill: drain whatever is pending, then stop."""
        total = 0
        for _ in range(max_iterations):
            n = self._run_once()
            total += n
            if n == 0:
                break
        return total
