export type StepId = 1 | 2 | 3;

export type BatchStatus =
  | 'queued'
  | 'processing'
  | 'done'
  | 'failed'
  | 'skipped';

export interface RejectedFile {
  name: string;
  size: number;
  reason: string;
}

export interface BatchEntry {
  file: File;
  name: string;
  runId: string | null;
  status: BatchStatus;
  sections_filled: number;
  markers_count: number;
  reason: string;
}

export interface AppState {
  runId: string | null;
  files: File[];
  rejected: RejectedFile[];
  filename: string | null;
  fromHistory: boolean;
  batch: BatchEntry[];
  batchIndex: number;
  activeRunId: string | null;
  activeFilename: string | null;
  // Stage 4 - workflow / version-control state.
  versions: VersionEntry[];
  currentVersionNo: number | null;
  reviewAudit: AuditEntry[];
}

/**
 * Brain slot identifier.
 *   - 0 = free paragraph (content outside any Brain slot; appears at top of docx)
 *   - 1..15 = the 15 Brain slots (see framework/section_map.py)
 */
export type SlotKind =
  | 0
  | 1
  | 2
  | 3
  | 4
  | 5
  | 6
  | 7
  | 8
  | 9
  | 10
  | 11
  | 12
  | 13
  | 14
  | 15;

export interface RichParagraph {
  /** Brain slot id; 0 means free paragraph (above all slots). */
  slot: SlotKind;
  /** Plain-text fallback (used by audit log, table cells, search). */
  text: string;
  /** CKEditor 5 serialized HTML; round-tripped to .docx in publish. */
  html: string;
  /** Optional footnotes anchored to this paragraph. */
  footnotes?: { id: string; body: string }[];
}

/** CKEditor 5 table-cell payload: text + html per cell. */
export interface RichCell {
  text: string;
  html: string;
}

/**
 * Row payload for a Brain-slot table line.
 * Each cell carries both text (audit-safe) and html (CKEditor 5 output).
 */
export type RichTable = RichCell[][];

export type PreviewLine =
  | ['p', RichParagraph]
  | ['t', { slot: SlotKind; rows: RichTable }]
  | ['divider', { slot: SlotKind }];

/**
 * Normalise historical payload shape (legacy `['p', string]` or
 * `['t', string[][]]`) into the rich shape. Used by:
 *   - api_preview.py on read
 *   - docx_approved_export.py on export
 *   - frontend on initial load before mounting the editor
 */
export function normalisePreviewLine(raw: unknown): PreviewLine | null {
  if (!Array.isArray(raw) || raw.length !== 2) return null;
  const [kind, payload] = raw as [unknown, unknown];
  if (kind === 'p') {
    if (typeof payload === 'string') {
      return ['p', { slot: 0, text: payload, html: '' }];
    }
    if (payload && typeof payload === 'object' && 'text' in payload) {
      const p = payload as Partial<RichParagraph>;
      return [
        'p',
        {
          slot: (p.slot ?? 0) as SlotKind,
          text: p.text ?? '',
          html: p.html ?? '',
          footnotes: p.footnotes ?? []
        }
      ];
    }
    return null;
  }
  if (kind === 't') {
    if (Array.isArray(payload)) {
      const rows = payload as unknown[];
      const normRows: RichTable = rows.map((row) => {
        if (!Array.isArray(row)) return [];
        return row.map((cell) =>
          typeof cell === 'string' ? { text: cell, html: '' } : (cell as RichCell)
        );
      });
      return ['t', { slot: 0, rows: normRows }];
    }
    if (payload && typeof payload === 'object' && 'rows' in payload) {
      const p = payload as { slot?: SlotKind; rows?: RichCell[][] };
      return ['t', { slot: (p.slot ?? 0) as SlotKind, rows: p.rows ?? [] }];
    }
    return null;
  }
  if (kind === 'divider') {
    if (payload && typeof payload === 'object' && 'slot' in payload) {
      const p = payload as { slot?: SlotKind };
      return ['divider', { slot: (p.slot ?? 0) as SlotKind }];
    }
    return ['divider', { slot: 0 }];
  }
  return null;
}

export interface PreviewData {
  lines: PreviewLine[];
}

export interface HistoryEntry {
  run_id: string;
  filename: string;
  created_at: string;
  status: string;
  sections_filled: number;
  markers_count: number;
}

export interface UploadResp {
  run_id: string;
}

export interface ProcessResp {
  ok: boolean;
}

export interface StatusResp {
  state: 'queued' | 'processing' | 'done' | 'failed';
  sections_filled?: number;
  markers_count?: number;
  message?: string;
}

export interface ResultResp {
  ok: boolean;
  filename?: string;
  download_url?: string;
  [k: string]: unknown;
}

// Stage 3 - workflow / version-control types.

export type ReviewStatus =
  | 'draft'
  | 'in_review'
  | 'approved'
  | 'rejected'
  | 'published';

export interface VersionEntry {
  version_id: number;
  run_id: string;
  version_no: number;
  change_summary: string | null;
  modified_by: string;
  modified_at: string;
  review_status: ReviewStatus;
  reviewer: string | null;
  review_note: string | null;
  reviewed_at: string | null;
  published_at: string | null;
  source: 'pipeline' | 'user_edit' | 'restore' | string;
  lines_json?: PreviewLine[];
  /** Stage 4.12 — 'frozen' for policy_versions rows, 'draft' for
   *  policy_drafts rows returned by /api/versions/<id>. */
  kind?: 'frozen' | 'draft';
  /** Stage 4.12 — only present when kind === 'draft'. */
  draft_id?: number;
  /** Stage 4.12 — only present when kind === 'draft'. */
  edit_count?: number;
}

export interface ReviewComment {
  comment_id: number;
  run_id: string;
  version_no: number;
  anchor_kind: 'slot' | 'paragraph' | 'general' | null;
  anchor_key: string | null;
  body: string;
  author: string;
  created_at: string;
  resolved: 0 | 1;
}

export interface AuditEntry {
  audit_id: number;
  run_id: string;
  version_no: number | null;
  event_type: string;
  actor: string;
  details: string | null;
  created_at: string;
}

export interface VersionsListResp {
  items: VersionEntry[];
}

export interface VersionGetResp extends VersionEntry {
  lines_json: PreviewLine[];
}

export interface CommentsListResp {
  items: ReviewComment[];
}

export interface AuditListResp {
  items: AuditEntry[];
}
