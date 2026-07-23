import os
import re
import sys
import platform
import subprocess
import pyodbc
from concurrent.futures import ThreadPoolExecutor

# Connection string for your database
CONN_STR = (
)

RAW_SWITCH_DATA = """
"""

def parse_switches(text: str) -> list[dict]:
    """Parses concatenated string into IP and Device Name dictionaries."""
    pattern = re.compile(r"(10\.36\.\d{1,3}\.\d{1,3})(.+?)(?=(?:10\.36\.\d{1,3}\.\d{1,3}|$))", re.DOTALL)
    matches = pattern.findall(text)
    
    devices = []
    for ip, name in matches:
        devices.append({
            "ip_address": ip.strip(),
            "device_name": name.strip(),
            "device_type": "network_switch"
        })
    return devices


def ping_ip(ip: str, timeout: int = 2) -> bool:
    """Pings an IP address and returns True if online, False otherwise."""
    param = "-n" if platform.system().lower() == "windows" else "-c"
    timeout_flag = "-w" if platform.system().lower() == "windows" else "-W"
    timeout_val = str(timeout * 1000) if platform.system().lower() == "windows" else str(timeout)

    command = ["ping", param, "1", timeout_flag, timeout_val, ip]

    try:
        output = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output.returncode == 0
    except Exception:
        return False


def test_device_status(device: dict) -> dict:
    """Worker function for pinging concurrently."""
    ip = device["ip_address"]
    device["status"] = "online" if ping_ip(ip) else "offline"
    return device

import os
import re
import sys
import platform
import subprocess
import pyodbc
from concurrent.futures import ThreadPoolExecutor

CONN_STR = (
)

RAW_SWITCH_DATA = """
"""

def parse_switches(text: str) -> list[dict]:
    pattern = re.compile(r"(10\.36\.\d{1,3}\.\d{1,3})(.+?)(?=(?:10\.36\.\d{1,3}\.\d{1,3}|$))", re.DOTALL)
    matches = pattern.findall(text)
    
    devices = []
    seen_ids = set()

    for ip, name in matches:
        clean_ip = ip.strip()
        clean_name = name.strip()
        
        if not clean_name:
            clean_name = f"Switch_{clean_ip}"
            
        # Ensure device_id is unique across the batch if identical names exist
        dev_id = clean_name
        if dev_id in seen_ids:
            dev_id = f"{clean_name} ({clean_ip})"
            
        seen_ids.add(dev_id)

        devices.append({
            "ip_address": clean_ip,
            "device_name": dev_id
        })
    return devices


def ping_ip(ip: str, timeout: int = 2) -> bool:
    param = "-n" if platform.system().lower() == "windows" else "-c"
    timeout_flag = "-w" if platform.system().lower() == "windows" else "-W"
    timeout_val = str(timeout * 1000) if platform.system().lower() == "windows" else str(timeout)

    command = ["ping", param, "1", timeout_flag, timeout_val, ip]

    try:
        output = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output.returncode == 0
    except Exception:
        return False


def test_device_status(device: dict) -> dict:
    ip = device["ip_address"]
    device["status"] = "online" if ping_ip(ip) else "offline"
    return device


def sync_to_database(devices: list[dict]) -> None:
    device_type = "cisco_switch"

    # Match ON device_id (Primary Key) to perform a proper Upsert
    upsert_sql = """
    MERGE INTO iot.devices AS target
    USING (VALUES (?, ?, ?, ?)) AS source (device_id, ip_address, device_type, status)
    ON target.device_id = source.device_id
    WHEN MATCHED THEN
        UPDATE SET 
            ip_address = source.ip_address,
            status = source.status,
            device_type = COALESCE(source.device_type, target.device_type),
            updated_at = SYSUTCDATETIME(),
            last_seen = SYSUTCDATETIME()
    WHEN NOT MATCHED THEN
        INSERT (
            device_id, 
            ip_address, 
            device_type,
            status, 
            last_seen, 
            created_at, 
            updated_at
        ) 
        VALUES (
            source.device_id, 
            source.ip_address, 
            source.device_type,
            source.status, 
            SYSUTCDATETIME(), 
            SYSUTCDATETIME(), 
            SYSUTCDATETIME()
        );
    """

    print("-- Connecting to InfrastructureMonitorDB...", file=sys.stderr)
    
    # Sequence matching: (device_id, ip_address, device_type, status)
    param_data = [
        (d["device_name"], d["ip_address"], device_type, d["status"]) 
        for d in devices
    ]

    try:
        with pyodbc.connect(CONN_STR) as conn:
            with conn.cursor() as cursor:
                cursor.fast_executemany = True
                cursor.executemany(upsert_sql, param_data)
                conn.commit()
                print(f"[✓] Database sync completed for {len(devices)} switch records.", file=sys.stderr)
                
    except Exception as e:
        print(f"[X] Database sync failed: {e}", file=sys.stderr)


def main():
    devices = parse_switches(RAW_SWITCH_DATA)
    print(f"Parsed {len(devices)} switch addresses.", file=sys.stderr)
    print("Pinging all devices concurrently...", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=30) as executor:
        results = list(executor.map(test_device_status, devices))

    sync_to_database(results)


if __name__ == "__main__":
    main()