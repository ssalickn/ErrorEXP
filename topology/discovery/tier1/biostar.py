"""Suprema BioStar 2 local API client (Tier 1D).

Uses the BioStar 2 local REST API (https://bs2_local_api) to enumerate:
  - /api/devices          : controllers
  - /api/doors            : doors and their parent device
  - /api/connectors       : connectors / readers
  - /api/zone             : access zones (door groupings)
  - /api/event_log        : (used by Tier 2 for inference)

Authentication: login → session token; then per-request `bs-session-id` header.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Iterator

import requests
from requests.adapters import HTTPAdapter
from tenacity import retry, stop_after_attempt, wait_exponential

from topology.config import load_settings

log = logging.getLogger("topology.discovery.tier1.biostar")


@dataclass
class DoorRecord:
    door_id: str
    name: str
    device_id: str            # parent controller device_id
    device_name: str
    server_id: str | None = None
    site: str | None = None
    raw: dict = field(default_factory=dict)


class BioStarClient:
    def __init__(self, base_url: str | None = None, username: str | None = None, password: str | None = None,
                 verify: bool = False, timeout: float = 5.0):
        s = load_settings()
        self.base_url = (base_url or s.discovery.tier1.biostar.base_url).rstrip("/")
        self.username = username or s.discovery.tier1.biostar.username
        self.password = password or s.discovery.tier1.biostar.password
        self.timeout = timeout
        self.session = requests.Session()
        self.session.verify = verify
        self.session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=4))
        self.token: str | None = None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=3))
    def login(self) -> None:
        r = self.session.post(
            f"{self.base_url}/api/login",
            json={"User": {"user_id": self.username, "password": self.password}},
            timeout=self.timeout,
        )
        r.raise_for_status()
        body = r.json()
        # BioStar returns { "User": { ... }, "bs-session-id": "..." } in header; also in body
        self.token = r.headers.get("bs-session-id") or body.get("bs-session-id") or body.get("session_id")
        if not self.token:
            raise RuntimeError("BioStar login did not return a session id")
        self.session.headers.update({"bs-session-id": self.token})

    def logout(self) -> None:
        try:
            self.session.post(f"{self.base_url}/api/logout", timeout=self.timeout)
        except Exception:
            pass
        self.token = None

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.token:
            self.login()
        r = self.session.get(f"{self.base_url}{path}", params=params or {}, timeout=self.timeout)
        if r.status_code == 401:
            self.login()
            r = self.session.get(f"{self.base_url}{path}", params=params or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def list_devices(self) -> list[dict]:
        """Iterate all controllers/devices with paging."""
        return list(self._paged("/api/devices"))

    def list_doors(self) -> list[dict]:
        return list(self._paged("/api/doors"))

    def list_connectors(self) -> list[dict]:
        return list(self._paged("/api/connectors"))

    def _paged(self, path: str, page_size: int = 100) -> Iterator[dict]:
        offset = 0
        while True:
            data = self._get(path, {"limit": page_size, "offset": offset})
            rows = data.get("DeviceCollection") or data.get("DoorCollection") or data.get("ConnectorCollection") or []
            if not rows:
                break
            for row in rows:
                yield row
            if len(rows) < page_size:
                break
            offset += page_size


def discover() -> list[DoorRecord]:
    """High-level helper: connect, enumerate doors, return canonical records."""
    s = load_settings()
    client = BioStarClient()
    try:
        client.login()
    except Exception as e:
        log.error("BioStar login failed: %s", e)
        return []
    try:
        # Index controllers by id
        controllers = {c.get("id"): c for c in client.list_devices()}
        server_id = s.discovery.tier1.biostar.base_url  # treat server URL as canonical id
        out: list[DoorRecord] = []
        for d in client.list_doors():
            dev = d.get("device_id") or {}
            controller = controllers.get(dev.get("id"), {})
            out.append(DoorRecord(
                door_id=f"DOOR-{d.get('id')}",
                name=d.get("name") or d.get("door_name") or "",
                device_id=f"BIOC-{dev.get('id') or controller.get('id')}",
                device_name=dev.get("name") or controller.get("name") or "",
                server_id=server_id,
                site=controller.get("building") or controller.get("site"),
                raw=d,
            ))
        return out
    finally:
        client.logout()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for d in discover():
        print(f"{d.door_id:18} {d.name:30} -> {d.device_id} ({d.device_name})")
