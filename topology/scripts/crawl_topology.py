"""
SNMP/LLDP/CDP Topology Crawler → SQL Server
Compatible with pysnmp 6.x and 7.x

Usage:
    pip install pysnmp pyodbc pandas
    python crawl_topology.py --community public --seed 10.36.4.1
"""

import argparse
import asyncio
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

import pyodbc

# ═══════════════════════════════════════════════════════════
# Try multiple pysnmp import styles (version compatibility)
# ═══════════════════════════════════════════════════════════

USING_LEGACY_PYSNMP = False
try:
    # New pysnmp 7.x API
    from pysnmp.hlapi.v3arch.asyncio import (
        SnmpEngine, CommunityData, UdpTransportTarget,
        ContextData, ObjectType, ObjectIdentity
    )
    from pysnmp.hlapi.v3arch.asyncio import get_cmd, walk_cmd
    print("Using pysnmp v3arch.asyncio API")
except ImportError:
    try:
        # Older pysnmp 6.x API
        from pysnmp.hlapi.asyncio import (
            SnmpEngine, CommunityData, UdpTransportTarget,
            ContextData, ObjectType, ObjectIdentity
        )
        from pysnmp.hlapi.asyncio import get_cmd, walk_cmd
        print("Using pysnmp hlapi.asyncio API")
    except ImportError:
        try:
            # Legacy sync API
            from pysnmp.hlapi import (
                SnmpEngine, CommunityData, UdpTransportTarget,
                ContextData, ObjectType, ObjectIdentity,
                getCmd, walkCmd
            )
            USING_LEGACY_PYSNMP = True
            get_cmd = getCmd
            walk_cmd = walkCmd
            print("Using legacy pysnmp hlapi sync API")
        except ImportError as e:
            print(f"ERROR: Cannot import pysnmp: {e}")
            print("Run: py -m pip install pysnmp")
            exit(1)

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=thtrdinfradb1;"
    "Database=InfrastructureMonitorDB;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

SITE_ID = "BLDG-A"

OIDS = {
    "sysDescr":       "1.3.6.1.2.1.1.1.0",
    "sysName":        "1.3.6.1.2.1.1.5.0",
    "ifName":         "1.3.6.1.2.1.31.1.1.1.1",
    "lldpRemSysName": "1.0.8802.1.1.2.1.4.1.1.9",
    "cdpCacheDeviceId": "1.3.6.1.4.1.9.9.23.1.2.1.1.6",
}

# ═══════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════

@dataclass
class Device:
    device_id: str
    device_type: str
    vendor: str
    model: str
    ip_address: Optional[str] = None

@dataclass
class Edge:
    source_id: str
    target_id: str
    relationship_type: str
    source_port: str
    target_port: str
    confidence: float = 0.95
    source: str = "lldp"

# ═══════════════════════════════════════════════════════════
# SNMP HELPERS
# ═══════════════════════════════════════════════════════════

async def snmp_get(host: str, community: str, oid: str,
                   timeout: int = 3, retries: int = 1) -> Optional[str]:
    """Single SNMP GET using async API."""
    snmp_engine = SnmpEngine()
    try:
        if USING_LEGACY_PYSNMP:
            # Sync legacy API - wrap in async
            def sync_get():
                from pysnmp.hlapi import getCmd
                g = getCmd(
                    snmp_engine,
                    CommunityData(community, mpModel=1),
                    UdpTransportTarget((host, 161), timeout=timeout, retries=retries),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid))
                )
                return next(g)

            error_indication, error_status, error_index, var_binds = await asyncio.get_event_loop().run_in_executor(
                None, sync_get
            )
        else:
            # Async API
            transport = await UdpTransportTarget.create((host, 161),
                                                       timeout=timeout,
                                                       retries=retries)
            iterator = get_cmd(
                snmp_engine,
                CommunityData(community, mpModel=1),
                transport,
                ContextData(),
                ObjectType(ObjectIdentity(oid))
            )
            error_indication, error_status, error_index, var_binds = await iterator

        if error_indication:
            print(f"  ✗ SNMP error on {host}: {error_indication}")
            return None
        if error_status:
            print(f"  ✗ SNMP status: {error_status.prettyPrint()}")
            return None

        for var_bind in var_binds:
            return str(var_bind[1])
    except Exception as e:
        print(f"  ✗ SNMP exception on {host}: {e}")
        return None
    finally:
        snmp_engine._close()
    return None


async def snmp_walk(host: str, community: str, oid: str,
                    timeout: int = 5, retries: int = 2) -> list:
    """Walk an OID using async API."""
    snmp_engine = SnmpEngine()
    results = []
    try:
        if USING_LEGACY_PYSNMP:
            def sync_walk():
                from pysnmp.hlapi import walkCmd
                w = walkCmd(
                    snmp_engine,
                    CommunityData(community, mpModel=1),
                    UdpTransportTarget((host, 161), timeout=timeout, retries=retries),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid)),
                    lexicographicMode=False
                )
                out = []
                for ei, es, _, vbs in w:
                    if ei or es:
                        break
                    for vb in vbs:
                        out.append((str(vb[0]), str(vb[1])))
                return out

            results = await asyncio.get_event_loop().run_in_executor(
                None, sync_walk
            )
        else:
            transport = await UdpTransportTarget.create((host, 161),
                                                       timeout=timeout,
                                                       retries=retries)
            iterator = walk_cmd(
                snmp_engine,
                CommunityData(community, mpModel=1),
                transport,
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
                lexicographicMode=False
            )
            async for error_indication, error_status, error_index, var_binds in iterator:
                if error_indication:
                    print(f"  ✗ Walk error: {error_indication}")
                    break
                if error_status:
                    break
                for var_bind in var_binds:
                    results.append((str(var_bind[0]), str(var_bind[1])))
    except Exception as e:
        print(f"  ✗ Walk exception on {host}: {e}")
    finally:
        _
    return results


# ═══════════════════════════════════════════════════════════
# DEVICE DISCOVERY
# ═══════════════════════════════════════════════════════════

async def probe_switch(host: str, community: str) -> Optional[Device]:
    """Connect to a switch and gather identity info."""
    sys_descr = await snmp_get(host, community, OIDS["sysDescr"])
    if not sys_descr:
        return None

    sys_name = await snmp_get(host, community, OIDS["sysName"]) or host
    descr_lower = sys_descr.lower()

    if "catalyst" in descr_lower or "router" in descr_lower:
        dtype = "cisco_switch"
    elif "air-" in descr_lower or "accesspoint" in descr_lower:
        dtype = "cisco_ap"
    elif "biostar" in descr_lower or "suprema" in descr_lower:
        dtype = "biostar_server"
    elif "cisco" in descr_lower:
        dtype = "cisco_switch"
    else:
        dtype = "other"

    vendor = "cisco" if "cisco" in descr_lower else "unknown"
    model = sys_descr.split(",")[0].strip()

    print(f"  ✓ {host}: {sys_name} → {dtype}")
    print(f"    Model: {model[:80]}")
    return Device(
        device_id=sys_name,
        device_type=dtype,
        vendor=vendor,
        model=model,
        ip_address=host,
    )


# ═══════════════════════════════════════════════════════════
# NEIGHBOR DISCOVERY
# ═══════════════════════════════════════════════════════════

def _extract_index(oid: str) -> Optional[int]:
    """Extract the trailing index from an OID like '...1.4.1.1.9.5.7'."""
    try:
        return int(oid.rsplit(".", 1)[-1])
    except (ValueError, IndexError):
        return None


async def discover_neighbors(host: str, community: str,
                             sys_name: str) -> list:
    """Discover LLDP + CDP neighbors."""
    edges = []

    # ifIndex → ifName lookup
    if_rows = await snmp_walk(host, community, OIDS["ifName"])
    idx_to_name = {}
    for oid, name in if_rows:
        idx = _extract_index(oid)
        if idx is not None:
            idx_to_name[idx] = name

    # LLDP neighbors
    lldp_rows = await snmp_walk(host, community, OIDS["lldpRemSysName"])
    for oid, neighbor_name in lldp_rows:
        # lldpRemSysName OID: ...1.4.1.1.9.<localPortNum>.<remIndex>
        parts = oid.rsplit(".", 2)
        if len(parts) < 2:
            continue
        try:
            local_port_num = int(parts[-2])
        except ValueError:
            continue
        local_port = idx_to_name.get(local_port_num, f"port-{local_port_num}")
        edges.append(Edge(
            source_id=sys_name,
            target_id=neighbor_name,
            relationship_type="connects_to",
            source_port=local_port,
            target_port="unknown",
            confidence=0.95,
            source="lldp",
        ))

    # CDP neighbors (Cisco-specific)
    cdp_rows = await snmp_walk(host, community, OIDS["cdpCacheDeviceId"])
    for oid, neighbor_name in cdp_rows:
        # cdpCacheDeviceId OID: ...1.2.1.1.6.<ifIndex>.<cdpIndex>
        parts = oid.rsplit(".", 2)
        if len(parts) < 2:
            continue
        try:
            if_index = int(parts[-2])
        except ValueError:
            continue
        local_port = idx_to_name.get(if_index, f"port-{if_index}")
        edges.append(Edge(
            source_id=sys_name,
            target_id=neighbor_name,
            relationship_type="connects_to",
            source_port=local_port,
            target_port="unknown",
            confidence=0.95,
            source="cdp",
        ))

    return edges


# ═══════════════════════════════════════════════════════════
# DATABASE WRITE
# ═══════════════════════════════════════════════════════════

UPSERT_DEVICE = """
MERGE iot.devices AS target
USING (SELECT ? AS device_id) AS src
ON target.device_id = src.device_id
WHEN NOT MATCHED THEN
    INSERT (device_id, device_type, vendor, model, ip_address,
            site_id, status, last_seen, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, 'online', SYSUTCDATETIME(),
            SYSUTCDATETIME(), SYSUTCDATETIME())
WHEN MATCHED THEN
    UPDATE SET last_seen = SYSUTCDATETIME(),
               updated_at = SYSUTCDATETIME();
"""

UPSERT_REL = """
MERGE iot.device_relationships AS target
USING (SELECT ? AS source_id, ? AS target_id, ? AS rel_type) AS src
ON target.source_id = src.source_id
   AND target.target_id = src.target_id
   AND target.relationship_type = src.rel_type
   AND target.valid_to IS NULL
WHEN NOT MATCHED THEN
    INSERT (source_id, target_id, relationship_type, confidence,
            source, inferred, valid_from)
    VALUES (?, ?, ?, 0.95, 'snmp', 0, SYSUTCDATETIME());
"""


def write_to_sql(devices: list, edges: list, dry_run: bool = False):
    if dry_run:
        print(f"\n[DRY RUN] Would write {len(devices)} devices and {len(edges)} edges")
        for d in devices:
            print(f"  Device: {d.device_id:30s} {d.device_type:18s} {d.ip_address}")
        for e in edges[:30]:
            print(f"  Edge:   {e.source_id:30s} --{e.source_port:12s}--> {e.target_id}")
        if len(edges) > 30:
            print(f"  ... and {len(edges) - 30} more edges")
        return

    conn = pyodbc.connect(CONN_STR, autocommit=False)
    cur = conn.cursor()
    try:
        for d in devices:
            cur.execute(UPSERT_DEVICE, (
                d.device_id, d.device_id, d.device_type, d.vendor,
                d.model, d.ip_address, SITE_ID
            ))
        for e in edges:
            cur.execute(UPSERT_REL, (
                e.source_id, e.target_id, e.relationship_type,
                e.source_id, e.target_id, e.relationship_type,
            ))
        conn.commit()
        print(f"\n✓ Wrote {len(devices)} devices and {len(edges)} edges")
    except Exception as ex:
        conn.rollback()
        print(f"\n✗ Database error: {ex}")
        raise
    finally:
        cur._close()
        conn._close()


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

async def run_crawl(args):
    seeds = list(args.seed or [])
    if args.seed_file:
        seeds.extend(
            line.strip() for line in Path(args.seed_file).read_text().splitlines()
            if line.strip() and not line.startswith("#")
        )
    if not seeds:
        print("ERROR: provide --seed IP or --seed-file")
        return

    print(f"Starting crawl with {len(seeds)} seed switch(es)")
    print(f"Community: {args.community}\n")

    all_devices = {}
    all_edges = []
    visited = set()

    for ip in seeds:
        if ip in visited:
            continue
        visited.add(ip)

        print(f"\n[Probing {ip}]")
        device = await probe_switch(ip, args.community)
        if not device:
            continue
        all_devices[device.device_id] = device

        edges = await discover_neighbors(ip, args.community, device.device_id)
        all_edges.extend(edges)
        print(f"  Found {len(edges)} neighbors total")

    # Dedupe edges
    seen = set()
    unique_edges = []
    for e in all_edges:
        key = (e.source_id, e.target_id, e.relationship_type)
        if key not in seen:
            seen.add(key)
            unique_edges.append(e)

    # Add neighbor devices (unknown type until crawled)
    for e in unique_edges:
        if e.target_id not in all_devices:
            all_devices[e.target_id] = Device(
                device_id=e.target_id,
                device_type="other",
                vendor="unknown",
                model="unknown",
            )

    print(f"\n{'='*60}")
    print(f"Discovered: {len(all_devices)} devices, {len(unique_edges)} edges")
    print(f"{'='*60}")

    write_to_sql(list(all_devices.values()), unique_edges, dry_run=args.dry_run)


def main():
    ap = argparse.ArgumentParser(description="SNMP topology crawler")
    ap.add_argument("--community", default="public")
    ap.add_argument("--seed", action="append",
                    help="Switch IP to crawl (repeatable)")
    ap.add_argument("--seed-file", help="File with one IP per line")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    asyncio.run(run_crawl(args))


if __name__ == "__main__":
    main()
