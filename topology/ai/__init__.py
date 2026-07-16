"""
AI-assisted root cause analysis package.

Modules:
  - llm         : LLM client abstraction (Ollama, OpenAI-compatible).
  - analyzer    : per-event LLM diagnosis (RCAAnalyzer).
  - correlator  : cluster detector that picks the single root-cause
                  device when many devices fail at once.
  - trigger     : DB poller that fans new events into the analyzer
                  and the correlator, and persists results.
"""
from .analyzer import Finding, KnowledgeBase, RCAAnalyzer
from .correlator import (
    CandidateScore,
    Cascade,
    CascadeCorrelator,
    FailingDevice,
    TopologyGraph,
)
from .llm import LLMClient, OllamaClient, OpenAIClient, get_default_client
from .trigger import RCATrigger

__all__ = [
    # analyzer
    "Finding",
    "KnowledgeBase",
    "RCAAnalyzer",
    # correlator
    "FailingDevice",
    "CandidateScore",
    "Cascade",
    "CascadeCorrelator",
    "TopologyGraph",
    # llm
    "LLMClient",
    "OllamaClient",
    "OpenAIClient",
    "get_default_client",
    # trigger
    "RCATrigger",
]
