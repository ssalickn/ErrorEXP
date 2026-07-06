// queries/upstream_blast.cypher
// All devices transitively dependent on $device_id.
:param device_id => "SW-CORE-07";
:param max_depth => 5;
:param min_confidence => 0.5;

MATCH (d:Device {device_id: $device_id})
MATCH p = (upstream:Device)-[*1..$max_depth]-(d)
WHERE ALL(r IN relationships(p) WHERE coalesce(r.confidence, 1.0) >= $min_confidence)
RETURN upstream.device_id AS device_id,
       upstream.device_type AS type,
       length(p) AS hops,
       [r IN relationships(p) | type(r)] AS rel_types
ORDER BY hops, device_id;
