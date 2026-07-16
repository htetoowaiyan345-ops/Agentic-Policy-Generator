# Agentic Policy Processing Platform

Backend + minimal UI that processes policy documents (PDF, DOCX, TXT) and emits a standardized framework-based Microsoft Word (`.docx`) output matching the locked Brain template.

## Rules enforced
- Original policy text is **never** rewritten, paraphrased, summarized, translated, corrected, removed, or added.
- The Brain Framework is **frozen** (manifest hash checked on every run).
- Sections not present in the source are marked `Data is not found in source file`. Processing continues.
- Audit log (`backend/data/audit/<run_id>.xlsx` + `.json`) is produced per run.
- Validation failures return the canonical message and a 422 status.

## Setup on a new laptop

This project is fully portable. To run it on a new Windows/macOS/Linux laptop from the zipped project folder:

**Step 1 — Install Python 3.11 or newer**

Download from https://www.python.org/downloads/ and check "Add Python to PATH" during install (Windows).

Verify in PowerShell / Terminal:
```powershell
python --version
```

**Step 2 — Install Python dependencies**

Open PowerShell / Terminal, `cd` into the unzipped project folder, and run:
```powershell
pip install -r requirements.txt
```

This installs FastAPI, pdfplumber, python-docx, PyMuPDF, and other packages the app needs.

**Step 3 — Install Node.js 20+ and frontend dependencies**

The frontend is a SvelteKit + TypeScript app. Install Node.js 20+ (https://nodejs.org/), then:
```powershell
cd frontend\web
npm install
```

**Step 4 — Launch the app**

Double-click `run.bat` (Windows). The browser should open automatically to `http://localhost:5173/`.

**That's it.** You don't need to:
- ❌ Set any environment variables
- ❌ Edit any config files
- ❌ Place the Brain template in your Downloads folder (it's already inside the project at `backend/data/brain_template/`)
- ❌ Manually create `data/runs/` or `data/outputs/` folders (auto-created on first use)

If you see "Python is not recognized", Python is not on PATH — reinstall Python with the "Add to PATH" checkbox, or use `py` instead of `python` in commands.

If port 8000 or 5173 is already in use, `run.bat` will kill the old process automatically.

## Quick start

**1. Install Python dependencies (first time only):**
```powershell
pip install -r requirements.txt
```

**2. Install Node.js 20+ and frontend dependencies (first time only):**
```powershell
cd frontend\web
npm install
```

**3. Launch the app:**
Double-click **`run.bat`** — it starts the API on port 8000 and the SvelteKit dev server on port 5173, then opens the browser to `http://localhost:5173/`.

Or from the command line:

```powershell
# Terminal 1 — backend
cd backend
set PYTHONPATH=%CD%
python -m api.server

# Terminal 2 — frontend
cd frontend\web
npm run dev

# Browser
start http://localhost:5173/
```

## Install dependencies

```powershell
pip install -r requirements.txt
```

Optional spaCy path (opt-in, falls back to regex automatically):

```powershell
pip install spacy==3.8.13
python -m spacy download en_core_web_sm
$env:AGENTIC_POLICY_USE_SPACY = "1"
```

## Tests

```powershell
pytest -v
```

## Project layout

The project uses a minimal two-folder layout. Only 5 files + 2 folders at root.

```
agentic-policy-platform/
├── backend/                                # ⚙️  Python package + tests + data
│   ├── api/                               # REST API (built-in http.server, no framework)
│   │   ├── __init__.py
│   │   ├── server.py                      # 6 endpoints + CORS
│   │   ├── db.py                          # sqlite3 wrapper
│   │   └── pipeline_runner.py             # background pipeline executor
│   ├── policy_platform/                   # pipeline library
│   │   ├── __init__.py
│   │   ├── config.py                      # paths + constants
│   │   ├── pipeline.py                    # Steps 1-7 orchestrator
│   │   ├── analyzer.py                    # rule-based section matcher
│   │   ├── renderer.py                    # docx builder using Brain as template
│   │   ├── validator.py                   # integrity gate
│   │   ├── audit.py                       # Excel writer
│   │   ├── post_render.py
│   │   ├── style.py
│   │   ├── cli.py
│   │   ├── pipeline_types.py
│   │   ├── extractors/
│   │   │   ├── base.py
│   │   │   ├── cleaner.py
│   │   │   ├── cleaner_mojibake.py
│   │   │   ├── dispatch.py
│   │   │   ├── docx_extractor.py
│   │   │   ├── field_parser.py
│   │   │   ├── header_image_extractor.py
│   │   │   ├── mojibake.py
│   │   │   ├── narrative_inference.py
│   │   │   ├── pdf_extractor.py
│   │   │   ├── spacy_extractor.py
│   │   │   ├── text_extractor.py
│   │   │   └── title_extractor.py
│   │   └── framework/
│   │       ├── brain.py
│   │       ├── brain_fields.py
│   │       ├── brain_loader.py
│   │       ├── agent_classifier.py
│   │       ├── section_map.py
│   │       ├── slot_label_canonical.py
│   │       └── slot_summary.py
│   ├── tests/                             # pytest suite (20 files, 179 tests)
│   │   ├── conftest.py
│   │   └── test_*.py
│   └── data/                              # Brain + samples + outputs
│       ├── brain_template/                # 🔒  LOCKED
│       │   ├── Policy_Framework_5.docx
│       │   └── framework_manifest.json
│       ├── samples/                       # 5 sample source PDFs (for testing)
│       │   ├── Policy_Template_Award_and_Recognition_Updated.pdf
│       │   ├── Earthquake_Full_Policy_One_Paragraph.pdf
│       │   ├── Hospital_Buildings_Policy_Template.pdf
│       │   ├── Policy For Coronavirus Disease.pdf
│       │   └── Sexual Harassment Policy.pdf
│       ├── outputs/                       # latest generated .docx
│       ├── audit/                         # latest audit logs (xlsx + json)
│       ├── runs/                          # per-run source + output (gitignored)
│       └── runs.db                        # SQLite — run history (gitignored)
│
├── frontend/                              # 🖥  SvelteKit + TypeScript SPA (Tailwind via CDN)
│   └── web/
│       ├── package.json                   # SvelteKit + Svelte 5 + Vite + TS
│       ├── svelte.config.js               # adapter-static (SPA mode)
│       ├── vite.config.ts                 # dev server on port 5173
│       ├── tsconfig.json                  # strict mode
│       ├── build/                         # static build output (gitignored)
│       └── src/
│           ├── app.html                   # SvelteKit entry shell + fonts + Tailwind CDN
│           ├── app.css                    # global styles (verbatim from vanilla CSS)
│           ├── lib/
│           │   ├── api.ts                 # typed fetch wrappers (8 endpoints)
│           │   ├── types.ts               # AppState, BatchEntry, PreviewData, ...
│           │   ├── stores.ts              # Svelte writable stores
│           │   ├── escape.ts              # shared utils (escapeHtml, fmtMB)
│           │   ├── page-actions.ts        # loadResultAndShow, renderSlots
│           │   ├── Header.svelte          # top nav + history toggle
│           │   ├── History.svelte         # Run History panel
│           │   ├── Upload.svelte          # Step 1 — drop zone, queue, rejected
│           │   ├── Process.svelte         # Step 2 — batch, polling, progress
│           │   └── Review.svelte          # Step 3 — slot rendering, marker styling
│           └── routes/
│               ├── +layout.ts             # ssr=false, prerender=false (SPA)
│               └── +page.svelte           # page shell, mounts 3 step components
│
├── pyproject.toml                         # pytest config (pythonpath = backend)
├── README.md                              # this file
├── requirements.txt                       # core + optional spaCy (commented)
├── run.bat                                # one-click launcher (Windows)
└── .gitignore
```

## REST API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/upload` | upload PDF/DOCX/TXT → `run_id` |
| POST | `/api/process/{run_id}` | start pipeline in background |
| GET | `/api/status/{run_id}` | poll `{state, sections_filled, markers_count}` |
| GET | `/api/result/{run_id}` | full extracted result JSON |
| GET | `/api/history` | last 50 done runs |
| GET | `/api/download/{run_id}/docx` | download generated `.docx` |

CORS headers are sent on every response (echoes `Origin` with `Vary: Origin`).

## Brain Framework Version
`Brain-PF5-v1.1.0`

## Framework (15 frozen sections)
See `backend/policy_platform/framework/section_map.py` for the canonical list.