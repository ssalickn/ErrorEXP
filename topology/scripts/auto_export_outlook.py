"""
Automated Outlook email exporter.
Pulls new emails from a shared mailbox and saves them as .msg files.
Tracks last-run state to only fetch new emails.

Usage:
    py -m pip install pywin32
    python auto_export_outlook.py --once           # run once and exit
    python auto_export_outlook.py --watch          # run continuously (every 15 min)
    python auto_export_outlook.py --mailbox "IT-Alerts" --output "C:\\Logs\\outlook"
"""

import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta

try:
    import win32com.client
    HAVE_WIN32 = True
except ImportError:
    HAVE_WIN32 = False

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════

DEFAULT_OUTPUT = r"C:\Logs\outlook_inbox"
STATE_FILE = "outlook_export_state.json"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler("outlook_export.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════

def load_state(state_file: Path) -> dict:
    """Load last-run state (last exported timestamp)."""
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception as e:
            log.warning(f"Cannot read state file: {e}")
    return {"last_run": None, "exported_count": 0}


def save_state(state_file: Path, state: dict):
    """Save run state."""
    state_file.write_text(json.dumps(state, indent=2, default=str))


# ═══════════════════════════════════════════════════════════
# OUTLOOK CONNECTION
# ═══════════════════════════════════════════════════════════

def connect_to_outlook(mailbox_name: str = None):
    """Connect to Outlook and return the Inbox folder."""
    if not HAVE_WIN32:
        log.error("pywin32 not installed. Run: py -m pip install pywin32")
        return None
    
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
        
        if mailbox_name:
            try:
                recipient = namespace.CreateRecipient(mailbox_name)
                recipient.Resolve()
                if recipient.Resolved:
                    inbox = namespace.GetSharedDefaultFolder(recipient, 6)
                    log.info(f"Connected to shared mailbox: {mailbox_name}")
                else:
                    log.error(f"Cannot resolve mailbox: {mailbox_name}")
                    log.info("Falling back to default inbox")
                    inbox = namespace.GetDefaultFolder(6)
            except Exception as e:
                log.error(f"Shared mailbox error: {e}")
                inbox = namespace.GetDefaultFolder(6)
        else:
            inbox = namespace.GetDefaultFolder(6)
            log.info("Connected to default inbox")
        
        return inbox
    except Exception as e:
        log.error(f"Outlook connection failed: {e}")
        return None


def export_emails(
    output_folder: str,
    mailbox_name: str = None,
    subject_filter: str = None,
    only_newer_than: datetime = None,
) -> tuple:
    """
    Export emails from Outlook to .msg files.
    Returns (count_exported, last_email_time).
    """
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)
    
    inbox = connect_to_outlook(mailbox_name)
    if not inbox:
        return 0, None
    
    messages = inbox.Items
    total_count = messages.Count
    log.info(f"Inbox contains {total_count} messages")
    
    # Apply filters
    if subject_filter or only_newer_than:
        filter_parts = []
        if subject_filter:
            filter_parts.append(
                f"\"urn:schemas:httpmail:subject\" LIKE '%{subject_filter}%'"
            )
        if only_newer_than:
            # Outlook filter format: "6/29/2026 10:00 AM"
            ts = only_newer_than.strftime("%m/%d/%Y %I:%M %p")
            filter_parts.append(f"\"urn:schemas:httpmail:datereceived\" > '{ts}'")
        
        if filter_parts:
            filter_str = "@SQL=" + " AND ".join(f"({p})" for p in filter_parts)
            messages = messages.Restrict(filter_str)
            log.info(f"After filter: {messages.Count} messages")
    
    # Sort newest first
    messages.Sort("[ReceivedTime]", Descending=True)
    
    exported = 0
    skipped = 0
    last_time = only_newer_than
    
    for msg in messages:
        try:
            received = msg.ReceivedTime
            # Convert to naive datetime for comparison
            received_naive = datetime(
                received.year, received.month, received.day,
                received.hour, received.minute, received.second
            )
            
            if only_newer_than and received_naive <= only_newer_than:
                continue  # Skip old emails
            
            # Build filename
            subject = (msg.Subject or "(no subject)").replace("\n", " ")[:80]
            safe_subject = "".join(
                c if c.isalnum() or c in " -_." else "_"
                for c in subject
            ).strip()
            
            timestamp_str = received.strftime("%Y%m%d_%H%M%S")
            
            # Include sender email for uniqueness
            try:
                sender = msg.SenderEmailAddress or "unknown"
                sender = sender.split("@")[0] if "@" in sender else sender
                sender = "".join(c if c.isalnum() else "_" for c in sender)[:20]
            except Exception:
                sender = "unknown"
            
            filename = f"{timestamp_str}_{sender}_{safe_subject}.msg"
            filepath = output_path / filename
            
            # Skip if already exists
            if filepath.exists():
                continue
            
            # Save as .msg
            msg.SaveAs(str(filepath), 3)  # 3 = olSaveAsMsg (Unicode)
            
            exported += 1
            if exported <= 3 or exported % 25 == 0:
                log.info(f"  [{exported}] {filename}")
            
            # Track newest email time
            if last_time is None or received_naive > last_time:
                last_time = received_naive
                
        except Exception as e:
            skipped += 1
            if skipped <= 3:
                log.warning(f"  Skipped: {e}")
    
    return exported, last_time


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Automated Outlook email exporter")
    ap.add_argument('--mailbox', help='Shared mailbox name (e.g., "IT-Alerts")')
    ap.add_argument('--output', default=DEFAULT_OUTPUT,
                    help=f'Output folder (default: {DEFAULT_OUTPUT})')
    ap.add_argument('--subject-filter', default="LMD",
                    help='Only export emails matching subject (default: LMD)')
    ap.add_argument('--state-file', default=STATE_FILE,
                    help='State file for tracking last run')
    ap.add_argument('--once', action='store_true',
                    help='Run once and exit (default)')
    ap.add_argument('--watch', action='store_true',
                    help='Run continuously, every 15 minutes')
    ap.add_argument('--interval', type=int, default=15,
                    help='Watch interval in minutes')
    args = ap.parse_args()
    
    state_file = Path(args.state_file)
    
    while True:
        log.info("=" * 60)
        log.info("Starting Outlook export cycle")
        
        # Load state
        state = load_state(state_file)
        last_run_str = state.get("last_run")
        only_newer_than = None
        if last_run_str:
            try:
                only_newer_than = datetime.fromisoformat(last_run_str)
                log.info(f"Last run: {only_newer_than}")
                log.info(f"Only fetching emails newer than {only_newer_than}")
            except Exception:
                pass
        
        # Export
        try:
            count, last_email_time = export_emails(
                output_folder=args.output,
                mailbox_name=args.mailbox,
                subject_filter=args.subject_filter,
                only_newer_than=only_newer_than,
            )
            
            log.info(f"✓ Exported {count} new emails")
            
            # Save state
            new_state = {
                "last_run": (last_email_time or datetime.now()).isoformat(),
                "exported_count": state.get("exported_count", 0) + count,
                "output_folder": args.output,
            }
            save_state(state_file, new_state)
            log.info(f"State saved: {new_state}")
            
        except Exception as e:
            log.error(f"Export cycle failed: {e}")
        
        if not args.watch:
            break
        
        log.info(f"Sleeping for {args.interval} minutes...")
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
