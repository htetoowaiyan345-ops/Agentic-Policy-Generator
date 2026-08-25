import sys, time
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
from pathlib import Path
import hashlib
import pdfplumber
from policy_platform.extractors.pdf_extractor import _try_pymupdf
from policy_platform.extractors.ocr_fallback import should_use_ocr, extract_text_via_ocr
from policy_platform.extractors.myanmar_post_ocr import correct_myanmar_ocr

mm_pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")

# Step A: pdfplumber
t = time.perf_counter()
paragraphs = []
tables = []
with pdfplumber.open(str(mm_pdf)) as pdf:
    for page in pdf.pages:
        txt = page.extract_text() or ""
        for line in txt.split("\n"):
            if line.strip():
                paragraphs.append(line)
        tables.extend(page.extract_tables() or [])
t1 = time.perf_counter() - t
print(f"A. pdfplumber: {t1:.1f}s paragraphs={len(paragraphs)} tables={len(tables)}", flush=True)

# Step B: should_use_ocr
t = time.perf_counter()
ocr_needed = should_use_ocr(paragraphs, tables)
t2 = time.perf_counter() - t
print(f"B. should_use_ocr: {t2:.1f}s -> {ocr_needed}", flush=True)

# Step C: pymupdf
t = time.perf_counter()
pymupdf_doc = _try_pymupdf(mm_pdf)
t3 = time.perf_counter() - t
print(f"C. pymupdf: {t3:.1f}s", flush=True)

# Step D: OCR
t = time.perf_counter()
ocr_text = extract_text_via_ocr(mm_pdf, preprocess=False)
t4 = time.perf_counter() - t
print(f"D. OCR: {t4:.1f}s chars={len(ocr_text)}", flush=True)

# Step E: correct
t = time.perf_counter()
corrected = correct_myanmar_ocr(ocr_text)
t5 = time.perf_counter() - t
print(f"E. correct_myanmar_ocr: {t5:.1f}s lines={len(corrected.split(chr(10)))}", flush=True)

print(f"\nTotal: {t1+t2+t3+t4+t5:.1f}s", flush=True)
