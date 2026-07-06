"""Cypher query library for Root-Cause Analysis traversals.

Each query is a function that returns a parameterized Cypher string + parameter
dict, ready to execute via the Neo4j Python driver.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Query:
    cypher: str
    params: dict[str, Any]
    description: str


# ── 1. Upstream blast radius ──────────────────────────────────────────────────
def upstream_blast(device_id: str, *, max_depth: int = 5,
                   min_confidence: float = 0.5) -> Query:
    """All devices that depend on `device_id` (transitively).

    If SW-CORE-07 fails, what else breaks?
    """
    return Query(
        cypher=f"""
        MATCH (d:Device {{device_id: $device_id}})
        MATCH p = (upstream:Device)-[*1..{max_depth}]-(d)
        WHERE ALL(r IN relationships(p) WHERE coalesce(r.confidence, 1.0) >= $min_confidence)
        RETURN upstream.device_id AS device_id,
               upstream.device_type AS type,
               length(p) AS hops,
               [r IN relationships(p) | type(r)] AS rel_types
        ORDER BY hops, device_id
        """,
        params={"device_id": device_id, "min_confidence": min_confidence},
        description="All devices transitively dependent on `device_id`",
    )


# ── 2. Downstream root-cause candidates ──────────────────────────────────────
def downstream_dependencies(device_id: str, *, max_depth: int = 5,
                            min_confidence: float = 0.5) -> Query:
    """Devices that `device_id` transitively depends on (potential root cause)."""
    return Query(
        cypher=f"""
        MATCH (d:Device {{device_id: $device_id}})
        MATCH p = (d)-[*1..{max_depth}]->(down:Device)
        WHERE ALL(r IN relationships(p) WHERE coalesce(r.confidence, 1.0) >= $min_confidence)
        RETURN down.device_id AS device_id,
               down.device_type AS type,
               length(p) AS hops,
               [r IN relationships(p) | type(r)] AS rel_types
        ORDER BY hops, device_id
        """,
        params={"device_id": device_id, "min_confidence": min_confidence},
        description="Devices `device_id` transitively depends on",
    )


# ── 3. Path between two devices ──────────────────────────────────────────────
def shortest_path(src: str, dst: str) -> Query:
    return Query(
        cypher="""
        MATCH (a:Device {device_id: $src}), (b:Device {device_id: $dst}),
              p = shortestPath((a)-[*..15]-(b))
        RETURN p,
               [n IN nodes(p) | {id: n.device_id, type: n.device_type, status: n.status}] AS nodes,
               [r IN relationships(p) | {type: type(r), confidence: r.confidence, source: r.source}] AS edges
        """,
        params={"src": src, "dst": dst},
        description=f"Shortest path between {src} and {dst}",
    )


# ── 4. Site-fanout (criticality) ─────────────────────────────────────────────
def site_criticality(site_id: str) -> Query:
    """Devices per site ranked by downstream fan-out (top of the cascade tree)."""
    return Query(
        cypher="""
        MATCH (d:Device {site: $site_id})
        OPTIONAL MATCH (d)-[*1..5]->(down:Device)
        WITH d, count(DISTINCT down) AS fanout
        RETURN d.device_id AS device_id,
               d.device_type AS type,
               d.status AS status,
               fanout
        ORDER BY fanout DESC
        LIMIT 50
        """,
        params={"site_id": site_id},
        description="Devices in site ranked by downstream fan-out",
    )


# ── 5. VLAN membership ──────────────────────────────────────────────────────
def vlan_members(vlan_id: int) -> Query:
    return Query(
        cypher="""
        MATCH (d:Device)-[:MEMBER_OF]->(v:VLAN {vlan_id: $vlan_id})
        RETURN d.device_id AS device_id,
               d.device_type AS type,
               d.ip AS ip,
               d.status AS status
        ORDER BY device_id
        """,
        params={"vlan_id": vlan_id},
        description="All devices on a VLAN",
    )


# ── 6. Inferred edges pending review ─────────────────────────────────────────
def pending_review(limit: int = 200) -> Query:
    return Query(
        cypher="""
        MATCH (a:Device)-[r]->(b:Device)
        WHERE r.inferred = true AND r.review_status IN ['pending', 'flagged']
        RETURN a.device_id AS src, type(r) AS rel, b.device_id AS dst,
               r.confidence AS confidence, r.source AS source, r.review_status AS status
        ORDER BY confidence DESC
        LIMIT $limit
        """,
        params={"limit": limit},
        description="Edges awaiting human review (inferred + flagged)",
    )


# ── 7. AP ↔ switch port detail ──────────────────────────────────────────────
def ap_poe_view(switch_device_id: str) -> Query:
    return Query(
        cypher="""
        MATCH (sw:Device {device_id: $switch_id})-[:HAS_PORT]->(p:Port)
        OPTIONAL MATCH (p)-[:CONNECTS_TO]->(neighbor:Device)
        RETURN p.port_id AS port,
               p.vlan AS vlan,
               p.poe AS poe,
               neighbor.device_id AS neighbor,
               neighbor.device_type AS neighbor_type,
               neighbor.status AS neighbor_status
        ORDER BY p.port_id
        """,
        params={"switch_id": switch_device_id},
        description="Switch port → connected device with PoE/VLAN view",
    )


# ── 8. Camera → NVR/VMS chain ────────────────────────────────────────────────
def camera_stream_chain(camera_id: str) -> Query:
    return Query(
        cypher="""
        MATCH (c:Device {device_id: $camera_id})
        OPTIONAL MATCH path = (c)-[:STREAMS_TO|MANAGES*1..5]->(sink:Device)
        RETURN [n IN nodes(path) | n.device_id] AS chain,
               [r IN relationships(path) | type(r)] AS rel_types
        """,
        params={"camera_id": camera_id},
        description="Camera → NVR → VMS chain",
    )


# ── 9. Confidence drop candidates (Tier 2) ──────────────────────────────────
def low_confidence_edges(limit: int = 200) -> Query:
    return Query(
        cypher="""
        MATCH (a:Device)-[r]->(b:Device)
        WHERE r.inferred = true AND r.confidence < 0.6
        RETURN a.device_id AS src, type(r) AS rel, b.device_id AS dst,
               r.confidence AS confidence, r.source AS source
        ORDER BY confidence
        LIMIT $limit
        """,
        params={"limit": limit},
        description="Inferred edges below confidence threshold",
    )


# ── 10. Drift hotlist (recently changed) ─────────────────────────────────────
def recent_drift(days: int = 7) -> Query:
    return Query(
        cypher="""
        MATCH (a:Device)-[r]->(b:Device)
        WHERE r.last_observed IS NOT NULL
          AND datetime(r.last_observed) > datetime() - duration({days: $days})
        RETURN a.device_id AS src, type(r) AS rel, b.device_id AS dst,
               r.confidence AS confidence, r.last_observed AS last_observed
        ORDER BY last_observed DESC
        LIMIT 200
        """,
        params={"days": days},
        description="Edges observed in the last N days (drift hotlist)",
    )


CATALOG: dict[str, callable] = {
    "upstream_blast":     upstream_blast,
    "downstream":         downstream_dependencies,
    "shortest_path":      shortest_path,
    "site_criticality":   site_criticality,
    "vlan_members":       vlan_members,
    "pending_review":     pending_review,
    "ap_poe_view":        ap_poe_view,
    "camera_chain":       camera_stream_chain,
    "low_confidence":     low_confidence_edges,
    "recent_drift":       recent_drift,
}
