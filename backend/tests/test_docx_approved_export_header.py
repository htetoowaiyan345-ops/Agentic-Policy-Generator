"""test_docx_approved_export_header.py

Phase 8 — locks the Word-style header + footer behaviour of
`build_approved_docx`. The published .docx must:

  - Preserve the Brain-framework logo (`<w:drawing>` in header2.xml +
    header3.xml).
  - Strip `[...]` bracket wrappers from the Brain's existing title and
    policy-number text runs so the visible header text is bare.
  - Preserve the Brain's PAGE / NUMPAGES page-number fields without
    injecting duplicates.
"""
from __future__ import annotations

import sys
import zipfile
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.docx_approved_export import build_approved_docx, _read_header_metadata


BRAIN_TEMPLATE = ROOT / 'data' / 'brain_template' / 'Policy_Framework_5.docx'


def _read_all(xml_path_zip: zipfile.ZipFile, key: str) -> list[str]:
    """Return all `<w:t>` text runs concatenated for any part whose
    filename ends with `key` (e.g. 'header1.xml')."""
    out = []
    import re
    for name in xml_path_zip.namelist():
        if name.endswith(key):
            content = xml_path_zip.read(name).decode('utf-8', errors='replace')
            out.extend(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', content))
    return out


def test_read_header_metadata_extracts_bracket_fields():
    lines = [
        ['p', {'slot': 0, 'text': 'Type: HR Policy', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Policy Title: TEST TITLE', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Policy Number: ABC_99', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Effective Date/Period: 01 Jan 2026', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Approved by: The Approver', 'html': '', 'footnotes': []}],
    ]
    meta = _read_header_metadata(lines)
    assert meta['type'] == 'HR Policy'
    assert meta['title'] == 'TEST TITLE'
    assert meta['number'] == 'ABC_99'
    assert meta['effective_date'] == '01 Jan 2026'
    assert meta['approved_by'] == 'The Approver'


def test_read_header_metadata_falls_back_to_brackets():
    meta = _read_header_metadata([])
    assert meta['type'] == '[Policy Type]'
    assert meta['title'] == '[Policy Title]'
    assert meta['number'] == '[Policy Number]'
    assert meta['effective_date'] == '[Effective Date]'
    assert meta['approved_by'] == '[Approved By]'


def test_header_footer_writes_brackets_and_page_fields(tmp_path: Path):
    if not BRAIN_TEMPLATE.exists():
        # Skip if the brain template isn't shipped (dev/test environments).
        return
    lines = [
        ['p', {'slot': 0, 'text': 'Type: HR Policy', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Policy Title: TEST HEADER', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Policy Number: CLH_P8_99', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Effective Date/Period: 01 Aug 2026', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Approved by: P8 Approver', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Body paragraph 1', 'html': 'Body paragraph 1', 'footnotes': []}],
    ]
    out = tmp_path / 'phase8.docx'
    build_approved_docx(BRAIN_TEMPLATE, lines, str(out))
    assert out.exists()
    with zipfile.ZipFile(out, 'r') as z:
        all_texts = []
        for name in z.namelist():
            if name.startswith('word/') and name.endswith('.xml'):
                import re
                content = z.read(name).decode('utf-8', errors='replace')
                all_texts.extend(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', content))
        joined = '\n'.join(all_texts)
        # Header / footer must include the resolved metadata.
        for needle in ['TEST HEADER', 'CLH_P8_99', 'HR Policy', '01 Aug 2026']:
            assert needle in joined, f'{needle} missing from generated docx'
        # PAGE / NUMPAGES fields must be present in footer.
        footer_field_pages = []
        for name in z.namelist():
            if 'footer' in name and name.endswith('.xml'):
                content = z.read(name).decode('utf-8', errors='replace')
                if 'PAGE' in content or 'NUMPAGES' in content:
                    footer_field_pages.append(name)
        assert footer_field_pages, 'no PAGE/NUMPAGES fields found in any footer part'


def test_header_footer_preserves_brain_page_field(tmp_path: Path):
    """The Brain template ships with a `<w:sdt>` 'Page Numbers (Bottom
    of Page)' docPart that already supplies a PAGE field. The new
    preserve-and-strip strategy keeps that SDT intact (so we don't
    duplicate the page field) and only adds fields when absent."""
    if not BRAIN_TEMPLATE.exists():
        return
    lines = [
        ['p', {'slot': 0, 'text': 'Type: HR', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Policy Title: T', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Policy Number: X', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Effective Date/Period: 2026', 'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Approved by: A', 'html': '', 'footnotes': []}],
    ]
    out = tmp_path / 'phase8b.docx'
    build_approved_docx(BRAIN_TEMPLATE, lines, str(out))
    with zipfile.ZipFile(out, 'r') as z:
        first_footer = sorted(
            n for n in z.namelist() if 'footer' in n and n.endswith('.xml')
        )[0]
        content = z.read(first_footer).decode('utf-8', errors='replace')
        # PAGE field (or NUMPAGES) must still be present.
        assert 'PAGE' in content, (
            f'PAGE field missing in {first_footer}: {content[:400]}'
        )
        # No duplicate PAGE field should have been injected by us — the
        # Brain's PAGE field is the only one expected.  We detect this
        # by counting the number of `w:instrText` nodes that contain
        # 'PAGE'.
        import re
        page_fields = re.findall(
            r'<w:instrText[^>]*>\s*PAGE\b[^<]*</w:instrText>', content
        )
        # The Brain ships with one PAGE field; we should never add a
        # second one.
        assert len(page_fields) <= 1, (
            f'duplicate PAGE field injected: {page_fields}'
        )


def test_header_footer_strips_brackets_from_brain_title(tmp_path: Path):
    """The Brain's `word/header2.xml` ships with `[City Family High
    School Completion Award Policy]` and `[CL&H_02/24]` runs. After
    `build_approved_docx`, neither run should display with literal
    bracket characters in the visible `<w:t>` text."""
    if not BRAIN_TEMPLATE.exists():
        return
    lines = [
        ['p', {'slot': 0, 'text': 'Policy Title: EarthQuake Emergency Assistance Policy',
              'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Policy Number: CL&H_03/26',
              'html': '', 'footnotes': []}],
    ]
    out = tmp_path / 'phase8c.docx'
    build_approved_docx(BRAIN_TEMPLATE, lines, str(out))
    with zipfile.ZipFile(out, 'r') as z:
        h2 = z.read('word/header2.xml').decode('utf-8', errors='replace')
        # Every visible <w:t> run is either empty (was '[' or ']') or
        # contains the inner text without brackets.  No run should start
        # with '[' or end with ']'.
        import re
        texts = re.findall(r'<w:t[^>]*>([^<]*)</w:t>', h2)
        for t in texts:
            stripped = t.strip()
            if stripped:
                assert not stripped.startswith('['), (
                    f'remaining bracket in header2.xml run: {t!r}'
                )
                assert not stripped.endswith(']'), (
                    f'remaining bracket in header2.xml run: {t!r}'
                )
        # The Brain's title text and number should still be present
        # (joined with no separator — Brain's runs are concatenated as-is
        # in document order).
        joined = ''.join(t for t in texts)
        assert 'City Family High School Completion Award Policy' in joined
        assert 'CL&amp;H_02/24' in joined


def test_header_footer_preserves_brain_logo_drawing(tmp_path: Path):
    """The Brain logo `<w:drawing>` in `word/header2.xml` and
    `word/header3.xml` must survive `build_approved_docx`."""
    if not BRAIN_TEMPLATE.exists():
        return
    lines = [
        ['p', {'slot': 0, 'text': 'Policy Title: T',
              'html': '', 'footnotes': []}],
        ['p', {'slot': 0, 'text': 'Policy Number: X',
              'html': '', 'footnotes': []}],
    ]
    out = tmp_path / 'phase8d.docx'
    build_approved_docx(BRAIN_TEMPLATE, lines, str(out))
    with zipfile.ZipFile(out, 'r') as z:
        h2 = z.read('word/header2.xml').decode('utf-8', errors='replace')
        assert '<w:drawing>' in h2, 'logo drawing wiped from header2.xml'
        h3 = z.read('word/header3.xml').decode('utf-8', errors='replace')
        assert '<w:drawing>' in h3, 'logo drawing wiped from header3.xml'
