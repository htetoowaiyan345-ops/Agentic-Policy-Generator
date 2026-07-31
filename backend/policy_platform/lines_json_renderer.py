"""lines_json_renderer.py

Stage 2 — direct slot-by-slot writer for the reviewer's saved `lines_json`.

Why this file exists
--------------------
The legacy publish path (`policy_platform.pipeline.run_from_lines_json`) routes
the reviewer's saved content through the full Brain pipeline (RAG + classifier
+ renderer). That path is correct for fresh PDF input but loses the reviewer's
exact edits because every saved paragraph/table is tagged `slot=0` in the
database — and the classifier cannot route `slot=0` content to any of the
14 frozen Brain slots, so the pipeline falls back to the Brain template's
default text and overwrites the user's edits.

This module takes a different approach: treat the Brain template as a
**frozen scaffold** and write the reviewer's saved content directly into
each slot's body, slot-by-slot, paragraph-by-paragraph, character-by-character.
The brain framework (15 slots, slot titles, ordering, fonts, header, footer,
logo, media) is untouched; only the slot body content is replaced.

What this module preserves
--------------------------
* The 15-slot Brain framework structure (slot titles, ordering, page layout).
* The Brain header (logo + title + version + connector line) — untouched.
* The Brain footer (page X of Y, history anchor) — untouched.
* The Brain's frozen slot capacity (each slot has a fixed number of body
  paragraphs in the template; we never grow slots beyond their scaffold).
* The Brain's table dimensions for slots 10 and 14 (HISTORY table).
* Rich text formatting from the reviewer's HTML: bold, italic, underline,
  strikethrough, color, font-family, font-size, headings, lists, footnotes.
* Real spaces (no `&nbsp;` injection — `&nbsp;` only appears if the user
  typed it).
* Real line breaks (`<br>` in the user's HTML becomes a soft break in Word).
* Each user paragraph becomes its own Word paragraph (no merging into a
  single line, no flattening, no bullet reinterpretation).

What this module DOES NOT do
----------------------------
* Does not run RAG / classifier / extractor (those are designed for fresh
  PDF input, not re-rendering reviewer content).
* Does not modify the slot title or ordering.
* Does not change the slot heading text (label rows in slots 1, 2, 3, 4,
  11 are still handled by the existing `_apply_brain_label_rows` logic
  inside the brain renderer's `render()` call — see `publish_to_brain.py`
  which delegates to this writer first, then to the legacy pipeline's
  label-row pass as a follow-up).
* Does not inject `&nbsp;` for spacing.

Stage mapping
-------------
* Stage 1: editor slot assignment (frontend)
* Stage 2: this file (the core fix)
* Stage 3: wired into publish_to_brain.py (silent fallback)
* Stage 4: spacing/formatting fidelity (handled here, no `&nbsp;` injection)
* Stage 5: backward compatibility (slot=0 content → free-paragraph zone)
* Stage 6: verification (tests)
"""
from __future__ import annotations

import copy
import importlib
import importlib.util
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from . import config
from .framework.brain_slot_map import BRAIN_SLOT_RANGES, SLOT_HEADINGS, find_slot_boundaries
from .framework.section_map import FROZEN_SECTIONS


# Frozen-slot guards. Slots outside this set are skipped by the renderer.
# Slot 15 = Brain Logo & Image — never touched.
_SLOT_IDS_TO_RENDER: frozenset[int] = frozenset(range(1, 15))


def _normalise_paragraph_payload(payload: Any) -> dict:
    """Coerce a `['p', payload]` payload into the rich `dict` shape.

    Accepts:
      * `dict` already rich — returned with slot normalized to int.
      * `str` — wrapped into `{'slot': 0, 'text': str, 'html': ...}`.

    Empty / whitespace-only paragraphs are returned with an empty text.
    """
    if isinstance(payload, dict):
        try:
            slot = int(payload.get('slot', 0) or 0)
        except (TypeError, ValueError):
            slot = 0
        text = payload.get('text') or payload.get('html') or ''
        html = payload.get('html') or payload.get('text') or ''
        return {'slot': slot, 'text': text, 'html': html}
    if isinstance(payload, str):
        return {'slot': 0, 'text': payload, 'html': payload}
    return {'slot': 0, 'text': '', 'html': ''}


def _normalise_table_payload(payload: Any) -> dict:
    """Coerce a `['t', payload]` payload into the rich `dict` shape.

    Accepts `dict` (with `slot`, `rows`) or `list` (legacy rows-only).
    Each row is normalized to a list of `{text, html}` cell dicts when
    the source was dict-shaped, or to a list of strings when legacy.
    """
    if isinstance(payload, dict):
        try:
            slot = int(payload.get('slot', 0) or 0)
        except (TypeError, ValueError):
            slot = 0
        rows = payload.get('rows') or []
        return {'slot': slot, 'rows': rows}
    if isinstance(payload, list):
        # Legacy list-of-list-of-strings
        return {'slot': 0, 'rows': [list(r) for r in payload]}
    return {'slot': 0, 'rows': []}


def _normalise_lines_json(lines_json: Iterable) -> tuple[list[dict], list[dict]]:
    """Return (paragraphs_by_slot, tables_by_slot) — both keyed by slot id.

    Each dict is `{slot_id: [entries...]}`. Slot 0 entries (free paragraphs)
    are kept under key `0` and rendered into the free-paragraph zone at the
    top of the body.

    Entries are dicts; for paragraphs `{'slot', 'text', 'html'}`; for tables
    `{'slot', 'rows'}` where `rows` is a list of rows of either dict cells
    or string cells (we handle both shapes when writing).
    """
    paragraphs: dict[int, list[dict]] = {}
    tables: dict[int, list[dict]] = {}
    for raw in lines_json or []:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            continue
        kind, payload = raw[0], raw[1]
        if kind == 'p':
            p = _normalise_paragraph_payload(payload)
            slot = p['slot']
            paragraphs.setdefault(slot, []).append(p)
        elif kind == 't':
            t = _normalise_table_payload(payload)
            slot = t['slot']
            tables.setdefault(slot, []).append(t)
    return paragraphs, tables


# ---------------------------------------------------------------------------
# Media integrity (mirrors renderer._verify_media_against)
# ---------------------------------------------------------------------------

def _read_media(path: Path) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.startswith("word/media/"):
                out.append((n, z.read(n)))
    return out


def _verify_media_against(brain_path: Path, output_path: Path) -> None:
    expected = {n: d for n, d in _read_media(brain_path)}
    actual = {n: d for n, d in _read_media(output_path)}
    for n, d in expected.items():
        if n not in actual or _sha256(actual[n]) != _sha256(d):
            raise RuntimeError(f"Brain media integrity check failed for {n}")


def _sha256(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


def _restore_media_store_compression(docx_path: Path) -> None:
    """python-docx writes media files (JPEG, PNG) using DEFLATE compression.
    Microsoft's strict OOXML reader sometimes rejects DEFLATE-compressed media.
    The Brain template stores media as STORE (no compression). This function
    rewrites the docx so that word/media/* entries use STORE compression
    while everything else stays DEFLATE."""
    tmp_path = docx_path.with_suffix(".docx.tmp")
    with zipfile.ZipFile(str(docx_path), "r") as z_in, zipfile.ZipFile(
        str(tmp_path), "w", zipfile.ZIP_DEFLATED
    ) as z_out:
        for info in z_in.infolist():
            data = z_in.read(info.filename)
            if info.filename.startswith("word/media/"):
                z_out.writestr(
                    zipfile.ZipInfo(info.filename, date_time=info.date_time),
                    data,
                    compress_type=zipfile.ZIP_STORED,
                )
            else:
                z_out.writestr(info, data)
    tmp_path.replace(docx_path)


# ---------------------------------------------------------------------------
# Rich paragraph writer (delegates to existing docx_rich_writer)
# ---------------------------------------------------------------------------

def _import_rich_writer():
    """Locate `docx_rich_writer.write_paragraph` under any reasonable
    import path. The renderer is a sub-module of `policy_platform` and
    may be loaded via several patterns:

      * As `policy_platform.lines_json_renderer` (normal production
        path) — relative import `from ..docx_rich_writer import ...`
        would work but `docx_rich_writer` lives in the `api` package,
        not under `policy_platform`, so the relative path differs.
      * As a bare module via importlib.util (test path) — the
        import path is set up by the test harness, and may include
        or exclude `backend/` on sys.path.
      * When launched via `python -m api.server` (real backend) —
        `from api.docx_rich_writer import ...` works because the
        cwd is `backend/`.

    We try a sequence of candidates and return the first that
    imports. If all fail, fall back to direct file-based loading
    via importlib.util (works regardless of sys.path)."""
    candidates = [
        # Normal backend path: cwd == backend/, or sys.path includes it.
        'api.docx_rich_writer',
        # As a top-level package (when `backend/` is on sys.path and
        # importable as `api.docx_rich_writer`).
        'docx_rich_writer',
    ]
    for name in candidates:
        try:
            mod = importlib.import_module(name)
            wp = getattr(mod, 'write_paragraph', None)
            if wp is not None:
                return wp
        except Exception:
            continue
    # Direct file-based fallback: locate `docx_rich_writer.py` relative
    # to this module file (which is `backend/policy_platform/lines_json_renderer.py`).
    # `docx_rich_writer.py` lives at `backend/api/docx_rich_writer.py`.
    try:
        here = Path(__file__).resolve().parent  # backend/policy_platform/
        candidate = here.parent / 'api' / 'docx_rich_writer.py'
        if candidate.exists():
            spec = importlib.util.spec_from_file_location(
                'docx_rich_writer', str(candidate),
            )
            if spec is not None and spec.loader is not None:
                m = importlib.util.module_from_spec(spec)
                sys.modules.setdefault('docx_rich_writer', m)
                spec.loader.exec_module(m)
                wp = getattr(m, 'write_paragraph', None)
                if wp is not None:
                    return wp
    except Exception:
        pass
    return None


def _apply_publication_styling(p_elem) -> None:
    """Apply publication-time styling to a body paragraph:
      - 2.0 line height (line=480 lineRule=auto)
      - left alignment (jc=left, override scaffold's jc=both)
      - preserve brain's pStyle (Text / ListParagraph)
      - preserve brain's ind (left=2880 hanging=2880 or left=360)
      - DO NOT set space-after or space-before — only the line height
        (unless the paragraph already has pBdr — user-requested borders
        with margins are preserved)

    Does NOT touch:
      - Table cells (preserves brain's table formatting).
      - The scaffold's numPr (Roman numerals on headings) — we only
        clear numPr on empty/dead paragraphs; user content paragraphs
        inherit whatever numPr the scaffold paragraph had.

    Brain's scaffold pStyle is kept so font/theme metrics match the
    brain framework.
    """
    # Skip table cells — preserve their original formatting.
    parent = p_elem.getparent()
    if parent is not None and parent.tag.split("}")[-1] == "tc":
        return

    pPr = p_elem.find(qn("w:pPr"))
    if pPr is None:
        pPr = OxmlElement("w:pPr")
        p_elem.insert(0, pPr)

    # If the paragraph has a bottom border (user-requested), preserve
    # the before/after margins.
    has_border = pPr.find(qn("w:pBdr")) is not None

    # Spacing: only set line=480 lineRule=auto (2.0x).
    spacing = pPr.find(qn("w:spacing"))
    if spacing is None:
        spacing = OxmlElement("w:spacing")
        pPr.append(spacing)
    if not has_border:
        # Strip existing before/after unless the user explicitly added
        # a border (which implies they wanted margins too).
        if spacing.get(qn("w:after")) is not None:
            del spacing.attrib[qn("w:after")]
        if spacing.get(qn("w:before")) is not None:
            del spacing.attrib[qn("w:before")]
    spacing.set(qn("w:line"), "480")
    spacing.set(qn("w:lineRule"), "auto")

    # Left alignment (override scaffold's jc=both)
    for jc in pPr.findall(qn("w:jc")):
        pPr.remove(jc)
    jc = OxmlElement("w:jc")
    jc.set(qn("w:val"), "left")
    pPr.append(jc)


def _apply_publication_styling_in_cell(p_elem) -> None:
    """Variant of `_apply_publication_styling` for paragraphs inside
    table cells — same 2.0 line / jc=left / preserve pPr, but does NOT
    skip table cells. Used by `_apply_publication_styling_to_tables`.
    """
    pPr = p_elem.find(qn("w:pPr"))
    if pPr is None:
        pPr = OxmlElement("w:pPr")
        p_elem.insert(0, pPr)

    spacing = pPr.find(qn("w:spacing"))
    if spacing is None:
        spacing = OxmlElement("w:spacing")
        pPr.append(spacing)
    if spacing.get(qn("w:after")) is not None:
        del spacing.attrib[qn("w:after")]
    if spacing.get(qn("w:before")) is not None:
        del spacing.attrib[qn("w:before")]
    spacing.set(qn("w:line"), "480")
    spacing.set(qn("w:lineRule"), "auto")

    for jc in pPr.findall(qn("w:jc")):
        pPr.remove(jc)
    jc = OxmlElement("w:jc")
    jc.set(qn("w:val"), "left")
    pPr.append(jc)


def _apply_metadata_styling(p_elem) -> None:
    """Apply slot-1 metadata styling (single line, 10pt, Times New Roman,
    left-aligned) to a paragraph. Used for slot-1 metadata rows
    (Type:, Policy Title:, Policy Number:, etc.) which need a denser
    layout than body paragraphs.
      - 1.0 line height (line=240 lineRule=auto)
      - left alignment
      - Times New Roman 10pt applied to all runs (overrides existing
        rPr so labels and values render uniformly)
    """
    # Skip table cells — preserve their original formatting.
    parent = p_elem.getparent()
    if parent is not None and parent.tag.split("}")[-1] == "tc":
        return

    pPr = p_elem.find(qn("w:pPr"))
    if pPr is None:
        pPr = OxmlElement("w:pPr")
        p_elem.insert(0, pPr)

    # 1.0 line height (line=240). Strip before/after.
    spacing = pPr.find(qn("w:spacing"))
    if spacing is None:
        spacing = OxmlElement("w:spacing")
        pPr.append(spacing)
    if spacing.get(qn("w:after")) is not None:
        del spacing.attrib[qn("w:after")]
    if spacing.get(qn("w:before")) is not None:
        del spacing.attrib[qn("w:before")]
    spacing.set(qn("w:line"), "240")
    spacing.set(qn("w:lineRule"), "auto")

    # Left alignment
    for jc in pPr.findall(qn("w:jc")):
        pPr.remove(jc)
    jc = OxmlElement("w:jc")
    jc.set(qn("w:val"), "left")
    pPr.append(jc)

    # Times New Roman 10pt on every run.
    for r in p_elem.findall(qn("w:r")):
        _set_run_font(r, family="Times New Roman", size_hp=20)


def _apply_paragraph_border_bottom(p_elem) -> None:
    """Add a single bottom border (thin black line) to a paragraph. Used
    to underline metadata rows like 'Functional Area(s): Human Resources'
    and 'Applies to: All eligible employees' per user spec."""
    _apply_paragraph_border(p_elem, side="bottom")


def _apply_paragraph_border_top(p_elem) -> None:
    """Add a single top border (thin black line) to a paragraph. Used
    to draw a divider above the first slot-1 metadata row ('Type:')."""
    _apply_paragraph_border(p_elem, side="top")


def _apply_paragraph_border(p_elem, *, side: str = "bottom") -> None:
    """Add a single border (thin black line) to a paragraph on the given
    side ('top' or 'bottom'). Preserves any existing pBdr on the OTHER
    side (so a paragraph can have both top and bottom borders)."""
    pPr = p_elem.find(qn("w:pPr"))
    if pPr is None:
        pPr = OxmlElement("w:pPr")
        p_elem.insert(0, pPr)
    existing_pBdr = pPr.find(qn("w:pBdr"))
    if existing_pBdr is None:
        pBdr = OxmlElement("w:pBdr")
    else:
        pBdr = existing_pBdr
        # Remove existing border on this side (avoid stacking)
        for existing_side in pBdr.findall(qn(f"w:{side}")):
            pBdr.remove(existing_side)
    border = OxmlElement(f"w:{side}")
    border.set(qn("w:val"), "single")
    border.set(qn("w:sz"), "4")  # 0.5pt (4 eighths of a point)
    border.set(qn("w:space"), "1")
    border.set(qn("w:color"), "000000")
    pBdr.append(border)
    if existing_pBdr is None:
        # pBdr must be inserted BEFORE spacing per OOXML schema.
        spacing = pPr.find(qn("w:spacing"))
        if spacing is not None:
            spacing.addprevious(pBdr)
        else:
            pPr.append(pBdr)


def _apply_paragraph_margins(p_elem, top_twips: int = 100, bottom_twips: int = 100) -> None:
    """Add margin-top and margin-bottom (in twips, 100 twips ≈ 5px) to a
    paragraph. Used alongside the bottom border for metadata rows."""
    pPr = p_elem.find(qn("w:pPr"))
    if pPr is None:
        pPr = OxmlElement("w:pPr")
        p_elem.insert(0, pPr)
    spacing = pPr.find(qn("w:spacing"))
    if spacing is None:
        spacing = OxmlElement("w:spacing")
        pPr.append(spacing)
    spacing.set(qn("w:before"), str(top_twips))
    spacing.set(qn("w:after"), str(bottom_twips))


def _write_paragraph_rich(p_elem, html: str) -> None:
    """Write the user's HTML into an existing `<w:p>` element using the
    rich writer (bold/italic/colour/font/heading). Falls back to plain
    text if the rich writer is unavailable or raises.

    Publication styling (1.15 line height, jc=left) is applied via
    `_apply_publication_styling`. The brain scaffold's pStyle is
    preserved so font/theme metrics match the brain framework.
    User-provided rich styling (bold/italic/color/font-size overrides
    in <span style="...">) is honoured. Tables are not affected."""
    # Remove all children except pPr (preserves paragraph styling).
    for child in list(p_elem):
        if child.tag == qn("w:pPr"):
            continue
        p_elem.remove(child)
    # Apply publication styling: 1.15 line, jc=left. Tables are skipped
    # inside `_apply_publication_styling`.
    _apply_publication_styling(p_elem)
    wp = _import_rich_writer()
    if wp is not None:
        try:
            wp(p_elem, html or "")
            return
        except Exception:
            pass
    # Fallback: plain text run preserving the first run's rPr if present.
    text = _strip_html(html or "")
    new_r = OxmlElement("w:r")
    first_r = p_elem.find(qn("w:r"))
    if first_r is not None:
        first_rpr = first_r.find(qn("w:rPr"))
        if first_rpr is not None:
            new_r.append(copy.deepcopy(first_rpr))
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    new_r.append(t)
    p_elem.append(new_r)


def _bold_paragraph_runs(p_elem) -> None:
    """Make every run in `p_elem` bold. Adds `<w:b/>` to each run's
    `<w:rPr>` (creating one if missing). Also sets `<w:bCs/>` for
    complex script consistency. Idempotent."""
    for r in p_elem.iter(qn("w:r")):
        rPr = r.find(qn("w:rPr"))
        if rPr is None:
            rPr = OxmlElement("w:rPr")
            r.insert(0, rPr)
        if rPr.find(qn("w:b")) is None:
            b = OxmlElement("w:b")
            rPr.insert(0, b)
        if rPr.find(qn("w:bCs")) is None:
            bCs = OxmlElement("w:bCs")
            rPr.insert(0, bCs)


# Slot 1 metadata field labels that should render as bold (the label
# part, not the value). The user's value after the colon stays as-is.
_SLOT1_BOLD_LABELS = (
    "Type:",
    "Policy Title:",
    "Policy Number:",
    "Applicable Sector",
    "Functional Area",
    "Brief Description:",
    "Effective Date",
    "Approved by:",
    "Prepared by:",
    "Responsible Function",
    "Supersedes:",
    "Last Reviewed",
    "Applies to:",
    "Reason for Policy:",
)


def _apply_label_bold(p_elem, user_text: str) -> None:
    """Make the label part of a slot-1 metadata line bold.

    For a line like "Type: HR Policy", the label "Type:" should be bold
    while "HR Policy" stays normal. We achieve this by splitting the
    first run into two: a bold "Type:" run + a normal "HR Policy" run.

    Also: if the user's text starts with a slot heading label
    (e.g. "INTRODUCTION"), the WHOLE paragraph is bolded.
    """
    t = (user_text or '').strip()
    if not t:
        return

    # Slot heading: bold the whole paragraph
    upper_t = t.upper()
    for heading in _SLOT_HEADING_BOLD_LABELS:
        if upper_t == heading or upper_t.startswith(heading + ' ') or upper_t.startswith(heading + ':'):
            _bold_paragraph_runs(p_elem)
            return

    # Slot-1 label: bold only the label, keep value as-is
    label = None
    for candidate in _SLOT1_BOLD_LABELS:
        if t.lower().startswith(candidate.lower()):
            label = candidate
            break
    if not label:
        return

    # Find the first run with text and split it into (label, value)
    for r in p_elem.findall(qn("w:r")):
        t_elem = r.find(qn("w:t"))
        if t_elem is None or not t_elem.text:
            continue
        run_text = t_elem.text
        if not run_text.lower().startswith(label.lower()):
            continue
        # Extract label portion (preserve original casing)
        # Find the longest case-insensitive match
        match_len = 0
        for candidate in _SLOT1_BOLD_LABELS:
            if run_text.lower().startswith(candidate.lower()):
                if len(candidate) > match_len:
                    match_len = len(candidate)
        if match_len == 0:
            continue
        label_text = run_text[:match_len]
        value_text = run_text[match_len:]

        # Split the run: clone with bold for label, keep original for value
        rPr_template = r.find(qn("w:rPr"))
        # Modify the existing run to hold only the value (non-bold)
        t_elem.text = value_text
        # Ensure no bold on the value run
        if rPr_template is not None:
            for b in rPr_template.findall(qn("w:b")):
                rPr_template.remove(b)
            for bCs in rPr_template.findall(qn("w:bCs")):
                rPr_template.remove(bCs)

        # Build a new run for the label (bold)
        new_r = OxmlElement("w:r")
        new_rPr = OxmlElement("w:rPr")
        if rPr_template is not None:
            for child in rPr_template:
                new_rPr.append(copy.deepcopy(child))
        b = OxmlElement("w:b")
        new_rPr.insert(0, b)
        bCs = OxmlElement("w:bCs")
        new_rPr.insert(0, bCs)
        new_r.append(new_rPr)
        new_t = OxmlElement("w:t")
        new_t.set(qn("xml:space"), "preserve")
        new_t.text = label_text
        new_r.append(new_t)

        # Insert the new bold run BEFORE the value run
        r.addprevious(new_r)
        break

# Slot heading labels that should render as bold (the heading itself,
# not the body content). When the user's content starts with one of
# these labels, the WHOLE paragraph is bolded.
_SLOT_HEADING_BOLD_LABELS = (
    "INTRODUCTION",
    "POLICY STATEMENT",
    "DEFINITIONS",
    "HISTORY",
    "RELATED POLICIES",
)


def _split_label_and_value(text: str) -> tuple[str, str]:
    """Split a slot-1 line into (label, value). The label is the part
    up to (and including) the first ':' or '('; the value is the rest.
    Returns (text, '') if no separator is found."""
    t = text.strip()
    for sep in (':', '\t'):
        idx = t.find(sep)
        if idx >= 0:
            return t[: idx + 1].strip(), t[idx + 1 :].strip()
    return t, ''


# (No divider insertion. The brain template does not have dividers, so we
# do not insert any.)


def _strip_html(html: str) -> str:
    """Best-effort plain text fallback. Strips tags and decodes entities."""
    import re
    text = re.sub(r"<[^>]+>", "", html or "")
    return (text
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&apos;", "'")
            .replace("&nbsp;", " "))


# ---------------------------------------------------------------------------
# Per-slot writers
# ---------------------------------------------------------------------------

def _get_slot_body_paragraphs_and_tables(doc, sec_id: int) -> tuple[list, list, object]:
    """Return (para_items, table_items, heading_elem) for `sec_id` using the
    frozen `BRAIN_SLOT_RANGES` index map. Falls back to `find_slot_boundaries`
    if the indexed lookup is invalid (defensive)."""
    body = doc.element.body
    children = list(body)
    info = BRAIN_SLOT_RANGES.get(sec_id)
    if info and info.get("body_items"):
        body_items = info["body_items"]
        heading_idx = body_items[0]
        if heading_idx < len(children):
            heading_elem = children[heading_idx]
            if heading_elem.tag.split("}")[-1] == "p":
                para_items = []
                table_items = []
                for i in body_items[1:]:
                    if i >= len(children):
                        continue
                    e = children[i]
                    tag = e.tag.split("}")[-1]
                    if tag == "p":
                        para_items.append(e)
                    elif tag == "tbl":
                        table_items.append(e)
                return para_items, table_items, heading_elem
    # Fallback: use find_slot_boundaries (heading + subsequent siblings).
    bounds = find_slot_boundaries(doc)
    sec_info = bounds.get(sec_id, {})
    elements = sec_info.get("elements", [])
    if not elements:
        return [], [], None
    heading_elem = elements[0]
    para_items = []
    table_items = []
    for e in elements[1:]:
        tag = e.tag.split("}")[-1]
        if tag == "p":
            para_items.append(e)
        elif tag == "tbl":
            table_items.append(e)
    return para_items, table_items, heading_elem


def _cell_to_plain(cell: Any) -> str:
    """Extract a plain-text value from a table cell.

    Cell shape: either `{text, html}` dict (rich) or `str` (legacy).
    """
    if isinstance(cell, dict):
        return cell.get("text") or _strip_html(cell.get("html") or "")
    if cell is None:
        return ""
    return str(cell)


def _cell_to_html(cell: Any) -> str:
    """Extract an HTML value from a table cell (for rich cell writes)."""
    if isinstance(cell, dict):
        return cell.get("html") or cell.get("text") or ""
    if cell is None:
        return ""
    return str(cell)


def _write_table(table_elem, rows: list[list[Any]], preserve_dimensions: bool = False) -> int:
    """Replace the contents of `table_elem` with the user's rows.

    Mirrors the brain renderer's behaviour: row count = source row count;
    column count = max source column count (each row padded with empty
    cells). Column widths = equal split of the existing total table
    width (so the page layout doesn't shift).

    `preserve_dimensions` (default False): when True, the target row/col
    counts come from the table's current rows/cols (the brain's scaffold);
    otherwise they grow to fit the source. For the history / award tables
    (slots 10, 14) we typically preserve so the brain template's grid
    widths stay stable.
    """
    MIN_COL_DXA = 1500
    if not rows:
        return 0

    # Strip all existing <w:tr>.
    for tr in table_elem.findall(qn("w:tr")):
        table_elem.remove(tr)

    actual_cols = max((len(r) for r in rows), default=1)

    # Existing grid widths → redistribute equally.
    grid_cols_elem = table_elem.find(qn("w:tblGrid"))
    total_width = 0
    if grid_cols_elem is not None:
        for gc in grid_cols_elem.findall(qn("w:gridCol")):
            try:
                w = gc.get(qn("w:w"))
                if w is not None and w != "":
                    total_width += int(w)
            except (TypeError, ValueError):
                pass
    if total_width == 0:
        total_width = 5000 * actual_cols
    col_w = max(total_width // actual_cols, MIN_COL_DXA)

    if grid_cols_elem is not None:
        for gc in grid_cols_elem.findall(qn("w:gridCol")):
            grid_cols_elem.remove(gc)
        for _ in range(actual_cols):
            gc = OxmlElement("w:gridCol")
            gc.set(qn("w:w"), str(col_w))
            grid_cols_elem.append(gc)

    # Emit exactly len(rows) rows.
    for row in rows:
        tr = OxmlElement("w:tr")
        for ci in range(actual_cols):
            tc = OxmlElement("w:tc")
            tcPr = OxmlElement("w:tcPr")
            tcW = OxmlElement("w:tcW")
            tcW.set(qn("w:w"), str(col_w))
            tcW.set(qn("w:type"), "dxa")
            tcPr.append(tcW)
            tc.append(tcPr)
            tr.append(tc)
            new_p = OxmlElement("w:p")
            # Brain scaffold's table-cell pPr (if any) was attached by
            # python-docx; we preserve it as-is. No publication overrides.
            cell = row[ci] if ci < len(row) else None
            html = _cell_to_html(cell)
            plain = _cell_to_plain(cell)
            wp = _import_rich_writer()
            wrote = False
            if wp is not None:
                try:
                    wp(new_p, html or plain)
                    wrote = True
                except Exception:
                    wrote = False
            if not wrote or not list(new_p.findall(qn("w:r"))):
                # Fallback plain run.
                new_r = OxmlElement("w:r")
                t = OxmlElement("w:t")
                t.set(qn("xml:space"), "preserve")
                t.text = plain
                new_r.append(t)
                new_p.append(new_r)
            tc.append(new_p)
        table_elem.append(tr)
    return len(rows)


def _make_new_paragraph(template_pPr, text_or_html: str, is_html: bool) -> object:
    """Build a new `<w:p>` element. When `is_html`, write rich; else plain.

    Publication styling is applied (1.15 line, jc=left). Brain scaffold
    pStyle is kept; scaffold's jc/numPr/ind overrides are removed.
    """
    new_p = OxmlElement("w:p")
    if template_pPr is not None:
        new_p.append(copy.deepcopy(template_pPr))
    # Apply publication styling (1.15 line, jc=left). Tables are skipped.
    _apply_publication_styling(new_p)
    if is_html:
        wp = _import_rich_writer()
        if wp is not None:
            try:
                wp(new_p, text_or_html)
                if list(new_p.findall(qn("w:r"))):
                    return new_p
            except Exception:
                pass
    new_r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text_or_html if not is_html else _strip_html(text_or_html)
    new_r.append(t)
    new_p.append(new_r)
    return new_p


# ---------------------------------------------------------------------------
# Slot renderer
# ---------------------------------------------------------------------------


def _slot_heading_label(sec_id: int) -> Optional[str]:
    """Return the canonical heading text for `sec_id` (from
    `SLOT_HEADINGS`). Used for dedup detection: when the user's first
    paragraph matches the heading label, we skip the scaffold's heading
    paragraph to avoid duplicate headings."""
    return SLOT_HEADINGS.get(sec_id)

def _render_slot_direct(doc, sec_id: int,
                        paragraphs: list[dict],
                        tables: list[dict]) -> None:
    """Write `paragraphs` and `tables` into `sec_id`'s body region.

    Architecture (matches the reference codebase in
    `Agentic-Policy-Generator-…/backend/policy_platform/renderer.py`):

      * `heading_elem` is the slot's heading paragraph — kept separate
        from the body loop. User content goes into the body slots
        (`para_items`), never into the heading.
      * For each scaffold body paragraph in order, write the user's
        paragraph HTML into it (overwriting the brain default text).
      * Any extra user paragraphs (more than the scaffold has) are
        appended as new `<w:p>` elements AFTER the last scaffold body
        paragraph.
      * Empty user paragraphs are skipped (we don't inject blank lines).
      * **Empty leftover scaffold paragraphs are DELETED entirely**
        from the body — not just stripped of runs — so they don't
        render as visible blank lines or empty bullet points in Word.
        This matches the user's directive ("don't give back bullet
        points with empty line").
      * Brain scaffold structure preserved verbatim: pStyle, jc, ind,
        numPr (Roman numerals), spacing, font/theme all stay intact.
      * Heading dedup: when the user's first paragraph starts with the
        slot's heading label (e.g. "INTRODUCTION"), the scaffold
        heading is cleared so the user's text fills the heading slot
        cleanly (no duplicate "INTRODUCTION / INTRODUCTION").
      * Tables: if the slot has scaffold tables, replace their contents
        with the user's rows; if the user has no tables but the
        scaffold has tables (e.g. slot 10 award tiers, slot 14 history),
        the scaffold tables are removed entirely.
    """
    para_items, table_items, heading_elem = _get_slot_body_paragraphs_and_tables(doc, sec_id)
    if heading_elem is None:
        return

    # Filter empty/whitespace paragraphs from user input.
    non_empty = [p for p in paragraphs
                 if (p.get('text') or '').strip() or (p.get('html') or '').strip()]

    # Slot-1 metadata slots (1, 2, 3, 4, 11) get label-bold for every
    # body paragraph the user provides. Slot-5/6/12/14 headings also get
    # bold for the whole paragraph.
    _BOLD_LABEL_SLOTS = {1, 2, 3, 4, 11}
    _BOLD_HEADING_SLOTS = {5, 6, 12, 14}

    # Body counts (declared up front so the if/elif/else below can
    # reference them).
    body_paras = non_empty
    body_scaffold_count = len(para_items)
    body_user_count = len(body_paras)

    # Heading dedup: if the user's first paragraph's text starts with
    # the slot's heading label, drop the scaffold heading so we don't
    # render the label twice.
    heading_label = _slot_heading_label(sec_id)
    skip_scaffold_heading = False
    if heading_label and non_empty:
        first_user_text = (
            (non_empty[0].get('text') or '').strip()
            or _strip_html(non_empty[0].get('html') or '')
        ).lower()
        if first_user_text.startswith(heading_label.lower()):
            skip_scaffold_heading = True

    if skip_scaffold_heading:
        # Clear scaffold heading runs/sdt but keep pPr (which carries
        # the brain's numPr / pStyle / jc / ind — all preserved so the
        # Roman numeral "I." / "II." etc. continues to render).
        for child in list(heading_elem):
            if child.tag == qn("w:pPr"):
                continue
            heading_elem.remove(child)
        # Write the user's first paragraph INTO the heading element
        # (so it inherits the scaffold's numPr → renders as
        # "I. INTRODUCTION" with the bold font).
        first_entry = non_empty[0]
        html = first_entry.get('html') or first_entry.get('text') or ''
        _write_paragraph_rich(heading_elem, html)
        if sec_id in _BOLD_HEADING_SLOTS:
            _bold_paragraph_runs(heading_elem)
        if sec_id in _BOLD_LABEL_SLOTS:
            # Slot-1 metadata: split first run into (bold label,
            # normal value) so e.g. "Type:" renders bold and "HR Policy"
            # renders normal.
            _apply_label_bold(heading_elem, first_entry.get('text') or '')
        # Consume the first user paragraph; the body loop processes
        # remaining user paragraphs into para_items.
        body_paras = non_empty[1:]
        body_user_count = len(body_paras)
    elif not non_empty and not tables:
        # No user content for this slot at all. DELETE the heading
        # (scaffold label-row or scaffold heading). The body loop
        # below will delete every body paragraph in turn. After
        # deletion, the slot contributes zero visible paragraphs.
        # This is essential so empty label-rows like "Type:Policy"
        # / "City Family High School Completion Award" never leak
        # into the output.
        parent = heading_elem.getparent()
        if parent is not None:
            parent.remove(heading_elem)
    else:
        # Keep scaffold heading. Apply publication styling (1.15 line,
        # jc=left) so it matches the rest of the document. Bold the
        # whole paragraph if it's a slot heading (matches the Brain's
        # bolded section titles).
        _apply_publication_styling(heading_elem)
        if sec_id in _BOLD_HEADING_SLOTS:
            _bold_paragraph_runs(heading_elem)
        # Multi-label-row slot handling: when body_scaffold_count > 0
        # AND user has content, the heading is a label-row that
        # contains brain example text. Overwrite it with the user's
        # first paragraph; the body loop will overwrite body[0] with
        # user's second paragraph. For slot 1 specifically (label-row
        # slot with body_items = [2,3,4,5,6,7] — heading is "Type:Policy"),
        # this keeps the structure: body[0]="Policy Title:..." gets
        # overwritten with user's 2nd paragraph, etc.
        if body_scaffold_count > 0 and len(non_empty) >= 1:
            first_entry = non_empty[0]
            html = first_entry.get('html') or first_entry.get('text') or ''
            _write_paragraph_rich(heading_elem, html)
            if sec_id in _BOLD_LABEL_SLOTS:
                _apply_label_bold(heading_elem, first_entry.get('text') or '')
            # Consume the first user paragraph; the body loop will
            # process the remaining user paragraphs into para_items.
            body_paras = non_empty[1:]
            body_user_count = len(body_paras)

    # Body loop: write user content into scaffold body paragraphs in
    # order. Leftover scaffold paragraphs (when user has fewer than the
    # scaffold) are DELETED entirely so they don't leak brain placeholder
    # text into the output (e.g. "Effective Date/Period:[13 Feb 2023]"
    # when user provided a different date).

    # Track which scaffold body paragraphs were OVERWRITTEN with user
    # content. We skip them when scanning for deletions below.
    overwritten_indices = set()

    for i, p_elem in enumerate(para_items):
        if p_elem.getparent() is None:
            continue
        if i < body_user_count:
            entry = body_paras[i]
            html = entry.get('html') or entry.get('text') or ''
            _write_paragraph_rich(p_elem, html)
            # Bold the label portion for slot-1 metadata fields.
            # (Heading paragraphs are handled above; slot body paragraphs
            # are not bolded so values remain normal weight.)
            if sec_id in _BOLD_LABEL_SLOTS:
                _apply_label_bold(p_elem, entry.get('text') or '')
            overwritten_indices.add(id(p_elem))
        else:
            # Leftover scaffold paragraph (no user content for this slot).
            # ALWAYS DELETE it (regardless of whether user provided
            # content for this slot or any other slot). The brain's
            # example text ("Effective Date/Period:[13 Feb 2023]",
            # "Approved by:[Daw Win Win Tint, Group CEO]", bullet items,
            # "300,000 MMK", "Si Thu Maung", etc.) never appears in
            # the published output. The user's directive: only the
            # Result output (their content) is rendered.
            parent = p_elem.getparent()
            if parent is not None:
                parent.remove(p_elem)
            # Update the parent's children cache isn't needed since we
            # re-fetch para_items each iteration through `enumerate`.

    # Append extras: if user has more paragraphs than the scaffold body
    # supports, append them after the last scaffold body paragraph.
    # For label-row slots (2, 4, 11) where the scaffold "heading" IS the
    # label-row paragraph (no body items), the FIRST user paragraph is
    # written into heading_elem (replacing the scaffold label-row) and
    # the remaining paragraphs are appended after it.
    if body_user_count > body_scaffold_count:
        if para_items:
            # Find the LAST non-removed scaffold body paragraph as anchor.
            anchor = None
            for p_elem in reversed(para_items):
                if p_elem.getparent() is not None:
                    anchor = p_elem
                    break
            if anchor is None:
                anchor = heading_elem
            parent = anchor.getparent()
            if parent is not None:
                pPr_template = anchor.find(qn("w:pPr"))
                try:
                    insert_pos = list(parent).index(anchor) + 1
                except ValueError:
                    insert_pos = len(list(parent))
                for entry in body_paras[body_scaffold_count:]:
                    html = entry.get('html') or entry.get('text') or ''
                    new_p = _make_new_paragraph(pPr_template, html, is_html=True)
                    parent.insert(insert_pos, new_p)
                    insert_pos += 1
        else:
            # No scaffold body paragraphs (label-row slot like 2/4/11).
            # Write the FIRST user paragraph into heading_elem (replacing
            # the scaffold label-row). Append remaining paragraphs.
            first_entry = body_paras[0]
            html = first_entry.get('html') or first_entry.get('text') or ''
            _write_paragraph_rich(heading_elem, html)
            if sec_id in _BOLD_LABEL_SLOTS:
                _apply_label_bold(heading_elem, first_entry.get('text') or '')
            parent = heading_elem.getparent()
            if parent is not None:
                pPr_template = heading_elem.find(qn("w:pPr"))
                try:
                    insert_pos = list(parent).index(heading_elem) + 1
                except ValueError:
                    insert_pos = len(list(parent))
                for entry in body_paras[1:]:
                    html = entry.get('html') or entry.get('text') or ''
                    new_p = _make_new_paragraph(pPr_template, html, is_html=True)
                    parent.insert(insert_pos, new_p)
                    insert_pos += 1

    # 3) Tables: replace scaffold tables; append extras after.
    if tables and table_items:
        # First table → first scaffold table.
        first_table = tables[0]
        first_rows = first_table.get('rows') or []
        _write_table(table_items[0], first_rows)
        # Extra user tables → append after the first scaffold table.
        if len(tables) > 1 and table_items:
            anchor = table_items[0]
            parent = anchor.getparent()
            if parent is not None:
                try:
                    insert_pos = list(parent).index(anchor) + 1
                except ValueError:
                    insert_pos = len(list(parent))
                pPr_template = anchor.find(qn("w:pPr"))
                for extra in tables[1:]:
                    extra_rows = extra.get('rows') or []
                    if not extra_rows:
                        continue
                    # Build a fresh <w:tbl> element using the anchor's
                    # tblPr/tblGrid as the template (so it picks up the
                    # brain's table styling).
                    new_tbl = OxmlElement("w:tbl")
                    # Copy tblPr if present.
                    src_tblPr = anchor.find(qn("w:tblPr"))
                    if src_tblPr is not None:
                        new_tbl.append(copy.deepcopy(src_tblPr))
                    # Build grid + rows.
                    actual_cols = max((len(r) for r in extra_rows), default=1)
                    new_grid = OxmlElement("w:tblGrid")
                    MIN_COL_DXA = 1500
                    # Borrow total width from the anchor's grid.
                    total_width = 0
                    src_grid = anchor.find(qn("w:tblGrid"))
                    if src_grid is not None:
                        for gc in src_grid.findall(qn("w:gridCol")):
                            try:
                                w = gc.get(qn("w:w"))
                                if w is not None and w != "":
                                    total_width += int(w)
                            except (TypeError, ValueError):
                                pass
                    if total_width == 0:
                        total_width = 5000 * actual_cols
                    col_w = max(total_width // actual_cols, MIN_COL_DXA)
                    for _ in range(actual_cols):
                        gc = OxmlElement("w:gridCol")
                        gc.set(qn("w:w"), str(col_w))
                        new_grid.append(gc)
                    new_tbl.append(new_grid)
                    for row in extra_rows:
                        tr = OxmlElement("w:tr")
                        for ci in range(actual_cols):
                            tc = OxmlElement("w:tc")
                            tcPr = OxmlElement("w:tcPr")
                            tcW = OxmlElement("w:tcW")
                            tcW.set(qn("w:w"), str(col_w))
                            tcW.set(qn("w:type"), "dxa")
                            tcPr.append(tcW)
                            tc.append(tcPr)
                            new_p = OxmlElement("w:p")
                            cell = row[ci] if ci < len(row) else None
                            wp = _import_rich_writer()
                            wrote = False
                            if wp is not None:
                                try:
                                    wp(new_p, _cell_to_html(cell) or _cell_to_plain(cell))
                                    wrote = True
                                except Exception:
                                    wrote = False
                            if not wrote or not list(new_p.findall(qn("w:r"))):
                                new_r = OxmlElement("w:r")
                                t = OxmlElement("w:t")
                                t.set(qn("xml:space"), "preserve")
                                t.text = _cell_to_plain(cell)
                                new_r.append(t)
                                new_p.append(new_r)
                            if not list(new_p.findall(qn("w:r"))):
                                new_r = OxmlElement("w:r")
                                t = OxmlElement("w:t")
                                t.set(qn("xml:space"), "preserve")
                                t.text = _cell_to_plain(cell)
                                new_r.append(t)
                                new_p.append(new_r)
                            tc.append(new_p)
                            tr.append(tc)
                        new_tbl.append(tr)
                    parent.insert(insert_pos, new_tbl)
                    insert_pos += 1
                    # Spacer paragraph between tables.
                    spacer = OxmlElement("w:p")
                    parent.insert(insert_pos, spacer)
                    insert_pos += 1
    elif tables and not table_items:
        # Slot has no scaffold table (e.g. a prose slot where the user
        # added a table). Insert the user's tables after the heading /
        # after the last paragraph.
        anchor = None
        if para_items:
            anchor = para_items[-1]
        else:
            anchor = heading_elem
        parent = anchor.getparent()
        if parent is not None:
            try:
                insert_pos = list(parent).index(anchor) + 1
            except ValueError:
                insert_pos = len(list(parent))
            for t in tables:
                rows = t.get('rows') or []
                if not rows:
                    continue
                actual_cols = max((len(r) for r in rows), default=1)
                new_tbl = OxmlElement("w:tbl")
                tblPr = OxmlElement("w:tblPr")
                tblW = OxmlElement("w:tblW")
                tblW.set(qn("w:w"), "5000")
                tblW.set(qn("w:type"), "pct")
                tblPr.append(tblW)
                tblBorders = OxmlElement("w:tblBorders")
                for bn in ("top", "left", "bottom", "right", "insideH", "insideV"):
                    b = OxmlElement(f"w:{bn}")
                    b.set(qn("w:val"), "single")
                    b.set(qn("w:sz"), "4")
                    b.set(qn("w:color"), "auto")
                    tblBorders.append(b)
                tblPr.append(tblBorders)
                new_tbl.append(tblPr)
                new_grid = OxmlElement("w:tblGrid")
                for _ in range(actual_cols):
                    gc = OxmlElement("w:gridCol")
                    gc.set(qn("w:w"), str(int(9000 / actual_cols)))
                    new_grid.append(gc)
                new_tbl.append(new_grid)
                for row in rows:
                    tr = OxmlElement("w:tr")
                    for ci in range(actual_cols):
                        tc = OxmlElement("w:tc")
                        tcPr = OxmlElement("w:tcPr")
                        tcW = OxmlElement("w:tcW")
                        tcW.set(qn("w:w"), str(int(9000 / actual_cols)))
                        tcW.set(qn("w:type"), "dxa")
                        tcPr.append(tcW)
                        tc.append(tcPr)
                        new_p = OxmlElement("w:p")
                        cell = row[ci] if ci < len(row) else None
                        wp = _import_rich_writer()
                        wrote = False
                        if wp is not None:
                            try:
                                wp(new_p, _cell_to_html(cell) or _cell_to_plain(cell))
                                wrote = True
                            except Exception:
                                wrote = False
                        if not wrote or not list(new_p.findall(qn("w:r"))):
                            new_r = OxmlElement("w:r")
                            t = OxmlElement("w:t")
                            t.set(qn("xml:space"), "preserve")
                            t.text = _cell_to_plain(cell)
                            new_r.append(t)
                            new_p.append(new_r)
                        if not list(new_p.findall(qn("w:r"))):
                            new_r = OxmlElement("w:r")
                            t = OxmlElement("w:t")
                            t.set(qn("xml:space"), "preserve")
                            t.text = _cell_to_plain(cell)
                            new_r.append(t)
                            new_p.append(new_r)
                        tc.append(new_p)
                        tr.append(tc)
                    new_tbl.append(tr)
                parent.insert(insert_pos, new_tbl)
                insert_pos += 1
                spacer = OxmlElement("w:p")
                parent.insert(insert_pos, spacer)
                insert_pos += 1
    elif not tables and table_items:
        # User has no tables for this slot, but the scaffold has tables
        # (e.g. slot 10 "Award Structure & Payout Tiers" or slot 14
        # "HISTORY"). Remove the scaffold tables entirely so the brain's
        # example data (e.g. "300,000 MMK", "Pass with distinction",
        # "Si Thu Maung") does NOT leak into the published output
        # alongside the user's Result.
        for tbl in table_items:
            parent = tbl.getparent()
            if parent is not None:
                parent.remove(tbl)

    # Slot-1 metadata post-processing is now handled by a global post-pass
    # in `render_lines_json_to_brain` (see
    # `_apply_slot1_metadata_styling_post_pass`) after
    # `_apply_publication_styling_to_body` runs, so the slot-1
    # 1.0 line-height (line=240) isn't overwritten by the body's 1.5
    # line-height pass. The post-pass scans the full body and matches
    # paragraphs by their text prefix.


def _render_free_paragraph_zone(doc, free_paragraphs: list[dict]) -> None:
    """Render slot=0 (free) paragraphs into a free-paragraph zone at the
    top of the body — AFTER the header but BEFORE slot 1.

    This is the graceful-degradation path: when the user has paragraphs
    with no slot assigned (or whose slot is unknown), we still emit them
    so the user doesn't lose data. They appear at the top of the body
    so the rest of the brain scaffold is preserved."""
    if not free_paragraphs:
        return
    body = doc.element.body
    children = list(body)
    # Find slot 1's heading; insert before it.
    insert_pos = 0
    for i, ch in enumerate(children):
        if not ch.tag.endswith("}p"):
            continue
        text = "".join((t.text or "") for t in ch.iter(qn("w:t"))).strip()
        if text == "Type" or text.startswith("Type:"):
            insert_pos = i
            break
        # Fallback: insert at index 0 if no slot-1 heading found.
        if i == len(children) - 1:
            insert_pos = 0
    # Take a heading paragraph as the template for new paragraphs' pPr.
    template_pPr = None
    for ch in children:
        if ch.tag.endswith("}p"):
            template_pPr = ch.find(qn("w:pPr"))
            if template_pPr is not None:
                break
    # Insert a zone-label heading so reviewers can see this is "free".
    zone_label = OxmlElement("w:p")
    if template_pPr is not None:
        zone_label.append(copy.deepcopy(template_pPr))
    label_r = OxmlElement("w:r")
    label_t = OxmlElement("w:t")
    label_t.set(qn("xml:space"), "preserve")
    label_t.text = "Free Paragraphs"
    label_r.append(label_t)
    zone_label.append(label_r)
    body.insert(insert_pos, zone_label)
    insert_pos += 1
    # Insert the free paragraphs.
    for entry in free_paragraphs:
        text = entry.get('text') or ''
        html = entry.get('html') or text or ''
        if not text.strip() and not html.strip():
            continue
        new_p = _make_new_paragraph(template_pPr, html, is_html=True)
        body.insert(insert_pos, new_p)
        insert_pos += 1


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def render_lines_json_to_brain(
    lines_json: Iterable,
    brain_path: Path,
    output_path: Path,
    *,
    header_text: Optional[str] = None,
    header_version: Optional[str] = None,
) -> Path:
    """Render the reviewer's saved `lines_json` directly into a copy of
    the brain template at `output_path`.

    The brain template's structure (15 slots, titles, ordering, header,
    footer, logo, media) is preserved. The body of each slot 1..14 is
    replaced with the reviewer's saved content for that slot. Slot 0
    content (free paragraphs / orphaned additions) is rendered into a
    free-paragraph zone at the top of the body.

    Returns the `output_path` on success. Raises on any unrecoverable
    failure (caller should fall back to the legacy renderer).

    This function is the Stage 2 / Stage 3 / Stage 4 fix — see the
    module docstring.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1) Copy the brain template → output (frozen scaffold).
    shutil.copy2(brain_path, output_path)
    _verify_media_against(brain_path, output_path)

    doc = Document(str(output_path))

    # 2) Normalise the reviewer's saved lines_json into per-slot buckets.
    paragraphs_by_slot, tables_by_slot = _normalise_lines_json(lines_json)

    # 3) Walk slots in REVERSE order (so insertions don't shift indices
    # of earlier slots — mirrors the legacy renderer's behaviour).
    for sec_id in sorted(_SLOT_IDS_TO_RENDER, reverse=True):
        paras = paragraphs_by_slot.get(sec_id, [])
        tbls = tables_by_slot.get(sec_id, [])
        try:
            _render_slot_direct(doc, sec_id, paras, tbls)
        except Exception as e:
            print(f'[lines_json_renderer] slot {sec_id} failed: {e}', flush=True)
            raise

    # 4) Slot 0 (free paragraphs) → free-paragraph zone at top of body.
    free_paragraphs = paragraphs_by_slot.get(0, [])
    try:
        _render_free_paragraph_zone(doc, free_paragraphs)
    except Exception as e:
        print(f'[lines_json_renderer] free-paragraph zone failed: {e}', flush=True)
        # Non-fatal — the rest of the body is still valid.

    # 5) Optional header text override (mirrors the legacy renderer's
    # `_replace_header_text`). This only writes the bracket text inside
    # the existing header paragraph — it never modifies the logo,
    # connector line, or any non-text element.
    if header_text is not None or header_version is not None:
        try:
            from .renderer import _replace_header_text as _rht
            _rht(doc, header_text or "", header_version or "")
        except Exception as e:
            print(f'[lines_json_renderer] header override failed: {e}', flush=True)

    # 5.5) Strip ALL remaining empty <w:p> elements in the document body.
    # The brain template has structural empty paragraphs between slots
    # that serve as visual separators. Once content is filled in, these
    # create unwanted blank lines between sections. We delete every
    # empty paragraph that has no text and isn't inside a table.
    _strip_empty_body_paragraphs(doc)

    # 5.6) Apply publication styling (2.0 line, jc=left, Times New Roman
    # 10pt) to every remaining body paragraph. Tables are skipped.
    _apply_publication_styling_to_body(doc)

    # 5.7) Apply publication styling to TABLE CELLS (2.0 line, jc=left,
    # Times New Roman 10pt). The brain's table borders/widths are
    # preserved — only paragraph-level styling inside cells is updated.
    _apply_publication_styling_to_tables(doc)

    # 5.8) Slot-1 metadata post-pass (1.0 line height, Times New Roman
    # 10pt, jc=left; bottom border + 5px margins for 'Functional Area(s)'
    # and 'Applies to'). Runs AFTER the body pass so the slot-1 line
    # height isn't overwritten. Scans the full body (including extra
    # paragraphs the user provided beyond the brain scaffold).
    _apply_slot1_metadata_styling_post_pass(doc)

    # 5.9) Strip faded gray table borders (BFBFBF) from the brain's
    # table styles. These render as light gray lines (the "old line"
    # the user sees). Only affects the visual appearance — the brain
    # table cell layout is preserved.
    _strip_faded_table_borders(doc)

    doc.save(str(output_path))
    _verify_media_against(brain_path, output_path)
    _restore_media_store_compression(output_path)
    return output_path


def _strip_empty_body_paragraphs(doc) -> None:
    """Delete every empty `<w:p>` element in the document body that has
    no text content and isn't inside a table cell. Brain-template
    structural scaffolding paragraphs (used as visual separators between
    slots) are removed so the output has no blank lines between
    content paragraphs.

    Also deletes paragraphs whose runs contain ONLY `<w:br/>` (soft
    line breaks) and no `<w:t>` text — these render as blank lines
    in Word and add unwanted vertical space between content paragraphs.
    """
    body = doc.element.body
    for p in list(body.findall(qn("w:p"))):
        # Skip if inside a table cell.
        parent = p.getparent()
        if parent is not None and parent.tag.split("}")[-1] == "tc":
            continue
        text = "".join(t.text or "" for t in p.iter(qn("w:t")))
        if text.strip():
            continue
        # Skip if there's any other content (hyperlinks, images).
        if p.findall(qn("w:hyperlink")) or p.findall(qn("w:drawing")):
            continue
        # If the paragraph contains ONLY <w:br/> runs (no <w:t> text),
        # delete it — these are stray soft breaks that render as blank
        # lines between content paragraphs. Defensive: ensure no run has
        # actual text (already covered above), and no images/hyperlinks.
        runs = p.findall(qn("w:r"))
        if runs:
            # Check: does any run contain anything other than <w:br/>?
            only_breaks = True
            for r in runs:
                children = [c for c in r if not c.tag.endswith("}rPr")]
                for c in children:
                    if not c.tag.endswith("}br"):
                        only_breaks = False
                        break
                if not only_breaks:
                    break
            if only_breaks:
                body.remove(p)
            continue
        # Empty structural paragraph → delete.
        body.remove(p)


def _apply_publication_styling_to_body(doc) -> None:
    """Walk every paragraph in the document body (excluding tables and
    table cells) and apply publication styling:
      - 2.0 line height (line=480 lineRule=auto)
      - left alignment (jc=left)
      - Times New Roman 10pt on ALL runs (uniform font/size)

    Bold/italic/underline/strikethrough/color are preserved on runs
    that have explicit rPr — we only override the font family and
    size, not the rest of the rPr.
    Tables (and table cells) are skipped — they keep brain's formatting.
    """
    body = doc.element.body
    for p in body.findall(qn("w:p")):
        parent = p.getparent()
        if parent is not None and parent.tag.split("}")[-1] == "tc":
            continue
        # Skip paragraphs inside tables (defensive — same check as above).
        if _has_table_ancestor(p):
            continue
        _apply_publication_styling(p)
        # Apply Times New Roman 10pt to ALL runs (uniform font/size).
        # Bold/italic/underline/color preserved via rPr — only font and
        # size are overridden.
        for r in p.findall(qn("w:r")):
            _set_run_font(r, family="Times New Roman", size_hp=20)


def _apply_publication_styling_to_tables(doc) -> None:
    """Walk every paragraph inside table cells and apply publication
    styling:
      - 2.0 line height (line=480 lineRule=auto)
      - left alignment (jc=left)
      - Times New Roman 10pt on ALL runs

    This matches the user spec: tables and everything else must use
    2.0 line height. Brain's tblPr (table borders, widths) is preserved
    — only paragraph-level styling inside cells is updated.
    """
    body = doc.element.body
    for tbl in body.iter(qn("w:tbl")):
        for p in tbl.iter(qn("w:p")):
            parent = p.getparent()
            if parent is not None and parent.tag.split("}")[-1] == "tc":
                # Confirmed inside a cell — apply styling.
                _apply_publication_styling_in_cell(p)
                for r in p.findall(qn("w:r")):
                    _set_run_font(r, family="Times New Roman", size_hp=20)


# Slot-1 metadata labels (lowercased) used by the post-pass to identify
# metadata rows in the rendered document body. Matched against the
# trimmed paragraph text (lowercased) via startswith().
_SLOT1_METADATA_LABELS = (
    "type:",
    "policy title:",
    "policy number:",
    "applicable sector",
    "functional area",
    "brief description:",
    "effective date",
    "approved by:",
    "prepared by:",
    "responsible function",
    "supersedes:",
    "last reviewed:",
    "applies to:",
    "reason for policy:",
)

# Slot-1 metadata rows that need a bottom border + 8px top/bottom
# margins per user spec. Matched against startswith().
_SLOT1_BOTTOM_BORDER_LABELS = (
    "functional area",
    "applies to:",
    "[english]",
)

# Slot-1 metadata rows that need a TOP border + 8px top/bottom margins
# per user spec. Currently only the very first row ('Type:') so the
# first divider sits above the slot-1 metadata block.
_SLOT1_TOP_BORDER_LABELS = (
    "type:",
)

# Margin values for divider rows.
# 8px ≈ 160 twips (used for Type, Functional Area, Applies to dividers).
_DIVIDER_MARGIN_TWIPS = 160
# 5px ≈ 100 twips (used for [English] dividers per user spec).
_DIVIDER_MARGIN_ENGLISH_TWIPS = 100


def _make_divider_paragraph(side: str = "bottom",
                            margin_twips: int = _DIVIDER_MARGIN_TWIPS) -> object:
    """Create a new empty divider paragraph with a single bottom (or top)
    border line + symmetric top/bottom margins. Used by
    `_apply_slot1_metadata_styling_post_pass` to insert dedicated
    divider paragraphs in the rendered document.

    Each divider paragraph is a separate `<w:p>` element (not a border
    applied to an existing content paragraph). The margin is applied to
    BOTH top (`w:before=margin_twips`) AND bottom (`w:after=margin_twips`).
    Use `_DIVIDER_MARGIN_TWIPS` (160 = 8px) for slot-1 metadata dividers
    and `_DIVIDER_MARGIN_ENGLISH_TWIPS` (100 = 5px) for `[English]`
    dividers per user spec.
    """
    new_p = OxmlElement("w:p")
    pPr = OxmlElement("w:pPr")
    new_p.append(pPr)
    # Symmetric top AND bottom margins.
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:before"), str(margin_twips))
    spacing.set(qn("w:after"), str(margin_twips))
    pPr.append(spacing)
    # Border on the requested side.
    pBdr = OxmlElement("w:pBdr")
    border = OxmlElement(f"w:{side}")
    border.set(qn("w:val"), "single")
    border.set(qn("w:sz"), "4")  # 0.5pt
    border.set(qn("w:space"), "1")
    border.set(qn("w:color"), "000000")
    pBdr.append(border)
    pPr.append(pBdr)
    return new_p


def _apply_slot1_metadata_styling_post_pass(doc) -> None:
    """Global post-pass that walks the entire document body (not just
    the brain scaffold paragraphs) and applies slot-1 metadata styling
    (1.0 line height, Times New Roman 10pt, jc=left) to every paragraph
    whose text starts with a known slot-1 metadata label.

    Also inserts 5 dedicated divider paragraphs (empty `<w:p>` with a
    single bottom-border line + 8px top/bottom margins) at:
      1) Above Type:                  (paragraph BEFORE 'Type:')
      2) Below Functional Area(s):    (paragraph AFTER)
      3) Below Applies to:            (paragraph AFTER)
      4) Below first [English]        (paragraph AFTER)
      5) Below second [English]       (paragraph AFTER)

    The dividers are SEPARATE empty paragraphs (not borders applied to
    content paragraphs). 8px margin applies to BOTH top and bottom of
    each divider.

    Why a global post-pass (instead of inline in `_render_slot_direct`):
    The slot-1 metadata block in `_render_slot_direct` only iterates
    over `para_items + [heading_elem]` — but the user can supply more
    metadata paragraphs than the brain scaffold has body paragraphs,
    and the extras are appended via `_make_new_paragraph` directly to
    the parent. Those extras would be missed by the inline pass.

    Also: this post-pass runs AFTER `_apply_publication_styling_to_body`
    so the 1.0 line-height (line=240) for slot-1 metadata isn't
    overwritten by the body's 2.0 line-height (line=480) pass.
    """
    body = doc.element.body
    # First pass: apply metadata styling (no borders on content paragraphs).
    for p in list(body.findall(qn("w:p"))):
        # Skip paragraphs inside tables — they're handled by
        # `_apply_publication_styling_to_tables`.
        parent = p.getparent()
        if parent is not None and parent.tag.split("}")[-1] == "tc":
            continue
        if _has_table_ancestor(p):
            continue
        text = "".join(t.text or "" for t in p.iter(qn("w:t"))).strip()
        if not text:
            continue
        text_lower = text.lower()
        is_metadata = any(text_lower.startswith(label) for label in _SLOT1_METADATA_LABELS)
        if not is_metadata:
            continue
        _apply_metadata_styling(p)
        # Strip any prior pBdr that might have been added previously
        # (defensive — should be none on a fresh render).
        pPr = p.find(qn("w:pPr"))
        if pPr is not None:
            for existing in pPr.findall(qn("w:pBdr")):
                pPr.remove(existing)

    # Second pass: identify the divider anchor paragraphs and insert
    # dedicated divider paragraphs in the correct positions.
    divider_inserts = []  # list of (anchor_p_elem, side)
    for p in list(body.findall(qn("w:p"))):
        parent = p.getparent()
        if parent is not None and parent.tag.split("}")[-1] == "tc":
            continue
        if _has_table_ancestor(p):
            continue
        text = "".join(t.text or "" for t in p.iter(qn("w:t"))).strip()
        if not text:
            continue
        text_lower = text.lower()
        # Determine which divider set this anchor belongs to.
        is_top = any(text_lower.startswith(label) for label in _SLOT1_TOP_BORDER_LABELS)
        is_bottom = any(text_lower.startswith(label) for label in _SLOT1_BOTTOM_BORDER_LABELS)
        if is_top:
            # 'Type:' divider: 8px margins.
            divider_inserts.append((p, "before", _DIVIDER_MARGIN_TWIPS))
        elif is_bottom:
            # Bottom dividers — split by anchor type:
            # - 'Functional Area', 'Applies to:' → 8px margins
            # - '[English]' → 5px margins (per user spec)
            if text_lower.startswith("[english]"):
                margin = _DIVIDER_MARGIN_ENGLISH_TWIPS
            else:
                margin = _DIVIDER_MARGIN_TWIPS
            divider_inserts.append((p, "after", margin))

    # Insert dividers (process 'before' in reverse index order so the
    # indices of subsequent anchors don't shift).
    for anchor, where, margin in divider_inserts:
        parent = anchor.getparent()
        if parent is None:
            continue
        try:
            anchor_index = list(parent).index(anchor)
        except ValueError:
            continue
        divider_p = _make_divider_paragraph(side="bottom", margin_twips=margin)
        if where == "before":
            parent.insert(anchor_index, divider_p)
        else:  # "after"
            parent.insert(anchor_index + 1, divider_p)


# Faded gray border colors to strip from brain tables. These render as
# light gray lines in the published output (the "old line" the user
# sees around the document). The brain template's `TableGridLight` and
# `PlainTable3` styles use these colors.
_FADED_BORDER_COLORS = frozenset((
    "BFBFBF",
    "7F7F7F",
    "D9D9D9",
    "A6A6A6",
    "595959",
    "auto",
    "808080",
))


def _strip_faded_table_borders(doc) -> None:
    """Remove faded-gray borders from the brain's table styles so the
    published document doesn't have any old light-gray lines around
    Type, Exclusions, etc.

    The brain template uses two table styles that have BFBFBF (light
    gray) borders on every cell edge. These render as visible gray
    lines in the rendered output — what the user calls the "old line".
    This pass removes those borders by setting them to `val="nil"`.

    The brain table layout (rows, columns, cell widths) is preserved.
    Only the cell border colors are stripped.
    """
    body = doc.element.body
    for tbl in body.iter(qn("w:tbl")):
        for tblBorders in tbl.iter(qn("w:tblBorders")):
            for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                el = tblBorders.find(qn(f"w:{side}"))
                if el is None:
                    continue
                color = (el.get(qn("w:color")) or "").upper()
                if color in _FADED_BORDER_COLORS:
                    el.set(qn("w:val"), "nil")
        # Strip cell-level borders too.
        for tcBorders in tbl.iter(qn("w:tcBorders")):
            for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                el = tcBorders.find(qn(f"w:{side}"))
                if el is None:
                    continue
                color = (el.get(qn("w:color")) or "").upper()
                if color in _FADED_BORDER_COLORS:
                    el.set(qn("w:val"), "nil")


def _has_table_ancestor(elem) -> bool:
    """Return True if `elem` has any `<w:tbl>` ancestor (i.e. it is
    inside a table, but not necessarily directly inside a cell)."""
    cur = elem.getparent()
    while cur is not None:
        if cur.tag.split("}")[-1] == "tbl":
            return True
        cur = cur.getparent()
    return False


def _set_run_font(r, *, family: str, size_hp: int) -> None:
    """Set the font family (e.g. 'Times New Roman') and size (half-points,
    e.g. 22 for 11pt) on a single `<w:r>` element. Adds `<w:rPr>` if
    missing. Removes any existing font/size so the new values win."""
    rPr = r.find(qn("w:rPr"))
    if rPr is None:
        rPr = OxmlElement("w:rPr")
        r.insert(0, rPr)
    # Remove existing font + size.
    for tag in ("w:rFonts", "w:sz", "w:szCs"):
        for el in rPr.findall(qn(tag)):
            rPr.remove(el)
    rFonts = OxmlElement("w:rFonts")
    rFonts.set(qn("w:ascii"), family)
    rFonts.set(qn("w:hAnsi"), family)
    rFonts.set(qn("w:cs"), family)
    rFonts.set(qn("w:eastAsia"), family)
    rPr.append(rFonts)
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), str(size_hp))
    rPr.append(sz)
    szCs = OxmlElement("w:szCs")
    szCs.set(qn("w:val"), str(size_hp))
    rPr.append(szCs)