"""
Cascade / root-cause correlation.

When several devices go down in a short window, this module picks the
single device most likely to be the *root* cause of the outage, by
combining:

  1. Temporal evidence   - which device's failure came first.
  2. Topology evidence    - which device has the most failing
                             downstream descendants in the graph.
  3. Severity evidence    - error vs degraded vs offline.

The scoring function is deliberately simple and explainable. Each
candidate device gets a score in [0, 1] plus a per-component
breakdown, so the dashboard can show *why* the AI chose it.

The detector is topology-aware: it pulls the active graph from
`iot.v_active_topology` (a view over `iot.device_relationships`) and
uses a BFS from each candidate to count how many of the
currently-failing devices lie in its downstream blast radius.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #

@dataclass
class FailingDevice:
    """One device that has at least one 'problem' event in the window."""
    device_id: str
    device_type: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    severity: str = "error"           # error | critical | warning
    status: Optional[str] = None      # offline | degraded | unknown
    event_count: int = 1


@dataclass
class CandidateScore:
    """Score breakdown for one candidate root cause."""
    device_id: str
    score: float
    temporal_first: float             # 0..1, higher if it failed earliest
    topology_coverage: float          # 0..1, share of cluster it explains
    severity: float                   # 0..1, weight by error severity
    explained_devices: list[str] = field(default_factory=list)
    explanation: str = ""


@dataclass
class Cascade:
    """A detected cascade event."""
    cascade_id: Optional[int] = None
    detected_at: Optional[datetime] = None
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    cluster_size: int = 0
    root_cause_device_id: Optional[str] = None
    root_cause_confidence: float = 0.0
    candidates: list[CandidateScore] = field(default_factory=list)
    affected_device_ids: list[str] = field(default_factory=list)
    explanation: str = ""
    contributing_log_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.detected_at:
            d["detected_at"] = self.detected_at.isoformat()
        if self.window_start:
            d["window_start"] = self.window_start.isoformat()
        if self.window_end:
            d["window_end"] = self.window_end.isoformat()
        return d


# --------------------------------------------------------------------------- #
# Graph (loaded once, then queried in-memory)
# --------------------------------------------------------------------------- #

class TopologyGraph:
    """Adjacency list of downstream edges.

    `downstream[a]` = the set of devices that depend on `a`. We treat
    `device_relationships.source_id -> target_id` as "source provides
    service to target", so a failure of `source` propagates *to*
    `target` -- i.e. `target` is in the downstream of `source`.

    This view is built from the SQL view the rest of the dashboard
    already uses (`iot.v_active_topology`), so the cascade detector
    stays consistent with what the user sees on the topology page.
    """

    def __init__(self, edges: Iterable[tuple[str, str]]):
        self._downstream: dict[str, set[str]] = defaultdict(set)
        self._upstream: dict[str, set[str]] = defaultdict(set)
        for src, dst in edges:
            if src == dst:
                continue
            self._downstream[src].add(dst)
            self._upstream[dst].add(src)

    def downstream_of(self, device_id: str, max_hops: int = 10) -> set[str]:
        """All devices transitively dependent on `device_id`."""
        seen: set[str] = set()
        frontier = {device_id}
        for _ in range(max_hops):
            nxt: set[str] = set()
            for d in frontier:
                for child in self._downstream.get(d, ()):
                    if child not in seen and child != device_id:
                        seen.add(child)
                        nxt.add(child)
            if not nxt:
                break
            frontier = nxt
        return seen

    @property
    def node_count(self) -> int:
        return len(set(self._downstream) | set(self._upstream))

    @property
    def edge_count(self) -> int:
        return sum(len(s) for s in self._downstream.values())


# --------------------------------------------------------------------------- #
# Correlator
# --------------------------------------------------------------------------- #

class CascadeCorrelator:
    """Detect and explain cascading failures.

    Usage:
        graph = CascadeCorrelator.load_graph(pool)
        corr = CascadeCorrelator(graph, cluster_threshold=3, window_minutes=5)
        cascade = corr.evaluate(failing_devices, now=datetime.utcnow())
    """

    # Minimum cluster size before we even attempt to pick a root cause.
    DEFAULT_CLUSTER_THRESHOLD = 3

    # How wide a temporal window defines "the same outage".
    DEFAULT_WINDOW_MINUTES = 5

    # Temporal decay: how aggressively we reward earlier failures.
    #   0.0 -> ignore timing, 1.0 -> first-failure always wins.
    TEMPORAL_WEIGHT = 0.25
    TOPOLOGY_WEIGHT = 0.65
    SEVERITY_WEIGHT = 0.10

    def __init__(
        self,
        graph: TopologyGraph,
        *,
        cluster_threshold: int = DEFAULT_CLUSTER_THRESHOLD,
        window_minutes: int = DEFAULT_WINDOW_MINUTES,
    ):
        self.graph = graph
        self.cluster_threshold = cluster_threshold
        self.window = timedelta(minutes=window_minutes)

    # -- cluster extraction ------------------------------------------------- #

    def evaluate(
        self,
        failing: list[FailingDevice],
        now: Optional[datetime] = None,
    ) -> Optional[Cascade]:
        """Return a Cascade if `failing` looks like a correlated outage."""
        now = now or datetime.utcnow()

        # 1. Filter to the active window.
        windowed = self._filter_window(failing, now)
        if len(windowed) < self.cluster_threshold:
            return None

        # 2. Score every device in the cluster.
        candidates = [self._score(c, windowed) for c in windowed]
        candidates.sort(key=lambda c: c.score, reverse=True)

        winner = candidates[0]
        affected = sorted({c.device_id for c in windowed})

        cascade = Cascade(
            detected_at=now,
            window_start=min(c.first_seen or now for c in windowed),
            window_end=max(c.last_seen or now for c in windowed),
            cluster_size=len(windowed),
            root_cause_device_id=winner.device_id,
            root_cause_confidence=round(winner.score, 3),
            candidates=candidates[:5],   # top 5 for the UI
            affected_device_ids=affected,
            explanation=self._explain(winner, windowed),
            contributing_log_ids=[],     # filled in by the trigger
        )
        return cascade

    # -- helpers ------------------------------------------------------------ #

    def _filter_window(
        self, failing: list[FailingDevice], now: datetime
    ) -> list[FailingDevice]:
        cutoff = now - self.window
        out = []
        for f in failing:
            ts = f.first_seen or now
            if ts >= cutoff:
                out.append(f)
        return out

    def _score(
        self, candidate: FailingDevice, cluster: list[FailingDevice]
    ) -> CandidateScore:
        cluster_ids = {c.device_id for c in cluster}
        cluster_ids.discard(candidate.device_id)

        # 1. Topology: which share of the rest of the cluster is the
        #    candidate's downstream blast radius?
        blast = self.graph.downstream_of(candidate.device_id) & cluster_ids
        coverage = len(blast) / max(1, len(cluster_ids))

        # 2. Temporal: how much earlier than the cluster mean did it fail?
        times = [c.first_seen for c in cluster if c.first_seen]
        if times and candidate.first_seen:
            mean = (min(times) + (max(times) - min(times)) / 2)
            delta_s = (mean - candidate.first_seen).total_seconds()
            # Map [-window..+window] seconds to [1..0].
            window_s = max(1.0, self.window.total_seconds())
            temporal = max(0.0, min(1.0, 0.5 + delta_s / window_s))
        else:
            temporal = 0.5  # unknown -> neutral

        # 3. Severity weight.
        sev_table = {"critical": 1.0, "error": 0.8, "warning": 0.5}
        severity = sev_table.get((candidate.severity or "").lower(), 0.6)
        if (candidate.status or "").lower() == "offline":
            severity = max(severity, 0.9)

        score = (
            self.TEMPORAL_WEIGHT * temporal
            + self.TOPOLOGY_WEIGHT * coverage
            + self.SEVERITY_WEIGHT * severity
        )

        return CandidateScore(
            device_id=candidate.device_id,
            score=round(score, 4),
            temporal_first=round(temporal, 3),
            topology_coverage=round(coverage, 3),
            severity=round(severity, 3),
            explained_devices=sorted(blast),
            explanation=(
                f"Failed ~{int((1-temporal)*self.window.total_seconds())}s before the "
                f"cluster mean; {len(blast)} of {len(cluster_ids)} other failing "
                f"devices are in its downstream blast radius; severity={candidate.severity}."
            ),
        )

    @staticmethod
    def _explain(winner: CandidateScore, cluster: list[FailingDevice]) -> str:
        names = ", ".join(c.device_id for c in cluster if c.device_id != winner.device_id)
        return (
            f"{winner.device_id} is the most likely root cause "
            f"(score={winner.score:.2f}). It explains "
            f"{len(winner.explained_devices)} of the other "
            f"failing devices via the topology graph: {names}."
        )

    # -- DB loader ---------------------------------------------------------- #

    @staticmethod
    def load_graph(db_pool) -> TopologyGraph:
        """Build a TopologyGraph from `iot.v_active_topology`."""
        try:
            with db_pool.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT source_id, target_id
                    FROM iot.v_active_topology
                    WHERE confidence >= 0.5
                """)
                edges = [(r[0], r[1]) for r in cur.fetchall()]
            graph = TopologyGraph(edges)
            logger.info(
                "Loaded topology graph: %d nodes, %d edges",
                graph.node_count, graph.edge_count,
            )
            return graph
        except Exception as e:
            logger.warning("Could not load topology graph (%s); using empty graph.", e)
            return TopologyGraph([])
