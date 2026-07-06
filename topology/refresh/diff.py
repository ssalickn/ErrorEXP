"""Continuous refresh: diff current discovery results against the live graph
and emit drift alerts.

Steps:
  1. Re-run the most recent discovery (or accept a snapshot of new edges).
  2. Compare the discovered set with the persisted edges in Postgres.
  3. Record additions, removals, and confidence drops in topology.drift_log.
  4. Send a webhook summary if configured.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx
import sqlalchemy as sa

from topology.config import load_settings

log = logging.getLogger("topology.refresh.diff")


@dataclass
class Drift:
    kind: str                # edge_added | edge_removed | device_added | device_removed | confidence_dropped
    subject: str
    payload: dict


def _pg_engine():
    s = load_settings()
    return sa.create_engine(
        f"postgresql+psycopg2://{s.postgres.user}:{s.postgres.password}@"
        f"{s.postgres.host}:{s.postgres.port}/{s.postgres.database}",
        future=True,
    )


def _current_edges() -> dict[tuple, dict]:
    eng = _pg_engine()
    with eng.connect() as cx:
        rows = cx.execute(sa.text("""
            SELECT src_device_id, dst_device_id, rel_type, source, confidence
            FROM topology.edges
        """)).fetchall()
    return {(r[0], r[1], r[2], r[3]): {"confidence": float(r[4] or 0)} for r in rows}


def _record_drift(drifts: Iterable[Drift]) -> int:
    eng = _pg_engine()
    payload = [{"change_kind": d.kind, "subject": d.subject,
                "payload": json.dumps(d.payload)} for d in drifts]
    if not payload:
        return 0
    with eng.begin() as cx:
        cx.execute(sa.text("""
            INSERT INTO topology.drift_log (change_kind, subject, payload)
            VALUES (:change_kind, :subject, CAST(:payload AS JSONB))
        """), payload)
    return len(payload)


def _post_webhook(url: str, summary: dict) -> None:
    if not url:
        return
    try:
        httpx.post(url, json=summary, timeout=5.0)
    except Exception as e:
        log.warning("drift webhook failed: %s", e)


def diff(snapshot_edges: list[dict], *, drift_threshold: float = 0.2) -> list[Drift]:
    """Compare the discovery snapshot against the persisted edges.

    `snapshot_edges` items: {src, dst, rel, source, confidence, inferred, properties}
    """
    current = _current_edges()
    snapshot = {(e["src"], e["dst"], e["rel"], e["source"]):
                {"confidence": float(e.get("confidence", 0))} for e in snapshot_edges}
    drifts: list[Drift] = []

    added = set(snapshot) - set(current)
    removed = set(current) - set(snapshot)
    for k in added:
        drifts.append(Drift("edge_added", f"{k[2]}:{k[0]}->{k[1]}",
                            {"src": k[0], "dst": k[1], "rel": k[2],
                             "source": k[3], "confidence": snapshot[k]["confidence"]}))
    for k in removed:
        drifts.append(Drift("edge_removed", f"{k[2]}:{k[0]}->{k[1]}",
                            {"src": k[0], "dst": k[1], "rel": k[2], "source": k[3]}))

    for k in current.keys() & snapshot.keys():
        delta = current[k]["confidence"] - snapshot[k]["confidence"]
        if delta > drift_threshold:
            drifts.append(Drift("confidence_dropped", f"{k[2]}:{k[0]}->{k[1]}",
                                {"src": k[0], "dst": k[1], "rel": k[2],
                                 "old": current[k]["confidence"],
                                 "new": snapshot[k]["confidence"]}))
    return drifts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, required=True, help="NDJSON of latest edges")
    ap.add_argument("--alert", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    snap = [json.loads(line) for line in args.snapshot.read_text().splitlines() if line.strip()]
    drifts = diff(snap)
    n = _record_drift(drifts)
    log.info("recorded %d drift events", n)
    if args.alert:
        cfg = load_settings()
        _post_webhook(cfg.refresh.drift_alert_webhook,
                      {"drift_count": n, "summary": [{"kind": d.kind, "subject": d.subject} for d in drifts[:50]]})


if __name__ == "__main__":
    main()
