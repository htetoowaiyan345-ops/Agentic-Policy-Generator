"""Tests for the sentence-aware chunker."""
from __future__ import annotations

from policy_platform.rag.chunker import chunk_paragraphs, chunk_text


def test_empty_input_returns_no_chunks():
    assert chunk_paragraphs([]) == []


def test_blank_paragraphs_skipped():
    chunks = chunk_paragraphs(["", "   ", "\t", "Hello world."])
    assert len(chunks) == 1
    assert "Hello world" in chunks[0].text


def test_short_paragraph_kept_whole():
    chunks = chunk_paragraphs(["This is a short sentence about workplace safety."])
    assert len(chunks) == 1
    assert chunks[0].text == "This is a short sentence about workplace safety."


def test_chunk_id_stable_and_monotonic():
    chunks = chunk_paragraphs(["one", "two", "three"])
    assert [c.chunk_id for c in chunks] == [0, 1, 2]


def test_source_idx_records_origin():
    chunks = chunk_paragraphs(["first para", "second para", "third para"])
    assert [c.source_idx for c in chunks] == [0, 1, 2]


def test_long_paragraph_is_split_on_sentences():
    long_text = (
        "This is the first sentence about safety. "
        "This is the second sentence about training. "
        "This is the third sentence about compliance. "
        "This is the fourth sentence about reporting. "
        "This is the fifth sentence about escalation."
    )
    chunks = chunk_paragraphs([long_text], target_chunk_size=80, overlap=10)
    assert len(chunks) >= 2
    # The union of all chunk texts should cover the input sentences.
    joined = " ".join(c.text for c in chunks)
    for needle in ("first sentence", "fifth sentence"):
        assert needle in joined


def test_chunk_text_invalidates_whitespace():
    chunks = chunk_paragraphs(["line1\nline2\tline3"])
    assert len(chunks) == 1
    assert "line1 line2 line3" == chunks[0].text


def test_chunk_text_helper():
    text = "Para one.\n\nPara two."
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert chunks[0].text.startswith("Para one")
    assert chunks[1].text.startswith("Para two")


def test_invalid_target_size_raises():
    import pytest
    with pytest.raises(ValueError):
        chunk_paragraphs(["x"], target_chunk_size=0)


def test_invalid_overlap_raises():
    import pytest
    with pytest.raises(ValueError):
        chunk_paragraphs(["x"], target_chunk_size=10, overlap=20)


# -- label row + short title detection --

def test_label_row_paragraph_detected():
    from policy_platform.rag.chunker import is_label_row_paragraph
    assert is_label_row_paragraph("Type: HR Policy")
    assert is_label_row_paragraph("Policy Number: HR-001")
    assert is_label_row_paragraph("Effective Date: 2024-01-15")
    assert is_label_row_paragraph("Approved By: John Smith")
    assert is_label_row_paragraph("Brief Description: A test policy")
    assert is_label_row_paragraph("Applies To: All employees")


def test_non_label_row_paragraph_not_detected():
    from policy_platform.rag.chunker import is_label_row_paragraph
    assert not is_label_row_paragraph("This is a normal paragraph about the policy.")
    assert not is_label_row_paragraph("Introduction: This policy supports employee engagement.")
    assert not is_label_row_paragraph("Random text without a label:")


def test_short_title_detected():
    from policy_platform.rag.chunker import is_short_title
    assert is_short_title("POLICY TEMPLATE - AWARD AND RECOGNITION PROGRAM")
    assert is_short_title("MANAGEMENT POLICY")
    assert is_short_title("Short title")


def test_long_paragraph_not_short_title():
    from policy_platform.rag.chunker import is_short_title
    long_para = "This is a much longer paragraph that has real content and should not be considered a short title. " * 3
    assert not is_short_title(long_para)


def test_footnote_detected():
    from policy_platform.rag.chunker import is_footnote
    assert is_footnote("1 While this policy specifically addresses sexual harassment, harassment because of and discrimination against persons of all protected classes is prohibited.")
    assert is_footnote("2 A non-employee is someone who is (or is employed by) a contractor, subcontractor, vendor, consultant, or anyone providing services in the workplace.")
    assert is_footnote("13 Prioritization of EUA requests from (or supported by) government stakeholders is discussed in the section that follows.")


def test_short_paragraph_not_footnote():
    from policy_platform.rag.chunker import is_footnote
    # Section headings like "1. Purpose" are short and should not be
    # classified as footnotes.
    assert not is_footnote("1. Purpose")
    assert not is_footnote("2. Scope")
    assert not is_footnote("1 ")
    assert not is_footnote("")


def test_long_non_footnote_paragraph():
    from policy_platform.rag.chunker import is_footnote
    # Long paragraphs that don't start with a number + space + text
    # are not footnotes.
    assert not is_footnote("This is a long paragraph that has real content and doesn't start with a number reference.")
    assert not is_footnote("Section A provides general guidelines for compliance with all applicable laws.")


def test_label_row_not_short_title():
    from policy_platform.rag.chunker import is_short_title
    # "Type: HR Policy" has a colon - it's a label row, not a short title.
    assert not is_short_title("Type: HR Policy")


def test_label_row_uses_centralized_synonyms():
    """Label-row detection should pull from brain_fields, not hardcoded list.

    Adding a new synonym to brain_fields.py should automatically
    enable detection of new label-row styles.
    """
    from policy_platform.rag.chunker import is_label_row_paragraph
    # These labels/synonyms come from brain_fields.py BRAIN_LABEL_ROWS
    assert is_label_row_paragraph("Reference Number: REF-001")
    assert is_label_row_paragraph("Doc No: DOC-001")
    assert is_label_row_paragraph("Policy Name: Test")
    # Label rows from slot 3 (Approval & Governance)
    assert is_label_row_paragraph("Effective Date/Period: 01 July 2026")
    assert is_label_row_paragraph("Supersedes: Version 0.9")
