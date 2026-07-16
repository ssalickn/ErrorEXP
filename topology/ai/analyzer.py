"""
AI-assisted root cause analyzer.

Loads a *knowledge base* (error source snippets + resolution docs) and a
freshly-arrived device event, then asks an LLM (configurable: OpenAI,
Azure OpenAI, OpenRouter, a local OpenAI-compatible proxy, or Ollama)
to produce:

  - root_cause:    why the device is down / errored
  - actions:       ordered, concrete remediation steps
  - confidence:    0.0 - 1.0
  - citations:     which KB chunks informed the answer

The LLM is *not* trained or served by this module. The user provides a
model via an OpenAI-compatible API key (e.g. GPT-5). The analyzer
only handles KB retrieval, prompt construction, and JSON parsing.

The KB is vector-free (keyword + source-code lookup) so it works on any
machine without FAISS / pgvector. Swap to embeddings later if needed.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import yaml

from .llm import LLMClient, OllamaClient

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


@dataclass
class Finding:
    """Structured RCA result for a single event."""
    log_id: int
    device_id: str
    root_cause: str
    actions: list[str]
    confidence: float
    citations: list[str]
    raw: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Knowledge base
# --------------------------------------------------------------------------- #

class KnowledgeBase:
    """A small, file-backed KB of {error_code -> {source, resolution}} docs.

    Layout on disk (all optional, KB is best-effort):

        ai/kb/
          errors/
            CISCO_AP_OFFLINE.md
            NVR_DISK_FULL.md
            CAMERA_AUTH_FAILED.md
            ...
          sources/
            ap_firmware_3.2.1_snippet.c
            onvif_error_codes.txt
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.errors_dir = self.root / "errors"
        self.sources_dir = self.root / "sources"
        self._cache: Optional[list[dict]] = None

    def _load(self) -> list[dict]:
        if self._cache is not None:
            return self._cache

        chunks: list[dict] = []

        # 1. Resolution docs.
        if self.errors_dir.exists():
            for p in sorted(self.errors_dir.glob("*.md")):
                chunks.append({
                    "source": p.name,
                    "kind": "resolution_doc",
                    "text": p.read_text(encoding="utf-8", errors="ignore"),
                })
            for p in sorted(self.errors_dir.glob("*.txt")):
                chunks.append({
                    "source": p.name,
                    "kind": "resolution_doc",
                    "text": p.read_text(encoding="utf-8", errors="ignore"),
                })

        # 2. Source code / error code dumps.
        if self.sources_dir.exists():
            for p in sorted(self.sources_dir.rglob("*")):
                if p.is_file() and p.suffix in {".c", ".h", ".py", ".js", ".txt", ".log"}:
                    chunks.append({
                        "source": str(p.relative_to(self.root)),
                        "kind": "source_code",
                        "text": p.read_text(encoding="utf-8", errors="ignore"),
                    })

        self._cache = chunks
        logger.info("Loaded %d KB chunks from %s", len(chunks), self.root)
        return self._cache

    # -- public API ---------------------------------------------------------- #

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Naive keyword-ranked retrieval. Replace with embeddings later."""
        chunks = self._load()
        if not chunks or not query.strip():
            return []

        tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) > 2]
        if not tokens:
            return []

        scored: list[tuple[float, dict]] = []
        for ch in chunks:
            text_lower = ch["text"].lower()
            score = sum(text_lower.count(tok) for tok in tokens)
            if score > 0:
                # Prefer resolution docs over raw source.
                if ch["kind"] == "resolution_doc":
                    score *= 1.5
                scored.append((score, ch))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ch for _, ch in scored[:top_k]]

    def format_for_prompt(self, query: str, top_k: int = 5, max_chars: int = 12_000) -> str:
        hits = self.search(query, top_k=top_k)
        if not hits:
            return "(no matching knowledge base entries)"

        blocks: list[str] = []
        total = 0
        for h in hits:
            body = h["text"]
            # Truncate very long source files to keep prompt reasonable.
            if len(body) > 2_500:
                body = body[:2_500] + "\n... [truncated] ..."
            block = f"--- KB[{h['kind']}]: {h['source']} ---\n{body}\n"
            if total + len(block) > max_chars:
                break
            blocks.append(block)
            total += len(block)
        return "\n".join(blocks)


# --------------------------------------------------------------------------- #
# Analyzer
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """You are a senior network operations engineer.
You diagnose why a specific device is down or erroring.
You always respond with strict JSON of the form:

{
  "root_cause": "<one-paragraph explanation>",
  "actions": ["<step 1>", "<step 2>", "<step 3>"],
  "confidence": 0.0,
  "citations": ["<filename>", "..."]
}

Rules:
  - Cite the KB chunk filename(s) you used under "citations".
  - If the KB does not contain a match, say so in root_cause and set
    confidence <= 0.4.
  - Actions must be concrete (commands, GUI menus, vendor docs).
  - Never invent device IDs or IPs not present in the input.
"""


class RCAAnalyzer:
    """High-level facade: (event_dict, device_context) -> Finding."""

    def __init__(
        self,
        kb: KnowledgeBase,
        llm: Optional[LLMClient] = None,
        settings_path: Path = DEFAULT_SETTINGS_PATH,
    ):
        self.kb = kb
        # Default to a local Ollama client if nothing is provided; the
        # caller (main.py / run.py) usually passes an OpenAIClient.
        self.llm = llm or OllamaClient()
        self._settings = self._load_settings(settings_path)

    @staticmethod
    def _load_settings(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return yaml.safe_load(path.read_text()) or {}
        except Exception as e:  # pragma: no cover
            logger.warning("Could not read settings.yaml: %s", e)
            return {}

    # -- main entry point --------------------------------------------------- #

    def analyze(self, event: dict, device: Optional[dict] = None) -> Finding:
        log_id = int(event.get("log_id", 0))
        device_id = str(event.get("device_id", "unknown"))

        # 1. Build a retrieval query from the event signature.
        query_bits = [
            str(event.get("status_code") or ""),
            str(event.get("status") or ""),
            str(event.get("severity") or ""),
            str(event.get("device_type") or ""),
            str(event.get("message") or "")[:300],
        ]
        query = " ".join(b for b in query_bits if b).strip()

        # 2. Retrieve relevant KB chunks.
        kb_text = self.kb.format_for_prompt(query, top_k=5)

        # 3. Compose the user prompt.
        user_prompt = self._build_user_prompt(event, device, kb_text)

        # 4. Ask the LLM.
        try:
            raw = self.llm.generate(SYSTEM_PROMPT, user_prompt)
            parsed = self._parse_json(raw)
        except Exception as e:
            logger.exception("LLM call failed for log_id=%s", log_id)
            parsed = {
                "root_cause": f"AI analyzer unavailable: {e}",
                "actions": ["Manually inspect device and recent log entry."],
                "confidence": 0.0,
                "citations": [],
            }

        return Finding(
            log_id=log_id,
            device_id=device_id,
            root_cause=str(parsed.get("root_cause", "")).strip(),
            actions=[str(a).strip() for a in parsed.get("actions", []) if str(a).strip()],
            confidence=float(parsed.get("confidence", 0.0) or 0.0),
            citations=[str(c).strip() for c in parsed.get("citations", []) if str(c).strip()],
            raw=parsed,
        )

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _build_user_prompt(event: dict, device: Optional[dict], kb_text: str) -> str:
        device_block = json.dumps(device, indent=2, default=str) if device else "(no device record)"
        event_block = json.dumps(event, indent=2, default=str)
        return (
            "A new event just landed in the monitoring database.\n\n"
            "## Device context\n```json\n"
            f"{device_block}\n```\n\n"
            "## Event\n```json\n"
            f"{event_block}\n```\n\n"
            "## Relevant knowledge base excerpts\n"
            f"{kb_text}\n\n"
            "Produce the JSON response described in the system prompt."
        )

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Tolerate ```json fences and stray prose around the JSON."""
        text = text.strip()
        # Strip code fences.
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        # Grab the first {...} block.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {
                "root_cause": text[:500],
                "actions": [],
                "confidence": 0.0,
                "citations": [],
            }
