"""Renderer: produces output .docx using the Brain as a template.

Reverse-order render:
  - Slots are processed in REVERSE order (last to first).
  - This way, inserting paragraphs in slot N doesn't shift slot N-1's
    body indices.
  - For each slot, replace the body in place with source content.
  - All slot headings are stripped of example values (text after ':' or '\t').
"""
from __future__ import annotations

import copy
import hashlib
import re
import shutil
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from . import config
from .analyzer import ClassificationResult
from .extractors.base import ExtractedDocument
from .extract_myanmar.debug_logging import log_checkpoint
from .framework.brain_fields import (
    BRAIN_APPROVAL_FIELDS,
    BRAIN_BRIEF_DESCRIPTION_FIELDS,
    BRAIN_HEADER_FIELDS,
    BRAIN_LABEL_ROWS,
    BRAIN_REASON_FIELDS,
    BRAIN_REVIEW_NOTE_FIELDS,
    canonical_label,
    missing_field_placeholder,
)
from .framework.brain_slot_map import BRAIN_SLOT_RANGES, find_slot_boundaries
from .framework.slot_tiers import SLOT_TIERS, slot_label, slot_required
from .post_render import strip_black_lines  # noqa: F401 — kept for future re-introduction

from .style import (
    apply_styles_to_section,
    handle_example_prefix,
    replace_bullets_with_filled,
)
from .framework.slot_capacity import get_slot_capacity


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
        if n not in actual or hashlib.sha256(actual[n]).hexdigest() != hashlib.sha256(d).hexdigest():
            raise RuntimeError(f"Brain media integrity check failed for {n}")


def _restore_media_store_compression(docx_path: Path) -> None:
    """python-docx writes media files (JPEG, PNG) using DEFLATE compression.
    Microsoft's strict OOXML reader sometimes rejects DEFLATE-compressed media.
    The Brain template stores media as STORE (no compression). This function
    rewrites the docx so that word/media/* entries use STORE compression
    (matching Brain's structure) while everything else stays DEFLATE."""
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


def _set_paragraph_text(p_elem, new_text: str) -> None:
    runs = p_elem.findall(qn("w:r"))
    for r in runs:
        p_elem.remove(r)
    for h in p_elem.findall(qn("w:hyperlink")):
        p_elem.remove(h)
    new_r = OxmlElement("w:r")
    if runs:
        first_rpr = runs[0].find(qn("w:rPr"))
        if first_rpr is not None:
            new_r.append(copy.deepcopy(first_rpr))
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    # FIX (Phase I.4b): also strip leading bullet characters from
    # body paragraphs (not just table cells).
    t.text = _strip_leading_bullet(new_text)
    new_r.append(t)
    p_elem.append(new_r)


def _make_paragraph(pPr_template, text: str):
    new_p = OxmlElement("w:p")
    if pPr_template is not None:
        new_p.append(copy.deepcopy(pPr_template))
    new_r = OxmlElement("w:r")
    if pPr_template is not None:
        rPr = pPr_template.find(qn("w:rPr"))
        if rPr is not None:
            new_r.append(copy.deepcopy(rPr))
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    new_r.append(t)
    new_p.append(new_r)
    return new_p


def _strip_heading_label(heading_text: str) -> str:
    for sep in (":", "\t"):
        if sep in heading_text:
            return heading_text.split(sep)[0].strip()
    return heading_text.strip()


def _replace_table_element(table_elem, new_rows: list[list[str]], target_rows: int, target_cols: int) -> int:
    """Phase T rewrite: rebuild the table from source data, ignoring
    the Brain template's row/column count.

    Per the user's directive ("for the table you can adjust the rows
    and cols — no need to be the same as brain"), we no longer try
    to preserve the Brain template's column count. Instead:
      - Row count: exactly `len(new_rows)` (one row per source row).
      - Column count: `max(len(r) for r in new_rows)` (one column per
        source field).
      - Column widths: equal split of the existing total table width
        (sum of original `<w:gridCol>` widths, default 5000×N twips).

    This eliminates the "extra column" bug (HISTORY table's empty
    5th grid column in the Brain template) and the "missing widths"
    bug (tier table had 4 source cells but only 2 grid columns, so
    cells 3-4 had no explicit width).
    """
    # Floor for per-column width. Matches Phase L.1b.
    MIN_COL_DXA = 1500

    # Step 1: Remove all existing <w:tr> rows (Brain template example rows).
    for tr in table_elem.findall(qn("w:tr")):
        table_elem.remove(tr)

    # Step 2: Compute the actual column count from the source data
    # (NOT from the Brain template's grid). This is the key Phase T
    # change: the source data's column count is the source of truth.
    actual_cols = max((len(r) for r in new_rows), default=1)

    # Step 3: Compute the total table width by summing existing
    # `<w:gridCol>` widths. This preserves the overall table width
    # (so the page layout doesn't shift) while redistributing evenly.
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
    # If the original grid had no usable widths, fall back to a
    # reasonable default: 5000 twips (~3.5 inches) per column.
    if total_width == 0:
        total_width = 5000 * actual_cols

    # Step 4: Compute per-column width (equal split).
    col_w = max(total_width // actual_cols, MIN_COL_DXA)

    # Step 5: Rebuild the <w:tblGrid> with exactly `actual_cols` columns,
    # each with the equal width. This is the fix for the "extra
    # column" bug — the grid now matches the source data.
    if grid_cols_elem is not None:
        # Remove all existing <w:gridCol> children.
        for gc in grid_cols_elem.findall(qn("w:gridCol")):
            grid_cols_elem.remove(gc)
        # Add `actual_cols` new <w:gridCol> children.
        for _ in range(actual_cols):
            gc = OxmlElement("w:gridCol")
            gc.set(qn("w:w"), str(col_w))
            grid_cols_elem.append(gc)

    # Step 6: Emit exactly len(new_rows) rows, each with exactly
    # `actual_cols` cells. Every cell gets an explicit <w:tcW> width
    # (no more `w=None` cells).
    rows_to_emit = new_rows
    for row in rows_to_emit:
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
            new_r = OxmlElement("w:r")
            new_t = OxmlElement("w:t")
            new_t.set(qn("xml:space"), "preserve")
            cell_text = row[ci] if ci < len(row) else ""
            # FIX (Phase I.4): strip leading bullet characters from
            # individual cell text so table cells don't show leftover
            # `•`, `◦`, or `o` glyphs that came from a numbered/lettered
            # list in the source PDF.
            cell_text = _strip_leading_bullet(cell_text)
            new_t.text = cell_text
            new_r.append(new_t)
            new_p.append(new_r)
            tc.append(new_p)
        table_elem.append(tr)
    return len(rows_to_emit)


# Match leading bullet characters at the start of a cell/line. We
# accept:
#   - Unicode bullets: • ◦ ○ ● ◉ ◎ ◌ ▫ ▪ ▸ ‣ ⁃ ⁌ ⁍
#   - ASCII 'o' or 'O' ONLY when followed by ` <space><Capital>` (i.e.
#     it's a list-item marker, not part of a word like "or" or "Oct").
#   - ASCII '*' ONLY at line-start followed by space.
#   - ASCII '-' is intentionally NOT matched (it appears in normal
#     text such as "Tier 1 - Spot Award").
_LEADING_BULLET_RE = re.compile(
    r"^\s*"
    r"(?:"
    r"[•◦○●◉◎◌▫▪▸‣⁃⁌⁍]"
    r"|(?<![A-Za-z0-9])[oO](?= [A-Z])"
    r"|\*(?= )"
    r")"
    r"\s*"
)


def _strip_leading_bullet(text: str) -> str:
    """Remove a leading bullet glyph + trailing whitespace from a single
    line/table-cell string. Examples:
      '• Tier 1 …'        -> 'Tier 1 …'
      'o  Touching …'     -> 'Touching …'   ('o' matches as bullet)
      'o Requests for …'  -> 'Requests for …'
      '  *  Item'         -> 'Item'         ('*' followed by space)
      'Tier 1 - Spot Award' -> unchanged   ('-' not a bullet)
      'Oct 2025'          -> unchanged     (preceded by alpha — no bullet)
    Operates on a SINGLE line at a time. We only strip ONE leading
    bullet per line (multi-line cells like 'line1\\n• line2' get their
    second line stripped only if you call us on each line)."""
    if not text:
        return text
    if "\n" in text:
        # Cell wraps multiple lines; strip each independently.
        return "\n".join(_strip_leading_bullet(line) for line in text.split("\n"))
    m = _LEADING_BULLET_RE.match(text)
    if m:
        return text[m.end():]
    return text


def _count_table_rows_cols(table_elem) -> tuple[int, int]:
    rows = len(table_elem.findall(qn("w:tr")))
    cols = 0
    if rows > 0:
        first_tr = table_elem.findall(qn("w:tr"))[0]
        cols = len(first_tr.findall(qn("w:tc")))
    return rows, cols


def _brain_table_dimensions(brain_path: Path, slot_id: int) -> tuple[int, int] | None:
    doc = Document(str(brain_path))
    initial = list(doc.element.body)
    info = BRAIN_SLOT_RANGES.get(slot_id, {})
    for i in info.get("body_items", []):
        if i < len(initial) and initial[i].tag.split("}")[-1] == "tbl":
            return _count_table_rows_cols(initial[i])
    return None


def _build_table_from_data(
    rows: list[list[str]],
    target_cols: int,
    doc: Document,
) -> object:
    """Build a <w:tbl> element from a 2D list of cell strings.

    Creates a new table with the given number of columns, where each
    cell contains the corresponding string. Returns the root <w:tbl>
    element. The table uses the same styling as the Brain template's
    prose paragraphs.
    """
    tbl = OxmlElement("w:tbl")
    # Table properties: basic, no special borders
    tblPr = OxmlElement("w:tblPr")
    tblW = OxmlElement("w:tblW")
    tblW.set(qn("w:w"), "5000")
    tblW.set(qn("w:type"), "pct")
    tblPr.append(tblW)
    tblBorders = OxmlElement("w:tblBorders")
    for border_name in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = OxmlElement(f"w:{border_name}")
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), "4")
        b.set(qn("w:color"), "auto")
        tblBorders.append(b)
    tblPr.append(tblBorders)
    tbl.append(tblPr)
    # Table grid (column widths)
    tblGrid = OxmlElement("w:tblGrid")
    for _ in range(target_cols):
        gc = OxmlElement("w:gridCol")
        gc.set(qn("w:w"), str(int(9000 / target_cols)))
        tblGrid.append(gc)
    tbl.append(tblGrid)
    # Rows
    for row_data in rows:
        tr = OxmlElement("w:tr")
        for col_idx in range(target_cols):
            tc = OxmlElement("w:tc")
            tcPr = OxmlElement("w:tcPr")
            tcW = OxmlElement("w:tcW")
            tcW.set(qn("w:w"), str(int(9000 / target_cols)))
            tcW.set(qn("w:type"), "dxa")
            tcPr.append(tcW)
            tc.append(tcPr)
            # Cell content: a paragraph with the cell text
            cell_text = row_data[col_idx] if col_idx < len(row_data) else ""
            p = OxmlElement("w:p")
            pPr = OxmlElement("w:pPr")
            pStyle = OxmlElement("w:pStyle")
            pStyle.set(qn("w:val"), "Normal")
            pPr.append(pStyle)
            p.append(pPr)
            r = OxmlElement("w:r")
            t = OxmlElement("w:t")
            t.set(qn("xml:space"), "preserve")
            t.text = str(cell_text) if cell_text else ""
            r.append(t)
            p.append(r)
            tc.append(p)
            tr.append(tc)
        tbl.append(tr)
    return tbl


def _append_extra_tables_after(
    anchor_tbl_elem,
    extra_tables: list,
    doc: Document,
) -> None:
    """Phase K.1 — append extra tables as separate <w:tbl> elements
    after `anchor_tbl_elem`, separated by spacer paragraphs.

    Used when a slot has more than one source table (e.g. multiple
    exclusion tables or tier breakdowns). Each extra table is built
    from its rows and inserted directly after the previous table.
    """
    parent = anchor_tbl_elem.getparent()
    if parent is None:
        return
    # Find position immediately after anchor_tbl_elem.
    try:
        insert_pos = list(parent).index(anchor_tbl_elem) + 1
    except ValueError:
        return
    for extra in extra_tables:
        if not extra or not extra[0]:
            continue
        extra_cols = max(len(r) for r in extra)
        if extra_cols < 1:
            continue
        extra_tbl = _build_table_from_data(extra, extra_cols, doc)
        parent.insert(insert_pos, extra_tbl)
        # Spacer paragraph between tables.
        spacer = OxmlElement("w:p")
        parent.insert(insert_pos + 1, spacer)
        insert_pos += 2


def _insert_prose_slot_table(
    doc: Document,
    sec_id: int,
    slot,
    heading_elem,
    brain_path: Path,
) -> None:
    """Insert a source table into a prose slot that doesn't have one
    in the Brain template.

    Called when the input file has a table in a section where the Brain
    has prose (e.g., Exclusions has a table in the source but not in
    the Brain). We CREATE a new <w:tbl> element from the source data
    and insert it after the slot's heading paragraph.

    Phase K.1: If the slot has multiple tables (content_tables[1:]),
    each additional table is appended as a separate <w:tbl> element
    after the first, separated by a spacer paragraph.
    """
    if not slot.content_tables:
        return
    source_table = slot.content_tables[0]
    if not source_table or not source_table[0]:
        return
    # Determine column count from the source table's first row.
    target_cols = max(len(r) for r in source_table)
    if target_cols < 1:
        return
    # Build the new table element.
    new_tbl = _build_table_from_data(source_table, target_cols, doc)
    # Insert after the heading paragraph.
    if heading_elem is not None and heading_elem.getparent() is not None:
        parent = heading_elem.getparent()
        try:
            insert_pos = list(parent).index(heading_elem) + 1
        except ValueError:
            return
        parent.insert(insert_pos, new_tbl)
        # Also insert a blank paragraph after the table for spacing.
        spacer = OxmlElement("w:p")
        parent.insert(insert_pos + 1, spacer)
        insert_pos += 2

        # Phase K.1: append any extra tables (content_tables[1:]).
        for extra in slot.content_tables[1:]:
            if not extra or not extra[0]:
                continue
            extra_cols = max(len(r) for r in extra)
            if extra_cols < 1:
                continue
            extra_tbl = _build_table_from_data(extra, extra_cols, doc)
            parent.insert(insert_pos, extra_tbl)
            # Spacer paragraph between tables.
            spacer2 = OxmlElement("w:p")
            parent.insert(insert_pos + 1, spacer2)
            insert_pos += 2


def _render_slot(doc, sec_id: int, slot, brain_path: Path, children):
    """Render a single slot using its original element references."""
    info = BRAIN_SLOT_RANGES.get(sec_id)
    if not info:
        return
    body_items = info["body_items"]
    if not body_items:
        return

    # Phase 7: Skip slots 1, 2, 3, 4, 11 body overwrite because
    # `_apply_brain_label_rows` already handled their label-rows by
    # editing runs in-place. Re-running `_set_paragraph_text` here
    # would erase that work.
    if sec_id in (1, 2, 3, 4, 11):
        return

    # Get heading element by original index
    heading_idx = body_items[0]
    if heading_idx >= len(children):
        return
    heading_elem = children[heading_idx]
    if heading_elem.tag.split("}")[-1] != "p":
        # Heading slot points at a non-paragraph element (e.g., sectPr).
        # Skip slot rendering for this slot.
        return

    # Get body elements
    para_items = []
    table_items = []
    for i in body_items[1:]:
        if i >= len(children):
            continue
        e = children[i]
        local_tag = e.tag.split("}")[-1]
        # Defensive: skip non-content elements (e.g., sectPr)
        if local_tag == "tbl":
            table_items.append(e)
        elif local_tag == "p":
            para_items.append(e)
        # else: ignore — e.g., sectPr, sdt, etc.

    # Determine new content
    if slot is not None and slot.status == "Found":
        new_lines = list(slot.content_paragraphs)
    else:
        new_lines = []
    cap = get_slot_capacity(sec_id)
    new_lines = new_lines[:cap]

    # FIX (Phase I.3a — history-duplication fix): when a Found table
    # slot (slot 10 or 14) ALSO has `content_paragraphs` populated
    # (analyzer double-routed the same content into both), the body
    # paragraphs are written BELOW the table by inserting after
    # para_items[-1]. This caused the same HISTORY row content to
    # render twice in the output: once as a table row and once as
    # body paragraphs. To prevent this, for slots 10 and 14 with
    # content_tables, we drop the body paragraphs (the table IS the
    # body's content). For non-table slots, behaviour is unchanged.
    #
    # FIX (Phase K.1 — extend dedup to slot 9): Slot 9 (Exclusions)
    # can also have BOTH prose AND a table (e.g. Hospital PDF).
    # Without this fix, slot 9 would render the prose paragraph
    # AND the table, producing duplicate content. Apply the same
    # dedup rule: drop prose when a table is present.
    if (
        sec_id in (9, 10, 14)
        and slot is not None
        and slot.status == "Found"
        and slot.content_tables
    ):
        new_lines = []	

    # Replace paragraphs (in-place overwrites + insert extras)
    for i, e in enumerate(para_items):
        if e.getparent() is None:
            continue
        if i < len(new_lines):
            _set_paragraph_text(e, new_lines[i])
        else:
            _set_paragraph_text(e, "")

    # FIX (Phase J.1 — your chosen Option 1): trim trailing empty
    # placeholders that the Brain template carried over from its
    # example body. Award slot 9 (Exclusions) has `body_items = [35,
    # 36, 37, 38, 39]` (1 heading + 4 body placeholders) but the
    # source has only 1 body line. The 3 unfilled placeholders were
    # rendered as visible blank lines in the .docx. Delete ONLY the
    # trailing empty placeholders (`extras`) to clean up the output
    # without touching filled content or headings.
    #
    # IMPORTANT: Reserve at least one trailing placeholder for slots
    # that are Skipped (no `content_paragraphs`). The post-pass in
    # `render()` writes the `Data is not found in source file`
    # placeholder into the FIRST body paragraph of Skipped slots; if
    # we deleted all of them here, the post-pass would have no
    # `first_p` to write into, and the validator (which requires a
    # placeholder for tier-1/2 Skipped slots) would fail. So for
    # `status != "Found"` slots, we KEEP exactly one trailing
    # placeholder and delete the rest.
    extras = para_items[len(new_lines):]
    is_skipped = slot is None or slot.status != "Found"
    if is_skipped and extras:
        # Keep the LAST one, delete the rest.
        keeper = extras[-1]
        for e in extras[:-1]:
            if e.getparent() is None:
                continue
            txt = "".join(t.text or "" for t in e.iter(qn("w:t")))
            if txt.strip():
                continue
            e.getparent().remove(e)
    else:
        # Found slot — safe to delete all trailing empty placeholders.
        for e in extras:
            if e.getparent() is None:
                continue
            txt = "".join(t.text or "" for t in e.iter(qn("w:t")))
            if txt.strip():
                continue
            e.getparent().remove(e)

    # Insert extras after the last paragraph element
    if para_items and len(new_lines) > len(para_items):
        last_para = para_items[-1]
        if last_para.getparent() is not None:
            parent = last_para.getparent()
            pPr_template = last_para.find(qn("w:pPr"))
            try:
                insert_pos = list(parent).index(last_para) + 1
            except ValueError:
                insert_pos = len(list(parent))
            for line in new_lines[len(para_items):]:
                new_p = _make_paragraph(pPr_template, line)
                parent.insert(insert_pos, new_p)
                insert_pos += 1

    # Strip heading example value (text after ':' or '\t') for all non-image slots
    if sec_id != 1 and heading_elem.getparent() is not None:
        heading_text = "".join(
            (t.text or "") for t in heading_elem.iter(qn("w:t"))
        )
        stripped = _strip_heading_label(heading_text)
        if stripped != heading_text.strip() and stripped:
            _set_paragraph_text(heading_elem, stripped)
    elif sec_id == 1 and slot is not None and slot.status != "Found":
        # Slot 1 (Header): if not Found, clear heading too
        if heading_elem.getparent() is not None:
            _set_paragraph_text(heading_elem, "")

    # Handle table
    for table_elem in table_items:
        if slot is not None and slot.status == "Found" and slot.content_tables:
            dims = _brain_table_dimensions(brain_path, sec_id)
            if dims:
                target_rows, target_cols = dims
            else:
                target_rows, target_cols = len(slot.content_tables[0]), max(
                    (len(r) for r in slot.content_tables[0]), default=1
                )
            # If the source table has MORE rows or MORE columns than the
            # Brain template, grow the target. This preserves the source's
            # full data without truncation.
            src_rows = len(slot.content_tables[0])
            src_cols = max(
                (len(r) for r in slot.content_tables[0]), default=1
            )
            if src_rows > target_rows:
                target_rows = src_rows
            if src_cols > target_cols:
                target_cols = src_cols
            _replace_table_element(
                table_elem, slot.content_tables[0], target_rows, target_cols
            )
            # Phase K.1 — multi-table passthrough: if the slot has
            # additional tables (content_tables[1:]), append each
            # as a separate <w:tbl> element after the main one,
            # separated by a spacer paragraph. This preserves
            # multi-table input (e.g. multiple exclusion tables or
            # tier breakdowns) without truncating.
            if len(slot.content_tables) > 1 and table_elem.getparent() is not None:
                _append_extra_tables_after(
                    table_elem, slot.content_tables[1:], doc
                )
        elif slot is not None and slot.status == "Found":
            # Phase K.6: slot is "Found" but has no source table —
            # the source provided prose for this slot. The Brain's
            # placeholder table should NOT be rendered as a "Data
            # is not found" marker (the slot HAS data, just not in
            # table form). Remove the placeholder table so only
            # the prose body is shown.
            if table_elem.getparent() is not None:
                table_elem.getparent().remove(table_elem)
        else:
            # FIX (Phase I.3c): when a table slot has NO source data
            # (Found = False, or status = Skipped), the previous code
            # filled EVERY Brain placeholder row with the marker
            # "Data is not found in source file". This produced a
            # visible table of 5-7 × "Data is not found" markers in
            # the output .docx (e.g., Sexual Harassment HISTORY: 7
            # empty rows × 2 cols of identical marker text). Reduce
            # this to a SINGLE placeholder row that spans all
            # columns, and DELETE the leftover Brain template rows
            # beyond that single row. Net visible result: one
            # centered "Data is not found in source file" row.
            #
            # Phase T add-on: rebuild the <w:tblGrid> to drop the
            # 236-twip Brain spacer column (otherwise the underlying
            # grid still has 5 cols, which trips the no-extra-column
            # regression test even though the cell uses gridSpan to
            # merge them visually).
            from .style import render_table_no_data_placeholder
            existing_trs = table_elem.findall(qn("w:tr"))
            # Delete all existing rows first
            for tr in existing_trs:
                if tr.getparent() is not None:
                    tr.getparent().remove(tr)
            # Build ONE row with a single cell spanning all columns
            # of the original first row.
            if existing_trs:
                # Compute total width by summing existing grid columns.
                grid_cols_elem = table_elem.find(qn("w:tblGrid"))
                col_widths: list[int] = []
                if grid_cols_elem is not None:
                    for gc in grid_cols_elem.findall(qn("w:gridCol")):
                        try:
                            w = gc.get(qn("w:w"))
                            if w is not None and w != "":
                                col_widths.append(int(w))
                        except (TypeError, ValueError):
                            pass
                # Phase T: rebuild the <w:tblGrid> with a single
                # column whose width is the SUM of all original
                # column widths. This eliminates the 236-twip spacer
                # column from the grid (the spanning cell needs only
                # one gridCol since gridSpan merges them all).
                total_dxa = sum(col_widths) if col_widths else 0
                if grid_cols_elem is not None:
                    for gc in grid_cols_elem.findall(qn("w:gridCol")):
                        grid_cols_elem.remove(gc)
                    new_gc = OxmlElement("w:gridCol")
                    if total_dxa > 0:
                        new_gc.set(qn("w:w"), str(total_dxa))
                    grid_cols_elem.append(new_gc)
                n_cols = 1
                tr = OxmlElement("w:tr")
                tc = OxmlElement("w:tc")
                tcPr = OxmlElement("w:tcPr")
                tcW = OxmlElement("w:tcW")
                if total_dxa > 0:
                    tcW.set(qn("w:w"), str(total_dxa))
                    tcW.set(qn("w:type"), "dxa")
                tcPr.append(tcW)
                # Span across all columns (now just 1 after Phase T
                # grid rebuild, but kept for safety).
                gridSpan = OxmlElement("w:gridSpan")
                gridSpan.set(qn("w:val"), str(n_cols))
                tcPr.append(gridSpan)
                tc.append(tcPr)
                tr.append(tc)
                # Marker paragraph in the single cell
                new_p = OxmlElement("w:p")
                new_r = OxmlElement("w:r")
                new_t = OxmlElement("w:t")
                new_t.set(qn("xml:space"), "preserve")
                new_t.text = "Data is not found in source file"
                new_r.append(new_t)
                new_p.append(new_r)
                tc.append(new_p)
                render_table_no_data_placeholder(new_p)
                table_elem.append(tr)

    # Handle prose-slot tables: when the input has a table in a section
    # where the Brain has prose (e.g., Exclusions), insert that table
    # into the slot's body area. The Brain template has no <w:tbl> here,
    # so we CREATE a new table element and insert it after the heading.
    if (
        slot is not None
        and slot.status == "Found"
        and slot.content_tables
        and not table_items  # Brain template has no table in this slot
        and sec_id not in (1, 2, 3, 4, 11)  # not label-row slots
    ):
        _insert_prose_slot_table(
            doc, sec_id, slot, heading_elem, brain_path
        )

    if slot is not None:
        slot.placed_paragraphs = new_lines


def render(
    classified: ClassificationResult,
    extracted: ExtractedDocument,
    brain_path: Path,
    output_path: Path,
    *,
    header_text: str | None = None,
    header_version: str | None = None,
    field_map: dict[str, str] | None = None,
) -> Path:
    """Render output. Slots are processed in REVERSE order (last to first)
    so that index shifts from earlier-rendered slots don't affect later slots.

    `header_text`/`header_version` override the default Brain header text
    when supplied (input-derived policy title + version tag).

    `field_map` is a dict of canonical Brain labels → values, e.g.
    {"Type:": "HR", "Policy Number:": "BT-001"}. Used by slot 1
    (Header), slot 2 (Brief Description), slot 3 (Approval & Governance),
    slot 4 (Reason for Policy) and slot 11 (Policy Review Note) for
    per-label substitution. When a label has no entry, the renderer
    replaces the Brain's example value with the marker
    `Data is not found in source file` (plain body styling) — never
    leaking Brain defaults into the output.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(brain_path, output_path)
    _verify_media_against(brain_path, output_path)

    doc = Document(str(output_path))

    # Inject input-derived header text BEFORE we walk the body so we
    # don't disturb the body's structural indices. Always runs (even
    # when no input title/version is supplied) so every output has the
    # same correct header layout: title on the left (top), version on
    # the left (below the title), Brain's anchored logo on the right.
    _replace_header_text(doc, header_text or "", header_version or "")

    # Phase 7: per-label substitution across slots 1, 2, 3, 4, 11.
    # Done FIRST so subsequent slot rendering doesn't disturb the
    # label-row indices.
    #
    # When the input has a value for a label, the Brain's example value
    # is replaced with the input value (verbatim). When the input has
    # nothing for that label, the Brain's example value is REPLACED
    # with `Data is not found in source file` — never preserved as-is.
    _apply_brain_label_rows(doc, field_map or {})

    # Process slots in REVERSE order (last to first) so that insertions in
    # later slots don't shift earlier slots' indices.
    for sec_id in sorted(BRAIN_SLOT_RANGES.keys(), reverse=True):
        if sec_id == 15:
            continue
        slot = classified.sections.get(sec_id)
        # Re-snapshot children each iteration so indices are fresh
        children = list(doc.element.body)
        _render_slot(doc, sec_id, slot, brain_path, children)

    # Post-pass: For every Skipped prose slot (no input routed), the
    # Brain's own body content is REPLACED with the marker
    # `Data is not found in source file` (plain styling). The framework
    # framework (slot heading + structure) stays identical to Brain —
    # only the body content is wiped to "input-only" semantics.
    #
    # Slots 1, 2, 3, 4, 11 are label-row slots handled by
    # `_apply_brain_label_rows`; we skip them here to avoid disturbing the
    # substituted/placeholder values already written into their paragraphs.
    out_bounds = find_slot_boundaries(doc)
    for sec_id, slot in classified.sections.items():
        if sec_id in (15, 1, 2, 3, 4, 11):
            continue
        if slot.status == "Found":
            continue
        slot_elems = out_bounds.get(sec_id, {}).get("elements", [])
        if not slot_elems:
            continue
        heading = slot_elems[0]
        if heading.tag.split("}")[-1] == "p":
            heading_text = "".join((t.text or "") for t in heading.iter(qn("w:t")))
            if any(sep in heading_text for sep in (":", "\t")):
                stripped = _strip_heading_label(heading_text)
                if stripped:
                    _set_paragraph_text(heading, stripped + ":")
        # For prose slots, wipe the Brain's example body and place the
        # marker in the first body paragraph (plain body styling).
        # Tables (slots 10, 14) keep their structure but every cell gets
        # the unified marker via the regular `_render_slot` table branch.
        from .style import render_not_found_placeholder
        from .framework.slot_tiers import SLOT_TIERS, slot_label
        if sec_id in (10, 14):
            # Skip — handled by `_render_slot` table branch.
            continue
        if SLOT_TIERS.get(sec_id, 3) >= 1:
            body_records = slot_elems[1:]
            first_p = None
            for e in body_records:
                if e.tag.split("}")[-1] == "p":
                    first_p = e
                    break
            label = slot_label(sec_id)
            if first_p is not None:
                render_not_found_placeholder(first_p, label)
                # Remove sibling paragraphs (keep just the marker).
                parent = first_p.getparent()
                if parent is not None:
                    for e in body_records:
                        if e is first_p:
                            continue
                        local_tag = e.tag.split("}")[-1]
                        if local_tag == "p":
                            if e in list(parent):
                                parent.remove(e)

    # Strip paragraphs that are EITHER pure-digit body/header paragraphs
    # (extraction artifacts like "31756133100", "08281400", etc.) OR are
    # drawing-only paragraphs that contain no `<w:t>` text but DO have
    # `<w:drawing>` shapes (connector lines / decorative shapes from the
    # Brain template whose `<wp:posOffset>` digit values get exposed as
    # "numbers" when the XML is naively tag-stripped by the web preview).
    #
    # The Brain template contains connector-shape paragraphs between the
    # label rows (e.g. between "Functional Area(s):" and "Brief Description:")
    # whose visible content in Word is empty (a thin horizontal line), but
    # whose XML contains `<wp:posOffset>3175</wp:posOffset>` and
    # `<wp:posOffset>61331</wp:posOffset>` — and stripping all tags
    # concatenates those digits as "31756133100". These paragraphs are
    # visual separators that have NO semantic content and should not
    # appear in the output at all (preview or docx).
    #
    # This runs AFTER all slot rendering + the Skipped-slot post-pass so
    # that `_render_slot` cannot re-add these paragraphs.
    import re as _re
    _PURE_DIGIT_RE = _re.compile(r"^\d+$")

    def _strip_artifact_paragraphs(parent_el):
        if parent_el is None:
            return
        for _p in list(parent_el):
            if not _p.tag.endswith("}p"):
                continue
            # Real text content (only `<w:t>` elements, not `<wp:posOffset>`).
            _ptext = "".join((_t.text or "") for _t in _p.iter(qn("w:t"))).strip()
            # Drawing/shape element — connector lines, decorative shapes.
            # The Brain template wraps drawing elements in
            # `<mc:AlternateContent><mc:Choice Requires="wps"><w:drawing>`
            # (markup-compatibility wrapper), so a simple `find` for
            # `w:drawing` misses them. Walk the tree with `iter()` and
            # also count `<w:r>` runs that hold AlternateContent.
            _has_drawing = False
            for _el in _p.iter():
                _tag = _el.tag
                if _tag == qn("w:drawing"):
                    _has_drawing = True
                    break
                # Wordprocessing shape (connector line) inside AlternateContent
                if _tag.endswith("}wsp") or _tag.endswith("}wpg") or _tag.endswith("}pic"):
                    _has_drawing = True
                    break
            # Case 1: paragraph text is purely digits (artifact).
            if _PURE_DIGIT_RE.match(_ptext):
                parent_el.remove(_p)
                continue
            # Case 2: paragraph is drawing-only (empty text but has a
            # drawing/shape). These are decorative connector lines from
            # the Brain template that show as concatenated digits in the
            # preview when tags are stripped.
            if _has_drawing and not _ptext:
                parent_el.remove(_p)
                continue

    _strip_artifact_paragraphs(doc.element.body)
    # Header sections (w:hdr) — same cleanup applies.
    for _sect in doc.element.iter(qn("w:headerReference")):
        _r_id = _sect.get(qn("r:id")) or _sect.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        if not _r_id:
            continue
        _rel = doc.part.rels.get(_r_id)
        if _rel is None:
            continue
        _hdr_part = _rel.target_part
        if _hdr_part is not None:
            _strip_artifact_paragraphs(_hdr_part.element)

    # Enforce terminal period (`.`) on ALL prose paragraphs in every
    # prose slot (5, 6, 7, 8, 9, 12, 13). Input content may end mid-sentence
    # because the source PDF/DOCX wraps the line visually but the sentence
    # continues elsewhere. The output must read as complete sentences, so
    # every prose paragraph that does not already end with terminal
    # punctuation (`.`, `!`, `?`) is suffixed with `.`.
    #
    # Rules:
    #   - Skipped slots (filled with `Data is not found in source file`)
    #     already end with `.`, so they are naturally compliant.
    #   - Label-row slots (1, 2, 3, 4, 11) and table slots (10, 14) are
    #     excluded — they are not prose.
    #   - Short fragments (< 3 words) and sub-headings (< 30 chars) are
    #     left alone — they may be intentional fragments or section
    #     labels that should not get a period.
    #   - The slot heading (first element) is excluded — it's a label,
    #     not prose.
    _TERMINAL_PUNCT_RE = _re.compile(r"[.!?]\s*$")
    _PROSE_SLOTS_FOR_TERMINATION = {5, 6, 7, 8, 9, 12, 13}
    _MIN_PROSE_WORD_COUNT = 3
    _MIN_PROSE_CHAR_COUNT = 30
    _bounds_for_term = find_slot_boundaries(doc)
    for _sec_id, _sec_info in _bounds_for_term.items():
        if _sec_id not in _PROSE_SLOTS_FOR_TERMINATION:
            continue
        _elems = _sec_info.get("elements", [])
        if len(_elems) < 2:
            continue
        # Walk ALL body paragraphs (skip heading at [0])
        _body_records = _elems[1:]
        for _p in _body_records:
            if not _p.tag.split("}")[-1] == "p":
                continue
            # Get full paragraph text (concatenated across runs)
            _ptext = "".join((_t.text or "") for _t in _p.iter(qn("w:t"))).strip()
            if not _ptext:
                continue
            if _ptext == "Data is not found in source file":
                continue
            if _TERMINAL_PUNCT_RE.search(_ptext):
                continue
            # Skip short fragments (< 3 words) and sub-headings (< 30 chars)
            # — these may be intentional fragments or section labels.
            _word_count = len(_ptext.split())
            if _word_count < _MIN_PROSE_WORD_COUNT:
                continue
            if len(_ptext) < _MIN_PROSE_CHAR_COUNT:
                continue
            # Append `.` to the last <w:t> in the last run.
            _t_elems = list(_p.iter(qn("w:t")))
            if not _t_elems:
                continue
            _last_t = _t_elems[-1]
            _last_t.text = (_last_t.text or "").rstrip() + "."

    # Apply styles + page-break-before per slot.
    _apply_post_render_styling(doc, classified)

    # Apply bullet substitution + Example handling at the document level
    # (after slot styling). This walks the body and:
    #   1. Replaces `•` and `◦` with filled-black `●` inline.
    #   2. Bolds every `●` character via run-splitting.
    #   3. Detects paragraphs beginning with `Example:` / `Example -` and
    #      prepends `● `.
    _apply_bullet_and_example_polish(doc, classified)

    # Final Word-format normalization pass:
    #   - Force justify on every body paragraph.
    #   - Strip inherited bold from body data runs (label bold preserved).
    #   - Force A4 page size on every <w:sectPr>.
    #   - Re-assert Calibri 10pt and 1.5 line + 4pt before/after.
    _normalize_word_format(doc)

    if classified.sections:
        all_text_chunks = []
        for sec_id, slot in sorted(classified.sections.items()):
            if slot and getattr(slot, "chunk_text", None):
                all_text_chunks.append(
                    f"[Slot {sec_id}] {slot.chunk_text}"
                )
        if all_text_chunks:
            log_checkpoint(
                "before_docx_write",
                "\n\n".join(all_text_chunks),
                run_id=output_path.stem,
            )

    doc.save(str(output_path))
    _verify_media_against(brain_path, output_path)
    _restore_media_store_compression(output_path)


def _render_required_field_placeholder(sec_id: int, slot_elems: list,
                                       tier: int, records: list[dict]) -> None:
    """Wipe the slot body and place a NOT FOUND IN SOURCE placeholder.

    Heading remains visible. For tables (slot 10 Award, slot 14 History)
    we keep the table but empty cells.
    """
    label = slot_label(sec_id)
    body_records: list = slot_elems[1:] if slot_elems else []
    # First body paragraph gets the placeholder text.
    first_p = None
    for e in body_records:
        if e.tag.split("}")[-1] == "p":
            first_p = e
            break
    if first_p is None:
        # No body paragraph exists — append a new placeholder paragraph.
        from .style import render_not_found_placeholder
        # Find the heading's parent and append after.
        if slot_elems:
            heading = slot_elems[0]
            parent = heading.getparent()
            if parent is not None:
                from docx.oxml import OxmlElement
                new_p = OxmlElement("w:p")
                parent.insert(list(parent).index(heading) + 1, new_p)
                render_not_found_placeholder(new_p, label)
    else:
        from .style import render_not_found_placeholder
        render_not_found_placeholder(first_p, label)
    # Remove any remaining body paragraphs (keep just the placeholder).
    for e in body_records:
        if e is first_p:
            continue
        local_tag = e.tag.split("}")[-1]
        parent = e.getparent()
        if parent is None:
            continue
        if local_tag == "p":
            parent.remove(e)
        elif local_tag == "tbl":
            # Tables for structural slots: keep + empty cells.
            from .framework.slot_tiers import SLOT_TIERS
            if SLOT_TIERS.get(sec_id, 3) in (1, 2) and sec_id in (10, 14):
                for tr in e.findall(qn("w:tr")):
                    for tc in tr.findall(qn("w:tc")):
                        for p in tc.findall(qn("w:p")):
                            for r in p.findall(qn("w:r")):
                                for t in r.findall(qn("w:t")):
                                    t.text = ""
            else:
                parent.remove(e)
    records.append({
        "slot_id": sec_id,
        "tier": tier,
        "label": label,
    })


def _apply_bullet_and_example_polish(doc, classified) -> None:
    """Apply inline bullet substitution + Example handling to body text.

    Walks every <w:p> in body, skips tables, and:
      1. Replaces `•` `◦` → `●` (filled-black Unicode glyph).
      2. Does NOT apply any auto-bolding — character formatting is whatever
         the Brain already has.
      3. Prepends `● ` to paragraphs that begin with `Example:` (kept) or
         `Example -` / `Example —` / `Example --` (stripped).
    """
    from .style import (
        handle_example_prefix,
        replace_bullets_with_filled,
    )
    body = doc.element.body
    for p in body.iter(qn("w:p")):
        # Skip if parent is a table cell (don't touch Brain structural bullets).
        parent = p.getparent()
        if parent is None:
            continue
        ancestor_names = []
        cur = parent
        while cur is not None:
            ancestor_names.append(cur.tag.split("}")[-1])
            cur = cur.getparent()
        if "tc" in ancestor_names:
            continue
        replace_bullets_with_filled(p)
        handle_example_prefix(p)


# ---------------------------------------------------------------------------
# Helpers used by render()
# ---------------------------------------------------------------------------


def _is_empty_slot(slot_elems: list) -> bool:
    """True if every body element of the slot is empty / bullet-only.

    The first element is the heading; if it's followed by paragraphs that
    contain only bullet characters or whitespace, the slot has no real
    content and is treated as empty.
    """
    if not slot_elems:
        return False
    body_items = list(slot_elems[1:])
    if not body_items:
        return False
    for e in body_items:
        local_tag = e.tag.split("}")[-1]
        if local_tag == "p":
            text = "".join((t.text or "") for t in e.iter(qn("w:t"))).strip()
            # Real prose paragraph: at least one alpha char
            if any(ch.isalpha() for ch in text) and len(text) > 12:
                return False
        elif local_tag == "tbl":
            # Has table content
            for t in e.iter(qn("w:t")):
                if t.text and t.text.strip():
                    return False
    return True


# Slots whose tables are structurally part of the framework — never
# removed even if "empty" (so reviewers can fill them later).
_STRUCTURAL_TABLE_SLOTS: set[int] = {10, 14}


def _remove_empty_slot_block(sec_id: int, slot_elems: list) -> None:
    """Remove the empty-slot block from the document.

    For non-structural slots (most cases), this removes the heading
    paragraph AND every body element, leaving the document with one
    blank paragraph in their place so the surrounding slots don't
    visually crash together.

    For structural slots (10 = Award, 14 = History), the heading is
    preserved (so the slot boundary is still detectable by validators)
    and the structural table is preserved with emptied cells.
    """
    structural = sec_id in _STRUCTURAL_TABLE_SLOTS
    body_anchor = None
    for e in list(slot_elems):
        local_tag = e.tag.split("}")[-1]
        parent = e.getparent()
        if parent is None:
            continue
        if local_tag == "p":
            if body_anchor is None:
                body_anchor = parent
                # First paragraph slot. For non-structural slots we drop
                # the heading; for structural slots we keep it as the
                # anchor for the validator to find.
                if structural:
                    # Restore the heading text in case we wiped it earlier.
                    heading_text = _slot_heading_label_for_slot(sec_id)
                    _set_paragraph_text(e, heading_text or "")
                else:
                    _set_paragraph_text(e, "")
            else:
                parent.remove(e)
        elif local_tag == "tbl":
            if structural:
                # Empty the cells but keep the table.
                for tr in e.findall(qn("w:tr")):
                    for tc in tr.findall(qn("w:tc")):
                        for p in tc.findall(qn("w:p")):
                            for r in p.findall(qn("w:r")):
                                for t in r.findall(qn("w:t")):
                                    t.text = ""
            else:
                parent.remove(e)


def _slot_heading_label_for_slot(sec_id: int) -> str | None:
    """Return the canonical heading label for sec_id from the slot map."""
    from .framework.brain_slot_map import SLOT_HEADINGS
    return SLOT_HEADINGS.get(sec_id)


def _replace_header_text(doc, title: str, version: str) -> None:
    """Rewrite the header text in every output so it matches the Brain
    template's structure exactly.

    The Brain's header2.xml layout is:
      Paragraph 1:  [Title text] <tab> <inline image=logo>
      Paragraph 2:  [Version text] <ptab> <anchor straight-connector line>

    We only swap the text content inside the existing brackets. The
    Brain's image and anchor elements are left untouched, so the logo
    stays inline after the tab (visually on the right of the title
    line) and the straight-connector line stays anchored across the
    header width.

    Strict policy: header text comes ONLY from the explicit body
    `Policy Title:` / `Policy Number:` line — there is no fallback to
    a heuristic-extracted title, no fallback to a hard-coded
    "POLICY TEMPLATE" string, and no fallback to "CL&H_02/24".

    When `title` / `version` is empty (the body has no explicit line
    for them) the bracketed slot is written empty so the final
    header shows nothing in that position. The Brain's logo and
    straight-connector line are still preserved.
    """
    # Resolve text values — NO FALLBACK to defaults. Empty in → empty out.
    title_text = str(title).strip() if title else ""
    version_text = str(version).strip() if version else ""

    if not doc.sections:
        return

    for section in doc.sections:
        for header_obj in (section.header, section.first_page_header, section.even_page_header):
            if not hasattr(header_obj, "paragraphs"):
                continue
            _rewrite_header_paragraphs(header_obj, title_text, version_text)


def _rewrite_header_paragraphs(header_obj, title_text: str, version_text: str) -> None:
    """Swap the bracketed text content in the Brain's header paragraphs.

    The Brain's header2.xml has two paragraphs:
      P1: [ <title runs> ] <tab> <inline image>
      P2: [ <version runs> ] <ptab> <anchor line>

    We walk each paragraph and rewrite ONLY the text inside the
    surrounding brackets — leaving all non-text elements (tab,
    drawing, ptab, anchor) untouched. This preserves the Brain's
    inline image and anchored line, so the logo stays on the right
    of the title line and the line stays anchored across the header.
    """
    body = header_obj._element  # the <w:hdr> element
    paragraphs = list(body.findall(qn("w:p")))

    # The title paragraph is the first <w:p> that contains a <w:drawing>
    # (the inline logo image). The version paragraph is the first <w:p>
    # that contains a <w:ptab> (the position-tab that anchors the
    # straight-connector line).
    title_p = None
    version_p = None
    for p in paragraphs:
        if title_p is None and p.find(".//" + qn("w:drawing")) is not None:
            title_p = p
            continue
        if version_p is None and p.find(".//" + qn("w:ptab")) is not None:
            version_p = p

    # Fallback: if the structure is missing, use the first two body
    # paragraphs.
    if title_p is None and len(paragraphs) >= 1:
        title_p = paragraphs[0]
    if version_p is None and len(paragraphs) >= 2:
        version_p = paragraphs[1]

    if title_p is not None:
        _swap_bracketed_text(title_p, title_text, add_brackets=True)
    if version_p is not None:
        _swap_bracketed_text(version_p, version_text, add_brackets=True)


def _swap_bracketed_text(p_elem, new_text: str, *, add_brackets: bool) -> None:
    """Find the runs inside the leading "[" and trailing "]" of a
    header paragraph and replace them with a single run holding
    `new_text`. Tab, drawing, ptab, and anchor elements are kept
    untouched.
    """
    children = list(p_elem)
    if not children:
        return

    # Find the index of the run that contains the opening "[" and the
    # index of the run that contains the closing "]". We work on
    # p_elem's direct children so we can preserve non-<w:r> elements.
    open_idx = None
    close_idx = None
    for i, child in enumerate(children):
        if not child.tag.endswith("}r"):
            continue
        text = "".join((t.text or "") for t in child.findall(qn("w:t")))
        if open_idx is None and "[" in text:
            open_idx = i
        if "]" in text:
            close_idx = i
            break

    if open_idx is None:
        return
    if close_idx is None:
        # No closing "]" found in this paragraph — treat the rest of
        # the paragraph as part of the bracketed text.
        close_idx = len(children) - 1

    # Build a new text run with Calibri 10pt (matches Brain's body
    # styling directive).
    new_r = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    rFonts = OxmlElement("w:rFonts")
    rFonts.set(qn("w:ascii"), "Calibri")
    rFonts.set(qn("w:hAnsi"), "Calibri")
    rFonts.set(qn("w:cs"), "Calibri")
    rPr.append(rFonts)
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), "20")
    rPr.append(sz)
    szCs = OxmlElement("w:szCs")
    szCs.set(qn("w:val"), "20")
    rPr.append(szCs)
    new_r.append(rPr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = ("[" + new_text + "]") if add_brackets else new_text
    new_r.append(t)

    # Remove every child in [open_idx, close_idx] inclusive, but ONLY
    # remove <w:r> children. Non-<w:r> children (tab, drawing, ptab,
    # anchor in <mc:AlternateContent>, etc.) are kept untouched.
    for i in range(close_idx, open_idx - 1, -1):
        child = children[i]
        if child.tag.endswith("}r"):
            p_elem.remove(child)

    # Insert the new run at the open_idx position. Re-read the
    # current children list because the remove() calls may have
    # shifted indices.
    new_children = list(p_elem)
    if open_idx >= len(new_children):
        p_elem.append(new_r)
    else:
        p_elem.insert(open_idx, new_r)


def _apply_brain_label_rows(doc, field_map: dict[str, str]) -> None:
    """Phase 7: per-label substitution for Brain label-rows.

    Walks every body paragraph and substitutes the value portion of any
    paragraph whose leading label matches a canonical Brain label — for
    slots 1, 2, 3, 4, 11.

    Behavior:
      - When the input supplied a value for the label → replace the
        Brain's existing example value with the input value (verbatim).
      - When the input supplied no value → the Brain's example value is
        REPLACED (not preserved) with the marker
        `Data is not found in source file` (plain body styling, no
        italic, no gray). This matches the user's directive that the
        output contain only input-derived data, with the marker
        filling any gap.

    Label-row slots in this pass:
      - Slot 1: Type, Policy Title, Policy Number, Applicable Sector(s),
        Functional Area(s).
      - Slot 2: Brief Description.
      - Slot 3: Effective Date/Period, Approved by, Prepared by,
        Responsible Function(s), Responsible Function Officer(s),
        Supersedes, Last Reviewed/Updates, Applies to.
      - Slot 4: Reason for Policy.
      - Slot 11: Policy Review Note.

    We deliberately skip slot headings for slots 5, 6, 7, 8, 9, 10, 12,
    13, 14 (those use multi-paragraph body substitutes, handled by the
    normal slot-render path).
    """
    body = doc.element.body
    children = list(body)

    # Slot 5+ headings we should never rewrite as label-rows.
    NON_LABEL_SLOT_HEADINGS = (
        "INTRODUCTION",
        "POLICY STATEMENT",
        "1. Purpose",
        "2. Scope",
        "3. Exclusions",
        "4. Award",
        "DEFINITIONS",
        "RELATED POLICIES",
        "HISTORY",
    )

    for i, e in enumerate(children):
        if not e.tag.endswith("}p"):
            continue
        text = "".join((t.text or "") for t in e.iter(qn("w:t"))).strip()
        if not text:
            continue

        # Detect a label-row paragraph.
        detected = _detect_brain_label(text)
        if detected is not None:
            value = field_map.get(detected)
            _replace_label_value_in_paragraph(e, detected, value)
            continue

        # Some Brain paragraphs are slot-headings that contain a Brain
        # label inline (e.g. the slot 2 heading has the literal
        # `Brief Description` as the first token; same for slots 4, 11).
        # These show up as plain paragraphs (no value after the colon) and
        # `_detect_brain_label` skips them; we still need to write the
        # marker there when no value was supplied.
        for label_tuple in BRAIN_BRIEF_DESCRIPTION_FIELDS + BRAIN_REASON_FIELDS + BRAIN_REVIEW_NOTE_FIELDS:
            canonical, _syns = label_tuple
            if text.strip().rstrip(".").strip() == canonical.rstrip(":").strip():
                # Heading paragraph — write label + (value or marker).
                value = field_map.get(canonical)
                _replace_paragraph_with_label_value(e, canonical, value)
                break


def _index_of_first_text_matching(children, predicate, prefer_value: str = "after"):
    """Walk `children` and return index of first <w:p> whose visible
    text matches `predicate(text)`. Unused after rewrite; kept for
    future use."""
    for i, e in enumerate(children):
        if not e.tag.endswith("}p"):
            continue
        text = "".join((t.text or "") for t in e.iter(qn("w:t")))
        if predicate(text):
            return i
    return None


def _detect_brain_label(text: str) -> str | None:
    """Return the canonical Brain label iff this paragraph is a label-row.

    A label-row looks like `Label:[ value]` where `Label` is one of the
    canonical Brain labels (slot 1, 2, 3, 4, 11) and the value portion
    is optional — empty means "no value supplied yet" and the renderer
    writes the marker.
    """
    parts = re.split(r"[:\t]", text, maxsplit=1)
    if len(parts) != 2:
        return None
    head = parts[0]
    label = head.strip() + ":"
    canonical = canonical_label(label)
    if canonical is None:
        return None
    return canonical


def _replace_label_value_in_paragraph(p_elem, canonical_label: str,
                                     value: str | None) -> None:
    """Replace the value portion of a Brain label-row paragraph.

    Strategy: clear all runs (`<w:r>`), all `<w:sdt>` content controls
    (and their `sdtContent`), and other inline content. Keep `<w:pPr>`.

    Then write two runs:
      1. The canonical label + space (bold, matching Brain's heading weight).
      2. The substituted value text (when supplied) — or the marker
         `Data is not found in source file` (the marker text is appended
         directly, no embedded label) in plain body styling — no italic,
         no gray.
    """
    text = "".join((t.text or "") for t in p_elem.iter(qn("w:t")))
    if not text.strip():
        return
    # Use the canonical label (e.g., `Last Reviewed:`) — NOT the Brain's
    # extended form like `Last Reviewed/Updates:` — so the rendered
    # paragraph reads `Last Reviewed: <value>` with one canonical prefix.
    # Clear runs AND `<w:sdt>` content controls.
    for r in list(p_elem.findall(qn("w:r"))):
        p_elem.remove(r)
    for sdt in list(p_elem.findall(qn("w:sdt"))):
        p_elem.remove(sdt)
    p_elem.append(_make_text_run(canonical_label + " ", bold=True))
    if value is None or not str(value).strip():
        # Marker only — no embedded label (avoid duplication).
        marker_text = "Data is not found in source file"
        p_elem.append(_make_text_run(marker_text, bold=False, italic=False, gray=False))
    else:
        p_elem.append(_make_text_run(str(value), bold=False, italic=False, gray=False))


def _replace_paragraph_with_label_value(p_elem, canonical_label: str,
                                        value: str | None) -> None:
    """Write `<Label>: value` (or `<Label>: marker`) to a Brain heading
    paragraph that has no example value yet (e.g. slot 2/4/11 heading
    paragraphs whose entire text is just the label).
    """
    text = "".join((t.text or "") for t in p_elem.iter(qn("w:t")))
    if not text.strip():
        return
    # Clear runs.
    for r in list(p_elem.findall(qn("w:r"))):
        p_elem.remove(r)
    label_text = canonical_label
    p_elem.append(_make_text_run(label_text + " ", bold=True))
    if value is None or not str(value).strip():
        marker_text = missing_field_placeholder(canonical_label)
        p_elem.append(_make_text_run(marker_text, bold=False, italic=False, gray=False))
    else:
        p_elem.append(_make_text_run(str(value), bold=False, italic=False, gray=False))


def _make_text_run(text: str, *, bold: bool = False, italic: bool = False,
                   gray: bool = False) -> OxmlElement:
    """Create a <w:r> with given text and optional bold/italic/gray flags."""
    r = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    if bold:
        b = OxmlElement("w:b")
        rPr.append(b)
        bCs = OxmlElement("w:bCs")
        rPr.append(bCs)
    if italic:
        i = OxmlElement("w:i")
        rPr.append(i)
    if gray:
        color = OxmlElement("w:color")
        color.set(qn("w:val"), "808080")
        rPr.append(color)
    if list(rPr):
        r.append(rPr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    r.append(t)
    return r


def _make_missing_value_run(canonical_label: str) -> OxmlElement:
    """Build a run holding ` Data is not found in source file` in plain
    body styling (no italic, no gray)."""
    return _make_text_run("Data is not found in source file",
                          bold=False, italic=False, gray=False)


def _apply_post_render_styling(doc, classified: ClassificationResult) -> None:
    """For every slot body, apply Calibri Headings / 10pt body / justify /
    1.5 line / 4pt before-after.

    Per the user's directive the new Brain has zero page-breaks —
    matching it exactly. So no `<w:pageBreakBefore/>` insertion here.

    Phase 6: no paragraph splitting. Bullets are rendered inline by
    `_apply_bullet_and_example_polish` after this pass.
    """
    out_bounds = find_slot_boundaries(doc)
    sorted_slots = sorted(out_bounds.keys())
    for idx, sec_id in enumerate(sorted_slots):
        slot_elems = out_bounds.get(sec_id, {}).get("elements", [])
        if not slot_elems:
            continue
        if sec_id == 15:
            continue
        # Heading is elements[0], body elements follow.
        heading = slot_elems[0]
        body_items = []
        if heading.tag.split("}")[-1] == "p":
            body_items = slot_elems[1:]
            apply_styles_to_section([heading], heading_indices={0})
        else:
            body_items = slot_elems
        if body_items:
            apply_styles_to_section(body_items, heading_indices=set())


# A4 page dimensions (twips). 8.27 × 11.69 inches × 1440 twips/inch.
_A4_W_TWIPS = "11906"
_A4_H_TWIPS = "16838"

# Slots whose heading paragraph is to remain BOLD (kept for headings),
# but data paragraphs (body) of the same slot have bold STRIPPED.
# Heading-of-slot paragraphs (e.g. INTRODUCTION, 1. Purpose) are kept
# bold here because the user said "do as you like" — i.e., bold headings
# are acceptable, only data values must be non-bold.
# However user reaffirmed "body data not bold" — so we still strip bold
# from every body run; heading runs themselves keep their bold flag from
# the Brain template (Heading style is preserved).

def _normalize_word_format(doc) -> None:
    """Final Word-format normalization pass.

    Per the user's directive (the 'is word format every output must use'):
      - Force A4 page size on every <w:sectPr> in the document.
      - Force justify alignment on every body paragraph.
      - Strip inherited bold from BODY data runs (slot heading runs keep
        their bold flag — e.g., `INTRODUCTION`, `1. Purpose`).
      - Re-assert Calibri 10pt on body runs and 1.5 line + 4pt
        before/after spacing.
      - For runs that contain Myanmar (Burmese) text, also set the
        `Noto Sans Myanmar` font face so the DOCX renders correctly
        when opened in Word/LibreOffice (the bundled Myanmar Text font
        has cmap issues that propagate through Word's PDF generator,
        causing Unicode-level corruption; Noto Sans Myanmar uses
        modern Unicode-compliant glyph IDs).

    Run after bullet polish, just before save.
    """
    body = doc.element.body

    # 1. Force A4 on every section.
    for sect in body.iter(qn("w:sectPr")):
        pg_sz = sect.find(qn("w:pgSz"))
        if pg_sz is None:
            pg_sz = OxmlElement("w:pgSz")
            sect.insert(0, pg_sz)
        pg_sz.set(qn("w:w"), _A4_W_TWIPS)
        pg_sz.set(qn("w:h"), _A4_H_TWIPS)
        pg_sz.set(qn("w:orient"), "portrait")
        # Drop any w:code attribute (US Letter code), it's stale.
        if pg_sz.get(qn("w:code")) is not None:
            del pg_sz.attrib[qn("w:code")]

    # Identify slot heading paragraphs — those whose text is exactly one
    # of the canonical Brain slot headings. The renderer leaves the
    # `<w:b/>` flag on these (matches Brain's heading weight). All
    # other body paragraphs get bold stripped.
    from .framework.brain_slot_map import SLOT_HEADINGS
    HEADING_KEYWORDS = set()
    for kw in SLOT_HEADINGS.values():
        HEADING_KEYWORDS.add(kw.upper().strip())

    # 2. Force justify + body typography on every body paragraph.
    for p_elem in body.iter(qn("w:p")):
        # Build / fetch pPr.
        pPr = p_elem.find(qn("w:pPr"))
        if pPr is None:
            pPr = OxmlElement("w:pPr")
            p_elem.insert(0, pPr)
        # Force <w:jc w:val="both"/> (justify) on every body paragraph.
        for el in pPr.findall(qn("w:jc")):
            pPr.remove(el)
        jc = OxmlElement("w:jc")
        jc.set(qn("w:val"), "both")
        pPr.append(jc)
        # Force spacing (2.0 line + 4pt before + 4pt after).
        for el in pPr.findall(qn("w:spacing")):
            pPr.remove(el)
        spacing = OxmlElement("w:spacing")
        spacing.set(qn("w:line"), "480")
        spacing.set(qn("w:lineRule"), "auto")
        spacing.set(qn("w:before"), "80")
        spacing.set(qn("w:after"), "80")
        pPr.append(spacing)

        # Strip bold from BODY data runs (every paragraph that isn't a
        # slot heading). Slot heading paragraphs (e.g., INTRODUCTION,
        # 1. Purpose, Policy Review Note, etc.) keep their bold flag.
        para_text = "".join(
            (t.text or "") for t in p_elem.iter(qn("w:t"))
        ).strip()
        is_heading = (
            para_text.upper() in HEADING_KEYWORDS
            or any(para_text.upper().startswith(kw + ":") for kw in HEADING_KEYWORDS if kw.endswith(":"))
        )
        # Label-row paragraphs (Type:, Policy Title:, etc.) live in the
        # label-row slots 1, 2, 3, 4, 11 and were written by Phase 7
        # with explicit bold-flag toggles. Detect and preserve their bold.
        is_label_row = False
        from .framework.brain_fields import BRAIN_LABEL_ROWS
        for canonical, _ in BRAIN_LABEL_ROWS:
            if para_text.startswith(canonical) and (
                canonical == para_text
                or canonical.rstrip(":") + ": " in para_text
                or canonical.rstrip(":") == para_text
            ):
                # The leading bold label run is the first run; only strip
                # bold from the value (non-first) runs.
                is_label_row = True
                break

        if is_label_row:
            # For label-row paragraphs, strip bold only from non-first runs.
            runs = p_elem.findall(qn("w:r"))
            for r in runs[1:]:
                rPr = r.find(qn("w:rPr"))
                if rPr is None:
                    continue
                for tag in ("w:b", "w:bCs"):
                    for el in rPr.findall(qn(tag)):
                        rPr.remove(el)
        elif is_heading:
            # Slot heading paragraph — keep bold + Calibri (Headings).
            for r in p_elem.findall(qn("w:r")):
                rPr = r.find(qn("w:rPr"))
                if rPr is None:
                    rPr = OxmlElement("w:rPr")
                    r.insert(0, rPr)
                # Ensure Calibri font + 14pt (Heading style) — but preserve
                # whatever size Brain specified via <w:sz>.
                if not [el for el in rPr.findall(qn("w:rFonts"))]:
                    rFonts = OxmlElement("w:rFonts")
                    rFonts.set(qn("w:ascii"), "Calibri")
                    rFonts.set(qn("w:hAnsi"), "Calibri")
                    rFonts.set(qn("w:cs"), "Calibri")
                    rFonts.set(qn("w:eastAsia"), "Calibri")
                    rPr.append(rFonts)
        else:
            # Body data paragraph — strip bold, force Calibri 10pt.
            for r in p_elem.findall(qn("w:r")):
                rPr = r.find(qn("w:rPr"))
                if rPr is None:
                    rPr = OxmlElement("w:rPr")
                    r.insert(0, rPr)
                for tag in ("w:b", "w:bCs"):
                    for el in rPr.findall(qn(tag)):
                        rPr.remove(el)
                # Force Calibri font (NOT Calibri Light theme) + 10pt
                # on every body run. Replace any inherited <w:rFonts>
                # (whether theme-ref or literal name) with literal
                # Calibri so the body is plain Calibri.
                for el in rPr.findall(qn("w:rFonts")):
                    rPr.remove(el)
                for el in rPr.findall(qn("w:sz")):
                    rPr.remove(el)
                for el in rPr.findall(qn("w:szCs")):
                    rPr.remove(el)
                rFonts = OxmlElement("w:rFonts")
                rFonts.set(qn("w:ascii"), "Calibri")
                rFonts.set(qn("w:hAnsi"), "Calibri")
                rFonts.set(qn("w:cs"), "Calibri")
                rPr.append(rFonts)
                sz = OxmlElement("w:sz")
                sz.set(qn("w:val"), "20")  # 10pt
                rPr.append(sz)
                szCs = OxmlElement("w:szCs")
                szCs.set(qn("w:val"), "20")
                rPr.append(szCs)

    _apply_myanmar_font_to_burmese_runs(doc)


_MYANMAR_CODEPOINT_RANGE = (
    range(0x1000, 0x109F + 1),
    range(0xAA60, 0xAA7F + 1),
    range(0xA9E0, 0xA9FF + 1),
)
_MYANMAR_FROZEN = frozenset(
    cp for r in _MYANMAR_CODEPOINT_RANGE for cp in r
)


def _text_contains_myanmar(text: str | None) -> bool:
    """True if `text` contains any Myanmar (Burmese) codepoint."""
    if not text:
        return False
    return any(ord(ch) in _MYANMAR_FROZEN for ch in text)


def _apply_myanmar_font_to_burmese_runs(doc) -> None:
    """For runs that contain Myanmar text, set the font face to
    `Noto Sans Myanmar` (preferred modern Unicode font). Also sets the
    `cs` (complex script) font and `eastAsia` font slots so Word renders
    the run correctly regardless of which script is dominant.

    Noto Sans Myanmar is preferred over Myanmar Text because:
      - Myanmar Text's cmap has historical issues with stacked-consonant
        ligatures that cause Unicode corruption when extracted from Word PDFs
      - Noto Sans Myanmar uses modern Unicode-compliant glyph IDs
      - Noto Sans Myanmar has better support for newer Myanmar extensions
    """
    for p in doc.element.body.iter(qn("w:p")):
        # Skip paragraphs inside table cells (preserve Brain template
        # styling for label rows and structural table headers).
        ancestor = p.getparent()
        in_table = False
        while ancestor is not None:
            if ancestor.tag.split("}")[-1] == "tc":
                in_table = True
                break
            ancestor = ancestor.getparent()
        for r in p.findall(qn("w:r")):
            t_elems = r.findall(qn("w:t"))
            if not t_elems:
                continue
            text = "".join((t.text or "") for t in t_elems)
            if not _text_contains_myanmar(text):
                continue
            rPr = r.find(qn("w:rPr"))
            if rPr is None:
                rPr = OxmlElement("w:rPr")
                r.insert(0, rPr)
            rFonts = rPr.find(qn("w:rFonts"))
            if rFonts is None:
                rFonts = OxmlElement("w:rFonts")
                rPr.append(rFonts)
            # Set the complex-script + east-asia font names so Word
            # renders Myanmar glyphs with the bundled `Noto Sans Myanmar`.
            rFonts.set(qn("w:cs"), "Noto Sans Myanmar")
            rFonts.set(qn("w:eastAsia"), "Noto Sans Myanmar")
