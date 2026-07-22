"""
Topology endpoints.
"""

from fastapi import APIRouter, HTTPException
from typing import List, Optional
import pandas as pd
import warnings
from backend.database import pool
from backend.models import TopologyEdge
import re
import ipaddress

warnings.filterwarnings(
    "ignore",
    message="pandas only supports SQLAlchemy connectable",
)

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

def calculate_node_position(device_name: str, ip_address: str, index: int) -> dict:
    """
    Derive visual grouping, tier level, and relative (x, y) coordinates
    from the device name and IP address.
    """
    name = device_name or ""
    
    # 1. Detect Device Role & Vertical Tier (Y-Axis)
    # Tier 1 (Y=0): Switches | Tier 2 (Y=200): NVRs | Tier 3 (Y=400): Cameras/Endpoints
    name_lower = name.lower()
    if any(k in name_lower for k in ["sw", "switch", "core", "router"]):
        tier = 1
        y = 0
    elif any(k in name_lower for k in ["nvr", "server", "recorder"]):
        tier = 2
        y = 200
    else:
        tier = 3
        y = 400

    # 2. Extract Zone / Location Keyword from Device Name (e.g., "Admin Building", "Clarifier")
    # Matches patterns like "Admin Building SW (S)" or "Clarifier (West)"
    zone_match = re.search(r"^([^\(-]+)", name)
    zone = zone_match.group(1).strip() if zone_match else "General Plant"

    # 3. Parse IP Address for Subnet Grouping & Horizontal Alignment (X-Axis)
    x = index * 150  # Default spacing fallback
    subnet_key = "1"
    
    if ip_address:
        try:
            ip_obj = ipaddress.ip_address(ip_address)
            octets = ip_address.split(".")
            if len(octets) == 4:
                subnet_key = octets[2]    # 3rd octet = Subnet / VLAN cluster
                host_id = int(octets[3])  # 4th octet = Host ID for horizontal ordering
                
                # Offset X based on Subnet cluster + host ID position
                subnet_offset = int(subnet_key) * 1000
                x = subnet_offset + (host_id * 60)
        except ValueError:
            pass

    return {
        "x": x,
        "y": y,
        "tier_level": tier,
        "zone_group": zone,
        "subnet": f"Subnet .{subnet_key}"
    }


@router.get("/graph")
def get_graph_data():
    """Get topology with relative (x, y) positions computed from name & IP."""
    try:
        with pool.get_connection() as conn:
            nodes_df = pd.read_sql("""
                SELECT device_id, device_name, device_type, status, site_id, ip_address
                FROM iot.devices
            """, conn)
            
            edges_df = pd.read_sql("""
                SELECT source_id, target_id, relationship_type, confidence
                FROM iot.v_active_topology
            """, conn)

        nodes = nodes_df.to_dict(orient="records")

        # Assign calculated spatial metadata to each node
        for idx, node in enumerate(nodes):
            pos_data = calculate_node_position(
                device_name=node.get("device_name", ""),
                ip_address=node.get("ip_address", ""),
                index=idx
            )
            # Add spatial coordinates and layout properties directly to node output
            node["position"] = {"x": pos_data["x"], "y": pos_data["y"]}
            node["tier_level"] = pos_data["tier_level"]
            node["zone_group"] = pos_data["zone_group"]
            node["subnet"] = pos_data["subnet"]

        return {
            "nodes": nodes,
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