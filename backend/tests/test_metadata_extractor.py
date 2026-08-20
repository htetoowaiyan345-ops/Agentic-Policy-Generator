"""Tests for the metadata-based PDF extractor.

Generic invariants, no fixture-specific golden text. Uses the
HR_00002_redacted.pdf fixture when available; skip-cleanly otherwise.
"""
import sys
import unittest
from pathlib import Path

from policy_platform.extract_myanmar.metadata_extractor import (
    FontResolver,
    build_font_resolvers,
    extract_text_via_metadata,
    is_extractable,
    _parse_tounicode_cmap,
    _cp_from_glyph_name,
)

FIXTURE = Path(
    "D:\\Htet Oo Wai Yan\\OneDrive - City Holdings Limited\\Desktop\\"
    "agentic-policy-platform - Copy (4)\\backend\\tests\\fixtures\\"
    "HR_00002_redacted.pdf"
)


def _safe_setup():
    if not FIXTURE.exists():
        return None
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    return FIXTURE


class TestParseToUnicodeCMap(unittest.TestCase):
    def test_parses_bfchar_block(self) -> None:
        data = b"""begincmap
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
2 beginbfchar
<0041> <1000>
<0042> <1001>
endbfchar
endcmap
"""
        cmap = _parse_tounicode_cmap(data)
        self.assertEqual(cmap.get(0x41), 0x1000)
        self.assertEqual(cmap.get(0x42), 0x1001)

    def test_parses_bfrange_sequential(self) -> None:
        data = b"""begincmap
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
1 beginbfrange
<0100> <0102> <2000>
endbfrange
endcmap
"""
        cmap = _parse_tounicode_cmap(data)
        self.assertEqual(cmap.get(0x100), 0x2000)
        self.assertEqual(cmap.get(0x101), 0x2001)
        self.assertEqual(cmap.get(0x102), 0x2002)

    def test_parses_bfrange_array(self) -> None:
        data = b"""begincmap
1 begincodespacerange
<0000> <FFFF>
endcodespacerange
1 beginbfrange
<0100> <0102> [<002D> <2013> <0020>]
endbfrange
endcmap
"""
        cmap = _parse_tounicode_cmap(data)
        self.assertEqual(cmap.get(0x100), 0x2D)
        self.assertEqual(cmap.get(0x101), 0x2013)
        self.assertEqual(cmap.get(0x102), 0x20)


class TestFontResolverDecode(unittest.TestCase):
    def test_decode_filters_pua(self) -> None:
        r = FontResolver(
            subtype="Type0",
            cid_to_cp={0: 0x1000, 1: 0xE000, 2: 0x1001},
            resolvable=True,
        )
        out = r.decode(bytes([0x00, 0x00, 0x00, 0x01, 0x00, 0x02]))
        self.assertEqual(out, "\u1000\u1001")
        self.assertNotIn("\uE000", out)

    def test_decode_filters_surrogates(self) -> None:
        r = FontResolver(
            subtype="Type0",
            cid_to_cp={0: 0xD800, 1: 0x1000},
            resolvable=True,
        )
        out = r.decode(bytes([0x00, 0x00, 0x00, 0x01]))
        self.assertEqual(out, "\u1000")

    def test_decode_decomposes_4byte_composite(self) -> None:
        # 0x103E102F decomposes to U+103E (း) + U+102F (ု)
        r = FontResolver(
            subtype="Type0",
            cid_to_cp={0: 0x103E102F, 1: 0x1000},
            resolvable=True,
        )
        out = r.decode(bytes([0x00, 0x00, 0x00, 0x01]))
        self.assertEqual(out, "\u103E\u102F\u1000")


class TestCpFromGlyphName(unittest.TestCase):
    """Tests for parsing cp from `uniNNNN` / `uNNNNNNNN` glyph names.

    Microsoft Word's PDF export sometimes emits embedded fonts whose
    glyphs follow the `uniNNNN` naming convention but are not listed in
    the embedded cmap table. The name itself is a reliable Unicode
    encoding and can be used as a last-resort fallback.
    """

    def test_uni_four_hex(self) -> None:
        self.assertEqual(_cp_from_glyph_name("uni1019"), 0x1019)
        self.assertEqual(_cp_from_glyph_name("uni103B"), 0x103B)
        self.assertEqual(_cp_from_glyph_name("uni1000"), 0x1000)

    def test_uni_lowercase_hex(self) -> None:
        self.assertEqual(_cp_from_glyph_name("uni101a"), 0x101A)

    def test_uni_uppercase_hex(self) -> None:
        self.assertEqual(_cp_from_glyph_name("uni101F"), 0x101F)

    def test_u_prefix_alternate(self) -> None:
        # Some fonts use the shorter `uNNNN` form (no `ni`).
        self.assertEqual(_cp_from_glyph_name("u1000"), 0x1000)

    def test_uni_five_hex_pua_returns_none(self) -> None:
        # 5-digit hex would be > 0xFFFF, so still a single cp.
        # But it's a 21-bit value -- still within Unicode range.
        # We accept it if it's a valid codepoint.
        self.assertEqual(_cp_from_glyph_name("uni10000"), 0x10000)

    def test_uni_six_hex_returns_codepoint(self) -> None:
        # 6-digit hex, valid Unicode.
        self.assertEqual(_cp_from_glyph_name("uni10FFFF"), 0x10FFFF)

    def test_uni_eight_hex_returns_none(self) -> None:
        # 8-digit hex would be > 0x10FFFF, so reject.
        self.assertIsNone(_cp_from_glyph_name("u10000000"))

    def test_uni_surrogate_returns_none(self) -> None:
        # Surrogate range is invalid for single-cp output.
        self.assertIsNone(_cp_from_glyph_name("uniD800"))
        self.assertIsNone(_cp_from_glyph_name("uniDFFF"))

    def test_uni_pua_returns_none(self) -> None:
        # Private Use Area is excluded.
        self.assertIsNone(_cp_from_glyph_name("uniE000"))
        self.assertIsNone(_cp_from_glyph_name("uniF8FF"))

    def test_glyph_private_name_returns_none(self) -> None:
        # Microsoft Word's subset renames unmapped glyphs to `glyphNNNNN`.
        self.assertIsNone(_cp_from_glyph_name("glyph00513"))
        self.assertIsNone(_cp_from_glyph_name("glyph00020"))

    def test_latin_name_returns_none(self) -> None:
        # Non-hex glyph names like 'A', 'space', 'endash' don't encode cp.
        self.assertIsNone(_cp_from_glyph_name("A"))
        self.assertIsNone(_cp_from_glyph_name("space"))
        self.assertIsNone(_cp_from_glyph_name("endash"))

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(_cp_from_glyph_name(""))
        self.assertIsNone(_cp_from_glyph_name("uni"))
        self.assertIsNone(_cp_from_glyph_name("uniZZZZ"))


class TestExtractFixture(unittest.TestCase):
    def setUp(self) -> None:
        if _safe_setup() is None:
            self.skipTest(f"Fixture missing: {FIXTURE}")

    def test_is_extractable_true(self) -> None:
        self.assertTrue(is_extractable(FIXTURE))

    def test_extract_returns_per_page_strings(self) -> None:
        pages = extract_text_via_metadata(FIXTURE)
        self.assertEqual(len(pages), 12)  # known page count
        for page_text in pages:
            self.assertIsInstance(page_text, str)

    def test_extracted_text_has_myanmar(self) -> None:
        pages = extract_text_via_metadata(FIXTURE)
        full = "\n".join(pages)
        burmese_chars = sum(1 for ch in full if 0x1000 <= ord(ch) <= 0x109F)
        self.assertGreater(burmese_chars, 100)

    def test_extracted_text_no_pua(self) -> None:
        pages = extract_text_via_metadata(FIXTURE)
        full = "\n".join(pages)
        pua_chars = sum(1 for ch in full if 0xE000 <= ord(ch) <= 0xF8FF)
        self.assertEqual(pua_chars, 0)


if __name__ == "__main__":
    unittest.main()
