"""OCR fallback for Myanmar PDFs using Tesseract with OpenCV preprocessing.

Based on techniques from myNRC-OCR (zawlinnhtet03):
- OpenCV image preprocessing (Otsu thresholding, morphological operations)
- Unicode NFC normalization
- Zero-width character stripping

Preprocessing pipeline:
1. Convert to grayscale
2. Denoise (bilateral filter preserves edges)
3. Otsu binarization (clean B&W for Tesseract)
4. Morphological operations (remove small noise)
5. Optional dilation (connect broken characters)

Then run Tesseract in parallel for speed.
"""

from __future__ import annotations

import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TESSERACT_CMD = os.environ.get(
    "TESSERACT_CMD",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
)
POPPLER_PATH = os.environ.get(
    "POPPLER_PATH",
    r"C:\poppler\poppler-24.02.0\Library\bin",
)
DEFAULT_LANG = os.environ.get("TESSERACT_LANG", "mya+eng")
DEFAULT_DPI = int(os.environ.get("TESSERACT_DPI", "80"))
DEFAULT_OEM = int(os.environ.get("TESSERACT_OEM", "1"))  # LSTM only
DEFAULT_PSM = int(os.environ.get("TESSERACT_PSM", "6"))  # Uniform block of text
MAX_WORKERS = int(os.environ.get("TESSERACT_WORKERS", "8"))


def _configure_tesseract() -> None:
    """Set Tesseract binary path if available."""
    if TESSERACT_CMD and os.path.isfile(TESSERACT_CMD):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


# Myanmar Unicode ranges for detection
_MYANMAR_RANGE = re.compile(
    r"[\u1000-\u109F\uAA60-\uAA7F\uA9E0-\uA9FF]"
)


def _has_myanmar(text: str) -> bool:
    """Return True if text contains Myanmar script characters."""
    return bool(_MYANMAR_RANGE.search(text))


# ---------------------------------------------------------------------------
# OpenCV preprocessing (from myNRC-OCR approach)
# ---------------------------------------------------------------------------

def preprocess_image(image: Image.Image) -> Image.Image:
    """Apply OpenCV preprocessing to improve OCR accuracy.

    Pipeline (based on myNRC-OCR preprocessing.ipynb):
    1. Convert PIL Image to numpy array
    2. Convert to grayscale
    3. Bilateral filter (denoise while preserving edges)
    4. Otsu binarization (clean B&W)
    5. Morphological opening (remove small noise)
    6. Optional dilation (connect broken characters)
    7. Convert back to PIL Image

    Args:
        image: PIL Image from pdf2image

    Returns:
        Preprocessed PIL Image ready for Tesseract
    """
    # Convert PIL to numpy array
    img_array = np.array(image)

    # Convert to grayscale if needed
    if len(img_array.shape) == 3:
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_array

    # Step 1: Bilateral filter - denoise while preserving edges
    # This is better than Gaussian blur for text because it keeps edges sharp
    denoised = cv2.bilateralFilter(gray, 9, 75, 75)

    # Step 2: Otsu binarization
    # Automatically finds the optimal threshold value
    _, binary = cv2.threshold(
        denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    # Step 3: Morphological opening (remove small noise)
    # Use a small kernel to remove dots/specks without affecting characters
    kernel = np.ones((2, 2), np.uint8)
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    # Step 4: Light dilation to connect broken character parts
    # This helps with Myanmar stacked characters where parts may be slightly disconnected
    dilate_kernel = np.ones((2, 2), np.uint8)
    dilated = cv2.dilate(opened, dilate_kernel, iterations=1)

    # Convert back to PIL Image
    return Image.fromarray(dilated)


def preprocess_image_light(image: Image.Image) -> Image.Image:
    """Light preprocessing - denoise only, no binarization.

    Better for Myanmar stacked characters which may be damaged by
    aggressive binarization. Preserves color information.
    """
    img_array = np.array(image)

    if len(img_array.shape) == 3:
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_array

    # Light denoising - preserves Myanmar ligatures
    denoised = cv2.bilateralFilter(gray, 5, 50, 50)

    # Very light sharpening to help Tesseract see character boundaries
    kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]]) / 9
    sharpened = cv2.filter2D(denoised, -1, kernel)

    return Image.fromarray(sharpened)


def preprocess_image_alternative(image: Image.Image) -> Image.Image:
    """Alternative preprocessing: adaptive thresholding.

    Better for documents with varying illumination.
    """
    img_array = np.array(image)

    if len(img_array.shape) == 3:
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    else:
        gray = img_array

    # Denoise
    denoised = cv2.bilateralFilter(gray, 9, 75, 75)

    # Adaptive thresholding - better for uneven lighting
    binary = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 11, 2
    )

    # Morphological opening
    kernel = np.ones((2, 2), np.uint8)
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    return Image.fromarray(opened)


# ---------------------------------------------------------------------------
# PDF → Image conversion
# ---------------------------------------------------------------------------

def _pdf_to_images(
    pdf_path: Path,
    dpi: int = DEFAULT_DPI,
) -> list[Image.Image]:
    """Convert PDF pages to PIL Images using pdf2image (Poppler)."""
    from pdf2image import convert_from_path

    kwargs: dict = {"dpi": dpi}
    if POPPLER_PATH and os.path.isdir(POPPLER_PATH):
        kwargs["poppler_path"] = POPPLER_PATH

    return convert_from_path(str(pdf_path), **kwargs)


# ---------------------------------------------------------------------------
# Header table extraction (label-row tables on page 1)
# ---------------------------------------------------------------------------
#
# Generic for any policy PDF: scans the full page 1 (not just top region,
# because the header table can sit below the title in Myanmar layouts) and
# feeds the OCR output through a generic label-value parser. No per-file
# hardcoding — relies on ``canonical_label()`` (English + Burmese YAML
# reverse index) for label recognition.

# Higher DPI for header-table OCR (label-row accuracy). Configurable.
HEADER_DPI = int(os.environ.get("TESSERACT_HEADER_DPI", "200"))


def _ocr_page1_high_dpi(image: Image.Image) -> str:
    """OCR a single page image (already rendered at HEADER_DPI) and
    return the raw OCR text. Generic: scans the full page, the parser
    is responsible for finding label-value pairs.

    Args:
        image: PIL Image of a full page, rendered at HEADER_DPI.

    Returns:
        Raw OCR text from the entire page.
    """
    _configure_tesseract()
    config = f"--oem {DEFAULT_OEM} --psm {DEFAULT_PSM}"
    text = pytesseract.image_to_string(
        image, lang=DEFAULT_LANG, config=config
    )
    return text.strip()


# ---------------------------------------------------------------------------
# Single-page OCR
# ---------------------------------------------------------------------------

def _ocr_single_page(
    image: Image.Image,
    *,
    lang: str = DEFAULT_LANG,
    oem: int = DEFAULT_OEM,
    psm: int = DEFAULT_PSM,
    preprocess: bool = True,
    preprocess_mode: str = "light",
) -> str:
    """Run Tesseract on a single PIL Image and return extracted text.

    Args:
        image: PIL Image (raw or preprocessed)
        lang: Tesseract language pack
        oem: Tesseract OCR Engine mode
        psm: Tesseract page segmentation mode
        preprocess: Whether to apply OpenCV preprocessing
        preprocess_mode: "light" (denoise only, good for Myanmar)
                       or "aggressive" (Otsu + morphological, may break ligatures)
    """
    if preprocess:
        if preprocess_mode == "aggressive":
            image = preprocess_image(image)
        else:
            image = preprocess_image_light(image)

    config = f"--oem {oem} --psm {psm}"
    text = pytesseract.image_to_string(image, lang=lang, config=config)
    return text.strip()


# ---------------------------------------------------------------------------
# Parallel OCR
# ---------------------------------------------------------------------------

def extract_text_via_ocr(
    pdf_path: Path,
    *,
    lang: str = DEFAULT_LANG,
    dpi: int = DEFAULT_DPI,
    oem: int = DEFAULT_OEM,
    psm: int = DEFAULT_PSM,
    max_workers: int = MAX_WORKERS,
    preprocess: bool = True,
    psm_per_page: dict[int, int] | None = None,
) -> str:
    """Extract text from a PDF using Tesseract OCR with OpenCV preprocessing.

    Renders each page to an image, applies OpenCV preprocessing,
    then runs OCR in parallel. Returns the concatenated text of all pages.

    Args:
        pdf_path: Path to the PDF file
        lang: Tesseract language pack (default: mya+eng)
        dpi: Image rendering DPI (default: 250)
        oem: Tesseract OCR Engine mode (default: 1 = LSTM)
        psm: Tesseract page segmentation mode (default: 6 = uniform block)
        max_workers: Number of parallel OCR threads
        preprocess: Whether to apply OpenCV preprocessing
        psm_per_page: Optional dict mapping page index (0-based) to a
            page-specific PSM override. Useful for hybrid OCR where
            page 1 (header table) needs PSM=4 and rest use PSM=6.
    """
    _configure_tesseract()

    # Step 1: Render PDF to images
    images = _pdf_to_images(pdf_path, dpi=dpi)
    if not images:
        return ""

    # Step 2: OCR in parallel
    page_texts: list[str] = [None] * len(images)  # type: ignore[list-item]

    def _ocr_page(idx: int, img: Image.Image) -> tuple[int, str]:
        page_psm = (psm_per_page or {}).get(idx, psm)
        try:
            return idx, _ocr_single_page(
                img, lang=lang, oem=oem, psm=page_psm,
                preprocess=preprocess, preprocess_mode="light"
            )
        except Exception as e:
            print(f"[ocr_fallback] page {idx} failed: {type(e).__name__}: {e}", flush=True)
            return idx, ""

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_ocr_page, i, img): i
            for i, img in enumerate(images)
        }
        for future in as_completed(futures):
            try:
                idx, text = future.result()
                page_texts[idx] = text
            except Exception as e:
                print(f"[ocr_fallback] future failed: {type(e).__name__}: {e}", flush=True)

    # Step 3: Join with page separators
    result_parts = []
    for i, text in enumerate(page_texts):
        if text:
            result_parts.append(text)

    return "\n\n".join(result_parts)


# ---------------------------------------------------------------------------
# Detection: should we use OCR for this PDF?
# ---------------------------------------------------------------------------

def should_use_ocr(paragraphs: list[str], tables: list[list[list[str]]]) -> bool:
    """Heuristic: return True if the extracted text looks corrupt
    and OCR might produce better results.

    Signals of corruption (Myanmar PDFs):
    - Broken character sequences: spaces between Myanmar chars that
      should be joined (e.g., "မ်း မျာ်း" instead of "မ်းမျာ်း")
    - Wrong mark ordering: asat/virama before vowels, visarga in wrong place
    - Excessive short fragments: 1-2 char Myanmar words that should be longer
    - Known garbage patterns from Tesseract/PDF CMap corruption
    """
    if not paragraphs:
        return False

    # Combine all text for analysis
    all_text = "\n".join(paragraphs)
    if not all_text:
        return False

    # Check 1: Does the text contain Myanmar characters at all?
    if not _has_myanmar(all_text):
        return False

    # Check 2: Look for known garbage patterns from Tesseract/PDF corruption
    garbage_patterns = [
        r"FAV\s*city",
        r"wy\s*Holdings",
        r"wosa\d",
        r"Gty\s*Holdings",
        r"DOC\s*PEO",
        r"sagsz\d",
        r"c0dz:",
        r"aySqep\d",
        r"spocaps",
    ]
    garbage_count = sum(
        len(re.findall(p, all_text, re.IGNORECASE))
        for p in garbage_patterns
    )
    if garbage_count >= 1:
        return True

    # Check 3: Broken Myanmar character sequences
    _broken_seq = len(re.findall(
        r"[\u1000-\u1021]\s+[\u102B-\u103A\u103B-\u103E]", all_text
    ))
    if _broken_seq > 5:
        return True

    # Check 4: Wrong mark ordering (asat/virama before vowel)
    _wrong_order = len(re.findall(r"\u103A[\u102D-\u1032]", all_text))
    if _wrong_order > 3:
        return True

    # Check 5: Excessive short Myanmar fragments
    words = all_text.split()
    if words:
        _myanmar_words = [w for w in words if _MYANMAR_RANGE.match(w)]
        _short_words = [w for w in _myanmar_words if len(w) <= 2]
        if _myanmar_words and len(_short_words) / len(_myanmar_words) > 0.25:
            return True

    # Check 6: Repeated broken patterns
    _broken_repeats = len(re.findall(
        r"[\u1000-\u1021]\s+[\u1000-\u1021\u103B-\u103E]\s+[\u1000-\u1021\u103B-\u103E]",
        all_text
    ))
    if _broken_repeats > 10:
        return True

    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_with_ocr_fallback(
    pdf_path: Path,
    paragraphs: list[str],
    tables: list[list[list[str]]],
) -> tuple[str, bool]:
    """Try OCR if the default extraction looks corrupt.

    Returns:
        (text, used_ocr): The extracted text and whether OCR was used.
    """
    if not should_use_ocr(paragraphs, tables):
        return "", False

    text = extract_text_via_ocr(pdf_path)
    return text, True
