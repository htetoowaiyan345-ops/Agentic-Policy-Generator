import sys, time, os
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
os.environ["TESSERACT_DPI"] = "100"
from pathlib import Path
from policy_platform.extractors.ocr_fallback import extract_text_via_ocr
from policy_platform.extractors.myanmar_post_ocr import correct_myanmar_ocr

mm_pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")

t = time.perf_counter()
text = extract_text_via_ocr(mm_pdf, dpi=100, preprocess=False)
dt_ocr = time.perf_counter() - t
corrected = correct_myanmar_ocr(text)
lines = [l for l in corrected.split("\n") if l.strip()]
print(f"OCR: {dt_ocr:.1f}s, raw chars={len(text)}, cleaned lines={len(lines)}", flush=True)
print("\n=== First 15 lines ===")
for line in lines[:15]:
    print(f"  [{len(line):3d}] {line[:80]}")
print("\n=== Last 5 lines ===")
for line in lines[-5:]:
    print(f"  [{len(line):3d}] {line[:80]}")
