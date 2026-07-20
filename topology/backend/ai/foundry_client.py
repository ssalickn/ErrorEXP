"""
Microsoft Foundry (Azure AI Inference) client for root-cause analysis.

Mirrors the foun.py proof-of-concept which successfully hits the same endpoint
using the native OpenAI SDK's ``responses`` API (not ``chat.completions``):

    POST {project_endpoint}/openai/v1/responses
    Headers: Authorization: Bearer <key>   Content-Type: application/json
    Body:    { "model": "<deployment>", "input": "...", "instructions": "..." }

Returns a structured RCA: summary, root_cause_device, confidence,
recommended_actions[], severity. Robust to non-JSON or partial replies
so the dashboard keeps working when the model is misbehaving.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dotenv import load_dotenv
from typing import Any, Optional

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

logger = logging.getLogger(__name__)

# Severity ordering — used to decide which events warrant AI analysis
SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}

# Defaults — match the working foun.py configuration
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

SYSTEM_PROMPT = """You are a senior network operations engineer analyzing an IoT/security topology outage.

You will receive:
- The device that just went offline (or generated the alert)
- The recent event log lines for that device and its neighbors
- Its upstream dependencies and any sibling devices that are also currently offline
- The available device types and site context

Your job: identify the MOST LIKELY root-cause device and give a concise, actionable
remediation plan an on-call tech can execute in under 30 minutes.

Respond with a SINGLE JSON object — no prose, no markdown, no code fences — matching
this exact schema:

{
  "summary": "<one or two sentence plain-English explanation of what is happening>",
  "root_cause_device_id": "<device_id most likely causing this outage, or 'unknown'>",
  "root_cause_device_type": "<device type of the root cause, e.g. cisco_switch, nvr, camera>",
  "confidence": <float 0.0..1.0 representing how sure you are>,
  "severity": "<info|warning|error|critical>",
  "blast_radius": [<list of device_ids currently impacted downstream>],
  "recommended_actions": [
    "<step 1, imperative, concrete>",
    "<step 2>",
    "<step 3>"
  ],
  "rationale": "<2-3 sentence technical reasoning that cites the evidence>"
}

Rules:
- Always return valid JSON. If unsure, return confidence < 0.4 and root_cause_device_id = "unknown".
- recommended_actions must be 1-6 short imperative steps.
- Never invent device_ids that are not present in the provided context.
"""


class FoundryClient:
    """Thin synchronous client over the Foundry Responses API (OpenAI SDK)."""

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
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_json(self, text: str) -> dict[str, Any]:
        """Tolerate markdown code fences and stray prose around the JSON body."""
        if not text:
            return {}
        text = text.strip()
        # Strip ```json ... ``` fences if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```\s*$", "", text)
        # Find the outermost {...} block
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        candidate = match.group(0) if match else text
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse Foundry JSON: %s | text=%r", e, text[:400])
            return {}

    def _extract_text(self, response: Any) -> str:
        """Pull the assistant text out of a Responses API object.

        ``response.output`` is a list of output items. Message-type items
        carry ``.content`` which is itself a list of parts; each part with
        a ``.text`` attribute contributes the visible reply. We ignore
        non-message items (e.g. reasoning traces) and concatenate any
        message text we find.
        """
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
        """Call the Responses API — same pattern as foun.py."""
        logger.info(
            "Calling Foundry Responses API: model=%s endpoint=%s",
            self.model_deployment,
            self.endpoint,
        )
        response = self._client.responses.create(
            model=self.model_deployment,
            instructions=SYSTEM_PROMPT,  # system / role instructions
            input=user_input,            # user payload (string)
            temperature=temperature,
            max_output_tokens=900,
        )
        text = self._extract_text(response)
        if not text:
            # Surface a clear error so retry / fallback logic can react
            raise RuntimeError(
                f"Foundry returned no text content. raw={response!r}"
            )
        return text

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Health check — does a trivial completion."""
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
        user_payload = {
            "device_under_investigation": device,
            "recent_events_for_device": recent_events[:30],
            "upstream_dependencies": upstream[:30],
            "downstream_dependencies": downstream[:30],
            "other_offline_devices": offline_siblings[:30],
            "site_context": site_context or {},
        }
        user_input = (
            "Perform a root-cause analysis for the following outage. "
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
                "root_cause_device_id": "unknown",
                "root_cause_device_type": "unknown",
                "confidence": 0.0,
                "severity": device.get("status") or "warning",
                "blast_radius": [],
                "recommended_actions": [
                    "Check Foundry configuration (endpoint, api-key, model deployment).",
                    "Verify the deployment is running in the Microsoft Foundry portal.",
                ],
                "rationale": "",
                "elapsed_s": round(time.time() - started, 2),
            }

        parsed = self._extract_json(raw)
        if not parsed:
            return {
                "ok": False,
                "error": "Model response was not valid JSON.",
                "summary": (raw or "")[:280] or "AI returned no content.",
                "root_cause_device_id": "unknown",
                "root_cause_device_type": "unknown",
                "confidence": 0.0,
                "severity": device.get("status") or "warning",
                "blast_radius": [],
                "recommended_actions": ["Re-run analysis; previous response was malformed."],
                "rationale": "",
                "elapsed_s": round(time.time() - started, 2),
            }

        # Normalize to the contract the dashboard expects
        return {
            "ok": True,
            "summary": str(parsed.get("summary") or "").strip(),
            "root_cause_device_id": str(parsed.get("root_cause_device_id") or "unknown"),
            "root_cause_device_type": str(parsed.get("root_cause_device_type") or "unknown"),
            "confidence": float(parsed.get("confidence") or 0.0),
            "severity": str(parsed.get("severity") or device.get("status") or "warning"),
            "blast_radius": [str(x) for x in (parsed.get("blast_radius") or [])],
            "recommended_actions": [
                str(x).strip()
                for x in (parsed.get("recommended_actions") or [])
                if str(x).strip()
            ][:6],
            "rationale": str(parsed.get("rationale") or "").strip(),
            "elapsed_s": round(time.time() - started, 2),
        }


# Module-level singleton (lazy)
_client: Optional[FoundryClient] = None


def get_client() -> FoundryClient:
    global _client
    if _client is None:
        _client = FoundryClient()
    return _client
