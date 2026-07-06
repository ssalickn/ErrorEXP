// queries/site_criticality.cypher
// Devices in site ranked by downstream fan-out.
:param site_id => "BLDG-A";

MATCH (d:Device {site: $site_id})
OPTIONAL MATCH (d)-[*1..5]->(down:Device)
WITH d, count(DISTINCT down) AS fanout
RETURN d.device_id AS device_id,
       d.device_type AS type,
       d.status AS status,
       fanout
ORDER BY fanout DESC
LIMIT 50;
