"""Entry point: orchestrate all Tier 1 discovery and emit merged JSON."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from topology.discovery.tier1 import snmp_crawl, onvif
from topology.discovery.tier1.biostar import discover as biostar_discover
from topology.discovery.tier1.honeywell import parse as honeywell_parse

log = logging.getLogger("topology.discovery.tier1.run")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", type=Path, help="SNMP switch list (YAML)")
    ap.add_argument("--onvif", action="store_true")
    ap.add_argument("--biostar", action="store_true")
    ap.add_argument("--honeywell-dir", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    result: dict = {"neighbors": [], "fdb": [], "arp": [], "cameras": [], "doors": [], "panels": []}

    if args.targets:
        targets = snmp_crawl._load_targets(args.targets)
        snmp_result = snmp_crawl.run(targets, args.out.with_suffix(".snmp.json"))
        result["neighbors"].extend(snmp_result["neighbors"])
        result["fdb"].extend(snmp_result["fdb"])
        result["arp"].extend(snmp_result["arp"])

    if args.onvif:
        cams = onvif.discover()
        cams = onvif.enrich(cams)
        result["cameras"].extend([c.__dict__ for c in cams])

    if args.biostar:
        result["doors"].extend([d.__dict__ for d in biostar_discover()])

    if args.honeywell_dir:
        result["panels"].extend([p.__dict__ for p in honeywell_parse(args.honeywell_dir)])

    args.out.write_text(json.dumps(result, indent=2, default=str))
    log.info("wrote tier1 bundle to %s", args.out)


if __name__ == "__main__":
    main()
