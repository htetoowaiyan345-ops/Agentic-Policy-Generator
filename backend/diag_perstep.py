import sys, time, os
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
from pathlib import Path
from policy_platform.extractors import dispatch as extractor_dispatch
from policy_platform.extractors.header_extractor import extract as header_extract
from policy_platform.rag.retrieval_pipeline import RetrievalPipeline

mm_pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")
DPI = int(os.environ.get("TEST_DPI", "150"))

t = time.perf_counter()
extracted = extractor_dispatch(mm_pdf)
dt_extract = time.perf_counter() - t
print(f"Step 2 Extract (DPI={DPI}): {dt_extract:.1f}s paragraphs={len(extracted.paragraphs)}", flush=True)

t = time.perf_counter()
hi = header_extract(mm_pdf, pdf_metadata=None, cleaned_paragraphs=list(extracted.paragraphs))
dt_header = time.perf_counter() - t
print(f"Header: {dt_header:.1f}s", flush=True)

t = time.perf_counter()
rag = RetrievalPipeline()
rag_result = rag.run(
    list(extracted.paragraphs),
    tables=list(extracted.tables) if getattr(extracted, "tables", None) else None,
    table_paragraph_indices=list(extracted.table_paragraph_indices)
        if getattr(extracted, "table_paragraph_indices", None) else None,
)
dt_rag = time.perf_counter() - t
print(f"Step 3 RAG: {dt_rag:.1f}s timed_out={rag_result.timed_out} found={sum(1 for s in rag_result.slots.values() if s and s.backend != 'timeout')}", flush=True)

print(f"\nTotal (2-3): {dt_extract + dt_header + dt_rag:.1f}s")
