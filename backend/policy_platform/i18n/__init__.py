"""Public surface for the i18n module.

Re-exports Burmese synonym/query loaders. The pipeline threads ``lang``
through as a parameter; this module is the single source of truth for
``en`` / ``my`` / ``mixed`` handling.
"""
from .burmese_synonyms import (
    get_burmese_synonyms,
    get_all_burmese_synonyms,
    reset_cache,
)

__all__ = [
    "get_burmese_synonyms",
    "get_all_burmese_synonyms",
    "reset_cache",
]