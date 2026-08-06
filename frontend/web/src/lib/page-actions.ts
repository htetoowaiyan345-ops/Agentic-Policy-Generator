import { appState, setActiveRun, setFromHistory } from './stores';
import { getHistory, downloadDocx } from './api';
import { get } from 'svelte/store';
import type { PreviewData, HistoryEntry, AppState } from './types';

let _reloadActiveRun: (() => Promise<void>) | null = null;

export function setReloadHandler(handler: (() => Promise<void>) | null): void {
  _reloadActiveRun = handler;
}

let _navigateToReview: ((runId: string) => void) | null = null;
let _onStep3: (() => boolean) | null = null;
let _closeHistory: (() => void) | null = null;

/** Register a navigation handler so `loadResultAndShow` can switch the
 *  user to Step 3 (Review) before loading the result. The parent
 *  `+page.svelte` registers its `onSelectRun` here so this module
 *  doesn't have to know about routing directly. */
export function setNavigateHandler(handler: ((runId: string) => void) | null): void {
  _navigateToReview = handler;
}

/** Register a "are we already on Step 3?" probe so `loadResultAndShow`
 *  can avoid unnecessary re-navigation when the user clicks "Load
 *  result" while already on the Review step. */
export function setOnStep3Probe(probe: (() => boolean) | null): void {
  _onStep3 = probe;
}

/** Register a History-panel closer so `loadResultAndShow` can collapse
 *  the History overlay before/after the result loads. Without this,
 *  users on Step 3 who click "Load result" while History is open would
 *  still see the panel blocking the Review screen. */
export function setCloseHistoryHandler(handler: (() => void) | null): void {
  _closeHistory = handler;
}

export async function loadResultAndShow(runId: string): Promise<void> {
  const sel = document.getElementById('results-picker') as HTMLSelectElement | null;
  const wrap = document.getElementById('results-picker-wrap');
  if (sel) sel.innerHTML = '';
  if (wrap) wrap.classList.add('hidden');

  const dlAllBtn = document.getElementById('download-all-btn') as HTMLButtonElement | null;
  if (dlAllBtn) {
    dlAllBtn.classList.remove('hidden');
    dlAllBtn.textContent = 'Download all files';
    dlAllBtn.disabled = false;
  }
  const dlAllDone = document.getElementById('download-all-done');
  if (dlAllDone) dlAllDone.classList.add('hidden');

  // Trigger the preview load via Review.svelte's reactive $effect.
  // The effect watches `activeRunId` (line ~798 of Review.svelte) and
  // calls `loadPreview(runId)` which sets `previewData`, pushes
  // `editableLines` into the editor via `applyExternalContent`, and
  // re-renders the editor with the loaded content.
  //
  // Previously this function ALSO called `getPreview(runId)` directly
  // + `renderSlots(data)` which wrote raw `<p>` / `<table>` elements
  // into `#slots-container`. That corrupted the Svelte-managed
  // `<ReviewEditor>` that now lives there â€” the user would click
  // "Load result" and see nothing change. Removed: the reactive
  // $effect handles the preview load correctly.
  setActiveRun(runId, null);

  let match: HistoryEntry | null = null;
  try {
    const hist = await getHistory();
    match = (hist || []).find((h) => h.run_id === runId) || null;
  } catch {
    match = null;
  }
  if (match) {
    setActiveRun(runId, match.filename);
  }

  if (sel && wrap && match) {
    wrap.classList.remove('hidden');
    const opt = document.createElement('option');
    opt.value = runId;
    opt.textContent = match.filename;
    sel.appendChild(opt);
    sel.value = runId;
  }

  // Collapse the History panel so the loaded result is visible. Safe
  // to call when the panel is already closed (idempotent). Required
  // for the same-step path (user on Step 3 with History open) since
  // the nav handler below is skipped in that case.
  if (_closeHistory) {
    _closeHistory();
  }

  // If the user is on Step 3 (Review is mounted), don't navigate â€”
  // the $effect should fire because `activeRunId` changed. If for
  // some reason it doesn't (e.g. same-runId click), the registered
  // reload handler still re-runs `loadPreview` directly below.
  //
  // Otherwise (user is on Step 1 or Step 2), defer to the nav
  // handler so `+page.svelte` mounts Review, the $effect sees
  // `lastLoadedRunId === null` on the fresh mount, and the result
  // loads automatically.
  if (_navigateToReview && _onStep3 && !_onStep3()) {
    _navigateToReview(runId);
    return;
  }

  // Force-reload via the registered Review.svelte handler. Bypasses
  // the `$effect` gate `runId !== lastLoadedRunId` so clicking
  // "Load result" on the already-loaded run still re-fetches the
  // preview and re-applies it to the editor.
  if (_reloadActiveRun) {
    await _reloadActiveRun();
  }

  const dlBtn = document.getElementById('download-btn');
  if (dlBtn) {
    const current = get(appState);
    const name = current.activeFilename || 'Policy.docx';
    const stem = name.replace(/\.[^/.]+$/, '');
    dlBtn.textContent = `Download ${stem}.docx`;
  }
}
