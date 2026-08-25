import sys, time
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
from pathlib import Path
from policy_platform.pipeline import process

mm_pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")

t = time.perf_counter()
extracted = dispatch = None
from policy_platform.extractors import dispatch as extractor_dispatch
extracted = extractor_dispatch(mm_pdf)
dt_extract = time.perf_counter() - t
print(f"Step 2 dispatch: {dt_extract:.1f}s paragraphs={len(extracted.paragraphs)}", flush=True)

from policy_platform.extractors.header_extractor import extract as header_extract
t = time.perf_counter()
hi = header_extract(mm_pdf, pdf_metadata=None, cleaned_paragraphs=list(extracted.paragraphs))
print(f"Header: {time.perf_counter()-t:.1f}s", flush=True)

from policy_platform.rag.retrieval_pipeline import RetrievalPipeline
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

from policy_platform.framework.brain import init_or_verify
t = time.perf_counter()
manifest = init_or_verify(init=False)
print(f"Step 4 Apply: {time.perf_counter()-t:.1f}s", flush=True)

from policy_platform.pipeline import _run_extracted_pipeline
t = time.perf_counter()
import uuid
run_id = f"diag_{uuid.uuid4().hex[:8]}"
from pathlib import Path as P
output_path = P(r"C:\Users\htetoowaiyan\Downloads") / f"{run_id}.docx"
from policy_platform import config
from policy_platform.pipeline import _step
from policy_platform.config import FROZEN_SECTIONS
steps = [
    _step(1, "Receive", True, mm_pdf.name),
    _step(2, "Extract", True, f"format=pdf"),
]
result = _run_extracted_pipeline(
    extracted=extracted,
    output_path=output_path,
    steps=steps,
    sections_meta=[],
    started_at="diag",
    t0=t,
    run_id=run_id,
    input_path=mm_pdf,
    header_text=None,
    header_version=None,
    fail_on_validation=False,
)
print(f"Steps 4-6: {time.perf_counter()-t:.1f}s", flush=True)
for s in result.steps:
    print(f"  {s.no} {s.name}: ok={s.ok} {s.detail[:60]}")
