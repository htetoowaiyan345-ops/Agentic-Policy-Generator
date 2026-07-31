"""docx_rich_writer.py

Phase 4 — round-trips rich HTML (the SvelteKit TipTap editor's `html`
field on every paragraph) into python-docx's underlying OxmlElement
tree, so the generated `.docx` preserves bold / italic / underline /
strikethrough / colour / font-family / font-size / heading levels.

Approach:
  - Parse the TipTap HTML as a flat list of styled text runs.
  - Use `html.parser.HTMLParser` (stdlib) so we don't pull in lxml as a
    hard dependency; `html.parser` is in stdlib since Python 3.0.
  - For each text node, emit a `w:r` run with the appropriate
    `w:rPr` children based on the active style stack.
  - Convert each run into `docx.oxml.OxmlElement` with the right
    styling using `qn()` namespacing.

This file is intentionally independent of `policy_platform.renderer` —
the existing text-only `_set_paragraph_text` already covers the legacy
case, while `docx_rich_writer.write_paragraph` is the new rich path.
"""
from __future__ import annotations

import re
import copy
from html.parser import HTMLParser
from typing import Optional, List

from docx.oxml import OxmlElement
from docx.oxml.ns import qn


# ---------------------------------------------------------------------------
# Inline-tag -> WordprocessingML property factory
# ---------------------------------------------------------------------------

_TRUE_COLOR_MAP = {
    'true': '1',
    'on': '1',
    'yes': '1',
    '1': '1',
    '': '1',  # bare <b> attribute absence
}

_HEADING_TAG_TO_OUTLINE = {
    'h1': '0',
    'h2': '1',
    'h3': '2',
}


def _as_bool(v: Optional[str]) -> bool:
    if v is None:
        return False
    return v.strip().lower() in _TRUE_COLOR_MAP


def _as_color(v: Optional[str]) -> Optional[str]:
    """Extract a hex colour from a CSS color property like '#abc' or 'rgb(...)'.
    Returns a 6-digit uppercase hex (no leading #) suitable for `w:color w:val`.
    Falls back to None for anything we can't parse."""
    if not v:
        return None
    v = v.strip()
    if v.startswith('#'):
        h = v[1:]
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        if len(h) == 6:
            return h.upper()
    m = re.match(r'rgb\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', v)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{r:02X}{g:02X}{b:02X}"
    return None


def _as_size(v: Optional[str]) -> Optional[int]:
    """Extract a font size in half-points from a CSS font-size.
    Common values: '14px', '0.875em', 'small'. Returns half-points
    (i.e. w:sz value) suitable for `w:sz w:val`."""
    if not v:
        return None
    s = v.strip().lower()
    m = re.match(r'(\d+(?:\.\d+)?)\s*(px|pt|em|rem)?', s)
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2) or 'px'
    if unit == 'em' or unit == 'rem':
        pt = val * 16.0  # assume 16px base
    elif unit == 'pt':
        pt = val
    else:
        pt = val * (72.0 / 96.0)  # px -> pt
    return int(round(pt * 2))


def _strip_bom_whitespace(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


# ---------------------------------------------------------------------------
# HTML parser -> list of styled spans
# ---------------------------------------------------------------------------

class _Span:
    __slots__ = ('text', 'rPr', 'footnoteId')

    def __init__(self, text: str, rPr: dict, footnoteId: Optional[str] = None):
        self.text = text
        self.rPr = rPr  # dict of prop -> value
        self.footnoteId = footnoteId  # str when this run is a <sup data-fn-id="X"> marker


class _TipTapParser(HTMLParser):
    """Walk the TipTap HTML, tracking a stack of inline-style properties.
    On text, emit a span. On </tag>, pop. Block tags (h1/h2/h3/p/li) are
    converted into a `_Block` marker so the outer reader knows where to
    end the paragraph."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.spans: List[_Span] = []
        self.style_stack: List[dict] = []
        self.blocks: List[str] = []  # textual hint: 'p', 'h1', etc.

    @property
    def current_style(self) -> dict:
        merged = {}
        for layer in self.style_stack:
            merged.update(layer)
        return merged

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        style_str = attr_dict.get('style', '') or ''
        layer = {}
        for chunk in style_str.split(';'):
            if not chunk.strip():
                continue
            if ':' not in chunk:
                continue
            k, v = chunk.split(':', 1)
            layer[k.strip().lower()] = v.strip().lower()
        # Tag-level shortcuts
        if tag in ('b', 'strong'):
            layer.setdefault('font-weight', 'bold')
        if tag in ('i', 'em'):
            layer.setdefault('font-style', 'italic')
        if tag in ('u',):
            layer['underline'] = '1'
        if tag in ('s', 'del', 'strike'):
            layer['strike'] = '1'
        if tag in _HEADING_TAG_TO_OUTLINE:
            layer['outline'] = _HEADING_TAG_TO_OUTLINE[tag]
        if tag in ('br',):
            # Treat <br> as a soft line break inside the same run
            self.spans.append(_Span('\n', self.current_style))
            return
        if tag == 'sup':
            # Footnote anchor marker — <sup data-fn-id="X">N</sup>
            fn_id = attr_dict.get('data-fn-id') or attr_dict.get('data-fn')
            if fn_id:
                # Emit a zero-width-ish sentinel with footnoteId set;
                # the renderer will substitute a <w:footnoteReference> run.
                self.spans.append(_Span('', self.current_style, footnoteId=str(fn_id)))
                self.style_stack.append(layer)
                return
            self.style_stack.append(layer)
            return
        if tag in ('p', 'div'):
            self.blocks.append(tag)
        elif tag in _HEADING_TAG_TO_OUTLINE:
            self.blocks.append(tag)
        elif tag in ('li',):
            self.blocks.append(tag)
        self.style_stack.append(layer)

    def handle_endtag(self, tag):
        if self.style_stack:
            self.style_stack.pop()
        if tag in ('p', 'div') and self.blocks and self.blocks[-1] == tag:
            self.blocks.pop()
            # Emit a newline span to terminate the paragraph
            self.spans.append(_Span('\n', self.current_style))
        elif tag in _HEADING_TAG_TO_OUTLINE and self.blocks and self.blocks[-1] == tag:
            self.blocks.pop()
            self.spans.append(_Span('\n', self.current_style))
        elif tag == 'li' and self.blocks and self.blocks[-1] == tag:
            self.blocks.pop()

    def handle_data(self, data):
        text = data
        if not text:
            return
        self.spans.append(_Span(text, self.current_style))


def _parse_spans(html: str) -> List[_Span]:
    parser = _TipTapParser()
    # TipTap wraps content in <p>...</p> typically. We treat the entire
    # HTML as a sequence of styled spans terminated by '\n' per block.
    parser.feed(html or '')
    parser.close()
    return parser.spans


# ---------------------------------------------------------------------------
# WordprocessingML builders
# ---------------------------------------------------------------------------

def _make_rpr(*,
              bold: bool = False,
              italic: bool = False,
              underline: bool = False,
              strike: bool = False,
              color: Optional[str] = None,
              font_family: Optional[str] = None,
              size_hp: Optional[int] = None,
              outline: Optional[str] = None) -> Optional[OxmlElement]:
    """Build a <w:rPr/> element with the given run properties."""
    if not any([bold, italic, underline, strike, color, font_family,
                size_hp is not None, outline is not None]):
        return None
    rPr = OxmlElement('w:rPr')
    if bold:
        b = OxmlElement('w:b')
        rPr.append(b)
    if italic:
        i = OxmlElement('w:i')
        rPr.append(i)
    if underline:
        u = OxmlElement('w:u')
        u.set(qn('w:val'), 'single')
        rPr.append(u)
    if strike:
        s = OxmlElement('w:strike')
        rPr.append(s)
    if color:
        c = OxmlElement('w:color')
        c.set(qn('w:val'), color)
        rPr.append(c)
    if font_family:
        rFonts = OxmlElement('w:rFonts')
        rFonts.set(qn('w:ascii'), font_family)
        rFonts.set(qn('w:hAnsi'), font_family)
        rFonts.set(qn('w:cs'), font_family)
        rPr.append(rFonts)
    if size_hp is not None and size_hp > 0:
        sz = OxmlElement('w:sz')
        sz.set(qn('w:val'), str(size_hp))
        rPr.append(sz)
        szCs = OxmlElement('w:szCs')
        szCs.set(qn('w:val'), str(size_hp))
        rPr.append(szCs)
    if outline is not None:
        out = OxmlElement('w:outlineLvl')
        out.set(qn('w:val'), outline)
        rPr.append(out)
    return rPr


def _build_run(text: str, style: dict) -> OxmlElement:
    """Build a `<w:r>` run from a (text, inline-style) pair.

    No default font/size is emitted: when the user HTML doesn't specify
    inline styling, the run inherits the brain framework's native
    styling (font, size, alignment, indent) via the surrounding `pPr`.
    Explicit user styling (bold/italic/color/font-family/font-size via
    `<span style="...">`) is honoured and overrides the scaffold.
    """
    r = OxmlElement('w:r')
    fw = (style.get('font-weight') or '').strip().lower()
    bold = fw in ('bold', 'bolder', '700', '800', '900')
    italic = (style.get('font-style') or '').strip().lower() in ('italic', 'oblique')
    underline = _as_bool(style.get('underline')) or (style.get('text-decoration') or '').strip().lower() == 'underline'
    strike = _as_bool(style.get('strike')) or 'line-through' in (style.get('text-decoration') or '').strip().lower()
    color = _as_color(style.get('color'))
    font_family = style.get('font-family')
    size_hp = _as_size(style.get('font-size'))
    outline = style.get('outline')

    rPr = _make_rpr(
        bold=bold, italic=italic, underline=underline, strike=strike,
        color=color, font_family=_sanitize_font(font_family),
        size_hp=size_hp, outline=outline
    )
    if rPr is not None:
        # Strip empty rPr (shouldn't happen, but defensive)
        if len(rPr):
            r.append(rPr)
    # Split text into paragraphs whenever a newline appears, by emitting
    # multiple <w:t> segments broken with <w:br/>. Easier: replace '\n'
    # with <w:br/> elements inside the same run.
    parts = text.split('\n')
    for i, part in enumerate(parts):
        if i > 0:
            br = OxmlElement('w:br')
            r.append(br)
        if part:
            t = OxmlElement('w:t')
            t.set(qn('xml:space'), 'preserve')
            t.text = part
            r.append(t)
    return r


def _sanitize_font(font_family: Optional[str]) -> Optional[str]:
    """Pull a single font name out of CSS font-family lists like
    'Inter, sans-serif'."""
    if not font_family:
        return None
    first = font_family.split(',', 1)[0].strip().strip('"').strip("'")
    # Strip generic family names
    if first.lower() in ('sans-serif', 'serif', 'monospace', 'inherit', 'initial',
                          'unset', 'auto', 'system-ui', 'arial', ''):
        # fall back to 'Calibri' for sans-serif, 'Times New Roman' for serif
        if first.lower() in ('serif',):
            return 'Times New Roman'
        if first.lower() in ('monospace',):
            return 'Courier New'
        return 'Calibri'
    return first


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _build_footnote_reference(footnote_word_id: int, style: dict) -> OxmlElement:
    """Build a `<w:r>` run that emits a superscript footnoteReference.

    `<w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteReference w:id="N"/>`
    """
    r = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    rStyle = OxmlElement('w:rStyle')
    rStyle.set(qn('w:val'), 'FootnoteReference')
    rPr.append(rStyle)
    # Inherit text-style if any (size, color, etc.)
    if (style.get('font-family') or style.get('font-size')
            or style.get('color')):
        from docx.oxml import OxmlElement as _OE
        if style.get('font-family'):
            rFonts = _OE('w:rFonts')
            family = _sanitize_font(style.get('font-family'))
            rFonts.set(qn('w:ascii'), family or 'Calibri')
            rFonts.set(qn('w:hAnsi'), family or 'Calibri')
            rFonts.set(qn('w:cs'), family or 'Calibri')
            rPr.append(rFonts)
        if style.get('font-size'):
            sz = _as_size(style['font-size'])
            if sz and sz > 0:
                szEl = _OE('w:sz')
                szEl.set(qn('w:val'), str(sz))
                rPr.append(szEl)
    r.append(rPr)
    ref = OxmlElement('w:footnoteReference')
    ref.set(qn('w:id'), str(footnote_word_id))
    r.append(ref)
    return r


def write_paragraph(p_elem, html: str, footnote_id_map: Optional[Dict[str, int]] = None) -> None:
    """Replace the contents of `p_elem` (a python-docx `_Paragraph._p`
    OOXML element) with rich runs parsed from `html`.

    Preserves the paragraph's `w:pPr` (style, alignment, etc.) by not
    touching it. Leaves a single empty `<w:r><w:t/></w:r>` if there's
    no text (Word requires at least one run to be renderable).

    `footnote_id_map` (optional) maps frontend footnote ids (str) to
    Word footnote ids (int). If a span has `footnoteId` but no mapping
    is provided, we still emit the reference run but with id=0 (Word
    will fall back to the separator)."""
    pPr = p_elem.find(qn('w:pPr'))
    # Remove ALL child runs/hyperlinks, but keep pPr.
    for child in list(p_elem):
        if child.tag == qn('w:pPr'):
            continue
        p_elem.remove(child)
    if not html:
        p_elem.append(OxmlElement('w:r'))
        return
    spans = _parse_spans(html)
    # Strip trailing '\n'-only spans (they come from the closing </p>
    # in the user's HTML). When write_paragraph is called on an
    # existing <w:p> element, the paragraph break is the <w:p> itself
    # — emitting a soft <w:br/> at the end renders a blank line in Word.
    while spans and (spans[-1].text or '') == '\n':
        spans.pop()
    if not spans:
        p_elem.append(OxmlElement('w:r'))
        return
    for span in spans:
        # Footnote anchor span
        if span.footnoteId:
            word_id = 0
            if footnote_id_map is not None:
                key_match = next((k for k in footnote_id_map.keys()
                                  if k.startswith(span.footnoteId + '::')), None)
                if key_match is not None:
                    word_id = footnote_id_map[key_match]
            p_elem.append(_build_footnote_reference(word_id, span.rPr or {}))
            continue
        text = span.text or ''
        if not text.strip() and text != '\n':
            continue
        run = _build_run(text, span.rPr or {})
        p_elem.append(run)
    if not list(p_elem.findall(qn('w:r'))):
        # Word requires at least one run for the paragraph to render
        p_elem.append(OxmlElement('w:r'))


def plain_text_from_html(html: str) -> str:
    """Extract a plain-text fallback from rich HTML, used when we don't
    care about formatting (audit log, free-paragraph text, etc.)."""
    spans = _parse_spans(html or '')
    return ''.join(s.text or '' for s in spans)
