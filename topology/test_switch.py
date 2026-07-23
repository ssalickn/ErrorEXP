from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    next_cmd,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("snmp.test")

# ── Corrected OID Constants ──────────────────────────────────────────────────
LLDP_REM_SYS_NAME   = "1.0.8802.1.1.2.1.4.1.1.9"
LLDP_REM_PORT_ID    = "1.0.8802.1.1.2.1.4.1.1.7"
LLDP_REM_MAN_ADDR   = "1.0.8802.1.1.2.1.4.1.1.12"

CDP_CACHE_DEVICE_ID = "1.3.6.1.4.1.9.9.23.1.2.1.1.6"
CDP_CACHE_PORT_ID   = "1.3.6.1.4.1.9.9.23.1.2.1.1.2"
CDP_CACHE_ADDR      = "1.3.6.1.4.1.9.9.23.1.2.1.1.4"

IF_DESCR            = "1.3.6.1.2.1.2.2.1.2"
BRIDGE_FDB_PORT     = "1.3.6.1.2.1.17.4.3.1.2"
IP_NET_TO_MEDIA     = "1.3.6.1.2.1.4.22.1.2"


# ── Data Structures ─────────────────────────────────────────────────────────
@dataclass
class SwitchTarget:
    device_id: str
    ip: str
    community: str = "public"


@dataclass
class NeighborRecord:
    local_device_id: str
    local_port: str
    remote_device_id: str
    remote_port: str | None
    remote_ip: str | None
    protocol: str


@dataclass
class FDBRecord:
    switch_device_id: str
    mac: str
    bridge_port: int
    port_name: str | None = None


# ── SNMP Walk Engine ────────────────────────────────────────────────────────
def _oid_tail(oid: str, base: str) -> list[int]:
    return [int(x) for x in oid[len(base) + 1 :].split(".")]


async def _walk(target: SwitchTarget, base_oid: str) -> AsyncIterator[tuple[str, object]]:
    engine = SnmpEngine()
    auth = CommunityData(target.community, mpModel=1)  # v2c
    # ✅ FIX: Await the async create call
    transport = await UdpTransportTarget.create((target.ip, 161), timeout=2, retries=1)
    ctx = ContextData()

    current_oid = base_oid
    while True:
        # ✅ FIX: Await next_cmd coroutine
        errind, errstatus, erridx, vbs = await next_cmd(
            engine,
            auth,
            transport,
            ctx,
            ObjectType(ObjectIdentity(current_oid)),
            lexicographicMode=False,
        )
        if errind or errstatus:
            break
        for vb in vbs:
            oid, val = vb
            full = str(oid)
            if not full.startswith(base_oid):
                return
            yield full, val.prettyPrint() if hasattr(val, "prettyPrint") else val
            current_oid = full
        if not vbs:
            break


async def _get_if_descriptions(target: SwitchTarget) -> dict[int, str]:
    mapping = {}
    async for oid, val in _walk(target, IF_DESCR):
        idx = _oid_tail(oid, IF_DESCR)
        if idx:
            mapping[idx[0]] = str(val)
    return mapping


# ── Discovery Functions ──────────────────────────────────────────────────────
async def discover_lldp(target: SwitchTarget, if_map: dict[int, str]) -> list[NeighborRecord]:
    rows: dict[tuple, dict] = {}

    async for oid, val in _walk(target, LLDP_REM_SYS_NAME):
        idx = _oid_tail(oid, LLDP_REM_SYS_NAME)
        if len(idx) >= 3:
            rows.setdefault((idx[1], idx[2]), {})["sys_name"] = str(val)

    async for oid, val in _walk(target, LLDP_REM_PORT_ID):
        idx = _oid_tail(oid, LLDP_REM_PORT_ID)
        if len(idx) >= 3:
            rows.setdefault((idx[1], idx[2]), {})["port_id"] = str(val)

    async for oid, val in _walk(target, LLDP_REM_MAN_ADDR):
        idx = _oid_tail(oid, LLDP_REM_MAN_ADDR)
        if len(idx) >= 3:
            rows.setdefault((idx[1], idx[2]), {})["man_addr"] = str(val)

    out = []
    for (if_index, _), d in rows.items():
        sys_name = d.get("sys_name", "").strip()
        if not sys_name:
            continue
        out.append(
            NeighborRecord(
                local_device_id=target.device_id,
                local_port=if_map.get(if_index, f"ifIndex-{if_index}"),
                remote_device_id=sys_name,
                remote_port=d.get("port_id"),
                remote_ip=d.get("man_addr"),
                protocol="lldp",
            )
        )
    return out


async def discover_cdp(target: SwitchTarget, if_map: dict[int, str]) -> list[NeighborRecord]:
    rows: dict[tuple, dict] = {}

    async for oid, val in _walk(target, CDP_CACHE_DEVICE_ID):
        idx = _oid_tail(oid, CDP_CACHE_DEVICE_ID)
        if len(idx) >= 2:
            rows.setdefault((idx[0], idx[1]), {})["device"] = str(val)

    async for oid, val in _walk(target, CDP_CACHE_PORT_ID):
        idx = _oid_tail(oid, CDP_CACHE_PORT_ID)
        if len(idx) >= 2:
            rows.setdefault((idx[0], idx[1]), {})["port"] = str(val)

    async for oid, val in _walk(target, CDP_CACHE_ADDR):
        idx = _oid_tail(oid, CDP_CACHE_ADDR)
        if len(idx) >= 2:
            rows.setdefault((idx[0], idx[1]), {})["ip"] = str(val)

    out = []
    for (if_index, _), d in rows.items():
        device = d.get("device", "").strip()
        if not device:
            continue
        out.append(
            NeighborRecord(
                local_device_id=target.device_id,
                local_port=if_map.get(if_index, f"ifIndex-{if_index}"),
                remote_device_id=device,
                remote_port=d.get("port"),
                remote_ip=d.get("ip"),
                protocol="cdp",
            )
        )
    return out


async def discover_fdb(target: SwitchTarget, if_map: dict[int, str]) -> list[FDBRecord]:
    out = []
    async for oid, val in _walk(target, BRIDGE_FDB_PORT):
        idx = _oid_tail(oid, BRIDGE_FDB_PORT)
        if len(idx) < 6:
            continue

        mac_bytes = idx[-6:]
        mac = ":".join(f"{b:02X}" for b in mac_bytes)

        try:
            b_port = int(val)
        except ValueError:
            continue

        out.append(
            FDBRecord(
                switch_device_id=target.device_id,
                mac=mac,
                bridge_port=b_port,
                port_name=if_map.get(b_port),
            )
        )
    return out


# ── Main Runner ─────────────────────────────────────────────────────────────
async def main():
    target = SwitchTarget(device_id="SWITCH_10_36_0_91", ip="10.36.0.91", community="public")

    log.info("Starting SNMP harvest test on %s (%s)...", target.device_id, target.ip)

    # 1. Interfaces
    log.info("Fetching Interface Descriptions...")
    if_map = await _get_if_descriptions(target)
    log.info("Discovered %d interface entries.", len(if_map))

    # 2. Neighbors (LLDP & CDP)
    log.info("Fetching LLDP Neighbors...")
    lldp_neighbors = await discover_lldp(target, if_map)

    log.info("Fetching CDP Neighbors...")
    cdp_neighbors = await discover_cdp(target, if_map)

    # 3. MAC Address Table (FDB)
    log.info("Fetching Bridge FDB (MAC Address Table)...")
    fdb_records = await discover_fdb(target, if_map)

    # Compile JSON Output
    results = {
        "target": target.__dict__,
        "summary": {
            "lldp_neighbors_found": len(lldp_neighbors),
            "cdp_neighbors_found": len(cdp_neighbors),
            "mac_fdb_entries_found": len(fdb_records),
        },
        "neighbors": [n.__dict__ for n in (lldp_neighbors + cdp_neighbors)],
        "fdb": [f.__dict__ for f in fdb_records],
    }

    out_file = Path("test_harvest_10_36_0_91.json")
    out_file.write_text(json.dumps(results, indent=2))

    print("\n" + "=" * 50)
    print("HARVEST TEST COMPLETE")
    print("=" * 50)
    print(f"LLDP Neighbors Discovered: {len(lldp_neighbors)}")
    print(f"CDP Neighbors Discovered:  {len(cdp_neighbors)}")
    print(f"MAC Table (FDB) Entries:   {len(fdb_records)}")
    print(f"Results written to:        {out_file.resolve()}\n")


if __name__ == "__main__":
    asyncio.run(main())