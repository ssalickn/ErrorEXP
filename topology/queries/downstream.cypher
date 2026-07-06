// queries/downstream.cypher
// Devices that $device_id transitively depends on.
:param device_id => "AP-FL3-012";

MATCH (d:Device {device_id: $device_id})
MATCH p = (d)-[*1..5]->(down:Device)
WHERE ALL(r IN relationships(p) WHERE coalesce(r.confidence, 1.0) >= 0.5)
RETURN down.device_id AS device_id,
       down.device_type AS type,
       length(p) AS hops,
       [r IN relationships(p) | type(r)] AS rel_types
ORDER BY hops, device_id;
