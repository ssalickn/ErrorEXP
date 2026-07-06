"""
Extract device topology from SolarWinds Orion database.
SolarWinds automatically discovers and stores network topology
including nodes, interfaces, and connections.

Usage:
    python extract_solarwinds_topology.py --dry-run
    python extract_solarwinds_topology.py
"""

import pyodbc
import pandas as pd
from datetime import datetime

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════

# Try several common SolarWinds DB names
SOLARWINDS_DBS = ["SolarWindsOrion", "SolarWindsOrion2019.4", "SolarWindsOrion2019.4V2"]
IOT_DB = "InfrastructureMonitorDB"
SITE_ID = "BLDG-A"

def make_conn(database):
    return (
        "Driver={ODBC Driver 17 for SQL Server};"
        "Server=thtrdinfradb1;"
        f"Database={database};"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
    )

IOT_CONN_STR = make_conn(IOT_DB)

# ═══════════════════════════════════════════════════════════
# SOLARWINDS CONNECTION
# ═══════════════════════════════════════════════════════════

def find_solarwinds_db():
    """Find which SolarWinds database exists and is accessible."""
    for db in SOLARWINDS_DBS:
        try:
            conn = pyodbc.connect(make_conn(db), timeout=5)
            cur = conn.cursor()
            cur.execute("SELECT 1")
            conn.close()
            return db
        except Exception:
            continue
    return None

def list_solarwinds_tables(conn):
    """List expected tables in SolarWinds."""
    cur = conn.cursor()
    cur.execute("""
        SELECT TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_NAME IN (
            'Nodes', 'Interfaces', 'Topology',
            'NodeSettings', 'Volumes', 'CustomPollerAssignment',
            'NodeIPAddresses', 'Applications', 'ApplicationTemplates'
        )
        ORDER BY TABLE_NAME
    """)
    return cur.fetchall()

# ═══════════════════════════════════════════════════════════
# EXTRACT FUNCTIONS
# ═══════════════════════════════════════════════════════════

def extract_nodes(conn):
    """Pull all discovered nodes from SolarWinds."""
    query = """
    SELECT
        n.NodeID,
        n.Caption           AS hostname,
        n.IP_Address        AS ip_address,
        n.MachineType       AS device_type,
        n.Vendor            AS vendor,
        n.VendorVersion     AS model,
        n.SysName,
        n.SysDescr,
        n.Location          AS location,
        n.Status            AS status,
        n.LastSync,
        n.LastPoll
    FROM dbo.Nodes n
    WHERE n.Deleted = 0
      AND n.IP_Address IS NOT NULL
    """
    return pd.read_sql(query, conn)

def extract_topology(conn):
    """Pull layer-2 topology connections."""
    query = """
    SELECT
        t.SourceNodeID,
        t.SourceInterfaceID,
        t.DestNodeID,
        t.DestInterfaceID,
        t.ConnectionType,
        t.DiscoveredTime
    FROM dbo.Topology t
    WHERE t.DestNodeID IS NOT NULL
    """
    return pd.read_sql(query, conn)

def extract_interfaces(conn):
    """Pull all interfaces per node."""
    query = """
    SELECT
        i.InterfaceID,
        i.NodeID,
        i.Name              AS port_name,
        i.IfName,
        i.Type              AS port_type,
        i.Speed             AS speed_bps,
        i.Status            AS admin_status,
        i.OperStatus        AS oper_status,
        i.VLAN,
        i.LastPoll
    FROM dbo.Interfaces i
    WHERE i.Deleted = 0
    """
    return pd.read_sql(query, conn)

# ═══════════════════════════════════════════════════════════
# CLASSIFICATION
# ═══════════════════════════════════════════════════════════

def classify_device_type(machine_type, vendor, sys_descr=""):
    """Map SolarWinds device types to our enum."""
    s = f"{machine_type or ''} {vendor or ''} {sys_descr or ''}".lower()
    if "switch" in s and ("cisco" in s or "catalyst" in s):
        return "cisco_switch"
    if "router" in s:
        return "cisco_switch"  # you can add 'router' to enum later
    if "wireless" in s or "accesspoint" in s or " ap " in s:
        return "cisco_ap"
    if "server" in s:
        return "other"
    if "biostar" in s or "suprema" in s:
        return "biostar_server"
    if "nvr" in s or "vms" in s or "camera" in s:
        return "nvr"
    if "honeywell" in s:
        return "honeywell_panel"
    return "other"

# ═══════════════════════════════════════════════════════════
# WRITE TO IOT
# ═══════════════════════════════════════════════════════════

UPSERT_DEVICE = """
MERGE iot.devices AS target
USING (SELECT ? AS device_id) AS src
ON target.device_id = src.device_id
WHEN NOT MATCHED THEN
    INSERT (device_id, device_type, vendor, model, ip_address,
            site_id, status, last_seen, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, SYSUTCDATETIME(),
            SYSUTCDATETIME(), SYSUTCDATETIME())
WHEN MATCHED THEN
    UPDATE SET
        last_seen = SYSUTCDATETIME(),
        updated_at = SYSUTCDATETIME(),
        status = COALESCE(?, status);
"""

UPSERT_REL = """
MERGE iot.device_relationships AS target
USING (SELECT ? AS source_id, ? AS target_id, ? AS rel_type) AS src
ON target.source_id = src.source_id
   AND target.target_id = src.target_id
   AND target.relationship_type = src.rel_type
   AND target.valid_to IS NULL
WHEN NOT MATCHED THEN
    INSERT (source_id, target_id, relationship_type, confidence,
            source, inferred, valid_from)
    VALUES (?, ?, ?, 0.95, 'solarwinds_topology', 0, SYSUTCDATETIME());
"""

def write_devices(df, dry_run=False):
    if df.empty:
        print("  No devices to write.")
        return
    print(f"  Writing {len(df)} devices...")

    if dry_run:
        for _, r in df.head(20).iterrows():
            dtype = classify_device_type(
                r.get("device_type") or "",
                r.get("vendor") or "",
                r.get("SysDescr") or ""
            )
            print(f"    {str(r['hostname'])[:30]:30s}  {dtype:18s}  {r.get('ip_address')}")
        if len(df) > 20:
            print(f"    ... and {len(df) - 20} more")
        return

    conn = pyodbc.connect(IOT_CONN_STR, autocommit=False)
    cur = conn.cursor()
    try:
        written = 0
        for _, r in df.iterrows():
            hostname = r["hostname"]
            if not hostname or pd.isna(hostname):
                continue
            dtype = classify_device_type(
                r.get("device_type") or "",
                r.get("vendor") or "",
                r.get("SysDescr") or ""
            )
            status = "online" if str(r.get("status", "")).lower() in ("1", "up", "online") else "unknown"
            cur.execute(UPSERT_DEVICE, (
                str(hostname), str(hostname), dtype,
                str(r.get("vendor") or "solarwinds"),
                str(r.get("model") or "unknown"),
                str(r.get("ip_address") or ""),
                SITE_ID, status, status,
            ))
            written += 1
        conn.commit()
        print(f"  ✓ {written} devices upserted")
    except Exception as e:
        conn.rollback()
        print(f"  ✗ Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()

def write_topology(topo_df, nodes_df, dry_run=False):
    if topo_df.empty:
        print("  No topology edges to write.")
        return

    id_to_name = dict(zip(nodes_df["NodeID"], nodes_df["hostname"]))
    print(f"  Writing topology edges from {len(topo_df)} rows...")

    if dry_run:
        shown = 0
        for _, r in topo_df.iterrows():
            src = id_to_name.get(r["SourceNodeID"], f"node-{r['SourceNodeID']}")
            tgt = id_to_name.get(r["DestNodeID"], f"node-{r['DestNodeID']}")
            print(f"    {str(src)[:30]:30s}  --[{r.get('SourceInterfaceID', '?')}]-->  {str(tgt)[:30]:30s}  ({r.get('ConnectionType', '?')})")
            shown += 1
            if shown >= 20:
                print(f"    ... and {len(topo_df) - 20} more")
                break
        return

    conn = pyodbc.connect(IOT_CONN_STR, autocommit=False)
    cur = conn.cursor()
    try:
        written = 0
        for _, r in topo_df.iterrows():
            src = id_to_name.get(r["SourceNodeID"])
            tgt = id_to_name.get(r["DestNodeID"])
            if not src or not tgt or pd.isna(src) or pd.isna(tgt):
                continue
            cur.execute(UPSERT_REL, (
                str(src), str(tgt), "depends_on",
                str(src), str(tgt), "depends_on",
            ))
            written += 1
        conn.commit()
        print(f"  ✓ {written} edges upserted")
    except Exception as e:
        conn.rollback()
        print(f"  ✗ Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    import sys
    dry_run = "--dry-run" in sys.argv

    print("=" * 60)
    print("SolarWinds Topology Extractor")
    print("=" * 60)

    # Step 1: Find SolarWinds DB
    print("\n[1/5] Looking for SolarWinds database...")
    sw_db = find_solarwinds_db()
    if not sw_db:
        print("  ✗ No SolarWinds database found on this server.")
        print("  Tried:", SOLARWINDS_DBS)
        print("  You can edit SOLARWINDS_DBS in this script to add others.")
        return
    print(f"  ✓ Found: {sw_db}")

    # Step 2: Connect and check tables
    print(f"\n[2/5] Connecting to {sw_db}...")
    try:
        sw_conn = pyodbc.connect(make_conn(sw_db), timeout=30)
    except Exception as e:
        print(f"  ✗ Cannot connect: {e}")
        return
    print("  ✓ Connected")

    print("\n[3/5] Checking schema...")
    tables = list_solarwinds_tables(sw_conn)
    for schema, name in tables:
        print(f"  ✓ {schema}.{name}")
    if not tables:
        print("  ⚠ No expected tables found. Listing all tables...")
        all_tables = pd.read_sql(
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "ORDER BY TABLE_NAME", sw_conn
        )
        print(all_tables.to_string())
        return

    # Step 3: Extract nodes
    print("\n[4/5] Extracting nodes...")
    try:
        nodes_df = extract_nodes(sw_conn)
        print(f"  Found {len(nodes_df)} nodes")
    except Exception as e:
        print(f"  ✗ Cannot extract nodes: {e}")
        sw_conn.close()
        return

    # Step 4: Extract topology
    print("  Extracting topology...")
    try:
        topo_df = extract_topology(sw_conn)
        print(f"  Found {len(topo_df)} topology edges")
    except Exception as e:
        print(f"  ⚠ No topology table or cannot read: {e}")
        topo_df = pd.DataFrame()

    sw_conn.close()

    # Step 5: Write to iot.*
    print(f"\n[5/5] Writing to iot.* (dry_run={dry_run})...")
    write_devices(nodes_df, dry_run=dry_run)
    if not topo_df.empty:
        write_topology(topo_df, nodes_df, dry_run=dry_run)
    else:
        print("  (Skipping topology - no data)")

    print("\n" + "=" * 60)
    print("Done.")
    print("=" * 60)

if __name__ == "__main__":
    main()