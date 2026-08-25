import sys, time
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
from pathlib import Path
from policy_platform.extractors.pdf_extractor import _try_pdfplumber, _try_pymupdf

mm_pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")

t = time.perf_counter()
doc1 = _try_pdfplumber(mm_pdf)
t1 = time.perf_counter() - t
print(f"pdfplumber: {t1:.1f}s paragraphs={len(doc1.paragraphs)} tables={len(doc1.tables)}", flush=True)

t = time.perf_counter()
doc2 = _try_pymupdf(mm_pdf)
t2 = time.perf_counter() - t
if doc2:
    print(f"pymupdf: {t2:.1f}s paragraphs={len(doc2.paragraphs)} tables={len(doc2.tables)}", flush=True)
else:
    print(f"pymupdf: returned None in {t2:.1f}s", flush=True)
