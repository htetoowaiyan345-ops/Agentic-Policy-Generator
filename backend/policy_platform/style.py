"""Style application for the rendered Brain output.

Centralized policy for typography:

- Body paragraphs: Calibri 10pt, justify, line spacing 1.5, with 4pt
  paragraph spacing before/after.
- Slot headings: Calibri (Headings), bold, sized by Brain convention.
- Important inline markers (e.g. "Note:", "Important:") get bold via
  inline run formatting — only the marker text is bold, not the whole
  paragraph.
- Page break inserted before each slot (except slot 1 and slot 15).

This module exposes pure functions that mutate the python-docx Document
in place. The renderer calls them in a sensible order per slot.
"""
from __future__ import annotations

import re
from typing import Iterable

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt


# Inline markers: text after these in a paragraph becomes bold.
# Only the marker text is bold; the rest of the paragraph keeps body styling.
_INLINE_MARKERS: tuple[str, ...] = (
    "Note:",
    "Important:",
    "Caution:",
    "Warning:",
    "Reminder:",
)


def _apply_body_paragraph_format(p_elem) -> None:
    """Apply body formatting to one <w:p> element: Calibri 10pt, justify,
    line spacing 1.5, 4pt before/after spacing.

    Skips if the paragraph is empty (no runs).
    """
    if p_elem.tag.split("}")[-1] != "p":
        return
    runs = p_elem.findall(qn("w:r"))
    if not runs:
        return
    # Build <w:pPr>
    pPr = p_elem.find(qn("w:pPr"))
    if pPr is None:
        pPr = OxmlElement("w:pPr")
        p_elem.insert(0, pPr)
    # Remove existing justification & spacing so we set fresh values.
    for tag in ("w:jc", "w:spacing"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    jc = OxmlElement("w:jc")
    jc.set(qn("w:val"), "both")
    pPr.append(jc)
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:line"), "360")  # 1.5 * 240 twips/line
    spacing.set(qn("w:lineRule"), "auto")
    spacing.set(qn("w:before"), "80")  # 4 points
    spacing.set(qn("w:after"), "80")  # 4 points
    pPr.append(spacing)
    # Apply Calibri 10pt to every run. Strip inherited <w:b/> —
    # body data is non-bold per the user's directive.
    for r in runs:
        rPr = r.find(qn("w:rPr"))
        if rPr is None:
            rPr = OxmlElement("w:rPr")
            r.insert(0, rPr)
        # Remove old font / size / bold so we set fresh values.
        for tag in ("w:rFonts", "w:sz", "w:szCs", "w:b", "w:bCs"):
            for el in rPr.findall(qn(tag)):
                rPr.remove(el)
        rFonts = OxmlElement("w:rFonts")
        rFonts.set(qn("w:ascii"), "Calibri")
        rFonts.set(qn("w:hAnsi"), "Calibri")
        rFonts.set(qn("w:cs"), "Calibri")
        rPr.append(rFonts)
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), "20")  # 10pt = 20 half-points
        rPr.append(sz)
        szCs = OxmlElement("w:szCs")
        szCs.set(qn("w:val"), "20")
        rPr.append(szCs)


def _apply_heading_format(p_elem) -> None:
    """Set the heading font to Calibri (Headings) and KEEP bold.

    Headings remain bold (matches Brain's heading weight) but their
    font is normalized to Calibri (Headings). Body data is non-bold —
    see `_apply_body_paragraph_format`.
    """
    if p_elem.tag.split("}")[-1] != "p":
        return
    runs = p_elem.findall(qn("w:r"))
    if not runs:
        return
    for r in runs:
        rPr = r.find(qn("w:rPr"))
        if rPr is None:
            rPr = OxmlElement("w:rPr")
            r.insert(0, rPr)
        # Remove ONLY the font element; leave <w:b>, <w:sz>, <w:i>,
        # <w:color>, etc. exactly as the Brain specified them.
        for el in rPr.findall(qn("w:rFonts")):
            rPr.remove(el)
        rFonts = OxmlElement("w:rFonts")
        rFonts.set(qn("w:ascii"), "Calibri")
        rFonts.set(qn("w:hAnsi"), "Calibri")
        rFonts.set(qn("w:cs"), "Calibri")
        rFonts.set(qn("w:eastAsia"), "Calibri")
        rPr.append(rFonts)


def _bold_run(r) -> None:
    """Make a single run bold (idempotent)."""
    rPr = r.find(qn("w:rPr"))
    if rPr is None:
        rPr = OxmlElement("w:rPr")
        r.insert(0, rPr)
    if rPr.find(qn("w:b")) is None:
        b = OxmlElement("w:b")
        rPr.append(b)
    if rPr.find(qn("w:bCs")) is None:
        bCs = OxmlElement("w:bCs")
        rPr.append(bCs)


def _split_run_at_marker(run_elem, marker_index: int, marker_len: int):
    """Split a <w:r> in two at marker position. Returns the new run placed
    AFTER the current one containing the post-marker text. The first run
    ends up holding only the pre-marker + marker text (still in the same run)."""
    # Concatenate all <w:t> text in this run (rare but possible: multiple)
    texts = run_elem.findall(qn("w:t"))
    if not texts:
        return None
    full = "".join((t.text or "") for t in texts)
    if marker_index + marker_len > len(full):
        return None
    # Find which <w:t> element the marker starts in.
    accum = 0
    split_t_elem = None
    split_t_offset = 0
    for t in texts:
        tlen = len(t.text or "")
        if accum + tlen > marker_index:
            split_t_elem = t
            split_t_offset = marker_index - accum
            break
        accum += tlen
    if split_t_elem is None:
        return None
    # text elements of the run
    pre_marker = full[:marker_index]
    marker = full[marker_index:marker_index + marker_len]
    post_marker = full[marker_index + marker_len:]

    # Wipe text content of every existing <w:t>, and put pre_marker text into the
    # original first text element (everything before the marker stays in run 1).
    remaining_in_first = pre_marker + marker
    first_text_elem = texts[0]
    for t in texts[1:]:
        t.text = ""
    first_text_elem.text = remaining_in_first

    # Build post-marker run if there is anything after the marker.
    if not post_marker:
        return None
    # Copy run-properties rPr so the continuation run keeps the original
    # formatting (font, size, etc.).
    orig_rPr = run_elem.find(qn("w:rPr"))
    new_run = OxmlElement("w:r")
    if orig_rPr is not None:
        # Deep-copy rPr
        import copy as _copy
        new_rPr = _copy.deepcopy(orig_rPr)
        new_run.append(new_rPr)
    new_t = OxmlElement("w:t")
    new_t.set(qn("xml:space"), "preserve")
    new_t.text = post_marker
    new_run.append(new_t)
    # Insert new run immediately after current run.
    run_elem.addnext(new_run)
    return new_run


def _apply_marker_bold(p_elem) -> bool:
    """Bold ONLY the marker word, not the whole paragraph.

    Looks for the first occurrence of any inline marker (e.g., "Note:")
    inside the paragraph's text and splits the surrounding run so only the
    marker is bold. The rest of the paragraph remains in normal weight.

    Returns True if a marker was found and bolded.
    """
    if p_elem.tag.split("}")[-1] != "p":
        return False
    runs = p_elem.findall(qn("w:r"))
    if not runs:
        return False
    full = "".join("".join((t.text or "") for t in r.iter(qn("w:t"))) for r in runs)
    if not full:
        return False
    full_lower = full.lower()
    for marker in _INLINE_MARKERS:
        marker_lower = marker.lower()
        idx = full_lower.find(marker_lower)
        if idx == -1:
            continue
        # Find which run contains absolute position idx.
        accum = 0
        target_run = None
        for r in runs:
            r_len = sum(len(t.text or "") for t in r.findall(qn("w:t")))
            if accum + r_len > idx:
                target_run = r
                break
            accum += r_len
        if target_run is None:
            return False
        rel_idx = idx - accum
        # Split the run at (rel_idx + len(marker)). After split, run 1 has
        # pre-marker + marker, run 2 has post-marker. Then we bold only run 1.
        # But we want only the marker bold, so split first at rel_idx (pre),
        # then split again at the marker boundary to isolate the marker.
        # Simpler: split target_run at rel_idx; recurse-bold runs.
        after_run = _split_run_at_marker(target_run, rel_idx, len(marker))
        # Now target_run holds pre-marker + marker.
        # Bold only the marker part: split target_run at len(pre-marker) to
        # isolate marker; then bold that run's text.
        pre_len = rel_idx
        if pre_len > 0:
            # We need to split target_run between pre-marker and marker.
            # After the previous split, target_run now has pre+marker; we
            # need to split it at pre_len to keep marker-only bold.
            pass
        # Re-fetch runs after mutation.
        runs = p_elem.findall(qn("w:r"))
        # find which run holds the marker now (it's the one ending at
        # idx + marker_len cumulative offset).
        cum = 0
        marker_run = None
        marker_text_len = 0
        for r in runs:
            r_text = "".join((t.text or "") for t in r.findall(qn("w:t")))
            r_len = len(r_text)
            if cum <= idx and cum + r_len >= idx + len(marker):
                marker_run = r
                marker_text_len = r_len
                break
            cum += r_len
        if marker_run is None:
            return False
        # If the marker run also contains text BEFORE the marker (pre-marker
        # wasn't split), split it now at relative index pre_len.
        pre_off = idx - cum
        if pre_off > 0 and marker_text_len > len(marker):
            _split_run_at_marker(marker_run, pre_off, 0)
            # Re-fetch runs and re-find the marker run.
            runs = p_elem.findall(qn("w:r"))
            cum = 0
            marker_run = None
            for r in runs:
                r_text = "".join((t.text or "") for t in r.findall(qn("w:t")))
                r_len = len(r_text)
                if cum <= idx and cum + r_len >= idx + len(marker):
                    marker_run = r
                    break
                cum += r_len
        if marker_run is None:
            return False
        _bold_run(marker_run)
        return True
    return False


def apply_styles_to_paragraph(p_elem, *, heading: bool = False) -> None:
    """Apply heading or body styling to a single <w:p> element.

    Character formatting (bold, italic, etc.) is NOT touched — only
    paragraph-level font + size + alignment + spacing changes. The
    Brain's run-level bold/italic decisions are preserved verbatim so
    we never insert our own bolding.
    """
    if heading:
        _apply_heading_format(p_elem)
    else:
        _apply_body_paragraph_format(p_elem)


def apply_styles_to_section(body_elements: list, *, heading_indices: set[int]) -> None:
    """Apply body styles to a slot's body elements.

    body_elements: list of <w:p> elements belonging to one section.
    heading_indices: set of local indices in body_elements that are headings.
    """
    for idx, el in enumerate(body_elements):
        if el.tag.split("}")[-1] != "p":
            continue
        apply_styles_to_paragraph(el, heading=idx in heading_indices)


def insert_page_break_before(p_elem) -> None:
    """Insert a page break by adding a page-break-before element to the
    given <w:p>'s pPr."""
    if p_elem.tag.split("}")[-1] != "p":
        return
    pPr = p_elem.find(qn("w:pPr"))
    if pPr is None:
        pPr = OxmlElement("w:pPr")
        p_elem.insert(0, pPr)
    # Remove existing pageBreakBefore
    for el in pPr.findall(qn("w:pageBreakBefore")):
        pPr.remove(el)
    pbb = OxmlElement("w:pageBreakBefore")
    pPr.append(pbb)


def is_empty_bullet_paragraph(p_elem) -> bool:
    """A paragraph is a 'bullet with no content' if its text is just a
    bullet symbol/prefix with no real word content."""
    if p_elem.tag.split("}")[-1] != "p":
        return False
    text = "".join((t.text or "") for t in p_elem.iter(qn("w:t"))).strip()
    if not text:
        return True
    if _BULLET_ONLY.match(text):
        return True
    return False


_BULLET_ONLY = re.compile(r"^[\s•\-\*\u2022\u2023\u2043\u204C\u204D\.\,]+$")


def remove_empty_paragraph(p_elem) -> bool:
    """Remove empty paragraphs (<w:p> with no runs or only whitespace runs).

    Returns True if the paragraph was removed.
    """
    if p_elem.tag.split("}")[-1] != "p":
        return False
    parent = p_elem.getparent()
    if parent is None:
        return False
    parent.remove(p_elem)
    return True


def strip_empty_bullet_runs(p_elem) -> bool:
    """If a paragraph is just a bullet with no content, blank its text."""
    if p_elem.tag.split("}")[-1] != "p":
        return False
    text = "".join((t.text or "") for t in p_elem.iter(qn("w:t")))
    if _BULLET_ONLY.match(text.strip()):
        for t in p_elem.iter(qn("w:t")):
            t.text = ""
        return True
    return False


# Bullet character pattern used when splitting inline bullets into separate
# paragraphs. Only TRUE bullet characters (•, *, ◦) are recognized as
# bullets. Dashes (– or -) are NOT bullets — they're part of regular text
# like "FDA-2020-D-0987" and must not split paragraphs.
_BULLET_INLINE_RE = re.compile(r"[•\u2022\u25E6\*]")


def _make_new_paragraph(parent, after_p, text, *, pPr_template=None):
    """Create a new <w:p> after `after_p` (sibling) with given text and
    optional pPr template. Returns the new element."""
    new_p = OxmlElement("w:p")
    if pPr_template is not None:
        import copy as _copy
        pPr = _copy.deepcopy(pPr_template)
        new_p.append(pPr)
    if text:
        r = OxmlElement("w:r")
        new_t = OxmlElement("w:t")
        new_t.set(qn("xml:space"), "preserve")
        new_t.text = text
        r.append(new_t)
        new_p.append(r)
    if after_p is not None:
        after_p.addnext(new_p)
    else:
        parent.append(new_p)
    return new_p


def split_inline_bullets(p_elem) -> int:
    """DEPRECATED — kept for backward compatibility. No longer called
    by the renderer. Bullets are now rendered inline via
    `replace_bullets_with_filled`; paragraphs are NOT split.

    The function body is preserved but unreachable from the new pipeline.
    """
    return 0


# ---------------------------------------------------------------------------
# Phase 6 — Required-field placeholders + bullet rendering
# ---------------------------------------------------------------------------

# Replace `•` with `●` inline (no paragraph restructure).
_BULLET_TO_FILLED = str.maketrans({
    "\u2022": "\u25CF",  # • → ●
    "\u25E6": "\u25CF",  # ◦ → ●
})


def replace_bullets_with_filled(p_elem) -> int:
    """In-place replace `•` and `◦` characters with filled-black `●`
    inside every <w:t> text run of the paragraph.

    Returns the number of characters replaced.
    """
    if p_elem.tag.split("}")[-1] != "p":
        return 0
    count = 0
    for t in p_elem.iter(qn("w:t")):
        if not t.text:
            continue
        original = t.text
        new = original.translate(_BULLET_TO_FILLED)
        if new != original:
            t.text = new
            count += sum(1 for a, b in zip(original, new) if a != b)
    return count


def bold_bullet_characters(p_elem) -> int:
    """Make every `●` character in the paragraph bold (split runs so
    only the bullet character is bold). Same approach as Phase 5's
    word-level bold.

    Returns the number of bullet characters bolded.
    """
    if p_elem.tag.split("}")[-1] != "p":
        return 0
    runs = p_elem.findall(qn("w:r"))
    if not runs:
        return 0
    count = 0
    for run in runs:
        for t in list(run.findall(qn("w:t"))):
            text = t.text or ""
            if "\u25CF" not in text:
                continue
            # Split the text element around each `●`.
            segments = text.split("\u25CF")
            # Re-build: non-bullet text in current run (alternating), bullet chars bolded.
            new_t_text = segments[0]
            t.text = new_t_text
            parent_run = run
            insert_after = parent_run
            for s_idx in range(1, len(segments)):
                bullet_run = OxmlElement("w:r")
                # Copy run formatting from original run so styling is preserved.
                orig_rPr = parent_run.find(qn("w:rPr"))
                if orig_rPr is not None:
                    import copy as _copy
                    bullet_rPr = _copy.deepcopy(orig_rPr)
                    if bullet_rPr.find(qn("w:b")) is None:
                        b = OxmlElement("w:b")
                        bullet_rPr.append(b)
                    if bullet_rPr.find(qn("w:bCs")) is None:
                        bCs = OxmlElement("w:bCs")
                        bullet_rPr.append(bCs)
                    bullet_run.append(bullet_rPr)
                bullet_t = OxmlElement("w:t")
                bullet_t.set(qn("xml:space"), "preserve")
                bullet_t.text = "\u25CF"
                bullet_run.append(bullet_t)
                insert_after.addnext(bullet_run)
                insert_after = bullet_run
                count += 1
                # Next non-bullet segment.
                non_bullet_run = OxmlElement("w:r")
                if orig_rPr is not None:
                    import copy as _copy
                    non_rPr = _copy.deepcopy(orig_rPr)
                    non_bullet_run.append(non_rPr)
                non_t = OxmlElement("w:t")
                non_t.set(qn("xml:space"), "preserve")
                non_t.text = segments[s_idx]
                non_bullet_run.append(non_t)
                insert_after.addnext(non_bullet_run)
                insert_after = non_bullet_run
    return count


def render_not_found_placeholder(p_elem, slot_name: str) -> None:
    """Replace the paragraph text with `<slot_name>: Data is not found in source file`
    in plain body styling (no italic, no gray) — matching the Brain's
    normal body weight.

    Per the user's directive the marker phrasing is exact:
    `Data is not found in source file`.

    Strips the leading section number (e.g. "3. ") from `slot_name` so the
    placeholder reads as `Exclusions: Data is not found in source file`
    instead of `3. Exclusions: Data is not found in source file`.
    """
    if p_elem.tag.split("}")[-1] != "p":
        return
    # Strip leading "N." section number from the label, if present.
    import re as _re
    clean_label = _re.sub(r"^\s*\d+\.\s*", "", slot_name)
    text = f"{clean_label}: Data is not found in source file"
    # Wipe existing runs.
    for r in list(p_elem.findall(qn("w:r"))):
        p_elem.remove(r)
    new_r = OxmlElement("w:r")
    new_t = OxmlElement("w:t")
    new_t.set(qn("xml:space"), "preserve")
    new_t.text = text
    new_r.append(new_t)
    p_elem.append(new_r)


def render_table_no_data_placeholder(p_elem) -> None:
    """Render the unified marker in a table cell paragraph.

    Plain body styling — no italic, no gray. Used for slots 10 (Award
    tiers) and 14 (History) when the input supplies no table data.

    Marker phrasing is unified to the same text the renderer uses for
    empty label-rows: `Data is not found in source file`.
    """
    if p_elem.tag.split("}")[-1] != "p":
        return
    for r in list(p_elem.findall(qn("w:r"))):
        p_elem.remove(r)
    new_r = OxmlElement("w:r")
    new_t = OxmlElement("w:t")
    new_t.set(qn("xml:space"), "preserve")
    new_t.text = "Data is not found in source file"
    new_r.append(new_t)
    p_elem.append(new_r)


def handle_example_prefix(p_elem) -> bool:
    """Detect `Example:` (with colon) or `Example —`/`Example -` (hyphen variants)
    at the start of a paragraph and:
    - For `Example: text.` → prepend `● ` keeping the literal `Example:` text.
    - For `Example - text.` (or `—`, `--`) → strip the `Example <sep>` prefix,
      then prepend `● ` to the body.

    Returns True if the paragraph was modified.
    """
    if p_elem.tag.split("}")[-1] != "p":
        return False
    text = "".join((t.text or "") for t in p_elem.iter(qn("w:t"))).strip()
    if not text:
        return False
    # Pattern A: `Example: ...`  -> keep `Example:` literal, prepend `● `.
    m = re.match(r"^Example\s*:\s*(.+)$", text)
    if m:
        body = m.group(1)
        return _replace_paragraph_text(p_elem, f"\u25CF Example: {body}")
    # Pattern B: `Example - ...` / `Example — ...` / `Example -- ...` -> strip prefix.
    m = re.match(r"^Example\s*(?:-{1,2}|—|–)\s*(.+)$", text)
    if m:
        body = m.group(1)
        return _replace_paragraph_text(p_elem, f"\u25CF {body}")
    return False


def _replace_paragraph_text(p_elem, new_text: str) -> bool:
    """Replace all run text in a paragraph with a single line of new_text.

    Preserves the first run's formatting (rPr) and clears siblings.
    """
    if p_elem.tag.split("}")[-1] != "p":
        return False
    runs = p_elem.findall(qn("w:r"))
    if not runs:
        # No runs — create one.
        new_r = OxmlElement("w:r")
        new_t = OxmlElement("w:t")
        new_t.set(qn("xml:space"), "preserve")
        new_t.text = new_text
        new_r.append(new_t)
        p_elem.append(new_r)
        return True
    first_run = runs[0]
    # Clear all text elements in first run and set new_text.
    for t in list(first_run.findall(qn("w:t"))):
        first_run.remove(t)
    new_t = OxmlElement("w:t")
    new_t.set(qn("xml:space"), "preserve")
    new_t.text = new_text
    first_run.append(new_t)
    # Remove sibling runs.
    for r in runs[1:]:
        p_elem.remove(r)
    return True

