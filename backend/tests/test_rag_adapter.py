"""Tests for the RAG -> ClassificationResult adapter."""
from __future__ import annotations

from policy_platform.analyzer import ClassificationResult, SectionSlot
from policy_platform.rag import RAGResult
from policy_platform.rag.retrieval_pipeline import SlotAssignment
from policy_platform.rag_adapter import build_classification_from_rag


def _make_rag(slots: dict[int, SlotAssignment], *, timed_out: bool = False) -> RAGResult:
    return RAGResult(slots=slots, timed_out=timed_out)


def test_adapter_populates_all_15_slots():
    rag = _make_rag({})
    out = build_classification_from_rag(rag)
    for sid in range(1, 16):
        assert sid in out.sections
        assert isinstance(out.sections[sid], SectionSlot)


def test_adapter_marks_found_when_chunk_present():
    rag = _make_rag({
        1: SlotAssignment(slot_id=1, chunk_text="Policy Title: Test", source_idx=0),
    })
    out = build_classification_from_rag(rag, source_paragraph_count=5)
    assert out.sections[1].status == "Found"
    assert out.sections[1].content_paragraphs == ["Policy Title: Test."]
    assert out.sections[1].routing_rule == "rag_hybrid"


def test_adapter_marks_skipped_when_no_chunk():
    rag = _make_rag({
        1: SlotAssignment(slot_id=1, chunk_text=None, backend="no_hits"),
    })
    out = build_classification_from_rag(rag)
    assert out.sections[1].status == "Skipped - Section Not Found"


def test_adapter_normalizes_chunk_text():
    rag = _make_rag({
        2: SlotAssignment(slot_id=2, chunk_text="hello world", source_idx=0),
    })
    out = build_classification_from_rag(rag)
    # chunk text gets a trailing period added
    assert out.sections[2].content_paragraphs == ["hello world."]


def test_adapter_does_not_double_period():
    rag = _make_rag({
        3: SlotAssignment(slot_id=3, chunk_text="Already ended.", source_idx=0),
    })
    out = build_classification_from_rag(rag)
    assert out.sections[3].content_paragraphs == ["Already ended."]


def test_adapter_collapses_whitespace():
    rag = _make_rag({
        4: SlotAssignment(slot_id=4, chunk_text="line1\n\nline2\t\tline3", source_idx=0),
    })
    out = build_classification_from_rag(rag)
    assert out.sections[4].content_paragraphs == ["line1 line2 line3."]


def test_adapter_routing_source_indices():
    rag = _make_rag({
        5: SlotAssignment(slot_id=5, chunk_text="intro", source_idx=2),
        6: SlotAssignment(slot_id=6, chunk_text="policy", source_idx=4),
    })
    out = build_classification_from_rag(rag, source_paragraph_count=10)
    assert out.routing_source_indices.get(5) == [2]
    assert out.routing_source_indices.get(6) == [4]


def test_adapter_dropped_indices():
    rag = _make_rag({
        7: SlotAssignment(slot_id=7, chunk_text="purpose", source_idx=3),
    })
    out = build_classification_from_rag(rag, source_paragraph_count=5)
    # Indices 0,1,2,4 were not routed -> dropped.
    assert sorted(out.dropped_paragraph_indices) == [0, 1, 2, 4]


def test_adapter_propagates_timeout_flag():
    rag = _make_rag({}, timed_out=True)
    out = build_classification_from_rag(rag)
    assert out.fallback_used is True


def test_adapter_treats_empty_input_with_no_source_count():
    rag = _make_rag({1: SlotAssignment(slot_id=1, chunk_text="x", source_idx=0)})
    out = build_classification_from_rag(rag)  # default source_paragraph_count=0
    assert out.dropped_paragraph_indices == []


# -- V2: table passthrough + heading-anchor --

def test_adapter_populates_content_tables_when_table_present():
    table = [["Tier", "Payout"], ["1", "100"], ["2", "200"]]
    rag = _make_rag({
        10: SlotAssignment(slot_id=10, chunk_text=None, table=table),
    })
    out = build_classification_from_rag(rag)
    assert out.sections[10].status == "Found"
    assert out.sections[10].content_tables == [table]
    assert out.sections[10].content_paragraphs == []
    assert out.sections[10].routing_rule == "table_passthrough"


def test_adapter_populates_both_prose_and_table():
    table = [["Tier", "Payout"], ["1", "100"]]
    rag = _make_rag({
        10: SlotAssignment(
            slot_id=10,
            chunk_text="Award structure overview",
            table=table,
            source_idx=2,
        ),
    })
    out = build_classification_from_rag(rag)
    assert out.sections[10].status == "Found"
    assert out.sections[10].content_tables == [table]
    assert out.sections[10].content_paragraphs == ["Award structure overview."]
    assert out.sections[10].routing_rule == "heading_anchor"


def test_adapter_marks_heading_anchor_rule():
    rag = _make_rag({
        7: SlotAssignment(slot_id=7, chunk_text="Purpose body", backend="heading_anchor"),
    })
    out = build_classification_from_rag(rag)
    assert out.sections[7].routing_rule == "heading_anchor"


def test_adapter_marks_rag_rule_for_chunk_text():
    rag = _make_rag({
        5: SlotAssignment(slot_id=5, chunk_text="intro paragraph", backend="rag:sentence-transformers+cross-encoder"),
    })
    out = build_classification_from_rag(rag)
    assert out.sections[5].routing_rule == "rag_hybrid"
