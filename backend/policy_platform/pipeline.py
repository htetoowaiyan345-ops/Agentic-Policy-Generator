"""Pipeline orchestrator: implements Steps 1-7 of the platform spec.

RAG-Hybrid edition: Step 3 ("Analyze") now runs the RAG retrieval
pipeline from `policy_platform.rag` rather than the historical
rule-based analyzer. The renderer and downstream code are unchanged.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Iterable, Optional

from . import config
from .audit import new_run_id, now_iso, write_audit
from .extractors import dispatch as extractor_dispatch
from .extractors import header_extractor
from .framework import brain as brain_loader
from .framework.section_map import FROZEN_SECTIONS
from .pipeline_types import AgentStep, AuditResult
from .rag import RetrievalPipeline
from .rag_adapter import build_classification_from_rag
from .renderer import render
from .validator import ValidationFailed, validate


class PipelineError(RuntimeError):
    """A pipeline step failed; carries the failing step number and name."""


def _step(no: int, name: str, ok: bool, detail: str = "") -> AgentStep:
    return AgentStep(no=no, name=name, ok=ok, detail=detail)


def _maybe_inject_title_from_top_paragraph(
    field_map: dict, paragraphs: list
) -> dict:
    """If the Policy Title slot is empty after label-row synthesis, use the
    first short heading-like paragraph near the top as the title.

    Structural heuristic — no hardcoded labels, no defaults. Only fires when
    the field_map has no Policy Title AND a top paragraph looks title-like.

    Phase 6 — strict selection rules to extract only the main title:
      - Look at first 5 paragraphs after dispatch() normalization.
      - Reject any candidate that:
        * contains `:` (it's a Label:Value line)
        * contains `;` (multi-clause prose)
        * ends with `.` followed by lowercase (sentence, not title)
        * starts with a digit and contains `|` or `Page` (page footer)
        * is shorter than 8 chars (too short to be a real title)
        * contains `.` mid-string before the end (multi-sentence prose)
      - Among the remaining candidates, pick the FIRST one that survives.
      - Multi-line: if the candidate contains `\n`, take only first segment.
      - Title prefix stripping: strip leading "Document Type – Title"
        or "Document Type: Title" prefix to extract just the title.
      - Phase 6+: validate the candidate via `parse_field_value` which
        also rejects section-marker words (Purpose, Scope, etc.).

    Lets documents that put the title as a standalone heading (e.g.
    "POLICY TEMPLATE - AWARD AND RECOGNITION PROGRAM") still fill the
    Policy Title slot. This works for any file — no per-file hardcoding.
    """
    if field_map.get("Policy Title:"):
        return field_map
    if not paragraphs:
        return field_map
    for p in paragraphs[:5]:
        if not p:
            continue
        clean = str(p).strip()
        if not clean:
            continue
        # Rejection rules (Phase 6 — general heuristics).
        if ":" in clean:
            continue
        if ";" in clean:
            continue
        if len(clean) < 8 or len(clean) > 200:
            continue
        # Page footer: starts with digit + contains `|` or "Page".
        if (
            clean[:1].isdigit()
            and ("|" in clean or "Page" in clean or "page" in clean)
        ):
            continue
        # Sentence ending: `.` followed by lowercase (sentence, not title).
        if clean.endswith(".") and len(clean) > 1 and clean[-2].islower():
            continue
        # Has a period before the end (multi-sentence prose).
        if clean[:-1].count(".") > 0:
            continue
        # Multi-line: if there's a newline, take only first segment.
        first_segment = clean.split("\n", 1)[0].strip()
        if not first_segment or len(first_segment) < 8:
            continue
        # Phase 6 — strip document-type prefix. Many real-world PDFs
        # render titles as "Group Policy – Employee Health Benefit Policy"
        # where the first part is a document classification. General
        # pattern: strip leading capitalized-word(s) followed by a
        # separator (`–`, `—`, `-`, or `:`).
        stripped = _strip_title_prefix(first_segment)
        # Final validation via parse_field_value (rejects section-marker
        # words like "Purpose", "Scope", etc., and applies length cap).
        from .framework.brain_fields import parse_field_value
        validated = parse_field_value("Policy Title:", stripped)
        if validated is None:
            continue
        # Layer F: Myanmar-aware preference. If the validated title is
        # purely Myanmar-script AND an English-script title appears in
        # the first 10 paragraphs (e.g., "Group Policy – …"), prefer
        # the English title. General heuristic — works for any
        # Myanmar PDF where the source has both languages on the cover.
        try:
            from policy_platform.i18n.burmese_strings import has_burmese
            is_pure_mm = has_burmese(validated) and not any(
                c.isascii() and c.isalpha() for c in validated
            )
        except Exception:
            is_pure_mm = False
        if is_pure_mm:
            for q in paragraphs[:10]:
                if not q:
                    continue
                q_clean = str(q).strip()
                if not q_clean:
                    continue
                # Quick check: has English letters AND not Myanmar.
                try:
                    from policy_platform.i18n.burmese_strings import has_burmese
                    if has_burmese(q_clean):
                        continue
                except Exception:
                    continue
                has_en = any(c.isascii() and c.isalpha() for c in q_clean)
                if not has_en:
                    continue
                # Reject obvious non-title lines.
                if ":" in q_clean or ";" in q_clean:
                    continue
                if len(q_clean) < 8 or len(q_clean) > 200:
                    continue
                if q_clean.endswith(".") and len(q_clean) > 1 and q_clean[-2].islower():
                    continue
                # Apply same prefix stripper.
                q_stripped = _strip_title_prefix(
                    q_clean.split("\n", 1)[0].strip()
                )
                q_validated = parse_field_value("Policy Title:", q_stripped)
                if q_validated is not None:
                    validated = q_validated
                    break
        return {**field_map, "Policy Title:": validated}
    return field_map


def _normalize_value_for_compare(value: str) -> str:
    """Normalize a value for cross-field comparison.

    Lowercases, collapses whitespace, strips punctuation. Used to
    detect whether two values refer to the same thing (e.g., "Human
    Resources" appearing in both Functional Area and Applies to).
    General helper — no per-file hardcoding.
    """
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _refine_field_map_cross_field(field_map: dict, paragraphs: list | None = None) -> dict:
    """Apply cross-field refinement rules to the populated field_map.

    General heuristics — no per-file hardcoding. Rules:

      1. Applies to: drop values that match (case-insensitive,
         whitespace-normalized) any value already in Functional Area(s)
         or Responsible Function(s). General rule: "Do not extract
         Responsible departments or policy owners unless they are
         explicitly part of the audience."

      2. Supersedes: drop values that match the Policy Number (the
         current document's reference). General rule: "Do Not Extract:
         Current document version."

      3. Last Reviewed: drop values that match the Effective Date/Period
         exactly. General rule: "Do Not Extract: Effective Date / Issue
         Date / Revision Date."

      4. Responsible Function Officer(s): when the slot is empty AND
         Approved by is populated, inherit Approved by. General rule:
         "Include individuals listed under Approved by, Policy Owner,
         Responsible Officer, Accountable Executive, Sponsor."

    The rules apply independently and are not order-dependent (each
    rule only operates on the input map's existing values).

    If a cross-field rule empties a slot, the value is removed so the
    renderer writes the standard "Data is not found in source file"
    marker.
    """
    if not field_map:
        return field_map

    out = dict(field_map)

    # Gather normalized values from supporting slots for dedup checks.
    policy_number = out.get("Policy Number:", "")
    effective_date = out.get("Effective Date/Period:", "")
    approved_by = out.get("Approved by:", "")

    # Gather Functional Area + Responsible Function values for Applies-to
    # dedup. Multi-value fields are comma-joined; split them.
    def _split_multi(v: str) -> list[str]:
        if not v:
            return []
        # Split on comma, semicolon, ` and `, ` & `.
        v2 = re.sub(r"\s+and\s+", ",", v, flags=re.IGNORECASE)
        v2 = re.sub(r"\s+&\s+", ",", v2)
        return [p.strip() for p in re.split(r"[,;]", v2) if p.strip()]

    func_area_parts = {
        _normalize_value_for_compare(p)
        for p in _split_multi(out.get("Functional Area(s):", ""))
    }
    resp_func_parts = {
        _normalize_value_for_compare(p)
        for p in _split_multi(out.get("Responsible Function(s):", ""))
    }
    protected_areas = func_area_parts | resp_func_parts

    # Rule 1: Applies to — drop values that match a protected area.
    applies_to = out.get("Applies to:", "")
    if applies_to:
        parts = _split_multi(applies_to)
        kept = [
            p for p in parts
            if _normalize_value_for_compare(p) not in protected_areas
        ]
        if not kept:
            out.pop("Applies to:", None)
        elif len(kept) != len(parts):
            out["Applies to:"] = ", ".join(kept)

    # Rule 2: Supersedes — drop values that match the Policy Number.
    supersedes = out.get("Supersedes:", "")
    if supersedes and policy_number:
        pol_norm = _normalize_value_for_compare(policy_number)
        if pol_norm:
            parts = [p.strip() for p in supersedes.split(";") if p.strip()]
            kept = [
                p for p in parts
                if _normalize_value_for_compare(p) != pol_norm
            ]
            if not kept:
                out.pop("Supersedes:", None)
            elif len(kept) != len(parts):
                out["Supersedes:"] = "; ".join(kept)

    # Rule 3: Last Reviewed — drop values that match Effective Date/Period.
    last_reviewed = out.get("Last Reviewed:", "")
    if last_reviewed and effective_date:
        if (
            _normalize_value_for_compare(last_reviewed)
            == _normalize_value_for_compare(effective_date)
        ):
            out.pop("Last Reviewed:", None)

    # Rule 4: Responsible Function Officer(s) — inherit Approved by when empty.
    # Layer E: only inherit when the source contains no Myanmar text.
    # Myanmar PDFs often have a separate officer value that doesn't
    # match Approved by; leaking Approved by there produces wrong output.
    # English PDFs preserve the original behavior (inherit when empty).
    officer_val = out.get("Responsible Function Officer(s):", "")
    if not officer_val and approved_by:
        try:
            from policy_platform.i18n.burmese_strings import has_burmese
            source_has_mm = any(
                has_burmese(p) for p in (paragraphs or []) if p
            )
        except Exception:
            source_has_mm = False
        if not source_has_mm:
            out["Responsible Function Officer(s):"] = approved_by

    return out


def _maybe_inject_reason_from_intro_paragraph(
    field_map: dict, paragraphs: list
) -> dict:
    """If the Reason for Policy slot is empty after label-row synthesis,
    infer it from a paragraph that begins with a reason-intro phrase.

    General English patterns detected:
      - "This policy is required/needed/necessary/intended/designed/established …"
      - "The purpose of this policy is …"
      - "In order to …"
      - "To ensure/comply with/meet …"
      - "Because/Since/As …"

    Only fires when the field_map has no Reason for Policy AND a
    paragraph matches the intro pattern. The matched paragraph is
    truncated to a single sentence (≤ 600 chars) to keep the slot
    concise.

    Works for any file whose reason-for-policy section starts with
    one of these general phrasing patterns — no per-file hardcoding.
    """
    if field_map.get("Reason for Policy:"):
        return field_map
    if not paragraphs:
        return field_map
    reason_intros = (
        "this policy is required",
        "this policy is needed",
        "this policy is necessary",
        "this policy is intended",
        "this policy is designed",
        "this policy is established",
        "this policy is aimed",
        "this policy aims",
        "this standard is required",
        "this standard is intended",
        "this procedure is required",
        "this guideline is required",
        "this framework is required",
        "this manual is required",
        "this charter is required",
        "this directive is required",
        "this regulation is required",
        "this document is required",
        "this document is intended",
        "this document is designed",
        "the purpose of this policy",
        "the purpose of this standard",
        "the purpose of this document",
        "in order to",
        "to ensure",
        "to comply with",
        "to meet",
        "to satisfy",
        "to address",
        "to protect",
        "because ",
        "since ",
        "as ",
        "aimed to provide",
        "designed to",
        "intended to",
        "established to",
        "created to",
    )
    for p in paragraphs[:30]:
        if not p:
            continue
        clean = str(p).strip()
        if not clean or len(clean) < 20 or len(clean) > 1000:
            continue
        low = clean.lower()
        if not any(low.startswith(intro) for intro in reason_intros):
            continue
        # Take only first sentence.
        first_sentence = re.split(r"(?<=[.!?])\s+", clean, maxsplit=1)[0]
        # Cap length.
        truncated = first_sentence[:600].rstrip()
        if not truncated:
            continue
        return {**field_map, "Reason for Policy:": truncated}
    return field_map


# Phase 6 — title prefix stripping pattern. Matches leading
# capitalized word(s) followed by a separator (– en-dash, — em-dash,
# hyphen, or colon). General heuristic — works for any future file
# whose title follows the "Document Type – Title" or "Document Type:
# Title" pattern.
_TITLE_PREFIX_RE = re.compile(
    r"^([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\s*[\u2013\u2014\-:]\s*"
)


def _strip_title_prefix(candidate: str) -> str:
    """Strip a leading document-type prefix from a title candidate.

    General heuristic — detects patterns like:
      - "Group Policy – Employee Health Benefit Policy"
        → "Employee Health Benefit Policy"
      - "Standard Procedure: Leave Application" → "Leave Application"
      - "Framework - Data Privacy" → "Data Privacy"
      - "Sexual Harassment Policy for All Employers" → unchanged
        (no separator prefix)

    Works for any document whose title is prefixed with a document-
    classification noun phrase followed by a separator (`–`, `—`,
    `-`, or `:`). HR_00002 PDF is used only as a reference for the
    expected format — no per-file hardcoding.
    """
    v = candidate.strip()
    m = _TITLE_PREFIX_RE.match(v)
    if m:
        # Only strip if the prefix is 1-4 capitalized words. This
        # avoids stripping single common words like "Annual" or
        # "Final" that are not document-type prefixes.
        prefix_words = m.group(1).split()
        if 1 <= len(prefix_words) <= 4:
            remainder = v[m.end():].strip()
            if remainder:
                return remainder
    return v


# Process-wide RAG pipeline. The underlying sentence-transformer +
# cross-encoder models are expensive to load, so we share a single
# instance across the lifetime of the server.
_PIPELINE: RetrievalPipeline | None = None


def get_pipeline() -> RetrievalPipeline:
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = RetrievalPipeline()
    return _PIPELINE


def process(input_path: Path, output_path: Path | None = None, *, fail_on_validation: bool = True) -> AuditResult:
    """Run the full pipeline. Returns an AuditResult.
    If fail_on_validation is True (default), raises ValidationFailed on integrity violation.
    If False, returns AuditResult with validation_ok=False (used by golden-brain self-test)."""
    input_path = Path(input_path)
    if not input_path.exists():
        raise PipelineError(f"Input file not found: {input_path}")

    run_id = new_run_id()
    started_at = now_iso()
    t0 = time.perf_counter()
    steps: list[AgentStep] = []
    sections_meta: list[dict] = []

    if output_path is None:
        config.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = config.OUTPUTS_DIR / f"{run_id}.docx"
    else:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    output_path = Path(output_path)

    # Step 1: Receive
    try:
        size = input_path.stat().st_size
        steps.append(_step(1, "Receive", True, f"{input_path.name} ({size} bytes)"))
    except Exception as e:
        steps.append(_step(1, "Receive", False, str(e)))
        raise PipelineError("Step 1 Receive failed") from e

    # Step 2: Extract
    try:
        extracted = extractor_dispatch(input_path)
        steps.append(_step(2, "Extract", True, f"format={extracted.source_format} sha={extracted.source_sha256[:12]}"))
    except Exception as e:
        steps.append(_step(2, "Extract", False, str(e)))
        raise PipelineError("Step 2 Extract failed") from e

    return _run_extracted_pipeline(
        extracted=extracted,
        output_path=output_path,
        steps=steps,
        sections_meta=sections_meta,
        started_at=started_at,
        t0=t0,
        run_id=run_id,
        input_path=input_path,
        header_text=None,
        header_version=None,
        fail_on_validation=fail_on_validation,
    )


def run_from_lines_json(
    lines_json: Iterable,
    output_path: Path,
    *,
    run_id: Optional[str] = None,
    document_name: str = "reviewer-edit-lines-json",
    fail_on_validation: bool = False,
    reviewer_bindings: Optional[dict] = None,
) -> AuditResult:
    """Phase 6 — re-run the Brain pipeline end-to-end against the
    reviewer's saved lines_json (rich or legacy). Useful for the
    'Publish & Generate DOCX' button: take whatever the reviewer saved,
    re-extract it (via LinesJsonExtractor), and run the same 7 steps.

    `fail_on_validation` defaults to False because the reviewer-driven
    output is permitted to differ from the Brain template's emitted
    value-counts (slot assignments can change) — a hard validation
    failure would block every Publish, which is the opposite of what
    this feature is for.

    If the lines_json payload contains an explicit `Policy Title:`
    or `Policy Number:` line, those values are extracted and passed
    through to the renderer as `header_text` / `header_version` so the
    header mirrors the body's title 1:1. The renderer's heuristic
    title-extractor can otherwise pick a long body paragraph (e.g. a
    scope statement) as the title because of length-based scoring —
    the explicit value short-circuits that path.

    `reviewer_bindings` (optional): `{slot_id: [reviewer_paragraph, ...]}`
    map produced by `api.lines_json_extractor.reviewer_slot_bindings`.
    When provided, the pipeline overrides each bound slot's
    `placed_paragraphs` with the reviewer's text BEFORE the render
    step. Slots not in the map are filled by RAG as usual (hybrid:
    edited slots are locked, unedited slots keep RAG content).
    """
    from api.lines_json_extractor import LinesJsonExtractor
    from api.docx_approved_export import extract_explicit_title_and_version
    if run_id is None:
        run_id = new_run_id()
    started_at = now_iso()
    t0 = time.perf_counter()
    steps: list[AgentStep] = []
    sections_meta: list[dict] = []
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Pull the body's explicit Policy Title / Policy Number BEFORE the
    # pipeline runs, so we can pass them through as `header_text` /
    # `header_version`. This guarantees `header.title == body.title`
    # and `header.version == body.number` regardless of how the
    # heuristic title extractor would otherwise score the paragraphs.
    try:
        explicit_title, explicit_version = extract_explicit_title_and_version(
            list(lines_json) if lines_json is not None else None
        )
    except Exception:
        explicit_title, explicit_version = None, None

    try:
        extracted = LinesJsonExtractor(lines_json).to_extracted_document()
        steps.append(
            _step(
                2, "Extract", True,
                f"format={extracted.source_format} sha={extracted.source_sha256[:12]} "
                f"paragraphs={len(extracted.paragraphs)} tables={len(extracted.tables)}",
            )
        )
    except Exception as e:
        steps.append(_step(2, "Extract", False, str(e)))
        raise PipelineError(f"Step 2 Extract (lines_json) failed: {e}") from e

    # Step 1 doesn't apply — we already have the lines_json content. Synthesise
    # a Receive step so the audit log is complete.
    steps.insert(0, _step(1, "Receive", True, f"{document_name} (lines_json)"))

    return _run_extracted_pipeline(
        extracted=extracted,
        output_path=output_path,
        steps=steps,
        sections_meta=sections_meta,
        started_at=started_at,
        t0=t0,
        run_id=run_id,
        input_path=Path(document_name),
        header_text=explicit_title,
        header_version=explicit_version,
        fail_on_validation=fail_on_validation,
        reviewer_bindings=reviewer_bindings,
    )


def _run_extracted_pipeline(
    *,
    extracted,
    output_path: Path,
    steps: list[AgentStep],
    sections_meta: list[dict],
    started_at: str,
    t0: float,
    run_id: str,
    input_path: Path,
    header_text: Optional[str],
    header_version: Optional[str],
    fail_on_validation: bool,
    reviewer_bindings: Optional[dict] = None,
) -> AuditResult:
    """Shared post-extraction body for `process` and `run_from_lines_json`."""
    # Step 2.5: Extract header info from the cleaned first page
    #
    # Per user directive, the header MUST show ONLY the explicit
    # `Policy Title:` / `Policy Number:` text from the body — no
    # heuristic fallback to whatever the longest paragraph or PDF
    # metadata happens to be. The pipeline computes `header_info`
    # (heuristic) below for audit logging only; the renderer's
    # `_replace_header_text` receives the explicit `header_text` /
    # `header_version` (which may be `None` → empty header slot).
    header_info = header_extractor.extract(
        input_path,
        pdf_metadata=None,
        cleaned_paragraphs=list(extracted.paragraphs),
    )
    # Explicit-only: when the caller supplied `header_text` /
    # `header_version` (from `extract_explicit_title_and_version`),
    # those are the SOLE source. When the caller did NOT supply them
    # (regular PDF pipeline path with no `lines_json`), keep the
    # heuristic values so a PDF with no explicit label still gets a
    # title — but a `lines_json` path with no explicit label gets an
    # empty header slot. Caller signals "explicit-only" by passing
    # `header_text=""` (empty string) vs. `None`.
    if header_text is not None:
        header_title = header_text if header_text else ""
        header_version = header_version if header_version else ""
    else:
        header_title = header_info.get("title")
        header_version = header_info.get("version")
    header_source = (
        "explicit" if header_text is not None else header_info.get("source", "fallback")
    )

    # Step 2.7: Extract label-value pairs (Phase 7) for slot 1 + slot 3 fields.
    from .extractors import field_parser
    from .rag.table_routing import _looks_like_label_row_table

    def _label_row_tables_to_paragraphs(tables):
        """Convert key-value tables into `Label: value` paragraphs.

        Supports both the canonical 2-column form (col 0 = label,
        col 1 = value) AND the transposed N-column form (row 0 =
        labels across columns, row 1 = values across columns). The
        transposed form is general — it works for any future file
        that encodes slot-1 metadata as a single-row label table.

        Phase 8 (field-extraction completeness for Myanmar PDFs):
        Newlines in label cells are collapsed to spaces so multi-line
        labels like `Effected/Review\ndate` become a single-token
        `Effected/Review date` that the Brain label vocabulary can
        match. Without this, the synthesized paragraph carries an
        embedded newline that breaks `_LABEL_LINE_RE`'s single-line
        anchor and the label is silently dropped.
        """
        out: list[str] = []
        if not tables:
            return out
        for tbl in tables:
            if not tbl or not _looks_like_label_row_table(tbl):
                continue
            # ---- Phase 5: TRANSPOSED form (N columns, labels in row 0, values in row 1) ----
            n_cols = max(len(r) for r in tbl if r)
            if n_cols >= 3 and len(tbl) <= 3:
                header_cells = [
                    (" ".join(str(c).split()) if c else "") for c in (tbl[0] or [])
                ]
                value_row = tbl[1] if len(tbl) >= 2 else []
                value_cells = [
                    (" ".join(str(c).split()) if c else "") for c in (value_row or [])
                ]
                for label, value in zip(header_cells, value_cells):
                    if not label or not value:
                        continue
                    if label.endswith(":"):
                        out.append(f"{label} {value}")
                    else:
                        out.append(f"{label}: {value}")
                continue
            # ---- Canonical 2-column form (col 0 = label, col 1 = value) ----
            for row in tbl:
                if not row or len(row) < 2:
                    continue
                label = " ".join((row[0] or "").split())
                value = " ".join((row[1] or "").split())
                if not label or not value:
                    continue
                if label.endswith(":"):
                    out.append(f"{label} {value}")
                else:
                    out.append(f"{label}: {value}")
        return out

    synth_paragraphs = _label_row_tables_to_paragraphs(
        getattr(extracted, "tables", None)
    )
    # Phase 8 (field-extraction completeness): parse synth and plain
    # SEPARATELY, then merge with synth authoritative. Background —
    # Myanmar PDFs render their English header labels in BOTH a
    # transposed header table (clean values) AND as raw text lines
    # (where post-OCR has split "Daw Win Win Tint" into "Daw Win" +
    # "Win Tint" across cells, or contaminated the date with the
    # Myanmar title). The previous "prefer first non-empty" merge
    # silently overwrote the clean synth values with the corrupted
    # plain-text values. Synth wins on conflict; plain only fills
    # fields that synth did not populate. English PDFs (no table) see
    # no behavior change because synth is empty.
    if synth_paragraphs:
        synth_map = field_parser.parse(
            list(synth_paragraphs),
            dropped_paragraphs=getattr(extracted, "cleaner_dropped", None),
            cleaned_to_original=getattr(extracted, "original_indices", None),
        ) or {}
        plain_map = field_parser.parse(
            list(extracted.paragraphs),
            dropped_paragraphs=getattr(extracted, "cleaner_dropped", None),
            cleaned_to_original=getattr(extracted, "original_indices", None),
        ) or {}
        # synth authoritative, plain fills gaps
        field_map = dict(plain_map)
        for k, v in synth_map.items():
            if v:
                field_map[k] = v
    else:
        field_map = field_parser.parse(
            list(extracted.paragraphs),
            dropped_paragraphs=getattr(extracted, "cleaner_dropped", None),
            cleaned_to_original=getattr(extracted, "original_indices", None),
        ) or {}
    if not field_map:
        field_map = {}

    field_map = _maybe_inject_title_from_top_paragraph(
        field_map, list(extracted.paragraphs)
    )
    field_map = _maybe_inject_reason_from_intro_paragraph(
        field_map, list(extracted.paragraphs)
    )
    field_map = _refine_field_map_cross_field(field_map, list(extracted.paragraphs))
    # Phase 7: prose-inference for label-light documents. Fills empty
    # slots from body prose using general English patterns. Existing
    # label-row values are not overwritten.
    from .extractors.narrative_inference import infer_narrative_fields
    inferred = infer_narrative_fields(
        list(extracted.paragraphs), field_map
    )
    if inferred:
        for k, v in inferred.items():
            if not field_map.get(k):
                field_map[k] = v

    # Step 3: RAG-Hybrid retrieval
    try:
        from .framework.slot_tiers import SLOT_TIERS, slot_required
        rag_result = get_pipeline().run(
            list(extracted.paragraphs),
            tables=list(extracted.tables) if getattr(extracted, "tables", None) else None,
            table_paragraph_indices=list(extracted.table_paragraph_indices)
                if getattr(extracted, "table_paragraph_indices", None) else None,
        )
        # Burmese post-processing: when the English RAG pipeline returns
        # ``no_*_section`` markers (because no English heading was found),
        # try a Burmese heading-anchor match and override the slot result.
        # This is a non-invasive hook — ``RetrievalPipeline`` is unchanged.
        try:
            from .extract_myanmar.burmese_pipeline import (
                apply_burmese_heading_anchors,
                apply_burmese_label_row_overrides,
            )
            apply_burmese_heading_anchors(
                list(extracted.paragraphs), rag_result,
                tables=list(extracted.tables) if getattr(extracted, "tables", None) else None,
                table_paragraph_indices=list(extracted.table_paragraph_indices) if getattr(extracted, "table_paragraph_indices", None) else None,
            )
            apply_burmese_label_row_overrides(
                list(extracted.paragraphs), rag_result
            )
        except Exception as _e:
            import traceback
            traceback.print_exc()
            pass
        classified = build_classification_from_rag(
            rag_result,
            source_paragraph_count=len(extracted.paragraphs),
        )
        for sec in FROZEN_SECTIONS:
            sid = sec["id"]
            slot = classified.sections.get(sid)
            status = slot.status if slot else config.SKIPPED_STATUS
            paras = slot.content_paragraphs if slot else []
            placed = slot.placed_paragraphs if slot and slot.placed_paragraphs else []
            tables = slot.content_tables if slot else []
            routing_rule = slot.routing_rule if slot else ""
            placed_chars = sum(len(p) for p in placed) + sum(
                len(c) for t in tables for r in t for c in r
            )
            tier = SLOT_TIERS.get(sid, 3)
            required = slot_required(sid)
            has_real_content = (status != "Found") and (not paras or not any(p.strip() for p in paras))
            placeholder_rendered = bool(required and (status != "Found") and has_real_content)
            sections_meta.append({
                "id": sid,
                "name": sec["title"],
                "status": status,
                "routing_rule": routing_rule,
                "tier": tier,
                "required": required,
                "slot_unchanged": status in (config.SKIPPED_STATUS, config.FOUND_EMPTY_STATUS),
                "paragraphs": placed,
                "source_paragraph_count": len(paras),
                "tables": tables,
                "placed_chars": placed_chars,
                "dropped_chars": 0,
                "placeholder_rendered": placeholder_rendered,
            })
        found = sum(1 for s in sections_meta if s["status"] == "Found")
        skipped = sum(1 for s in sections_meta if s["status"] == config.SKIPPED_STATUS)
        empty = sum(1 for s in sections_meta if s["status"] == config.FOUND_EMPTY_STATUS)
        rag_detail = (
            f"found={found} skipped={skipped} empty={empty} "
            f"embedder={rag_result.embedder_backend} faiss={rag_result.faiss_backend} "
            f"reranker={rag_result.reranker_backend} timed_out={rag_result.timed_out}"
        )
        steps.append(_step(3, "RAG-Retrieve", True, rag_detail))
    except Exception as e:
        steps.append(_step(3, "RAG-Retrieve", False, str(e)))
        raise PipelineError(f"Step 3 RAG-Retrieve failed: {e}") from e

    # Step 4: Apply (no transformation; routing result is the payload)
    try:
        manifest = brain_loader.init_or_verify(init=False)
        steps.append(_step(4, "Apply", True, f"framework={manifest['version']}"))
    except Exception as e:
        steps.append(_step(4, "Apply", False, str(e)))
        raise PipelineError(f"Step 4 Apply failed: {e}") from e

    # Step 4.5: Reviewer-bindings override. When the caller supplied a
    # `reviewer_bindings` map (publish path), override each bound slot's
    # `placed_paragraphs` AND `content_paragraphs` with the reviewer's
    # text BEFORE the render step so the renderer's `placed_paragraphs`
    # is what gets written to the .docx. Unbound slots keep RAG output.
    # slot=0 entries (reviewer additions outside any known slot) are
    # ignored here — the renderer's existing fallback path handles
    # them.
    if reviewer_bindings:
        try:
            bound_count = 0
            for sid, slot in classified.sections.items():
                # Normalise sid key types (JSON returns str, dataclass
                # may store int; check both).
                key_candidates = [sid, int(sid)] if isinstance(sid, str) and sid.isdigit() else [sid]
                reviewer_paras = None
                for k in key_candidates:
                    if k in reviewer_bindings:
                        reviewer_paras = reviewer_bindings[k]
                        break
                if not reviewer_paras:
                    continue
                # Replace both placed (rendered) and content (audit)
                # lists. The status flips to "Found" so the renderer
                # treats it as a populated slot.
                slot.placed_paragraphs = list(reviewer_paras)
                slot.content_paragraphs = list(reviewer_paras)
                slot.status = "Found"
                bound_count += 1
            steps.append(
                _step(
                    4, "Reviewer-Bind", True,
                    f"bound_slots={bound_count}/{len(reviewer_bindings or {})}",
                )
            )
        except Exception as e:
            print(f'[_run_extracted_pipeline] reviewer-bind failed: {e}', flush=True)
            steps.append(_step(4, "Reviewer-Bind", False, str(e)))
            # Non-fatal: continue without binding.

    # Step 5+6: Render
    try:
        render(
            classified,
            extracted,
            config.BRAIN_PATH,
            output_path,
            header_text=header_title,
            header_version=header_version,
            field_map=field_map,
        )
        for s in sections_meta:
            sid = s["id"]
            slot = classified.sections.get(sid)
            if slot and slot.placed_paragraphs:
                s["paragraphs"] = slot.placed_paragraphs
                s["placed_chars"] = sum(len(p) for p in slot.placed_paragraphs) + sum(
                    len(c) for t in slot.content_tables for r in t for c in r
                )
        steps.append(
            _step(
                5,
                "Render",
                True,
                f"path={output_path} header_source={header_source} title={header_title!r} version={header_version!r}",
            )
        )
    except Exception as e:
        steps.append(_step(5, "Render", False, str(e)))
        raise PipelineError(f"Step 5/6 Render failed: {e}") from e

    # Step 7: Validate
    report: dict = {"checks": []}
    try:
        report = validate(extracted, output_path, classified)
        steps.append(_step(6, "Validate", True, f"checks={len(report.get('checks', []))}"))
    except ValidationFailed as vf:
        steps.append(_step(6, "Validate", False, str(vf)))
        if fail_on_validation:
            raise
    except Exception as e:
        steps.append(_step(6, "Validate", False, str(e)))
        if fail_on_validation:
            raise PipelineError(f"Step 7 Validate failed: {e}") from e

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    finished_at = now_iso()

    total_source_chars = sum(len(p) for p in extracted.paragraphs) + sum(
        len(c) for t in extracted.tables for r in t for c in r
    )
    placed_total = sum(s.get("placed_chars", 0) for s in sections_meta)
    total_dropped = max(0, total_source_chars - placed_total)
    total_dropped_paragraphs = len(classified.dropped_paragraph_indices)

    unallocated_slots = [s for s in sections_meta if s["status"] not in ("Found",)]
    per_slot_drop = 0
    if unallocated_slots and total_dropped > 0:
        per_slot_drop = total_dropped // len(unallocated_slots)
        for s in unallocated_slots:
            s["dropped_chars"] = per_slot_drop
    leftover = total_dropped - per_slot_drop * len(unallocated_slots) if unallocated_slots else 0
    if unallocated_slots and leftover > 0:
        unallocated_slots[0]["dropped_chars"] += leftover

    sample = []
    for entry in (extracted.cleaner_dropped or [])[:20]:
        sample.append({
            "index": entry.get("index", ""),
            "text": entry.get("text", ""),
            "reason": entry.get("reason", "cleaner"),
        })
    remaining = max(0, 20 - len(sample))
    for idx in classified.dropped_paragraph_indices[:remaining]:
        if 0 <= idx < len(extracted.paragraphs):
            sample.append({"index": idx, "text": extracted.paragraphs[idx], "reason": "rag_dropped"})

    integrity_checks = report.get("checks", []) if isinstance(report, dict) else []

    result = AuditResult(
        run_id=run_id,
        document_name=str(input_path.name),
        processing_time_ms=elapsed_ms,
        framework_version=manifest["version"],
        framework_sha256=manifest["sha256"],
        started_at=started_at,
        finished_at=finished_at,
        validation_ok=not any(not s.ok for s in steps if s.name == "Validate"),
        output_path=str(output_path),
        audit_json="",
        sections=sections_meta,
        steps=steps,
        integrity_checks=integrity_checks,
        fallback_used=classified.fallback_used,
        total_placed_chars=placed_total,
        total_dropped_chars=total_dropped,
        total_dropped_paragraphs=total_dropped_paragraphs,
        dropped_paragraphs_sample=sample,
        extraction_path="rag",
    )
    result.audit_json = write_audit(result)
    return result

    # Step 2.5: Extract header info from the cleaned first page
    header_info = header_extractor.extract(
        input_path,
        pdf_metadata=None,
        cleaned_paragraphs=list(extracted.paragraphs),
    )
    header_title = header_info.get("title")
    header_version = header_info.get("version")
    header_source = header_info.get("source", "fallback")

    # Step 2.7: Extract label-value pairs (Phase 7) for slot 1 + slot 3 fields.
    #
    # Some PDFs place the label-row data inside a 2-column key-value
    # table at the top of the document (e.g., Award PDF, School PDF).
    # The standard regex path walks `extracted.paragraphs` only and
    # therefore misses those values. To populate slots 1 and 3 in those
    # cases, we synthesize `Label: value` paragraphs from any 2-column
    # key-value table and feed them to the parser. The parser itself is
    # not modified — it receives a longer paragraph list and matches the
    # synthetic lines against its canonical-label vocabulary (which only
    # covers slot 1, 2, 3, 4, 11). Slots 9/10/14 and all other code paths
    # are untouched.
    from .extractors import field_parser
    from .rag.table_routing import _looks_like_label_row_table

    def _label_row_tables_to_paragraphs(tables):
        """Convert key-value tables into `Label: value` paragraphs.

        Returns a list of synthetic paragraph strings (possibly empty).
        Only tables detected as label-row tables are converted; other
        tables (tier tables, exclusion tables, history tables) are
        skipped by `_looks_like_label_row_table()`.

        Supports both the canonical 2-column form (col 0 = label,
        col 1 = value) AND the transposed N-column form (row 0 =
        labels across columns, row 1 = values across columns). The
        transposed form is general — it works for any future file
        that encodes slot-1 metadata as a single-row label table.
        """
        out: list[str] = []
        if not tables:
            return out
        for tbl in tables:
            if not tbl or not _looks_like_label_row_table(tbl):
                continue
            # ---- Phase 5: TRANSPOSED form (N columns, labels in row 0, values in row 1) ----
            n_cols = max(len(r) for r in tbl if r)
            if n_cols >= 3 and len(tbl) <= 3:
                header_cells = [
                    (str(c).strip() if c else "") for c in (tbl[0] or [])
                ]
                value_row = tbl[1] if len(tbl) >= 2 else []
                value_cells = [
                    (str(c).strip() if c else "") for c in (value_row or [])
                ]
                for label, value in zip(header_cells, value_cells):
                    if not label or not value:
                        continue
                    if label.endswith(":"):
                        out.append(f"{label} {value}")
                    else:
                        out.append(f"{label}: {value}")
                continue
            # ---- Canonical 2-column form (col 0 = label, col 1 = value) ----
            for row in tbl:
                if not row or len(row) < 2:
                    continue
                label = (row[0] or "").strip()
                value = (row[1] or "").strip()
                if not label or not value:
                    continue
                if label.endswith(":"):
                    out.append(f"{label} {value}")
                else:
                    out.append(f"{label}: {value}")
        return out

    synth_paragraphs = _label_row_tables_to_paragraphs(
        getattr(extracted, "tables", None)
    )
    parser_input = list(synth_paragraphs) + list(extracted.paragraphs)
    field_map = field_parser.parse(
        parser_input,
        dropped_paragraphs=getattr(extracted, "cleaner_dropped", None),
        cleaned_to_original=getattr(extracted, "original_indices", None),
    )
    if not field_map:
        field_map = {}

    # Title fallback: if the Policy Title slot is still empty after
    # label-row synthesis + field parsing, use the first short heading-like
    # paragraph near the top of the document. Structural heuristic — no
    # hardcoded labels, no defaults. Only fires when no Policy Title was
    # already extracted AND a top paragraph looks title-like (2-15 words,
    # no trailing period).
    field_map = _maybe_inject_title_from_top_paragraph(
        field_map, list(extracted.paragraphs)
    )
    field_map = _maybe_inject_reason_from_intro_paragraph(
        field_map, list(extracted.paragraphs)
    )
    field_map = _refine_field_map_cross_field(field_map, list(extracted.paragraphs))
    # Phase 7: prose-inference for label-light documents. Fills empty
    # slots from body prose using general English patterns. Existing
    # label-row values are not overwritten.
    from .extractors.narrative_inference import infer_narrative_fields
    inferred = infer_narrative_fields(
        list(extracted.paragraphs), field_map
    )
    if inferred:
        for k, v in inferred.items():
            if not field_map.get(k):
                field_map[k] = v

    # Step 3: RAG-Hybrid retrieval
    try:
        from .framework.slot_tiers import SLOT_TIERS, slot_required
        rag_result = get_pipeline().run(
            list(extracted.paragraphs),
            tables=list(extracted.tables) if getattr(extracted, "tables", None) else None,
            table_paragraph_indices=list(extracted.table_paragraph_indices)
                if getattr(extracted, "table_paragraph_indices", None) else None,
        )
        # Burmese post-processing: when the English RAG pipeline returns
        # ``no_*_section`` markers (because no English heading was found),
        # try a Burmese heading-anchor match and override the slot result.
        # This is a non-invasive hook — ``RetrievalPipeline`` is unchanged.
        try:
            from .extract_myanmar.burmese_pipeline import (
                apply_burmese_heading_anchors,
                apply_burmese_label_row_overrides,
            )
            apply_burmese_heading_anchors(
                list(extracted.paragraphs), rag_result,
                tables=list(extracted.tables) if getattr(extracted, "tables", None) else None,
                table_paragraph_indices=list(extracted.table_paragraph_indices) if getattr(extracted, "table_paragraph_indices", None) else None,
            )
            apply_burmese_label_row_overrides(
                list(extracted.paragraphs), rag_result
            )
        except Exception as _e:
            import traceback
            traceback.print_exc()
            pass
        classified = build_classification_from_rag(
            rag_result,
            source_paragraph_count=len(extracted.paragraphs),
        )
        for sec in FROZEN_SECTIONS:
            sid = sec["id"]
            slot = classified.sections.get(sid)
            status = slot.status if slot else config.SKIPPED_STATUS
            paras = slot.content_paragraphs if slot else []
            placed = slot.placed_paragraphs if slot and slot.placed_paragraphs else []
            tables = slot.content_tables if slot else []
            routing_rule = slot.routing_rule if slot else ""
            placed_chars = sum(len(p) for p in placed) + sum(
                len(c) for t in tables for r in t for c in r
            )
            tier = SLOT_TIERS.get(sid, 3)
            required = slot_required(sid)
            has_real_content = (status != "Found") and (not paras or not any(p.strip() for p in paras))
            placeholder_rendered = bool(required and (status != "Found") and has_real_content)
            sections_meta.append({
                "id": sid,
                "name": sec["title"],
                "status": status,
                "routing_rule": routing_rule,
                "tier": tier,
                "required": required,
                "slot_unchanged": status in (config.SKIPPED_STATUS, config.FOUND_EMPTY_STATUS),
                "paragraphs": placed,
                "source_paragraph_count": len(paras),
                "tables": tables,
                "placed_chars": placed_chars,
                "dropped_chars": 0,
                "placeholder_rendered": placeholder_rendered,
            })
        found = sum(1 for s in sections_meta if s["status"] == "Found")
        skipped = sum(1 for s in sections_meta if s["status"] == config.SKIPPED_STATUS)
        empty = sum(1 for s in sections_meta if s["status"] == config.FOUND_EMPTY_STATUS)
        rag_detail = (
            f"found={found} skipped={skipped} empty={empty} "
            f"embedder={rag_result.embedder_backend} faiss={rag_result.faiss_backend} "
            f"reranker={rag_result.reranker_backend} timed_out={rag_result.timed_out}"
        )
        steps.append(_step(3, "RAG-Retrieve", True, rag_detail))
    except Exception as e:
        steps.append(_step(3, "RAG-Retrieve", False, str(e)))
        raise PipelineError(f"Step 3 RAG-Retrieve failed: {e}") from e

    # Step 4: Apply (no transformation; routing result is the payload)
    try:
        manifest = brain_loader.init_or_verify(init=False)
        steps.append(_step(4, "Apply", True, f"framework={manifest['version']}"))
    except Exception as e:
        steps.append(_step(4, "Apply", False, str(e)))
        raise PipelineError(f"Step 4 Apply failed: {e}") from e

    # Step 4.5: Reviewer-bindings override. When the caller supplied a
    # `reviewer_bindings` map (publish path), override each bound slot's
    # `placed_paragraphs` AND `content_paragraphs` with the reviewer's
    # text BEFORE the render step so the renderer's `placed_paragraphs`
    # is what gets written to the .docx. Unbound slots keep RAG output.
    # slot=0 entries (reviewer additions outside any known slot) are
    # ignored here — the renderer's existing fallback path handles
    # them.
    if reviewer_bindings:
        try:
            bound_count = 0
            for sid, slot in classified.sections.items():
                # Normalise sid key types (JSON returns str, dataclass
                # may store int; check both).
                key_candidates = [sid, int(sid)] if isinstance(sid, str) and sid.isdigit() else [sid]
                reviewer_paras = None
                for k in key_candidates:
                    if k in reviewer_bindings:
                        reviewer_paras = reviewer_bindings[k]
                        break
                if not reviewer_paras:
                    continue
                # Replace both placed (rendered) and content (audit)
                # lists. The status flips to "Found" so the renderer
                # treats it as a populated slot.
                slot.placed_paragraphs = list(reviewer_paras)
                slot.content_paragraphs = list(reviewer_paras)
                slot.status = "Found"
                bound_count += 1
            steps.append(
                _step(
                    4, "Reviewer-Bind", True,
                    f"bound_slots={bound_count}/{len(reviewer_bindings or {})}",
                )
            )
        except Exception as e:
            print(f'[_run_extracted_pipeline] reviewer-bind failed: {e}', flush=True)
            steps.append(_step(4, "Reviewer-Bind", False, str(e)))
            # Non-fatal: continue without binding.

    # Step 5+6: Render
    try:
        render(
            classified,
            extracted,
            config.BRAIN_PATH,
            output_path,
            header_text=header_title,
            header_version=header_version,
            field_map=field_map,
        )
        for s in sections_meta:
            sid = s["id"]
            slot = classified.sections.get(sid)
            if slot and slot.placed_paragraphs:
                s["paragraphs"] = slot.placed_paragraphs
                s["placed_chars"] = sum(len(p) for p in slot.placed_paragraphs) + sum(
                    len(c) for t in slot.content_tables for r in t for c in r
                )
        steps.append(
            _step(
                5,
                "Render",
                True,
                f"path={output_path} header_source={header_source} title={header_title!r} version={header_version!r}",
            )
        )
    except Exception as e:
        steps.append(_step(5, "Render", False, str(e)))
        raise PipelineError(f"Step 5/6 Render failed: {e}") from e

    # Step 7: Validate
    report: dict = {"checks": []}
    try:
        report = validate(extracted, output_path, classified)
        steps.append(_step(6, "Validate", True, f"checks={len(report.get('checks', []))}"))
    except ValidationFailed as vf:
        steps.append(_step(6, "Validate", False, str(vf)))
        if fail_on_validation:
            raise
    except Exception as e:
        steps.append(_step(6, "Validate", False, str(e)))
        if fail_on_validation:
            raise PipelineError(f"Step 7 Validate failed: {e}") from e

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    finished_at = now_iso()

    total_source_chars = sum(len(p) for p in extracted.paragraphs) + sum(
        len(c) for t in extracted.tables for r in t for c in r
    )
    placed_total = sum(s.get("placed_chars", 0) for s in sections_meta)
    total_dropped = max(0, total_source_chars - placed_total)
    total_dropped_paragraphs = len(classified.dropped_paragraph_indices)

    unallocated_slots = [s for s in sections_meta if s["status"] not in ("Found",)]
    per_slot_drop = 0
    if unallocated_slots and total_dropped > 0:
        per_slot_drop = total_dropped // len(unallocated_slots)
        for s in unallocated_slots:
            s["dropped_chars"] = per_slot_drop
    leftover = total_dropped - per_slot_drop * len(unallocated_slots) if unallocated_slots else 0
    if unallocated_slots and leftover > 0:
        unallocated_slots[0]["dropped_chars"] += leftover

    sample = []
    for entry in (extracted.cleaner_dropped or [])[:20]:
        sample.append({
            "index": entry.get("index", ""),
            "text": entry.get("text", ""),
            "reason": entry.get("reason", "cleaner"),
        })
    remaining = max(0, 20 - len(sample))
    for idx in classified.dropped_paragraph_indices[:remaining]:
        if 0 <= idx < len(extracted.paragraphs):
            sample.append({"index": idx, "text": extracted.paragraphs[idx], "reason": "rag_dropped"})

    integrity_checks = report.get("checks", []) if isinstance(report, dict) else []

    result = AuditResult(
        run_id=run_id,
        document_name=input_path.name,
        processing_time_ms=elapsed_ms,
        framework_version=manifest["version"],
        framework_sha256=manifest["sha256"],
        started_at=started_at,
        finished_at=finished_at,
        validation_ok=not any(not s.ok for s in steps if s.name == "Validate"),
        output_path=str(output_path),
        audit_json="",
        sections=sections_meta,
        steps=steps,
        integrity_checks=integrity_checks,
        fallback_used=classified.fallback_used,
        total_placed_chars=placed_total,
        total_dropped_chars=total_dropped,
        total_dropped_paragraphs=total_dropped_paragraphs,
        dropped_paragraphs_sample=sample,
        extraction_path="rag",
    )
    result.audit_json = write_audit(result)
    return result

