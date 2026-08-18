import unittest
from pathlib import Path

from policy_platform.extractors.pdf_extractor import _try_pymupdf, _try_pdfplumber

FIXTURE = Path(
    r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend\tests\fixtures\HR_00002_redacted.pdf"
)


class TestPDFExtractorSmartPath(unittest.TestCase):
    def setUp(self) -> None:
        if not FIXTURE.exists():
            self.skipTest(f"Fixture missing: {FIXTURE}")

    def test_pymupdf_classifies_unsafe(self) -> None:
        doc = _try_pymupdf(FIXTURE)
        if doc is None:
            self.skipTest("pymupdf unavailable")
        self.assertEqual(doc.source_format, "pdf")
        self.assertEqual(doc.pdf_verdict, "unsafe")
        self.assertIn(
            doc.extraction_method,
            (
                "metadata_recovered",
                "myanmar_recovered",
                "unsafe_high_corruption",
                "myanmar_repair_attempted",
            ),
        )
        self.assertGreaterEqual(doc.corruption_score, 0.0)
        self.assertLessEqual(doc.corruption_score, 1.0)

    def test_no_pua_in_paragraphs(self) -> None:
        doc = _try_pymupdf(FIXTURE)
        if doc is None:
            self.skipTest("pymupdf unavailable")
        for p in doc.paragraphs:
            for ch in p:
                self.assertFalse(0xE000 <= ord(ch) <= 0xF8FF)

    def test_pdfplumber_path_also_wired(self) -> None:
        try:
            import pdfplumber  # noqa: F401
        except ImportError:
            self.skipTest("pdfplumber unavailable")
        doc = _try_pdfplumber(FIXTURE)
        self.assertEqual(doc.pdf_verdict, "unsafe")
        self.assertIn(
            doc.extraction_method,
            (
                "metadata_recovered",
                "myanmar_recovered",
                "unsafe_high_corruption",
                "myanmar_repair_attempted",
            ),
        )


if __name__ == "__main__":
    unittest.main()
