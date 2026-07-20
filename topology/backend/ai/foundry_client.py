"""
Microsoft Foundry (Azure AI Inference) client for root-cause analysis.

Calls the Responses API at the Foundry project endpoint using the native OpenAI
SDK — same pattern as foun.py:

    POST {project_endpoint}/responses
    Headers: api-key: <key>   Content-Type: application/json

Features:
- Pre-LLM deterministic scoring (catches obvious cascades before burning tokens)
- Topology-aware blast radius computation
- Closed-set device universe with vendor runbook hints baked into the system prompt
- Post-LLM response validation and repair
- Robust to non-JSON or partial replies; never raises
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from dotenv import load_dotenv
from typing import Any, Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

logger = logging.getLogger(__name__)

# Severity ordering — used to decide which events warrant AI analysis
SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}

DEFAULT_ENDPOINT = os.environ.get(
    "FOUNDRY_PROJECT_ENDPOINT",
    "https://nirnu-itopssmartmonitor.services.ai.azure.com/openai/v1",
)
DEFAULT_KEY = (
    os.environ.get("FOUNDRY_API_KEY")
    or os.environ.get("AZURE_OPENAI_API_KEY")
    or os.environ.get("OPENAI_API_KEY")
)
DEFAULT_MODEL = os.environ.get(
    "FOUNDRY_MODEL_DEPLOYMENT", "NirnuSmartMonitor_GPT"
)

# Closed-set device universe for this Nucor site
ALLOWED_DEVICE_TYPES = {
    "cisco_switch",
    "nvr",
    "camera",
    "access_control_panel",
    "biometric_reader",
    "access_point",
    "unknown",
}

# Aliases for normalizing DB device_type strings into the closed set
_TYPE_ALIASES = {
    "biostar_reader": "biometric_reader",
    "biostar_panel": "access_control_panel",
    "biostar_ap": "access_point",
    "honeywell_camera": "camera",
    "hikvision_nvr": "nvr",
    "genetec_nvr": "nvr",
    "avigilon_nvr": "nvr",
    "cisco_cat": "cisco_switch",
    "cisco_nexus": "cisco_switch",
    "cisco_switch": "cisco_switch",
    "switch": "cisco_switch",
    "nvr": "nvr",
    "camera": "camera",
    "access_control_panel": "access_control_panel",
    "biometric_reader": "biometric_reader",
    "access_point": "access_point",
}


def normalize_device_type(t: Any) -> str:
    """Map any DB device_type string to the closed-set universe."""
    if not t:
        return "unknown"
    s = str(t).strip().lower()
    return _TYPE_ALIASES.get(s, s)


# Keywords that hint at specific root causes in event messages
_SIGNAL_PATTERNS = [
    # (regex, suspected_device_type, weight, reason)
    (re.compile(r"\b(poe|power\s*inline|power\s*budget)\b", re.I), "cisco_switch", 0.4, "PoE-related event"),
    (re.compile(r"\b(stack|stack-?member|stacking)\b", re.I), "cisco_switch", 0.5, "Stacking event"),
    (re.compile(r"\b(sfp|xfp|gbic|optic|uplink)\b", re.I), "cisco_switch", 0.4, "Uplink/optics event"),
    (re.compile(r"\b(vlan|trunk|spanning[- ]tree|stp)\b", re.I), "cisco_switch", 0.3, "L2 config event"),
    (re.compile(r"\b(license|licensing)\b", re.I), "nvr", 0.3, "License event"),
    (re.compile(r"\b(archive|storage|disk|raid)\b", re.I), "nvr", 0.4, "Storage event"),
    (re.compile(r"\b(camera\s*re-?register|camera\s*offline|cameras?\s+drop)\b", re.I), "nvr", 0.5, "Camera registration cascade"),
    (re.compile(r"\b(biostar\s*2?\s*server|template\s*sync)\b", re.I), "biometric_reader", 0.4, "BioStar server/template event"),
    (re.compile(r"\b(wiegand|reader|lock|door\s*contact)\b", re.I), "access_control_panel", 0.4, "Access-control wiring event"),
    (re.compile(r"\b(controller|capwap|join)\b", re.I), "access_point", 0.4, "Wireless controller event"),
    (re.compile(r"\b(dhcp|ip\s*conflict|arp)\b", re.I), "camera", 0.2, "IP/DHCP event"),
]


SYSTEM_PROMPT = """You are a senior network operations engineer working at a Nucor steel-mill site.

# CLOSED-SET DEVICE UNIVERSE
This site ONLY has the following device types. If the most likely root cause is not
one of these, set root_cause_device_id = "unknown" and root_cause_device_type = "unknown".
Do NOT invent or speculate about other device types (no firewalls, no servers, no
workstations, no IoT sensors, no UPSes, no environmental monitors, no servers).

- cisco_switch         (network)            — Cisco Catalyst / Nexus; PoE, stacking, uplinks, VLAN trunks
- nvr                  (video_recording)    — Genetec / Hikvision / Avigilon; cold-boot ordering matters
- camera               (video_surveillance) — Honeywell; PoE-powered, firmware, DHCP
- access_control_panel (access_control)     — BioStar panels; Wiegand wiring, lock power, door contacts
- biometric_reader     (access_control)     — BioStar fingerprint / face readers; template sync to BioStar 2 server
- access_point         (wireless_access)    — BioStar APs; PoE-powered, controller reachability

# KNOWN FAILURE MODES (cite these in rationale when evidence matches)
- cisco_switch: stacking member failure, PoE budget exceeded, uplink SFP failure, VLAN trunk misconfig
- nvr: cold boot requires cameras to re-register AFTER NVR is back; storage full / disk failure; license server unreachable; archive DB corruption
- camera: PoE drop from upstream switch, firmware crash after power blip, IP conflict after DHCP renewal, factory reset = physical button hold 10s
- access_control_panel: panel-to-server link down, door contact sensor failure, lock power supply failure, schedule misconfig after BioStar 2 restart
- biometric_reader: BioStar 2 server unreachable, template sync failure after BioStar restart, reader head dirty/worn, Wiegand wiring fault
- access_point: PoE drop from upstream switch, controller unreachable, channel interference, firmware mismatch

# VENDOR RUNBOOK HINTS (cite in recommended_actions)
- Cisco switches: 'show stack', 'show power inline', 'show interfaces status', 'show logging'
- NVRs: verify NVR service is up BEFORE troubleshooting cameras; check archive storage volume
- Honeywell cameras: ping from NVR to verify reachability; hold reset button 10s for factory default
- BioStar 2: verify panel/reader heartbeat in BioStar 2 device log; reseat Wiegand connector at panel

# SITE PRIORITIES
- Safety systems > production visibility > office IT.
- A single tier-1 device outage is CRITICAL.
- NVRs MUST come online BEFORE their cameras can re-register.
- BioStar panels and readers MUST reach the BioStar 2 server for normal operation.
- For BioStar outages, suspect the server FIRST, then the panel, then the reader.

# TOPOLOGY REASONING RULES
- A device is most likely the root cause if it sits at the TOP of a cascade
  (upstream of multiple downstream devices that are also offline).
- A device is UNLIKELY to be the root cause if many of its upstream dependencies
  are also offline (then the upstream device is the more likely cause).
- Multiple sibling devices going offline at the same time is strong evidence
  of a shared upstream cause (typically a switch, NVR, or BioStar server).
- A single isolated device going offline is most likely a local cause
  (PoE drop, lock power, firmware crash, dirty reader head).
- Time correlation matters: events within 5 minutes of each other are likely related.
- Direction matters: 'downstream' from A means A is upstream of B (B depends on A).

# SEVERITY RUBRIC
- critical: any tier-1 device offline, OR >50% of devices in a site offline, OR
            a switch/NVR/panel taking down a safety/visibility cluster
- error:    single non-tier-1 device with multiple downstream impacts (>5)
- warning:  single non-tier-1 device with limited downstream impact
- info:     everything else, including flap/recovery

# INPUTS YOU WILL RECEIVE
- The device under investigation, with its device_type, vendor, model, status
- Recent event log lines (last 30) with severity, message, source_system
- Upstream dependencies (devices THIS device depends on)
- Downstream dependencies (devices that depend on THIS device)
- Other currently-offline devices (likely siblings of the same root cause)
- A local_rca_hypothesis: deterministic pre-analysis suggesting a candidate
  root cause based on event signal matching. Treat it as a strong prior.
- A topology summary: blast_radius size, sibling count, time-window stats

# YOUR JOB
1. Decide the most likely root cause device_id and root_cause_device_type.
2. Assign a severity using the rubric above.
3. List the impacted downstream devices in blast_radius.
4. Give a 1-6 step recommended_actions plan an on-call tech can execute in <30 min.
5. Cite evidence in rationale (event messages, time correlation, topology).

# OUTPUT SCHEMA (SINGLE JSON object, no prose, no markdown, no code fences)
{
  "summary": "<one or two sentence plain-English explanation>",
  "root_cause_device_id": "<device_id most likely causing this outage, or 'unknown'>",
  "root_cause_device_type": "<one of: cisco_switch, nvr, camera, access_control_panel, biometric_reader, access_point, or 'unknown'>",
  "confidence": <float 0.0..1.0>,
  "severity": "<info|warning|error|critical>",
  "blast_radius": [<list of device_ids currently impacted downstream>],
  "recommended_actions": [
    "<step 1, imperative, concrete, citing vendor command from runbook hints>",
    "<step 2>",
    "<step 3>"
  ],
  "rationale": "<2-3 sentence technical reasoning citing evidence AND a known failure mode from the device universe>"
}

# RULES
- Always return valid JSON. If unsure, return confidence < 0.4 and root_cause_device_id = "unknown".
- Bump severity to 'critical' if any tier-1 device is in the blast_radius.
- recommended_actions must be 1-6 short imperative steps.
- Never invent device_ids that are not present in the provided context.
- Root cause MUST be one of the closed-set device types listed above, or "unknown".
- For camera cascades, the NVR is almost always the root cause, not the cameras.
- For BioStar cascades, the BioStar 2 server or upstream switch is almost always the root cause.
- Prefer concrete, executable actions over generic advice.
"""


class FoundryClient:
    """Synchronous client wrapping the Foundry Responses API (OpenAI SDK)."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        api_key: str = DEFAULT_KEY,
        model_deployment: str = DEFAULT_MODEL,
        timeout_s: float = 45.0,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.model_deployment = model_deployment

        if not self.api_key:
            raise RuntimeError(
                "No API key found. Set FOUNDRY_API_KEY (or OPENAI_API_KEY) in your env / .env."
            )

        # Azure AI Foundry /openai/v1 uses the `api-key` header, not `Authorization: Bearer`.
        # Pass a placeholder api_key so the SDK never emits Bearer, then attach the real
        # key via default_headers.
        self._client = OpenAI(
            base_url=self.endpoint,
            api_key="placeholder",
            timeout=timeout_s,
            default_headers={"api-key": self.api_key},
        )

    # ------------------------------------------------------------------
    # Deterministic pre-LLM scoring
    # ------------------------------------------------------------------

    def _score_root_cause(
        self,
        device: dict[str, Any],
        recent_events: list[dict[str, Any]],
        upstream: list[dict[str, Any]],
        downstream: list[dict[str, Any]],
        offline_siblings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run a quick local RCA pass before calling the LLM.

        Returns a dict with a candidate root cause, score, and reasoning
        that we feed into the LLM as a strong prior.
        """
        scores: dict[str, float] = defaultdict(float)
        evidence: list[str] = []

        device_type = normalize_device_type(device.get("device_type"))
        sibling_types = [normalize_device_type(s.get("device_type")) for s in offline_siblings]
        sibling_count = len(offline_siblings)

        # 1. Signal matching on recent events
        for evt in recent_events[:30]:
            msg = str(evt.get("message") or "")
            sev = str(evt.get("severity") or "").lower()
            sev_weight = {"info": 0.1, "warning": 0.2, "error": 0.4, "critical": 0.6}.get(sev, 0.1)
            for pat, dtype, weight, reason in _SIGNAL_PATTERNS:
                if pat.search(msg):
                    scores[dtype] += weight * sev_weight
                    evidence.append(f"{reason} ({sev}): {msg[:120]}")

        # 2. Topology priors
        #    If the device under investigation is downstream of many offline siblings,
        #    it's a victim, not a cause.
        n_downstream = len(downstream)
        n_upstream = len(upstream)

        if n_downstream >= 5 and sibling_count >= 5:
            scores["cisco_switch"] += 0.4
            evidence.append(
                f"Device has {n_downstream} downstream deps; {sibling_count} siblings also offline — likely switch upstream"
            )

        if device_type == "nvr" and sibling_count >= 3:
            scores["nvr"] += 0.5
            evidence.append(
                f"NVR offline with {sibling_count} sibling cameras — NVR is almost certainly the root cause"
            )

        if device_type in ("biometric_reader", "access_control_panel") and sibling_count >= 3:
            scores["access_control_panel"] += 0.4
            scores["biometric_reader"] += 0.2
            evidence.append(
                f"BioStar device offline with {sibling_count} siblings — upstream panel or server likely"
            )

        # 3. If many siblings share the device's upstream, the upstream switch is suspect
        if sibling_count >= 5 and n_upstream >= 1:
            # Sibling's upstream might be this device's upstream — pick the device
            scores["cisco_switch"] += 0.3
            evidence.append(
                f"{sibling_count} siblings offline; suspect shared upstream switch"
            )

        # 4. If the device is a switch with PoE-related events, it's a strong PoE cause
        if device_type == "cisco_switch":
            poe_events = sum(1 for e in recent_events if re.search(r"\bpoe\b", str(e.get("message") or ""), re.I))
            if poe_events >= 2:
                scores["cisco_switch"] += 0.4
                evidence.append(f"{poe_events} PoE-related events on this switch — strong PoE cause candidate")

        # 5. Pick the winner
        if not scores:
            return {
                "candidate_device_id": device.get("device_id"),
                "candidate_device_type": device_type,
                "score": 0.0,
                "reasoning": "No strong local signals; deferring to LLM.",
            }

        winner_type, winner_score = max(scores.items(), key=lambda kv: kv[1])
        # Clamp to [0, 1]
        winner_score = min(winner_score, 1.0)

        return {
            "candidate_device_id": device.get("device_id"),
            "candidate_device_type": winner_type if winner_type in ALLOWED_DEVICE_TYPES else "unknown",
            "score": round(winner_score, 2),
            "reasoning": " | ".join(evidence[:6]) or "Topology suggests this device type.",
            "signal_breakdown": {k: round(v, 2) for k, v in sorted(scores.items(), key=lambda kv: -kv[1])},
        }

    def _build_topology_summary(
        self,
        upstream: list[dict[str, Any]],
        downstream: list[dict[str, Any]],
        offline_siblings: list[dict[str, Any]],
        recent_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Compact topology stats the model can use to reason about cascades."""
        # Time-window: how many events in the last 5/15/60 minutes
        now = time.time()
        buckets = {"5m": 0, "15m": 0, "60m": 0}
        for e in recent_events[:30]:
            t = e.get("event_time")
            if t is None:
                continue
            try:
                # event_time may be ISO string or datetime
                if hasattr(t, "timestamp"):
                    ts = t.timestamp()
                else:
                    ts = pd_to_ts(t)
                age = now - ts
                if age <= 300:
                    buckets["5m"] += 1
                if age <= 900:
                    buckets["15m"] += 1
                if age <= 3600:
                    buckets["60m"] += 1
            except Exception:
                continue

        return {
            "upstream_count": len(upstream),
            "downstream_count": len(downstream),
            "offline_sibling_count": len(offline_siblings),
            "events_in_last_5m": buckets["5m"],
            "events_in_last_15m": buckets["15m"],
            "events_in_last_60m": buckets["60m"],
            "cascade_suspected": len(offline_siblings) >= 3,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_json(self, text: str) -> dict[str, Any]:
        if not text:
            return {}
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```\s*$", "", text)
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        candidate = match.group(0) if match else text
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse Foundry JSON: %s | text=%r", e, text[:400])
            return {}

    def _extract_text(self, response: Any) -> str:
        parts: list[str] = []
        for item in (getattr(response, "output", None) or []):
            content = getattr(item, "content", None)
            if not content:
                continue
            for c in content:
                txt = getattr(c, "text", None)
                if txt:
                    parts.append(txt)
        return "\n".join(parts).strip()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
    )
    def _post_response(self, user_input: str, temperature: float = 0.2) -> str:
        logger.info(
            "Calling Foundry Responses API: model=%s endpoint=%s",
            self.model_deployment,
            self.endpoint,
        )
        response = self._client.responses.create(
            model=self.model_deployment,
            instructions=SYSTEM_PROMPT,
            input=user_input,
            temperature=temperature,
            max_output_tokens=1000,
        )
        text = self._extract_text(response)
        if not text:
            raise RuntimeError(f"Foundry returned no text content. raw={response!r}")
        return text

    # ------------------------------------------------------------------
    # Post-LLM validation & repair
    # ------------------------------------------------------------------

    def _validate_and_repair(
        self,
        parsed: dict[str, Any],
        device: dict[str, Any],
        upstream_ids: set[str],
        downstream_ids: set[str],
        sibling_ids: set[str],
        known_device_ids: set[str],
    ) -> dict[str, Any]:
        """Sanity-check the LLM response. Returns a repaired dict."""
        # 1. Coerce root_cause_device_type to the closed set
        rct = str(parsed.get("root_cause_device_type") or "").strip().lower()
        if rct not in ALLOWED_DEVICE_TYPES:
            logger.warning("Out-of-universe device_type=%r, downgrading", rct)
            parsed["root_cause_device_type"] = "unknown"
            try:
                parsed["confidence"] = min(float(parsed.get("confidence") or 0.0), 0.3)
            except (TypeError, ValueError):
                parsed["confidence"] = 0.3

        # 2. Validate root_cause_device_id is in the known set
        rcid = str(parsed.get("root_cause_device_id") or "").strip()
        if rcid and rcid != "unknown" and rcid not in known_device_ids:
            logger.warning("Hallucinated root_cause_device_id=%r, downgrading", rcid)
            parsed["root_cause_device_id"] = "unknown"
            try:
                parsed["confidence"] = min(float(parsed.get("confidence") or 0.0), 0.3)
            except (TypeError, ValueError):
                parsed["confidence"] = 0.3

        # 3. Coerce confidence to [0, 1]
        try:
            c = float(parsed.get("confidence") or 0.0)
            parsed["confidence"] = max(0.0, min(1.0, c))
        except (TypeError, ValueError):
            parsed["confidence"] = 0.0

        # 4. Filter blast_radius to known device_ids only
        br = parsed.get("blast_radius") or []
        if not isinstance(br, list):
            br = []
        parsed["blast_radius"] = [str(x) for x in br if str(x) in known_device_ids][:50]

        # 5. Coerce severity to the allowed set
        sev = str(parsed.get("severity") or "").strip().lower()
        if sev not in {"info", "warning", "error", "critical"}:
            parsed["severity"] = device.get("status") or "warning"

        # 6. Coerce recommended_actions to 1-6 short strings
        ra = parsed.get("recommended_actions") or []
        if not isinstance(ra, list):
            ra = []
        parsed["recommended_actions"] = [
            str(x).strip() for x in ra if str(x).strip()
        ][:6]
        if not parsed["recommended_actions"]:
            parsed["recommended_actions"] = [
                "Inspect the candidate root cause device physically.",
                "Check upstream dependencies for shared failures.",
            ]

        return parsed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        try:
            out = self._post_response("Reply with the single word: ok", temperature=0.0)
            return bool(out and out.strip())
        except Exception as e:
            logger.warning("Foundry ping failed: %s", e)
            return False

    def analyze(
        self,
        device: dict[str, Any],
        recent_events: list[dict[str, Any]],
        upstream: list[dict[str, Any]],
        downstream: list[dict[str, Any]],
        offline_siblings: list[dict[str, Any]],
        site_context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Run RCA and return a structured insight dict (never raises)."""
        started = time.time()

        # Normalize device_type
        device = dict(device)
        device["device_type"] = normalize_device_type(device.get("device_type"))

        # 1. Local deterministic RCA hypothesis
        hypothesis = self._score_root_cause(
            device=device,
            recent_events=recent_events,
            upstream=upstream,
            downstream=downstream,
            offline_siblings=offline_siblings,
        )

        # 2. Topology summary
        topo = self._build_topology_summary(
            upstream=upstream,
            downstream=downstream,
            offline_siblings=offline_siblings,
            recent_events=recent_events,
        )

        # 3. Build the known device-id set so the model can't hallucinate
        upstream_ids = {str(d.get("related_id") or d.get("device_id")) for d in upstream}
        downstream_ids = {str(d.get("related_id") or d.get("device_id")) for d in downstream}
        sibling_ids = {str(d.get("device_id")) for d in offline_siblings}
        known_device_ids = (
            {str(device.get("device_id"))}
            | upstream_ids
            | downstream_ids
            | sibling_ids
        )

        # 4. Compose payload
        user_payload = {
            "device_under_investigation": device,
            "recent_events_for_device": recent_events[:30],
            "upstream_dependencies": upstream[:30],
            "downstream_dependencies": downstream[:30],
            "other_offline_devices": offline_siblings[:30],
            "site_context": site_context or {},
            "topology_summary": topo,
            "local_rca_hypothesis": hypothesis,
            "known_device_ids": sorted(known_device_ids),
        }

        user_input = (
            "Perform a root-cause analysis for the following outage. "
            "Use the local_rca_hypothesis as a strong prior. "
            "Return ONLY a JSON object matching the schema.\n\n"
            f"DATA:\n{json.dumps(user_payload, default=str, indent=2)}"
        )

        try:
            raw = self._post_response(user_input)
        except Exception as e:
            logger.error("Foundry call failed: %s", e)
            return {
                "ok": False,
                "error": str(e),
                "summary": "AI analysis unavailable — see error.",
                "root_cause_device_id": hypothesis.get("candidate_device_id") or "unknown",
                "root_cause_device_type": hypothesis.get("candidate_device_type") or "unknown",
                "confidence": hypothesis.get("score", 0.0),
                "severity": self._severity_from_topology(topo, device),
                "blast_radius": sorted(downstream_ids)[:20],
                "recommended_actions": self._fallback_actions(hypothesis, device),
                "rationale": hypothesis.get("reasoning", ""),
                "local_hypothesis": hypothesis,
                "topology_summary": topo,
                "elapsed_s": round(time.time() - started, 2),
            }

        parsed = self._extract_json(raw)
        if not parsed:
            return {
                "ok": False,
                "error": "Model response was not valid JSON.",
                "summary": (raw or "")[:280] or "AI returned no content.",
                "root_cause_device_id": hypothesis.get("candidate_device_id") or "unknown",
                "root_cause_device_type": hypothesis.get("candidate_device_type") or "unknown",
                "confidence": hypothesis.get("score", 0.0),
                "severity": self._severity_from_topology(topo, device),
                "blast_radius": sorted(downstream_ids)[:20],
                "recommended_actions": self._fallback_actions(hypothesis, device),
                "rationale": hypothesis.get("reasoning", ""),
                "local_hypothesis": hypothesis,
                "topology_summary": topo,
                "elapsed_s": round(time.time() - started, 2),
            }

        # 5. Validate and repair
        parsed = self._validate_and_repair(
            parsed=parsed,
            device=device,
            upstream_ids=upstream_ids,
            downstream_ids=downstream_ids,
            sibling_ids=sibling_ids,
            known_device_ids=known_device_ids,
        )

        # 6. Severity cross-check: if LLM said "info" but cascade_suspected=True, bump
        if topo.get("cascade_suspected") and parsed.get("severity") == "info":
            parsed["severity"] = "error"

        return {
            "ok": True,
            "summary": str(parsed.get("summary") or "").strip(),
            "root_cause_device_id": str(parsed.get("root_cause_device_id") or "unknown"),
            "root_cause_device_type": str(parsed.get("root_cause_device_type") or "unknown"),
            "confidence": float(parsed.get("confidence") or 0.0),
            "severity": str(parsed.get("severity") or self._severity_from_topology(topo, device)),
            "blast_radius": [str(x) for x in (parsed.get("blast_radius") or [])],
            "recommended_actions": [
                str(x).strip()
                for x in (parsed.get("recommended_actions") or [])
                if str(x).strip()
            ][:6],
            "rationale": str(parsed.get("rationale") or "").strip(),
            "local_hypothesis": hypothesis,
            "topology_summary": topo,
            "elapsed_s": round(time.time() - started, 2),
        }

    # ------------------------------------------------------------------
    # Fallback helpers (used when the LLM call fails)
    # ------------------------------------------------------------------

    @staticmethod
    def _severity_from_topology(topo: dict[str, Any], device: dict[str, Any]) -> str:
        if topo.get("cascade_suspected"):
            return "critical" if topo.get("offline_sibling_count", 0) >= 10 else "error"
        return device.get("status") or "warning"

    @staticmethod
    def _fallback_actions(hypothesis: dict[str, Any], device: dict[str, Any]) -> list[str]:
        ctype = hypothesis.get("candidate_device_type") or "unknown"
        dtype = device.get("device_type") or "unknown"
        target = ctype if ctype != "unknown" else dtype
        actions_by_type = {
            "cisco_switch": [
                "SSH to the candidate switch and run 'show stack' to verify all members are present.",
                "Run 'show power inline' to check for PoE budget overruns on downstream ports.",
                "Run 'show interfaces status' to find err-disabled or down uplinks.",
                "Check 'show logging' for the most recent error events.",
            ],
            "nvr": [
                "Verify the NVR service is up (Genetec/Hikvision/Avigilon dashboard).",
                "Check archive storage volume; a full disk can take cameras offline.",
                "Confirm the NVR is reachable from its upstream switch.",
                "After NVR recovery, allow 5-10 minutes for cameras to re-register.",
            ],
            "camera": [
                "Ping the camera from the NVR to verify reachability.",
                "Check the upstream switch port PoE status.",
                "If still offline, hold the camera's reset button for 10s to factory default.",
            ],
            "access_control_panel": [
                "Verify the panel heartbeat in BioStar 2.",
                "Reseat the Wiegand connector at the panel.",
                "Check 12/24V lock power supply.",
            ],
            "biometric_reader": [
                "Verify BioStar 2 server reachability from the reader subnet.",
                "Check BioStar 2 device log for 'server disconnected' messages.",
                "Clean the reader head; reseat cabling if needed.",
            ],
            "access_point": [
                "Check upstream switch port PoE status.",
                "Verify controller reachability from the AP subnet.",
                "Cycle AP power to force re-join.",
            ],
            "unknown": [
                "Inspect the candidate device physically.",
                "Check upstream dependencies for shared failures.",
                "Review recent log lines for the candidate and its neighbors.",
            ],
        }
        return actions_by_type.get(target, actions_by_type["unknown"])[:6]


def _pd_to_ts(value: Any) -> float:
    """Best-effort conversion of an event_time value to a unix timestamp."""
    import pandas as _pd
    try:
        return _pd.Timestamp(value).timestamp()
    except Exception:
        return 0.0


# Module-level singleton (lazy)
_client: Optional[FoundryClient] = None


def get_client() -> FoundryClient:
    global _client
    if _client is None:
        _client = FoundryClient()
    return _client
