"""SNMP/LLDP/CDP active probing (Tier 1A).

Walks a list of Cisco switches, harvests:
  - lldpRemoteTable   (LLDP-MIB, 1.0.8802.1.1.2.1.4)
  - cdpCacheTable     (CISCO-CDP-MIB, 1.3.6.1.4.1.9.9.23)
  - ifTable           (IF-MIB)
  - dot1dTpFdb        (BRIDGE-MIB)
  - ipNetToMediaTable (IP-MIB, for ARP)

Emits discovered edges of types:
  CONNECTS_TO   (switch port ↔ AP / neighbor)
  HAS_PORT      (switch → port node)
  MEMBER_OF     (port → VLAN)

The merger is responsible for writing these into Postgres + Neo4j.
"""
from __future__ import annotations

import argparse
import dataclasses
import ipaddress
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

import yaml
from pysnmp.hlapi.v3arch.asyncio import (
    SnmpEngine, CommunityData, UsmUserData,
    UdpTransportTarget, ContextData, ObjectType, ObjectIdentity,
    bulk_cmd, get_cmd, next_cmd,
)
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger("topology.discovery.tier1.snmp")


# ── OID constants (numeric, library-agnostic) ─────────────────────────────────
LLDP_REM_SYS_NAME     = "1.0.8802.1.1.2.1.4.1.1.9"     # lldpRemSysName
LLDP_REM_PORT_ID      = "1.0.8802.1.1.2.1.4.1.1.7"     # lldpRemPortId
LLDP_REM_PORT_DESC    = "1.0.8802.1.1.2.1.4.1.1.8"     # lldpRemPortDesc
LLDP_REM_CHASSIS_ID   = "1.0.8802.1.1.2.1.4.1.1.5"     # lldpRemChassisId
LLDP_REM_MAN_ADDR     = "1.0.8802.1.1.2.1.4.1.1.12"    # lldpRemManAddr

CDP_CACHE_ADDR        = "1.3.6.1.4.1.9.9.23.1.2.1.1.4"  # cdpCacheAddress (type 4 = ip)
CDP_CACHE_VERSION     = "1.3.6.1.4.1.9.9.23.1.2.1.1.5"  # cdpCacheVersion
CDP_CACHE_PLATFORM    = "1.3.6.1.4.1.9.9.23.1.2.1.1.7"  # cdpCachePlatform
CDP_CACHE_DEVICE_PORT = "1.3.6.1.4.1.9.9.23.1.2.1.1.7"  # see cdpCacheDevicePort
CDP_CACHE_DEVICE_ID   = "1.3.6.1.4.1.9.9.23.1.2.1.1.6"  # cdpCacheDeviceId
CDP_CACHE_LOCAL_PORT  = "1.3.6.1.4.1.9.9.23.1.2.1.1.3"  # cdpCacheIfIndex (local ifIndex)
CDP_CACHE_PORT_ID     = "1.3.6.1.4.1.9.9.23.1.2.1.1.2"  # remote port id

IF_DESCR              = "1.3.6.1.2.1.2.2.1.2"
IF_ALIAS              = "1.3.6.1.2.1.31.1.1.1.18"     # ifAlias
IF_VLAN               = "1.3.6.1.4.1.9.9.68.1.2.2.1.2"  # vmVlan (Cisco)
BRIDGE_FDB_PORT       = "1.3.6.1.2.1.17.4.3.1.2"      # dot1dTpFdbPort
IP_NET_TO_MEDIA       = "1.3.6.1.2.1.4.22.1.2"         # ipNetToMediaPhysAddress


# ── data classes ──────────────────────────────────────────────────────────────
@dataclass
class SwitchTarget:
    device_id: str
    ip: str
    snmp_version: str = "v2c"
    community: str = "public"
    v3_user: str = ""
    v3_auth_key: str = ""
    v3_priv_key: str = ""


@dataclass
class NeighborRecord:
    local_device_id: str
    local_port: str
    remote_device_id: str
    remote_port: str | None
    remote_ip: str | None
    remote_mac: str | None
    protocol: str                       # "lldp" | "cdp"
    vlan: int | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class FDBRecord:
    switch_device_id: str
    mac: str
    if_index: int
    # best-effort mapped port name by joining ifTable
    port_name: str | None = None
    vlan: int | None = None


@dataclass
class ARPRecord:
    switch_device_id: str
    ip: str
    mac: str
    if_index: int | None = None


# ── SNMP helpers ─────────────────────────────────────────────────────────────
def _auth_data(t: SwitchTarget):
    if t.snmp_version == "v3":
        return UsmUserData(
            t.v3_user,
            authKey=t.v3_auth_key or None,
            privKey=t.v3_priv_key or None,
        )
    return CommunityData(t.community, mpModel=1 if t.snmp_version == "v2c" else 0)


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=0.5, max=2))
def _walk(target: SwitchTarget, base_oid: str) -> Iterator[tuple[str, object]]:
    """Walk a single base OID; yields (full_oid_suffix, value) pairs."""
    engine = SnmpEngine()
    auth = _auth_data(target)
    transport = UdpTransportTarget((target.ip, 161), timeout=2, retries=1)
    ctx = ContextData()

    current_oid = base_oid
    while True:
        errind, errstatus, erridx, vbs = next_cmd(
            engine, auth, transport, ctx,
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


def _ip_to_str(val: str) -> str | None:
    """Convert IpAddress prettyPrint to dotted-quad."""
    if not val:
        return None
    try:
        from pysnmp.proto.rfc1902 import IpAddress
        return str(IpAddress(val))
    except Exception:
        m = re.match(r"(\d+\.\d+\.\d+\.\d+)", val)
        if m:
            return m.group(1)
    return None


def _oid_tail(oid: str, base: str) -> list[int]:
    return [int(x) for x in oid[len(base) + 1:].split(".")]


# ── discovery functions ──────────────────────────────────────────────────────
def discover_lldp(target: SwitchTarget) -> list[NeighborRecord]:
    """Walk LLDP-MIB and return neighbor records."""
    rows: dict[tuple, dict] = {}
    for oid, val in _walk(target, LLDP_REM_SYS_NAME):
        idx = _oid_tail(oid, LLDP_REM_SYS_NAME)
        k = (idx[1], idx[2])
        rows.setdefault(k, {})["sys_name"] = val

    for oid, val in _walk(target, LLDP_REM_PORT_ID):
        idx = _oid_tail(oid, LLDP_REM_PORT_ID)
        k = (idx[1], idx[2])
        rows.setdefault(k, {})["port_id"] = val

    for oid, val in _walk(target, LLDP_REM_CHASSIS_ID):
        idx = _oid_tail(oid, LLDP_REM_CHASSIS_ID)
        k = (idx[1], idx[2])
        rows.setdefault(k, {})["chassis"] = val

    for oid, val in _walk(target, LLDP_REM_MAN_ADDR):
        idx = _oid_tail(oid, LLDP_REM_MAN_ADDR)
        k = (idx[1], idx[2])
        rows.setdefault(k, {})["man_addr"] = _ip_to_str(val)

    if_index_to_name = dict(_walk_if_descr(target))

    out: list[NeighborRecord] = []
    for (if_index, _rem_idx), d in rows.items():
        sys_name = d.get("sys_name") or d.get("chassis") or ""
        if not sys_name:
            continue
        out.append(NeighborRecord(
            local_device_id=target.device_id,
            local_port=if_index_to_name.get(if_index, f"ifIndex{if_index}"),
            remote_device_id=_sanitize_device_id(sys_name),
            remote_port=d.get("port_id"),
            remote_ip=d.get("man_addr"),
            remote_mac=None,
            protocol="lldp",
            raw=d,
        ))
    return out


def discover_cdp(target: SwitchTarget) -> list[NeighborRecord]:
    rows: dict[tuple, dict] = {}
    for oid, val in _walk(target, CDP_CACHE_DEVICE_ID):
        idx = _oid_tail(oid, CDP_CACHE_DEVICE_ID)
        k = (idx[0], idx[1])
        rows.setdefault(k, {})["device"] = val

    for oid, val in _walk(target, CDP_CACHE_ADDR):
        idx = _oid_tail(oid, CDP_CACHE_ADDR)
        k = (idx[0], idx[1])
        rows.setdefault(k, {})["ip"] = _ip_to_str(val)

    for oid, val in _walk(target, CDP_CACHE_PORT_ID):
        idx = _oid_tail(oid, CDP_CACHE_PORT_ID)
        k = (idx[0], idx[1])
        rows.setdefault(k, {})["port"] = val

    for oid, val in _walk(target, CDP_CACHE_PLATFORM):
        idx = _oid_tail(oid, CDP_CACHE_PLATFORM)
        k = (idx[0], idx[1])
        rows.setdefault(k, {})["platform"] = val

    if_index_to_name = dict(_walk_if_descr(target))

    out: list[NeighborRecord] = []
    for (if_index, _dev_idx), d in rows.items():
        device = d.get("device")
        if not device:
            continue
        out.append(NeighborRecord(
            local_device_id=target.device_id,
            local_port=if_index_to_name.get(if_index, f"ifIndex{if_index}"),
            remote_device_id=_sanitize_device_id(device),
            remote_port=d.get("port"),
            remote_ip=d.get("ip"),
            remote_mac=None,
            protocol="cdp",
            raw=d,
        ))
    return out


def _walk_if_descr(target: SwitchTarget) -> Iterable[tuple[int, str]]:
    for oid, val in _walk(target, IF_DESCR):
        idx = _oid_tail(oid, IF_DESCR)
        yield idx[0], val


def discover_fdb(target: SwitchTarget) -> list[FDBRecord]:
    if_index_to_name = dict(_walk_if_descr(target))
    out: list[FDBRecord] = []
    for oid, val in _walk(target, BRIDGE_FDB_PORT):
        idx = _oid_tail(oid, BRIDGE_FDB_PORT)
        mac_bytes = [int(x) for x in val.prettyPrint().split(":")] if hasattr(val, "prettyPrint") else None
        if not mac_bytes or len(mac_bytes) != 6:
            continue
        mac = ":".join(f"{b:02X}" for b in mac_bytes)
        out.append(FDBRecord(
            switch_device_id=target.device_id,
            mac=mac,
            if_index=int(val.prettyPrint()) if hasattr(val, "prettyPrint") else 0,
            port_name=None,
        ))
    return out


def discover_arp(target: SwitchTarget) -> list[ARPRecord]:
    out: list[ARPRecord] = []
    for oid, val in _walk(target, IP_NET_TO_MEDIA):
        idx = _oid_tail(oid, IP_NET_TO_MEDIA)
        if len(idx) < 4:
            continue
        ip = ".".join(str(x) for x in idx[0:4])
        mac = val.prettyPrint() if hasattr(val, "prettyPrint") else str(val)
        out.append(ARPRecord(switch_device_id=target.device_id, ip=ip, mac=mac))
    return out


def _sanitize_device_id(name: str) -> str:
    """Normalize a discovered neighbor name to a stable device_id."""
    if not name:
        return ""
    s = name.strip()
    s = re.sub(r"\..*", "", s)
    s = re.sub(r"[^A-Za-z0-9_\-]", "-", s)
    return s.upper()


# ── CLI entrypoint ────────────────────────────────────────────────────────────
def run(targets: list[SwitchTarget], out_path: Path) -> dict:
    started = time.time()
    result = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "neighbors": [],
        "fdb": [],
        "arp": [],
    }
    for t in targets:
        log.info("probing %s (%s) via LLDP/CDP", t.device_id, t.ip)
        try:
            result["neighbors"].extend(n.__dict__ for n in discover_lldp(t))
            result["neighbors"].extend(n.__dict__ for n in discover_cdp(t))
        except Exception as e:
            log.warning("LLDP/CDP failed for %s: %s", t.device_id, e)
        try:
            result["fdb"].extend(f.__dict__ for f in discover_fdb(t))
        except Exception as e:
            log.warning("FDB failed for %s: %s", t.device_id, e)
        try:
            result["arp"].extend(a.__dict__ for a in discover_arp(t))
        except Exception as e:
            log.warning("ARP failed for %s: %s", t.device_id, e)

    out_path.write_text(json.dumps(result, indent=2, default=str))
    log.info("wrote %d neighbor records to %s", len(result["neighbors"]), out_path)
    return result


def _load_targets(path: Path) -> list[SwitchTarget]:
    raw = yaml.safe_load(path.read_text())

    # Extract target items whether under 'switches', 'devices', or top-level list
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        rows = raw.get("switches") or raw.get("devices") or []
    else:
        rows = []

    valid_fields = {f.name for f in dataclasses.fields(SwitchTarget)}

    targets = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            log.warning("Skipping entry #%d: Not a valid dictionary/object", idx)
            continue

        # Check for mandatory IP field
        ip_addr = row.get("ip")
        if not ip_addr:
            log.warning("Skipping entry #%d (%s): Missing 'ip' address", idx, row.get("name") or row.get("id"))
            continue

        # Ensure device_id is present (fall back to 'name' or 'id')
        if "device_id" not in row or not row["device_id"]:
            row["device_id"] = str(row.get("name") or row.get("id") or f"device_{idx}")

        # Extract only valid dataclass fields, filtering out 'id', 'channel', 'nvr', etc.
        filtered_row = {k: v for k, v in row.items() if k in valid_fields and v is not None}

        targets.append(SwitchTarget(**filtered_row))

    return targets


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", type=Path, required=True, help="YAML list of switches")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    targets = _load_targets(args.targets)
    run(targets, args.out)


if __name__ == "__main__":
    main()