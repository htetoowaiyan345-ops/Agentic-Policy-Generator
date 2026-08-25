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

Known Limitations
-----------------
- Empty F3 glyphs (CIDs 0x00F2, 0x00F3, 0x00F4, 0x0155): Source PDFs
  created from Word documents using Myanmar Text font may lack glyph
  data for these codepoints. Tesseract cannot recover missing visual
  data; these appear as silent gaps in the extracted text. Frequency
  in affected documents: ~3-5 gaps per page. Workaround: request
  original DOCX source for documents where critical content is missing.
- Stacked ligature errors: At DPI=80, stacked consonants (e.g., န်
  ligature) render at sub-pixel resolution. Tesseract's LSTM sees
  ambiguous shapes and may emit wrong Unicode (e.g., နး် instead of
  န်). Post-correction cannot safely fix these without a dictionary;
  this is the dominant remaining corruption type (~15-20 per page).
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
    """Reorder Myanmar dependent marks to canonical Unicode order.

    IMPORTANT: Two stacked-ligature patterns require special handling:

    1. When marks include an asat (U+103A) that is followed by more
       marks (a STACKED LIGATURE pattern like န်း), the asat is the
       ligature-stack marker and MUST stay in its position between
       the consonant and the following marks. Sorting blindly by
       canonical order (visarga=12 before asat=13) would move the
       asat AFTER the visarga and produce the wrong sequence (e.g.,
       နး် instead of န်း).

    2. When the cluster has NO asat but has a visarga (U+1038) AND
       a vowel mark (e.g., များ = medial-ya + aa + visarga), the
       visarga should sort LAST. The default _MARK_ORDER places
       visarga before dependent vowels (because dependent-vowel-i
       range is U+102D-U+1032 with order 5-10, and visarga is 12),
       but `ာ` (U+102C) is a DEPENDENT vowel NOT in _MARK_ORDER
       (default 99), so it sorts AFTER visarga. Fix: visarga always
       sorts last in the no-asat case.
    """
    result = []
    i = 0
    while i < len(text):
        ch = text[i]

        if _MYANMAR_CONSONANT.match(ch):
            result.append(ch)
            i += 1

            marks: list[str] = []
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
                # Case 1: asat present → split at asat boundary.
                if _MYANMAR_VIRAMA in marks:
                    asat_idx = marks.index(_MYANMAR_VIRAMA)
                    before = sorted(
                        marks[:asat_idx],
                        key=lambda m: _MARK_ORDER.get(m, 99),
                    )
                    after = sorted(
                        marks[asat_idx + 1:],
                        key=lambda m: _MARK_ORDER.get(m, 99),
                    )
                    result.extend(before)
                    result.append(_MYANMAR_VIRAMA)
                    result.extend(after)
                # Case 2: no asat, visarga present → push visarga last.
                elif "\u1038" in marks:
                    other = [m for m in marks if m != "\u1038"]
                    other.sort(key=lambda m: _MARK_ORDER.get(m, 99))
                    result.extend(other)
                    result.append("\u1038")
                else:
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

    # NOTE: Phase-4 added stacked-ligature normalization and triple-mark
    # collapse. The stacked-ligature fix is preserved as a SAFE normalization
    # (whitespace inside ligature is unambiguously wrong). The triple-mark
    # collapse was removed because it could destroy valid sequences.

    # Stacked ligature normalization: OCR may output medial-ya + medial-ha
    # with intervening whitespace. Canonical form has no whitespace inside
    # the two medials. This is conservative — only triggers when whitespace
    # is genuinely BETWEEN the two medial marks.
    _STACKED_LIGATURE_RE = re.compile(
        r"(\u103B[ \t]+\u103E)"  # ya + whitespace + ha
        r"|(\u103E[ \t]+\u103B)"  # ha + whitespace + ya
        r"|(\u103C[ \t]+\u103B)"  # ra + whitespace + ya
    )

    def _join_stacked(m: re.Match) -> str:
        return m.group(0).replace(" ", "").replace("\t", "")

    text = _STACKED_LIGATURE_RE.sub(_join_stacked, text)

    return text


def _fix_myanmar_punctuation(text: str) -> str:
    """Fix common Myanmar OCR punctuation errors.

    Two substitutions:

      1. U+1064 (ၤ, archaic) → U+104B (။, modern): Tesseract commonly
         mis-recognizes the modern sentence-final marker as the archaic
         form when it follows Myanmar characters before whitespace/EOL.

      2. ASCII colon (U+003A) → U+104B (။): Tesseract's Myanmar model
         lacks sufficient training on the tiny punctuation dot (။),
         so it falls back to the visually similar two-pixel ASCII colon.
         Guard with negative lookbehind to preserve timestamps
         (e.g., `10:30`) and ratios (e.g., `1:2`).
    """
    # 1. Myanmar char + U+1064 + (whitespace/eol) → Myanmar char + U+104B
    text = re.sub(
        r"([\u1000-\u109F\uAA60-\uAA7F\uA9E0-\uA9FF])ၤ(\s|$)",
        r"\1။\2",
        text,
    )
    # 2. Myanmar char + ASCII colon + (whitespace/EOL/punct) → Myanmar + ။
    #    Lookbehind blocks digit-preceded colons (timestamps).
    text = re.sub(
        r"([\u1000-\u109F\uAA60-\uAA7F\uA9E0-\uA9FF])(?<!\d):(\s|$|[,;])",
        r"\1။\2",
        text,
    )
    return text


def _fix_visarga_compound_vowel(text: str) -> str:
    """Fix Tesseract confusion of visarga (း) with vowel-u (ု) before ာ.

    The compound vowel ုာ (ta-thai-htoe, U+102F + U+102C) is sometimes
    OCR'd as းာ (visarga + aa). The pattern `consonant + း + ာ` is
    unambiguous in correct Burmese: it never appears after a bare
    consonant (visarga only appears after an already-attached vowel
    cluster). Safe to substitute unconditionally.
    """
    # consonant + း + ာ → consonant + ု + ာ
    text = re.sub(
        r"([\u1000-\u1021])းာ",
        r"\1ုာ",
        text,
    )
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

    Safe replacements (only verified-safe pairs, applied unconditionally):
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

    # NOTE: Phase-3 added pairs (၀→ဝ, ◌ီ→◌ိ, ဝ→ထ, သ→လ) were reverted
    # because they over-fired on legitimate text and caused more
    # corruption than they fixed. The myOCR paper's top confusions
    # (သ→လ, ◌ီ→◌ိ) cannot be safely auto-corrected without a
    # Myanmar dictionary or full word-level model.

    return text


# ---------------------------------------------------------------------------
# Main correction pipeline
# ---------------------------------------------------------------------------

def correct_myanmar_ocr(text: str) -> str:
    """Apply post-OCR corrections to Myanmar text.

    Pipeline:
    1. Strip inline garbage patterns (FAV city, wy Holdings, etc.)
    2. Fix asat word-breaks (Strategy 2: asat+space+consonant → asat+consonant)
    3. Join broken Myanmar syllables (Strategy 1: cluster+space+cluster → cluster+cluster)
    4. Fix composite character issues (broken kinzi, double asat, etc.)
    5. Reorder dependent marks to canonical Unicode order
    6. Remove duplicate characters
    7. Unicode NFC normalization
    8. AGGRESSIVELY filter out garbage lines (keep only readable Myanmar)
    9. Apply myOCR paper confusion pairs

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

    # Step 2: Fix asat word-breaks (Strategy 2).
    # Asat (U+103A) ALWAYS attaches to the following consonant, marking
    # the END of the preceding syllable. OCR incorrectly inserts a space
    # between asat and the next consonant, fragmenting words like
    # ``သည့်ရန်နှင့်`` into ``သည့ ရန်နှင့``. Remove the space.
    text = _fix_asat_word_breaks(text)

    # Step 3: Join broken Myanmar syllables (Strategy 1).
    # OCR sometimes breaks Myanmar syllables in the middle, leaving
    # ``cluster<space>cluster`` where they should be ``clustercluster``.
    # Apply Myanmar syllable-cluster detection to identify and join.
    text = _join_myanmar_syllables(text)

    # Step 4: Fix composite characters
    text = _fix_composite_chars(text)

    # Step 4b: Fix Myanmar punctuation errors (ၤ → ။, ASCII : → ။)
    text = _fix_myanmar_punctuation(text)

    # Step 4c: Fix visarga compound vowel confusion (းာ → ုာ)
    text = _fix_visarga_compound_vowel(text)

    # Step 5: Reorder marks to canonical Unicode order
    text = _reorder_marks(text)

    # Step 6: Remove duplicate characters
    text = _remove_duplicates(text)

    # Step 7: Unicode NFC normalization
    text = unicodedata.normalize("NFC", text)

    # Step 8: AGGRESSIVELY filter out garbage lines
    # This is the key step - drop anything not cleanly readable
    text = _strip_garbage_lines(text)

    # Step 9: Apply myOCR paper confusion pairs
    text = _apply_confusion_pairs(text)

    # Final cleanup
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ---------------------------------------------------------------------------
# Strategy 2: Asat word-break removal
# ---------------------------------------------------------------------------

# Consonant + asat + whitespace(s) + consonant -> remove whitespace.
# Applies to single-byte asat only (not double-asat or asat-with-other-marks).
_MYANMAR_CONSONANT_RNG = (
    "\u1000-\u1021"   # main consonants
    "\u103F"            # great sa
    "\u104E"            # foundational sign
    "\u1050-\u1059"    # extended consonants
    "\u1060-\u1063"    # other extensions
    "\u1066-\u106D"    # more extensions
    "\u106E-\u1070"    # western extensions
    "\u1071-\u1074"    # nnya, ttha, etc.
    "\u1075-\u1081"    # more
    "\u1082-\u1086"    # more
    "\u108E-\u108E"    # shan digit extensions
    "\u109E-\u109F"    # mon mark, etc.
)
_ASAT_BREAK_RE = re.compile(
    rf"([{_MYANMAR_CONSONANT_RNG}])"  # preceding consonant
    r"(\u103A)"                         # asat
    r"[ \t]+"                           # one or more horizontal whitespace
    r"([{_MYANMAR_CONSONANT_RNG}])".format(_MYANMAR_CONSONANT_RNG=_MYANMAR_CONSONANT_RNG)
)


def _fix_asat_word_breaks(text: str) -> str:
    """Remove whitespace between asat (U+103A) and the following consonant.

    Asat always attaches to the FOLLOWING syllable, never separated by
    space in correct Myanmar text. OCR breaks like ``သည့ ရန်နှင့``
    become ``သည့်ရန်နှင့်``.
    """
    prev = None
    while prev != text:
        prev = text
        text = _ASAT_BREAK_RE.sub(r"\1\2\3", text)
    return text


# ---------------------------------------------------------------------------
# Strategy 1: Myanmar syllable joining
# ---------------------------------------------------------------------------

# Myanmar cluster = consonant + dependent marks (vowels, medials, asat, etc.)
# C + (V|M)* where V|M = dependent vowel, medial, asat, etc.
_MYANMAR_DEP_MARK_RNG = (
    "\u102B-\u1032"   # dependent vowels
    "\u1036"            # anusvara
    "\u1037"            # dot below
    "\u1038"            # visarga
    "\u103A"            # asat
    "\u103B-\u103E"    # medials (ya, ra, wa, ha)
    "\u105E"            # mon vowel sign aa
    "\u105F"            # mon vowel sign e
    "\u1060-\u1064"    # various extensions
    "\u1067-\u106D"    # extensions
    "\u1071-\u1074"    # extensions
    "\u1082"            # shan medial wa
    "\u1083"            # shan medial ya
    "\u1084"            # shan medial na
    "\u1085-\u1086"    # extensions
    "\u108D-\u108E"    # extensions
    "\u109E"            # mon tone
)
_MYANMAR_CLUSTER_RE = re.compile(
    rf"[{_MYANMAR_CONSONANT_RNG}][{_MYANMAR_DEP_MARK_RNG}]*"
)


def _join_myanmar_syllables(text: str) -> str:
    """Join Myanmar syllable clusters that OCR broke apart with whitespace.

    Targets single-space separations between two Myanmar clusters. Does
    NOT join:
      - across newline boundaries
      - across multi-space gaps (paragraph signal)
      - when adjacent cluster contains ASCII / digit / Myanmar punctuation
      - clusters already joined (no double-join)
    """
    _JOIN_RE = re.compile(
        rf"({_MYANMAR_CLUSTER_RE.pattern})[ \t]({_MYANMAR_CLUSTER_RE.pattern})"
    )
    # Block: don't join if either side contains ASCII letter/digit.
    _BAD_JOIN_LEFT = re.compile(r"[A-Za-z0-9]")
    _BAD_JOIN_RIGHT = re.compile(r"[A-Za-z0-9]")
    # Block: don't join if right side starts with Myanmar punctuation.
    _MM_PUNCT_RE = re.compile(r"[\u104A-\u104F\u1050-\u1059]")

    # Process line-by-line so we don't join across paragraph boundaries.
    out_lines = []
    for line in text.split("\n"):
        prev = None
        cur = line
        while prev != cur:
            prev = cur
            cur = _JOIN_RE.sub(
                lambda m: (
                    m.group(0)
                    if (
                        _BAD_JOIN_LEFT.search(m.group(1))
                        or _BAD_JOIN_RIGHT.search(m.group(2))
                        or _MM_PUNCT_RE.match(m.group(2))
                    )
                    else m.group(1) + m.group(2)
                ),
                cur,
            )
        out_lines.append(cur)
    return "\n".join(out_lines)
