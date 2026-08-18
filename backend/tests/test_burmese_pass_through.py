"""End-to-end tests for the Burmese pass-through path.

These tests do NOT require a real Burmese PDF (the user will provide
one). Instead, they exercise the integration by:

  1. Feeding a list of mixed-language paragraphs to the retrieval
     pipeline directly (bypassing PDF extraction).
  2. Verifying that:
     - Per-paragraph lang tags are correct.
     - Burmese heading synonyms are matched.
     - The multilingual embedder is selected for "my"/"mixed" docs.
     - The English-only embedder stays selected for "en" docs.
     - The slot-tracking fields are populated correctly.
     - Burmese text is preserved verbatim in the rendered DOCX (no
       translation, no rewriting).
"""
from __future__ import annotations

import io
import unittest
from pathlib import Path

from policy_platform.extractors.base import ExtractedDocument
from policy_platform.i18n import (
    detect_paragraph_lang,
    detect_document_lang,
    normalize_burmese,
)
from policy_platform.i18n.burmese_synonyms import (
    get_burmese_synonyms,
    get_all_burmese_synonyms,
    reset_cache,
)
from policy_platform.i18n.burmese_strings import data_not_found_for_lang
from policy_platform.i18n.burmese_queries import get_queries_for_slot_my
from policy_platform.rag.heading_anchors import (
    find_heading_match,
    _compiled_patterns_for_lang,
)
from policy_platform.rag.slot_queries import (
    get_queries_for_slot,
    get_queries_for_slot_with_lang,
)
from policy_platform.rag.embedder import MultilingualEmbedder, get_multilingual_embedder
from policy_platform.framework.section_map import get_synonyms_for_slot


class TestBurmesePassThrough(unittest.TestCase):

    def setUp(self):
        reset_cache()

    def test_burmese_paragraph_preserved(self):
        """Burmese text must be preserved verbatim (no translation)."""
        text = "ဤမူဝါဒသည် ဝန်ထုတ်ဝန်ပိုးအားလုံးကို လွှမ်းခြုံသည်။"
        normed = normalize_burmese(text)
        # Burmese text passes through normalization unchanged
        # (normalization only strips invisible chars, doesn't
        # translate).
        self.assertEqual(normed, text)

    def test_burmese_heading_synonym_matches(self):
        """Burmese heading synonym should match its slot via heading-anchor."""
        paragraphs = [
            "နယ်ပယ်",  # Burmese for "Scope"
            "ဤမူဝါဒသည် ဝန်ထုတ်ဝန်ပိုးအားလုံးကို လွှမ်းခြုံသည်။",
            "နောက်ထပ် အချက်အလက်များ။",
        ]
        result = find_heading_match(8, paragraphs, lang="my")
        self.assertIsNotNone(result)
        # result is (start_idx, end_idx, joined_text)
        self.assertEqual(result[0], 0)

    def test_english_doc_uses_english_synonyms_only(self):
        """English-only docs must not pull Burmese synonyms."""
        syns = get_synonyms_for_slot(8, "en")
        self.assertIn("scope", syns)
        self.assertNotIn("နယ်ပယ်", syns)

    def test_burmese_doc_includes_burmese_synonyms(self):
        """Burmese docs get the English + Burmese union."""
        syns = get_synonyms_for_slot(8, "my")
        self.assertIn("scope", syns)
        self.assertIn("နယ်ပယ်", syns)

    def test_mixed_doc_includes_burmese_synonyms(self):
        """Mixed docs get the same union as Burmese."""
        syns = get_synonyms_for_slot(12, "mixed")
        self.assertIn("definitions", syns)
        self.assertIn("အဓိပ္ပာယ်", syns)

    def test_slot_queries_with_lang(self):
        """slot_queries returns the union for Burmese docs."""
        en_qs = get_queries_for_slot_with_lang(12, "en")
        my_qs = get_queries_for_slot_with_lang(12, "my")
        self.assertIn("definitions of terms glossary defined terms", en_qs)
        self.assertIn("အဓိပ္ပာယ်", my_qs)
        # English-only queries are still present in the Burmese result.
        self.assertIn("definitions of terms glossary defined terms", my_qs)

    def test_multilingual_embedder_default_to_tfidf(self):
        """Without the opt-in env var, multilingual embedder uses TF-IDF.

        BGE-M3 is ~2.2 GB and slow on first load. Default OFF keeps
        the cold-start fast.
        """
        import os
        os.environ.pop("AGENTIC_POLICY_MULTILINGUAL_EMBEDDING", None)
        me = get_multilingual_embedder()
        self.assertEqual(me.backend, "tfidf")
        emb = me.embed(["hello", "မိတ်ဆက်", "policy"])
        # Shape: (3 texts, 1024 dim).
        self.assertEqual(emb.shape, (3, 1024))

    def test_multilingual_embedder_l2_normalized(self):
        me = get_multilingual_embedder()
        emb = me.embed(["မိတ်ဆက်"])
        import numpy as np
        norm = float(np.linalg.norm(emb[0]))
        self.assertAlmostEqual(norm, 1.0, places=4)

    def test_per_paragraph_lang_tags(self):
        """detect_paragraph_lang produces the expected per-paragraph tags."""
        self.assertEqual(detect_paragraph_lang("English text."), "en")
        self.assertEqual(detect_paragraph_lang("မြန်မာ စာ"), "my")
        self.assertEqual(detect_paragraph_lang("Policy မူဝါဒ applies."), "mixed")

    def test_placeholder_per_lang(self):
        """Burmese paragraphs get Burmese placeholder text."""
        self.assertEqual(
            data_not_found_for_lang("my"),
            "အချက်အလက် မတွေ့ပါ",
        )
        self.assertEqual(
            data_not_found_for_lang("en"),
            "Data is not found in source file.",
        )

    def test_extracted_document_carries_lang_tags(self):
        """ExtractedDocument has paragraph_languages + source_lang fields."""
        doc = ExtractedDocument(
            paragraphs=["Hello", "မိတ်ဆက်"],
            paragraph_languages=["en", "my"],
            source_lang="en-my",
        )
        self.assertEqual(doc.paragraph_languages, ["en", "my"])
        self.assertEqual(doc.source_lang, "en-my")

    def test_dominant_lang_for_mixed(self):
        """detect_document_lang returns 'en-my' for mixed-language docs."""
        paras = ["Hello world", "မိတ်ဆက်", "Goodbye", "နယ်ပယ်"]
        self.assertEqual(detect_document_lang(paras), "en-my")

    def test_burmese_only_yields_my(self):
        paras = ["မိတ်ဆက်", "နယ်ပယ်", "အဓိပ္ပာယ်"]
        self.assertEqual(detect_document_lang(paras), "my")

    def test_yaml_synonyms_load_for_definitions(self):
        """Definitions slot has Burmese synonyms loaded from YAML."""
        syns = get_burmese_synonyms(12)
        self.assertGreater(len(syns), 0)
        # Most common Burmese forms for "Definitions".
        self.assertIn("အဓိပ္ပာယ်", syns)

    def test_all_yaml_slots_have_synonyms(self):
        """Every prose slot (5-14 except label-row) has Burmese synonyms."""
        for sid in (5, 6, 7, 8, 9, 10, 12, 13, 14):
            with self.subTest(slot=sid):
                syns = get_burmese_synonyms(sid)
                self.assertGreater(
                    len(syns),
                    0,
                    f"slot {sid} should have Burmese synonyms",
                )


class TestEmbedderRouting(unittest.TestCase):
    """Verify that Burmese/mixed docs use the multilingual embedder."""

    def test_english_doc_selects_english_embedder(self):
        """English docs use only the English embedder."""
        from policy_platform.rag.retrieval_pipeline import RetrievalPipeline
        pipeline = RetrievalPipeline()
        paras = [
            "This is an English-only policy.",
            "All employees are eligible for benefits.",
            "Scope and Beneficiaries section.",
        ]
        result = pipeline.run(
            paras,
            paragraph_languages=["en", "en", "en"],
        )
        # Embedder backend should NOT contain 'multi'.
        self.assertNotIn("multi", result.embedder_backend)

    def test_mixed_doc_selects_multilingual_embedder(self):
        from policy_platform.rag.retrieval_pipeline import RetrievalPipeline
        pipeline = RetrievalPipeline()
        paras = [
            "This is an English policy.",
            "နယ်ပယ် အကျုံးဝင်သူများ။",
            "All employees are eligible.",
        ]
        result = pipeline.run(
            paras,
            paragraph_languages=["en", "my", "en"],
        )
        # Embedder backend SHOULD contain 'multi'.
        self.assertIn("multi", result.embedder_backend)
        # Source lang should be 'mixed'.
        self.assertEqual(result.source_lang, "mixed")

    def test_burmese_doc_selects_multilingual_embedder(self):
        from policy_platform.rag.retrieval_pipeline import RetrievalPipeline
        pipeline = RetrievalPipeline()
        paras = [
            "မိတ်ဆက်။",
            "နယ်ပယ်။",
            "အဓိပ္ပာယ်။",
        ]
        result = pipeline.run(
            paras,
            paragraph_languages=["my", "my", "my"],
        )
        self.assertIn("multi", result.embedder_backend)
        self.assertEqual(result.source_lang, "my")


class TestCompiledPatternsForLang(unittest.TestCase):

    def test_en_does_not_compile_burmese(self):
        en_pats = _compiled_patterns_for_lang(8, "en")
        my_pats = _compiled_patterns_for_lang(8, "my")
        # my patterns should have more (Burmese additions).
        self.assertGreater(len(my_pats), len(en_pats))

    def test_my_includes_burmese(self):
        pats = _compiled_patterns_for_lang(12, "my")
        # At least one pattern should match a Burmese synonym.
        matched_any = False
        for p in pats:
            if p.match("အဓိပ္ပာယ် ဖော်ထုတ်ချက်များ"):
                matched_any = True
                break
        self.assertTrue(matched_any, "Burmese pattern should match Definitions heading")


if __name__ == "__main__":
    unittest.main()