import sys, time
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
from pathlib import Path
from policy_platform.extractors.pdf_extractor import _try_pdfplumber

mm_pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")

t = time.perf_counter()
doc = _try_pdfplumber(mm_pdf)
dt = time.perf_counter() - t
print(f"pdfplumber alone: {dt:.1f}s paragraphs={len(doc.paragraphs)}", flush=True)

t = time.perf_counter()
from policy_platform.extractors.ocr_fallback import should_use_ocr, extract_text_via_ocr
from policy_platform.extractors.myanmar_post_ocr import correct_myanmar_ocr
ocr_text = extract_text_via_ocr(mm_pdf, preprocess=False)
dt_ocr = time.perf_counter() - t
print(f"OCR alone: {dt_ocr:.1f}s chars={len(ocr_text)}", flush=True)

t = time.perf_counter()
corrected = correct_myanmar_ocr(ocr_text)
dt_corr = time.perf_counter() - t
print(f"correct_myanmar_ocr: {dt_corr:.1f}s", flush=True)
