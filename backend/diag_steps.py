import sys, time
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
from pathlib import Path
from policy_platform.extractors import dispatch as extractor_dispatch
from policy_platform.rag.retrieval_pipeline import RetrievalPipeline

mm_pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")

t = time.perf_counter()
extracted = extractor_dispatch(mm_pdf)
dt_extract = time.perf_counter() - t
print(f"Step 2 Extract: {dt_extract:.1f}s paragraphs={len(extracted.paragraphs)}", flush=True)

t = time.perf_counter()
rag = RetrievalPipeline()
rag_result = rag.run(
    list(extracted.paragraphs),
    tables=list(extracted.tables) if getattr(extracted, "tables", None) else None,
    table_paragraph_indices=list(extracted.table_paragraph_indices)
        if getattr(extracted, "table_paragraph_indices", None) else None,
)
dt_rag = time.perf_counter() - t
print(f"Step 3 RAG: {dt_rag:.1f}s timed_out={rag_result.timed_out}", flush=True)

print(f"\nTotal (2-3): {dt_extract + dt_rag:.1f}s")
