import sys, time, os
sys.path.insert(0, r"D:\Htet Oo Wai Yan\OneDrive - City Holdings Limited\Desktop\agentic-policy-platform - Copy (4)\backend")
from pathlib import Path
from policy_platform.extractors.ocr_fallback import (
    _pdf_to_images, _ocr_single_page, _configure_tesseract,
    MAX_WORKERS as DEFAULT_MAX_WORKERS, DEFAULT_DPI,
)
from concurrent.futures import ThreadPoolExecutor, as_completed

WORKERS = int(os.environ.get("WORKERS", "8"))
DPI = int(os.environ.get("DPI", str(DEFAULT_DPI)))
print(f"DEFAULT_DPI={DEFAULT_DPI} DEFAULT_MAX_WORKERS={DEFAULT_MAX_WORKERS} | testing WORKERS={WORKERS} DPI={DPI}", flush=True)

pdf = Path(r"C:\Users\htetoowaiyan\Downloads\HR_00002 Employee Health Benefit Policy_MM_V3_MM.pdf")
_configure_tesseract()

for run in range(2):
    t0 = time.perf_counter()
    images = _pdf_to_images(pdf, dpi=DPI)
    t_render = time.perf_counter() - t0

    t0 = time.perf_counter()
    page_texts = [None] * len(images)
    def _ocr(idx, img):
        try:
            return idx, _ocr_single_page(img, lang='mya+eng', oem=1, psm=6, preprocess=False, preprocess_mode='none')
        except Exception as e:
            return idx, ""
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(_ocr, i, img): i for i, img in enumerate(images)}
        for future in as_completed(futures):
            page_texts[futures[future]] = future.result()
    t_ocr = time.perf_counter() - t0
    print(f"Run {run+1}: render={t_render:.1f}s ocr={t_ocr:.1f}s total={t_render+t_ocr:.1f}s chars={sum(len(t) for t in page_texts)}", flush=True)
