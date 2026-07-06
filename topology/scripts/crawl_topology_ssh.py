"""
SSH-based Topology Crawler → SQL Server
Uses SSH to run 'show' commands on Cisco devices
and parses the output. Works when SNMP is unavailable.

Usage:
    pip install paramiko pyodbc pandas
    python crawl_topology_ssh.py --user admin --password X --seed 10.36.4.1 --dry-run
"""

import argparse
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict

import pyodbc

try:
    import paramiko
    HAVE_PARAMIKO = True
except ImportError:
    HAVE_PARAMIKO = False

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

# ═══════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════

@dataclass
class Device:
    device_id: str
    device_type: str = "other"
    vendor: str = "cisco"
    model: str = "unknown"
    ip_address: Optional[str] = None

@dataclass
class Edge:
    source_id: str
    target_id: str
    relationship_type: str = "connects_to"
    source_port: str = "unknown"
    target_port: str = "unknown"
    confidence: float = 0.95
    source: str = "ssh_lldp"

# ═══════════════════════════════════════════════════════════
# SSH CLIENT
# ═══════════════════════════════════════════════════════════

class CiscoSSH:
    """Runs commands on Cisco IOS devices via SSH."""

    def __init__(self, host: str, username: str, password: str,
                 enable_password: Optional[str] = None, timeout: int = 10):
        self.host = host
        self.username = username
        self.password = password
        self.enable_password = enable_password
        self.timeout = timeout
        self.client = None
        self.shell = None

    def __enter__(self):
        if not HAVE_PARAMIKO:
            raise RuntimeError("Run: pip install paramiko")
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            self.host, username=self.username, password=self.password,
            timeout=self.timeout, look_for_keys=False, allow_agent=False
        )
        self.shell = self.client.invoke_shell()
        self.shell.settimeout(self.timeout)
        # Disable paging so output isn't cut off
        self._send("terminal length 0")
        return self

    def __exit__(self, *args):
        if self.client:
            self.client.close()

    def _send(self, cmd: str) -> str:
        """Send command, wait, return output."""
        self.shell.send(cmd + "\n")
        import time
        time.sleep(1.5)
        # Read all available output
        output = b""
        while self.shell.recv_ready():
            output += self.shell.recv(65535)
        return output.decode("utf-8", errors="ignore")

    def run(self, cmd: str) -> str:
        return self._send(cmd)

# ═══════════════════════════════════════════════════════════
# PARSERS for Cisco 'show' output
# ═══════════════════════════════════════════════════════════

def parse_show_version(output: str) -> dict:
    """Parse 'show version' output."""
    info = {"hostname": None, "model": None, "version": None}
    for line in output.splitlines():
        line = line.strip()
        # hostname line varies by IOS version
        m = re.search(r"(\S+)\s+uptime is", line)
        if m and not info["hostname"]:
            info["hostname"] = m.group(1)
        # Model: "cisco WS-C2960-24TT-L..."
        m = re.match(r"(cisco\s+\S+)", line, re.IGNORECASE)
        if m and not info["model"]:
            info["model"] = m.group(1)
        # IOS version
        m = re.search(r"Version\s+(\S+),", line)
        if m and not info["version"]:
            info["version"] = m.group(1)
    return info

def parse_show_lldp_neighbors(output: str) -> list:
    """
    Parse 'show lldp neighbors' output.
    Example format:
        Device ID           Local Intf     Hold-time  Capability  Port ID
        SW-CORE-08          Gi0/1          120        R           Gi0/3
        AP-FL3-012          Gi0/5          120        B           eth0
    """
    edges = []
    lines = output.splitlines()
    # Find header line
    header_idx = None
    for i, line in enumerate(lines):
        if "Device ID" in line and "Local Intf" in line:
            header_idx = i
            break
    if header_idx is None:
        return edges

    for line in lines[header_idx + 1:]:
        line = line.strip()
        if not line or line.startswith("Total"):
            break
        # Split by whitespace; format varies but usually:
        #   Device-ID  Local-Intf  Hold  Capability  Port-ID
        parts = line.split()
        if len(parts) >= 4:
            device_id = parts[0]
            local_intf = parts[1]
            # Port ID is usually the last field
            remote_port = parts[-1]
            edges.append({
                "neighbor_name": device_id,
                "local_port": local_intf,
                "remote_port": remote_port,
                "method": "lldp",
            })
    return edges

def parse_show_cdp_neighbors(output: str) -> list:
    """
    Parse 'show cdp neighbors' output.
    """
    edges = []
    lines = output.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "Device ID" in line and "Local Intrfce" in line:
            header_idx = i
            break
    if header_idx is None:
        return edges

    for line in lines[header_idx + 1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 4:
            device_id = parts[0].split(".")[0]   # strip domain
            local_intf = parts[1]
            remote_port = parts[-1]
            # Capability codes are in middle; skip them
            edges.append({
                "neighbor_name": device_id,
                "local_port": local_intf,
                "remote_port": remote_port,
                "method": "cdp",
            })
    return edges

def parse_show_mac_address_table(output: str) -> list:
    """
    Parse 'show mac address-table' output.
    """
    entries = []
    for line in output.splitlines():
        # Typical format:
        #   vlan   mac-address       type    ports
        #   1      001a.2b3c.4d5e    DYNAMIC Gi0/3
        m = re.match(
            r"\s*(\d+)\s+([0-9a-fA-F.]+)\s+\S+\s+(\S+)",
            line
        )
        if m:
            vlan, mac_raw, port = m.groups()
            # Convert Cisco-style MAC to standard
            mac = mac_raw.replace(".", "").lower()
            if len(mac) == 12:
                mac = ":".join(mac[i:i+2] for i in range(0, 12, 2))
            entries.append({
                "vlan": vlan,
                "mac": mac,
                "port": port,
            })
    return entries

def parse_show_arp(output: str) -> list:
    """
    Parse 'show arp' output.
    """
    entries = []
    for line in output.splitlines():
        # Format:
        #   Protocol  Address          Age (min)  Hardware Addr   Type   Interface
        #   Internet  10.10.5.21             42   001a.2b3c.4d5e  ARPA   Vlan105
        m = re.search(
            r"Internet\s+(\d+\.\d+\.\d+\.\d+)\s+\d+\s+([0-9a-fA-F.]+).*?(\S+)\s*$",
            line
        )
        if m:
            ip, mac_raw, interface = m.groups()
            mac = mac_raw.replace(".", "").lower()
            if len(mac) == 12:
                mac = ":".join(mac[i:i+2] for i in range(0, 12, 2))
            entries.append({
                "ip": ip,
                "mac": mac,
                "interface": interface,
            })
    return entries

# ═══════════════════════════════════════════════════════════
# DISCOVERY via SSH
# ═══════════════════════════════════════════════════════════

async def discover_via_ssh(host: str, username: str, password: str,
                            enable_password: Optional[str] = None) -> dict:
    """Run all show commands and return parsed results."""
    results = {"devices": [], "edges": [], "macs": [], "arps": []}

    try:
        with CiscoSSH(host, username, password, enable_password) as ssh:
            # 1. Identity
            ver_output = ssh.run("show version")
            ver = parse_show_version(ver_output)
            hostname = ver.get("hostname") or host
            print(f"  ✓ Identity: {hostname} ({ver.get('model', 'unknown')[:50]})")
            print(f"    IOS: {ver.get('version', 'unknown')}")

            results["devices"].append(Device(
                device_id=hostname,
                device_type="cisco_switch",
                vendor="cisco",
                model=ver.get("model") or "cisco_ios",
                ip_address=host,
            ))

            # 2. LLDP neighbors
            lldp_output = ssh.run("show lldp neighbors")
            lldp_entries = parse_show_lldp_neighbors(lldp_output)
            print(f"  ✓ LLDP: {len(lldp_entries)} neighbors")

            for entry in lldp_entries:
                results["edges"].append(Edge(
                    source_id=hostname,
                    target_id=entry["neighbor_name"],
                    source_port=entry["local_port"],
                    target_port=entry["remote_port"],
                    source="ssh_lldp",
                ))

            # 3. CDP neighbors
            cdp_output = ssh.run("show cdp neighbors")
            cdp_entries = parse_show_cdp_neighbors(cdp_output)
            print(f"  ✓ CDP: {len(cdp_entries)} neighbors")

            for entry in cdp_entries:
                results["edges"].append(Edge(
                    source_id=hostname,
                    target_id=entry["neighbor_name"],
                    source_port=entry["local_port"],
                    target_port=entry["remote_port"],
                    source="ssh_cdp",
                ))

            # 4. MAC address table
            mac_output = ssh.run("show mac address-table")
            macs = parse_show_mac_address_table(mac_output)
            print(f"  ✓ MAC table: {len(macs)} entries")

            for m in macs:
                results["macs"].append({
                    "switch": hostname,
                    "port": m["port"],
                    "mac": m["mac"],
                    "vlan": m["vlan"],
                })

            # 5. ARP table
            arp_output = ssh.run("show arp")
            arps = parse_show_arp(arp_output)
            print(f"  ✓ ARP table: {len(arps)} entries")

            for a in arps:
                results["arps"].append({
                    "switch": hostname,
                    "interface": a["interface"],
                    "ip": a["ip"],
                    "mac": a["mac"],
                })

    except Exception as e:
        print(f"  ✗ SSH error: {e}")
        return results

    return results

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
    UPDATE SET last_seen = SYSUTCDATETIME(), updated_at = SYSUTCDATETIME();
"""

UPSERT_REL = """
MERGE iot.device_relationships AS target
USING (SELECT ? AS sid, ? AS tid, ? AS rt) AS src
ON target.source_id = src.sid
   AND target.target_id = src.tid
   AND target.relationship_type = src.rt
   AND target.valid_to IS NULL
WHEN NOT MATCHED THEN
    INSERT (source_id, target_id, relationship_type, confidence,
            source, inferred, valid_from)
    VALUES (?, ?, ?, 0.95, ?, 0, SYSUTCDATETIME());
"""

def write_to_sql(devices, edges, macs, arps, dry_run=False):
    print(f"\n{'='*60}")
    print(f"Totals: {len(devices)} devices, {len(edges)} edges, "
          f"{len(macs)} MACs, {len(arps)} ARPs")
    print(f"{'='*60}")

    if dry_run:
        print("\n[DRY RUN] Devices:")
        for d in devices:
            print(f"  {d.device_id:30s} {d.device_type:18s} {d.ip_address}")
        print("\n[DRY RUN] Edges:")
        for e in edges[:30]:
            print(f"  {e.source_id:30s} --[{e.source_port:12s}]--> "
                  f"{e.target_id:30s} ({e.source})")
        print("\n[DRY RUN] Sample MACs:")
        for m in macs[:10]:
            print(f"  Port {m['port']:12s} MAC {m['mac']}")
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
                e.source,
            ))
        conn.commit()
        print(f"\n✓ Wrote {len(devices)} devices and {len(edges)} edges")
    except Exception as ex:
        conn.rollback()
        print(f"\n✗ DB error: {ex}")
        raise
    finally:
        cur.close()
        conn.close()

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="SSH-based topology crawler")
    ap.add_argument("--user", required=True, help="SSH username")
    ap.add_argument("--password", required=True, help="SSH password")
    ap.add_argument("--enable", help="Enable password (optional)")
    ap.add_argument("--seed", action="append", help="Switch IP (repeatable)")
    ap.add_argument("--seed-file", help="File with one IP per line")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    seeds = list(args.seed or [])
    if args.seed_file:
        seeds.extend(line.strip() for line in Path(args.seed_file).read_text().splitlines()
                     if line.strip() and not line.startswith("#"))

    if not seeds:
        print("ERROR: provide --seed IP or --seed-file")
        return

    print(f"Crawling {len(seeds)} device(s) via SSH as {args.user}\n")

    all_devices = []
    all_edges = []
    all_macs = []
    all_arps = []

    for ip in seeds:
        print(f"\n=== Probing {ip} ===")
        results = asyncio.run(discover_via_ssh(
            ip, args.user, args.password, args.enable
        )) if False else discover_via_ssh_sync(
            ip, args.user, args.password, args.enable
        )
        all_devices.extend(results["devices"])
        all_edges.extend(results["edges"])
        all_macs.extend(results["macs"])
        all_arps.extend(results["arps"])

    write_to_sql(all_devices, all_edges, all_macs, all_arps, dry_run=args.dry_run)


def discover_via_ssh_sync(host, username, password, enable_password):
    """Sync wrapper that calls the async discovery function."""
    return asyncio.run(discover_via_ssh(host, username, password, enable_password))


if __name__ == "__main__":
    import asyncio
    main()
