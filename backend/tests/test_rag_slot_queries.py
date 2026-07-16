"""Tests for the 15-slot query dictionary."""
from __future__ import annotations

from policy_platform.rag.slot_queries import (
    SLOT_QUERIES,
    all_slots,
    get_queries_for_slot,
)


def test_all_15_slots_present():
    assert sorted(SLOT_QUERIES.keys()) == list(range(1, 16))


def test_every_slot_has_queries_except_logo():
    for sid in range(1, 15):
        queries = get_queries_for_slot(sid)
        assert queries, f"slot {sid} has no queries"
        assert all(isinstance(q, str) and q.strip() for q in queries)


def test_logo_slot_has_no_queries():
    assert get_queries_for_slot(15) == []


def test_all_slots_yields_1_to_15():
    assert list(all_slots()) == list(range(1, 16))


def test_queries_are_unique_per_slot():
    for sid, qs in SLOT_QUERIES.items():
        assert len(qs) == len(set(qs)), f"slot {sid} has duplicate queries"
