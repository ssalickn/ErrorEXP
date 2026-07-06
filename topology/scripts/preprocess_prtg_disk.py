"""
PRTG Disk Space Report → SQL Server (iot.* schema)
Version 3: tokenize-then-walk parser for messy PDF→TXT output

Usage:
    python preprocess_prtg_disk.py --file path/to/report.txt
    python preprocess_prtg_disk.py --file path/to/report.txt --debug
"""

import re
import argparse
import pandas as pd
import pyodbc
from datetime import datetime
from pathlib import Path

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

DEVICE_TYPE_MAP = {
    "thtrdbi":        "other",
    "thtrdbiodb":     "biostar_server",
    "thtrdbstar":     "biostar_server",
    "thtrdeam":       "other",
    "thtrdsp":        "other",
    "thtrdgp":        "other",
    "thtrdgdb":       "other",
    "thtrdpi":        "other",
    "thtrdpivision":  "other",
    "thtrdpiaf":      "other",
    "thtrdwa":        "other",
    "thtrinfrapc":    "other",
}

SITE_ID = "BLDG-A"

# Known sensor names (longest first for greedy matching)
SENSOR_TYPES = (
    "Free Disk Space (Multi Drive)",
    "Disk Free",
    "Ping",
)

# Tokens that are noise from PRTG (probe markers, channel codes, etc.)
NOISE_TOKENS = re.compile(
    r"\b(?:88|8g|YTT|AAH|AAF|BAF|HAF|FFA|HIFF|E|Probe|Group)\b",
    re.IGNORECASE,
)

# ═══════════════════════════════════════════════════════════
# DEBUG HELPER
# ═══════════════════════════════════════════════════════════

def debug_dump(text: str, n: int = 40):
    """Print first n non-empty lines so we can see the raw format."""
    print("=" * 60)
    print("DEBUG: First 40 non-empty lines of input file")
    print("=" * 60)
    nonempty = [ln for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(nonempty[:n]):
        print(f"{i:3d}: {repr(ln)}")
    print("=" * 60)

# ═══════════════════════════════════════════════════════════
# PARSING v3 — tokenize then walk
# ═══════════════════════════════════════════════════════════

def classify_device_type(device_name: str) -> str:
    lower = device_name.lower()
    for prefix, dtype in DEVICE_TYPE_MAP.items():
        if lower.startswith(prefix):
            return dtype
    return "other"


def normalize_text(text: str) -> str:
    """Strip recurring PRTG boilerplate and repair common OCR breaks."""
    # Remove "Local Probe" and "Servers Group" boilerplate
    text = re.sub(r"Local\s+Probe", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"Servers\s+Group", " ", text, flags=re.IGNORECASE)
    # Drop remaining chevrons
    text = text.replace("»", " ").replace("«", " ")
    # Remove noise tokens (88, 8g, AAF, etc.)
    text = NOISE_TOKENS.sub(" ", text)
    # Repair "DeviceTHTRDBSTAR2" → "Device THTRDBSTAR2"
    text = re.sub(r"\bDevice([A-Z][A-Z0-9]{3,})", r"Device \1", text)
    # Repair "681GB" → "681 GB" (number+unit glued together)
    text = re.sub(r"(\d+(?:\.\d+)?)(GB|MB|TB|msec|%)", r"\1 \2",
                  text, flags=re.IGNORECASE)
    # Drop stray warning markers that aren't attached to a row
    text = re.sub(r"(?<!\w)[*+](?!\w)", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_report(text: str) -> pd.DataFrame:
    """
    Brute-force: split into segments, then extract 3 numbers + 1 device
    from each segment.
    """
    text = normalize_text(text)
    
    # Split on rank markers (digit followed by period)
    segments = re.split(r"(?=\b\d+\.\s)", text)
    
    rows = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # Only consider segments starting with a rank
        m = re.match(r"^(\d+)\.\s+(.*)$", seg, re.DOTALL)
        if not m:
            continue
        rank = int(m.group(1))
        body = m.group(2)
        
        # Extract all number+unit pairs from body
        nums = re.findall(r"(\d+(?:\.\d+)?)\s*(GB|MB|TB|msec|%)?", body, re.IGNORECASE)
        if len(nums) < 3:
            continue
        avg, minv, maxv = float(nums[0][0]), float(nums[1][0]), float(nums[2][0])
        unit = (nums[0][1] or "GB").upper()
        
        # Find sensor type
        sensor = None
        for stype in sorted(SENSOR_TYPES, key=len, reverse=True):
            if stype in body:
                sensor = stype
                break
        if not sensor:
            continue
        
        # Find device name — last token that looks like a hostname
        body_tokens = body.split()
        device = None
        for tok in reversed(body_tokens):
            if re.match(r"^[A-Za-z][A-Za-z0-9_-]{3,}$", tok) and tok.lower() != "device":
                device = tok
                break
            if tok.lower() == "device" and body_tokens.index(tok) < len(body_tokens) - 1:
                # next token is the device name
                next_tok = body_tokens[body_tokens.index(tok) + 1]
                device = next_tok
                break
        
        if not device:
            continue
        
        rows.append({
            "rank": rank, "warning": False, "sensor": sensor,
            "avg": avg, "unit": unit, "min": minv, "max": maxv,
            "device": device,
            "device_type": classify_device_type(device),
        })
    
    return pd.DataFrame(rows)


def extract_report_timestamp(text: str, fallback: datetime) -> datetime:
    """Try the 'Created YYYY-MM-DD HH-MM-SS' header pattern."""
    m = re.search(r"Created\s+(\d{4}-\d{2}-\d{2})\s+(\d{2})-(\d{2})-(\d{2})", text)
    if not m:
        return fallback
    date, hh, mm, ss = m.groups()
    return datetime.fromisoformat(f"{date}T{hh}:{mm}:{ss}")

# ═══════════════════════════════════════════════════════════
# DATABASE LOAD
# ═══════════════════════════════════════════════════════════

UPSERT_DEVICE = """
MERGE iot.devices AS target
USING (SELECT ? AS device_id) AS src
ON target.device_id = src.device_id
WHEN NOT MATCHED THEN
    INSERT (device_id, device_type, vendor, site_id, status, created_at, updated_at)
    VALUES (?, ?, 'prtg', ?, 'online', SYSUTCDATETIME(), SYSUTCDATETIME())
WHEN MATCHED THEN
    UPDATE SET updated_at = SYSUTCDATETIME();
"""

INSERT_LOG = """
INSERT INTO iot.device_logs
    (device_id, event_time, severity, status_code, message, raw_payload, source_system)
VALUES (?, ?, ?, ?, ?, ?, 'prtg_report');
"""

def ensure_site(cur, site_id: str):
    """Insert the site if it doesn't already exist."""
    cur.execute(
        "IF NOT EXISTS (SELECT 1 FROM iot.sites WHERE site_id = ?) "
        "INSERT INTO iot.sites (site_id, site_name) VALUES (?, ?);",
        (site_id, site_id, site_id)
    )

def load_to_sql(df: pd.DataFrame, report_ts: datetime):
    conn = pyodbc.connect(CONN_STR, autocommit=False)
    cur  = conn.cursor()
    try:
        ensure_site(cur, SITE_ID)

        for _, r in df.iterrows():
            cur.execute(UPSERT_DEVICE, (
                r["device"], r["device"], r["device_type"], SITE_ID
            ))
        for _, r in df.iterrows():
            severity = "warning" if r["warning"] else "info"
            unit = r["unit"].lower()
            msg = (f'{r["sensor"]}: avg={r["avg"]} {unit}, '
                   f'min={r["min"]} {unit}, max={r["max"]} {unit}')
            raw = (f'rank={r["rank"]} sensor="{r["sensor"]}" '
                   f'avg={r["avg"]} unit={unit} '
                   f'min={r["min"]} max={r["max"]} '
                   f'warning={r["warning"]}')
            cur.execute(INSERT_LOG, (
                r["device"], report_ts, severity,
                r["sensor"][:50], msg, raw,
            ))

        conn.commit()
        print(f"✓ Loaded {len(df)} rows into iot.devices + iot.device_logs")
    except Exception as e:
        conn.rollback()
        print(f"✗ Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--debug", action="store_true",
                    help="Print first 40 lines of input to inspect format")
    args = ap.parse_args()

    text = Path(args.file).read_text(encoding="utf-8", errors="ignore")

    if args.debug:
        debug_dump(text)
        return

    df = parse_report(text)
    if df.empty:
        print("⚠ No rows parsed. Re-run with --debug to inspect file format.")
        return

    report_ts = extract_report_timestamp(text, datetime.utcnow())
    print(f"Parsed {len(df)} rows from {args.file}")
    print(f"Report timestamp: {report_ts}")
    print()
    print(df[["rank", "device", "device_type", "sensor", "avg", "unit"]].to_string())

    load_to_sql(df, report_ts)

if __name__ == "__main__":
    main()
