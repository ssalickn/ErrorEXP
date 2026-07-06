// queries/camera_chain.cypher
// Camera → NVR → VMS stream chain.
:param camera_id => "CAM-FL3-301";

MATCH (c:Device {device_id: $camera_id})
OPTIONAL MATCH path = (c)-[:STREAMS_TO|MANAGES*1..5]->(sink:Device)
RETURN [n IN nodes(path) | n.device_id] AS chain,
       [r IN relationships(path) | type(r)] AS rel_types;
