import { writable } from 'svelte/store';
import type { AppState, BatchEntry, RejectedFile, VersionEntry, AuditEntry } from './types';
import type { User } from './api';

export function createInitialAppState(): AppState {
  return {
    runId: null,
    files: [],
    rejected: [],
    filename: null,
    fromHistory: false,
    batch: [],
    batchIndex: 0,
    activeRunId: null,
    activeFilename: null,
    versions: [],
    currentVersionNo: null,
    reviewAudit: []
  };
}

export const appState = writable<AppState>(createInitialAppState());

// Separate writable for the current logged-in user. Kept outside
// `appState` because the auth lifecycle (login / logout / 401) is
// independent of the workflow app state.
export const currentUser = writable<User | null>(null);

export function resetAppState(): void {
  appState.set(createInitialAppState());
}

export function setFiles(files: File[]): void {
  appState.update((s) => ({ ...s, files }));
}

export function setRejected(rejected: RejectedFile[]): void {
  appState.update((s) => ({ ...s, rejected }));
}

export function setBatch(batch: BatchEntry[]): void {
  appState.update((s) => ({ ...s, batch }));
}

export function setActiveRun(runId: string | null, filename: string | null): void {
  appState.update((s) => ({
    ...s,
    activeRunId: runId,
    activeFilename: filename
  }));
}

export function setFromHistory(fromHistory: boolean): void {
  appState.update((s) => ({ ...s, fromHistory }));
}

// Stage 4 - workflow / version-control store helpers.

export function setVersions(versions: VersionEntry[]): void {
  appState.update((s) => ({ ...s, versions }));
}

export function setCurrentVersionNo(no: number | null): void {
  appState.update((s) => ({ ...s, currentVersionNo: no }));
}

export function setReviewAudit(audit: AuditEntry[]): void {
  appState.update((s) => ({ ...s, reviewAudit: audit }));
}

// Stage 2 - auth store helpers.

export function setCurrentUser(user: User | null): void {
  currentUser.set(user);
}

export function clearCurrentUser(): void {
  currentUser.set(null);
}

// Stage 4.4 — Flow 2 unread-shared-projects counter (header bell).
// Computed/polled by `NotificationBell.svelte` and shown in the
// header dropdown as `Notifications (N)`. `0` means "no unread items".
export const unreadSharedCount = writable<number>(0);

export function setUnreadSharedCount(n: number): void {
  unreadSharedCount.set(Math.max(0, n | 0));
}
