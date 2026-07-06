# IoT Device Topology Discovery

A production-grade pipeline for discovering, validating, and maintaining a unified
device topology graph (Neo4j) across an enterprise IoT/OT estate.

## Scope

Unifies five layers into a single graph:

| Layer | Examples | Source |
|-------|----------|--------|
| L1 - Physical | cabling, PoE, rack | LLDP/CDP, manual CSV |
| L2 - Network (MAC/VLAN/STP) | trunks, switch ports | SNMP `BRIDGE-MIB`, `ifTable` |
| L3 - Network (IP/routing) | subnets, gateways | ARP + NetFlow/IPFIX |
| L4 - Application/Service | RTSP, CAPWAP, HTTPS | NVR/VMS/WLC logs |
| L5 - Control Plane | WLC↔AP, BioStar server↔door | vendor APIs |

## Pipeline

```
Inventory Normalization
       │
       ▼
Tier 1 Active Probing  (SNMP/LLDP/CDP, ONVIF, BioStar API, Honeywell export)
       │
       ▼
Tier 2 Log Inference   (Spark co-occurrence, PrefixSpan, IP↔device extraction)
       │
       ▼
Graph Merge + Confidence Scoring
       │
       ▼
Human-in-the-Loop Validation  (UI hooks + feedback loop)
       │
       ▼
Continuous Refresh  (hourly/daily diff + drift alerts)
```

## Layout

```
topology/
├── config/                # settings, secrets templates, device seeds
├── inventory/             # Stage 1: normalize vendor inventories into Postgres
├── discovery/
│   ├── tier1/             # SNMP/LLDP/CDP, ONVIF, BioStar, Honeywell
│   └── tier2/             # Spark co-occurrence + PrefixSpan
├── graph/                 # Neo4j schema, loader, merge
├── validation/            # HITL review endpoints
├── refresh/               # diff + drift jobs
├── queries/               # RCA traversal Cypher library
├── visualization/         # Neo4j Browser / Grafana JSON
└── tests/
```

## Quickstart

```bash
pip install -r requirements.txt
psql -f config/schema.sql
python -m topology.inventory.load --source netbox
python -m topology.discovery.tier1.run --targets config/cisco_switches.yaml
python -m topology.discovery.tier1.onvif --subnet 10.10.5.0/24
python -m topology.graph.merge --since 24h
python -m topology.refresh.diff --alert
```

See `topology/config/` for environment-specific files.
