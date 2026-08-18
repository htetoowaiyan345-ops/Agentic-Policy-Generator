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
