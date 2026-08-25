import sys, time, os
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
os.environ["TESSERACT_DPI"] = "100"
os.environ["TESSERACT_WORKERS"] = "8"
from pathlib import Path
from policy_platform.extractors.ocr_fallback import extract_text_via_ocr

mm_pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")

t = time.perf_counter()
text = extract_text_via_ocr(mm_pdf, dpi=100, preprocess=False)
dt = time.perf_counter() - t
print(f"OCR (DPI=100, 8 workers): {dt:.1f}s, chars={len(text)}", flush=True)

t = time.perf_counter()
text = extract_text_via_ocr(mm_pdf, dpi=120, preprocess=False)
dt = time.perf_counter() - t
print(f"OCR (DPI=120, 8 workers): {dt:.1f}s, chars={len(text)}", flush=True)
