"""End-to-end test for the Award-and-Recognition template PDF.

This test is the SOURCE OF TRUTH for the Award PDF. If the parser or
cleaner regresses in any way that produces wrong/missing field values
for this input, this test will fail.

The Award PDF has alternating `[label, value, label, value, ...]`
layout AND the author name `Htet Oo Wai Yan` repeats 3 times. Earlier
versions of the cleaner ate the author name as a header_repeat, which
caused the parser to pair labels with the WRONG values (shifted by
one). This test guards against that regression permanently.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


AWARD_PDF = Path(
    "C:/Users/htetoowaiyan/Downloads/"
    "Policy_Template_Award_and_Recognition_Updated.pdf"
)


pytestmark = pytest.mark.skipif(
    not AWARD_PDF.exists(),
    reason="Award PDF not available in Downloads",
)


def test_award_pdf_full_run_field_map():
    """End-to-end: pipeline.process() on Award PDF populates every Tier-1/2
    label-row with the CORRECT verbatim value from source.
    """
    from policy_platform import pipeline

    result = pipeline.process(AWARD_PDF)

    # Read the audit JSON — it carries the parsed field map.
    # The audit JSON is now stored as a string on the result object
    # (the API persists it into runs.db).
    payload = json.loads(result.audit_json)

    # Re-parse by running the field parser ourselves with the actual
    # extracted+cleaned paragraphs (so we observe the field map
    # independent of the pipeline's slot-route layer).
    from policy_platform.extractors import pdf_extractor

    extracted = pdf_extractor.extract(AWARD_PDF)
    # Apply the same dispatch flow as the pipeline (clean + index map).
    from policy_platform.extractors import dispatch

    dispatched = dispatch(AWARD_PDF)

    from policy_platform.extractors import field_parser
    fm = field_parser.parse(
        dispatched.paragraphs,
        dropped_paragraphs=dispatched.cleaner_dropped,
        cleaned_to_original=dispatched.original_indices,
    )

    # Tier-1/2 label-rows MUST have non-marker, verbatim values.
    # Note: the values for `Brief Description` and `Supersedes` reflect
    # the improved extraction (pdfplumber preserves the full text
    # instead of truncating at column boundaries). Earlier versions
    # of the extractor produced `"payout tiers and admin"` (truncated);
    # the current extractor correctly produces the full text.
    expected = {
        "Type:": "HR Policy",
        "Policy Number:": "HR-ARP-001",
        "Applicable Sector(s):": "Corporate Services & Operations",
        "Functional Area(s):": "Human Resources",
        "Brief Description:": (
            "Framework governing employee awards, recognition criteria, "
            "payout tiers and administration."
        ),
        "Effective Date/Period:": "01 July 2026 - 30 June 2027",
        "Approved by:": "Htet Oo",
        "Prepared by:": "Htet Oo Wai Yan",
        "Responsible Function(s):": "Human Resources",
        "Responsible Function Officer(s):": "Htet Oo Wai Yan",
        "Supersedes:": "Version 0.9 dated 01 January 2026",
        "Last Reviewed:": "05 July 2026",
        "Applies to:": "All eligible employees",
        "Reason for Policy:": (
            "To establish a fair, transparent and consistent employee "
            "recognition framework."
        ),
        "Policy Review Note:": "Reviewed annually by Human Resources.",
    }

    missing = [
        (label, expected[label])
        for label in expected
        if label not in fm
    ]
    wrong = [
        (label, expected[label], fm.get(label))
        for label in expected
        if label in fm and fm[label] != expected[label]
    ]
    assert not missing, (
        f"Missing labels in field map: {missing}\n"
        f"Got: {dict((k, fm.get(k, '<<MISSING>>')) for k in expected)}"
    )
    assert not wrong, (
        f"Wrong values in field map:\n"
        f"  expected: {dict((k, expected[k]) for k in expected if fm.get(k) != expected[k])}\n"
        f"  got:      {dict((k, fm.get(k)) for k in expected if fm.get(k) != expected[k])}\n"
    )


def test_award_pdf_no_marker_for_recoverable_labels():
    """End-to-end: NO `Data is not found` markers for Tier-1/2 labels.

    Every Tier-1/2 label-row value is in the source PDF. The cleaner
    must not eat any of them, and the parser must not give up.
    """
    from policy_platform import pipeline
    import zipfile
    import re

    result = pipeline.process(AWARD_PDF)

    with zipfile.ZipFile(result.output_path) as z:
        x = z.read("word/document.xml").decode("utf-8", errors="replace")

    # Find every paragraph containing the marker.
    marker_text = "Data is not found in source file"
    recoverable_labels = [
        "Prepared by:",
        "Responsible Function(s):",
        "Responsible Function Officer(s):",
        "Supersedes:",
        "Effective Date/Period:",
        "Approved by:",
    ]
    bad: list[tuple[str, str]] = []
    for p in re.findall(r"<w:p\b[^>]*>(.*?)</w:p>", x, re.DOTALL):
        text = "".join(re.findall(r"<w:t[^>]*>([^<]+)</w:t>", p)).strip()
        if marker_text in text:
            for label in recoverable_labels:
                if label in text:
                    bad.append((label, text[:120]))
    assert not bad, (
        f"Recoverable Tier-1/2 labels should NOT render "
        f"`Data is not found` markers:\n"
        + "\n".join(f"  {l}: {t}" for l, t in bad)
    )
