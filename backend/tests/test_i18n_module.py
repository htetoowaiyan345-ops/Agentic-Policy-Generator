"""Tests for the policy_platform.i18n module."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from policy_platform.i18n import (
    detect_paragraph_lang,
    detect_document_lang,
    normalize_burmese,
    is_burmese_text,
)
from policy_platform.i18n.burmese_synonyms import (
    get_burmese_synonyms,
    get_all_burmese_synonyms,
    reset_cache,
)
from policy_platform.i18n.burmese_strings import data_not_found_for_lang
from policy_platform.i18n.burmese_queries import get_queries_for_slot_my


class TestDetectParagraphLang(unittest.TestCase):
    def test_english_only(self):
        self.assertEqual(detect_paragraph_lang("This is a policy document."), "en")

    def test_burmese_only(self):
        text = "ဤမူဝါဒသည် ဝန်ထုတ်ဝန်ပိုးအားလုံးကို လွှမ်းခြုံသည်။"
        self.assertEqual(detect_paragraph_lang(text), "my")

    def test_mixed_paragraph(self):
        text = "Policy နယ်ပယ် applies to all employees."
        self.assertEqual(detect_paragraph_lang(text), "mixed")

    def test_empty_string_defaults_to_en(self):
        self.assertEqual(detect_paragraph_lang(""), "en")

    def test_none_defaults_to_en(self):
        self.assertEqual(detect_paragraph_lang(None), "en")  # type: ignore[arg-type]

    def test_only_punctuation_defaults_to_en(self):
        self.assertEqual(detect_paragraph_lang("---...---"), "en")


class TestDetectDocumentLang(unittest.TestCase):
    def test_pure_english(self):
        self.assertEqual(
            detect_document_lang(["Hello", "World", "Foo"]),
            "en",
        )

    def test_pure_burmese(self):
        self.assertEqual(
            detect_document_lang(["မိတ်ဆက်", "နယ်ပယ်", "အဓိပ္ပာယ်"]),
            "my",
        )

    def test_mixed_document(self):
        # Two Burmese and one English paragraph: both >=10%, so "en-my".
        paras = ["Hello", "မိတ်ဆက်", "World", "နယ်ပယ်"]
        self.assertEqual(detect_document_lang(paras), "en-my")

    def test_empty_document(self):
        self.assertEqual(detect_document_lang([]), "en")


class TestNormalizeBurmese(unittest.TestCase):
    def test_nfc_passthrough(self):
        # Same string NFC and NFD should normalize to identical.
        import unicodedata
        s = "မိတ်ဆက်"
        nfd = unicodedata.normalize("NFD", s)
        self.assertEqual(normalize_burmese(nfd), s)

    def test_zwnj_stripped(self):
        text = "မိတ်\u200Cဆက်"
        result = normalize_burmese(text)
        self.assertNotIn("\u200C", result)
        self.assertEqual(result, "မိတ်ဆက်")

    def test_zwj_preserved(self):
        text = "မိတ်\u200Dဆက်"
        result = normalize_burmese(text)
        self.assertIn("\u200D", result)

    def test_invisible_chars_stripped(self):
        text = "hello\u200B\u200E\u200Fworld"
        result = normalize_burmese(text)
        self.assertNotIn("\u200B", result)
        self.assertNotIn("\u200E", result)
        self.assertNotIn("\u200F", result)
        self.assertEqual(result, "helloworld")

    def test_idempotent(self):
        text = "မိတ်\u200Cဆက်\u200B"
        once = normalize_burmese(text)
        twice = normalize_burmese(once)
        self.assertEqual(once, twice)

    def test_empty_string(self):
        self.assertEqual(normalize_burmese(""), "")

    def test_none_safe(self):
        self.assertEqual(normalize_burmese(None), "")  # type: ignore[arg-type]


class TestIsBurmeseText(unittest.TestCase):
    def test_english_only(self):
        self.assertFalse(is_burmese_text("Hello world"))

    def test_with_one_myanmar_char(self):
        self.assertTrue(is_burmese_text("Policy မူဝါဒ"))

    def test_empty(self):
        self.assertFalse(is_burmese_text(""))


class TestBurmeseSynonyms(unittest.TestCase):
    def setUp(self):
        reset_cache()

    def test_loads_from_yaml(self):
        syns = get_burmese_synonyms(12)
        self.assertGreater(len(syns), 0)
        self.assertIn("အဓိပ္ပာယ်", syns)

    def test_scope_synonyms(self):
        syns = get_burmese_synonyms(8)
        self.assertIn("နယ်ပယ်", syns)

    def test_unknown_slot_returns_empty(self):
        self.assertEqual(get_burmese_synonyms(999), [])

    def test_all_synonyms_returns_dict(self):
        all_syns = get_all_burmese_synonyms()
        self.assertIsInstance(all_syns, dict)
        self.assertGreater(len(all_syns), 0)
        for slot_id, syns in all_syns.items():
            self.assertIsInstance(slot_id, int)
            self.assertIsInstance(syns, list)


class TestBurmeseStrings(unittest.TestCase):
    def test_myanmar_placeholder(self):
        self.assertEqual(data_not_found_for_lang("my"), "အချက်အလက် မတွေ့ပါ")

    def test_english_placeholder(self):
        self.assertEqual(data_not_found_for_lang("en"), "Data is not found in source file.")

    def test_mixed_uses_english(self):
        self.assertEqual(data_not_found_for_lang("mixed"), "Data is not found in source file.")


class TestBurmeseQueries(unittest.TestCase):
    def test_definitions_queries(self):
        queries = get_queries_for_slot_my(12)
        self.assertGreater(len(queries), 0)
        self.assertIn("အဓိပ္ပာယ်", queries)

    def test_unknown_slot(self):
        self.assertEqual(get_queries_for_slot_my(999), [])


class TestFontBundled(unittest.TestCase):
    """The Burmese font should be present under data/fonts/."""

    def test_font_file_present(self):
        candidates = [
            Path(__file__).resolve().parent.parent / "data" / "fonts" / "NotoSansMyanmar-Regular.ttf",
            Path(__file__).resolve().parent.parent / "data" / "fonts" / "Pyidaungsu.ttf",
        ]
        if not any(c.exists() for c in candidates):
            self.skipTest("Burmese font not bundled (network unavailable at install time)")


if __name__ == "__main__":
    unittest.main()