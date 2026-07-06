"""Tier 2B: Sequence mining for failure cascades (PrefixSpan / SPMF).

Mines frequent ordered failure sequences across device types, e.g.:
    [SWITCH_DOWN, AP_OFFLINE, CAMERA_OFFLINE]  (support=412)

Each frequent pattern becomes a template edge set, weighted by support.

The implementation is a small PySpark wrapper around the
`prefixspan` library; if unavailable, a SPMF-style ASCII export is produced
for offline mining.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Iterable

from pyspark.sql import functions as F

from topology.config import load_settings
from topology.discovery.tier2.cooccurrence import build_spark, load_logs

log = logging.getLogger("topology.discovery.tier2.sequence")


def _sessionize(df, *, gap_minutes: int = 30):
    """Group OFFLINE events into sessions, where the gap between consecutive
    failures within a session is <= gap_minutes.
    Returns a DataFrame with [session_id, device_id, ts].
    """
    fail = df.filter(F.col("status") == "OFFLINE").select(
        F.col("device_id"), F.col("ts")
    ).orderBy("device_id", "ts")

    # Use a windowed lag; if gap > threshold → new session per device
    from pyspark.sql.window import Window
    w = Window.partitionBy("device_id").orderBy("ts")
    fail = fail.withColumn("prev_ts", F.lag("ts").over(w))
    fail = fail.withColumn(
        "is_new_session",
        F.when(F.col("prev_ts").isNull() |
               ((F.col("ts").cast("long") - F.col("prev_ts").cast("long")) / 60.0 > gap_minutes), 1).otherwise(0),
    )
    fail = fail.withColumn("session_id_local", F.sum("is_new_session").over(w))
    # Generate a globally unique session id
    fail = fail.withColumn("session_id",
                           F.concat_ws(":", F.col("device_id"),
                                       F.col("session_id_local").cast("string")))
    return fail.select("session_id", "device_id", "ts")


def mine(df, *, gap_minutes: int = 30, min_support: int = 50, max_len: int = 5) -> list[dict]:
    """Run prefixspan over per-session ordered failure lists."""
    sess = _sessionize(df, gap_minutes=gap_minutes)
    # Each session contributes a list of device_ids ordered by ts
    by_sess = sess.groupBy("session_id").agg(
        F.expr("collect_list(struct(ts, device_id))").alias("events")
    )
    # Sort events by ts in UDF (Spark collect_list does not preserve order)
    sort_udf = F.udf(lambda xs: [d for _, d in sorted(xs, key=lambda t: t[0])],
                     "array<string>")
    sequences = by_sess.withColumn("seq", sort_udf(F.col("events"))).select("seq")

    # Try prefixspan library; fall back to SPMF export.
    try:
        from prefixspan import PrefixSpan          # type: ignore
        ps = PrefixSpan()
        # collect_list of arrays → Python list of lists
        corpus = [list(row.seq) for row in sequences.collect()]
        ps.create_patterns(min_support=min_support, max_length=max_len, closed=False)
        results = ps.frequent(corpus)
        out = [
            {"pattern": list(p[0]), "support": int(p[1])}
            for p in results
        ]
        return out
    except ImportError:
        log.warning("prefixspan lib not available; exporting SPMF input")
        out_path = Path("var/sequences.spmf.txt")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w") as f:
            for row in sequences.collect():
                f.write(" -1 ".join(row.seq) + " -2\n")
        log.info("wrote SPMF input to %s; run SPMF PrefixSpan offline", out_path)
        return []


def confidence(support: int, total_sessions: int) -> float:
    if total_sessions <= 0:
        return 0.0
    base = 0.4 + min(0.3, 0.05 * (support / max(1, total_sessions // 100)))
    return max(0.3, min(0.9, base))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", required=True, help="JSON output path")
    ap.add_argument("--min-support", type=int, default=None)
    ap.add_argument("--gap-minutes", type=int, default=30)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_settings()
    spark = build_spark()
    logs = load_logs(spark, args.source)

    min_support = args.min_support or cfg.discovery.tier2.min_sequence_support
    patterns = mine(logs, gap_minutes=args.gap_minutes, min_support=min_support)
    Path(args.out).write_text(json.dumps(patterns, indent=2))
    log.info("wrote %d sequence patterns to %s", len(patterns), args.out)
    spark.stop()


if __name__ == "__main__":
    main()
