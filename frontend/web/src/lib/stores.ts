import { writable } from 'svelte/store';
import type { AppState, BatchEntry, RejectedFile, VersionEntry, AuditEntry } from './types';

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
