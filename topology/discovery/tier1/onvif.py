"""ONVIF WS-Discovery + GetDeviceInformation (Tier 1C).

UDP multicast probe on 239.255.255.250:3702 to enumerate ONVIF cameras,
then perform an unauthenticated GetDeviceInformation to capture make/model/serial.
Optionally cross-references results with VMS camera-registration logs.
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import struct
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

import requests
from requests.auth import HTTPDigestAuth
from topology.config import load_settings

log = logging.getLogger("topology.discovery.tier1.onvif")


WS_DISCOVERY_PROBE = """<?xml version="1.0" encoding="utf-8"?>
<Envelope xmlns:dn="http://www.onvif.org/ver10/network/wsdl"
          xmlns="http://www.w3.org/2003/05/soap-envelope">
  <Header>
    <wsa:MessageID xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">
      uuid:{uuid}
    </wsa:MessageID>
    <wsa:To xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">
      urn:schemas-xmlsoap-org:ws:2005:04:discovery
    </wsa:To>
    <wsa:Action xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">
      http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe
    </wsa:Action>
  </Header>
  <Body>
    <Probe xmlns="http://schemas.xmlsoap.org/ws/2005/04/discovery">
      <Types>dn:NetworkVideoTransmitter</Types>
    </Probe>
  </Body>
</Envelope>"""


@dataclass
class CameraRecord:
    ip_address: str
    xaddrs: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    manufacturer: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None
    hardware_id: str | None = None
    mac_address: str | None = None
    onvif_uuid: str | None = None
    name: str | None = None
    source: str = "onvif"


def _send_probe(multicast: str, port: int, timeout: float) -> bytes:
    msg = WS_DISCOVERY_PROBE.format(uuid=uuid.uuid4()).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, struct.pack("ff", timeout, 0))
    sock.settimeout(timeout)
    sock.sendto(msg, (multicast, port))
    chunks: list[bytes] = []
    try:
        while True:
            data, _ = sock.recvfrom(65535)
            chunks.append(data)
    except socket.timeout:
        pass
    finally:
        sock.close()
    return b"\n".join(chunks)


# Some stacks use ws-discovery on IPv4 multicast; we also bind to receive unicast
# responses to 0.0.0.0:3702 if the OS allows.
def discover(multicast: str, port: int, timeout: float = 5.0) -> list[CameraRecord]:
    raw = _send_probe(multicast, port, timeout)
    if not raw:
        return []
    cams: list[CameraRecord] = []
    seen: set[str] = set()
    for blob in raw.split(b"</Envelope>"):
        if not blob.strip():
            continue
        try:
            root = ET.fromstring(blob + b"</Envelope>")
        except ET.ParseError:
            continue
        ns = {
            "wsd": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
            "dn":  "http://www.onvif.org/ver10/network/wsdl",
        }
        xaddrs_el = root.find(".//wsd:XAddrs", ns)
        scopes_el = root.find(".//wsd:Scopes", ns)
        if xaddrs_el is None or xaddrs_el.text is None:
            continue
        xaddrs = [x.strip() for x in xaddrs_el.text.split() if x.strip()]
        scopes = scopes_el.text.split() if scopes_el is not None and scopes_el.text else []
        # Pick first IPv4 XAddr
        ip = None
        for x in xaddrs:
            m = re_ip(x)
            if m:
                ip = m
                break
        if not ip or ip in seen:
            continue
        seen.add(ip)
        cams.append(CameraRecord(
            ip_address=ip,
            xaddrs=xaddrs,
            scopes=scopes,
        ))
    return cams


def re_ip(s: str) -> str | None:
    import re
    m = re.search(r"https?://(\d+\.\d+\.\d+\.\d+)", s)
    return m.group(1) if m else None


# ── GetDeviceInformation ─────────────────────────────────────────────────────
GET_DEVICE_INFO_BODY = """<?xml version="1.0" encoding="utf-8"?>
<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope"
          xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
          xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
  <Header>
    <wsa:MessageID xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">uuid:{uuid}</wsa:MessageID>
    <wsa:To xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">{endpoint}</wsa:To>
    <wsa:Action xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">
      http://www.onvif.org/ver10/device/wsdl/GetDeviceInformation
    </wsa:Action>
  </Header>
  <Body>
    <tds:GetDeviceInformation/>
  </Body>
</Envelope>"""


def get_device_info(camera: CameraRecord, *, username: str | None = None, password: str | None = None,
                    timeout: float = 5.0) -> None:
    """Mutate the record in place with manufacturer/model/serial/etc."""
    if not camera.xaddrs:
        return
    endpoint = camera.xaddrs[0].rstrip("/") + "/onvif/device_service"
    body = GET_DEVICE_INFO_BODY.format(uuid=uuid.uuid4(), endpoint=endpoint)
    auth = HTTPDigestAuth(username, password) if username and password else None
    try:
        r = requests.post(endpoint, data=body,
                          headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                          auth=auth, timeout=timeout)
    except requests.RequestException as e:
        log.debug("GetDeviceInformation failed for %s: %s", camera.ip_address, e)
        return
    if r.status_code != 200:
        return
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return
    # ONVIF namespaced fields
    for ns_uri in ("http://www.onvif.org/ver10/device/wsdl",
                   "http://www.onvif.org/ver10/schema"):
        ns = {"tds": ns_uri}
        m = root.find(".//tds:Manufacturer", ns)
        if m is not None and m.text:
            camera.manufacturer = m.text
        m = root.find(".//tds:Model", ns)
        if m is not None and m.text:
            camera.model = m.text
        m = root.find(".//tds:FirmwareVersion", ns)
        if m is not None and m.text:
            camera.firmware_version = m.text
        m = root.find(".//tds:SerialNumber", ns)
        if m is not None and m.text:
            camera.serial_number = m.text
        m = root.find(".//tds:HardwareId", ns)
        if m is not None and m.text:
            camera.hardware_id = m.text


def enrich(cams: Iterable[CameraRecord], *, username: str | None = None, password: str | None = None) -> list[CameraRecord]:
    out = []
    for c in cams:
        get_device_info(c, username=username, password=password)
        out.append(c)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--user", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--timeout", type=float, default=5.0)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = load_settings()
    cams = discover(s.discovery.tier1.onvif.ws_discovery_multicast,
                    s.discovery.tier1.onvif.ws_discovery_port,
                    args.timeout)
    # ONVIF credentials are often not needed for GetDeviceInformation; allow
    # explicit --user/--password or fall back to BioStar creds as a convenience
    # for shared operator accounts.
    cams = enrich(cams,
                  username=args.user or s.discovery.tier1.biostar.username or None,
                  password=args.password or s.discovery.tier1.biostar.password or None)
    args.out.write_text(json.dumps([asdict(c) for c in cams], indent=2))
    log.info("discovered %d ONVIF devices", len(cams))


if __name__ == "__main__":
    main()
