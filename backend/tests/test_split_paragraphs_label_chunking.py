"""Tests for the RAG-side label-chunking contract.

These tests do NOT depend on any sample PDF or DOCX fixture.
Inputs are constructed inline so the tests are location-independent
and never hardcode filenames or paths.
"""
from __future__ import annotations

import os

import pytest

from policy_platform.extractors import (
    chunk_paragraphs_by_section_heading,
    split_on_section_heading_labels,
    split_paragraphs,
)


def test_split_on_section_heading_labels_emits_each_label_as_its_own_chunk():
    """The contract: every section-heading label occurrence is its own unit.

    Mirrors the Earthquake-style dense paragraph: one paragraph that
    contains two section-heading labels (Definitions and History)
    must split into two chunks, with the History value contained only
    in the second chunk.
    """
    p = (
        "Definitions: Company means City Holdings Group; Immediate "
        "Family Member means spouse, children, parents. Required "
        "Documents include form, photographs, official reports. "
        "History: Version FY26-27 Initial Draft issued on 03 July 2026."
    )
    chunks = split_on_section_heading_labels(p)
    assert len(chunks) == 2, f"expected 2 chunks, got {len(chunks)}: {chunks!r}"
    assert chunks[0].startswith("Definitions:"), chunks[0]
    assert chunks[1].startswith("History:"), chunks[1]
    # The History chunk must contain the version string verbatim.
    assert "Version FY26-27 Initial Draft" in chunks[1]
    assert "03 July 2026" in chunks[1]
    # And the History text MUST NOT appear in the Definitions chunk.
    assert "Version FY26-27" not in chunks[0]


def test_split_returns_single_chunk_when_no_label_present():
    p = "This is just a regular paragraph with no labels at all."
    chunks = split_on_section_heading_labels(p)
    assert chunks == [p]


def test_split_handles_empty_input():
    assert split_on_section_heading_labels("") == []
    assert split_on_section_heading_labels("   ") == []


def test_split_preserves_preamble_text_before_first_label():
    """A paragraph may have text before the first label; the preamble
    is emitted as its own chunk (when it does not itself trigger a
    label synonym match)."""
    p = (
        "Leading text here. Definitions: Company means the org. "
        "History: Version FY26-27."
    )
    chunks = split_on_section_heading_labels(p)
    assert len(chunks) == 3, f"got {len(chunks)}: {chunks!r}"
    assert chunks[0] == "Leading text here."
    assert chunks[1].startswith("Definitions:")
    assert chunks[2].startswith("History:")


def test_chunk_paragraphs_by_section_heading_disabled_preserves_legacy():
    """When the gate is off, paragraphs pass through unchanged."""
    paras = ["Just a paragraph.", "Another one with no labels."]
    out = chunk_paragraphs_by_section_heading(paras, enabled=False)
    assert out == paras


def test_chunk_paragraphs_by_section_heading_enabled_splits_dense_para():
    """When enabled, dense paragraphs get split, plain ones stay."""
    plain = ["Just a paragraph."]
    dense = (
        "Definitions: Company means City Holdings Group. History: "
        "Version FY26-27 Initial Draft issued on 03 July 2026."
    )
    out = chunk_paragraphs_by_section_heading(
        [plain[0], dense], enabled=True
    )
    # plain paragraph stays as one
    assert out[0] == plain[0]
    # dense paragraph expanded to >= 2 chunks (Definitions + History)
    assert len(out) >= 3  # plain + Definitions + History
    assert any(c.startswith("Definitions:") for c in out)
    assert any(c.startswith("History:") for c in out)
    # The History value MUST NOT be in any Definitions chunk.
    for c in out:
        if c.startswith("Definitions:"):
            assert "Version FY26-27" not in c


def test_chunk_paragraphs_by_section_heading_reads_env(monkeypatch):
    """When the env var is set to "1", the gate opens."""
    monkeypatch.setenv("AGENTIC_POLICY_RAG_LABEL_CHUNKING", "1")
    dense = "Definitions: X. History: Version FY26-27."
    out = chunk_paragraphs_by_section_heading([dense])
    # With gate open, dense is split into at least 2 chunks.
    assert len(out) >= 2


def test_chunk_paragraphs_by_section_heading_handles_empty_list():
    assert chunk_paragraphs_by_section_heading([], enabled=True) == []


# ---------------------------------------------------------------------------
# Unification tests (canonical helper = split_paragraphs).
# These tests pin the contract that the canonical helper is a strict
# superset / drop-in replacement for the historical M1 wrappers, and
# that the RAG-layer default is now ON.
# ---------------------------------------------------------------------------


def test_canonical_split_paragraphs_byte_matches_wrapper_on_dense_para():
    dense = (
        "Definitions: Company means City Holdings Group; Immediate "
        "Family Member means spouse, children, parents. Required "
        "Documents include form, photographs, official reports. "
        "History: Version FY26-27 Initial Draft issued on 03 July 2026."
    )
    canonical = split_paragraphs([dense])
    wrapper = split_on_section_heading_labels(dense)
    assert [c.strip() for c in canonical] == [w.strip() for w in wrapper], (
        f"wrapper/canonical divergence:\n"
        f"  canonical={canonical!r}\n"
        f"  wrapper={wrapper!r}"
    )
    assert any(c.startswith("History:") for c in canonical)


def test_canonical_split_paragraphs_enabled_false_passthrough():
    paras = ["Just text.", "Definitions: A means B. History: V1."]
    out = split_paragraphs(paras, enabled=False)
    assert out == paras


def test_canonical_split_paragraphs_iterates_over_list():
    plain = "Just a paragraph with no labels here."
    dense = (
        "Definitions: Company means Org. History: Version FY26-27."
    )
    out = split_paragraphs([plain, dense])
    assert out[0] == plain
    assert any(c.startswith("Definitions:") for c in out)
    assert any(c.startswith("History:") for c in out)
    for c in out:
        if c.startswith("Definitions:"):
            assert "Version FY26-27" not in c


def test_chunk_wrapper_reads_env_default_on(monkeypatch):
    """After the unification, `chunk_paragraphs_by_section_heading`
    defaults to ON when no explicit `enabled` is given. The env var
    default is "1" (post-unification).
    """
    monkeypatch.delenv("AGENTIC_POLICY_RAG_LABEL_CHUNKING", raising=False)
    dense = (
        "Definitions: Company means Org. History: Version FY26-27."
    )
    out = chunk_paragraphs_by_section_heading([dense])
    assert len(out) >= 2
    assert any(c.startswith("Definitions:") for c in out)
    assert any(c.startswith("History:") for c in out)


def test_chunk_wrapper_opt_out_via_env(monkeypatch):
    """Setting the env var to "0" disables the splitter (opt-out)."""
    monkeypatch.setenv("AGENTIC_POLICY_RAG_LABEL_CHUNKING", "0")
    dense = (
        "Definitions: Company means Org. History: Version FY26-27."
    )
    out = chunk_paragraphs_by_section_heading([dense])
    assert out == [dense]


def test_rag_layer_default_on_splits_dense_paragraphs(monkeypatch):
    """At the RAG layer, default-on splits dense paragraphs at every
    section-heading label. Mirrors what `retrieval_pipeline.run()`
    does at the top of the function: ``if config.RAG_LABEL_CHUNKING:
    paragraphs = split_paragraphs(paragraphs, slots=range(5, 15),
    terminator_aware=False)``.
    """
    monkeypatch.delenv(
        "AGENTIC_POLICY_RAG_LABEL_CHUNKING", raising=False
    )
    dense = (
        "Definitions: Company means Org. "
        "History: Version FY26-27 Initial Draft issued 03 July 2026."
    )
    paras = [dense]
    chunks = split_paragraphs(
        paras, slots=range(5, 15), terminator_aware=False
    )
    assert len(chunks) >= 2
    history_chunks = [c for c in chunks if c.startswith("History:")]
    assert len(history_chunks) == 1
    assert "FY26-27 Initial Draft" in history_chunks[0]
    defs_chunks = [c for c in chunks if c.startswith("Definitions:")]
    for d in defs_chunks:
        assert "Version FY26-27" not in d


# ---------------------------------------------------------------------------
# Narrowed boundary policy tests: only `.` and `\n` count as boundaries.
# `?!;` and comma/colon/paren are NOT boundaries (prevents mid-sentence
# cuts). Sources that join labels with anything other than `. ` fall
# through to Tier 3 RAG fallback.
# ---------------------------------------------------------------------------


def test_split_does_not_cut_after_semicolon():
    """A label preceded by `; ` is NOT a real boundary under the new
    narrowed policy. Sources that join labels with `;` only get
    chunked by Tier 3 RAG (FAISS + cross-encoder), not M1.
    """
    # Two recognisable labels joined by `; ` only — must NOT split.
    p = "Definitions: A means B; History: V1;"
    chunks = split_on_section_heading_labels(p)
    assert chunks == [p], (
        f"`; ` is not a boundary; expected single chunk, got {chunks!r}"
    )


def test_split_does_not_cut_after_question_or_exclamation():
    """`?` and `!` are NOT boundaries. Only `.` and `\\n`."""
    p_q = "Definitions: A means B? History: V1"
    chunks_q = split_on_section_heading_labels(p_q)
    assert chunks_q == [p_q], (
        f"`?` not a boundary; expected single chunk, got {chunks_q!r}"
    )
    p_e = "Definitions: A means B! History: V1"
    chunks_e = split_on_section_heading_labels(p_e)
    assert chunks_e == [p_e], (
        f"`!` not a boundary; expected single chunk, got {chunks_e!r}"
    )


def test_split_still_cuts_after_period():
    """The base contract is preserved: `. ` is still a boundary.
    A dense paragraph with `. ` between labels still splits.
    """
    p = "Definitions: A means B. History: V1."
    chunks = split_on_section_heading_labels(p)
    assert len(chunks) == 2, (
        f"`. ` is a boundary; expected 2 chunks, got {len(chunks)}: {chunks!r}"
    )
    assert chunks[0].startswith("Definitions:")
    assert chunks[1].startswith("History:")


# ---------------------------------------------------------------------------
# Synthetic Flood symptom test.
#
# Mirrors the bleed-over pattern reported in
# `Flood_Emergency_Assistance_Policy.pdf`: slot 10's body chunk
# contains Tier definitions, then `Annual Budget Allocation:`,
# then `Definitions: <clause list>.`, then `Required Documents
# include ...`. The splitter should cut at `. Definitions:` so
# chunk 0 holds the tiers+annual budget, chunk 1 holds the
# definitions, and chunk 2 holds the required documents.
#
# This test builds the input INLINE (no PDF fixture) so it runs
# on every contributor's machine and locates exactly which layer
# broke when the symptom is observed locally.
# ---------------------------------------------------------------------------


def test_flood_symptom_splits_at_definitions():
    """Flood symptom: splitter cuts at `. Definitions:` boundary.

    The chunk is one paragraph mirroring the slot-10 body of the
    Flood PDF. `.` precedes `Definitions:` so the boundary check
    accepts; `Definitions` is a slot-12 synonym so the regex
    matches. Expected: 2 chunks:
      - chunk 0: tiers + Annual Budget block (everything before `.`).
      - chunk 1: starts with `Definitions:`. (The remainder,
        `Required Documents include ...`, is a single trailing
        sentence of chunk 1 because `Required Documents` is not
        a Brain section-heading label and the splitter cuts only
        at `.` — that's the intentional narrowed-policy contract.)
    """
    flood_body_para = (
        "Tier 1 Minor Property Damage – MMK 300,000; Tier 2 Moderate Property Damage MMK 500,000; "
        "Tier 3 Severe Property Damage – MMK 1,000,000; Tier 4 Complete Loss of Home – MMK 2,000,000; "
        "Tier 5 Serious Injury or Hospitalization – MMK 1,500,000; Tier 6 Fatality Support – MMK 3,000,000. "
        "Annual Budget Allocation: Emergency Relief Fund MMK 50,000,000; Housing Recovery Support MMK 30,000,000; "
        "Medical Assistance Support MMK 15,000,000; Bereavement and Family Support MMK 5,000,000; "
        "Total Annual Budget MMK 100,000,000. "
        "Definitions: Company means City Holdings Group and its business units; Immediate Family Member means "
        "spouse, children, parents, or dependents; Flood Event means an officially recognized flood; "
        "Verified Damage means damage supported by acceptable evidence. "
        "Required Documents include application form, photographs, official reports."
    )
    chunks = split_paragraphs([flood_body_para])

    # Expect 2 chunks: tiers+annual-budget | definitions+required-docs
    # (Required Documents is NOT a Brain-section-heading synonym so
    # it lives as a trailing sentence of chunk 1.)
    assert len(chunks) == 2, (
        f"expected 2 chunks (tiers+annual-budget | definitions+required-docs); "
        f"got {len(chunks)}: {chunks!r}"
    )
    # Chunk 0: contains the tiers and the Annual Budget block.
    assert "Tier 1" in chunks[0]
    assert "Annual Budget" in chunks[0]
    # Chunk 0 MUST NOT contain any Definitions content.
    assert "Company means City Holdings Group" not in chunks[0]
    # Chunk 1: starts with `Definitions:`.
    assert chunks[1].startswith("Definitions:"), (
        f"chunk 1 must start with 'Definitions:'; got {chunks[1][:60]!r}"
    )
    # Tier text MUST NOT appear in the Definitions chunk.
    assert "Tier 1" not in chunks[1]
    # Annual Budget text MUST NOT appear in the Definitions chunk.
    assert "Annual Budget Allocation" not in chunks[1]


def test_flood_inline_definitions_resolves_via_split_paragraphs():
    """`split_on_section_heading_labels` (single-paragraph entry point)
    must produce identical chunks to the list-based variant on the
    Flood symptom — wrappers stay byte-compatible.
    """
    flood_body_para = (
        "Tier 1 – MMK 300,000; Tier 2 – MMK 500,000. "
        "Definitions: Company means City Holdings Group; "
        "Required Documents include application form."
    )
    via_wrapper = split_on_section_heading_labels(flood_body_para)
    via_canonical = split_paragraphs([flood_body_para])
    assert [c.strip() for c in via_canonical] == [
        w.strip() for w in via_wrapper
    ], (
        f"wrapper vs canonical divergence:\n"
        f"  canonical={via_canonical!r}\n"
        f"  wrapper={via_wrapper!r}"
    )
