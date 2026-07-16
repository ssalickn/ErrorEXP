"""
LLM client abstraction.

The RCA analyzer only cares that the client exposes a
`generate(system, user) -> str` method. Two implementations are
provided:

  - OllamaClient    : local model (e.g. the M3 in your Modelfile).
  - OpenAIClient    : any OpenAI-compatible Chat Completions API,
                      including OpenAI, Azure OpenAI, OpenRouter, and
                      a local proxy that speaks the same shape.

The choice is driven by config (env vars / settings.yaml). No code
change is needed to switch providers.
"""
from __future__ import annotations

import logging
import os
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Protocol
# --------------------------------------------------------------------------- #

class LLMClient(Protocol):
    """Minimal interface required by RCAAnalyzer."""

    def generate(self, system: str, user: str, *, temperature: float = 0.2) -> str: ...
    def close(self) -> None: ...


def get_default_client() -> LLMClient:
    """Build a client from environment variables.

    Env vars:
      LLM_PROVIDER       "ollama" (default) or "openai"
      LLM_MODEL          model name (default: "topology-rca" for ollama,
                         "gpt-5" for openai)
      LLM_BASE_URL       provider base URL
                         (default: http://localhost:11434 for ollama,
                          https://api.openai.com/v1 for openai)
      LLM_API_KEY        API key (REQUIRED for openai; reads
                         OPENAI_API_KEY if LLM_API_KEY is unset)
      LLM_TIMEOUT_S      request timeout in seconds (default 60)
    """
    provider = (os.environ.get("LLM_PROVIDER") or "ollama").lower()
    timeout = float(os.environ.get("LLM_TIMEOUT_S", "60"))

    if provider == "openai":
        model = os.environ.get("LLM_MODEL") or "gpt-5"
        base_url = os.environ.get("LLM_BASE_URL") or "https://api.openai.com/v1"
        api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        if not api_key:
            raise RuntimeError(
                "LLM_PROVIDER=openai requires LLM_API_KEY (or OPENAI_API_KEY) "
                "to be set in the environment."
            )
        return OpenAIClient(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_s=timeout,
        )

    # default: local ollama
    return OllamaClient(
        base_url=os.environ.get("LLM_BASE_URL", "http://localhost:11434"),
        model=os.environ.get("LLM_MODEL", "topology-rca"),
        timeout_s=timeout,
    )


# --------------------------------------------------------------------------- #
# Implementations
# --------------------------------------------------------------------------- #

class OllamaClient:
    """Minimal Ollama /api/generate client. Matches the Modelfile in this repo."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "topology-rca",
                 timeout_s: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=timeout_s)

    def generate(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        payload = {
            "model": self.model,
            "system": system,
            "prompt": user,
            "stream": False,
            "options": {"temperature": temperature},
        }
        r = self._client.post(f"{self.base_url}/api/generate", json=payload)
        r.raise_for_status()
        return r.json().get("response", "")

    def close(self) -> None:
        self._client.close()


class OpenAIClient:
    """OpenAI-compatible Chat Completions client.

    Works with:
      - OpenAI           (gpt-5, gpt-4o, ...)
      - Azure OpenAI     (set LLM_BASE_URL to your Azure endpoint)
      - OpenRouter       (https://openrouter.ai/api/v1)
      - Local proxies    (vLLM, LM Studio, llama.cpp, etc.) that serve the
                         /chat/completions schema.
    """

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-5",
        api_key: str = "",
        timeout_s: float = 60.0,
        organization: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.organization = organization
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if organization:
            headers["OpenAI-Organization"] = organization
        self._client = httpx.Client(timeout=timeout_s, headers=headers)

    def generate(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        """Call /chat/completions and return the assistant message text."""
        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        url = f"{self.base_url}/chat/completions"
        r = self._client.post(url, json=payload)
        if r.status_code >= 400:
            # Log the body for easier debugging of bad keys / model names.
            logger.error("LLM %s -> %s: %s", url, r.status_code, r.text[:500])
        r.raise_for_status()
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM returned no choices: {data}")
        msg = choices[0].get("message") or {}
        content = msg.get("content", "")
        if isinstance(content, list):
            # Some providers return a list of {type, text} parts.
            content = "".join(p.get("text", "") for p in content if p.get("type") == "text")
        return content

    def close(self) -> None:
        self._client.close()
