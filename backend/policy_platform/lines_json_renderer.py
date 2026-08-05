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


def _normalise_lines_json(lines_json: Iterable) -> tuple[list[dict], list[dict], list[dict], list[tuple]]:
    """Return (paragraphs_by_slot, tables_by_slot, dividers, free_zone_items).

    - `paragraphs_by_slot` / `tables_by_slot`: dicts keyed by slot id. Slot 0
      entries (free paragraphs) are kept under key `0` and rendered into the
      free-paragraph zone at the top of the body.
    - `dividers`: flat list of `{'slot': N}` for named-slot dividers (used by
      `_render_dividers_in_slot` for slots 1-14).
    - `free_zone_items`: ordered list of `(kind, payload)` tuples for ALL
      slot=0 entries (paragraphs, tables, dividers). The renderer iterates
      this in order so toolbar-inserted content preserves its original
      insertion order in the free paragraph zone.

    Entries are dicts; for paragraphs `{'slot', 'text', 'html'}`; for tables
    `{'slot', 'rows'}` where `rows` is a list of rows of either dict cells
    or string cells (we handle both shapes when writing).
    """
    paragraphs: dict[int, list[dict]] = {}
    tables: dict[int, list[dict]] = {}
    dividers: list[dict] = []
    free_zone_items: list[tuple[str, dict]] = []
    for raw in lines_json or []:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            continue
        kind, payload = raw[0], raw[1]
        if kind == 'p':
            p = _normalise_paragraph_payload(payload)
            slot = p['slot']
            paragraphs.setdefault(slot, []).append(p)
            if slot == 0:
                free_zone_items.append(('p', p))
        elif kind == 't':
            t = _normalise_table_payload(payload)
            slot = t['slot']
            tables.setdefault(slot, []).append(t)
            if slot == 0:
                free_zone_items.append(('t', t))
        elif kind == 'divider':
            # User-inserted <hr> via CKEditor toolbar. Carry slot for
            # render positioning; renderer inserts a divider paragraph
            # at the appropriate place.
            try:
                d_slot = int(payload.get('slot', 0) if isinstance(payload, dict) else 0)
            except (TypeError, ValueError):
                d_slot = 0
            dividers.append({'slot': d_slot})
            if d_slot == 0:
                free_zone_items.append(('divider', {'slot': d_slot}))
    return paragraphs, tables, dividers, free_zone_items


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

    # Left alignment. Only override the existing jc if the paragraph
    # does NOT already have a user-applied alignment. The rich writer
    # sets `<w:jc>` for `<p style="text-align: right|center|justify">`
    # in the user's HTML, and we want those values to survive the
    # post-pass. A jc="left" coming from the rich writer is also left
    # alone (idempotent).
    existing_jc = pPr.find(qn("w:jc"))
    if existing_jc is None or existing_jc.get(qn("w:val")) in (None, "both"):
        if existing_jc is not None:
            pPr.remove(existing_jc)
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
      - Strip italic (`<w:i/>`/`<w:iCs/>`) from the paragraph rPr so all
        labels render consistently bold (matches Policy Title:, etc.).
        The brain scaffold's 'Type:' paragraph inherits italic from the
        template's pPr (which other labels don't have), making it look
        different. Normalize here so the whole slot-1 block reads
        uniformly.
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

    # Strip italic from the paragraph rPr so the label runs render as
    # bold-only (matching Policy Title:, Applicable Sector(s):, etc.).
    # The brain scaffold's 'Type:' paragraph pPr carries `<w:i/>` from
    # the template which leaves the label visually italic.
    pPr_rPr = pPr.find(qn("w:rPr"))
    if pPr_rPr is not None:
        for i_tag in pPr_rPr.findall(qn("w:i")):
            pPr_rPr.remove(i_tag)
        for iCs_tag in pPr_rPr.findall(qn("w:iCs")):
            pPr_rPr.remove(iCs_tag)

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


def _resolve_part_from_p(p_elem):
    """Walk up the parent chain from a `<w:p>` lxml element looking for
    a `.part` attribute. Returns the python-docx `Part` instance, or
    `None` when the chain is detached (no host Document). Used to
    forward the part into the rich writer so `<a href="...">` elements
    can register their relationship in the host document."""
    parent = p_elem.getparent()
    while parent is not None:
        if hasattr(parent, "part"):
            try:
                return parent.part
            except Exception:
                return None
        parent = parent.getparent()
    return None


def _write_paragraph_rich(p_elem, html: str, doc=None) -> None:
    """Write the user's HTML into an existing `<w:p>` element using the
    rich writer (bold/italic/colour/font/heading). Falls back to plain
    text if the rich writer is unavailable or raises.

    Publication styling (1.15 line height) is applied via
    `_apply_publication_styling_no_jc`. The brain scaffold's pStyle is
    preserved so font/theme metrics match the brain framework.
    User-provided rich styling (bold/italic/color/font-size/alignment
    overrides in <span style="..."> or `<p style="text-align:...">`) is
    honoured. Tables are not affected.

    The jc is intentionally applied AFTER the rich writer runs so a
    user-applied `text-align` value wins over the publication default
    of `left`.

    `doc` (optional) is the python-docx `Document` so we can resolve
    the host part for hyperlink relationships. When omitted, we walk
    the parent chain — which only works for paragraphs that are still
    attached to a python-docx `_Body` element. lxml elements from
    `doc.element.body` (used by `_render_slot_direct`) need `doc`."""
    # Remove all children except pPr (preserves paragraph styling).
    for child in list(p_elem):
        if child.tag == qn("w:pPr"):
            continue
        p_elem.remove(child)
    # Apply publication styling (line height only — NOT jc, so the rich
    # writer's user-applied alignment wins). Tables are skipped inside
    # `_apply_publication_styling_no_jc`.
    _apply_publication_styling_no_jc(p_elem)
    # Resolve the host part so hyperlink relationships land in the
    # right place when the user HTML contains `<a href="...">`.
    # Prefer the explicit `doc` argument; fall back to walking the
    # parent chain (works for python-docx Paragraph objects).
    if doc is not None:
        _part = doc.part
    else:
        _part = _resolve_part_from_p(p_elem)
    wp = _import_rich_writer()
    if wp is not None:
        try:
            wp(p_elem, html or "", part=_part, doc=doc)
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


def _apply_publication_styling_no_jc(p_elem) -> None:
    """Apply publication-time line height to a body paragraph WITHOUT
    touching `<w:jc>`. Used by `_write_paragraph_rich` so a user-applied
    `text-align` from the rich writer (e.g. right, center, justify) is
    not overwritten by the publication default of jc=left.
    Mirrors `_apply_publication_styling` but skips the jc step.
    """
    # Skip table cells — preserve their original formatting.
    parent = p_elem.getparent()
    if parent is not None and parent.tag.split("}")[-1] == "tc":
        return
    pPr = p_elem.find(qn("w:pPr"))
    if pPr is None:
        pPr = OxmlElement("w:pPr")
        p_elem.insert(0, pPr)
    has_border = pPr.find(qn("w:pBdr")) is not None
    spacing = pPr.find(qn("w:spacing"))
    if spacing is None:
        spacing = OxmlElement("w:spacing")
        pPr.append(spacing)
    if not has_border:
        if spacing.get(qn("w:after")) is not None:
            del spacing.attrib[qn("w:after")]
        if spacing.get(qn("w:before")) is not None:
            del spacing.attrib[qn("w:before")]
    spacing.set(qn("w:line"), "480")
    spacing.set(qn("w:lineRule"), "auto")
    # Intentionally NOT setting jc here.


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

    Fix E: if the user's saved line_json already carries inline
    formatting (strong / em / u / s / span with style), we skip the
    label-bold split so the user's toolbar-applied formatting is not
    overwritten. Otherwise the value run is stripped of its bold and the
    user's `<strong>` on a metadata line becomes invisible (the label
    portion was always bold by default).
    """
    t = (user_text or '').strip()
    if not t:
        return
    # Fix E: detect user-applied inline formatting on any run in this
    # paragraph. If found, skip label-bold so the user's formatting
    # survives verbatim. The label portion will still be bold because
    # the user's <strong> wraps the whole text.
    _INLINE_FMT_TAGS = ('<strong', '<b>', '<em', '<i>', '<u', '<s ',
                        '<span', '<mark')
    for r in p_elem.findall(qn("w:r")):
        rPr = r.find(qn("w:rPr"))
        if rPr is not None:
            if (rPr.find(qn("w:b")) is not None
                or rPr.find(qn("w:i")) is not None
                or rPr.find(qn("w:u")) is not None
                or rPr.find(qn("w:strike")) is not None):
                return
        # Also check the raw HTML-equivalent: any run that has a w:t
        # whose text came from a tagged span. Since we already have
        # lxml runs here (not raw HTML), the rPr check above is the
        # reliable signal.

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


def _apply_visible_table_borders(tblPr) -> None:
    """Fix G3: force a visible 1pt black border on every edge of a table.

    Without this, tables that reuse the brain template's scaffold style
    (TableGridLight, PlainTable3) inherit very faint BFBFBF / 0.5pt
    borders that are effectively invisible in Word — the user sees an
    empty grid instead of a real table. We explicitly replace any
    inherited `<w:tblBorders>` with solid 1pt black borders so the
    user's toolbar-inserted tables are clearly visible.
    """
    if tblPr is None:
        return
    # Remove any existing tblBorders so our explicit borders win.
    for old in tblPr.findall(qn("w:tblBorders")):
        tblPr.remove(old)
    tblBorders = OxmlElement("w:tblBorders")
    for bn in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = OxmlElement(f"w:{bn}")
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), "8")  # 1pt
        b.set(qn("w:space"), "0")
        b.set(qn("w:color"), "000000")
        tblBorders.append(b)
    tblPr.append(tblBorders)


def _write_table(table_elem, rows: list[list[Any]], preserve_dimensions: bool = False, doc=None) -> int:
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
                    wp(new_p, html or plain, part=doc.part if doc is not None else None)
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
    # Fix G3: force visible 1pt black borders so toolbar-inserted
    # tables don't inherit the brain's faint TableGridLight style.
    _apply_visible_table_borders(table_elem.find(qn("w:tblPr")))
    return len(rows)


def _make_new_paragraph(template_pPr, text_or_html: str, is_html: bool, doc=None) -> object:
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
                # Fix B: pass `doc=doc` so write_paragraph's list branch
                # fires. Without doc, lists (ul/ol/todo-list) are
                # flattened into a single paragraph with no bullets /
                # numbers / checkbox markers.
                wp(new_p, text_or_html, part=doc.part if doc is not None else None, doc=doc)
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

    # Slot 14 (HISTORY): strip `<w:numPr>` from the slot-14 heading and
    # body paragraphs so the HISTORY heading and any scaffold body
    # paragraphs render as plain text (not as Roman-numeral bullets).
    # Mirrors the plain-paragraph style used by slot 10 (Award
    # Structure & Payout Tiers), which has no `<w:numPr>` and renders
    # cleanly. Without this, the user's edited HISTORY data renders
    # with inherited bullet numbering because the brain template's
    # HISTORY heading is a `ListParagraph` with `<w:numId val="6"/>`.
    # Only the heading + scaffold body paragraphs in this slot are
    # touched — other slots retain their original bullet numbering.
    if sec_id == 14:
        # Strip <w:numPr> from scaffold body paragraphs so user-edited
        # data renders as plain text (no bullets).
        for p in para_items:
            if p is None:
                continue
            pPr = p.find(qn("w:pPr"))
            if pPr is None:
                continue
            for numPr in pPr.findall(qn("w:numPr")):
                pPr.remove(numPr)
        # Add Roman numeral (numId=6) back to the HISTORY HEADING only.
        # Other titles (INTRODUCTION, DEFINITIONS, etc.) use Roman numerals
        # so HISTORY must match. The body paragraphs remain stripped so
        # the user's edited data doesn't render as bullets.
        if heading_elem is not None:
            pPr = heading_elem.find(qn("w:pPr"))
            if pPr is None:
                pPr = OxmlElement("w:pPr")
                heading_elem.insert(0, pPr)
            # Remove any existing numPr first to avoid duplicates.
            for existing in pPr.findall(qn("w:numPr")):
                pPr.remove(existing)
            numPr = OxmlElement("w:numPr")
            ilvl = OxmlElement("w:ilvl")
            ilvl.set(qn("w:val"), "0")
            numId = OxmlElement("w:numId")
            numId.set(qn("w:val"), "6")  # Roman numeral list
            numPr.append(ilvl)
            numPr.append(numId)
            # numPr must come before spacing per OOXML schema.
            spacing = pPr.find(qn("w:spacing"))
            if spacing is not None:
                spacing.addprevious(numPr)
            else:
                pPr.append(numPr)

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
        _write_paragraph_rich(heading_elem, html, doc=doc)
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
            _write_paragraph_rich(heading_elem, html, doc=doc)
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
            _write_paragraph_rich(p_elem, html, doc=doc)
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
                    new_p = _make_new_paragraph(pPr_template, html, is_html=True, doc=doc)
                    parent.insert(insert_pos, new_p)
                    insert_pos += 1
        else:
            # No scaffold body paragraphs (label-row slot like 2/4/11).
            # Write the FIRST user paragraph into heading_elem (replacing
            # the scaffold label-row). Append remaining paragraphs.
            first_entry = body_paras[0]
            html = first_entry.get('html') or first_entry.get('text') or ''
            _write_paragraph_rich(heading_elem, html, doc=doc)
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
                    new_p = _make_new_paragraph(pPr_template, html, is_html=True, doc=doc)
                    parent.insert(insert_pos, new_p)
                    insert_pos += 1

    # 3) Tables: replace scaffold tables; append extras after.
    if tables and table_items:
        # First table → first scaffold table.
        first_table = tables[0]
        first_rows = first_table.get('rows') or []
        _write_table(table_items[0], first_rows, doc=doc)
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
                                    wp(new_p, _cell_to_html(cell) or _cell_to_plain(cell), part=doc.part if doc is not None else None)
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
                    # Fix G3: override the copied brain-scaffold tblPr
                    # with explicit 1pt black borders so the extra table
                    # is clearly visible (not the faint brain default).
                    _apply_visible_table_borders(new_tbl.find(qn("w:tblPr")))
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
                                wp(new_p, _cell_to_html(cell) or _cell_to_plain(cell), part=doc.part if doc is not None else None)
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


def _render_dividers_in_slot(doc, slot_id: int, dividers: list[dict]) -> None:
    """Insert divider paragraphs for `<hr>` elements the reviewer added
    via CKEditor toolbar. Each divider is an empty paragraph with a
    bottom border + 8px top/bottom margins (matching slot-1 style).

    For slot 0, dividers go into the free-paragraph zone at the top of
    the body. For other slots, dividers are appended at the end of that
    slot's body (after the user's paragraphs and tables).
    """
    if not dividers:
        return

    def _make_divider() -> object:
        new_p = OxmlElement("w:p")
        pPr = OxmlElement("w:pPr")
        new_p.append(pPr)
        spacing = OxmlElement("w:spacing")
        spacing.set(qn("w:before"), "160")
        spacing.set(qn("w:after"), "160")
        pPr.append(spacing)
        pBdr = OxmlElement("w:pBdr")
        border = OxmlElement("w:bottom")
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), "4")
        border.set(qn("w:space"), "1")
        border.set(qn("w:color"), "000000")
        pBdr.append(border)
        pPr.append(pBdr)
        return new_p

    if slot_id == 0:
        # Free-paragraph zone: insert dividers at the top of body.
        body = doc.element.body
        insert_pos = 0
        for child in body:
            if child.tag == qn("w:p"):
                insert_pos = list(body).index(child)
                break
        for d in dividers:
            body.insert(insert_pos, _make_divider())
            insert_pos += 1
        return

    # For named slots: append dividers after the slot's body content.
    body = doc.element.body
    children = list(body)
    info = BRAIN_SLOT_RANGES.get(slot_id)
    if not info or not info.get("body_items"):
        return
    last_idx = -1
    body_items = info["body_items"]
    for i in reversed(body_items):
        if i < len(children):
            last_idx = i
            break
    if last_idx < 0:
        return
    insert_pos = last_idx + 1
    for d in dividers:
        body.insert(insert_pos, _make_divider())
        insert_pos += 1


def _render_free_paragraph_zone(doc, free_zone_items: list) -> None:
    """Render slot=0 items (paragraphs + tables + dividers) into a free
    paragraph zone at the top of the body — AFTER the header but BEFORE
    slot 1.

    `free_zone_items` is an ordered list of `(kind, payload)` tuples from
    `_normalise_lines_json`. The order is preserved so toolbar-inserted
    content (paragraphs, tables, dividers) lands in the SAME visual order
    as the user inserted it in the editor.

    This is the graceful-degradation path: when the user has paragraphs
    with no slot assigned (or whose slot is unknown), we still emit them
    so the user doesn't lose data. They appear at the top of the body
    so the rest of the brain scaffold is preserved.
    """
    if not free_zone_items:
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
    # The "Free Paragraphs" zone label was previously inserted here so
    # reviewers could see this section. Per user spec, it's unnecessary
    # text in the published docx — remove it. The free-zone items are
    # inserted in their original order below.
    # Insert the free-zone items in original insertion order.
    for kind, payload in free_zone_items:
        if kind == 'p':
            text = payload.get('text') or ''
            html = payload.get('html') or text or ''
            if not text.strip() and not html.strip():
                continue
            new_p = _make_new_paragraph(template_pPr, html, is_html=True, doc=doc)
            # Strip <w:numPr> from the user-written paragraph so it doesn't
            # render as a bullet (the brain's free-paragraph zone templates
            # inherit <w:numPr> from the scaffold, which would make ALL
            # user-typed text render as Roman-numeral bullets).
            # Fix B: BUT if this paragraph is a list (starts with <ul>/<ol>),
            # the numPr was intentionally added by _write_list_paragraphs
            # to render bullets/numbers — keep it.
            stripped_html = html.lstrip()
            is_list = stripped_html.startswith('<ul') or stripped_html.startswith('<ol')
            if not is_list:
                new_pPr = new_p.find(qn("w:pPr"))
                if new_pPr is not None:
                    for numPr in new_pPr.findall(qn("w:numPr")):
                        new_pPr.remove(numPr)
            body.insert(insert_pos, new_p)
            insert_pos += 1
        elif kind == 't':
            # Toolbar-inserted table with slot=0. Render as a standalone
            # table in the free zone. Uses the same styling as the
            # extra-table fallback in slot rendering.
            rows = payload.get('rows') or []
            if not rows:
                continue
            new_tbl = _build_free_zone_table(rows)
            if new_tbl is not None:
                body.insert(insert_pos, new_tbl)
                insert_pos += 1
        elif kind == 'divider':
            # User-inserted <hr> divider. Render as an empty paragraph
            # with a bottom border + 8px margins (matches slot-1
            # divider style).
            new_p = _make_divider_paragraph()
            body.insert(insert_pos, new_p)
            insert_pos += 1


def _build_free_zone_table(rows: list) -> object:
    """Build a standalone <w:tbl> element for a slot=0 table (free
    paragraph zone). Mirrors the styling of the extra-table fallback in
    `_render_slot_direct` (single black border, 100% width, fixed grid).
    """
    if not rows:
        return None
    actual_cols = max((len(r) for r in rows), default=1)
    new_tbl = OxmlElement("w:tbl")
    tblPr = OxmlElement("w:tblPr")
    tblW = OxmlElement("w:tblW")
    tblW.set(qn("w:w"), "5000")
    tblW.set(qn("w:type"), "pct")
    tblPr.append(tblW)
    # Fix G3: explicit, visible borders for toolbar-inserted tables.
    # Without this, the free-zone tables inherit the brain's
    # TableGridLight style which uses BFBFBF / sz=4 (very faint)
    # borders that are effectively invisible in Word — the user sees
    # an empty grid instead of a real table. Force solid 1pt black
    # borders here so user-inserted tables are clearly visible.
    tblBorders = OxmlElement("w:tblBorders")
    for bn in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = OxmlElement(f"w:{bn}")
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), "8")  # 1pt
        b.set(qn("w:space"), "0")
        b.set(qn("w:color"), "000000")
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
                    # Fix B: pass `doc=doc` so in-cell lists render
                    # with bullets / numbers / checkbox markers.
                    wp(new_p, _cell_to_html(cell) or _cell_to_plain(cell), part=doc.part if doc is not None else None, doc=doc)
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
            tc.append(new_p)
            tr.append(tc)
        new_tbl.append(tr)
    return new_tbl


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
    paragraphs_by_slot, tables_by_slot, dividers, free_zone_items = _normalise_lines_json(lines_json)

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

    # 4) Slot 0 (free paragraphs + tables + dividers) → free-paragraph zone
    # at top of body. The free_zone_items list preserves the original
    # insertion order so toolbar-inserted content (paragraphs, tables,
    # dividers) lands in the correct visual order.
    try:
        _render_free_paragraph_zone(doc, free_zone_items)
    except Exception as e:
        print(f'[lines_json_renderer] free-paragraph zone failed: {e}', flush=True)
        # Non-fatal — the rest of the body is still valid.

    # 4.5) User-inserted <hr> dividers (from CKEditor toolbar) for NAMED
    # slots (1-14). Each divider is rendered as an empty paragraph with a
    # bottom border line + 8px margins, matching the existing slot-1
    # divider style. Group by slot so dividers land in the correct
    # section body. Slot=0 dividers are already handled by
    # `_render_free_paragraph_zone` via `free_zone_items`.
    try:
        from collections import defaultdict
        dividers_by_slot: dict[int, list[dict]] = defaultdict(list)
        for d in dividers:
            d_slot = d.get('slot', 0)
            if d_slot == 0:
                continue  # handled by free_zone_items above
            dividers_by_slot[d_slot].append(d)
        for d_slot, d_list in dividers_by_slot.items():
            _render_dividers_in_slot(doc, d_slot, d_list)
    except Exception as e:
        print(f'[lines_json_renderer] dividers insertion failed (non-fatal): {e}', flush=True)

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

    # 5.10) Suppress the Word auto-separator between the page header
    # and the body content. The brain's `header2.xml` and `header3.xml`
    # carry the logo + "Document Control" text. Without this pass Word
    # draws an automatic 1px black rule between the header and the
    # first body paragraph (`Type:`) — the "second divider line" the
    # user sees. Setting `<w:pBdr><w:bottom w:val="nil"/>` on the LAST
    # paragraph of each header suppresses the auto-separator while
    # keeping the header content (logo + Document Control text) intact.
    _suppress_page_header_separator(doc)

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

    Preserves paragraphs with `<w:pBdr>` (paragraph borders) — these
    are intentional divider paragraphs (slot-1 metadata dividers, free
    zone dividers from user toolbar inserts) and must NOT be stripped.
    """
    body = doc.element.body
    for p in list(body.findall(qn("w:p"))):
        # Skip if inside a table cell.
        parent = p.getparent()
        if parent is not None and parent.tag.split("}")[-1] == "tc":
            continue
        # Preserve divider paragraphs (those with paragraph borders). These
        # are intentional visual dividers inserted by `_make_divider_paragraph`
        # (slot-1 metadata dividers in `_apply_slot1_metadata_styling_post_pass`
        # and toolbar-inserted dividers in `_render_free_paragraph_zone`).
        # Removing them would silently drop user toolbar inserts.
        pPr = p.find(qn("w:pPr"))
        if pPr is not None and pPr.find(qn("w:pBdr")) is not None:
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
      - Times New Roman 10pt on runs that have NO explicit size set

    Bold/italic/underline/strikethrough/color are preserved on runs
    that have explicit rPr — we only override the font family and
    size, not the rest of the rPr.
    Fix G1 (revised): runs that already carry `<w:sz>` (set by the
    heading pipeline to 36/30/26 half-points for H1/H2/H3) are LEFT
    ALONE so heading sizes survive this post-pass.
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
        # Apply Times New Roman 10pt to runs that DON'T already have a
        # custom size. Heading runs carry `<w:sz>` from the heading
        # pipeline and must not be clobbered back to 10pt.
        for r in p.findall(qn("w:r")):
            rPr = r.find(qn("w:rPr"))
            has_explicit_size = (
                rPr is not None and rPr.find(qn("w:sz")) is not None
            )
            if has_explicit_size:
                # Only set font family, preserve the existing size.
                _set_run_font(r, family="Times New Roman", size_hp=None)
            else:
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
                            margin_twips: int = _DIVIDER_MARGIN_TWIPS,
                            *,
                            before_twips: int = None,
                            after_twips: int = None) -> object:
    """Create a new empty divider paragraph with a single bottom (or top)
    border line + asymmetric top/bottom margins. Used by
    `_apply_slot1_metadata_styling_post_pass` to insert dedicated
    divider paragraphs in the rendered document.

    Each divider paragraph is a separate `<w:p>` element (not a border
    applied to an existing content paragraph). The margins can be
    configured independently for top and bottom:
      - `margin_twips` is the default for both when `before_twips`
        and `after_twips` are not provided (legacy symmetric mode).
      - `before_twips` overrides the top margin.
      - `after_twips` overrides the bottom margin.

    Use `_DIVIDER_MARGIN_TWIPS` (160 = 8px) for slot-1 metadata
    dividers, and `_DIVIDER_MARGIN_ENGLISH_TWIPS` (100 = 5px top,
    160 = 8px bottom) for `[English]` dividers per user spec.
    """
    new_p = OxmlElement("w:p")
    pPr = OxmlElement("w:pPr")
    new_p.append(pPr)
    # Fix I5: larger margins so the divider line is clearly separated
    # from surrounding text and unmistakably visible. Previous margins
    # (160 twips = 8px before/after) were too tight, making the
    # divider blend with adjacent content. 360 twips (~18px) gives
    # clear visual separation above and below the rule.
    before_val = str(before_twips if before_twips is not None else 360)
    after_val = str(after_twips if after_twips is not None else 360)
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:before"), before_val)
    spacing.set(qn("w:after"), after_val)
    pPr.append(spacing)
    # Border on the requested side.
    pBdr = OxmlElement("w:pBdr")
    border = OxmlElement(f"w:{side}")
    border.set(qn("w:val"), "single")
    # Fix I5: thicker, clearly visible divider border. 2pt (w:sz="16")
    # is an unambiguous, bold rule that is easily seen in Word.
    border.set(qn("w:sz"), "16")  # 2pt
    border.set(qn("w:space"), "4")
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
        if is_metadata:
            _apply_metadata_styling(p)
            # Strip any prior pBdr that might have been added previously
            # (defensive — should be none on a fresh render).
            pPr = p.find(qn("w:pPr"))
            if pPr is not None:
                for existing in pPr.findall(qn("w:pBdr")):
                    pPr.remove(existing)
        # Per user spec: `[English]` text gets 6px top margin (120 twips)
        # so the breathing-room ABOVE `[English]` matches the user's
        # design (6px above, 5px to divider, 8px below divider).
        # This runs for `[English]` paragraphs whether or not they're
        # in `_SLOT1_METADATA_LABELS` (they aren't — they're handled
        # separately).
        if text_lower.startswith("[english]"):
            pPr = p.find(qn("w:pPr"))
            if pPr is None:
                pPr = OxmlElement("w:pPr")
                p.insert(0, pPr)
            spacing = pPr.find(qn("w:spacing"))
            if spacing is None:
                spacing = OxmlElement("w:spacing")
                pPr.append(spacing)
            spacing.set(qn("w:before"), "120")

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
            # 'Type:' divider: 8px margins top+bottom.
            divider_inserts.append((p, "before", _DIVIDER_MARGIN_TWIPS, _DIVIDER_MARGIN_TWIPS))
        elif is_bottom:
            # Bottom dividers — split by anchor type:
            # - 'Functional Area', 'Applies to:' → 8px both
            # - '[English]' → 5px top, 8px bottom (per user spec)
            if text_lower.startswith("[english]"):
                before_m = _DIVIDER_MARGIN_ENGLISH_TWIPS
                after_m = _DIVIDER_MARGIN_TWIPS
            else:
                before_m = _DIVIDER_MARGIN_TWIPS
                after_m = _DIVIDER_MARGIN_TWIPS
            divider_inserts.append((p, "after", before_m, after_m))

    # Insert dividers (process 'before' in reverse index order so the
    # indices of subsequent anchors don't shift).
    for anchor, where, before_m, after_m in divider_inserts:
        parent = anchor.getparent()
        if parent is None:
            continue
        try:
            anchor_index = list(parent).index(anchor)
        except ValueError:
            continue
        divider_p = _make_divider_paragraph(
            side="bottom",
            before_twips=before_m,
            after_twips=after_m,
        )
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


# Table styles whose inherited borders should be cleared in the
# published output's styles.xml. The brain template defines borders on
# these styles (BFBFBF for TableGridLight, 7F7F7F for PlainTable3) that
# render as visible gray lines via the `<w:tblStyle>` reference on tables
# in document.xml — the user calls these the "old divider lines".
_TABLE_STYLES_TO_CLEAR = frozenset(("TableGridLight", "PlainTable3"))


def _strip_inherited_table_style_borders(doc) -> None:
    """Mutate `word/styles.xml` in the published document to clear all
    borders on `TableGridLight` and `PlainTable3` table styles.

    Why: tables in the published output reference these styles via
    `<w:tblStyle w:val="TableGridLight"/>` and `<w:tblStyle w:val=
    "PlainTable3"/>`. Word inherits the styles' `<w:tblBorders>` and
    `<w:tcBorders>` definitions (BFBFBF / 7F7F7F) and renders them as
    visible gray lines — the "old divider lines" the user wants
    removed.

    The `<w:tblStyle>` references in document.xml are preserved (the
    user does not want them stripped). This function only edits the
    styles part so the inherited borders no longer render.

    For each target style:
      - `<w:tblPr><w:tblBorders>` → set every side `val="nil"`
      - `<w:tblStylePr w:type="..."><w:tcBorders>` → set every side
        `val="nil"` (firstRow, firstCol, etc.)
    """
    styles_root = doc.styles.element
    for style in styles_root.findall(qn("w:style")):
        style_id = style.get(qn("w:styleId"))
        if style_id not in _TABLE_STYLES_TO_CLEAR:
            continue
        # 1) Clear <w:tblPr><w:tblBorders> if present.
        tblPr = style.find(qn("w:tblPr"))
        if tblPr is not None:
            tblBorders = tblPr.find(qn("w:tblBorders"))
            if tblBorders is not None:
                _nil_all_borders(tblBorders)
        # 2) Clear <w:tblStylePr w:type="..."><w:tcBorders> for all
        # conditional-format variants (firstRow, firstCol, lastRow,
        # lastCol, band1Vert, band1Horz, neCell, nwCell, etc.).
        for tblStylePr in style.findall(qn("w:tblStylePr")):
            inner_tcPr = tblStylePr.find(qn("w:tcPr"))
            if inner_tcPr is None:
                continue
            tcBorders = inner_tcPr.find(qn("w:tcBorders"))
            if tcBorders is not None:
                _nil_all_borders(tcBorders)


def _nil_all_borders(borders_elem) -> None:
    """Set every border side (`top`, `left`, `bottom`, `right`,
    `insideH`, `insideV`) inside `<w:tblBorders>` or `<w:tcBorders>`
    to `val="nil"`. Preserves the parent structure; just flips each
    side's val attribute. Other attributes (sz, color, space) are left
    in place — Word ignores them when val=nil but it keeps the file
    diff minimal for review."""
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = borders_elem.find(qn(f"w:{side}"))
        if el is None:
            continue
        el.set(qn("w:val"), "nil")


def _suppress_page_header_separator(doc) -> None:
    """Remove horizontal-line drawings from the page header AND the
    document body.

    Two sources of unwanted horizontal lines exist in the brain
    template:

    1. `header2.xml` contains a `<v:line>` ("Straight Connector")
       at 55.2pt vertical position — renders as a 1px black line
       between the page header and body content on every page.

    2. `document.xml` contains TWO `<v:line>` connectors ("Straight
       Connector 3" at 4.85pt and "Straight Connector 4" at 6.5pt)
       inside `<mc:AlternateContent>` / `<w:pict>` legacy-VML
       containers. These render as 2 horizontal lines in the body
       content (visible above `Type:` and above `3. Exclusions`).

    Without removal, the published docx shows 5 dividers (mine) + 2
    brain lines = 7 total visible lines. The user has asked for the
    brain lines to be removed so only the 5 dedicated dividers remain.

    This pass:
      A) Walks every section's header (default, first, even):
         - Removes every `<v:line>` element
         - Removes empty `<w:pict>` containers
         - Strips `<w:pBdr>` from the last paragraph (cleanup)
      B) Walks the document body:
         - Removes any `<mc:AlternateContent>` whose `<mc:Fallback>`
           contains ONLY `<w:pict><v:line>...</v:line></w:pict>`
           (the modern `<mc:Choice>` rendering uses `<w:drawing>`
           so removing the legacy fallback is safe)
         - Removes any standalone `<v:line>` in body paragraphs
         - Removes empty `<w:pict>` containers

    The brain's paragraphs, tables, text, headings, images, and the
    header's logo + Document Control text are NOT touched.
    """
    # VML namespace URI for <v:line> elements.
    V_NS = "urn:schemas-microsoft-com:vml"
    V_LINE = "{" + V_NS + "}line"
    # Markup-compatibility namespace for <mc:AlternateContent> and
    # <mc:Fallback>.
    MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    MC_ALT = "{" + MC_NS + "}AlternateContent"
    MC_FALLBACK = "{" + MC_NS + "}Fallback"

    def _remove_vlines_from(root) -> None:
        """Strip every <v:line> descendant; collapse empty <w:pict>."""
        for vline in list(root.iter(V_LINE)):
            parent = vline.getparent()
            if parent is not None:
                parent.remove(vline)
        for pict in list(root.iter(qn("w:pict"))):
            if len(list(pict)) == 0:
                pict.getparent().remove(pict)

    # A) Page-header pass.
    for section in doc.sections:
        for header_attr in ("header", "first_page_header", "even_page_header"):
            try:
                header = getattr(section, header_attr)
            except Exception:
                continue
            if header is None:
                continue
            try:
                if header.is_linked_to_previous:
                    continue
            except Exception:
                pass
            _remove_vlines_from(header._element)
            # Remove zero-height <w:drawing> elements — these are
            # degenerate drawings (cy="0") that render as horizontal
            # lines on every page where the header appears. The brain
            # template has one in `header2.xml` (cx=5936689 EMUs,
            # cy=0) which was the 2nd line the user kept seeing.
            # Wordprocessing-drawing extent namespace.
            WPD_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
            CY_ATTR = "{" + WPD_NS + "}cy"
            for drawing in list(header._element.iter(qn("w:drawing"))):
                extent = drawing.find(".//" + qn("wp:extent"))
                if extent is None:
                    continue
                # The cy attribute is unnamespaced in the XML on disk;
                # try both forms for robustness.
                cy_val = extent.get(CY_ATTR)
                if cy_val is None:
                    cy_val = extent.get("cy")
                if cy_val is not None and cy_val == "0":
                    parent = drawing.getparent()
                    if parent is not None:
                        parent.remove(drawing)
            # Strip leftover <w:pBdr> from last paragraph (cleanup).
            paras = header.paragraphs
            if not paras:
                continue
            last_p = paras[-1]._element
            pPr = last_p.find(qn("w:pPr"))
            if pPr is not None:
                for old_pBdr in list(pPr.findall(qn("w:pBdr"))):
                    pPr.remove(old_pBdr)

    # B) Document-body pass.
    body = doc.element.body
    # 1) Remove <mc:AlternateContent> whose <mc:Fallback> contains
    # ONLY a <w:pict><v:line/></w:pict>. The modern <mc:Choice>
    # rendering uses <w:drawing> so removing the legacy fallback
    # container is safe.
    for mc_alt in list(body.iter(MC_ALT)):
        fallback = mc_alt.find(MC_FALLBACK)
        if fallback is None:
            continue
        # Find any <v:line> in fallback
        vlines_in_fallback = list(fallback.iter(V_LINE))
        if not vlines_in_fallback:
            continue
        # Check fallback contains ONLY <w:pict><v:line/></w:pict>
        # (i.e. the only meaningful content is the line connectors)
        # If <mc:Choice> exists with real content, keep that and just
        # remove the fallback subtree.
        fallback_has_only_vlines = True
        for child in fallback:
            if child.tag != qn("w:pict"):
                fallback_has_only_vlines = False
                break
            # Check the pict itself contains only v:line
            for pict_child in child:
                if pict_child.tag != V_LINE:
                    fallback_has_only_vlines = False
                    break
            if not fallback_has_only_vlines:
                break
        # If fallback is purely <w:pict><v:line/></w:pict>, drop the
        # entire <mc:AlternateContent> (the line + the legacy
        # fallback wrapper).
        if fallback_has_only_vlines:
            mc_alt.getparent().remove(mc_alt)
            continue
        # Otherwise just remove the v:line(s) inside fallback.
        for vline in vlines_in_fallback:
            parent = vline.getparent()
            if parent is not None:
                parent.remove(vline)
    # 2) Strip any remaining standalone <v:line> elements anywhere
    # in the body.
    _remove_vlines_from(body)


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
    missing. PRESERVES any user-applied `<w:rFonts>` or `<w:sz>` that
    came from the rich writer — only writes the default when the run
    has no explicit font/size set already. This way user toolbar
    formatting (font family, font size) is not silently overwritten by
    the post-pass that normalises to Times New Roman 10pt."""
    rPr = r.find(qn("w:rPr"))
    if rPr is None:
        rPr = OxmlElement("w:rPr")
        r.insert(0, rPr)
    # Only set the default font when the run has no rFonts of its own
    # (i.e. the user did not pick a font via the toolbar).
    if not rPr.findall(qn("w:rFonts")):
        rFonts = OxmlElement("w:rFonts")
        rFonts.set(qn("w:ascii"), family)
        rFonts.set(qn("w:hAnsi"), family)
        rFonts.set(qn("w:cs"), family)
        rFonts.set(qn("w:eastAsia"), family)
        rPr.append(rFonts)
    # Only set the default size when the run has no <w:sz> of its own.
    if not rPr.findall(qn("w:sz")):
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), str(size_hp))
        rPr.append(sz)
    if not rPr.findall(qn("w:szCs")):
        szCs = OxmlElement("w:szCs")
        szCs.set(qn("w:val"), str(size_hp))
        rPr.append(szCs)