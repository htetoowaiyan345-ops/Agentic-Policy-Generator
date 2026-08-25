import sys, time
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
from pathlib import Path
from policy_platform.extractors import pdf_extractor
doc = pdf_extractor.extract(Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf"))
print(f"After extract: {len(doc.paragraphs)} paragraphs", flush=True)

# Time the Myanmar smart extractor
t = time.perf_counter()
try:
    from policy_platform.extract_myanmar.font_inspector import inspect_pdf_fonts, classify_pdf
    fonts = inspect_pdf_fonts(Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf"))
    print(f"inspect_pdf_fonts: {time.perf_counter()-t:.1f}s fonts={len(fonts.fonts) if hasattr(fonts, 'fonts') else 'n/a'}", flush=True)
    t = time.perf_counter()
    quality = classify_pdf(fonts)
    print(f"classify_pdf: {time.perf_counter()-t:.1f}s verdict={quality.verdict}", flush=True)
except Exception as e:
    print(f"smart extractor skipped: {e}", flush=True)
