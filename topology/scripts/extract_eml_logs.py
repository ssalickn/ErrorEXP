"""
Parse .eml alert files (LogicMonitor/PRTG/custom) and load into iot.device_logs.

Usage:
    python extract_eml_logs.py --folder "C:\\path\\to\\eml_files" --dry-run
"""

import os
import re
import argparse
import email
import email.utils
from email import policy
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass

import pyodbc

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=thtrdinfradb1;"
    "Database=InfrastructureMonitorDB;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)


@dataclass
class ParsedLog:
    device_id: str
    event_time: datetime
    severity: str
    status_code: str
    message: str
    raw_payload: str
    source_system: str = "email_alert"


def parse_eml_file(filepath: Path) -> list:
    """Parse one .eml file and extract log entries."""
    try:
        with open(filepath, 'rb') as f:
            msg = email.message_from_binary_file(f, policy=policy.default)
    except Exception as e:
        print(f"  ✗ Cannot read {filepath.name}: {e}")
        return []

    event_time = _parse_email_date(msg.get("Date", ""))
    if not event_time:
        try:
            event_time = datetime.fromtimestamp(filepath.stat().st_mtime, tz=timezone.utc)
        except Exception:
            event_time = datetime.now(timezone.utc)

    subject = msg.get("Subject", "")
    body = _get_body_text(msg)
    severity = _detect_severity(subject, body)
    device_ids = _extract_device_ids(subject, body)
    status_code = _extract_status_code(subject, body)

    raw_payload = (
        f"From: {msg.get('From','')}\n"
        f"To: {msg.get('To','')}\n"
        f"Date: {msg.get('Date','')}\n"
        f"Subject: {subject}\n\n"
        f"{body[:2000]}"
    )

    logs = []
    for device_id in device_ids:
        logs.append(ParsedLog(
            device_id=device_id,
            event_time=event_time,
            severity=severity,
            status_code=status_code[:50],
            message=subject[:500],
            raw_payload=raw_payload,
        ))
    return logs


def _parse_email_date(date_str: str):
    if not date_str:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _get_body_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_content()
                except Exception:
                    pass
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    return _strip_html(part.get_content())
                except Exception:
                    pass
        return ""
    else:
        try:
            return msg.get_content()
        except Exception:
            return str(msg.get_payload())


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _detect_severity(subject: str, body: str) -> str:
    """Severity from LogicMonitor/PRTG email content."""
    text = f"{subject} {body}".lower()
    
    # LogicMonitor cleared = resolution
    if "cleared" in text:
        return "info"
    
    # Critical / Down
    if any(kw in text for kw in [
        "critical", "is down", "is up",  # explicit up/down transitions
        "down", "failure", "offline", "unreachable", "alarm"
    ]):
        return "critical"
    
    # Warning / Degraded
    if any(kw in text for kw in [
        "warning", "degraded", "threshold", "idleinterval"
    ]):
        return "warning"
    
    # Error
    if any(kw in text for kw in [
        "error", "failed", "timeout", "lost", "reset"
    ]):
        return "error"
    
    return "info"


def _extract_device_ids(subject: str, body: str) -> list:
    """Extract device IDs from LogicMonitor/PRTG alerts."""
    text = f"{subject}\n{body}"
    device_ids = set()
    
    # 1) LogicMonitor subject: "***CLEARED***critical - <DEVICE> Host Status"
    lm_patterns = [
        r"(?:critical|warning|error|down|up)\s*-\s*([A-Za-z0-9][A-Za-z0-9 _().-]{3,60}?)\s+Host\s+Status",
        r"[Cc]ritical\s*-\s*([A-Za-z0-9][A-Za-z0-9 _().-]{3,60}?)\s+Host\s+Status",
    ]
    
    for pattern in lm_patterns:
        m = re.search(pattern, text)
        if m:
            candidate = m.group(1).strip().rstrip('.,;:')
            if candidate and len(candidate) >= 4:
                device_ids.add(candidate)
    
    # 1b) NEW: LogicMonitor subject: "The host <DEVICE> is down"
    is_down_patterns = [
        r"[Tt]he\s+host\s+([A-Za-z0-9][A-Za-z0-9 _().-]{3,60}?)\s+is\s+down",
        r"[Tt]he\s+host\s+([A-Za-z0-9][A-Za-z0-9 _().-]{3,60}?)\s+is\s+up",
    ]
    
    for pattern in is_down_patterns:
        m = re.search(pattern, text)
        if m:
            candidate = m.group(1).strip().rstrip('.,;:')
            if candidate and len(candidate) >= 4:
                device_ids.add(candidate)
    
    # 2) PRTG-style: thtrdbiodb, THTRDPIVISION
    prtg_patterns = [
        r"\b(thtrd[a-z0-9]{2,12})\b",
        r"\b(THTRD[A-Z0-9]{2,12})\b",
    ]
    
    exclude_prtg = {"thtrdsp", "thtrdgp", "thtrdgdb", "thtrdbi"}
    for pattern in prtg_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            candidate = match.group(1)
            if candidate.lower() not in exclude_prtg and len(candidate) >= 6:
                device_ids.add(candidate)
    
    return list(device_ids)


def _extract_status_code(subject: str, body: str) -> str:
    text = f"{subject} {body}"
    sensors = [
        "Host Status",
        "Host Status idleInterval",
        "Free Disk Space (Multi Drive)",
        "Free Disk Space",
        "Disk Free",
        "Ping",
        "CPU Load",
        "Memory Usage",
        "Interface Down",
    ]
    for sensor in sensors:
        if sensor.lower() in text.lower():
            return sensor
    return "Host Status" if "host status" in text.lower() else (subject[:50] if subject else "unknown")


UPSERT_DEVICE = """
MERGE iot.devices AS target
USING (SELECT ? AS device_id) AS src
ON target.device_id = src.device_id
WHEN NOT MATCHED THEN
    INSERT (device_id, device_type, vendor, site_id, status,
            last_seen, created_at, updated_at)
    VALUES (?, 'other', 'logicmonitor', 'BLDG-A', ?,
            ?, SYSUTCDATETIME(), SYSUTCDATETIME())
WHEN MATCHED THEN
    UPDATE SET 
        status = ?,
        last_seen = ?,
        updated_at = SYSUTCDATETIME();
"""


INSERT_LOG = """
INSERT INTO iot.device_logs
    (device_id, event_time, severity, status_code, message,
     raw_payload, source_system)
VALUES (?, ?, ?, ?, ?, ?, 'logicmonitor_email');
"""


def write_logs(parsed_logs: list, dry_run: bool = False):
    if not parsed_logs:
        print("  No logs to write.")
        return

    if dry_run:
        print(f"\n[DRY RUN] Would write {len(parsed_logs)} log entries")
        print("\nAll entries (with status updates):")
        for log in parsed_logs:
            status = _severity_to_status(log.severity)
            print(f"  {log.event_time.strftime('%Y-%m-%d %H:%M')} "
                  f"[{log.severity:8s}] → status={status:8s} "
                  f"{log.device_id:40s} {log.status_code}")
        return

    conn = pyodbc.connect(CONN_STR, autocommit=False)
    cur = conn.cursor()
    try:
        for log in parsed_logs:
            status = _severity_to_status(log.severity)
            
            # 1) UPSERT device with current status
            cur.execute(UPSERT_DEVICE, (
                log.device_id,                  # src key
                log.device_id,                  # new device_id
                status,                         # new status
                log.event_time,                 # last_seen
                status,                         # update status
                log.event_time,                 # update last_seen
            ))
            
            # 2) INSERT log entry
            cur.execute(INSERT_LOG, (
                log.device_id, log.event_time, log.severity,
                log.status_code, log.message, log.raw_payload,
            ))

        conn.commit()
        unique_devices = set(log.device_id for log in parsed_logs)
        print(f"\n✓ Wrote {len(parsed_logs)} log entries")
        print(f"✓ Updated status for {len(unique_devices)} devices")
    except Exception as e:
        conn.rollback()
        print(f"\n✗ DB error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


def _severity_to_status(severity: str) -> str:
    """Map alert severity to device status."""
    if severity == "critical":
        return "offline"
    if severity == "warning":
        return "degraded"
    if severity == "error":
        return "degraded"
    return "online"   # info = back up


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--folder', required=True)
    ap.add_argument('--limit', type=int)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        print(f"  ✗ Folder not found: {args.folder}")
        return

    files = list(folder.rglob("*.eml"))
    print(f"Found {len(files)} .eml files in {folder}")

    if args.limit:
        files = files[:args.limit]
        print(f"Processing first {len(files)} files")

    all_logs = []
    no_device_count = 0
    print("\nParsing emails...")
    for i, filepath in enumerate(files, 1):
        if i % 50 == 0:
            print(f"  Processed {i}/{len(files)}...")
        logs = parse_eml_file(filepath)
        if logs:
            all_logs.extend(logs)
        else:
            no_device_count += 1

    print(f"\nParsed {len(all_logs)} log entries")
    if no_device_count:
        print(f"  ({no_device_count} files had no recognizable device IDs)")

    if all_logs:
        unique_devices = set(log.device_id for log in all_logs)
        print(f"Unique devices: {len(unique_devices)}")
        print(f"Devices: {sorted(unique_devices)}")
        print(f"Date range: {min(l.event_time for l in all_logs).date()} "
              f"to {max(l.event_time for l in all_logs).date()}")

    write_logs(all_logs, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
