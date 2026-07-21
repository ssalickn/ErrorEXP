"""
Genetec camera export parser.

Generates UPSERT statements for iot.devices:
- If a row with the same ip_address already exists, UPDATE its fields
- Otherwise INSERT a new row

Also inserts static log entries for 'Camera is not added to IPEngine' so
the LLM can see them in recent_events.

Run: python parse_genetec_export.py > updates.sql
"""
import re
import sys
from pathlib import Path

INPUT_FILES = [Path("note.txt"), Path("note 1.txt"), Path("note1.txt")]

VENDOR_BY_MODEL = {
    "HC60WB5R2": "Honeywell", "HC60WB5R2P": "Honeywell",
    "HC60W45R2": "Honeywell", "HC60W35R2": "Honeywell",
    "HC30WF5R1": "Honeywell", "H3W2GR1V": "Honeywell",
    "H4W4GR1V": "Honeywell", "HBW2GR1V": "Honeywell",
    "HBW2GR3V": "Honeywell", "HBW4GR1": "Honeywell",
    "HBW4PR1": "Honeywell", "HDZ302DE": "Honeywell",
    "HC60WZ2E30": "Honeywell",
}

MODEL_FAMILY = {
    "HC60WB5R2": "Performance IP Camera",
    "HC60WB5R2P": "Performance IP Camera (Pendant)",
    "HC60W45R2": "Performance IP Camera",
    "HC60W35R2": "Performance IP Camera",
    "HC30WF5R1": "Performance IP Camera",
    "H3W2GR1V": "equIP IP Camera",
    "H4W4GR1V": "equIP IP Camera",
    "HBW2GR1V": "equIP IP Camera",
    "HBW2GR3V": "equIP IP Camera",
    "HBW4GR1": "equIP IP Camera",
    "HBW4PR1": "equIP IP Camera",
    "HDZ302DE": "HDZ PTZ Dome",
    "HC60WZ2E30": "Performance PTZ Dome",
}

IP_RE = re.compile(r"^([\d.]+)(?:#(TCP|UDP|HTTPS))?$")


def find_input_file():
    for p in INPUT_FILES:
        if p.exists():
            return p
    return None


def parse_line(line: str) -> dict | None:
    line = line.rstrip("\r\n").lstrip("\ufeff")
    if not line.strip():
        return None
    parts = line.split("\t")
    if len(parts) < 17:
        return None

    id_ = parts[1].strip()
    if not id_.isdigit():
        return None

    device_name = parts[3].strip()
    if not device_name or device_name == "-.-":
        return None

    return {
        "id": id_,
        "device_name": device_name,
        "device_type_text": parts[5].strip(),
        "nvr_name": parts[6].strip(),
        "channel": parts[8].strip(),
        "profile": parts[9].strip(),
        "enabled_status": parts[10].strip(),
        "message": parts[12].strip(),
        "ip_endpoint": parts[15].strip(),
        "model_code": parts[16].strip(),
    }


def classify_type(device_type_text: str) -> str:
    upper = device_type_text.upper()
    if "PTZ" in upper:
        return "ptz_camera"
    if "FIXED" in upper:
        return "fixed_camera"
    return "camera"


def make_device_id(nvr_name: str, channel: str, fallback_id: str) -> str:
    """Synthesize a stable device_id from NVR name + channel."""
    if nvr_name and channel:
        nvr_slug = re.sub(r"[^A-Za-z0-9]+", "-", nvr_name).strip("-")
        return f"{nvr_slug}-CH{channel.zfill(2)}"
    return f"CAM-{fallback_id}"


def emit_sql(records: list[dict]) -> None:
    print(f"-- Parsed {len(records)} camera records", file=sys.stderr)
    print()
    print("-- ============================================================")
    print("-- 1. UPSERT devices (update by IP if exists, else insert)")
    print("-- ============================================================")
    print()

    seen_ids = set()
    for r in records:
        if r["id"] in seen_ids:
            continue
        seen_ids.add(r["id"])

        device_type = classify_type(r["device_type_text"])
        vendor = VENDOR_BY_MODEL.get(r["model_code"] or "", "Honeywell")
        model = MODEL_FAMILY.get(r["model_code"] or "", r["model_code"] or "Unknown")

        ip_match = IP_RE.match(r["ip_endpoint"] or "")
        ip = ip_match.group(1) if ip_match else ""
        protocol = ip_match.group(2) if ip_match else None

        if not ip:
            # No IP, can't reliably upsert — skip with a comment
            print(f"-- SKIPPED (no IP): {r['device_name']} | {r['nvr_name']} CH{r['channel']}")
            continue

        device_id = make_device_id(r["nvr_name"], r["channel"], r["id"])
        name = r["device_name"].replace("'", "''")
        nvr = r["nvr_name"].replace("'", "''")

        # T-SQL pattern: UPDATE first, then INSERT if @@ROWCOUNT = 0
        print(
            f"IF EXISTS (SELECT 1 FROM iot.devices WHERE ip_address = '{ip}') "
            f"BEGIN "
            f"    UPDATE iot.devices "
            f"    SET device_id = '{device_id}', "
            f"        device_name = '{name}', "
            f"        device_type = '{device_type}', "
            f"        vendor = '{vendor}', "
            f"        model = '{model}' "
            f"    WHERE ip_address = '{ip}'; "
            f"END "
            f"ELSE "
            f"BEGIN "
            f"    INSERT INTO iot.devices "
            f"        (device_id, device_name, device_type, vendor, model, ip_address, status, last_seen, site_id) "
            f"    VALUES ('{device_id}', '{name}', '{device_type}', '{vendor}', '{model}', "
            f"            '{ip}', 'unknown', SYSUTCDATETIME(), NULL); "
            f"END"
        )

    print()
    print("-- ============================================================")
    print("-- 2. INSERT log entries for 'Camera is not added to IPEngine'")
    print("-- ============================================================")
    print()

    seen_logs = set()
    for r in records:
        if not r["message"] or "not added" not in r["message"].lower():
            continue
        if r["id"] in seen_logs:
            continue
        seen_logs.add(r["id"])

        ip_match = IP_RE.match(r["ip_endpoint"] or "")
        ip = ip_match.group(1) if ip_match else ""
        if not ip:
            continue

        name = r["device_name"].replace("'", "''")
        msg = r["message"].replace("'", "''")
        device_id = make_device_id(r["nvr_name"], r["channel"], r["id"])

        print(
            f"IF NOT EXISTS (SELECT 1 FROM iot.device_logs "
            f"               WHERE ip_address = '{ip}' AND message = '{msg}') "
            f"INSERT INTO iot.device_logs (device_id, device_name, severity, status, message, event_time, source_system, ip_address) "
            f"VALUES ((SELECT TOP 1 device_id FROM iot.devices WHERE ip_address = '{ip}'), "
            f"        '{name}', 'warning', 'disabled', '{msg}', SYSUTCDATETIME(), 'genetec_export', '{ip}');"
        )


def main():
    path = find_input_file()
    if not path:
        print(f"-- ERROR: no input file found", file=sys.stderr)
        sys.exit(1)
    print(f"-- Reading {path}", file=sys.stderr)

    raw = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            raw = path.read_text(encoding=enc)
            print(f"-- Decoded with {enc}", file=sys.stderr)
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        print("-- ERROR: could not decode file", file=sys.stderr)
        sys.exit(1)

    records = []
    for line in raw.splitlines():
        rec = parse_line(line)
        if rec:
            records.append(rec)

    emit_sql(records)


if __name__ == "__main__":
    main()
