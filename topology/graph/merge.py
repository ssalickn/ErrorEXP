"""Graph merge driver.

Reads Tier 1 (active probing) and Tier 2 (inferred) results, normalizes
to the canonical Edge schema, and runs the confidence + conflict resolution
pipeline before pushing to Neo4j.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from topology.graph.confidence import Edge, edge_confidence, upsert, resolve_conflicts
# Tier 2 readers are imported lazily by the loader functions in this module

log = logging.getLogger("topology.graph.merge")


def _from_tier1_snmp(path: Path) -> list[Edge]:
    raw = json.loads(path.read_text())
    out: list[Edge] = []
    for n in raw.get("neighbors", []):
        if not n.get("remote_device_id"):
            continue
        out.append(Edge(
            src=n["local_device_id"],
            dst=n["remote_device_id"],
            rel="CONNECTS_TO",
            confidence=edge_confidence(active_probe=True, lift=None, sequence_support=None, source_count=None),
            source=f"active_{n['protocol']}",
            inferred=False,
            properties={
                "local_port": n.get("local_port"),
                "remote_port": n.get("remote_port"),
                "remote_ip": n.get("remote_ip"),
                "remote_mac": n.get("remote_mac"),
            },
        ))
    return out


def _from_tier1_onvif(path: Path) -> list[Edge]:
    # ONVIF alone doesn't produce a topology edge; cameras are added to the
    # inventory and downstream VMS logs will link them. The NVR↔VMS relationship
    # is captured by IP/log inference.
    return []


def _from_tier1_biostar(path: Path) -> list[Edge]:
    raw = json.loads(path.read_text())
    out: list[Edge] = []
    for d in raw.get("doors", []):
        out.append(Edge(
            src=d["device_id"],
            dst=d["door_id"],
            rel="HAS_DOOR",
            confidence=edge_confidence(active_probe=True, lift=None, sequence_support=None, source_count=None),
            source="active_biostar",
            inferred=False,
            properties={"name": d.get("name"), "site": d.get("site")},
        ))
    return out


def _from_tier1_honeywell(path: Path) -> list[Edge]:
    raw = json.loads(path.read_text())
    out: list[Edge] = []
    for p in raw.get("panels", []):
        for z in p.get("zones", []):
            out.append(Edge(
                src=p["panel_id"], dst=f"ZONE-{p['panel_id']}-{z['zone_id']}",
                rel="HAS_ZONE",
                confidence=edge_confidence(active_probe=True, lift=None, sequence_support=None, source_count=None),
                source="active_honeywell",
                inferred=False,
                properties={"zone_name": z.get("name"), "zone_type": z.get("type")},
            ))
    return out


def _from_cooccurrence_csv(path: Path) -> list[Edge]:
    """Read the Spark output (single CSV with header)."""
    import csv
    out: list[Edge] = []
    with path.open() as f:
        for r in csv.DictReader(f):
            lift = float(r.get("lift", 0) or 0)
            support = int(r.get("count", 0) or 0)
            out.append(Edge(
                src=r["a_id"], dst=r["b_id"],
                rel="DEPENDS_ON",
                confidence=edge_confidence(active_probe=False, lift=lift, sequence_support=None, source_count=None),
                source="inferred_cooccur",
                inferred=True,
                properties={"lift": lift, "support": support},
            ))
    return out


def _from_sequences(path: Path) -> list[Edge]:
    raw = json.loads(path.read_text())
    out: list[Edge] = []
    for pat in raw:
        seq = pat["pattern"]
        for i in range(len(seq) - 1):
            out.append(Edge(
                src=seq[i], dst=seq[i + 1],
                rel="DEPENDS_ON",
                confidence=edge_confidence(active_probe=False, lift=None,
                                            sequence_support=pat["support"], source_count=None),
                source="inferred_seq",
                inferred=True,
                properties={"sequence_support": pat["support"], "pattern": seq},
            ))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier1-snmp", type=Path, default=None)
    ap.add_argument("--tier1-onvif", type=Path, default=None)
    ap.add_argument("--tier1-biostar", type=Path, default=None)
    ap.add_argument("--tier1-honeywell", type=Path, default=None)
    ap.add_argument("--tier2-cooccur", type=Path, default=None)
    ap.add_argument("--tier2-seq", type=Path, default=None)
    ap.add_argument("--tier2-ip", type=Path, default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    edges: list[Edge] = []

    if args.tier1_snmp:
        edges.extend(_from_tier1_snmp(args.tier1_snmp))
    if args.tier1_onvif:
        edges.extend(_from_tier1_onvif(args.tier1_onvif))
    if args.tier1_biostar:
        edges.extend(_from_tier1_biostar(args.tier1_biostar))
    if args.tier1_honeywell:
        edges.extend(_from_tier1_honeywell(args.tier1_honeywell))
    if args.tier2_cooccur:
        edges.extend(_from_cooccurrence_csv(args.tier2_cooccur))
    if args.tier2_seq:
        edges.extend(_from_sequences(args.tier2_seq))
    if args.tier2_ip:
        for line in args.tier2_ip.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            edges.append(Edge(
                src=d["src"], dst=d["dst"], rel=d["rel"],
                confidence=float(d.get("confidence", 0)),
                source=d.get("source", "inferred_ip_log"),
                inferred=True,
                properties=d.get("properties", {}),
            ))

    n = upsert(edges)
    c = resolve_conflicts()
    log.info("merged %d edges (%d conflicts resolved)", n, c)


if __name__ == "__main__":
    main()
