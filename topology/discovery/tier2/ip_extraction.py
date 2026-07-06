"""Tier 2C: Extract IP↔device relationships from vendor log fields.

Vendor logs frequently contain source/destination IP fields. This module
joins those to the canonical device inventory to produce
`STREAMS_TO`, `MANAGES`, `AUTHENTICATES_VIA` edges.

Inputs:
  - Parquet/JSONL of events: [ts, src_ip, dst_ip, src_port?, dst_port?, proto?, vendor, msg]
  - topology.devices (Postgres)
Output:
  - JSONL of edges
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Iterable

import sqlalchemy as sa
from topology.config import load_settings

log = logging.getLogger("topology.discovery.tier2.ip_extraction")


# Vendor → port mapping
VMS_PORTS     = {554, 8000, 8200, 9000, 11000}        # RTSP, ONVIF, Milestone, Genetec, Avigilon
WLC_PORTS     = {5246, 5247}                          # CAPWAP data/control
BIOC_PORTS    = {1470, 4430}                          # BioStar heartbeat/auth
CISCO_PORTS   = {22, 23, 161, 162}                    # SSH/Telnet/SNMP


def _engine():
    s = load_settings()
    url = f"postgresql+psycopg2://{s.postgres.user}:{s.postgres.password}@{s.postgres.host}:{s.postgres.port}/{s.postgres.database}"
    return sa.create_engine(url, future=True)


def _ip_to_devices() -> dict[str, list[dict]]:
    """Map IP → list of canonical device records."""
    eng = _engine()
    rows = []
    with eng.connect() as cx:
        for d in cx.execute(sa.text("SELECT device_id, device_type, ip_address FROM topology.devices")).fetchall():
            if d.ip_address:
                rows.append({"device_id": d.device_id, "device_type": d.device_type, "ip": str(d.ip_address)})
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["ip"], []).append({"device_id": r["device_id"], "device_type": r["device_type"]})
    return out


def classify(src_type: str, dst_type: str, dst_port: int | None) -> tuple[str, dict]:
    """Pick the most-specific relationship type given device types + port."""
    if dst_port in VMS_PORTS and (src_type == "camera" or dst_type in ("nvr", "vms")):
        return "STREAMS_TO", {"protocol": "rtsp" if dst_port == 554 else "tcp"}
    if dst_port in WLC_PORTS or (src_type == "cisco_ap" and dst_type == "cisco_wlc"):
        return "MANAGES", {"protocol": "capwap"}
    if dst_port in BIOC_PORTS or (src_type == "biostar_door" and dst_type == "biostar_server"):
        return "MANAGES", {"protocol": "biostar"}
    if dst_port in CISCO_PORTS:
        return "MANAGES", {"protocol": "snmp" if dst_port in (161, 162) else "ssh"}
    if src_type in ("biostar_door", "honeywell_panel") and dst_type.endswith("_server"):
        return "MANAGES", {"protocol": "vendor"}
    return "DEPENDS_ON", {}


def extract(events: Iterable[dict], *, min_observations: int = 3) -> list[dict]:
    ip_index = _ip_to_devices()
    pair_counts: dict[tuple, dict] = {}

    for ev in events:
        src_ip = ev.get("src_ip")
        dst_ip = ev.get("dst_ip")
        dst_port = ev.get("dst_port")
        if not (src_ip and dst_ip):
            continue
        src_devs = ip_index.get(src_ip) or [{"device_id": src_ip, "device_type": "unknown"}]
        dst_devs = ip_index.get(dst_ip) or [{"device_id": dst_ip, "device_type": "unknown"}]
        for s in src_devs:
            for d in dst_devs:
                rel, props = classify(s["device_type"], d["device_type"], dst_port)
                key = (s["device_id"], d["device_id"], rel)
                rec = pair_counts.setdefault(key, {"count": 0, "props": props})
                rec["count"] += 1
                # Carry the last observed timestamp
                rec["last_seen"] = ev.get("ts") or rec.get("last_seen")

    out = []
    for (src, dst, rel), info in pair_counts.items():
        if info["count"] < min_observations:
            continue
        out.append({
            "src": src, "dst": dst, "rel": rel,
            "confidence": min(0.9, 0.4 + 0.05 * min(10, info["count"])),
            "source": "inferred_ip_log",
            "inferred": True,
            "properties": {**info["props"], "observed_count": info["count"], "last_seen": info.get("last_seen")},
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True, help="NDJSON path with events")
    ap.add_argument("--out", required=True, help="NDJSON output path")
    ap.add_argument("--min-obs", type=int, default=3)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with args.events.open() as f:
        events = (json.loads(line) for line in f if line.strip())
    edges = extract(events, min_observations=args.min_obs)
    with args.out.open("w") as f:
        for e in edges:
            f.write(json.dumps(e) + "\n")
    log.info("wrote %d inferred IP-log edges to %s", len(edges), args.out)


if __name__ == "__main__":
    main()
