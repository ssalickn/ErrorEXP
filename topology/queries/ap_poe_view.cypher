// queries/ap_poe_view.cypher
// Switch port → neighbor with PoE / VLAN.
:param switch_id => "SW-CORE-07";

MATCH (sw:Device {device_id: $switch_id})-[:HAS_PORT]->(p:Port)
OPTIONAL MATCH (p)-[:CONNECTS_TO]->(neighbor:Device)
RETURN p.port_id AS port,
       p.vlan AS vlan,
       p.poe AS poe,
       neighbor.device_id AS neighbor,
       neighbor.device_type AS neighbor_type,
       neighbor.status AS neighbor_status
ORDER BY p.port_id;
