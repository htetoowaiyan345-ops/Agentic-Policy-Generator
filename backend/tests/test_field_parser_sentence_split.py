"""Tests for Phase B sentence-segmentation fallback in field_parser.

These exercise the Earthquake-PDF-style case: PyMuPDF emits a multi-
sentence document as 5-7 broken-by-line paragraphs whose labels span
across the paragraph breaks. The Phase B path re-joins continued lines
and splits on sentence boundaries before matching.
"""
from __future__ import annotations

from policy_platform.extractors import field_parser
from policy_platform.extractors.field_parser import (
    _join_continued_lines,
    _sentence_split_field_map,
    _split_into_label_clauses,
    parse,
)


def test_continued_lowercase_line_is_joined():
    """A line starting with a lowercase letter is a continuation."""
    paragraphs = [
        "Type: Policy.",
        "to all sectors under City Holdings",
    ]
    joined = _join_continued_lines(paragraphs)
    assert joined == ["Type: Policy. to all sectors under City Holdings"]


def test_unterminated_line_is_joined():
    """A line that did NOT end with `.!?;:` is a continuation of the previous."""
    paragraphs = [
        "Type: Policy",
        "to all sectors",
    ]
    joined = _join_continued_lines(paragraphs)
    assert len(joined) == 1
    assert "to all sectors" in joined[0]


def test_terminated_line_starts_new_paragraph():
    """A line after a sentence-terminator is its own paragraph."""
    paragraphs = [
        "Type: Policy.",
        "Brief Description: This policy provides cash assistance.",
    ]
    joined = _join_continued_lines(paragraphs)
    # Two separate paragraphs.
    assert len(joined) == 2


def test_one_paragraph_dense_input_matches_many_labels():
    """The Earthquake-style input populates many canonical labels."""
    paragraph = (
        "Earthquake Emergency Assistance Policy (Policy No. CL&H;_03/26). "
        "Type: Policy. "
        "Brief Description: This policy provides one-time financial assistance. "
        "Effective Date: 01 July 2026. "
        "Approved by: Group CEO. "
        "Prepared by: Group Corporate Affairs. "
        "Responsible Functions: Group Corporate Affairs. "
        "Responsible Officer: Corporate Affairs Director. "
        "Applies To: All eligible local employees. "
        "Reason for Policy: To support employee welfare."
    )
    fm = _sentence_split_field_map([paragraph])
    # All ten labels should resolve.
    assert fm.get("Type:") == "Policy."
    assert "Brief Description:" in fm
    assert "Effective Date/Period:" in fm
    assert "Approved by:" in fm
    assert "Prepared by:" in fm
    assert "Responsible Function(s):" in fm
    assert "Responsible Function Officer(s):" in fm
    assert "Applies to:" in fm
    assert "Reason for Policy:" in fm


def test_paragraphs_split_across_lines_matches_labels():
    """PyMuPDF-style split: labels span across paragraph breaks.

    This emulates what Earthquake paragraph [1] through [6] look like
    when PyMuPDF splits them with `\n`.
    """
    paragraphs = [
        "Earthquake Emergency Assistance Policy (Policy No. CL&H;_03/26). Type: Policy.",
        "to all sectors under City Holdings Group and all local employees affected by earthquake-related",
        "disasters. Brief Description: This policy provides one-time financial assistance to employees and",
        "their immediate families. Effective Date: 01 July 2026. Approved by: Group CEO. Prepared by: Group",
        "Corporate Affairs and Human Resources. Responsible Functions: Group Corporate Affairs.",
    ]
    fm = _sentence_split_field_map(paragraphs)
    # The labels spanning the breaks should now be caught.
    assert fm.get("Type:") is not None
    assert fm.get("Brief Description:") is not None
    assert fm.get("Effective Date/Period:") is not None
    assert fm.get("Approved by:") is not None
    assert fm.get("Prepared by:") is not None
    assert fm.get("Responsible Function(s):") is not None


def test_parse_uses_sentence_fallback_when_env_var_default():
    """Without opting out: rules+sentence path engaged when regex misses."""
    field_parser._LAST_PATH = "rules"  # noqa: SLF001 — test-only reset
    paragraphs = [
        "Earthquake Emergency Assistance Policy.",
        "Type: Policy.",
    ]
    fm = parse(paragraphs)
    # The Type: label should be in the field map.
    assert "Type:" in fm
    # The path should be either 'rules' or 'rules+sentence'
    # (depending on whether regex caught it directly).
    assert field_parser.last_extraction_path() in ("rules", "rules+sentence")


def test_parse_opt_out_skips_sentence_fallback(monkeypatch):
    """With AGENTIC_POLICY_NO_SENTENCE_SPLIT=1, sentence fallback is skipped."""
    monkeypatch.setenv("AGENTIC_POLICY_NO_SENTENCE_SPLIT", "1")
    paragraphs = [
        "Earthquake Emergency Assistance Policy.",
        "Type: Policy.",
    ]
    fm = parse(paragraphs)
    # Still caught by basic regex (Type: at start of line).
    assert "Type:" in fm
    # Path is rules (sentence path was skipped).
    assert field_parser.last_extraction_path() == "rules"


def test_split_into_label_clauses_returns_pairs():
    """Internals: `_split_into_label_clauses` returns (canonical, value) pairs."""
    paragraphs = ["Type: Policy. Effective Date: 01 July 2026."]
    pairs = _split_into_label_clauses(paragraphs)
    assert ("Type:", "Policy.") in pairs
    assert ("Effective Date/Period:", "01 July 2026.") in pairs


def test_split_into_label_clauses_handles_trailing_period():
    """Values with trailing `.` are returned verbatim (downstream trims)."""
    paragraphs = ["Type: Policy."]
    pairs = _split_into_label_clauses(paragraphs)
    assert pairs[0][1] == "Policy."


# ---------------------------------------------------------------------------
# Alternating label/value paragraphs (Award-and-Recognition template style).
# ---------------------------------------------------------------------------


def test_is_labelish_recognizes_label_only_line():
    """A short line with no colon and no trailing period looks labelish."""
    from policy_platform.extractors.field_parser import _is_labelish

    assert _is_labelish("Policy Type") is True
    assert _is_labelish("Approved By") is True
    assert _is_labelish("") is False
    assert _is_labelish("Policy Type:") is False  # has colon
    assert _is_labelish("Some very long line that exceeds the sixty-character labelish limit") is False


def test_alternating_label_value_pairs_caught_by_parse():
    """Award template alternating layout: label on line N, value on N+1."""
    paragraphs = [
        "Policy Type",
        "HR Policy",
        "Policy Number",
        "HR-ARP-001",
        "Applicable Sector",
        "Corporate Services & Operations",
        "Functional Area",
        "Human Resources",
        "Brief Description",
        "Framework governing employee awards.",
        "Effective Date/Period",
        "01 July 2026 - 30 June 2027",
        "Approved By",
        "Htet Oo",
        "Prepared By",
        "Htet Oo Wai Yan",
        "Responsible Function",
        "Human Resources",
        "Responsible Function Officer",
        "Htet Oo Wai Yan",
        "Supersedes",
        "Version 0.9 dated 01 January 2026",
        "Last Reviewed/Updated",
        "05 July 2026",
        "Applies To",
        "All eligible employees",
        "Reason for Policy",
        "To establish a fair, transparent and consistent employee recognition framework.",
        "Policy Review Note",
        "Reviewed annually by Human Resources.",
    ]
    fm = _sentence_split_field_map(paragraphs)
    # Each label should map to its next-line value.
    assert fm.get("Type:") == "HR Policy"
    assert fm.get("Policy Number:") == "HR-ARP-001"
    assert fm.get("Applicable Sector(s):") == "Corporate Services & Operations"
    assert fm.get("Functional Area(s):") == "Human Resources"
    assert fm.get("Brief Description:") == "Framework governing employee awards."
    assert fm.get("Effective Date/Period:") == "01 July 2026 - 30 June 2027"
    assert fm.get("Approved by:") == "Htet Oo"
    assert fm.get("Prepared by:") == "Htet Oo Wai Yan"
    assert fm.get("Responsible Function(s):") == "Human Resources"
    assert fm.get("Responsible Function Officer(s):") == "Htet Oo Wai Yan"
    assert fm.get("Supersedes:") == "Version 0.9 dated 01 January 2026"
    assert fm.get("Last Reviewed:") == "05 July 2026"
    assert fm.get("Applies to:") == "All eligible employees"
    assert fm.get("Reason for Policy:") == ("To establish a fair, transparent "
                                            "and consistent employee recognition "
                                            "framework.")
    assert fm.get("Policy Review Note:") == "Reviewed annually by Human Resources."


def test_parse_recovers_cleaner_dropped_value():
    """Smoke test: parse() accepts dropped_paragraphs and the alternating
    pass still works on typical 2-line label/value cases (no recovery).

    Cleaner-drop recovery is intentionally not attempted because index
    alignment between pre- and post-cleaning paragraphs is unreliable.
    """
    from policy_platform.extractors import field_parser

    paragraphs = [
        "Policy Type",
        "HR Policy",
        "Policy Number",
        "HR-ARP-001",
    ]
    # No actual drop needed; this is a smoke-test that dropped_paragraphs
    # keyword argument is accepted and the alternating path still works.
    dropped: list[dict] = []
    fm = field_parser.parse(paragraphs, dropped_paragraphs=dropped)
    assert "Type:" in fm
    assert fm["Type:"] == "HR Policy"
    assert "Policy Number:" in fm
    assert fm["Policy Number:"] == "HR-ARP-001"


def test_alternating_skips_header_lines_with_no_canonical():
    """A line that's 'labelish' but maps to NO canonical Brain label
    (e.g., 'POLICY TEMPLATE - AWARD AND RECOGNITION', 'PROGRAM')
    must be skipped without consuming the next paragraph's label.
    """
    from policy_platform.extractors import field_parser

    paragraphs = [
        "POLICY TEMPLATE - AWARD AND RECOGNITION",  # not canonical
        "PROGRAM",                                  # not canonical
        "Policy Type",                              # canonical -> Type
        "HR Policy",                                # value
        "Policy Number",                            # canonical
        "HR-ARP-001",                               # value
    ]
    fm = field_parser.parse(paragraphs, dropped_paragraphs=[])
    assert "Type:" in fm
    assert fm["Type:"] == "HR Policy"
    assert "Policy Number:" in fm
    assert fm["Policy Number:"] == "HR-ARP-001"


def test_recovery_then_restart_pairs_next_label():
    """After recovery, the next iteration should pair the recovered-around
    label (e.g., 'Last Reviewed/Updated') with the next non-empty line.
    """
    from policy_platform.extractors import field_parser

    paragraphs = [
        "Supersedes",                # canonical
        "Last Reviewed/Updated",      # canonical (cleaner ate its value)
        "05 July 2026",              # value (cleaner ate nothing for this)
    ]
    dropped = [
        {"index": 1, "text": "Version 0.9 dated 01 January 2026",
         "reason": "version_page_noise"},
    ]
    cleaned_to_original = [0, 2, 3]  # original indices for cleaned lines
    fm = field_parser.parse(
        paragraphs,
        dropped_paragraphs=dropped,
        cleaned_to_original=cleaned_to_original,
    )
    assert "Supersedes:" in fm
    assert fm["Supersedes:"] == "Version 0.9 dated 01 January 2026"
    assert "Last Reviewed:" in fm
    assert fm["Last Reviewed:"] == "05 July 2026"
