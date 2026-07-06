"""Honeywell panel configuration parser (Tier 1E).

Reads panel config exports (CSV, JSON, or Honeywell-proprietary XML) and
emits canonical device records mapping:
  panel (controller) -> zones (input/output points)
  panel -> receiver / central station (heartbeat)
"""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

log = logging.getLogger("topology.discovery.tier1.honeywell")


@dataclass
class PanelRecord:
    panel_id: str
    name: str
    model: str | None
    site: str | None
    firmware: str | None
    ip_address: str | None
    receiver_endpoint: str | None
    zones: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def parse(export_dir: Path) -> list[PanelRecord]:
    out: list[PanelRecord] = []
    if not export_dir.exists():
        log.warning("Honeywell export dir does not exist: %s", export_dir)
        return out
    for path in sorted(export_dir.iterdir()):
        try:
            if path.suffix.lower() == ".csv":
                out.extend(_parse_csv(path))
            elif path.suffix.lower() == ".json":
                out.extend(_parse_json(path))
            elif path.suffix.lower() in (".xml", ".hxcfg"):
                out.extend(_parse_xml(path))
            else:
                log.debug("Skipping unknown Honeywell export: %s", path)
        except Exception as e:
            log.warning("Failed to parse %s: %s", path, e)
    return out


# ── parsers ──────────────────────────────────────────────────────────────────
def _parse_csv(path: Path) -> list[PanelRecord]:
    """Expected columns: panel_id,name,model,site,firmware,ip,receiver,zone_id,zone_name,zone_type"""
    panels: dict[str, PanelRecord] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            pid = row["panel_id"]
            p = panels.setdefault(pid, PanelRecord(
                panel_id=pid, name=row.get("name", pid),
                model=row.get("model"), site=row.get("site"),
                firmware=row.get("firmware"), ip_address=row.get("ip"),
                receiver_endpoint=row.get("receiver"),
            ))
            if row.get("zone_id"):
                p.zones.append({
                    "zone_id": row["zone_id"],
                    "name": row.get("zone_name"),
                    "type": row.get("zone_type"),
                })
    return list(panels.values())


def _parse_json(path: Path) -> list[PanelRecord]:
    raw = json.loads(path.read_text())
    out: list[PanelRecord] = []
    for p in raw.get("panels", []):
        out.append(PanelRecord(
            panel_id=p["panel_id"],
            name=p.get("name", p["panel_id"]),
            model=p.get("model"),
            site=p.get("site"),
            firmware=p.get("firmware"),
            ip_address=p.get("ip_address"),
            receiver_endpoint=p.get("receiver"),
            zones=p.get("zones", []),
            raw=p,
        ))
    return out


def _parse_xml(path: Path) -> list[PanelRecord]:
    root = ET.parse(path).getroot()
    out: list[PanelRecord] = []
    for p in root.findall(".//Panel"):
        zones = [
            {"zone_id": z.get("id"), "name": z.get("name"), "type": z.get("type")}
            for z in p.findall(".//Zone")
        ]
        out.append(PanelRecord(
            panel_id=p.get("id", ""),
            name=p.get("name", p.get("id", "")),
            model=p.get("model"),
            site=p.get("site"),
            firmware=p.get("firmware"),
            ip_address=p.get("ip"),
            receiver_endpoint=p.get("receiver"),
            zones=zones,
            raw={"xml": p.attrib},
        ))
    return out
