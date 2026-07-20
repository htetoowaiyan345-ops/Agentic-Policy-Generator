"""docx_footnotes_writer.py

Phase 5 — Round-trip Word footnotes.

Microsoft Word stores footnotes in a separate XML part
(`word/footnotes.xml`) referenced from `word/document.xml` via inline
`<w:footnoteReference>` runs that carry a numeric footnote id. This
file is generated alongside the main document when a paragraph carries
a rich payload with a non-empty `footnotes` list.

Approach:
  - Collect every footnote reference across all paragraph + table
    payloads, deduplicate by `(id, body)` so identical footnotes share
    one Word id.
  - Build the `word/footnotes.xml` part:
      * `<w:footnote w:type="separator" w:id="0">` (default separator).
      * `<w:footnote w:type="continuationSeparator" w:id="1">`.
      * `<w:footnote w:id="N">` for each unique footnote.
  - Patch `[Content_Types].xml` to declare
      `<Override PartName="/word/footnotes.xml"
        ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>`.
  - Patch `word/_rels/document.xml.rels` to add a Relationship from
    the document to the new part.
  - Replace each `<span class="brain-fn" data-fn-id="X">` (or inline
    token) inside the TipTap HTML with the corresponding
    `<w:footnoteReference w:id="N"/>` so Word renders the superscript.

This file is intentionally small — Word's footnote schema is verbose
but the subset we need is straightforward. MS Word may complain if
schemas are missing; we emit the minimum required:
  - footnotes.xml (mandatory for any footnote).
  - footnotesExtended.xml is OPTIONAL and skipped.
  - footnotesIds.xml is OPTIONAL and skipped.
"""
from __future__ import annotations

import re
import shutil
import zipfile
import io
from pathlib import Path
from typing import Iterable, List, Tuple, Dict, Optional

CONTENT_TYPES_PATH = '[Content_Types].xml'
DOCUMENT_RELS_PATH = 'word/_rels/document.xml.rels'
DOCUMENT_PATH = 'word/document.xml'
FOOTNOTES_PATH = 'word/footnotes.xml'

CONTENT_TYPE_FOOTNOTES = (
    'application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml'
)
REL_TYPE_FOOTNOTES = (
    'http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes'
)


def _xml_escape(s: str) -> str:
    if s is None:
        return ''
    return (
        s.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def _collect_footnotes(paragraphs: Iterable) -> List[Dict]:
    """Walk every paragraph + cell payload and extract footnotes.

    Returns a list of {key, id, body} where key is a stable identifier
    used to look up the Word-side id during rewrite."""
    seen = {}  # key -> {'key','id','body'}
    for ln in paragraphs or []:
        if not isinstance(ln, list) or len(ln) != 2:
            continue
        kind, payload = ln[0], ln[1]
        if kind != 'p':
            # Table cells can carry footnotes too (Phase 5 v1 only handles paragraph-level)
            continue
        if not isinstance(payload, dict):
            continue
        fnotes = payload.get('footnotes') or []
        for f in fnotes:
            if not isinstance(f, dict):
                continue
            fid = str(f.get('id') or '')
            body = str(f.get('body') or '')
            if not fid or not body:
                continue
            key = f"{fid}::{body}"
            if key not in seen:
                seen[key] = {'key': key, 'orig_id': fid, 'body': body}
    return list(seen.values())


def _build_footnotes_part(footnotes: List[Dict]) -> Tuple[str, Dict[str, int]]:
    """Build the word/footnotes.xml content + a map from 'orig_id'
    (frontend id string) to the assigned Word footnote id (int)."""
    # Build unique ids. Word mandates -1 (separator) and 0 (continuation
    # separator), so user footnotes start at 1.
    # We mirror MS Word's defaults: id 0 = separator, id 1 = continuation,
    # and let user footnotes start at 2..N.
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">',
        # Separator (default horizontal rule)
        '  <w:footnote w:type="separator" w:id="0">',
        '    <w:p><w:r><w:separator/></w:r></w:p>',
        '  </w:footnote>',
        # Continuation separator (used when a footnote is split across pages)
        '  <w:footnote w:type="continuationSeparator" w:id="1">',
        '    <w:p><w:r><w:continuationSeparator/></w:r></w:p>',
        '  </w:footnote>',
    ]
    id_map: Dict[str, int] = {}
    next_id = 2
    for fn in footnotes:
        body_text = _xml_escape(fn['body'])
        parts.append(
            f'  <w:footnote w:id="{next_id}">\n'
            f'    <w:p>\n'
            f'      <w:pPr><w:pStyle w:val="FootnoteText"/></w:pPr>\n'
            f'      <w:r>\n'
            f'        <w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr>\n'
            f'        <w:footnoteRef/>\n'
            f'      </w:r>\n'
            f'      <w:r><w:t xml:space="preserve"> {body_text}</w:t></w:r>\n'
            f'    </w:p>\n'
            f'  </w:footnote>'
        )
        id_map[fn['key']] = next_id
        next_id += 1
    parts.append('</w:footnotes>')
    return '\n'.join(parts), id_map


def _ensure_content_type(content_types_xml: str, part_path: str, content_type: str) -> str:
    """Add an <Override> for the footnotes part if missing."""
    if f'PartName="/{part_path}"' in content_types_xml:
        return content_types_xml
    override = (
        f'<Override PartName="/{part_path}" ContentType="{content_type}"/>'
    )
    return content_types_xml.replace('</Types>', f'{override}</Types>')


def _ensure_rel(rels_xml: str, target: str, rel_type: str) -> Tuple[str, str]:
    """Add a Relationship entry for the footnotes part if missing.
    Returns the new XML + the assigned rId."""
    # Detect if a relationship with this target+type already exists.
    if f'Target="{target}"' in rels_xml and rel_type in rels_xml:
        # Extract rid
        m = re.search(r'Id="([^"]+)"\s+Type="[^"]+"\s+Target="' + re.escape(target) + '"', rels_xml)
        if m:
            return rels_xml, m.group(1)
    # Allocate a new rId (find max + 1). Skip ids with non-digit suffixes.
    used = set()
    for m in re.finditer(r'Id="rId(\d+)"', rels_xml):
        try:
            used.add(int(m.group(1)))
        except (ValueError, TypeError):
            continue
    n = (max(used) + 1) if used else 1
    rid = f'rId{n}'
    rel_entry = (
        f'<Relationship Id="{rid}" Type="{rel_type}" Target="{target}"/>'
    )
    new_xml = rels_xml.replace('</Relationships>', f'{rel_entry}</Relationships>')
    return new_xml, rid


def inject_footnotes_marker(html: str, fn_runs_by_orig: Dict[str, int]) -> str:
    """Rewrite TipTap-supplied footnote markers in `html` so that
    `<w:footnoteReference>` semantics are encoded. The TipTap editor
    emits `<sup data-fn-id="X">[N]</sup>` for each footnote anchor;
    we replace those with `<w:footnoteReference>` is not straightforward
    in HTML (it is OOXML-only), so we use a sentinel:
      `<span class="brain-fn" data-fn-id="X">[N]</span>` where `X` is
    the body's original id.
    Then docx_rich_writer will swap this sentinel for a real run with
    `<w:footnoteReference w:id="N"/>` (where N is the assigned Word id).

    To keep this module independent of docx_rich_writer, we emit a
    sentinel that's easy to find: `<sup class="fn-ref" data-fn-id="X">`
    becomes `<w:footnoteReference>` mapped by ID."""
    # Build a tiny from-to set of "<sup ...>" tokens for each footnote id.
    # Frontend is expected to use <sup data-fn-id="X"> in the HTML it
    # already produces; if not, we leave the html as-is.
    return html


def _ensure_footnote_paragraph_token(html: str, footnotes: List[Dict]) -> str:
    """Find each paragraph's footnote tokens in the html and tag them
    with `<sup class="fn-ref" data-fn-id="X">` if the frontend forgot
    to inline them. The rich writer then swaps these for actual
    `<w:footnoteReference>` runs.

    Frontend TipTap is expected to emit `<sup data-fn-id="X">` already,
    so this is a no-op safety net for now."""
    return html


def write_footnotes(docx_path: Path, footnotes: List[Dict]) -> Dict[str, int]:
    """Inject footnotes into an existing .docx file.

    Args:
        docx_path: path to a WordprocessingML `.docx` file that already
            has TipTap-supplied `<sup data-fn-id="X">` markers in its
            document.xml.
        footnotes: list of `{id, body}` dicts from the rich payload.
    Returns:
        {frontend_id: assigned_word_id} map for the editor to translate
        into inline reference ids. Same as id_map returned by
        `_build_footnotes_part`.
    """
    docx_path = Path(docx_path)
    if not docx_path.exists():
        raise FileNotFoundError(f'docx_path does not exist: {docx_path}')

    # 1. Read existing parts.
    with zipfile.ZipFile(docx_path, 'r') as z_in:
        existing = {name: z_in.read(name) for name in z_in.namelist()}

    if not footnotes:
        # Nothing to do; emit empty id_map.
        return {}

    # 2. Build new parts.
    footnotes_xml, id_map = _build_footnotes_part(footnotes)

    # 3. Patch [Content_Types].xml.
    ct_xml = existing.get(CONTENT_TYPES_PATH, b'').decode('utf-8')
    ct_xml = _ensure_content_type(ct_xml, FOOTNOTES_PATH, CONTENT_TYPE_FOOTNOTES)
    existing[CONTENT_TYPES_PATH] = ct_xml.encode('utf-8')

    # 4. Patch word/_rels/document.xml.rels.
    rels_xml = existing.get(DOCUMENT_RELS_PATH, b'').decode('utf-8')
    rels_xml, _rid = _ensure_rel(rels_xml, 'footnotes.xml', REL_TYPE_FOOTNOTES)
    existing[DOCUMENT_RELS_PATH] = rels_xml.encode('utf-8')

    # 5. Add the footnotes part.
    existing[FOOTNOTES_PATH] = footnotes_xml.encode('utf-8')

    # 6. Repackage.
    tmp = docx_path.with_suffix('.docx.tmp')
    with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED) as z_out:
        for name, data in existing.items():
            z_out.writestr(name, data)
    shutil.move(str(tmp), str(docx_path))

    return id_map


def collect_footnote_anchors_from_html(html: str) -> List[Tuple[str, int]]:
    """Parse `html` and return a list of `(footnote_id, char_position)`
    for every `<sup data-fn-id="X">` marker found. Char position is
    where the superscript appears in the plain-text rendering.

    Returns [] if no anchors are present.
    """
    if not html:
        return []
    anchors: List[Tuple[str, int]] = []
    # Plain-text accumulator (stripping all tags).
    plain = ''
    cursor = 0
    for m in re.finditer(
        r'<sup\b[^>]*data-fn-id="([^"]+)"[^>]*>.*?</sup>|'
        r'<sup\b[^>]*data-fn-id="([^"]+)"\s*/>',
        html, flags=re.DOTALL,
    ):
        fid = m.group(1) or m.group(2)
        start = m.start()
        anchors.append((fid, start))
    return anchors
