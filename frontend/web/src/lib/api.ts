import type {
  UploadResp,
  ProcessResp,
  StatusResp,
  PreviewData,
  HistoryEntry,
  VersionEntry,
  ReviewComment,
  AuditEntry,
  VersionsListResp,
  VersionGetResp,
  CommentsListResp,
  AuditListResp
} from './types';
import { authedFetch, getToken, setToken, clearToken } from './auth';

export const API_BASE = 'http://127.0.0.1:8000/api';

export interface User {
  id: number;
  username: string;
  is_admin: boolean;
  created_at: string;
}

export interface LoginResp {
  token: string;
  user: User;
  expires_at: string;
}

async function safeFetch(url: string, opts?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, opts);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new Error(
      `Cannot reach backend at ${url}. Is the API server running on port 8000? (${msg})`
    );
  }
}

// `apiFetch` is the authed replacement for `fetch` for all non-login
// calls. It auto-attaches the `Authorization: Bearer <token>` header
// and clears the token on 401. The legacy `safeFetch` (no auth) is
// still used for `/api/auth/login` since that endpoint creates the
// token in the first place.
async function apiFetch(url: string, opts?: RequestInit): Promise<Response> {
  return authedFetch(url, opts);
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export async function login(username: string, password: string): Promise<LoginResp> {
  const res = await safeFetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password })
  });
  if (!res.ok) {
    const text = await res.text();
    let detail = text;  
    try {
      const parsed = JSON.parse(text);
      detail = parsed.message || parsed.error || text;
    } catch { /* leave as text */ }
    throw new Error(detail || `Login failed (${res.status})`);
  }
  const data = (await res.json()) as LoginResp;
  setToken(data.token);
  return data;
}

export async function logout(): Promise<void> {
  const token = getToken();
  if (!token) return;
  try {
    await safeFetch(`${API_BASE}/auth/logout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token })
    });
  } catch {
    /* network errors are non-fatal for logout */
  } finally {
    clearToken();
  }
}

export async function getMe(): Promise<User | null> {
  if (!getToken()) return null;
  try {
    const res = await apiFetch(`${API_BASE}/auth/me`);
    if (!res.ok) {
      clearToken();
      return null;
    }
    const data = await res.json();
    return (data?.user ?? null) as User | null;
  } catch {
    return null;
  }
}

export async function listUsers(): Promise<User[]> {
  const res = await apiFetch(`${API_BASE}/auth/users`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`listUsers failed (${res.status}): ${text}`);
  }
  const data = (await res.json()) as { items: User[] };
  return data.items || [];
}

export async function uploadFile(file: File): Promise<UploadResp> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await apiFetch(`${API_BASE}/upload`, {
    method: 'POST',
    body: fd
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Upload failed (${res.status}): ${text}`);
  }
  return (await res.json()) as UploadResp;
}

export async function processRun(runId: string): Promise<ProcessResp> {
  const res = await apiFetch(`${API_BASE}/process/${runId}`, {
    method: 'POST'
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Process failed (${res.status}): ${text}`);
  }
  return (await res.json()) as ProcessResp;
}

export async function getStatus(runId: string): Promise<StatusResp> {
  const res = await apiFetch(`${API_BASE}/status/${runId}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Status failed (${res.status}): ${text}`);
  }
  return (await res.json()) as StatusResp;
}

export async function getResult(runId: string): Promise<unknown> {
  const res = await apiFetch(`${API_BASE}/result/${runId}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Result failed (${res.status}): ${text}`);
  }
  return await res.json();
}

export async function getPreview(runId: string): Promise<PreviewData> {
  const res = await apiFetch(`${API_BASE}/preview/${runId}`);
  if (!res.ok) {
    const text = await res.text();
    let parsed: { error?: string; message?: string; status?: string } | null = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      parsed = null;
    }
    if (parsed && (parsed.message || parsed.error)) {
      const detail = parsed.message || parsed.error || 'preview unavailable';
      throw new Error(detail, {
        cause: { status: res.status, ...parsed }
      });
    }
    throw new Error(`Preview failed (${res.status})`);
  }
  return (await res.json()) as PreviewData;
}

export async function getHistory(): Promise<HistoryEntry[]> {
  const res = await apiFetch(`${API_BASE}/history`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`History failed (${res.status}): ${text}`);
  }
  return (await res.json()) as HistoryEntry[];
}

export function triggerBlobDownload(blob: Blob, filename: string): void {
  const a = document.createElement('a');
  const objectUrl = URL.createObjectURL(blob);
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

export async function fetchDocxBlob(runId: string): Promise<Blob> {
  const url = `${API_BASE}/download/${runId}/docx`;
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  return await res.blob();
}

export async function fetchAllFilesBlob(runId: string, versionNo?: number | null): Promise<Blob> {
  // Scope the bundle to ONE version of the run — the version selected
  // in the editor's version dropdown (passed as `versionNo`). When
  // omitted, the backend falls back to the latest published version.
  const qs = versionNo != null ? `?version_no=${encodeURIComponent(String(versionNo))}` : '';
  const url = `${API_BASE}/download/${runId}/all${qs}`;
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  return await res.blob();
}

export interface AllFilesItem {
  runId: string;
  versionNo: number | null;
}

export async function fetchAllFilesZip(
  items: AllFilesItem[],
  mode: 'viewing' | 'published' = 'viewing'
): Promise<Blob> {
  // Multi-file "Download all files": one ZIP containing each file's
  // `.docx` (one per file in the Results dropdown). No source file, no
  // manifest — just the .docx files.
  //
  // mode='viewing' (default) — bundle each file's currently-viewing
  // version (legacy behavior; per-file `versionNo` is honored).
  //
  // mode='published' — bundle each run's LATEST PUBLISHED version and
  // SKIP any run that has no published version. Sends
  // `mode=published` + `versions=0,0,...` so the backend knows to skip
  // unpublished runs instead of erroring the whole batch.
  if (!items || items.length === 0 || !items[0]?.runId) {
    throw new Error('No files selected to download.');
  }
  const firstId = items[0].runId;
  // Validate the run_id is a non-empty hex string (the backend route
  // matches `^/api/download/([a-f0-9]+)/all$`). If the first id is
  // somehow not a valid run id, we'd hit a 404 — bail with a clear
  // error instead.
  if (!/^[a-f0-9]+$/i.test(firstId)) {
    throw new Error(
      `Invalid run id (${firstId!}). Try refreshing the page.`
    );
  }
  const ids = items.map((i) => i.runId).join(',');
  const versions = items.map((i) => i.versionNo ?? 0).join(',');
  const modeParam = mode === 'published' ? '&mode=published' : '';
  const url = `${API_BASE}/download/${firstId}/all?ids=${encodeURIComponent(ids)}&versions=${encodeURIComponent(versions)}${modeParam}`;
  const res = await apiFetch(url);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(
      `Download failed (${res.status})${text ? `: ${text.slice(0, 200)}` : ''}`
    );
  }
  return await res.blob();
}

export async function downloadDocx(
  runId: string,
  customFilename?: string,
  versionNo?: number | null
): Promise<void> {
  const qs = versionNo != null ? `?version_no=${encodeURIComponent(String(versionNo))}` : '';
  const url = `${API_BASE}/download/${runId}/docx${qs}`;
  try {
    const res = await apiFetch(url);
    if (!res.ok) throw new Error(`Download failed (${res.status})`);
    const blob = await res.blob();
    triggerBlobDownload(blob, customFilename || `${runId}.docx`);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    alert('Download failed: ' + msg);
  }
}

// Stage 3 - workflow / version-control read helpers.

export async function listVersions(runId: string): Promise<VersionEntry[]> {
  const res = await apiFetch(`${API_BASE}/versions/${runId}`);
  if (!res.ok) throw new Error(`listVersions failed (${res.status})`);
  const data = (await res.json()) as VersionsListResp;
  return data.items || [];
}

export async function getVersion(
  runId: string,
  versionNo: number
): Promise<VersionGetResp> {
  const res = await apiFetch(`${API_BASE}/versions/${runId}/${versionNo}`);
  if (!res.ok) throw new Error(`getVersion failed (${res.status})`);
  return (await res.json()) as VersionGetResp;
}

export async function listComments(
  runId: string,
  versionNo: number
): Promise<ReviewComment[]> {
  const res = await apiFetch(`${API_BASE}/versions/${runId}/${versionNo}/comments`);
  if (!res.ok) throw new Error(`listComments failed (${res.status})`);
  const data = (await res.json()) as CommentsListResp;
  return data.items || [];
}

export async function getAudit(runId: string): Promise<AuditEntry[]> {
  const res = await apiFetch(`${API_BASE}/versions/${runId}/audit`);
  if (!res.ok) throw new Error(`getAudit failed (${res.status})`);
  const data = (await res.json()) as AuditListResp;
  return data.items || [];
}

// Stage 4 - comment write helpers.

export interface AddCommentInput {
  body: string;
  anchor_kind?: 'slot' | 'paragraph' | 'general' | null;
  anchor_key?: string | null;
  author?: string;
}

export async function addComment(
  runId: string,
  versionNo: number,
  input: AddCommentInput
): Promise<ReviewComment> {
  const res = await apiFetch(`${API_BASE}/versions/${runId}/${versionNo}/comments`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input)
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`addComment failed (${res.status}): ${text}`);
  }
  return (await res.json()) as ReviewComment;
}

export async function resolveComment(
  runId: string,
  versionNo: number,
  commentId: number
): Promise<void> {
  const res = await apiFetch(
    `${API_BASE}/versions/${runId}/${versionNo}/comments/${commentId}/resolve`,
    { method: 'POST' }
  );
  if (!res.ok) throw new Error(`resolveComment failed (${res.status})`);
}

// Stage 5 - state-machine write helpers.

export interface SaveVersionInput {
  lines_json: string;
  change_summary: string;
  actor?: string;
}

export async function saveVersion(
  runId: string,
  input: SaveVersionInput
): Promise<VersionEntry> {
  const res = await apiFetch(`${API_BASE}/versions/${runId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input)
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`saveVersion failed (${res.status}): ${text}`);
  }
  return (await res.json()) as VersionEntry;
}

export async function submitForReview(
  runId: string,
  versionNo: number,
  actor?: string
): Promise<VersionEntry> {
  const res = await apiFetch(
    `${API_BASE}/versions/${runId}/${versionNo}/submit`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ actor: actor || 'user' })
    }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`submitForReview failed (${res.status}): ${text}`);
  }
  return (await res.json()) as VersionEntry;
}

export interface ReviewActionInput {
  action: 'approve' | 'reject';
  reviewer?: string;
  note?: string;
}

export async function reviewVersion(
  runId: string,
  versionNo: number,
  input: ReviewActionInput
): Promise<VersionEntry> {
  const res = await apiFetch(
    `${API_BASE}/versions/${runId}/${versionNo}/review`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input)
    }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`reviewVersion failed (${res.status}): ${text}`);
  }
  return (await res.json()) as VersionEntry;
}

export interface PublishVersionResp {
  version_no: number;
  review_status: string;
  docx_path: string | null;
  published_at: string | null;
}

export async function publishVersion(
  runId: string,
  versionNo: number,
  actor?: string
): Promise<PublishVersionResp> {
  const res = await apiFetch(
    `${API_BASE}/versions/${runId}/${versionNo}/publish`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ actor: actor || 'user' })
    }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`publishVersion failed (${res.status}): ${text}`);
  }
  return (await res.json()) as PublishVersionResp;
}

// -----------------------------------------------------------------------
// Stage 4.4 — Flow 1 / Flow 2 / Flow 3 backend bridge
// -----------------------------------------------------------------------

export type AccessLevel = 'viewer' | 'editor' | 'approver';

export interface ProjectMember {
  run_id: string;
  user_id: number;
  username: string;
  access_level: AccessLevel;
  added_by_user_id: number;
  added_at: string;
}

export interface ProjectSharingResp {
  project: { run_id: string; filename?: string };
  items: ProjectMember[];
  your_access: AccessLevel | null;
}

export async function listProjectMembers(runId: string): Promise<ProjectSharingResp> {
  const res = await apiFetch(`${API_BASE}/runs/${runId}/members`);
  if (!res.ok) throw new Error(`listProjectMembers failed (${res.status})`);
  return (await res.json()) as ProjectSharingResp;
}

export async function addProjectMember(
  runId: string,
  userId: number,
  accessLevel: AccessLevel
): Promise<ProjectMember> {
  const res = await apiFetch(`${API_BASE}/runs/${runId}/members`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, access_level: accessLevel })
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`addProjectMember failed (${res.status}): ${text}`);
  }
  return (await res.json()) as ProjectMember;
}

export async function removeProjectMember(
  runId: string,
  userId: number
): Promise<void> {
  const res = await apiFetch(`${API_BASE}/runs/${runId}/members/${userId}`, {
    method: 'DELETE'
  });
  if (!res.ok) throw new Error(`removeProjectMember failed (${res.status})`);
}

export async function markProjectSeen(runId: string): Promise<void> {
  const res = await apiFetch(`${API_BASE}/runs/${runId}/seen`, {
    method: 'POST'
  });
  if (!res.ok) throw new Error(`markProjectSeen failed (${res.status})`);
}

export interface ShareableUser {
  id: number;
  username: string;
}

export async function listShareableUsers(): Promise<ShareableUser[]> {
  const res = await apiFetch(`${API_BASE}/auth/shareable-users`);
  if (!res.ok) throw new Error(`listShareableUsers failed (${res.status})`);
  const data = (await res.json()) as { items: ShareableUser[] };
  return data.items || [];
}

export interface ReviewerQueueItem {
  run_id: string;
  filename: string;
  version_no: number;
  submitted_at: string;
  submitted_by: string;
  assigned_reviewer_user_id: number | null;
  your_access: AccessLevel | null;
  is_unread: boolean;
}

export async function getReviewerQueue(): Promise<ReviewerQueueItem[]> {
  const res = await apiFetch(`${API_BASE}/reviewer/queue`);
  if (!res.ok) throw new Error(`getReviewerQueue failed (${res.status})`);
  const data = (await res.json()) as { items: ReviewerQueueItem[] };
  return data.items || [];
}

// Stage 4.10 — Flow 2 share-notification feed ("you've been added
// to <project>"). Distinct from the reviewer queue above: this
// endpoint reports membership changes (Flow 1 events) whereas the
// queue reports review-task changes (Stage 5/6 events).
export interface SharedProjectItem {
  run_id: string;
  filename: string;
  created_at: string;
  status: string;
  your_access: AccessLevel;
  shared_by: string | null;
  added_at: string | null;
  last_seen_at: string | null;
  is_unread: boolean;
}

export async function getMySharedProjects(): Promise<SharedProjectItem[]> {
  const res = await apiFetch(`${API_BASE}/auth/shared-projects`);
  if (!res.ok) throw new Error(`getMySharedProjects failed (${res.status})`);
  const data = (await res.json()) as { items: SharedProjectItem[] };
  return data.items || [];
}

export async function markAllProjectsSeen(): Promise<{ ok: boolean; updated: number }> {
  const res = await apiFetch(`${API_BASE}/auth/mark-all-projects-seen`, {
    method: 'POST'
  });
  if (!res.ok) throw new Error(`markAllProjectsSeen failed (${res.status})`);
  return (await res.json()) as { ok: boolean; updated: number };
}

export async function dismissAllNotifications(): Promise<{ ok: boolean; dismissed: number }> {
  const res = await apiFetch(`${API_BASE}/auth/dismiss-all-notifications`, {
    method: 'POST'
  });
  if (!res.ok) throw new Error(`dismissAllNotifications failed (${res.status})`);
  return (await res.json()) as { ok: boolean; dismissed: number };
}

export async function assignReviewer(
  runId: string,
  versionNo: number,
  userId: number
): Promise<{ assigned_reviewer_user_id: number; assigned_reviewer: string }> {
  const res = await apiFetch(
    `${API_BASE}/versions/${runId}/${versionNo}/assign`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId })
    }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`assignReviewer failed (${res.status}): ${text}`);
  }
  return (await res.json());
}

// -----------------------------------------------------------------------
// Stage 4.11 — Autosave-draft bridge
// -----------------------------------------------------------------------

export interface DraftRow {
  run_id: string;
  draft_id: number;
  lines_json: string;
  last_edit_id: string | null;
  edit_count: number;
  modified_by: string;
  actor_user_id: number | null;
  modified_at: string;
}

export interface SaveDraftResp {
  edit_id: string;
  edit_count: number;
  saved_at: string;
  modified_by: string;
  draft_version_no: number;
}

export async function getDraft(runId: string): Promise<DraftRow | null> {
  const res = await apiFetch(`${API_BASE}/runs/${runId}/draft`);
  if (!res.ok) {
    if (res.status === 404) return null;
    throw new Error(`getDraft failed (${res.status})`);
  }
  const data = (await res.json()) as { draft: DraftRow | null };
  return data.draft || null;
}

export async function saveDraft(
  runId: string,
  linesJson: string,
  clientEditId: string
): Promise<SaveDraftResp> {
  const res = await apiFetch(`${API_BASE}/runs/${runId}/draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      lines_json: linesJson,
      client_edit_id: clientEditId
    })
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`saveDraft failed (${res.status}): ${text}`);
  }
  return (await res.json()) as SaveDraftResp;
}

/** Stage 4.11 — submit the autosave buffer as the next version.
 *  Calls the existing `/api/versions/<id>/<n>/submit` route with
 *  `from_draft=true`; the server snapshots the draft into a new
 *  frozen row and transitions it to in_review in one atomic step.
 *  `version_no` is informational (server picks the next one).
 */
export async function submitDraft(
  runId: string,
  versionNo: number | null,
  draftEditCount?: number | null
): Promise<VersionEntry> {
  // Stage 4.12 — "Submit Only the Currently Viewed Version". If a
  // `draftEditCount` is provided, the server consumes ONLY that draft
  // row (other drafting rows remain as orphan history). Otherwise the
  // server picks the latest drafting row.
  const body: Record<string, unknown> = { from_draft: true };
  if (draftEditCount != null) {
    body.draft_edit_count = draftEditCount;
  }
  const res = await apiFetch(
    `${API_BASE}/versions/${runId}/${versionNo ?? 0}/submit`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`submitDraft failed (${res.status}): ${text}`);
  }
  return (await res.json()) as VersionEntry;
}
