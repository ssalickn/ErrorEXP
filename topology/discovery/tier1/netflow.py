"""NetFlow / IPFIX v9/v10 parser stub.

Real-world deployments typically use goflow2, softflowd, or nfdump. This module
provides a thin wrapper around `pyflow` if available, otherwise a noop with
documented integration points. The output is a stream of (src_ip, dst_ip,
src_port, dst_port, protocol, timestamp) tuples that feed the IP↔device edge
builder in `tier2.ip_extraction`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

log = logging.getLogger("topology.discovery.tier1.netflow")


@dataclass
class FlowRecord:
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    proto: int
    timestamp: float
    bytes_: int = 0
    packets: int = 0


def from_nfdump_file(path: Path) -> Iterable[FlowRecord]:
    """Parse an nfdump binary export (text output of `nfdump -r file -o csv`).

    CSV columns: ts,te,td,sa,da,sp,dp,pr,flg,packets,bytes,...
    """
    import csv
    if not path.exists():
        log.warning("nfdump file not found: %s", path)
        return
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                yield FlowRecord(
                    src_ip=row["sa"], dst_ip=row["da"],
                    src_port=int(row["sp"]), dst_port=int(row["dp"]),
                    proto=int(row["pr"], 0),
                    timestamp=float(row["ts"]),
                    bytes_=int(row.get("bytes", 0) or 0),
                    packets=int(row.get("packets", 0) or 0),
                )
            except (KeyError, ValueError) as e:
                log.debug("Skipping malformed nfdump row: %s (%s)", row, e)


def from_live_collector(addr: str = "0.0.0.0", port: int = 9995) -> Iterable[FlowRecord]:
    """Hook for goflow2-compatible UDP JSON collector.

    Expected JSON:
    {"type":"FLOW","time":...,"sampler_address":"...","sampler_port":...,
     "sequence_num":...,"sampling_interval":...,"flow_records":[
        {"type":"FLOW_SRECORD","time":...,"sampling_interval":...,
         "uptime":..., "src_ip":"...","dst_ip":"...","src_port":...,
         "dst_port":...,"protocol":...,"bytes":...,"packets":...}, ...
     ]}
    """
    import json
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((addr, port))
    log.info("listening for goflow2 JSON on udp://%s:%d", addr, port)
    while True:
        data, _ = sock.recvfrom(65535)
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        for fr in obj.get("flow_records", []):
            yield FlowRecord(
                src_ip=fr.get("src_ip", ""), dst_ip=fr.get("dst_ip", ""),
                src_port=int(fr.get("src_port", 0)),
                dst_port=int(fr.get("dst_port", 0)),
                proto=int(fr.get("protocol", 0)),
                timestamp=float(fr.get("time", time.time())),
                bytes_=int(fr.get("bytes", 0) or 0),
                packets=int(fr.get("packets", 0) or 0),
            )
