"""Inventory normalization (Stage 1).

Pulls devices from authoritative inventory systems (NetBox, Device42, CMDB CSV)
and normalizes them into the `topology.devices` table.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pynetbox
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from topology.config import load_settings


@dataclass(frozen=True)
class DeviceRecord:
    device_id: str
    device_type: str
    vendor: str | None
    model: str | None
    ip_address: str | None
    mac_address: str | None
    site: str | None
    floor: int | None
    rack: str | None
    status: str
    source_system: str
    raw: dict


def _engine():
    s = load_settings()
    url = f"postgresql+psycopg2://{s.postgres.user}:{s.postgres.password}@{s.postgres.host}:{s.postgres.port}/{s.postgres.database}"
    return sa.create_engine(url, future=True)


def _upsert_devices(records: Iterable[DeviceRecord]) -> int:
    rows = []
    for r in records:
        rows.append({
            "device_id": r.device_id,
            "device_type": r.device_type,
            "vendor": r.vendor,
            "model": r.model,
            "ip_address": r.ip_address,
            "mac_address": r.mac_address,
            "site": r.site,
            "floor": r.floor,
            "rack": r.rack,
            "status": r.status,
            "source_system": r.source_system,
            "raw": json.dumps(r.raw),
        })
    if not rows:
        return 0
    eng = _engine()
    stmt = insert(sa.table("devices", schema="topology")).values(rows)
    update_cols = {c.name: stmt.excluded[c.name] for c in stmt.table.columns if c.name not in {"device_id", "first_seen"}}
    stmt = stmt.on_conflict_do_update(index_elements=["device_id"], set_=update_cols)
    with eng.begin() as cx:
        cx.execute(stmt)
    return len(rows)


def from_netbox(url: str, token: str) -> list[DeviceRecord]:
    nb = pynetbox.api(url, token=token)
    out: list[DeviceRecord] = []
    for d in nb.dcim.devices.all():
        primary_ip = None
        if d.primary_ip:
            primary_ip = d.primary_ip.address.split("/")[0]
        out.append(DeviceRecord(
            device_id=d.name,
            device_type=_map_netbox_role(d.device_role.slug if d.device_role else ""),
            vendor=d.device_type.manufacturer.slug if d.device_type and d.device_type.manufacturer else None,
            model=d.device_type.model if d.device_type else None,
            ip_address=primary_ip,
            mac_address=None,           # NetBox doesn't store MAC on device
            site=d.site.slug if d.site else None,
            floor=None,
            rack=d.rack.name if d.rack else None,
            status=str(d.status) if d.status else "unknown",
            source_system="netbox",
            raw=dict(d.serialize()),
        ))
    return out


def from_csv(path: Path) -> list[DeviceRecord]:
    out: list[DeviceRecord] = []
    with path.open() as f:
        for row in csv.DictReader(f):
            out.append(DeviceRecord(
                device_id=row["device_id"],
                device_type=row["device_type"],
                vendor=row.get("vendor") or None,
                model=row.get("model") or None,
                ip_address=row.get("ip_address") or None,
                mac_address=row.get("mac_address") or None,
                site=row.get("site") or None,
                floor=int(row["floor"]) if row.get("floor") else None,
                rack=row.get("rack") or None,
                status=row.get("status") or "unknown",
                source_system=row.get("source_system") or "manual",
                raw=row,
            ))
    return out


def _map_netbox_role(slug: str) -> str:
    """Map NetBox device role slugs to our canonical device types."""
    return {
        "access-switch": "cisco_switch",
        "core-switch": "cisco_switch",
        "distribution-switch": "cisco_switch",
        "wireless-controller": "cisco_wlc",
        "access-point": "cisco_ap",
        "nvr": "nvr",
        "vms": "vms",
        "camera": "camera",
        "honeywell-panel": "honeywell_panel",
        "biostar-door": "biostar_door",
        "biostar-server": "biostar_server",
    }.get(slug, slug or "unknown")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["netbox", "csv"], required=True)
    ap.add_argument("--netbox-url", default=None)
    ap.add_argument("--netbox-token", default=None)
    ap.add_argument("--csv-path", type=Path, default=None)
    args = ap.parse_args()

    if args.source == "netbox":
        if not (args.netbox_url and args.netbox_token):
            ap.error("--netbox-url and --netbox-token required for source=netbox")
        recs = from_netbox(args.netbox_url, args.netbox_token)
    else:
        if not args.csv_path:
            ap.error("--csv-path required for source=csv")
        recs = from_csv(args.csv_path)

    n = _upsert_devices(recs)
    print(f"Upserted {n} devices into topology.devices")


if __name__ == "__main__":
    main()
