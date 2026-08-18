"""Public surface for the i18n module.

Re-exports the per-paragraph language detector, Burmese normalization,
and synonym/query loaders. The pipeline threads ``lang`` through as a
parameter; this module is the single source of truth for what
``en`` / ``my`` / ``mixed`` mean.
"""
from .lang_detect import detect_paragraph_lang, detect_document_lang
from .burmese_normalize import normalize_burmese, is_burmese_text

__all__ = [
    "detect_paragraph_lang",
    "detect_document_lang",
    "normalize_burmese",
    "is_burmese_text",
]