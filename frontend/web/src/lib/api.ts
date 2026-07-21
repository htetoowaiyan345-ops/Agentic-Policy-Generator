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
    throw new Error(`Preview failed (${res.status}): ${text}`);
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

export async function fetchAllFilesZip(items: AllFilesItem[]): Promise<Blob> {
  // Multi-file "Download all files": one ZIP containing each file's
  // CURRENT view version's `.docx` (one per file in the Results
  // dropdown). No source file, no manifest — just the .docx files.
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
  const url = `${API_BASE}/download/${firstId}/all?ids=${encodeURIComponent(ids)}&versions=${encodeURIComponent(versions)}`;
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
