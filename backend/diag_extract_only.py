import sys, time
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
from pathlib import Path
from policy_platform.extractors.pdf_extractor import extract

mm_pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")

t = time.perf_counter()
doc = extract(mm_pdf)
dt = time.perf_counter() - t
print(f"pdf_extractor.extract: {dt:.1f}s paragraphs={len(doc.paragraphs)}", flush=True)
