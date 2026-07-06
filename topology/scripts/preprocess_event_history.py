"""
Excel Event History Report → SQL Server (iot.* schema)
Extracts: Source Device, Event Description, Received Date & Time
Optimized for large files (5000+ rows)

Usage:
    python preprocess_event_history.py --file path/to/report.xlsx
    python preprocess_event_history.py --file path/to/report.xlsx --debug
    python preprocess_event_history.py --file path/to/report.xlsx --limit 10
"""

import re
import argparse
import pandas as pd
import pyodbc
from datetime import datetime
from pathlib import Path

# ---------- Connection ----------
CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=thtrdinfradb1;"
    "Database=InfrastructureMonitorDB;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

SITE_ID = "BLDG-A"

# ---------- Device type classification ----------
DEVICE_TYPE_MAP = {
    "southfence": "perimeter_fence",
    "westgate":   "perimeter_fence",
    "eastgate":   "perimeter_fence",
    "northfence": "perimeter_fence",
    "facial":     "facial_recognition_camera",
    "ptz":        "ptz_camera",
    "booth":      "gate_booth",
    "camera":     "fixed_camera",
}

# Status keywords
STATUS_RULES = [
    (re.compile(r"disconnected|offline|lost\s*video|signal\s*lost", re.I), "offline"),
    (re.compile(r"reconnected|back\s*online|signal\s*restored|connected", re.I), "online"),
    (re.compile(r"motion|detected|triggered|alarm", re.I), "alert"),
]


# ---------- Helpers ----------
def debug_dump(df: pd.DataFrame, n: int = 15):
    print("=" * 60)
    print("DEBUG: Raw sheet structure")
    print("=" * 60)
    print(f"Total columns: {len(df.columns)}")
    print("Columns:", list(df.columns))
    print()
    print(f"First {n} rows:")
    print(df.head(n).to_string())
    print("=" * 60)


def classify_device_type(device_name: str) -> str:
    lower = device_name.lower()
    for prefix, dtype in DEVICE_TYPE_MAP.items():
        if prefix in lower:
            return dtype
    return "other"


def derive_status(description: str) -> str:
    for pat, status in STATUS_RULES:
        if pat.search(description):
            return status
    return "unknown"


def derive_device_id(source_device: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", source_device.lower()).strip("_")
    return f"vms_{slug}" if slug else "vms_unknown"


def _engine_for(path: str) -> str:
    ext = Path(path).suffix.lower()
    return "xlrd" if ext == ".xls" else "openpyxl"


# ---------- Parser ----------
def find_header_row(path: str, sheet_name: str = "EventLogReport", max_scan: int = 20) -> int:
    """Scan first N rows to find the actual header row."""
    engine = _engine_for(path)
    preview = pd.read_excel(path, sheet_name=sheet_name, header=None,
                           nrows=max_scan, engine=engine, dtype=str)
    for i, row in preview.iterrows():
        row_str = " ".join(str(c) for c in row.values if pd.notna(c)).lower()
        if "received" in row_str and "source device" in row_str:
            return i
    raise ValueError(f"Could not find header row in first {max_scan} rows")


def parse_excel(path: str) -> pd.DataFrame:
    """
    Parse the EventLogReport sheet from a VMS event history export.
    """
    engine = _engine_for(path)
    
    # Auto-detect header row
    header_idx = find_header_row(path)
    print(f"Detected header at row {header_idx}")
    
    df = pd.read_excel(
        path,
        sheet_name="EventLogReport",
        header=header_idx,
        engine=engine,
        dtype=str,
    )

    # Normalize column names
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
    print(f"Columns found: {list(df.columns)}")

    # Find columns by partial match (handles "Received  Date & Time" etc.)
    col_map = {}
    for col in df.columns:
        col_lower = col.lower()
        if "received" in col_lower and "date" in col_lower:
            col_map["ts"] = col
        elif col_lower == "source device":
            col_map["device"] = col
        elif "event description" in col_lower:
            col_map["desc"] = col
        elif "cleared" in col_lower and "date" in col_lower:
            col_map["cleared"] = col

    print(f"Column mapping: {col_map}")
    
    missing = [k for k in ["ts", "device", "desc"] if k not in col_map]
    if missing:
        raise ValueError(f"Missing required columns: {missing}. Found: {list(df.columns)}")

    # Parse timestamps
    ts_col = col_map["ts"]
    ev = pd.to_datetime(df[ts_col], errors="coerce", format="mixed")
    if ev.isna().sum() > len(df) * 0.5:
        # Fallback to explicit format
        ev = pd.to_datetime(df[ts_col], format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
    
    cl = None
    if "cleared" in col_map:
        cl = pd.to_datetime(df[col_map["cleared"]], errors="coerce", format="mixed")

    out = pd.DataFrame({
        "event_time":    ev,
        "source_device": df[col_map["device"]].fillna("").astype(str).str.strip(),
        "description":   df[col_map["desc"]].fillna("").astype(str).str.strip(),
    })
    if cl is not None:
        out["cleared_time"] = cl

    # Filter out invalid rows
    out = out[out["event_time"].notna() & out["description"].ne("")].copy()
    print(f"Valid rows after filtering: {len(out)}")

    # Add derived columns
    out["device_id"]   = out["source_device"].map(derive_device_id)
    out["status"]      = out["description"].map(derive_status)
    out["device_type"] = out["source_device"].map(classify_device_type)

    return out.reset_index(drop=True)


# ---------- DB DDL ----------
ENSURE_LOG_STATUS_COL = """
IF COL_LENGTH('iot.device_logs', 'status') IS NULL
    ALTER TABLE iot.device_logs ADD status VARCHAR(20) NULL;
"""

ENSURE_DEVICE_NAME_COL = """
IF COL_LENGTH('iot.devices', 'device_name') IS NULL
    ALTER TABLE iot.devices ADD device_name NVARCHAR(200) NULL;
"""

UPSERT_DEVICE = """
MERGE iot.devices AS target
USING (SELECT ? AS device_id) AS src
ON target.device_id = src.device_id
WHEN NOT MATCHED THEN
    INSERT (device_id, device_name, device_type, vendor, site_id,
            status, created_at, updated_at)
    VALUES (?, ?, ?, 'excel_report', ?, ?,
            SYSUTCDATETIME(), SYSUTCDATETIME())
WHEN MATCHED THEN
    UPDATE SET device_name = ?,
               device_type = ?,
               status      = ?,
               updated_at  = SYSUTCDATETIME();
"""

INSERT_LOG = """
INSERT INTO iot.device_logs
    (device_id, event_time, severity, status_code, status,
     message, raw_payload, source_system)
VALUES (?, ?, ?, ?, ?, ?, ?, 'event_history_xlsx');
"""


def ensure_site(cur, site_id: str):
    cur.execute(
        "IF NOT EXISTS (SELECT 1 FROM iot.sites WHERE site_id = ?) "
        "INSERT INTO iot.sites (site_id, site_name) VALUES (?, ?);",
        (site_id, site_id, site_id),
    )


def load_to_sql(df: pd.DataFrame, batch_size: int = 500):
    """Bulk load using executemany for performance."""
    conn = pyodbc.connect(CONN_STR, autocommit=False)
    cur = conn.cursor()
    try:
        # Schema bootstrap
        cur.execute(ENSURE_LOG_STATUS_COL)
        cur.execute(ENSURE_DEVICE_NAME_COL)
        ensure_site(cur, SITE_ID)

        # Step 1: Get unique devices and bulk upsert
        unique_devices = df[["device_id", "source_device", "device_type", "status"]].drop_duplicates(subset=["device_id"])
        device_rows = []
        for _, r in unique_devices.iterrows():
            device_rows.append((
                r["device_id"],
                r["device_id"],
                r["source_device"],
                r["device_type"],
                SITE_ID,
                r["status"],
                r["source_device"],
                r["device_type"],
                r["status"],
            ))
        cur.executemany(UPSERT_DEVICE, device_rows)
        print(f"Upserted {len(device_rows)} unique devices")

        # Step 2: Bulk insert logs
        severity_map = {
            "offline": "critical",
            "online":  "info",
            "alert":   "warning",
        }
        
        log_rows = []
        for _, r in df.iterrows():
            severity = severity_map.get(r["status"], "info")
            desc = r["description"]
            device_name = r["source_device"]
            
            msg = f"[VMS] {device_name}: {desc}"
            cleared_str = str(r.get("cleared_time", "")) if pd.notna(r.get("cleared_time")) else ""
            raw = (f'device="{device_name}" '
                   f'status="{r["status"]}" '
                   f'cleared="{cleared_str}" '
                   f'description="{desc}"')
            
            log_rows.append((
                r["device_id"],
                r["event_time"].to_pydatetime(),
                severity,
                "vms_event",
                r["status"],
                msg,
                raw,
            ))

        # Insert in batches for memory efficiency
        total_inserted = 0
        for i in range(0, len(log_rows), batch_size):
            batch = log_rows[i:i + batch_size]
            cur.executemany(INSERT_LOG, batch)
            total_inserted += len(batch)
            if (i // batch_size) % 5 == 0:
                print(f"  Inserted {total_inserted}/{len(log_rows)} log rows...")

        conn.commit()
        print(f"\n✓ Loaded {len(df)} events for "
              f"{df['device_id'].nunique()} unique devices into iot.*")
    except Exception as e:
        conn.rollback()
        print(f"\n✗ Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


# ---------- Main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--debug", action="store_true",
                    help="Print raw sheet structure")
    ap.add_argument("--limit", type=int,
                    help="Only process first N rows (for testing)")
    args = ap.parse_args()

    if args.debug:
        engine = _engine_for(args.file)
        raw = pd.read_excel(args.file, sheet_name="EventLogReport",
                           header=None, nrows=20, engine=engine, dtype=str)
        debug_dump(raw)
        return

    print(f"Parsing: {args.file}")
    df = parse_excel(args.file)
    
    if df.empty:
        print("[WARN] No rows parsed. Re-run with --debug to inspect format.")
        return

    if args.limit:
        df = df.head(args.limit)
        print(f"[LIMIT] Processing only first {len(df)} rows")

    print(f"\nParsed {len(df)} events")
    print(f"Unique devices: {df['device_id'].nunique()}")
    print(f"Status breakdown:")
    print(df["status"].value_counts().to_string())
    print()
    print(df[["event_time", "source_device", "status", "description"]]
          .head(10).to_string(index=False))
    print("...")

    load_to_sql(df)


if __name__ == "__main__":
    main()
