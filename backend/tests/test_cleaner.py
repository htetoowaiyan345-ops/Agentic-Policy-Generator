"""Tests for the extraction cleaner.

The cleaner is responsible for stripping:
- pure page-number lines (`"1"`, `"Page 4"`, etc.)
- repeating short strings (headers / footers)
- lines with >30% control / replacement characters (garbled text)

These tests target each category independently and confirm the cleaner
is wired into the dispatcher (so PDF/DOCX/TXT/RTF outputs are cleaned
transparently).
"""
from __future__ import annotations

from policy_platform.extractors.base import ExtractedDocument
from policy_platform.extractors.cleaner import (
    GARBLED_RATIO,
    REPEAT_THRESHOLD,
    clean_paragraphs,
)
from policy_platform.extractors import dispatch


def _into_cleaner(text: str) -> list[str]:
    """Helper: feed text into the cleaner the same way the dispatcher does."""
    paragraphs = text.split("\n") if text else []
    cleaned, dropped, _ = clean_paragraphs(paragraphs)
    assert isinstance(cleaned, list)
    assert isinstance(dropped, list)
    return [d["text"] for d in dropped]


def test_cleaner_drops_pure_numeric_page_numbers():
    """Pure-digit lines like '1', '2', '3' must be flagged as page numbers."""
    paragraphs = ["Body text A", "1", "Body text B", "2", "Body text C"]
    cleaned, dropped, _ = clean_paragraphs(paragraphs)
    kept_texts = [p.strip() for p in cleaned if p.strip()]
    assert "Body text A" in kept_texts
    assert "Body text B" in kept_texts
    assert "Body text C" in kept_texts
    drop_reasons = {d["reason"] for d in dropped}
    assert "page_number" in drop_reasons


def test_cleaner_drops_page_n_words():
    """`Page 1`, `Page 12 of 30`, `1 of 4`, `IV` all count as page-number."""
    paragraphs = [
        "Body", "Page 1", "Continued body", "1 of 4", "More", "IV", "End"
    ]
    cleaned, dropped, _ = clean_paragraphs(paragraphs)
    kept = [p.strip() for p in cleaned if p.strip()]
    assert "Body" in kept
    assert "Continued body" in kept
    assert "More" in kept
    assert "End" in kept
    drop_reasons = {d["reason"] for d in dropped}
    assert "page_number" in drop_reasons


def test_cleaner_drops_repeating_short_header():
    """A short line repeated >= REPEAT_THRESHOLD times is a header/footer."""
    header = "Confidential — Internal"
    # 5 body lines and 4 copies of the header (>= 3 threshold)
    paragraphs = (
        ["First real paragraph of meaningful prose for testing.", "Second body."] * 3
        + [header] * 4
    )
    cleaned, dropped, _ = clean_paragraphs(paragraphs)
    # All copies of the repeating header must be dropped
    kept_stripped = [p.strip() for p in cleaned]
    assert header not in kept_stripped
    drop_reasons = {d["reason"] for d in dropped}
    assert "header_repeat" in drop_reasons


def test_cleaner_drops_garbled_lines():
    """A line dominated by replacement/control chars is noise."""
    # '\ufffd' is the unicode replacement character (CP1252 -> UTF-8 misread)
    paragraphs = [
        "Some real text introducing a paragraph.",
        "\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd",
        "More real text after garbage noise characters here.",
    ]
    cleaned, dropped, _ = clean_paragraphs(paragraphs)
    kept = [p.strip() for p in cleaned]
    assert "Some real text introducing a paragraph." in kept
    assert "More real text after garbage noise characters here." in kept
    drop_reasons = {d["reason"] for d in dropped}
    assert "garbled" in drop_reasons


def test_cleaner_preserves_real_short_body():
    """A 1-sentence paragraph with prose is preserved (not flagged as page #)."""
    paragraphs = [
        "1",
        "Purpose",
        "This policy ensures appropriate handling.",
        "2",
        "Scope",
        "All employees of City Holdings are subject to this policy framework.",
    ]
    cleaned, dropped, _ = clean_paragraphs(paragraphs)
    kept_stripped = [p.strip() for p in cleaned]
    assert "Purpose" in kept_stripped
    assert "This policy ensures appropriate handling." in kept_stripped
    assert "Scope" in kept_stripped
    assert "All employees of City Holdings are subject to this policy framework." in kept_stripped


def test_cleaner_preserves_blank_lines():
    """Blank-line paragraphs must pass through (they're paragraph break signals)."""
    paragraphs = ["Header line", "", "Body paragraph one.", "", "Body paragraph two."]
    cleaned, dropped, _ = clean_paragraphs(paragraphs)
    # Two empty strings should still be in the output (one between each body)
    empty_kept = sum(1 for p in cleaned if not p.strip())
    assert empty_kept == 2


def test_cleaner_default_thresholds_match_docstring():
    """Sanity check that the public threshold values are stable defaults."""
    assert REPEAT_THRESHOLD >= 2
    assert 0.1 <= GARBLED_RATIO <= 0.6


def test_cleaner_records_index_and_reason():
    """Each dropped line must carry its original index and reason for audit."""
    paragraphs = ["Real text here.", "1", "More real text."]
    cleaned, dropped, _ = clean_paragraphs(paragraphs)
    assert len(dropped) == 1
    rec = dropped[0]
    assert rec["index"] == 1
    assert rec["text"] == "1"
    assert rec["reason"] == "page_number"


def test_cleaner_respects_threshold_for_repeats():
    """A line repeated only twice must NOT be flagged as a header."""
    paragraphs = ["Footer text here maybe", "Body A.", "Body B.", "Footer text here maybe"]
    cleaned, dropped, _ = clean_paragraphs(paragraphs)
    kept_stripped = [p.strip() for p in cleaned]
    # Default threshold is 3 — two copies stays
    if REPEAT_THRESHOLD > 2:
        assert "Footer text here maybe" in kept_stripped


def test_dispatcher_correctly_handles_award_template_proper_names():
    """Proper names (e.g., 'Htet Oo Wai Yan') are NEVER header_repeats.

    The Award template repeats the author name 3 times legitimately:
    once for Prepared By, once for Responsible Function Officer, once in
    HISTORY. The cleaner MUST keep all three to preserve the Brain
    label-rows.
    """
    paragraphs = [
        "Prepared By",
        "Htet Oo Wai Yan",
        "Responsible Function Officer",
        "Htet Oo Wai Yan",
        "DATE",
        "VERSION",
        "AUTHOR / REVIEWER",
        "05 July 2026",
        "1.0",
        "Initial Release",
        "Htet Oo Wai Yan",
    ]
    cleaned, dropped, _ = clean_paragraphs(paragraphs)
    # All 3 occurrences of 'Htet Oo Wai Yan' should be RETAINED in cleaned.
    kept_howy = [c for c in cleaned if "Htet Oo Wai Yan" in c]
    assert len(kept_howy) == 3, (
        f"Expected 3 occurrences retained, got {len(kept_howy)}: {kept_howy!r}"
    )
    # None should have reason 'header_repeat'.
    howy_drops = [
        d for d in dropped
        if "Htet Oo Wai Yan" in d.get("text", "")
        and d.get("reason") == "header_repeat"
    ]
    assert howy_drops == [], (
        f"Author name should not be header-repeat; got {howy_drops!r}"
    )


def test_cleaner_returns_original_indices_map():
    """`clean_paragraphs` returns a parallel `original_indices` list.

    For each cleaned line at position N, `original_indices[N]` is the
    index in the original (un-cleaned) input list. Lines are only kept
    in `cleaned` and added to `original_indices` together.
    """
    paragraphs = [
        "Header",  # 0
        "Body A",  # 1
        "1",       # 2 - dropped (page_number)
        "Body B",  # 3
        "",        # 4 - blank kept
        "Body C",  # 5
    ]
    cleaned, dropped, original_indices = clean_paragraphs(paragraphs)
    assert len(cleaned) == len(original_indices)
    # cleaned[0] = 'Header' was originally index 0.
    assert cleaned[0].strip() == "Header"
    assert original_indices[0] == 0
    # cleaned[1] = 'Body A' was index 1.
    assert cleaned[1].strip() == "Body A"
    assert original_indices[1] == 1
    # cleaned[2] = 'Body B' was at original index 3 (index 2 '1' was dropped).
    assert cleaned[2].strip() == "Body B"
    assert original_indices[2] == 3
    # cleaned[3] is the blank '' at original_index 4.
    assert cleaned[3].strip() == ""
    assert original_indices[3] == 4
    # cleaned[4] = 'Body C' at original_index 5.
    assert cleaned[4].strip() == "Body C"
    assert original_indices[4] == 5
    # Dropped line '1' had original index 2.
    assert dropped[0]["index"] == 2


def test_dispatch_strips_headers_via_cleaner():
    """The dispatch() function must apply the cleaner transparently.

    We can't easily mock a PDF here, but TXT is a transparent carrier.
    """
    import tempfile
    body = (
        "Contains Nonbinding Recommendations\n"
        "1\n"
        "This is a real policy paragraph about safety requirements.\n"
        "2\n"
        "This is another real policy paragraph about employee welfare.\n"
        "Contains Nonbinding Recommendations\n"
        "3\n"
        "Final paragraph for the test of cleaner integration.\n"
        "Contains Nonbinding Recommendations\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(body)
        path = f.name
    try:
        from pathlib import Path
        extracted = dispatch(Path(path))
        kept = [p.strip() for p in extracted.paragraphs]
        # The repeating header (>= 3 times) was dropped
        assert "Contains Nonbinding Recommendations" not in kept
        # Page numbers were dropped
        assert "1" not in kept
        assert "2" not in kept
        # Real body text survived
        assert any("real policy paragraph about safety" in k for k in kept)
    finally:
        from pathlib import Path
        Path(path).unlink(missing_ok=True)
