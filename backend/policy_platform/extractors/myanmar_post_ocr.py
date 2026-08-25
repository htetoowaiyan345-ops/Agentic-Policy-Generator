"""Post-OCR correction for Myanmar (Burmese) text.

Based on error analysis from:
  "myOCR: Optical Character Recognition for Myanmar Language
   with Post-OCR Error Correction" (Aung, Thu, Oo, iSAI-NLP 2024)

Key findings from the paper:
  - Top visual confusion pairs: သ→လ, ၀→ဝ, ဘ→တ, ထ→ဆ, etc.
  - Myanmar vowels are typed/stored AFTER consonant but rendered BEFORE
  - OCR sometimes outputs visual order instead of logical order
  - N-gram + SymSpell correction reduces WER from 9.18% to ~2%

This module applies:
1. Aggressive garbage line filtering (drop PDF visual artifacts)
2. Rule-based corrections for common Tesseract Myanmar OCR errors
3. Unicode normalization
4. Context-aware confusion pair corrections
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Myanmar Unicode block constants
# ---------------------------------------------------------------------------

# Consonants U+1000–U+1021
_MYANMAR_CONSONANT = re.compile(r"[\u1000-\u1021]")
# Independent vowels U+1023–U+102A
_MYANMAR_VOWEL = re.compile(r"[\u1023-\u102A]")
# Dependent vowels U+102B–U+103A
_MYANMAR_DEP_VOWEL = re.compile(r"[\u102B-\u103A]")
# Myanmar signs and marks
_MYANMAR_SIGN = re.compile(r"[\u103B-\u109F\uAA60-\uAA7F]")
# Virama (asat) U+103A
_MYANMAR_VIRAMA = "\u103A"
# Kinzi U+1064
_MYANMAR_KINZI = "\u1064"
# Full stop
_MYANMAR_PUNCT = re.compile(r"[\u104A\u104B\u104C\u104D\u104E\u104F]")
# All Myanmar block
_MYANMAR_ALL = re.compile(r"[\u1000-\u109F\uAA60-\uAA7F\uA9E0-\uA9FF]")
# Myanmar digits
_MYANMAR_DIGIT = re.compile(r"[\u1040-\u1049]")
# A "readable Myanmar word" = 4+ consecutive Myanmar chars
_READABLE_WORD = re.compile(r"[\u1000-\u109F\uAA60-\uAA7F]{4,}")


# ---------------------------------------------------------------------------
# Top 10 visual confusion pairs from myOCR paper (for context-aware use)
# ---------------------------------------------------------------------------

# Table III from the paper: common confusion pairs
# (ocr_output, correct, frequency)
_CONFUSION_PAIRS = [
    ("\u101C", "\u1019", 361),  # လ → သ
    ("\u101D", "\u1040", 236),  # ဝ → ၀
    ("\u1025", "\u1029", 152),  # ဥ → ီ
    ("\u1017", "\u1010", 136),  # ဘ → တ
    ("\u102E", "\u102D\u102E", 112),  # ◌ီ → ◌ ိ
    ("\u1010", "\u1011", 107),  # ထ → ဆ
    ("\u1011", "\u1010", 104),  # ဆ → ထ
    ("\u101D", "\u1010", 94),   # ဝ → ထ
    ("\u1085", "\u103B", 85),   # ဈ → ◌ျ
    ("\u1047", "\u101A", 81),   # ၇ → ရ
]


# ---------------------------------------------------------------------------
# Mark reordering rules
# ---------------------------------------------------------------------------

# Myanmar Unicode canonical order for dependent marks:
# 1. Medial ra (ြ) U+103C
# 2. Medial ya (ျ) U+103B
# 3. Medial wa (ွ) U+103D
# 4. Medial ha (ှ) U+103E
# 5. Vowel i (ိ) U+102D
# 6. Vowel ii (ီ) U+102E
# 7. Vowel u (ု) U+102F
# 8. Vowel uu (ူ) U+1030
# 9. Vowel e (ေ) U+1031
# 10. Vowel ai (ဲ) U+1032
# 11. Anusvara (ံ) U+1036
# 12. Visarga (း) U+1038
# 13. Asat/virama (်) U+103A

_MARK_ORDER: dict[str, int] = {
    "\u103C": 1,
    "\u103B": 2,
    "\u103D": 3,
    "\u103E": 4,
    "\u102D": 5,
    "\u102E": 6,
    "\u102F": 7,
    "\u1030": 8,
    "\u1031": 9,
    "\u1032": 10,
    "\u1036": 11,
    "\u1038": 12,
    "\u103A": 13,
}


def _reorder_marks(text: str) -> str:
    """Reorder Myanmar dependent marks to canonical Unicode order."""
    result = []
    i = 0
    while i < len(text):
        ch = text[i]

        if _MYANMAR_CONSONANT.match(ch):
            result.append(ch)
            i += 1

            marks = []
            while i < len(text):
                m = text[i]
                if _MYANMAR_DEP_VOWEL.match(m) or _MYANMAR_SIGN.match(m):
                    marks.append(m)
                    i += 1
                elif m == _MYANMAR_VIRAMA:
                    marks.append(m)
                    i += 1
                elif m == _MYANMAR_KINZI:
                    marks.append(m)
                    i += 1
                else:
                    break

            if marks:
                marks.sort(key=lambda m: _MARK_ORDER.get(m, 99))
                result.extend(marks)
        elif _MYANMAR_ALL.match(ch):
            result.append(ch)
            i += 1
        else:
            result.append(ch)
            i += 1

    return "".join(result)


# ---------------------------------------------------------------------------
# AGGRESSIVE garbage line filtering
# ---------------------------------------------------------------------------

def _is_readable_line(line: str) -> bool:
    """Return True if line contains at least one readable Myanmar word.

    A readable word = 4+ consecutive Myanmar characters that are joined
    normally (not all fragmented).
    """
    readable_words = _READABLE_WORD.findall(line)
    if not readable_words:
        return False

    # Must have at least one readable word
    return True


def _is_garbage_line(line: str) -> bool:
    """Return True if line is clearly garbage/PDF noise.

    AGGRESSIVE: we drop lines that are mostly garbage.
    A line is KEPT if it has substantial readable Myanmar content.
    """
    stripped = line.strip()
    if not stripped:
        return True

    # Rule 1: Very short lines
    if len(stripped) < 6:
        return True

    # Count various character types
    myanmar_chars = len(_MYANMAR_ALL.findall(stripped))
    latin_chars = len(re.findall(r"[A-Za-z]", stripped))
    digit_chars = sum(1 for c in stripped if c.isdigit() or _MYANMAR_DIGIT.match(c))
    symbol_chars = len(re.findall(r"[¢°§¤©®™¥£(){}\[\]<>!@#$%&*~`|\\/]", stripped))

    total = len(stripped)

    # Must have Myanmar text
    if myanmar_chars < 5:
        return True

    # Rule 2: Too many Latin chars (likely mixed garbage)
    if latin_chars > 2 and latin_chars / max(myanmar_chars, 1) > 0.2:
        return True

    # Rule 3: PDF symbols (¢, °, §, ¤, ©, ®, ™) - drop if any
    if symbol_chars > 0:
        return True

    # Rule 4: Broken kinzi + Latin fragments
    if re.search(r"င်[လသကဂ]\s+[A-Za-z]{2,}", stripped):
        return True

    # Rule 5: Latin fragments from PDF
    latin_garbage = ["SASS", "oP ", " ec", " oc", "ce ", "Co ", " cc",
                     "fo}", "onan", " gol", "Sol", "Né", "Ed)", "aySqep",
                     "GCsENSPO", "spocaps"]
    if any(pat in stripped for pat in latin_garbage):
        return True

    # Rule 6: Short Myanmar words are fragmented
    words = stripped.split()
    myanmar_words = [w for w in words if _MYANMAR_ALL.match(w)]
    if not myanmar_words:
        return True
    short_count = sum(1 for w in myanmar_words if len(w) <= 2)
    if len(myanmar_words) >= 3 and short_count / len(myanmar_words) > 0.6:
        return True

    # Rule 7: Must have at least one readable word (4+ Myanmar chars)
    if not _is_readable_line(stripped):
        return True

    # Rule 8: Numbers/punctuation-heavy lines
    if digit_chars > 0 and digit_chars / total > 0.5:
        return True

    # Rule 9: Has random symbols between Myanmar chars
    if re.search(r"[\u1040-\u1049][\s]*[)\]}!@#$%]", stripped):
        return True

    # Rule 10: Lines with [238: 89] or similar bracket numbers
    if re.search(r"^\s*\d+[\[\(].+[\]\)]\s*$", stripped):
        return True

    # Rule 11: Lines starting with §, ¢, °, or other PDF symbols
    if re.match(r"^[\s]*[§¢°¤©®™¥£04-9!@#$%&*(){}[\]<>]", stripped):
        return True

    # Rule 12: Lines that are mostly bracketed items
    if stripped.count("[") > 2 or stripped.count("(") > 4:
        return True

    # Rule 13: Lines with "- " or "_-" or " _ " in middle (PDF artifacts)
    # ONLY drop if surrounded by short fragments (real PDF artifact),
    # NOT if surrounded by readable words (legitimate title separator like "Policy - Title")
    if re.search(r"\s[-_]\s", stripped) and len(stripped) > 20:
        # Check if the chars around the dash are readable Myanmar words
        # If so, it's a legitimate separator — keep it
        m = re.search(r"\s[-_]\s", stripped)
        if m:
            before = stripped[max(0, m.start()-10):m.start()].strip()
            after = stripped[m.end():m.end()+10].strip()
            # If both sides have Myanmar content with readable words, keep
            before_has_my = bool(before) and _MYANMAR_ALL.search(before)
            after_has_my = bool(after) and _MYANMAR_ALL.search(after)
            if before_has_my and after_has_my:
                # Legitimate title separator — don't drop
                pass
            else:
                return True

    # Rule 14: Lines starting with garbage prefixes
    garbage_prefixes = ["De ", "oll ", "Ju ", "coe ", "Os ", "fe ", "ey ",
                        "ei ", "y 1", "y 9", "i ", "C ", "T ", "Q ", "f"]
    if any(stripped.startswith(p) for p in garbage_prefixes):
        return True

    # Rule 15: Lines with "!" followed by Myanmar without context
    if re.search(r"!\s*[\u1021-\u102A]", stripped):
        return True

    # Rule 16: Lines with "[" or "]" in middle (PDF column wrap)
    if re.search(r"[\u1000-\u109F]\[[\u1000-\u109F]", stripped):
        return True

    # Rule 17: Has "ၤၤ" or more than 2 visargas (corruption marker)
    if stripped.count("ၤ") > 2 or stripped.count("ၤၤ") > 0:
        return True

    # Rule 18: Has multi-char punctuation clusters (PDF noise)
    if re.search(r"[!@#$%&*]{2,}", stripped):
        return True

    # Rule 19: Lines with "|" in middle (table separators)
    # Only matches pipe (|) since dash (-) and underscore (_) are legitimate title separators
    if re.search(r"\s\|\s", stripped):
        return True

    # Rule 20: Lines that are mostly Myanmar numerals with little text
    if stripped.count("၀") + stripped.count("၁") + stripped.count("၂") + stripped.count("၃") + stripped.count("၄") > 5 and len(stripped) < 30:
        return True

    # Rule 21: Has random "?" or "!" patterns (corruption markers)
    if re.search(r"\?[၀-၉]", stripped):
        return True

    return False


def _strip_garbage_lines(text: str) -> str:
    """Drop all garbage lines. Keep only readable Myanmar lines.

    This is AGGRESSIVE - we'd rather lose 30% of content than have
    corrupted text in the output.
    """
    lines = text.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_garbage_line(stripped):
            continue
        result.append(line)
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Garbage pattern stripping (inline)
# ---------------------------------------------------------------------------

_GARBAGE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bFAV\s*city\b", re.IGNORECASE),
    re.compile(r"\bwy\s*Holdings\b", re.IGNORECASE),
    re.compile(r"\bwosa\d+\b", re.IGNORECASE),
    re.compile(r"\bGty\s*Holdings\b", re.IGNORECASE),
    re.compile(r"\bDOC\s*PEO\b", re.IGNORECASE),
    re.compile(r"\bsagsz\d+[:\)]*\b", re.IGNORECASE),
    re.compile(r"\bc0dz:\S*\b", re.IGNORECASE),
    re.compile(r"\b[Ff][Aa][Vv]\b"),
    re.compile(r"\b[Ss]ector\s*\(\d+\)"),
    re.compile(r"\baySqep\d*\b", re.IGNORECASE),
    re.compile(r"\bspocaps\b", re.IGNORECASE),
    re.compile(r"\bCity\s*Holdi\b"),
    re.compile(r"\bity\s*Holdings\b"),
]


def _strip_garbage_inline(text: str) -> str:
    """Remove known garbage patterns from a single line."""
    for pattern in _GARBAGE_PATTERNS:
        text = pattern.sub("", text)
    return text


# ---------------------------------------------------------------------------
# Composite character fixes
# ---------------------------------------------------------------------------

def _fix_composite_chars(text: str) -> str:
    """Fix common composite character issues in Myanmar OCR."""
    _asat = "\u103A"
    _anusvara = "\u1036"
    _visarga = "\u1038"
    _double_asat_re = re.compile(_asat + _asat)
    _anusvara_vowel_re = re.compile(
        _anusvara + r"([\u102D-\u1032])"
    )

    # Fix: orphaned asat (virama) without preceding consonant
    text = re.sub(
        r"(?<![\u1000-\u1021\u103B-\u103E])" + _asat, "", text
    )

    # Fix: double asat
    text = _double_asat_re.sub(_asat, text)

    # Fix: anusvara before vowel (should be after)
    text = _anusvara_vowel_re.sub(
        lambda m: m.group(1) + _anusvara, text
    )

    # Fix: visarga before asat (should be after)
    text = text.replace(_visarga + _asat, _asat + _visarga)

    # Fix: broken kinzi sequences
    # င + virama (U+1039) should be င + asat (U+103A)
    text = text.replace("\u1003\u1039", "\u1003\u103A")

    # Fix: virama → asat for all stacked consonants
    # Tesseract sometimes outputs virama (U+1039) instead of asat (U+103A)
    text = text.replace("\u1039", "\u103A")

    return text


# ---------------------------------------------------------------------------
# Duplicate removal
# ---------------------------------------------------------------------------

def _remove_duplicates(text: str) -> str:
    """Remove consecutive duplicate Myanmar characters."""
    result = []
    prev = ""
    for ch in text:
        if ch == prev and _MYANMAR_ALL.match(ch):
            continue
        result.append(ch)
        prev = ch
    return "".join(result)


# ---------------------------------------------------------------------------
# Targeted confusion-pair correction (from myOCR paper)
# ---------------------------------------------------------------------------

# Context-aware single-character replacements. Only apply in safe contexts
# (inside Myanmar words, not standalone).
def _apply_confusion_pairs(text: str) -> str:
    """Apply myOCR paper confusion pairs with context constraints.

    Safe replacements (applied unconditionally inside Myanmar words):
      - ဆ↔ထ (၁၀၄/၁၀၇ occurrences — common confusion)
      - ဈ → ◌ျ (medial ya reconstruction)
    """
    # ဆ (U+1011) ↔ ထ (U+1010): bidirectional swap is risky.
    # Only fix ဆ when it appears in specific patterns.
    # ထ → ဆ when followed by certain vowels
    text = text.replace("\u1011\u102E", "\u1010\u102E")  # ဆီ → ထီ
    text = text.replace("\u1011\u103A", "\u1010\u103A")  # ဆ် → ထ်

    # ဝ (U+101D) → ၀ (U+1040): Myanmar digit zero vs letter
    # This is ambiguous — skip for now

    # ဈ (U+1085) → ◌ျ (U+103B): medial ya
    # ဈ is a consonant that should be followed by medial ya
    text = text.replace("\u1085", "\u103B")

    # ၇ (U+1047) → ရ (U+101A): digit 7 vs letter ra
    # Only when ၇ appears in Myanmar text context (not as a digit)
    # Skip — too ambiguous

    # ဘ (U+1017) → တ (U+1010): be vs ta
    # Skip — context-dependent

    # လ (U+101C) → သ (U+1019): la vs tha
    # Skip — common letters, context matters

    # ဥ (U+1025) → ီ (U+1029): u → ii
    # Skip — independent vowels rarely confused in context

    return text


# ---------------------------------------------------------------------------
# Main correction pipeline
# ---------------------------------------------------------------------------

def correct_myanmar_ocr(text: str) -> str:
    """Apply post-OCR corrections to Myanmar text.

    Pipeline:
    1. Strip inline garbage patterns (FAV city, wy Holdings, etc.)
    2. Fix composite character issues (broken kinzi, double asat, etc.)
    3. Reorder dependent marks to canonical Unicode order
    4. Remove duplicate characters
    5. Unicode NFC normalization
    6. AGGRESSIVELY filter out garbage lines (keep only readable Myanmar)

    Args:
        text: Raw OCR output text

    Returns:
        Corrected text with garbage lines removed
    """
    if not text:
        return text

    # Step 1: Strip inline garbage patterns
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        cleaned = _strip_garbage_inline(line)
        cleaned_lines.append(cleaned)
    text = "\n".join(cleaned_lines)

    # Step 2: Fix composite characters
    text = _fix_composite_chars(text)

    # Step 3: Reorder marks to canonical Unicode order
    text = _reorder_marks(text)

    # Step 4: Remove duplicate characters
    text = _remove_duplicates(text)

    # Step 5: Unicode NFC normalization
    text = unicodedata.normalize("NFC", text)

    # Step 6: AGGRESSIVELY filter out garbage lines
    # This is the key step - drop anything not cleanly readable
    text = _strip_garbage_lines(text)

    # Step 7: Apply myOCR paper confusion pairs
    text = _apply_confusion_pairs(text)

    # Final cleanup
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
