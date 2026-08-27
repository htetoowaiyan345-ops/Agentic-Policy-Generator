"""Burmese localized strings for the renderer's placeholder text.

The Brain template's English labels are kept as-is; only the
"Data is not found in source file." placeholder gets a Burmese mirror,
applied per-paragraph by ``policy_platform.style.render_not_found_placeholder``.
"""
from __future__ import annotations


# Burmese translation of the standard placeholder.
DATA_NOT_FOUND_MY = "အချက်အလက်မရှိပါ။ မူရင်းဖိုင်တွင်"

# English (canonical).
DATA_NOT_FOUND_EN = "Data is not found in source file."


def data_not_found_for_lang(lang: str) -> str:
    """Return the placeholder string for the given paragraph language.

    ``"my"`` -> Burmese; ``"en"`` and ``"mixed"`` -> English (the
    mixed case keeps the canonical English placeholder because the
    paragraph also has Latin content and the reviewer can read both).
    """
    if lang == "my":
        return DATA_NOT_FOUND_MY
    return DATA_NOT_FOUND_EN


# ---------------------------------------------------------------------------
# Layer B / Layer D — Myanmar keyword constants.
#
# General-purpose Myanmar vocabulary for narrative inference. Mirrors
# the English keyword lists in `extractors/narrative_inference.py`.
# These constants are NOT per-document; they cover the standard
# Myanmar phrases used across policy documents (department-head
# nouns, ownership phrases, audience markers, officer/role titles,
# type-classification nouns). When a future Myanmar PDF uses any of
# these, the corresponding canonical English slot is filled.
# ---------------------------------------------------------------------------

# Ownership / "this function owns the policy" — mirrors _OWNERSHIP_KEYWORDS.
OWNERSHIP_KEYWORDS_MM: tuple[str, ...] = (
    "ပိုင်ဆိုင်သည်",        # is owned by
    "စီမံခန့်ခွဲသည်",        # administered by
    "ကြီးကြပ်သည်",          # overseen by
    "တာဝန်ယူသည်",          # responsible for
    "မူဝါဒပိုင်ရှင်",        # policy owner
    "စီမံအုပ်ချုပ်ရေး",      # administrative responsibility
    "လုပ်ငန်းဆောင်ရွက်မှု",   # functional area
    "ဌာန",                  # department
    "အဖွဲ့",                # team / group
    "ကုမ္ပဏီ",              # company
)

# Audience / "this policy applies to" — mirrors _AUDIENCE_KEYWORDS.
AUDIENCE_KEYWORDS_MM: tuple[str, ...] = (
    "ဝန်ထမ်း",              # employee / staff
    "ဝန်ထမ်းအားလုံး",        # all employees
    "မိသားစုဝင်",           # family member
    "ကာယကံရှင်",           # permanent staff
    "ကန့်လျှော်သူ",          # contract staff
    "အကျုံးဝင်သူ",          # covered party
    "သက်ဆိုင်သူ",          # applicable party
    "သက်ဆိုင်ပါသည်",       # applies to
)

# Officer / owner keywords — mirrors _OFFICER_KEYWORDS.
OFFICER_KEYWORDS_MM: tuple[str, ...] = (
    "မူဝါဒပိုင်ရှင်",        # policy owner
    "မူဝါဒထောက်ခံသူ",      # policy sponsor
    "တာဝန်ရှိသူ",          # responsible officer
    "တာဝန်ခံပုဂ္ဂိုလ်",     # accountable officer
    "တာဝန်ခံ",              # responsible person
    "ခွင့်ပြုသူ",           # approver
    "အတည်ပြုသူ",           # approver (synonym)
)

# Review-date keywords — mirrors _REVIEW_KEYWORDS.
REVIEW_KEYWORDS_MM: tuple[str, ...] = (
    "နောက်ဆုံးပြန်လည်သုံးသပ်",   # last reviewed
    "ပြန်လည်သုံးသပ်ခဲ့သော",    # reviewed
    "သုံးသပ်ရက်စွဲ",         # review date
    "ပြန်လည်သုံးသပ်ချက်",     # last review
)

# Purpose-intro / brief-description patterns — mirrors _BRIEF_INTRO_PATTERNS.
BRIEF_INTRO_PATTERNS_MM: tuple[str, ...] = (
    r"^\s*ဤမူဝါဒသည်",          # "This policy..."
    r"^\s*ဤစံသည်",             # "This standard..."
    r"^\s*ဤလုပ်ထုံးသည်",        # "This procedure..."
    r"^\s*ဤလမ်းညွှန်ချက်သည်",   # "This guideline..."
    r"^\s*ရည်ရွယ်ချက်",        # "Purpose:"
    r"^\s*အကျဉ်းချုပ်\s*[:\-]",  # "Summary:"
)

# Reason-intro patterns — mirrors _REASON_INTRO_PATTERNS.
REASON_INTRO_PATTERNS_MM: tuple[str, ...] = (
    r"^\s*ဤမူဝါဒသည်\s+(လိုအပ်|လိုအပ်သည်|လိုအပ်သော)",   # "This policy is required..."
    r"^\s*ဤမူဝါဒသည်\s+(ရည်ရွယ်|ရေးဆွဲ|ဖန်တီး)",
    r"^\s*(ရည်ရွယ်ချက်|ပန်းတိုင်)",   # "purpose/goal:"
)

# Department-head nouns — mirrors _DEPARTMENT_HEAD_NOUNS.
# General Myanmar vocabulary for organizational units (not per-document).
DEPARTMENT_HEAD_NOUNS_MM: tuple[str, ...] = (
    "ဌာန",                  # department
    "အဖွဲ့",                # team / group
    "ကုမ္ပဏီ",              # company
    "စီမံခန့်ခွဲမှု",        # management / administration
    "ဝန်ကြီးဌာန",          # ministry / department
    "ရေးရာဌာန",            # administration
    "ဘဏ္ဍာရေးဌာန",          # finance department
    "လူ့စွမ်းအားရင်းမြစ်",    # human resources
    "သတင်းအချက်အလက်",      # information
    "နည်းပညာ",              # technology
    "စစ်ဆေးရေး",            # audit / inspection
    "အကျိုးခံစားခွင့်",      # benefits
    "လစာ",                  # payroll
    "ဈေးကွက်",              # marketing
    "ရောင်းဝယ်ရေး",         # sales
    "ထုတ်လုပ်ရေး",          # production / manufacturing
    "လည်ပတ်မှု",           # operations
)

# Type classification — Myanmar word → English canonical.
# Used by Layer G Type inference.
TYPE_INFERENCE_MM: dict[str, str] = {
    "မူဝါဒ": "Policy",
    "မူဝါဒများ": "Policies",
    "စံ": "Standard",
    "စံနှုန်း": "Standard",
    "လုပ်ထုံး": "Procedure",
    "လုပ်ထုံးစဉ်": "Procedure",
    "လမ်းညွှန်ချက်": "Guideline",
    "မူဘောင်": "Framework",
    "အချက်ကျမ်း": "Charter",
    "ညွှန်ကြားချက်": "Directive",
    "စည်းမျဉ်း": "Rule",
    "စည်းကမ်း": "Regulation",
    "လုပ်နည်းစနစ်": "Protocol",
    "လက်စွဲ": "Manual",
    "လက်စွဲစာအုပ်": "Handbook",
    "ကျင့်ဝတ်": "Code",
}


# ---------------------------------------------------------------------------
# Layer B helpers
# ---------------------------------------------------------------------------

# Myanmar Unicode range (same regex used in ocr_fallback but exposed here
# for re-use by other modules without circular import).
_MYANMAR_RANGE = (
    "\u1000-\u109F\uAA60-\uAA7F\uA9E0-\uA9FF"
)


def has_burmese(s: str) -> bool:
    """Return True if `s` contains any Myanmar Unicode codepoint."""
    import re as _re
    return bool(_re.search(f"[{_MYANMAR_RANGE}]", s))