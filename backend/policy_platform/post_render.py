"""Post-render cleanup of the docx zip.

Removes decorative black horizontal lines that come from the Brain
framework's default-page header (``header2.xml``). These are
``<v:line>`` VML shapes with ``strokecolor="black [3040]"``,
positioned absolutely at the top of every non-first page.

This module is intentionally defensive: it works on raw XML
strings, not python-docx objects, because python-docx cannot
reach into header XML for line-shape cleanup.

The Brain framework brand elements (logo, contact info, page
numbers) are preserved verbatim. Only the decorative black
horizontal line is removed.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path


# Patterns to detect decorative black lines anywhere in a Word XML part.
_LINE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "vline_strokecolor_black",
        re.compile(
            r'<v:line\b[^>]*strokecolor\s*=\s*"[^"]*black[^"]*"',
            re.IGNORECASE,
        ),
    ),
    (
        "vline_strokeweight",
        re.compile(
            r'<v:line\b[^>]*strokeweight\s*=\s*"[^"]+"',
            re.IGNORECASE,
        ),
    ),
    (
        "ln_solidblack_fill",
        re.compile(
            r'<a:ln\b[^>]*>\s*<a:solidFill\b[^>]*>\s*<a:srgbClr\b[^>]*val\s*=\s*"(?:000000|0+)"',
            re.IGNORECASE,
        ),
    ),
)

# Pattern: a DrawingML <w:drawing> that wraps a <a:prstGeom prst="line"> shape
# (cy="0", meaning zero-height line). Word renders these as horizontal rules.
# They may use theme colors via <a:schemeClr val="dk1"/> (dark 1) which Word
# renders as black. We match by shape geometry rather than color, since
# theme colors are not literal hex values.
_DRAWING_LINE_SHAPE = re.compile(
    r'<w:drawing\b[^>]*>(?:(?!</w:drawing>).)*?'
    r'<a:prstGeom[^>]*prst=["\']line["\'][^>]*>'
    r'(?:(?!</a:prstGeom>).)*?</a:prstGeom>'
    r'(?:(?!</w:drawing>).)*?</w:drawing>',
    re.DOTALL,
)

# Pattern: a <w:drawing> whose shape body has cy="0" indicating a horizontal line.
# This catches variants where prstGeom is "line" with strict geometric properties.
_DRAWING_CY_ZERO = re.compile(
    r'<w:drawing\b[^>]*>(?:(?!</w:drawing>).)*?'
    r'<a:ext\s+cx=["\'][0-9]+["\']\s+cy=["\']0["\'][^/]*/>'
    r'(?:(?!</w:drawing>).)*?</w:drawing>',
    re.DOTALL,
)


def _is_black_line(xml: str) -> bool:
    """True if the XML fragment represents a decorative black horizontal line."""
    for _name, pat in _LINE_PATTERNS:
        if pat.search(xml):
            return True
    return False


def _strip_pict_wrappers(xml: str) -> str:
    """Strip <w:pict>...</w:pict> blocks whose inner content is a black line."""
    # Non-greedy across nested tags.
    return re.sub(
        r"<w:pict\b.*?</w:pict>",
        lambda m: "" if _is_black_line(m.group(0)) else m.group(0),
        xml,
        flags=re.DOTALL,
    )


def _strip_drawing_with_solidblack_ln(xml: str) -> str:
    """Strip <w:drawing> blocks whose inner content is a black line."""
    return re.sub(
        r"<w:drawing\b.*?</w:drawing>",
        lambda m: "" if _is_black_line(m.group(0)) else m.group(0),
        xml,
        flags=re.DOTALL,
    )


def _strip_orphan_a_ln_solidblack(xml: str) -> str:
    """Strip stray <a:ln> blocks whose content is a solid black fill.

    Some pictures embed the line inside <a:ln> rather than inside the
    <v:line> wrapper. Detected separately so a wrapperless line-shape
    is still cleaned.
    """
    return re.sub(
        r"<a:ln\b[^>]*>\s*<a:solidFill\b[^>]*>\s*<a:srgbClr\b[^>]*val\s*=\s*\"(?:000000|0+)\"[^/]*/>\s*</a:solidFill>\s*</a:ln>",
        "",
        xml,
        flags=re.DOTALL | re.IGNORECASE,
    )


def _strip_drawing_line_shapes(xml: str) -> str:
    """Strip <w:drawing> blocks whose inner shape is a horizontal line
    (DrawingML <a:prstGeom prst="line"/> with cy="0" extent).

    Word interprets these as full-width horizontal black/dark rules
    drawn into the page. They are decorative, not data.
    """
    # Two-pass strip: first by prstGeom prst=line, then by ext cy="0".
    xml = _DRAWING_LINE_SHAPE.sub("", xml)
    xml = _DRAWING_CY_ZERO.sub("", xml)
    return xml


def clean_xml_part(xml: str) -> str:
    """Apply every cleaning pass to a single XML string and return the result."""
    before = len(xml)
    xml = _strip_orphan_a_ln_solidblack(xml)
    xml = _strip_pict_wrappers(xml)
    xml = _strip_drawing_with_solidblack_ln(xml)
    xml = _strip_drawing_line_shapes(xml)
    after = len(xml)
    # Log ignored: the function is pure — callers can diff before/after.
    return xml


def strip_black_lines(docx_path: Path) -> int:
    """Rewrite any header/footer/document XML inside the zip that
    contains a decorative black line. Returns the number of parts
    that were modified.

    This is intentionally a path-mutating function: it copies the zip
    to a temp file, rewrites relevant entries, swaps the file in.
    The original is preserved if the rewrite raises mid-way.
    """
    docx_path = Path(docx_path)
    if not docx_path.is_file():
        raise FileNotFoundError(f"Output docx not found: {docx_path}")

    return _strip_lines_in_docx(docx_path)


def _strip_lines_in_docx(docx_path: Path) -> int:
    """Internal helper. Same as strip_black_lines, broken out so it
    can be reused by Brain-sanitization code (which calls without
    wanting the same post-render contract)."""
    tmp_path = docx_path.with_suffix(docx_path.suffix + ".tmp")
    changed = 0
    try:
        with zipfile.ZipFile(docx_path, "r") as zin:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    should_clean = (
                        item.filename == "word/document.xml"
                        or item.filename == "word/glossary/document.xml"
                        or (
                            item.filename.startswith("word/header")
                            and item.filename.endswith(".xml")
                        )
                        or (
                            item.filename.startswith("word/footer")
                            and item.filename.endswith(".xml")
                        )
                    )
                    if should_clean and item.filename.endswith(".xml"):
                        # Preserve byte-identical ZIP_DATE/permission/etc by re-instantiating.
                        new_info = zipfile.ZipInfo(
                            filename=item.filename,
                            date_time=item.date_time,
                        )
                        new_info.compress_type = item.compress_type
                        new_info.external_attr = item.external_attr
                        text = data.decode("utf-8", errors="replace")
                        cleaned = clean_xml_part(text)
                        if cleaned != text:
                            changed += 1
                            data = cleaned.encode("utf-8")
                        zout.writestr(new_info, data)
                    else:
                        new_info = zipfile.ZipInfo(
                            filename=item.filename,
                            date_time=item.date_time,
                        )
                        new_info.compress_type = item.compress_type
                        new_info.external_attr = item.external_attr
                        zout.writestr(new_info, data)
        shutil.move(str(tmp_path), str(docx_path))
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
    return changed


def sanitize_brain_in_place(brain_path: Path) -> int:
    """Remove decorative black horizontal lines from the Brain template
    IN PLACE so that no output ever inherits them.

    Use this ONCE during project setup (e.g., when installing the
    framework). After it returns N (number of parts rewritten), the
    Brain's SHA-256 changes and the manifest must be re-frozen with
    ``brain.init_or_verify(init=True)``.

    This is identical to strip_black_lines but uses an unambiguous name
    that conveys intent (Brain sanitization vs. output post-cleanup).
    """
    brain_path = Path(brain_path)
    if not brain_path.is_file():
        raise FileNotFoundError(f"Brain file not found: {brain_path}")
    return _strip_lines_in_docx(brain_path)
