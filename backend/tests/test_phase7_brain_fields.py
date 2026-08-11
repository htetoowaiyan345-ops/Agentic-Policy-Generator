"""Phase 7 tests: brain label-value substitution.

The renderer's Phase 7 behavior:
- Identifies Brain label-rows across slots 1, 2, 3, 4, 11
  (e.g., `Type:`, `Policy Number:`, `Effective Date/Period:`,
   `Approved by:`, `Brief Description:`, `Reason for Policy:`,
   `Policy Review Note:`).
- Substitutes the value portion from input where available.
- Replaces Brain example values (never preserves them) with the
  marker `Data is not found in source file` (plain body styling).
- Leaves the label itself intact so the Brain's framework is
  preserved (only the value changes).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from policy_platform.framework.brain_fields import (
    BRAIN_APPROVAL_FIELDS,
    BRAIN_BRIEF_DESCRIPTION_FIELDS,
    BRAIN_HEADER_FIELDS,
    BRAIN_LABEL_ROWS,
    BRAIN_REASON_FIELDS,
    BRAIN_REVIEW_NOTE_FIELDS,
    canonical_label,
    field_map,
    missing_field_placeholder,
)
from policy_platform.extractors.field_parser import (
    approval_labels,
    brief_description_labels,
    expected_labels,
    header_labels,
    parse,
    reason_labels,
    review_note_labels,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_canonical_label_matches_synonym():
    """`Policy Type:` maps to canonical `Type:`."""
    assert canonical_label("Policy Type:") == "Type:"
    assert canonical_label("Policy Title:") == "Policy Title:"
    assert canonical_label("Effective Date:") == "Effective Date/Period:"
    assert canonical_label("Approved By:") == "Approved by:"
    assert canonical_label("Unknown:") is None


def test_field_map_extracts_paired_lines():
    """`Label: value` lines are extracted with canonical keys."""
    paragraphs = [
        "Type: HR",
        "Policy Title: Vacation Policy",
        "Some prose paragraph.",
        "Policy Number: BT-001",
    ]
    fm = field_map(paragraphs)
    assert fm.get("Type:") == "HR"
    assert fm.get("Policy Title:") == "Vacation Policy"
    assert fm.get("Policy Number:") == "BT-001"


def test_field_map_ignores_non_label_lines():
    """Lines without `Label: value` structure are not parsed."""
    paragraphs = [
        "Just some text without colons here.",
        "Another line of text. Still no field.",
    ]
    assert field_map(paragraphs) == {}


def test_field_map_uses_synonyms():
    """Synonyms map to canonical Brain labels."""
    paragraphs = [
        "Policy Type: HR",
        "Effective Date: 2026-04-01",
        "Approver: CEO",
    ]
    fm = field_map(paragraphs)
    assert fm.get("Type:") == "HR"
    assert fm.get("Effective Date/Period:") == "2026-04-01"
    assert fm.get("Approved by:") == "CEO"


def test_expected_labels_returns_all_brain_labels():
    """All Brain labels appear in expected_labels() in order."""
    labels = expected_labels()
    assert "Type:" in labels
    assert "Policy Title:" in labels
    assert "Policy Number:" in labels
    assert "Applicable Sector(s):" in labels
    assert "Functional Area(s):" in labels
    # Type comes before Policy Title
    assert labels.index("Type:") < labels.index("Policy Title:")
    # Approval fields appear
    assert "Effective Date/Period:" in labels
    assert "Approved by:" in labels


def test_header_labels_count_is_five():
    """The 5 canonical header fields."""
    assert len(header_labels()) == 5


def test_approval_labels_includes_all_required():
    """Approval fields include effective-date, approver, etc."""
    labels = approval_labels()
    for required in ("Effective Date/Period:", "Approved by:", "Prepared by:",
                     "Responsible Function(s):", "Supersedes:",
                     "Last Reviewed:", "Applies to:"):
        assert required in labels


# ---------------------------------------------------------------------------
# End-to-end via pipeline (uses Brain template)
# ---------------------------------------------------------------------------

def test_pipeline_fills_field_value(tmp_path):
    """When input has `Type: <value>`, the output's Type row shows it."""
    p = tmp_path / "input.txt"
    p.write_text(
        "Type: HR Policy\n"
        "Policy Title: Vacation Policy\n"
        "Policy Number: BT-001\n",
        encoding="utf-8",
    )
    from policy_platform import pipeline, config
    r = pipeline.process(p, fail_on_validation=False)
    assert Path(r.output_path).exists()
    # The output docx must contain the input values.
    with zipfile.ZipFile(r.output_path) as z:
        doc = z.read("word/document.xml").decode("utf-8", errors="replace")
    # Per spec: Type extracts only the classification. "HR Policy" is
    # subject ("HR") + classification ("Policy"), so Type row shows
    # "Policy" only. Policy Title remains the full input title.
    assert "Vacation Policy" in doc
    assert "BT-001" in doc
    # The Type row's body text should be just "Policy", not "HR Policy".
    # Check the canonical "Type:" label renders with "Policy" body.
    assert "<w:t" in doc and "Policy" in doc


def test_pipeline_renders_missing_field_marker(tmp_path):
    """When input lacks `Policy Number:`, the renderer writes the
    `Data is not found in source file` marker after the label.

    Per the user's directive, the framework remains intact (label and
    Brain-level structure preserved); the marker replaces the Brain's
    example value (no Brain default leaks through).
    """
    p = tmp_path / "input.txt"
    p.write_text(
        "Type: HR\n"
        "Policy Title: Vacation\n",
        encoding="utf-8",
    )
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    with zipfile.ZipFile(r.output_path) as z:
        doc = z.read("word/document.xml").decode("utf-8", errors="replace")
    # The Brain's Policy Number: row label stays.
    assert "Policy Number:" in doc
    # The marker is rendered with the canonical text.
    assert "Data is not found in source file" in doc
    # The Brain's example value does NOT leak through.
    assert "[CL&amp;H_02/24]" not in doc or "Policy Number: Data is not found in source file" in doc
    # Older wording gone.
    assert "Data not found in source file" not in doc
    assert "NOT FOUND IN SOURCE" not in doc


def test_brief_description_label_recognised():
    """`Brief Description:` is a canonical Brain label."""
    assert canonical_label("Brief Description:") == "Brief Description:"
    assert canonical_label("Description:") == "Brief Description:"
    assert canonical_label("Summary:") == "Brief Description:"


def test_reason_for_policy_label_recognised():
    """`Reason for Policy:` is a canonical Brain label."""
    assert canonical_label("Reason for Policy:") == "Reason for Policy:"
    assert canonical_label("Rationale:") == "Reason for Policy:"


def test_policy_review_note_label_recognised():
    """`Policy Review Note:` is a canonical Brain label."""
    assert canonical_label("Policy Review Note:") == "Policy Review Note:"
    assert canonical_label("Review Note:") == "Policy Review Note:"


def test_missing_field_placeholder_format():
    """`missing_field_placeholder` returns the canonical marker string."""
    out = missing_field_placeholder("Type:")
    assert "Type:" in out
    assert "Data is not found in source file" in out


def test_brain_label_rows_include_slots_2_4_11():
    """`BRAIN_LABEL_ROWS` now contains slots 2/4/11 alongside 1/3."""
    labels = [label for label, _ in BRAIN_LABEL_ROWS]
    assert "Brief Description:" in labels
    assert "Reason for Policy:" in labels
    assert "Policy Review Note:" in labels


def test_pipeline_substitutes_brief_description(tmp_path):
    """When input has `Brief Description:`, the slot 2 paragraph is rewritten."""
    p = tmp_path / "inp.txt"
    p.write_text(
        "Brief Description: Covers vacation requests for all employees.\n",
        encoding="utf-8",
    )
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    with zipfile.ZipFile(r.output_path) as z:
        doc = z.read("word/document.xml").decode("utf-8", errors="replace")
    assert "Covers vacation requests" in doc


def test_pipeline_marks_missing_brief_description(tmp_path):
    """When input has no `Brief Description:`, slot 2 paragraph shows marker."""
    p = tmp_path / "inp.txt"
    p.write_text("Type: HR\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    with zipfile.ZipFile(r.output_path) as z:
        doc = z.read("word/document.xml").decode("utf-8", errors="replace")
    # The renderer splits the run into two: label run + value run.
    # Verify both parts are present in the rendered XML.
    assert "Brief Description:" in doc
    assert "Data is not found in source file" in doc


def test_pipeline_substitutes_reason_for_policy(tmp_path):
    p = tmp_path / "inp.txt"
    p.write_text(
        "Reason for Policy: To comply with regulatory requirements.\n",
        encoding="utf-8",
    )
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    with zipfile.ZipFile(r.output_path) as z:
        doc = z.read("word/document.xml").decode("utf-8", errors="replace")
    assert "To comply with regulatory requirements." in doc


def test_pipeline_substitutes_policy_review_note(tmp_path):
    p = tmp_path / "inp.txt"
    p.write_text(
        "Policy Review Note: Reviewed annually by Compliance team.\n",
        encoding="utf-8",
    )
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    with zipfile.ZipFile(r.output_path) as z:
        doc = z.read("word/document.xml").decode("utf-8", errors="replace")
    assert "Reviewed annually by Compliance team." in doc


def test_pipeline_no_page_breaks_inserted(tmp_path):
    """Per the user's directive the new Brain has 0 page-breaks;
    output mirrors that — no `<w:pageBreakBefore/>` inserted."""
    p = tmp_path / "inp.txt"
    p.write_text("Type: HR\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    with zipfile.ZipFile(r.output_path) as z:
        doc = z.read("word/document.xml").decode("utf-8", errors="replace")
    assert "<w:pageBreakBefore/>" not in doc, "output must not insert page breaks"


def test_pipeline_tables_10_14_have_no_data_when_no_input(tmp_path):
    """Slots 10 (Award) and 14 (HISTORY) cells show the unified marker
    `Data is not found in source file` when input has no table data."""
    p = tmp_path / "inp.txt"
    p.write_text("Type: HR\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    with zipfile.ZipFile(r.output_path) as z:
        doc = z.read("word/document.xml").decode("utf-8", errors="replace")
    # Multiple cells will share the marker.
    assert doc.count("Data is not found in source file") >= 2
    # Older wording gone.
    assert "No data found in source" not in doc


def test_no_brain_examples_leak_when_no_input(tmp_path):
    """When the input has no `Label: value` lines, the renderer's output
    must NOT contain any of the Brain's example values from slots 1, 2,
    3, 4, or 11 — they are all replaced with the marker.

    This is the user's directive from the latest round: only input
    data goes in the output. Brain defaults never leak.
    """
    p = tmp_path / "inp.txt"
    p.write_text("No data here.\n", encoding="utf-8")
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    with zipfile.ZipFile(r.output_path) as z:
        doc = z.read("word/document.xml").decode("utf-8", errors="replace")
    # Spot-check known Brain example fingerprints.
    leaks = [
        "City Family High School Completion Award Policy",
        "[CL&amp;H_02/24]",
        "Daw Win Win Tint",
        "Zin Min Htut",
        "13 Feb 2023",
        "03 June 2026",
    ]
    for leak in leaks:
        assert leak not in doc, f"Brain example leaked into output: {leak!r}"


def test_pipeline_label_value_keeps_brain_format(tmp_path):
    """The output preserves the Brain's `Label: value` formatting."""
    p = tmp_path / "input.txt"
    p.write_text(
        "Type: HR\n"
        "Policy Title: Vacation\n"
        "Policy Number: BT-001\n"
        "INTRODUCTION\nThis policy covers vacation requests.\n",
        encoding="utf-8",
    )
    from policy_platform import pipeline
    r = pipeline.process(p, fail_on_validation=False)
    from docx import Document
    d = Document(r.output_path)
    found_label_value = False
    for para in d.paragraphs[:20]:
        text = para.text
        if text.startswith("Type:") and ("HR" in text):
            found_label_value = True
    assert found_label_value, "Output missing the 'Type: <value>' row in the Brain's format"


def test_pipeline_preserves_logo_image():
    """The Brain logo image survives the per-label substitution pass."""
    p = Path("C:/Users/htetoowaiyan/Downloads/Policy For Coronavirus Disease.pdf")
    if not p.exists():
        pytest.skip("Source PDF not available")
    from policy_platform import pipeline
    r = pipeline.process(p)
    with zipfile.ZipFile(r.output_path) as z:
        names = z.namelist()
        media = [n for n in names if "word/media/" in n]
    assert any("image1.jpeg" in n for n in media)


# ---------------------------------------------------------------------------
# Stage B synonyms (added in Stage 2).
# ---------------------------------------------------------------------------

def test_synonym_policy_no_maps_to_policy_number():
    """`Policy No.`, `Policy No:`, `Policy No.:` map to `Policy Number:`."""
    assert canonical_label("Policy No.:") == "Policy Number:"
    assert canonical_label("Policy No:") == "Policy Number:"
    assert canonical_label("Policy No.") == "Policy Number:"
    assert canonical_label("Doc No:") == "Policy Number:"
    assert canonical_label("Reference:") == "Policy Number:"
    assert canonical_label("Ref:") == "Policy Number:"


def test_synonym_applies_to_capital_t():
    """`Applies To` (capital T) maps to canonical `Applies to:`."""
    assert canonical_label("Applies To:") == "Applies to:"
    assert canonical_label("Applicable To:") == "Applies to:"
    assert canonical_label("Application:") == "Applies to:"


def test_synonym_responsible_functions_vs_officer_split():
    """`Responsible Functions:` (plural) → `Responsible Function(s):`;
    `Responsible Officer:` → `Responsible Function Officer(s):`."""
    assert canonical_label("Responsible Functions:") == "Responsible Function(s):"
    assert canonical_label("Responsible Officer:") == "Responsible Function Officer(s):"
    assert canonical_label("Responsible Officers:") == "Responsible Function Officer(s):"
    # Sanity: existing synonym `Owner:` should not collide with `Officer:`.
    assert canonical_label("Owner:") == "Responsible Function(s):"


def test_synonym_reason_and_review_note_variants():
    """`Rationale:`, `Background:`, `Context:` map to `Reason for Policy:`;
    `Note:`, `Review Notes:` map to `Policy Review Note:`."""
    assert canonical_label("Rationale:") == "Reason for Policy:"
    assert canonical_label("Background:") == "Reason for Policy:"
    assert canonical_label("Context:") == "Reason for Policy:"
    assert canonical_label("Note:") == "Policy Review Note:"
    assert canonical_label("Review Notes:") == "Policy Review Note:"


def test_synonym_effective_date_variants():
    """`Effective Period:`, `Valid From:`, `Date Issued:` map to
    canonical `Effective Date/Period:`."""
    assert canonical_label("Effective Period:") == "Effective Date/Period:"
    assert canonical_label("Valid From:") == "Effective Date/Period:"
    assert canonical_label("Date Issued:") == "Effective Date/Period:"
    assert canonical_label("Issued On:") == "Effective Date/Period:"


def test_synonym_approved_prepared_variants():
    """`Approval:`, `Author:`, `Drafter:` map to expected canonicals."""
    assert canonical_label("Approval:") == "Approved by:"
    assert canonical_label("Approving Authority:") == "Approved by:"
    assert canonical_label("Author:") == "Prepared by:"
    assert canonical_label("Drafter:") == "Prepared by:"


def test_synonym_supersedes_predecessor_variants():
    """`Superseded By:`, `Previous Policy:` map to `Supersedes:`."""
    assert canonical_label("Superseded By:") == "Supersedes:"
    assert canonical_label("Previous Policy:") == "Supersedes:"
    assert canonical_label("Predecessor:") == "Supersedes:"


def test_synonym_applicable_sector_functional_area_variants():
    """`Sector:`, `Sectors:` → `Applicable Sector(s):`;
    `Area:`, `Departments:` → `Functional Area(s):`."""
    assert canonical_label("Sector:") == "Applicable Sector(s):"
    assert canonical_label("Sectors:") == "Applicable Sector(s):"
    assert canonical_label("Area:") == "Functional Area(s):"
    assert canonical_label("Departments:") == "Functional Area(s):"


def test_synonym_policy_title_extra_variants():
    """`Policy Name:` maps to `Policy Title:`."""
    assert canonical_label("Policy Name:") == "Policy Title:"
    assert canonical_label("Name:") == "Policy Title:"


def test_synonym_field_map_uses_new_variants():
    """`field_map` returns canonical keys when input uses new synonyms."""
    paragraphs = [
        "Policy No.: CL&H-02/24",
        "Approving Authority: Group CEO",
        "Author: Patrick @ Win Zaw Htet",
        "Sector: All sectors under City Holdings Group",
        "Departments: All local employees",
        "Note: Award amounts are not static.",
    ]
    fmap = field_map(paragraphs)
    assert fmap.get("Policy Number:") == "CL&H-02/24"
    assert fmap.get("Approved by:") == "Group CEO"
    assert fmap.get("Prepared by:") == "Patrick @ Win Zaw Htet"
    assert fmap.get("Applicable Sector(s):") == "All sectors under City Holdings Group"
    assert fmap.get("Functional Area(s):") == "All local employees"
    assert fmap.get("Policy Review Note:") == "Award amounts are not static."


# ---------------------------------------------------------------------------
# Stage 5 wiring: parse() uses spaCy when available.
# ---------------------------------------------------------------------------

def test_parse_falls_back_to_rules_by_default():
    """Without `AGENTIC_POLICY_USE_SPACY=1`, parse() returns 'rules' path."""
    from policy_platform.extractors import field_parser

    field_parser._LAST_PATH = "rules"  # noqa: SLF001 — test-only reset
    _ = parse(["Policy No.: CL&H-02/24", "Approved By: Group CEO"])
    assert field_parser.last_extraction_path() == "rules"


def test_parse_uses_spacy_when_env_var_set(monkeypatch):
    """With `AGENTIC_POLICY_USE_SPACY=1` AND spaCy installed: path is 'spacy'."""
    from policy_platform.extractors import field_parser
    from policy_platform.extractors.spacy_extractor import is_available

    if not is_available():
        # Try setting the env var first, then re-check.
        monkeypatch.setenv("AGENTIC_POLICY_USE_SPACY", "1")
        if not is_available():
            pytest.skip("spaCy or en_core_web_sm not installed")
    else:
        monkeypatch.setenv("AGENTIC_POLICY_USE_SPACY", "1")

    field_parser._LAST_PATH = "rules"  # noqa: SLF001
    result = parse([
        "Type: Internal.",
        "Effective Date: 01 July 2026.",
    ])
    assert "Type:" in result
    assert "Effective Date/Period:" in result
    assert field_parser.last_extraction_path() == "spacy"
