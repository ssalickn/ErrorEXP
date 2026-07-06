// Neo4j schema bootstrap. Apply with: cypher-shell -u neo4j -p <pw> < schema.cypher
// Idempotent: uses IF NOT EXISTS where supported by the version.

CREATE CONSTRAINT device_id IF NOT EXISTS
  FOR (d:Device) REQUIRE d.device_id IS UNIQUE;

CREATE CONSTRAINT port_id IF NOT EXISTS
  FOR (p:Port) REQUIRE (p.switch_id, p.port_id) IS NODE KEY;

CREATE CONSTRAINT vlan_id IF NOT EXISTS
  FOR (v:VLAN) REQUIRE v.vlan_id IS UNIQUE;

CREATE CONSTRAINT site_id IF NOT EXISTS
  FOR (s:Site) REQUIRE s.site_id IS UNIQUE;

CREATE CONSTRAINT service_id IF NOT EXISTS
  FOR (svc:Service) REQUIRE svc.service_id IS UNIQUE;

// Indexes that help RCA traversals
CREATE INDEX device_type_idx IF NOT EXISTS FOR (d:Device) ON (d.device_type);
CREATE INDEX device_site_idx  IF NOT EXISTS FOR (d:Device) ON (d.site);
CREATE INDEX device_status_idx IF NOT EXISTS FOR (d:Device) ON (d.status);

// Full-text search on device_id and name for engineer queries
CREATE FULLTEXT INDEX device_search IF NOT EXISTS FOR (d:Device) ON EACH [d.device_id, d.name];
