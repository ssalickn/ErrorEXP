"""
Topology endpoints.
"""

from fastapi import APIRouter, HTTPException
from typing import List
import pandas as pd
from backend.database import pool
from backend.models import TopologyEdge

router = APIRouter(prefix="/api/topology", tags=["topology"])


@router.get("", response_model=List[TopologyEdge])
def list_topology():
    """Get all active topology edges."""
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql("""
                SELECT source_id, target_id, relationship_type,
                       source_port, target_port, confidence, source
                FROM iot.v_active_topology
            """, conn)
        return df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/graph")
def get_graph_data():
    """Get topology in graph format (nodes + edges) for visualization."""
    try:
        with pool.get_connection() as conn:
            nodes_df = pd.read_sql("""
                SELECT device_id, device_name, device_type,
                       status, site_id
                FROM iot.devices
            """, conn)
            
            edges_df = pd.read_sql("""
                SELECT source_id, target_id, relationship_type,
                       source_port, confidence, source
                FROM iot.v_active_topology
            """, conn)
        
        return {
            "nodes": nodes_df.to_dict(orient="records"),
            "edges": edges_df.to_dict(orient="records"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dependencies/{device_id}")
def get_dependencies(device_id: str, depth: int = 3):
    """
    Get upstream and downstream dependencies for a device.
    Uses recursive CTE to traverse the topology.
    """
    query = """
    WITH deps AS (
        SELECT target_id AS related_id, 1 AS depth, 'downstream' AS direction
        FROM iot.device_relationships
        WHERE source_id = ? AND valid_to IS NULL
        UNION ALL
        SELECT source_id, d.depth + 1, 'upstream'
        FROM iot.device_relationships r
        JOIN deps d ON r.target_id = d.related_id
        WHERE r.valid_to IS NULL AND d.depth < ?
    )
    SELECT related_id, MIN(depth) AS min_depth, direction
    FROM deps
    GROUP BY related_id, direction
    ORDER BY direction, min_depth
    """
    try:
        with pool.get_connection() as conn:
            df = pd.read_sql(query, conn, params=[device_id, depth])
        return df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
