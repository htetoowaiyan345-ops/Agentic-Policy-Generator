"""Generate system_architecture.pdf in the user's Downloads folder.

Documents the backend of the Agentic Policy Platform:
- High-level architecture and step-by-step data flow
- Per-file inventory of every .py file in backend/ (purpose + key symbols)
- Saved to %USERPROFILE%\\Downloads\\system_architecture.pdf
"""
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors

OUT = Path.home() / "Downloads" / "system_architecture.pdf"


def _styles():
    base = getSampleStyleSheet()
    styles = {}
    styles["title"] = ParagraphStyle(
        "title", parent=base["Title"], fontSize=22, leading=28, spaceAfter=18
    )
    styles["h1"] = ParagraphStyle(
        "h1", parent=base["Heading1"], fontSize=16, leading=20, spaceBefore=18, spaceAfter=10,
        textColor=colors.HexColor("#0b3d91")
    )
    styles["h2"] = ParagraphStyle(
        "h2", parent=base["Heading2"], fontSize=13, leading=17, spaceBefore=12, spaceAfter=6,
        textColor=colors.HexColor("#1a4480")
    )
    styles["h3"] = ParagraphStyle(
        "h3", parent=base["Heading3"], fontSize=11, leading=14, spaceBefore=8, spaceAfter=4,
        textColor=colors.HexColor("#333333")
    )
    styles["body"] = ParagraphStyle(
        "body", parent=base["BodyText"], fontSize=9.5, leading=13, spaceAfter=4
    )
    styles["bullet"] = ParagraphStyle(
        "bullet", parent=base["BodyText"], fontSize=9.5, leading=13,
        leftIndent=14, bulletIndent=2, spaceAfter=2
    )
    styles["code"] = ParagraphStyle(
        "code", parent=base["Code"], fontSize=8.5, leading=11,
        leftIndent=10, backColor=colors.HexColor("#f4f4f4"), borderPadding=4, spaceAfter=4
    )
    styles["toc"] = ParagraphStyle(
        "toc", parent=base["BodyText"], fontSize=10, leading=14, leftIndent=14
    )
    return styles


def build():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT), pagesize=letter,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title="Agentic Policy Platform - Backend Architecture",
        author="System Documentation",
    )
    S = _styles()
    story = []

    # ---- Title page ----
    story.append(Paragraph("Agentic Policy Platform", S["title"]))
    story.append(Paragraph("Backend System Architecture", S["h2"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "This document explains how the backend works end-to-end and describes what every "
        "Python file in <b>backend/</b> does. It is generated from the codebase and reflects the "
        "current implementation as of the most recent commit.",
        S["body"]
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph("<b>Scope:</b> Backend only (Python, FastAPI-style stdlib HTTP server).", S["body"]))
    story.append(Paragraph("<b>Stack:</b> Python 3.14, pdfplumber, PyMuPDF, python-docx, sentence-transformers, "
                           "FAISS, BM25, spaCy (optional), reportlab.", S["body"]))
    story.append(Paragraph("<b>Entry point:</b> <font face='Courier'>python -m api.server</font> "
                           "(from <font face='Courier'>backend/</font>)", S["body"]))
    story.append(Paragraph("<b>Default URL:</b> http://localhost:8000", S["body"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Table of Contents", S["h2"]))
    toc = [
        "1. High-level architecture",
        "2. Step-by-step pipeline flow",
        "3. File-by-file inventory",
        "    3.1  policy_platform/  - core domain library",
        "    3.2  policy_platform/extractors/  - text extraction and cleaning",
        "    3.3  policy_platform/framework/  - Brain schema and slot map",
        "    3.4  policy_platform/rag/  - hybrid retrieval subsystem",
        "    3.5  api/  - HTTP server, DB, DOCX export",
        "    3.6  scripts/  - operational utilities",
        "    3.7  top-level diagnostic scripts",
        "4. How slots are filled (RAG-Hybrid)",
        "5. How the final DOCX is produced",
    ]
    for t in toc:
        story.append(Paragraph(t, S["toc"]))
    story.append(PageBreak())

    # ---- 1. High-level architecture ----
    story.append(Paragraph("1. High-level architecture", S["h1"]))
    story.append(Paragraph(
        "The backend is a single-process Python HTTP service. It accepts policy documents (PDF, DOCX, "
        "TXT, RTF) over HTTP, runs them through a 6-step pipeline, and returns a finished DOCX that has "
        "been written into a frozen Microsoft Word 'Brain' template (15 numbered slots).",
        S["body"]
    ))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Layered diagram:", S["h3"]))
    arch = """
        +---------------------------------------------+
        |  Browser (Vite/React, port 5173)            |
        +--------------------+------------------------+
                             | HTTPS / JSON
        +--------------------v------------------------+
        |  api/server.py  (BaseHTTPRequestHandler)    |
        |   - /api/login, /api/upload, /api/process   |
        |   - /api/result, /api/preview, /api/download|
        |   - /api/versions, /api/audit, /api/diff    |
        +--------------------+------------------------+
                             |
        +--------------------v------------------------+
        |  api/pipeline_runner.py  (60s hard timeout) |
        +--------------------+------------------------+
                             |
        +--------------------v------------------------+
        |  policy_platform/pipeline.py                |
        |  (orchestrator - 6 numbered AgentSteps)     |
        +-----+-----------+-------------+-------------+
              |           |             |
        +-----v----+ +----v-----+ +-----v-----+
        | Extract  | |   RAG    | |  Apply +  |
        | (PDF,    | |  Hybrid  | |  Validate |
        |  DOCX,   | |  FAISS + | |           |
        |  TXT,    | |  BM25 +  | |           |
        |  RTF)    | |  Rerank  | |           |
        +----------+ +----------+ +-----+-----+
                                           |
                                +----------v---------+
                                |  Render (DOCX)    |
                                |  renderer.py +    |
                                |  lines_json_      |
                                |  renderer.py      |
                                +--------------------+
    """
    for line in arch.splitlines():
        story.append(Paragraph(line.replace(" ", "&nbsp;") if line.strip() else "&nbsp;", S["code"]))
    story.append(PageBreak())

    # ---- 2. Step-by-step pipeline flow ----
    story.append(Paragraph("2. Step-by-step pipeline flow", S["h1"]))
    story.append(Paragraph(
        "The pipeline is implemented in <font face='Courier'>policy_platform/pipeline.py</font> and runs "
        "six numbered steps. Every step produces an entry in the audit trail (AgentStep).",
        S["body"]
    ))

    steps = [
        ("Step 1 - Receive",
         "The user uploads a file via <font face='Courier'>POST /api/upload</font>. "
         "The server saves the bytes under <font face='Courier'>data/runs/&lt;run_id&gt;/source/&lt;name&gt;</font> "
         "and inserts a row into the <font face='Courier'>runs</font> SQLite table (see <font face='Courier'>api/db.py</font>)."),
        ("Step 2 - Extract",
         "<font face='Courier'>policy_platform/extractors/dispatch(path)</font> picks the right format extractor "
         "(pdf_extractor, docx_extractor, txt_extractor, rtf_extractor) and returns an <font face='Courier'>ExtractedDocument</font>. "
         "It then runs <font face='Courier'>clean_paragraphs</font> (drops page numbers, repeating headers/footers, garbled lines) and "
         "<font face='Courier'>split_paragraphs</font> (M1 label-aware chunker) to produce the final paragraph stream."),
        ("Step 3 - Apply label-row fields",
         "For Brain label-row slots (1, 2, 3, 4, 11) the system runs <font face='Courier'>field_parser.parse</font> which uses "
         "regex + optional spaCy + sentence-splitting + narrative inference to produce a <font face='Courier'>FieldMap</font> "
         "(Type, Policy Title, Policy Number, Effective Date, Review Note). For label-light documents, "
         "<font face='Courier'>narrative_inference.infer_narrative_fields</font> (Phase C) infers values from prose."),
        ("Step 4 - RAG-Retrieve (Hybrid)",
         "For the prose slots (5-14) the system runs <font face='Courier'>RetrievalPipeline.run</font> with a 3-tier lookup: "
         "(a) heading-anchor regex match against the synonym dictionary in <font face='Courier'>framework/section_map.py</font>; "
         "(b) table passthrough for table-routed slots 9/10/14; (c) hybrid FAISS + BM25 retrieval with optional cross-encoder rerank. "
         "Per-slot guards prevent fabricating content for sections that don't exist in the source (History, Exclusions, "
         "Related Policies, Policy Statement, Introduction)."),
        ("Step 5 - Render to DOCX",
         "<font face='Courier'>policy_platform/renderer.render</font> mutates a frozen Brain template .docx in place. "
         "It walks the slots in <b>reverse order</b> so earlier-slot insertions don't shift later-slot body indices. "
         "It applies label-row substitution, bullet polish, typography, and final Word-format normalization. "
         "For published versions the <font face='Courier'>lines_json_renderer</font> is used as the primary path "
         "because it preserves reviewer edits verbatim."),
        ("Step 6 - Validate",
         "<font face='Courier'>policy_platform/validator.validate</font> performs integrity checks: Brain media SHA, "
         "body growth check, slot integrity, recoverable-value guard for label-rows, and no-Brain-defaults leak detection. "
         "If any check fails, <font face='Courier'>ValidationFailed</font> is raised. The audit JSON is finalized and "
         "the output DOCX is moved to its final location."),
    ]
    for title, body in steps:
        story.append(Paragraph(title, S["h3"]))
        story.append(Paragraph(body, S["body"]))
        story.append(Spacer(1, 4))
    story.append(PageBreak())

    # ---- 3. File-by-file inventory ----
    story.append(Paragraph("3. File-by-file inventory", S["h1"]))
    story.append(Paragraph(
        "Every Python file in the backend, grouped by directory. Each entry has a one-line purpose and the "
        "3-8 most important public symbols.",
        S["body"]
    ))

    def section(title):
        story.append(Paragraph(title, S["h2"]))

    def file_block(path, purpose, symbols):
        story.append(Paragraph(f"<font face='Courier'>{path}</font>", S["h3"]))
        story.append(Paragraph(f"<b>Purpose:</b> {purpose}", S["body"]))
        for sym in symbols:
            story.append(Paragraph(f"&bull; {sym}", S["bullet"]))
        story.append(Spacer(1, 3))

    # 3.1 policy_platform
    section("3.1  policy_platform/  - core domain library")
    file_block(
        "policy_platform/pipeline.py",
        "Top-level 6-step pipeline orchestrator (Receive -> Extract -> RAG -> Apply -> Render -> Validate) "
        "with a shared post-extraction body used by both the PDF-input flow and the lines_json-input flow.",
        [
            "<b>get_pipeline()</b> - process-wide singleton accessor for the lazily-built RetrievalPipeline.",
            "<b>process(input_path, output_path, ...)</b> - run the full pipeline on a source file (PDF/DOCX/TXT/RTF); returns an AuditResult.",
            "<b>run_from_lines_json(lines_json, output_path, ...)</b> - re-run the pipeline against a reviewer's saved rich payload, with optional reviewer_bindings override map.",
            "<b>_run_extracted_pipeline(...)</b> - shared body for both entry points; performs label-row synthesis, title/reason injection, cross-field refinement, RAG retrieval, reviewer-binding, validation, and AuditResult assembly.",
            "<b>PipelineError</b> - exception carrying the failing step number/name.",
            "<b>_maybe_inject_title_from_top_paragraph / _maybe_inject_reason_from_intro_paragraph / _refine_field_map_cross_field / _strip_title_prefix</b> - field-map heuristics for label-light documents.",
        ],
    )
    file_block(
        "policy_platform/pipeline_types.py",
        "Defines the AuditResult + AgentStep dataclasses that flow from the pipeline back into the audit log / DB.",
        [
            "<b>AgentStep</b> - step number/name/ok/detail row for the audit trail.",
            "<b>AuditResult</b> - full run record (run_id, document_name, processing_time_ms, framework_version, framework_sha256, sections, steps, integrity_checks, total_placed/dropped chars).",
            "<b>extraction_path</b> field on AuditResult: 'rules' | 'spacy' | 'spacy-fallback' indicating which label-extraction path was used.",
        ],
    )
    file_block(
        "policy_platform/rag_adapter.py",
        "Bridge between RAGResult (new) and ClassificationResult (legacy renderer contract); single place that "
        "translates per-slot RAG assignments into SectionSlot objects the renderer expects.",
        [
            "<b>build_classification_from_rag(rag_result, source_paragraph_count)</b> - returns a ClassificationResult with content_paragraphs / content_tables / placed_paragraphs / status / routing_rule per slot; marks missing slots as Skipped.",
            "<b>_CLAUSE_SPLIT_RE / _BULLET_SPLIT_RE</b> - pre-compiled regexes that split a chunk at numbered clauses (2.1., 3.4.) and bullet markers (U+25AA) already present in the source, preserving PDF layout.",
            "<b>_split_into_source_paragraphs / _normalize_chunk</b> - final paragraph-and-bullet splitting + whitespace/sentence-terminator cleanup before handing text to the renderer.",
        ],
    )
    file_block(
        "policy_platform/renderer.py",
        "Reverse-order renderer that mutates the Brain template .docx in place; writes user content into each "
        "Brain slot, applies typography, page breaks, bullet substitution, and normalization.",
        [
            "<b>render(classified, extracted, brain_path, output_path, ...)</b> - top-level render call; processes slots in REVERSE order, applies label-row substitution, post-render styling, bullet polish, and final Word-format normalization.",
            "<b>_render_slot</b> - renders one slot (skipping label-row slots 1, 2, 3, 4, 11 which are owned by _apply_brain_label_rows).",
            "<b>_replace_table_element</b> - Phase T rewrite that rebuilds the table from source data (rows=source row count, cols=max source col count, equal-split column widths).",
            "<b>_replace_header_text / _rewrite_header_paragraphs</b> - header-text rewrite that only swaps the bracketed [title] / [version] tokens, preserving the Brain's anchored logo and straight-connector line.",
            "<b>_restore_media_store_compression</b> - fix for python-docx writing media as DEFLATE; rewrites word/media/* entries as STORE so Microsoft Word's strict OOXML reader accepts them.",
        ],
    )
    file_block(
        "policy_platform/style.py",
        "Centralized typography / placeholder / bullet-handling primitives; pure functions that mutate "
        "python-docx OxmlElements in place.",
        [
            "<b>apply_styles_to_paragraph / apply_styles_to_section</b> - body typography (Calibri 10pt, justify, line 1.5, 4pt before/after) or heading typography.",
            "<b>_apply_marker_bold</b> - bolds ONLY inline markers like 'Note:' / 'Important:' without bolding the whole paragraph (run-splitting approach).",
            "<b>replace_bullets_with_filled / bold_bullet_characters</b> - replaces (U+2022)/(U+25E6) with (U+25CF) (filled black) and optionally bolds the bullet glyphs via per-run splitting.",
            "<b>render_not_found_placeholder / render_table_no_data_placeholder</b> - render the unified marker 'Data is not found in source file' in plain body styling.",
            "<b>handle_example_prefix</b> - detects 'Example:' / 'Example -' / 'Example --' prefixes and prepends a filled bullet accordingly.",
        ],
    )
    file_block(
        "policy_platform/validator.py",
        "Post-render integrity validator (Brain media SHA, body growth check, slot integrity, recoverable-value "
        "guard for label-rows, no-Brain-defaults leak detection).",
        [
            "<b>validate(extracted, rendered_docx, classified)</b> - runs every integrity check and returns a {checks: [...]} report; raises ValidationFailed on any check fail.",
            "<b>ValidationFailed</b> - exception raised when any check fails.",
            "<b>VALIDATION_FAILURE_MSG</b> - exact message string used by the renderer/UI.",
            "<b>BRAIN_EXAMPLE_FINGERPRINTS</b> - tuple of canonical Brain defaults that must NEVER leak into output (Award PDF, '13 Feb 2023', 'Daw Win Win Tint', etc.).",
            "<b>LABEL_ROW_SLOT_IDS / SLOT_LABEL_CANONICAL / LABEL_ROW_CANONICAL</b> - module-level constants derived from FROZEN_SECTIONS so adding a new Brain label-row slot auto-extends the guard.",
        ],
    )
    file_block(
        "policy_platform/audit.py",
        "Builds the JSON-serializable audit dict stored in runs.db's audit_json column (sections, "
        "integrity_checks, steps, dropped samples); no files are written here.",
        [
            "<b>new_run_id()</b> - returns uuid4().hex.",
            "<b>now_iso()</b> - UTC ISO-8601 timestamp with timezone.",
            "<b>build_audit(result) / build_audit_json(result)</b> - convert an AuditResult into the JSON dict/serialized string the runner stores in the DB.",
            "<b>attach_slot_diff(sections, prev_lines_json, new_lines_json)</b> - annotates each section with before/after text + changed flag for the per-slot diff column.",
        ],
    )
    file_block(
        "policy_platform/analyzer.py",
        "Type-compatibility shim; re-exports SectionSlot and ClassificationResult dataclasses so existing "
        "imports keep working after the rule-based analyzer was retired.",
        [
            "<b>SectionSlot</b> - renderer-facing dataclass (status, content_paragraphs, content_tables, placed_paragraphs, routing_rule).",
            "<b>ClassificationResult</b> - sections dict keyed by slot id plus routing source/table indices and dropped_paragraph/table_indices.",
        ],
    )
    file_block(
        "policy_platform/cli.py",
        "Command-line interface for Brain manifest init/verify/info and pipeline process; used by "
        "<font face='Courier'>python -m policy_platform.cli</font>.",
        [
            "<b>main(argv)</b> - routes to init / verify / info / process subcommands; prints a one-line result or raises ValidationFailed/PipelineError.",
        ],
    )
    file_block(
        "policy_platform/config.py",
        "Project-wide configuration: Brain manifest paths, RAG env-var knobs, API host/port, DB paths.",
        [
            "<b>PROJECT_ROOT / DATA_DIR / BRAIN_DIR / BRAIN_PATH / MANIFEST_PATH</b> - frozen Brain template + manifest paths.",
            "<b>FRAMEWORK_VERSION</b> - 'Brain-PF5-v1.1.0'.",
            "<b>SKIPPED_STATUS / FOUND_EMPTY_STATUS / BRAIN_REASON_CAPACITY</b> - render-time strings + caps.",
            "<b>RAG_TIMEOUT_SECONDS / RAG_ALPHA / RAG_TOP_K_PER_BACKEND / RAG_RERANK_POOL / RAG_MIN_CONFIDENCE</b> - per-document RAG tunables (all env-var backed).",
            "<b>API_HOST / API_PORT / API_BASE_URL / DB_PATH</b> - API and DB defaults.",
            "<b>RAG_LABEL_CHUNKING</b> - master switch for the M1 label-aware chunking family (default ON).",
        ],
    )
    file_block(
        "policy_platform/lines_json_renderer.py",
        "Stage-2 direct slot-by-slot writer for the reviewer's saved lines_json; treats the Brain as a "
        "frozen scaffold and writes rich HTML content into each slot's body, paragraph-by-paragraph (no RAG re-run).",
        [
            "<b>render_lines_json_to_brain(lines_json, brain_path, output_path, ...)</b> - top-level entry point; normalises the payload, infers anchor slots, then delegates to _render_slot_direct per slot.",
            "<b>_render_slot_direct</b> - per-slot body writer (heading dedup, scaffold body overwrite, leftover scaffold paragraph deletion, slot14 numPr fix).",
            "<b>_normalise_lines_json / _normalise_paragraph_payload / _normalise_table_payload</b> - coerce legacy string/rows payloads into the rich dict shape.",
            "<b>_apply_publication_styling / _apply_metadata_styling</b> - publication-time 2.0 line-height, jc=left, italic-strip for metadata rows.",
            "<b>_apply_visible_table_borders</b> - force visible 1pt black borders so toolbar tables don't inherit the brain's faint TableGridLight style.",
        ],
    )
    file_block(
        "policy_platform/post_render.py",
        "Path-mutating helper that strips decorative black horizontal lines (VML/DrawingML) from a docx zip; "
        "used both at output time and to sanitize the Brain template once.",
        [
            "<b>clean_xml_part(xml)</b> - apply every cleaning pass (orphan &lt;a:ln&gt;, &lt;w:pict&gt;, &lt;w:drawing&gt;, &lt;a:prstGeom prst='line'&gt;, cy='0' line shapes).",
            "<b>strip_black_lines(docx_path)</b> - rewrite header/footer/document XML in-place, swapping the docx with a temp.",
            "<b>sanitize_brain_in_place(brain_path)</b> - same as strip_black_lines but with intent-revealing name for Brain initialization.",
        ],
    )
    file_block(
        "policy_platform/shims.py",
        "Python 3.14 compatibility shim; aliased inspect.getargspec = inspect.getfullargspec so lxml's "
        "precompiled Windows wheel doesn't crash on first import.",
        [
            "<b>_apply_inspect_shim()</b> - runs at module import; sets inspect.getargspec to inspect.getfullargspec if missing.",
        ],
    )

    story.append(PageBreak())

    # 3.2 extractors
    section("3.2  policy_platform/extractors/  - text extraction and cleaning")
    file_block(
        "policy_platform/extractors/__init__.py",
        "Format dispatch, format-parity normalization, and M1 label-aware paragraph splitting.",
        [
            "<b>SUPPORTED_EXTENSIONS</b> - {'pdf', 'docx', 'txt', 'rtf'}.",
            "<b>dispatch(path)</b> - extract -> clean -> join mid-sentence lines -> split on Brain-label boundaries -> normalize colons; returns an ExtractedDocument.",
            "<b>_join_mid_sentence_lines / _split_paragraphs_on_brain_labels</b> - format-parity helpers (Phase R).",
            "<b>split_paragraphs(...)</b> - canonical M1 chunker; splits dense paragraphs at every section-heading label occurrence.",
            "<b>split_on_section_heading_labels / chunk_paragraphs_by_section_heading</b> - thin back-compat wrappers around split_paragraphs.",
        ],
    )
    file_block(
        "policy_platform/extractors/base.py",
        "Common ExtractedDocument dataclass shared by every format extractor.",
        [
            "<b>ExtractedDocument</b> - paragraphs / tables / source_sha256 / source_format / cleaner_dropped / original_indices / table_paragraph_indices / paragraph_table_origin / line_bboxes / page_rotations.",
            "<b>full_text</b> property - concatenation of paragraphs and table cells used for checksum reference.",
        ],
    )
    file_block(
        "policy_platform/extractors/cleaner.py",
        "Pre-routing text cleaning; drops page numbers, repeating headers/footers, garbled lines, and applies "
        "mojibake repair to cleaned output.",
        [
            "<b>clean_paragraphs(paragraphs)</b> - returns (cleaned, dropped_records, original_indices); proper-name aware so names like 'Htet Oo Wai Yan' are not eaten as header-repeats.",
            "<b>REPEAT_THRESHOLD / GARBLED_RATIO</b> - tunables (env-var backed).",
            "<b>clean_extracted(paragraphs, format, sha)</b> - convenience wrapper for callers that don't need original_indices.",
        ],
    )
    file_block(
        "policy_platform/extractors/doc_extractor.py",
        "Legacy .doc file support stub; explicitly raises UnsupportedFormatError instructing the user to "
        "resave as .docx or .pdf.",
        [
            "<b>UnsupportedFormatError</b> - exception class.",
            "<b>extract(_path)</b> - always raises UnsupportedFormatError.",
        ],
    )
    file_block(
        "policy_platform/extractors/docx_extractor.py",
        "python-docx based .docx extractor that walks body in document order and emits alternating "
        "Label/Value paragraphs for label-row tables.",
        [
            "<b>extract(path)</b> - builds ExtractedDocument; emits label-row table cells into the paragraph stream as Label/Value alternation; data tables stay in tables[] only.",
            "<b>_is_label_row_table(rows)</b> - heuristic that flags 2-column tables whose col-0 contains a known Brain label-row keyword.",
            "<b>_LABEL_ROW_KEYWORDS</b> - frozen keyword set ('type', 'policy title', 'policy number', ...).",
        ],
    )
    file_block(
        "policy_platform/extractors/field_parser.py",
        "Extracts Label: value pairs from cleaned paragraphs using regex + spaCy + sentence-splitting fallback "
        "+ narrative inference.",
        [
            "<b>parse(input_paragraphs, dropped_paragraphs, cleaned_to_original)</b> - master entry; runs spaCy -> regex -> sentence-split -> narrative-inference; returns FieldMap dict.",
            "<b>last_extraction_path()</b> - returns 'rules' | 'spacy' | 'spacy-fallback' | 'rules+sentence' | 'rules+narrative'.",
            "<b>_split_alternating_label_value</b> - recovers cleaner-dropped values between two known labels via original-index alignment (Phase B.1).",
            "<b>_split_into_label_clauses / _join_continued_lines</b> - sentence-level clause splitting and continuation-joining.",
        ],
    )
    file_block(
        "policy_platform/extractors/header_extractor.py",
        "Heuristic policy-title + version-tag extraction from PDF metadata or first-page largest line.",
        [
            "<b>extract(input_path, pdf_metadata, cleaned_paragraphs)</b> - returns {'title', 'version', 'source'}; 'source' is 'pdf_metadata' | 'first_page_largest' | 'filename' | 'fallback'.",
            "<b>_score_title(line, position)</b> - length + ALL-CAPS bonus + position bonus, with penalties for scope/audience statements and Label: value lines.",
            "<b>_VERSION_PATTERNS</b> - compiled regexes for CL&amp;H_NN/NN, FYNN-NN, vN.N, Rev. N.",
        ],
    )
    file_block(
        "policy_platform/extractors/mojibake.py",
        "Targeted mojibake repair for (U+FFFD) replacement characters produced by CP1252-&gt;UTF-8 misreads.",
        [
            "<b>normalize_mojibake(text)</b> - replaces (U+FFFD) with (U+2019) apostrophe / (U+2013) en-dash / '?' based on context.",
        ],
    )
    file_block(
        "policy_platform/extractors/narrative_inference.py",
        "Phase C narrative-inference rules for FDA-style / label-light documents (Type, Applicable Sector, "
        "Functional Area, Brief Description, Reason for Policy, Supersedes, etc.).",
        [
            "<b>infer_narrative_fields(paragraphs, existing_field_map, always_run=True)</b> - returns additional canonical-label -> value inferred from prose; never overwrites existing non-empty values.",
            "<b>_infer_type_from_prose / _infer_applicable_sectors / _infer_functional_area / _infer_responsible_function / _infer_responsible_officer / _infer_last_reviewed / _infer_supersedes / _infer_applies_to / _infer_brief_description / _infer_reason_for_policy / _infer_exclusions</b> - per-field inference rules.",
            "<b>_DEPARTMENT_HEAD_NOUNS / _OWNERSHIP_KEYWORDS / _AUDIENCE_KEYWORDS / _OFFICER_KEYWORDS / _REVIEW_KEYWORDS / _EXCLUSIONS_HEADING_RE</b> - vocabulary tables.",
        ],
    )
    file_block(
        "policy_platform/extractors/normalize.py",
        "Utility (NOT wired into dispatch) that merges mid-sentence lines using a conservative rule "
        "(no terminator + lowercase start); left for experimental use.",
        [
            "<b>normalize_lines_to_paragraphs(lines, max_paragraph_chars=600)</b> - conservative line-merge helper.",
        ],
    )
    file_block(
        "policy_platform/extractors/pdf_extractor.py",
        "PDF extractor with pdfplumber as primary (preserves visual row layout via x/y tolerance) and "
        "PyMuPDF as table/augmentation source.",
        [
            "<b>extract(path)</b> - pdfplumber first; PyMuPDF for extra tables when pdfplumber missed any; raises ValueError if neither produces content.",
            "<b>_try_pdfplumber / _try_pymupdf</b> - backend implementations that filter data-table and label-row-table cell text out of the paragraph stream.",
        ],
    )
    file_block(
        "policy_platform/extractors/prose_normalize.py",
        "Shared format-parity prose normalizer producing a Block stream (paragraph | label_row | table); "
        "intended for the chunker/future wiring.",
        [
            "<b>Block</b> - kind/text/pairs/rows dataclass.",
            "<b>raw_lines_to_blocks(lines, ...)</b> - convert raw lines into a Block stream with aggressive/conservative continuation profiles.",
            "<b>_ensure_brain_label_regex()</b> - lazy-built regex for label-row detection.",
        ],
    )
    file_block(
        "policy_platform/extractors/rtf_extractor.py",
        "Minimal RTF extractor using striprtf.rtf_to_text.",
        [
            "<b>extract(path)</b> - returns ExtractedDocument with paragraphs from line-split RTF text (no tables).",
        ],
    )
    file_block(
        "policy_platform/extractors/spacy_extractor.py",
        "Opt-in spaCy sentence-segmentation-based label extraction; gated by AGENTIC_POLICY_USE_SPACY env var.",
        [
            "<b>is_available()</b> - True iff env var is set AND spaCy + en_core_web_sm import cleanly.",
            "<b>SpaCyUnavailable</b> - raised when spaCy is requested but missing.",
            "<b>extract_field_map(input_paragraphs)</b> - returns (field_map, extraction_path) where path is 'spacy' or 'spacy-fallback'.",
        ],
    )
    file_block(
        "policy_platform/extractors/title_extractor.py",
        "Stage-C simple line-1 fallback for policy title; takes the first non-empty line if its length is 8-80 chars.",
        [
            "<b>extract_title_from_paragraphs(input_paragraphs)</b> - returns the first 8-80 char non-empty line, or None.",
        ],
    )
    file_block(
        "policy_platform/extractors/txt_extractor.py",
        "Minimal TXT extractor that preserves content verbatim line-for-line (CRLF preserved).",
        [
            "<b>extract(path)</b> - returns ExtractedDocument; raises ValueError for non-UTF-8 input.",
        ],
    )

    story.append(PageBreak())

    # 3.3 framework
    section("3.3  policy_platform/framework/  - Brain schema and slot map")
    file_block(
        "policy_platform/framework/agent_classifier.py",
        "Reserved placeholder for a future LLM classifier; explicitly raises NotImplementedError to forbid "
        "text mutation if wired in.",
        [
            "<b>classify(*args, **kwargs)</b> - always raises NotImplementedError.",
        ],
    )
    file_block(
        "policy_platform/framework/brain.py",
        "Initialize and verify the frozen Brain manifest (SHA-256 + embedded media list).",
        [
            "<b>BrainManifestTampered</b> - raised when the on-disk Brain SHA does not match the manifest.",
            "<b>write_manifest(brain_path, manifest_path)</b> - compute SHA + media list, persist the manifest.",
            "<b>load_manifest(manifest_path)</b> - read the manifest JSON.",
            "<b>init_or_verify(init=True|False)</b> - either copy the source Brain in + write the manifest, or verify the on-disk Brain matches the manifest.",
            "<b>main(argv)</b> - CLI entry point used by <font face='Courier'>python -m policy_platform.framework.brain</font>.",
        ],
    )
    file_block(
        "policy_platform/framework/brain_fields.py",
        "Brain label-value schema for slots 1, 2, 3, 4, 11 (Type / Policy Title / Policy Number / ... / "
        "Policy Review Note) including synonym dictionaries, validation rules, and field value parsing.",
        [
            "<b>BRAIN_HEADER_FIELDS / BRAIN_BRIEF_DESCRIPTION_FIELDS / BRAIN_APPROVAL_FIELDS / BRAIN_REASON_FIELDS / BRAIN_REVIEW_NOTE_FIELDS / BRAIN_LABEL_ROWS</b> - ordered list of (canonical, synonyms) tuples (the single source of truth for label-row slots).",
            "<b>canonical_label(input_label)</b> - maps any user-supplied label (with synonyms) to its canonical Brain label; uses longest-prefix match as fallback.",
            "<b>is_exact_label_or_synonym(text)</b> - exact-match counterpart to canonical_label for split-on-label-boundary use.",
            "<b>field_map(input_paragraphs)</b> - returns {canonical_label: value} by regex-matching Label: value lines.",
            "<b>parse_field_value(canonical, raw_value)</b> - per-field validation (Type noun-phrase, dates, references, person names, department suffixes, review-cycle fallbacks).",
            "<b>missing_field_placeholder(label)</b> - returns '&lt;label&gt; Data is not found in source file' marker.",
        ],
    )
    file_block(
        "policy_platform/framework/brain_slot_map.py",
        "Identifier-based slot lookup (heading strings, slot names, original body-child indices) used by "
        "renderer + validators.",
        [
            "<b>SLOT_HEADINGS</b> - dict[slot_id, canonical heading string].",
            "<b>SLOT_NAMES / SLOT_HAS_TABLE</b> - display metadata.",
            "<b>BRAIN_SLOT_RANGES</b> - dict[slot_id, {body_items: [...]}]; index-free lookup is built on top of these.",
            "<b>find_slot_boundaries(doc)</b> - walk the body of <font face='Courier'>doc</font> and return {sec_id: {start, end, elements}} by matching paragraph text against SLOT_HEADINGS.",
        ],
    )
    file_block(
        "policy_platform/framework/config_loader.py",
        "Typed wrapper around layout_config.yaml that exposes per-slot synonyms / table signals / column "
        "templates / phrase templates / table-order hints without forcing YAML-aware code into consumers.",
        [
            "<b>get_slot_section / get_slot_synonyms_overrides / get_slot_table_signal_overrides / get_slot_column_templates / get_phrase_templates / get_table_order_hints / get_slot_queries_override / get_generic_table_signals / get_label_row_keywords / get_structural_body_breaks</b> - typed accessors that return defaults when the YAML is missing.",
            "<b>reset_cache()</b> - drop the in-process config cache (used by tests).",
        ],
    )
    file_block(
        "policy_platform/framework/section_map.py",
        "Frozen 15-slot Brain Framework definition with synonym dictionaries.",
        [
            "<b>FROZEN_SECTIONS</b> - tuple of {id, title} for slots 1-15; never modified.",
            "<b>SECTION_HEADING_SYNONYMS</b> - dict[slot_id, list[str]] of all synonyms for every prose slot heading (single source of truth for heading detection).",
            "<b>is_frozen()</b> - always True.",
        ],
    )
    file_block(
        "policy_platform/framework/slot_capacity.py",
        "Per-slot paragraph capacity; effectively unlimited (10,000) so the renderer can grow slots when "
        "distribution places unmatched content.",
        [
            "<b>DEFAULT_SLOT_CAPACITY / SLOT_CAPACITY</b> - per-slot caps.",
            "<b>get_slot_capacity(slot_id)</b> - returns the cap for slot_id.",
        ],
    )
    file_block(
        "policy_platform/framework/slot_tiers.py",
        "Slot tier classification (1=critical, 2=soft-required, 3=optional) for required-field handling and "
        "validation rules.",
        [
            "<b>SLOT_TIERS</b> - dict[slot_id, tier]; frozen with the Brain manifest.",
            "<b>slot_required(sec_id)</b> - True iff tier is 1 or 2.",
            "<b>slot_label(sec_id)</b> - display label looked up from FROZEN_SECTIONS.",
        ],
    )

    story.append(PageBreak())

    # 3.4 rag
    section("3.4  policy_platform/rag/  - hybrid retrieval subsystem")
    file_block(
        "policy_platform/rag/__init__.py",
        "Package surface that re-exports RetrievalPipeline and RAGResult.",
        [
            "<b>RetrievalPipeline / RAGResult</b> - public types.",
        ],
    )
    file_block(
        "policy_platform/rag/bm25_store.py",
        "Single-document BM25 keyword index with deterministic tokenization and pure-Python BM25 Okapi fallback.",
        [
            "<b>BM25Store</b> - <font face='Courier'>build(texts) / search(queries, k)</font>; uses rank_bm25.BM25Okapi when importable, else _PythonBM25.",
            "<b>_PythonBM25</b> - pure-Python BM25 Okapi implementation with Robertson IDF (+0.5 smoothing).",
        ],
    )
    file_block(
        "policy_platform/rag/chunker.py",
        "Sentence-aware chunker for RAG ingestion with overlap; also exposes label-row / short-title / footnote "
        "classifiers used by the RAG exclusion list.",
        [
            "<b>Chunk(text, source_idx, chunk_id)</b> - dataclass.",
            "<b>chunk_paragraphs(paragraphs, target_chunk_size=300, overlap=50)</b> - splits at bullet markers first, then sentence-aware chunks of ~300 chars with 50-char overlap.",
            "<b>split_bullets(paragraph)</b> - splits a paragraph into one per bullet.",
            "<b>is_label_row_paragraph / is_short_title / is_footnote</b> - RAG exclusion classifiers.",
        ],
    )
    file_block(
        "policy_platform/rag/embedder.py",
        "Local sentence-transformer embedder with TF-IDF (sklearn) and deterministic SHA-256 hash fallback.",
        [
            "<b>Embedder</b> - prefer_tfidf=True by default (skips 80-MB cold load); backend == 'sentence-transformers' | 'tfidf' | 'hash'.",
            "<b>embed(texts)</b> - returns (N, D) float32 L2-normalized matrix.",
            "<b>DEFAULT_MODEL_NAME</b> = 'sentence-transformers/all-MiniLM-L6-v2'; <b>FALLBACK_DIM</b> = 384.",
        ],
    )
    file_block(
        "policy_platform/rag/faiss_store.py",
        "Per-document FAISS vector index with numpy cosine fallback.",
        [
            "<b>FaissStore</b> - <font face='Courier'>build(embeddings) / search(query_vecs, k)</font>; backend == 'faiss' | 'numpy'.",
        ],
    )
    file_block(
        "policy_platform/rag/heading_anchors.py",
        "Heading-anchored retrieval for prose slots (5-14); matches heading-anchor regexes, walks paragraphs "
        "forward to find the next boundary, and produces a (start, end, joined_text) tuple per slot.",
        [
            "<b>HEADING_ANCHOR_SLOTS / HEADING_PATTERNS / _COMPILED_HEADINGS</b> - compiled per-slot heading regexes derived from SECTION_HEADING_SYNONYMS.",
            "<b>find_heading_match(slot_id, paragraphs, reserved_paragraphs)</b> - returns Optional[(start, end, joined_text)]; handles inline-body, multi-line headings, cross-slot boundary detection, and structural body-break labels.",
            "<b>_strip_heading_label / _extract_inline_body</b> - per-slot heading-label removal so the body doesn't repeat the heading word.",
            "<b>build_section_index(paragraphs)</b> - per-paragraph slot index used as foundation for table routing.",
            "<b>_is_cross_slot_boundary / _find_inline_clause_break / _clip_chunk_at_clause_break</b> - cross-slot / inline-clause break detection that stops slot-walk from leaking into the next slot's territory.",
        ],
    )
    file_block(
        "policy_platform/rag/reranker.py",
        "Cross-encoder reranker with raw-score fallback (Jaccard lexical overlap) - default OFF so the heavy "
        "90-MB CrossEncoder isn't loaded unless env opt-in.",
        [
            "<b>Reranker</b> - <font face='Courier'>rerank(query, candidates, top_k)</font> returns [(idx, score)] sorted descending.",
        ],
    )
    file_block(
        "policy_platform/rag/retrieval_pipeline.py",
        "End-to-end RAG orchestrator implementing 3-tier lookup per slot (heading-anchor -> table passthrough "
        "-> RAG fallback) with per-document timeout, paragraph reservation, and per-slot guards for History / "
        "Exclusions / Related Policies / Policy Statement / Introduction.",
        [
            "<b>RetrievalPipeline</b> - orchestrator with shared Embedder + Reranker (TF-IDF + fallback); per-call rebuild of FAISS + BM25 indices.",
            "<b>RAGResult / SlotAssignment</b> - dataclasses describing per-slot outcomes.",
            "<b>RetrievalPipeline.run(paragraphs, tables, table_paragraph_indices)</b> - the orchestrator. Tier1 heading-anchor + Tier 2 table-passthrough first; Tier 3 RAG for remaining slots. Tier-1 slots are sorted by tier so critical slots get the largest time budget.",
            "<b>_rag_assign_slot(...)</b> - per-slot hybrid FAISS+BM25 retrieval with position-aware scoring, confidence threshold, and reranker integration.",
            "<b>_find_intro_paragraph / _find_table_section_slot</b> - position-based fallbacks for slot 5 / table routing.",
            "<b>_has_version_history_markers / _has_exclusions_section_markers / _has_related_policies_section_markers / _has_introduction_section_markers / _has_policy_statement_section_markers</b> - per-slot RAG-fallback guards (avoid fabricating wrong content when the source lacks the section).",
            "<b>_install_timeout / _clear_timeout</b> - SIGALRM-based timeout (POSIX only; Windows uses wall-clock checks).",
        ],
    )
    file_block(
        "policy_platform/rag/section_detector.py",
        "Generic section-start detector used as a fallback signal when slot-specific heading patterns miss "
        "a real section.",
        [
            "<b>looks_like_section_heading(paragraph)</b> - True for ALL-CAPS, numbered, short title, multi-line heading+body patterns.",
            "<b>find_section_starts(paragraphs)</b> - list of candidate section boundary indices.",
        ],
    )
    file_block(
        "policy_platform/rag/slot_queries.py",
        "Per-slot retrieval queries used by the RAG tier; abstract natural-language descriptions of each "
        "slot's content.",
        [
            "<b>SLOT_QUERIES</b> - dict[slot_id, list[str]] of natural-language queries (e.g. slot 10 has award-tier, flood-relief, and facility-level queries).",
            "<b>get_queries_for_slot / all_slots</b> - accessors.",
        ],
    )
    file_block(
        "policy_platform/rag/table_routing.py",
        "Table routing for slots 9 (Exclusions), 10 (Award Structure), 14 (History); routes tables by document "
        "position first and content signals as fallback, suppresses generic non-award tables.",
        [
            "<b>TABLE_SLOTS / TABLE_SLOT_SIGNALS / GENERIC_TABLE_SIGNALS / MIN_SIGNAL_HITS / SLOT_10_MIN_SIGNAL_HITS</b> - routing knobs.",
            "<b>_LABEL_ROW_KEYWORDS</b> - frozen set used to exclude slot-1 schema tables from slot 9/10/14 matching.",
            "<b>_looks_like_label_row_table / _classify_table_by_content / _table_is_generic_non_award</b> - per-table classifiers.",
            "<b>find_table_for_slot / find_table_for_slot_with_context / find_all_tables_for_slot_with_context</b> - primary routing functions.",
        ],
    )

    story.append(PageBreak())

    # 3.5 api
    section("3.5  api/  - HTTP server, DB, DOCX export")
    file_block(
        "api/server.py",
        "Stdlib BaseHTTPRequestHandler backend that serves the entire HTTP API (auth, upload, process, status, "
        "result, preview, download, download-all, history, version-control, comments, audit, project-members, "
        "notifications); includes a thread-safe LRU preview cache.",
        [
            "<b>Handler</b> - BaseHTTPRequestHandler subclass dispatching all routes via regex matchers (do_GET / do_POST / do_DELETE / do_OPTIONS).",
            "<b>handle_upload / handle_process / handle_status / handle_result / handle_preview / handle_download / handle_download_all / handle_history / handle_login / handle_logout / handle_me / handle_list_users / handle_versions_list / handle_version_get / handle_comments_list / handle_audit_list</b> - HTTP route handlers.",
            "<b>PreviewCache</b> - thread-safe LRU cache keyed on (run_id, docx_path, mtime, audit_json_len); 5-minute TTL, max 64 entries.",
            "<b>invalidate_preview_cache(run_id)</b> - drop cache entries for a given run after publish/save.",
            "<b>parse_multipart(handler)</b> - stdlib-only multipart/form-data parser for the upload route.",
        ],
    )
    file_block(
        "api/db.py",
        "SQLite helpers for the runs table; init_db creates schema (with idempotent migrations for older DBs), "
        "insert_run / update_status / get_run / list_done_runs; enforces MAX_RUN_HISTORY=30 with FIFO eviction.",
        [
            "<b>_conn()</b> - sqlite3.Row-factory connection.",
            "<b>init_db()</b> - create / migrate schema; calls versions_io.init_version_tables, versions_io.init_drafts_table, users.init_user_tables, and seeds the 3 default users.",
            "<b>insert_run(run_id, filename, size, source_path, created_by_user_id)</b> - evicts oldest runs to keep MAX_RUN_HISTORY cap; auto-adds creator as 'approver' in project_members.",
            "<b>update_status / get_run / list_done_runs</b> - status / read helpers.",
            "<b>MAX_RUN_HISTORY = 30</b>; <b>_evict_run(c, run_id)</b> - cascades child rows + on-disk .docx.",
        ],
    )
    file_block(
        "api/auth_middleware.py",
        "Stdlib-only HTTP request authentication helpers used by Handler; reads Authorization: Bearer &lt;token&gt;, "
        "looks up session, returns user or sends 401/403.",
        [
            "<b>_extract_bearer_token(handler)</b> - read bearer token from Authorization header.",
            "<b>get_current_user(handler)</b> - non-sending user lookup.",
            "<b>require_auth / require_admin</b> - send 401/403 and return None on failure.",
            "<b>require_project_access(handler, run_id, min_level)</b> - returns (user, access_level) after enforcing auth + membership + minimum level.",
        ],
    )
    file_block(
        "api/users.py",
        "Multi-user authentication + session + project membership management; bcrypt password hashing (cost 12), "
        "opaque 32-byte URL-safe session tokens (TTL 24h), 3 seed users (admin via env, user1/user2 via bcrypt), "
        "Flow-2 notification helpers.",
        [
            "<b>_hash_password_bcrypt / _verify_password_bcrypt / _verify_password_env / _verify_password_for_user</b> - password hashing + verification (admin uses ADMIN_PASSWORD env var).",
            "<b>SCHEMA_SQL / init_user_tables(conn)</b> - idempotent user/session/project_members schema.",
            "<b>SEED_USERS / seed_users_if_empty(conn)</b> - insert 3 default users on first init only.",
            "<b>login / logout / create_session / delete_session / get_user_by_token</b> - session lifecycle.",
            "<b>add_project_member / remove_project_member / list_project_members / get_user_project_access / meets_access</b> - per-project access levels (viewer / editor / approver).",
            "<b>get_my_shared_projects / get_unread_count / mark_project_seen / mark_all_projects_seen / dismiss_notification / dismiss_all_notifications</b> - Flow-2 notification feed helpers.",
        ],
    )
    file_block(
        "api/versions_io.py",
        "DB helpers for the workflow / version-control feature: policy_versions (V1, V2, ...) / "
        "review_comments / audit_log + policy_drafts (autosave with 60s update window).",
        [
            "<b>SCHEMA_SQL / init_version_tables(conn)</b> - idempotent workflow schema (including actor_user_id, last_seen_at, dismissed_at migrations).",
            "<b>create_initial_version / save_version / latest_version_no / get_versions / get_version / get_previous_version / latest_published_version_no</b> - version CRUD.",
            "<b>set_review_status / set_published</b> - status transitions with audit logging.",
            "<b>add_comment / list_comments / resolve_comment</b> - review comments.",
            "<b>add_audit / list_audit</b> - manual + list audit entries.",
            "<b>DRAFTS_SCHEMA_SQL / init_drafts_table</b> - draft schema (drops + recreates to clean Stage-4.12 residue).",
            "<b>get_draft / list_drafts / get_draft_by_edit_count / upsert_draft / delete_draft / delete_draft_by_edit_count</b> - draft persistence with 60s update-in-place window per (run_id, edit_id).",
            "<b>consume_draft_into_version</b> - submit flow that snapshots a draft row into a frozen policy_versions row.",
        ],
    )
    file_block(
        "api/audit_diff.py",
        "Phase-6 long-format per-slot diff xlsx comparing previous approved version's lines_json to the new "
        "approved version; falls back to CSV when openpyxl is unavailable.",
        [
            "<b>build_diff_rows(run_id, version_no, prev_lines_json, curr_lines_json)</b> - one row per 0..15 with slot_label / before_text / after_text / changed.",
            "<b>write_diff_xlsx(...)</b> - writes SlotDiff sheet (or CSV fallback).",
            "<b>read_diff_xlsx(output_path)</b> - load the diff back as a list of rows (for tests).",
        ],
    )
    file_block(
        "api/api_preview.py",
        "Reads a generated .docx and returns its paragraphs and tables in exact order (no slot grouping), "
        "normalised to the rich payload shape so the web preview matches the .docx 1:1.",
        [
            "<b>build_preview_from_docx(docx_path)</b> - returns {'lines': [normalized_paragraphs_and_tables]}; the canonical preview API used by /api/preview.",
            "<b>_normalise_paragraph_payload / _normalise_table_payload</b> - legacy string / list-of-list coercion into rich dict shape.",
            "<b>_extract_paragraphs_from_docx(docx_path)</b> - pure regex over word/document.xml.",
        ],
    )
    file_block(
        "api/pipeline_runner.py",
        "Per-run pipeline runner with 60s hard timeout (daemon thread + join); batch processing with one retry "
        "of timeouts; auto-creates V1 from the rendered docx after a successful run.",
        [
            "<b>run_pipeline(run_id, source_path, output_dir)</b> - runs the RAG-Hybrid pipeline; state transitions processing -> done / failed / failed_timeout; persists docx_path + audit_json + framework version + template sha.",
            "<b>run_batch(items, output_root)</b> - sequential batch run with one retry of timed-out items.",
            "<b>PER_FILE_TIMEOUT_SECONDS = 60.0</b> (matches RAG timeout).",
            "<b>_summarize(result)</b> - counts sections_filled and markers_count from AuditResult.",
        ],
    )
    file_block(
        "api/publish_to_brain.py",
        "Phase 6 final .docx production for an approved version; PRIMARY path uses lines_json_renderer "
        "(preserves user edits verbatim), SILENT FALLBACK uses run_from_lines_json; applies header/footer "
        "(Phase 8); annotates per-slot diff; marks version as published.",
        [
            "<b>publish_approved_version(c, run_id, version_no, output_dir, actor, actor_user_id)</b> - orchestrates the publish flow; checks brain SHA (advisory), detects newer drafts (Fix G), normalises + infers anchor slots, calls PRIMARY or FALLBACK renderer, applies header/footer, attaches per-slot diff to audit dict, marks published.",
            "<b>_verify_brain_soft()</b> - advisory SHA verification (never blocks).",
        ],
    )
    file_block(
        "api/lines_json_extractor.py",
        "Phase-6 conversion of an approved lines_json back into an ExtractedDocument so "
        "pipeline.run_from_lines_json can re-run end-to-end; also provides slot-classification heuristics for "
        "infer_anchor_slots.",
        [
            "<b>normalise_lines_json(lines_json)</b> - coerce legacy / rich / divider payloads into the rich dict shape.",
            "<b>infer_anchor_slots(lines_json)</b> - pattern-based slot inference for slot=0 paragraphs / tables; uses slot inheritance (paragraph inherits the previous paragraph's slot).",
            "<b>preserve_editor_anchor_slot(lines_json)</b> - propagate editor's anchor_slot sidecar onto slot when slot=0.",
            "<b>reviewer_slot_bindings(lines_json)</b> - build {slot_id: [paragraphs]} map for the pipeline's reviewer-bindings override.",
            "<b>LinesJsonExtractor(lines_json).to_extracted_document()</b> - convert to ExtractedDocument; attaches paragraph_slot_origin sidecar.",
        ],
    )
    file_block(
        "api/lines_json_to_source.py",
        "Phase-6 renderer of a reviewer's lines_json back into a deterministic .txt corpus (slot label + "
        "paragraphs) so pipeline.process() can re-run via a tempfile.",
        [
            "<b>lines_json_to_source_text(lines_json)</b> - slot-id-keyed block emitter.",
            "<b>write_lines_json_as_tempfile / cleanup_tempfile</b> - stdlib tempfile helpers.",
        ],
    )
    file_block(
        "api/docx_approved_export.py",
        "Builds the final .docx for an approved version by rewriting body text from approved_lines_json "
        "(Phase 2 / Phase 4 rich path), then applying header / footer (Phase 8) and round-tripping footnotes (Phase 5).",
        [
            "<b>build_approved_docx(original_docx_path, approved_lines_json, output_path)</b> - Phase-4 rich-text writer via docx_rich_writer; Phase-5 footnote injection via write_footnotes.",
            "<b>_set_paragraph_rich / _set_cell_rich</b> - rich HTML writers; fall back to plain-text when html is empty.",
            "<b>_apply_header_footer(doc, lines_json)</b> - preserves Brain logo + connector line, strips [...] brackets from header/footer, ensures right-aligned tab stop + page X of Y field.",
            "<b>extract_explicit_title_and_version(lines_json)</b> - pull explicit 'Policy Title:' / 'Policy Number:' lines (case-insensitive prefix match).",
            "<b>_strip_brackets_in_runs(part)</b> - walk &lt;w:t&gt; nodes inside part and strip single-run / multi-run [bracket] wrappers.",
        ],
    )
    file_block(
        "api/docx_footnotes_writer.py",
        "Phase 5 round-trip of Word footnotes; builds word/footnotes.xml, patches [Content_Types].xml and "
        "word/_rels/document.xml.rels, returns the frontend-id -&gt; Word-id map for the rich writer to swap in "
        "&lt;w:footnoteReference&gt;.",
        [
            "<b>_collect_footnotes(paragraphs)</b> - walk payloads, deduplicate by (id, body).",
            "<b>_build_footnotes_part(footnotes)</b> - build XML + id_map.",
            "<b>_ensure_content_type / _ensure_rel</b> - idempotent [Content_Types].xml + rels patching.",
            "<b>write_footnotes(docx_path, footnotes)</b> - repack the docx with footnotes part injected.",
            "<b>collect_footnote_anchors_from_html(html)</b> - parse &lt;sup data-fn-id='X'&gt; anchors.",
        ],
    )
    file_block(
        "api/docx_rich_writer.py",
        "Phase 4 TipTap HTML -&gt; python-docx OxmlElement round-trip; preserves bold / italic / colour / font-family / "
        "font-size / heading levels / lists (with checkbox todo lists) / hyperlinks / footnote anchors.",
        [
            "<b>write_paragraph(p_elem, html, footnote_id_map, part, doc)</b> - replace paragraph contents (keeps pPr). Detects lists and routes to _write_list_paragraphs.",
            "<b>_TipTapParser</b> - stdlib HTMLParser tracking inline-style stack + block markers.",
            "<b>_make_rpr / _build_run / _wrap_run_in_hyperlink</b> - run-property builders.",
            "<b>_write_list_paragraphs</b> - splits &lt;ul&gt;/&lt;ol&gt;/todo-list into one &lt;w:p&gt; per &lt;li&gt;, each carrying &lt;w:numPr&gt;.",
            "<b>_ensure_list_numbering_definition(doc)</b> - idempotently declares abstractNum 90 (bullet), 91 (decimal), 92 (todo checkbox).",
            "<b>_HEADING_TAG_TO_OUTLINE / _HEADING_SIZE_BY_OUTLINE</b> - H1=16pt, H2=14pt, H3=13pt.",
        ],
    )

    story.append(PageBreak())

    # 3.6 scripts
    section("3.6  scripts/  - operational utilities")
    file_block(
        "scripts/deploy_outputs.py",
        "Standalone utility that detects the 4 most-recent outputs/ files by document label (Award / Earthquake "
        "/ Coronavirus / Sexual Harassment / Flood) and copies them to ~/Downloads and ~/Documents with friendly "
        "filenames; skips OneDrive-locked files.",
        [
            "<b>detect_label(p)</b> - inspects word/document.xml to classify a docx as one of the 5 known templates.",
            "<b>main()</b> - copies each detected output to ~/Downloads + ~/Documents.",
        ],
    )
    file_block(
        "scripts/find_ids.py",
        "TTY helper that scans HTML on stdin for element id attributes matching step-N / generate-btn / next-N / "
        "gen-filename / gen-status-badge / status-processing / status-done / status-failed / review-btn.",
        [
            "(no public functions; reads stdin and prints matching ids).",
        ],
    )
    file_block(
        "scripts/reset_db.py",
        "Destructive DB reset utility: wipes runs / policy_versions / review_comments / audit_log / "
        "project_members and data/runs/ contents, preserves the 3 seed users, re-initializes schema.",
        [
            "<b>main()</b> - interactive confirmation prompt; auto-confirms on non-tty stdin.",
        ],
    )

    # 3.7 top-level
    section("3.7  Top-level diagnostic scripts")
    file_block(
        "diag_slots.py",
        "Diagnostic CLI that runs dispatch + RetrievalPipeline on a single input file and prints every "
        "paragraph + the classification slot (chunk_text + table + backend).",
        [
            "(no public functions; runs dispatch + pipeline.run then prints sections).",
        ],
    )
    file_block(
        "verify_slot_separation.py",
        "Diagnostic CLI that verifies slot 12 (Definitions) and slot 14 (History) do not bleed into each other "
        "on a single input file; toggles AGENTIC_POLICY_RAG_LABEL_CHUNKING via the second arg.",
        [
            "(no public functions; sets env var, runs dispatch + pipeline.run, checks for the 'Version FY26-27' string in slot 12 vs 14).",
        ],
    )

    story.append(PageBreak())

    # 4. How slots are filled (RAG-Hybrid)
    story.append(Paragraph("4. How slots are filled (RAG-Hybrid)", S["h1"]))
    story.append(Paragraph(
        "Each of the 15 Brain slots is filled by a different code path. The table below summarises which path "
        "fills which slot.",
        S["body"]
    ))
    slot_table = [
        ["Slot", "Title (truncated)", "Filled by"],
        ["1", "Type", "label-row regex / narrative_inference._infer_type_from_prose"],
        ["2", "Policy Title", "label-row regex / header_extractor"],
        ["3", "Policy Number", "label-row regex"],
        ["4", "Effective Date", "label-row regex / header_extractor"],
        ["5", "Introduction", "heading-anchor + position fallback (guard: no_introduction_section)"],
        ["6", "Policy Statement", "heading-anchor (guard: no_policy_statement_section)"],
        ["7", "Applicable Sectors", "narrative_inference._infer_applicable_sectors"],
        ["8", "Scope & Beneficiaries", "heading-anchor (slot 8 synonyms)"],
        ["9", "Exclusions", "table passthrough / RAG (guard: no_exclusions_section)"],
        ["10", "Type of Benefits", "table passthrough (TABLE_SLOTS)"],
        ["11", "Approval & Review Note", "label-row regex"],
        ["12", "Definitions", "heading-anchor"],
        ["13", "Related Policies", "heading-anchor (guard: no_related_policies_section)"],
        ["14", "Document Control / History", "table passthrough (guard: no_version_history_markers)"],
        ["15", "(reserved)", "n/a"],
    ]
    t = Table(slot_table, colWidths=[0.4 * inch, 1.8 * inch, 4.3 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3d91")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f4")]),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "The 'guard' annotations on slots 5, 6, 9, 13, 14 mean: if the source PDF has no matching section "
        "marker (e.g. no 'Exclusions:' heading), the slot returns '<i>Data is not found in source file</i>' "
        "instead of fabricating content from semantically-similar but unrelated paragraphs.",
        S["body"]
    ))

    # 5. Final DOCX
    story.append(Paragraph("5. How the final DOCX is produced", S["h1"]))
    story.append(Paragraph(
        "Two renderer paths exist, chosen automatically by <font face='Courier'>api/publish_to_brain.publish_approved_version</font>:",
        S["body"]
    ))
    story.append(Paragraph(
        "<b>PRIMARY</b> - <font face='Courier'>lines_json_renderer.render_lines_json_to_brain</font>. "
        "This is used whenever the version was edited in the web UI, because it writes the reviewer's "
        "rich payload (HTML/JSON) directly into each Brain slot without re-running the RAG pipeline. "
        "Reviewer edits are preserved verbatim.",
        S["body"]
    ))
    story.append(Paragraph(
        "<b>FALLBACK</b> - <font face='Courier'>policy_platform.pipeline.run_from_lines_json</font>. "
        "Used when no reviewer edits exist; re-runs the full RAG-Hybrid pipeline against the saved "
        "lines_json. This is the deterministic path used by tests.",
        S["body"]
    ))
    story.append(Paragraph(
        "Both paths end with header/footer rewriting (<font face='Courier'>docx_approved_export._apply_header_footer</font>), "
        "footnote round-tripping (<font face='Courier'>docx_footnotes_writer.write_footnotes</font>), and a final "
        "post-render pass that strips black lines and applies visible table borders.",
        S["body"]
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "<b>End of document.</b> Generated by <font face='Courier'>scripts/generate_system_doc.py</font>.",
        S["body"]
    ))

    doc.build(story)
    print(f"Wrote: {OUT}")


if __name__ == "__main__":
    build()
