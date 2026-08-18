"""Burmese RAG retrieval queries.

Mirrors ``policy_platform.rag.slot_queries.SLOT_QUERIES`` so each slot
has natural-language queries in Burmese for retrieval scoring. The
union of English + Burmese queries is used when the document contains
Burmese paragraphs; English-only docs use only the English list.

These are NOT translations of the English queries - they are
Burmese-language phrasings of "what content lives in this slot",
written so BM25 tokenization matches well against Burmese source text.
"""
from __future__ import annotations

from typing import Dict, List


SLOT_QUERIES_MY: Dict[int, List[str]] = {
    5: [  # Introduction
        "မိတ်ဆက်",
        "မူဝါဒ၏ ရည်ရွယ်ချက်",
        "နောက်ခံ",
        "ကြိုတင်အခြေအနေ",
    ],
    6: [  # Policy Statement
        "မူဝါဒ ထုတ်ပြန်ချက်",
        "ကုမ္ပဏီမူဝါဒ",
        "မူဝါဒ အခြေခံ",
    ],
    7: [  # Applicable Sectors
        "သက်ဆိုင်ရာ ကဏ္ဍများ",
        "အသုံးချနယ်ပယ်",
    ],
    8: [  # Scope
        "နယ်ပယ်",
        "အကျုံးဝင်သူများ",
        "လုပ်ပိုင်ခွင့်",
        "ဝန်ထုတ်ဝန်ပိုး",
    ],
    9: [  # Exclusions
        "ချွင်းချက်များ",
        "မပါဝင်သည့်အချက်များ",
        "ကန့်သတ်ချက်များ",
    ],
    10: [  # Type of Benefits
        "အကျိုးခံစားခွင့် အမျိုးအစားများ",
        "ခံစားခွင့်",
        "ထောက်ပံ့မှု",
    ],
    12: [  # Definitions
        "အဓိပ္ပာယ် ဖော်ထုတ်ချက်များ",
        "အဓိပ္ပာယ်",
        "ဝေါဟာရ",
        "သတ်မှတ်ချက်",
    ],
    13: [  # Related Policies
        "ဆက်စပ်မူဝါဒများ",
        "ဆက်စပ် စာရွက်စာတမ်း",
        "ကိုးကားချက်",
    ],
    14: [  # History
        "မှတ်တမ်း",
        "ဗားရှင်း",
        "ပြင်ဆင်မှု",
        "ထုတ်ဝေသည့်ရက်စွဲ",
    ],
}


def get_queries_for_slot_my(slot_id: int) -> List[str]:
    """Return Burmese queries for the given slot; empty list if none."""
    return list(SLOT_QUERIES_MY.get(int(slot_id), []))