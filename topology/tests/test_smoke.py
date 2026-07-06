"""Smoke tests for the discovery + merge modules.

Integration tests against a live SNMP device / Neo4j / Postgres should be
added under `tests/integration/`.
"""
from __future__ import annotations

from topology.discovery.tier1.snmp_crawl import _sanitize_device_id
from topology.graph.confidence import edge_confidence, review_action


def test_sanitize_device_id():
    assert _sanitize_device_id("sw-core-07.corp.local") == "SW-CORE-07"
    assert _sanitize_device_id("  AP FL3 012 ") == "AP-FL3-012"
    assert _sanitize_device_id("") == ""


def test_active_probe_confidence_caps_below_one():
    # 0.95 is the explicit cap; inferred must never hit it
    assert edge_confidence(active_probe=True, lift=None, sequence_support=None, source_count=None) == 0.95


def test_inferred_confidence_clamped_to_0_9():
    # A huge lift + heavy support must not exceed 0.9
    s = edge_confidence(active_probe=False, lift=1000, sequence_support=1000, source_count=1000)
    assert s <= 0.9


def test_review_action_thresholds():
    assert review_action(0.95) == "approved"
    assert review_action(0.7) == "flagged"
    assert review_action(0.5) == "pending"
    assert review_action(0.1) == "rejected"


def test_lift_saturates():
    s1 = edge_confidence(active_probe=False, lift=5, sequence_support=None, source_count=None)
    s2 = edge_confidence(active_probe=False, lift=20, sequence_support=None, source_count=None)
    # Past the cap, additional lift should not raise confidence
    assert s2 <= 0.9
    assert s2 >= s1
