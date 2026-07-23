import os
import re
import sys
import platform
import subprocess
import pyodbc
from concurrent.futures import ThreadPoolExecutor

# Connection string with your exact server details
CONN_STR = (

)

# Raw string directly from Genetec / NVR export
RAW_CAMERA_DATA = """
"""

def parse_cameras(text: str) -> list[dict]:
    """Parses text ensuring full IP addresses (10.x.x.x) are isolated."""
    pattern = re.compile(
        r"(.+?)(Digital (?:PTZ|Fixed) Camera)(NI-MPNVR-\d+|Warehouse NVR)(10\.\d{1,3}\.\d{1,3}\.\d{1,3})#(UDP|TCP|HTTPS)"
    )
    matches = pattern.findall(text)
    
    devices = []
    for match in matches:
        devices.append({
            "device_name": match[0].strip(),
            "device_type": match[1].strip(),
            "nvr": match[2].strip(),
            "ip_address": match[3].strip(),
            "protocol": match[4].strip()
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
    """Worker function for concurrent pinging."""
    ip = device["ip_address"]
    device["status"] = "online" if ping_ip(ip) else "offline"
    return device


def update_db_devices(devices: list[dict]) -> None:
    """Executes UPDATE SQL directly against the DB for pre-existing IPs."""
    # Strictly UPDATES records where ip_address already exists.
    update_sql = """
        UPDATE iot.devices
        SET status = ?,
            last_seen = SYSUTCDATETIME()
        WHERE ip_address = ?;
    """
    
    # Structure data array as tuples: (status, ip_address)
    payload = [(d["status"], d["ip_address"]) for d in devices]

    try:
        print("-- Connecting to database 'InfrastructureMonitorDB'...", file=sys.stderr)
        with pyodbc.connect(CONN_STR) as conn:
            with conn.cursor() as cursor:
                cursor.executemany(update_sql, payload)
                conn.commit()
                print(f"-- Successfully updated {cursor.rowcount} camera records.", file=sys.stderr)
    except Exception as e:
        print(f"FAILED to update database: {e}", file=sys.stderr)


def main():
    devices = parse_cameras(RAW_CAMERA_DATA)
    print(f"-- Successfully parsed {len(devices)} cameras.", file=sys.stderr)
    print(f"-- Pinging all devices concurrently...", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=30) as executor:
        results = list(executor.map(test_device_status, devices))

    # Perform UPDATE queries against the DB directly
    update_db_devices(results)


if __name__ == "__main__":
    main()