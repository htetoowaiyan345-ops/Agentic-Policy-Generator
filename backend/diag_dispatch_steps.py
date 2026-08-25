import sys, time
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
from pathlib import Path
from policy_platform.extractors import dispatch
from policy_platform.extractors.pdf_extractor import extract as pdf_extract

mm_pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")

t = time.perf_counter()
doc = pdf_extract(mm_pdf)
dt1 = time.perf_counter() - t
print(f"1. pdf_extract: {dt1:.1f}s paragraphs={len(doc.paragraphs)}", flush=True)

t = time.perf_counter()
from policy_platform.extractors import clean_paragraphs
cleaned, dropped, original_indices = clean_paragraphs(doc.paragraphs)
dt2 = time.perf_counter() - t
print(f"2. clean_paragraphs: {dt2:.1f}s cleaned={len(cleaned)}", flush=True)

t = time.perf_counter()
from policy_platform.extractors import _join_mid_sentence_lines, _split_paragraphs_on_brain_labels
joined = _join_mid_sentence_lines(cleaned)
dt3 = time.perf_counter() - t
print(f"3. _join_mid_sentence_lines: {dt3:.1f}s joined={len(joined)}", flush=True)

t = time.perf_counter()
split = _split_paragraphs_on_brain_labels(joined)
dt4 = time.perf_counter() - t
print(f"4. _split_paragraphs_on_brain_labels: {dt4:.1f}s split={len(split)}", flush=True)
