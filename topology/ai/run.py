"""
Standalone entry point for the AI RCA trigger.

Usage:
    python -m ai.run                              # start the background poller
    python -m ai.run --once                       # drain pending events and exit
    python -m ai.run --bootstrap-kb               # create a starter KB folder
    python -m ai.run --provider openai --model gpt-5
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .analyzer import KnowledgeBase, RCAAnalyzer
from .llm import get_default_client
from .trigger import DEFAULT_KB_ROOT, RCATrigger

# Make backend importable when invoked as a module.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import pool  # noqa: E402


def bootstrap_kb(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "errors").mkdir(exist_ok=True)
    (target / "sources").mkdir(exist_ok=True)
    sample = target / "errors" / "EXAMPLE_DEVICE_DOWN.md"
    if not sample.exists():
        sample.write_text(
            "# Example: <DEVICE> down\n\n"
            "## Symptoms\n- Device stops responding to polls.\n\n"
            "## Likely causes\n1. Power loss.\n2. Switchport error-disabled.\n\n"
            "## Resolution\n1. Verify PDU feed.\n2. `shut` / `no shut` the port.\n"
            "3. Check `show interfaces status` for err-disable reason.\n",
            encoding="utf-8",
        )
    print(f"Starter KB created at {target}")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("ai.run")

    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Drain pending and exit")
    ap.add_argument("--bootstrap-kb", action="store_true", help="Create starter KB")
    ap.add_argument("--kb", type=Path, default=DEFAULT_KB_ROOT)
    ap.add_argument("--provider", choices=["openai", "ollama"], default=None,
                    help="Override LLM_PROVIDER env var")
    ap.add_argument("--model", default=None,
                    help="Override LLM_MODEL env var (e.g. gpt-5)")
    ap.add_argument("--ollama-url", default=None,
                    help="Override LLM_BASE_URL when provider=ollama")
    ap.add_argument("--is-postgres", action="store_true",
                    help="Use Postgres DDL instead of SQL Server")
    args = ap.parse_args()

    if args.bootstrap_kb:
        bootstrap_kb(args.kb)
        if args.once:
            return

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model
    if args.ollama_url:
        os.environ["LLM_BASE_URL"] = args.ollama_url

    llm = get_default_client()
    analyzer = RCAAnalyzer(KnowledgeBase(args.kb), llm=llm)
    trigger = RCATrigger(analyzer, pool, is_postgres=args.is_postgres)

    if args.once:
        n = trigger.run_until_drained()
        log.info("Drained %d findings.", n)
        return

    trigger.start()
    log.info("AI RCA trigger running. Ctrl+C to stop.")
    try:
        while True:
            import time; time.sleep(60)
    except KeyboardInterrupt:
        log.info("Stopping...")
        trigger.stop()


if __name__ == "__main__":
    main()
