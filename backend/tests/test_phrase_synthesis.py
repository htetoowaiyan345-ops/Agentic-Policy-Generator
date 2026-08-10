"""Tests for Phase 2 RowPhraseBuilder behavior.

Verifies that:
  * default templates produce one phrase per cell
  * per-column overrides replace the round-robin default
  * empty cells / headers are skipped (no dangling "the tier is ")
  * round-robin distributes templates across cells in a row
"""
from __future__ import annotations


def test_default_templates_produce_phrases():
    from policy_platform.rag.chunker import RowPhraseBuilder

    builder = RowPhraseBuilder()
    out = builder.build(
        ["Tier", "Amount", "Notes"],
        ["1", "100", "first tier"],
    )
    # 3 cells, 3 phrases — round-robin assigns templates in order.
    assert len(out) == 3
    # First cell uses template[0].
    assert out[0][2] == "Tier: 1"
    # Second cell uses template[1].
    assert out[1][2] == "the Amount is 100"
    # Third cell uses template[2].
    assert out[2][2] == "Notes equals first tier"


def test_column_template_round_robin_overrides():
    from policy_platform.rag.chunker import RowPhraseBuilder

    builder = RowPhraseBuilder(
        templates=["{header}: {value}", "the {header} is {value}"],
        column_templates={"amount": "payout amount is {value}"},
    )
    out = builder.build(
        ["Tier", "Amount", "Notes"],
        ["1", "100", "first tier"],
    )
    # "Amount" has an override -> uses the override.
    assert any(p[2] == "payout amount is 100" for p in out)
    # Others use round-robin default.
    phrases = [p[2] for p in out]
    assert "Tier: 1" in phrases
    assert "the Notes is first tier" in phrases


def test_empty_value_skipped():
    from policy_platform.rag.chunker import RowPhraseBuilder

    builder = RowPhraseBuilder()
    out = builder.build(
        ["Tier", "Amount"],
        ["1", ""],   # empty value -> skip
    )
    # Only the first cell has both header and value.
    assert len(out) == 1
    assert out[0][2] == "Tier: 1"


def test_empty_header_skipped():
    from policy_platform.rag.chunker import RowPhraseBuilder

    builder = RowPhraseBuilder()
    out = builder.build(
        ["Tier", ""],
        ["1", "100"],
    )
    assert len(out) == 1
    assert out[0][2] == "Tier: 1"


def test_whitespace_value_skipped():
    from policy_platform.rag.chunker import RowPhraseBuilder

    builder = RowPhraseBuilder()
    out = builder.build(
        ["Tier", "Amount"],
        ["1", "   "],   # whitespace-only value -> skip
    )
    assert len(out) == 1


def test_phrase_cap_respected_via_chunk():
    """The cap_per_table arg on chunk_tables caps emitted chunks; the
    RowPhraseBuilder itself doesn't cap phrases (caller controls)."""
    from policy_platform.rag.chunker import chunk_tables

    # 30-cell row, 1 data row, no header -> 1 chunk, capped at 1.
    big_header = [f"H{i}" for i in range(30)]
    big_data = [f"V{i}" for i in range(30)]
    chunks = chunk_tables(
        [[big_header, big_data]],
        cap_per_table=20,
    )
    assert len(chunks) == 1
    # All 30 cells -> 30 phrases in that one chunk.
    assert len(chunks[0].phrases) == 30