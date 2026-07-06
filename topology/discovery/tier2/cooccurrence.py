"""Tier 2A: Time-windowed failure co-occurrence mining (Spark).

Computes, for all pairs (A,B) of devices, the probability that B goes offline
within Δ minutes of A going offline, and the resulting "lift" score over the
marginal failure rate. Emits candidate DEPENDS_ON edges with confidence.

Inputs:
  - A DataFrame with columns [device_id, ts, status] where status is one of
    OFFLINE / ONLINE / DEGRADED.

Output:
  - Parquet / JSON of edges (src, dst, count, p_b_given_a, p_b, lift, support)
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from pyspark.sql import SparkSession, DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql.types import StructType, StructField, StringType, TimestampType

from topology.config import load_settings

log = logging.getLogger("topology.discovery.tier2.cooccur")


def build_spark() -> SparkSession:
    s = load_settings()
    return (
        SparkSession.builder
        .appName(s.spark.app_name + "-cooccur")
        .master(s.spark.master)
        .config("spark.executor.memory", s.spark.executor_memory)
        .config("spark.sql.shuffle.partitions", s.spark.shuffle_partitions)
        .getOrCreate()
    )


def load_logs(spark: SparkSession, source: str) -> DataFrame:
    """Load device status logs.

    Supported sources:
      - 'parquet:<path>'
      - 'delta:<path>'
      - 'kafka:<bootstrap>,<topic>' (streaming)
      - 'json:<path>' (newline-delimited)
    """
    if source.startswith("parquet:"):
        return spark.read.parquet(source[len("parquet:"):])
    if source.startswith("delta:"):
        return spark.read.format("delta").load(source[len("delta:"):])
    if source.startswith("json:"):
        return spark.read.json(source[len("json:"):])
    if source.startswith("kafka:"):
        _, bootstrap_topic = source.split(":", 1)
        bootstrap, topic = bootstrap_topic.split(",", 1)
        return (spark.readStream
                .format("kafka")
                .option("kafka.bootstrap.servers", bootstrap)
                .option("subscribe", topic)
                .load()
                .selectExpr("CAST(value AS STRING) as v")
                .select(F.from_json("v", _schema()).alias("r"))
                .select("r.*"))
    raise ValueError(f"Unknown source kind: {source}")


def _schema() -> StructType:
    return StructType([
        StructField("device_id", StringType()),
        StructField("ts", TimestampType()),
        StructField("status", StringType()),
    ])


def mine(df: DataFrame, *, window_minutes: int, delta_minutes: int,
         min_lift: float) -> DataFrame:
    """Compute co-occurrence counts and lift for every (A, B) device pair.

    Approach:
      1. Restrict to OFFLINE events; assign an event_time.
      2. Self-join on a Δ-minute gap where src != dst.
      3. Group by (src, dst) and count.
      4. Compute marginals.
      5. lift = P(B in window | A) / P(B).
    """
    fail = df.filter(F.col("status") == "OFFLINE").select(
        F.col("device_id").alias("a_id"),
        F.col("ts").alias("a_ts"),
    )

    # Build window bounds: b_ts ∈ [a_ts, a_ts + Δ]
    joined = (
        fail.alias("a")
        .join(
            fail.alias("b"),
            (F.col("a.a_id") != F.col("b.b_id")) &
            (F.col("b.b_ts").between(F.col("a.a_ts"),
                                    F.col("a.a_ts") + F.expr(f"INTERVAL {delta_minutes} MINUTES"))),
            how="inner",
        )
        .select("a.a_id", "b.b_id", "a.a_ts")
    )

    cooccur = joined.groupBy("a_id", "b_id").agg(F.count("*").alias("count"))

    # Marginal counts per device
    a_marg = fail.groupBy("a_id").agg(F.count("*").alias("a_count"))
    b_marg = fail.groupBy("b_id").agg(F.count("*").alias("b_count"))
    total = fail.count()

    out = (
        cooccur
        .join(a_marg, "a_id")
        .join(b_marg, "b_id")
        .withColumn("p_b_given_a", F.col("count") / F.col("a_count"))
        .withColumn("p_b", F.col("b_count") / F.lit(total))
        .withColumn("lift",
                    F.when(F.col("p_b") > 0, F.col("p_b_given_a") / F.col("p_b")).otherwise(0.0))
        .withColumn("support", F.col("count") / F.lit(total))
        .filter(F.col("lift") >= F.lit(min_lift))
        .filter(F.col("count") >= F.lit(3))         # minimum support
    )
    return out


def confidence(lift: float, support: float) -> float:
    """Map (lift, support) to a 0..1 confidence.

    Caps: very high lift + high support → 0.9; we keep 1.0 reserved for Tier 1
    active probing. Below the configuration thresholds the confidence falls off.
    """
    from topology.config import load_settings
    cfg = load_settings().confidence
    if lift <= 0:
        return cfg.reject
    base = 0.4 + 0.05 * min(lift, 10.0)             # 0.4 .. 0.9
    if support > 0.01:
        base += 0.05
    return max(cfg.suggest, min(cfg.auto_accept, base))


def attach_confidence(df: DataFrame) -> DataFrame:
    """Add a `confidence` column derived from lift/support."""
    @F.udf("double")
    def _conf(lift, support):
        return confidence(float(lift or 0), float(support or 0))
    return df.withColumn("confidence", _conf(F.col("lift"), F.col("support")))


def write(df: DataFrame, out: str) -> None:
    if out.startswith("parquet:"):
        df.write.mode("overwrite").parquet(out[len("parquet:"):])
    elif out.startswith("json:"):
        df.coalesce(1).write.mode("overwrite").json(out[len("json:"):])
    else:
        df.coalesce(1).write.mode("overwrite").csv(out, header=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="parquet:/path | delta:/path | json:/path | kafka:host,topic")
    ap.add_argument("--out", required=True, help="parquet:/path | json:/path | /dir")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_settings()
    spark = build_spark()
    logs = load_logs(spark, args.source)
    edges = mine(logs,
                 window_minutes=cfg.discovery.tier2.failure_window_minutes,
                 delta_minutes=cfg.discovery.tier2.delta_minutes,
                 min_lift=cfg.discovery.tier2.min_lift)
    edges = attach_confidence(edges)
    write(edges, args.out)
    log.info("wrote inferred DEPENDS_ON candidates to %s", args.out)
    spark.stop()


if __name__ == "__main__":
    main()
