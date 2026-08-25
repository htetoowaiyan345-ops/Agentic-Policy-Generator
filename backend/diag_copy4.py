import sys, time
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
from pathlib import Path
from policy_platform.pipeline import process

mm_pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")
t0 = time.time()
try:
    result = process(mm_pdf, fail_on_validation=False)
    elapsed = time.time() - t0
    print(f"Pipeline completed in {elapsed:.1f}s", flush=True)
    for s in result.steps:
        print(f"  Step {s.no} ({s.name}): ok={s.ok} {s.detail[:80]}")
except Exception as e:
    elapsed = time.time() - t0
    print(f"Pipeline FAILED after {elapsed:.1f}s", flush=True)
    print(f"Error: {type(e).__name__}: {e}", flush=True)
