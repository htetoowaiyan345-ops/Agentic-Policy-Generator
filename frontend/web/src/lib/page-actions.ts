import { appState, setActiveRun, setFromHistory } from './stores';
import { getHistory, downloadDocx } from './api';
import { get } from 'svelte/store';
import type { PreviewData, HistoryEntry, AppState } from './types';

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
  // `<ReviewEditor>` that now lives there — the user would click
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

  const dlBtn = document.getElementById('download-btn');
  if (dlBtn) {
    const current = get(appState);
    const name = current.activeFilename || 'Policy.docx';
    const stem = name.replace(/\.[^/.]+$/, '');
    dlBtn.textContent = `Download ${stem}.docx`;
  }
}
