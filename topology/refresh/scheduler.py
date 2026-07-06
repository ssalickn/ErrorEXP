"""Run the topology pipeline end-to-end on a schedule.

Wraps the discovery, merge, and loader commands. Designed to be invoked
by cron / Kubernetes CronJob / Airflow.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("topology.refresh.scheduler")


def _run(cmd: list[str]) -> None:
    log.info("$ %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config-dir", type=Path, required=True)
    ap.add_argument("--var-dir", type=Path, default=Path("var"))
    ap.add_argument("--events", type=Path, default=None,
                    help="NDJSON of recent events for IP inference")
    ap.add_argument("--skip-tier1", action="store_true")
    ap.add_argument("--skip-tier2", action="store_true")
    ap.add_argument("--skip-neo4j", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args.var_dir.mkdir(parents=True, exist_ok=True)
    snmp_out = args.var_dir / "snmp.json"
    onvif_out = args.var_dir / "onvif.json"
    biostar_out = args.var_dir / "biostar.json"
    honeywell_dir = args.config_dir.parent / "var" / "honeywell_exports"
    cooccur_out = args.var_dir / "cooccur.csv"
    seq_out = args.var_dir / "sequences.json"
    ip_out = args.var_dir / "ip_edges.jsonl"
    tier1_bundle = args.var_dir / "tier1.json"
    snapshot = args.var_dir / "snapshot_edges.jsonl"

    # Stage 1 inventory (NetBox or CSV) — call separately or in a wrapper.
    # Stage 2 Tier 1
    if not args.skip_tier1:
        ti = ["python", "-m", "topology.discovery.tier1.run", "--out", str(tier1_bundle)]
        ti += ["--targets", str(args.config_dir / "cisco_switches.yaml")]
        ti += ["--onvif", "--biostar"]
        if honeywell_dir.exists():
            ti += ["--honeywell-dir", str(honeywell_dir)]
        _run(ti)

    # Stage 3 Tier 2
    if not args.skip_tier2 and args.events:
        _run(["python", "-m", "topology.discovery.tier2.ip_extraction",
              "--events", str(args.events), "--out", str(ip_out)])

    # Stage 4 merge
    merge = ["python", "-m", "topology.graph.merge"]
    if not args.skip_tier1:
        merge += ["--tier1-snmp", str(tier1_bundle)]
        merge += ["--tier1-onvif", str(onvif_out if onvif_out.exists() else tier1_bundle)]
        merge += ["--tier1-biostar", str(tier1_bundle)]
        merge += ["--tier1-honeywell", str(tier1_bundle)]
    if not args.skip_tier2:
        if cooccur_out.exists():
            merge += ["--tier2-cooccur", str(cooccur_out)]
        if seq_out.exists():
            merge += ["--tier2-seq", str(seq_out)]
        if ip_out.exists():
            merge += ["--tier2-ip", str(ip_out)]
    _run(merge)

    # Drift
    if tier1_bundle.exists() and ip_out.exists():
        # Build a tiny snapshot combining tier1 + inferred
        with snapshot.open("w") as out:
            for line in tier1_bundle.read_text().splitlines():
                # Synthesize a snapshot row per neighbor (very small sample)
                out.write(line + "\n")
            for line in ip_out.read_text().splitlines():
                out.write(line + "\n")
        _run(["python", "-m", "topology.refresh.diff", "--snapshot", str(snapshot), "--alert"])

    # Stage 6 loader
    if not args.skip_neo4j:
        _run(["python", "-m", "topology.graph.loader"])

    log.info("topology refresh complete")


if __name__ == "__main__":
    main()
