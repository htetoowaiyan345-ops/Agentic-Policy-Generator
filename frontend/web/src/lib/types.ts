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

export type PreviewLine = ['p', string] | ['t', string[][]];

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
