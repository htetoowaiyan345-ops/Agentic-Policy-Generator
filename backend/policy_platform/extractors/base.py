"""Common extraction data structures."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractedDocument:
    """Verbatim extracted content. NO TEXT IS MUTATED.

    `paragraphs` and `tables` preserve the original strings exactly as read.
    `full_text` is the concatenation, used only as a checksum reference —
    it is byte-equal to the source concatenation normalized by line breaks
    only where the source truly contained them.

    `cleaner_dropped` records any lines removed by the noise-cleaner
    between extraction and analysis. Each entry is
    {index, text, reason} where reason is one of:
    "page_number" | "header_repeat" | "garbled".
    """
    paragraphs: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    source_sha256: str = ""
    source_format: str = ""
    cleaner_dropped: list[dict] = field(default_factory=list)
    # Detected language of the source. One of: "en", "my", "mixed", or "".
    # Set by the extractor (pdf_extractor, docx_extractor, etc.) based on
    # character-class detection of the extracted paragraphs.
    source_lang: str = ""
    # Per-paragraph language tag, parallel to `paragraphs`. Empty string
    # when not computed. Reserved for future use; not populated yet.
    paragraph_languages: list[str] = field(default_factory=list)
    # Parallel list to `paragraphs` (post-cleaner). Each entry is the
    # index in the original (pre-cleaner) paragraph stream that the
    # cleaned paragraph came from. Used by parsers to recover
    # cleaner-dropped values via cleaner_dropped records.
    original_indices: list[int] = field(default_factory=list)
    # For each table in `tables`, the index in `paragraphs` AFTER which
    # the table appeared in the source document. Used by the analyzer
    # to map source tables to Brain slots based on the closest preceding
    # section heading. If `table_paragraph_indices[i] = N`, the i-th
    # table appeared in the source between paragraphs[N-1] and
    # paragraphs[N] (or before paragraph 0 if N=0).
    table_paragraph_indices: list[int] = field(default_factory=list)
    # For each paragraph in `paragraphs`, the index of the table it
    # came from (if any). If `paragraph_table_origin[i] = T`, then
    # paragraphs[i] was emitted from table T's cell text. If None,
    # the paragraph is a regular document paragraph (not from a table).
    # Used by the analyzer to exclude cell-text paragraphs from slot
    # content_paragraphs when the corresponding table is already
    # routed to that slot (prevents duplication).
    paragraph_table_origin: list[int | None] = field(default_factory=list)
    # Per-paragraph bbox parallel to `paragraphs`. Each entry is
    # `(page, x0, top, x1, bottom)` in PDF point coordinates (1pt = 1/72
    # inch). PDF extractors populate this for every paragraph. None
    # entries mean the source had no coordinates for the line (e.g. a
    # synthetic continuation line). Empty list = the extractor didn't
    # produce coordinates at all (DOCX, RTF, TXT). Downstream code
    # should check `len(line_bboxes) == len(paragraphs)` before using.
    line_bboxes: list[tuple[int, float, float, float, float] | None] = field(
        default_factory=list
    )
    # Page rotations in degrees as detected per-page during extraction.
    # Each entry is `(page_number, rotation_degrees)` where rotation is
    # one of 0, 90, 180, 270. Empty list if the source had no rotation
    # (DOCX, RTF, TXT, or PDFs all at 0°).
    page_rotations: list[tuple[int, int]] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        parts: list[str] = []
        for p in self.paragraphs:
            parts.append(p)
        for table in self.tables:
            for row in table:
                for cell in row:
                    parts.append(cell)
        return "\n".join(parts)
