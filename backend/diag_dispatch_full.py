import sys, time
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
from pathlib import Path
mm_pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")

t0 = time.perf_counter()
from policy_platform.extractors import pdf_extractor
doc = pdf_extractor.extract(mm_pdf)
print(f"1. extract: {time.perf_counter()-t0:.1f}s paragraphs={len(doc.paragraphs)}", flush=True)

t = time.perf_counter()
from policy_platform.extractors import clean_paragraphs
cleaned, dropped, original_indices = clean_paragraphs(doc.paragraphs)
print(f"2. clean_paragraphs: {time.perf_counter()-t:.1f}s", flush=True)

t = time.perf_counter()
from policy_platform.extractors import _paragraphs_look_burmese_corrupt
looks_burmese = _paragraphs_look_burmese_corrupt(doc.paragraphs)
print(f"3. _paragraphs_look_burmese_corrupt: {time.perf_counter()-t:.1f}s -> {looks_burmese}", flush=True)

t = time.perf_counter()
from policy_platform.extract_myanmar.font_inspector import inspect_pdf_fonts, classify_pdf
from policy_platform.extract_myanmar.myanmar_extractor import extract_text_smart, METHOD_METADATA_RECOVERED
fonts = inspect_pdf_fonts(mm_pdf)
quality = classify_pdf(fonts)
print(f"4a. inspect/classify: {time.perf_counter()-t:.1f}s verdict={quality.verdict}", flush=True)
if quality.verdict == "unsafe" and looks_burmese:
    t = time.perf_counter()
    smart = extract_text_smart(mm_pdf)
    print(f"4b. extract_text_smart: {time.perf_counter()-t:.1f}s method={smart.method} score={smart.score}", flush=True)

t = time.perf_counter()
from policy_platform.extractors import _join_mid_sentence_lines, _split_paragraphs_on_brain_labels
joined = _join_mid_sentence_lines(cleaned)
print(f"5. _join_mid_sentence_lines: {time.perf_counter()-t:.1f}s", flush=True)

t = time.perf_counter()
split = _split_paragraphs_on_brain_labels(joined)
print(f"6. _split_paragraphs_on_brain_labels: {time.perf_counter()-t:.1f}s", flush=True)

print(f"\nTotal: {time.perf_counter()-t0:.1f}s")
