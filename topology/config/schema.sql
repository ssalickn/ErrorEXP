-- topology/config/schema.sql
-- Postgres schema for the normalized device inventory and discovery results.
-- Run once during bootstrap.

CREATE SCHEMA IF NOT EXISTS topology;

-- Canonical device registry. The golden device_id ↔ IP ↔ MAC ↔ type.
CREATE TABLE IF NOT EXISTS topology.devices (
    device_id        TEXT PRIMARY KEY,
    device_type      TEXT NOT NULL,  -- cisco_ap, cisco_switch, nvr, vms, honeywell_panel, biostar_door, biostar_server, camera
    vendor           TEXT,
    model            TEXT,
    ip_address       INET,
    mac_address      MACADDR,
    site             TEXT,
    floor            INT,
    rack             TEXT,
    status           TEXT,           -- online | offline | unknown
    first_seen       TIMESTAMPTZ DEFAULT now(),
    last_seen        TIMESTAMPTZ DEFAULT now(),
    source_system    TEXT,           -- netbox, manual, onvif, etc.
    raw              JSONB
);
CREATE INDEX IF NOT EXISTS devices_ip_idx      ON topology.devices (ip_address);
CREATE INDEX IF NOT EXISTS devices_mac_idx     ON topology.devices (mac_address);
CREATE INDEX IF NOT EXISTS devices_type_idx    ON topology.devices (device_type);
CREATE INDEX IF NOT EXISTS devices_site_idx    ON topology.devices (site);

-- Discovered topology edges (Tier 1 active probing and Tier 2 inference).
-- Neo4j remains the queryable store; this table is the audit trail and source of merge.
CREATE TABLE IF NOT EXISTS topology.edges (
    edge_id          BIGSERIAL PRIMARY KEY,
    src_device_id    TEXT NOT NULL REFERENCES topology.devices(device_id),
    dst_device_id    TEXT NOT NULL REFERENCES topology.devices(device_id),
    rel_type         TEXT NOT NULL,  -- CONNECTS_TO, MEMBER_OF, STREAMS_TO, MANAGES, POWERS, DEPENDS_ON, ...
    confidence       REAL NOT NULL,  -- 0..1
    source           TEXT NOT NULL,  -- active_lldp, active_cdp, active_onvif, inferred_cooccur, inferred_seq, manual
    inferred         BOOLEAN NOT NULL DEFAULT FALSE,
    properties       JSONB,
    first_observed   TIMESTAMPTZ,
    last_observed    TIMESTAMPTZ,
    observed_count   INT DEFAULT 0,
    reviewed_by      TEXT,
    reviewed_at      TIMESTAMPTZ,
    review_status    TEXT,           -- approved | rejected | pending
    UNIQUE (src_device_id, dst_device_id, rel_type, source)
);
CREATE INDEX IF NOT EXISTS edges_src_idx        ON topology.edges (src_device_id);
CREATE INDEX IF NOT EXISTS edges_dst_idx        ON topology.edges (dst_device_id);
CREATE INDEX IF NOT EXISTS edges_conf_idx       ON topology.edges (confidence DESC);
CREATE INDEX IF NOT EXISTS edges_review_idx     ON topology.edges (review_status);

-- Drift / change log emitted by the refresh job.
CREATE TABLE IF NOT EXISTS topology.drift_log (
    drift_id         BIGSERIAL PRIMARY KEY,
    detected_at      TIMESTAMPTZ DEFAULT now(),
    change_kind      TEXT NOT NULL,  -- edge_added, edge_removed, device_added, device_removed, confidence_dropped
    subject          TEXT NOT NULL,  -- device_id or edge id
    payload          JSONB
);
CREATE INDEX IF NOT EXISTS drift_detected_idx ON topology.drift_log (detected_at DESC);
