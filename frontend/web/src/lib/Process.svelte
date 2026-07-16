<script lang="ts">
  import { onDestroy } from 'svelte';
  import { appState, setBatch, setActiveRun, setFromHistory } from './stores';
  import { uploadFile, processRun, getStatus, getPreview } from './api';
  import { escapeHtml } from './escape';
  import { renderSlots } from './page-actions';
  import type { BatchEntry, BatchStatus } from './types';

  const STUCK_TIMEOUT_MS = 60000;
  const DEFAULT_FILE_MS = 30000;
  const MIN_DIVISOR_MS = 8000;
  const MAX_DIVISOR_MS = 120000;

  interface Props {
    onReview: () => void;
  }
  let { onReview }: Props = $props();

  let files = $derived($appState.files);
  let batch = $derived($appState.batch);
  let batchIndex = $derived($appState.batchIndex);
  let activeRunId = $derived($appState.activeRunId);

  let genBtn: HTMLButtonElement | null = $state(null);
  let badge: HTMLDivElement | null = $state(null);
  let procBox: HTMLDivElement | null = $state(null);
  let doneBox: HTMLDivElement | null = $state(null);
  let failedBox: HTMLDivElement | null = $state(null);
  let countWrap: HTMLSpanElement | null = $state(null);
  let countEl: HTMLSpanElement | null = $state(null);
  let chipList: HTMLUListElement | null = $state(null);
  let chipListDone: HTMLUListElement | null = $state(null);
  let chipListFailed: HTMLUListElement | null = $state(null);
  let progressBar: HTMLDivElement | null = $state(null);
  let statusLine: HTMLDivElement | null = $state(null);
  let filenameEl: HTMLDivElement | null = $state(null);
  let elapsedEl: HTMLSpanElement | null = $state(null);
  let nextBtn: HTMLButtonElement | null = $state(null);

  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let stuckTimer: ReturnType<typeof setInterval> | null = null;
  let currentRunStart = 0;
  let currentRunLastProgress = 0;
  let currentFileDurationMs: number | null = null;
  let currentBarPct = 0;
  let skipRequested = false;
  let batchActive = false;
  let isRegenerate = false;
  let elapsedTimer: ReturnType<typeof setInterval> | null = null;

  function clearTimers(): void {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (stuckTimer) { clearInterval(stuckTimer); stuckTimer = null; }
    if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
  }

  function resetBatchTimers(): void {
    currentRunStart = Date.now();
    currentRunLastProgress = Date.now();
    skipRequested = false;
    if (stuckTimer) clearInterval(stuckTimer);
    stuckTimer = setInterval(() => {
      if (!batchActive) return;
      if (skipRequested) return;
      if (Date.now() - currentRunLastProgress >= STUCK_TIMEOUT_MS) {
        const b = batch[batchIndex];
        if (b) {
          b.status = 'failed';
          b.reason = 'timeout (60s with no progress)';
          renderChips();
          advanceBatch('timeout');
        }
      }
    }, 1000);
  }

  function renderChips(): void {
    if (chipList) chipList.innerHTML = '';
    if (chipListDone) chipListDone.innerHTML = '';
    if (chipListFailed) chipListFailed.innerHTML = '';

    batch.forEach((entry, i) => {
      const li = document.createElement('li');
      li.className = 'flex items-center gap-3 text-[12px] font-mono';
      let icon = '·', color = 'rgba(17,17,17,0.40)';
      if (entry.status === 'queued') { icon = '·'; color = 'rgba(17,17,17,0.40)'; }
      if (entry.status === 'processing') { icon = '⟳'; color = 'var(--link-blue)'; }
      if (entry.status === 'done') { icon = '✓'; color = '#0a7a2a'; }
      if (entry.status === 'failed') { icon = '✗'; color = 'var(--accent)'; }
      if (entry.status === 'skipped') { icon = '⊘'; color = 'var(--accent)'; }
      const label =
        entry.status === 'processing' ? 'PROCESSING' :
        entry.status === 'done' ? 'DONE' :
        entry.status === 'failed' ? 'FAILED' :
        entry.status === 'skipped' ? 'SKIPPED' :
        'QUEUED';
      const detail = entry.reason ? ` — ${escapeHtml(entry.reason)}` : '';
      const idx = String(i + 1).padStart(2, '0');
      li.innerHTML =
        `<span style="color:${color}; width:14px; text-align:center;">${icon}</span>` +
        `<span class="text-[rgba(17,17,17,0.58)]">${idx}</span>` +
        `<span class="flex-1 truncate" title="${escapeHtml(entry.name)}">${escapeHtml(entry.name)}</span>` +
        `<span style="color:${color};">${label}</span>${detail}`;
      if (chipList) chipList.appendChild(li);
    });
  }

  async function startGenerate(): Promise<void> {
    if (files.length === 0) return;

    const regen = isRegenerate;
    isRegenerate = false;

    let newBatch: BatchEntry[];
    let startIndex: number;
    if (regen) {
      newBatch = batch.map((b) => ({
        ...b,
        runId: null,
        status: 'queued' as BatchStatus,
        sections_filled: 0,
        markers_count: 0,
        reason: ''
      }));
      startIndex = 0;
    } else {
      const keepEntries: BatchEntry[] = batch.filter(
        (b) => b.status === 'done' || b.status === 'processing' || b.status === 'failed' || b.status === 'skipped'
      );
      const addedEntries: BatchEntry[] = files.map((f) => ({
        file: f,
        name: f.name,
        runId: null,
        status: 'queued' as BatchStatus,
        sections_filled: 0,
        markers_count: 0,
        reason: ''
      }));
      newBatch = [...keepEntries, ...addedEntries];
      startIndex = keepEntries.length;
    }
    setBatch(newBatch);
    appState.update((s) => ({ ...s, batchIndex: startIndex, activeRunId: null }));
    setFromHistory(false);

    if (files.length > 1) {
      if (countWrap) countWrap.classList.remove('hidden');
      if (countEl) countEl.textContent = String(files.length);
    } else {
      if (countWrap) countWrap.classList.add('hidden');
    }

    if (filenameEl) {
      filenameEl.textContent =
        files.length === 1 ? files[0].name : `${files.length} files (sequential)`;
    }

    if (files.length === 1 && chipList) chipList.innerHTML = '';

    if (genBtn) { genBtn.disabled = true; genBtn.textContent = 'Generating…'; }
    if (badge) badge.textContent = 'PROCESSING';
    if (procBox) procBox.classList.remove('hidden');
    if (doneBox) doneBox.classList.add('hidden');
    if (failedBox) failedBox.classList.add('hidden');

    renderChips();
    batchActive = true;
    await processNextInBatch();
  }

  async function processNextInBatch(): Promise<void> {
    const current = $appState;
    if (current.batchIndex >= current.batch.length) {
      finishBatch();
      return;
    }
    const entry = current.batch[current.batchIndex];
    entry.status = 'processing';
    setActiveRun(null, null);
    renderChips();

    if (progressBar) progressBar.style.width = '0%';
    currentBarPct = 0;
    currentRunStart = Date.now();

    const total = current.batch.length;
    if (statusLine) {
      if (total === 1) {
        statusLine.innerHTML = `PROCESSING — <span id="elapsed">0</span>s`;
      } else {
        statusLine.innerHTML = `PROCESSING ${current.batchIndex + 1} OF ${total} — <span class="text-[#111111]">${escapeHtml(entry.name)}</span> — <span id="elapsed">0</span>s`;
      }
      elapsedEl = document.getElementById('elapsed') as HTMLSpanElement | null;
    }

    resetBatchTimers();

    try {
      const r = await uploadFile(entry.file);
      entry.runId = r.run_id;
      setActiveRun(r.run_id, null);
    } catch (e) {
      entry.status = 'failed';
      entry.reason = 'upload: ' + (e instanceof Error ? e.message : String(e));
      renderChips();
      advanceBatch('upload-failed');
      return;
    }

    try {
      await processRun(entry.runId!);
    } catch (e) {
      entry.status = 'failed';
      entry.reason = 'process: ' + (e instanceof Error ? e.message : String(e));
      renderChips();
      advanceBatch('process-failed');
      return;
    }

    startPolling();
  }

  function startPolling(): void {
    if (pollTimer) clearInterval(pollTimer);
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = setInterval(() => {
      const e = document.getElementById('elapsed');
      if (e) e.textContent = String(Math.floor((Date.now() - currentRunStart) / 1000));
    }, 250);
    pollTimer = setInterval(async () => {
      const current = $appState;
      if (current.batchIndex >= current.batch.length) {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        return;
      }
      const entry = current.batch[current.batchIndex];
      const elapsedMs = Date.now() - currentRunStart;

      const divisorMs = currentFileDurationMs
        ? Math.max(MIN_DIVISOR_MS, Math.min(currentFileDurationMs, MAX_DIVISOR_MS))
        : DEFAULT_FILE_MS;
      const fakeProgress = Math.min(100, (elapsedMs / divisorMs) * 100);
      currentBarPct = Math.max(currentBarPct, fakeProgress);
      if (progressBar) progressBar.style.width = currentBarPct + '%';

      try {
        const s = await getStatus(entry.runId!);
        if (s && (typeof s.sections_filled === 'number' || s.state === 'done')) {
          currentRunLastProgress = Date.now();
        }
        if (skipRequested) {
          entry.status = 'skipped';
          entry.reason = 'user skipped';
          renderChips();
          advanceBatch('user-skip');
          return;
        }
        if (s.state === 'done') {
          if (progressBar) progressBar.style.width = '100%';
          currentBarPct = 100;
          currentFileDurationMs = Date.now() - currentRunStart;
          entry.status = 'done';
          entry.sections_filled = s.sections_filled || 0;
          entry.markers_count = s.markers_count || 0;
          renderChips();
          advanceBatch('done');
        } else if (s.state === 'failed') {
          entry.status = 'failed';
          entry.reason = s.message || 'pipeline failed';
          renderChips();
          advanceBatch('pipeline-failed');
        }
      } catch (e) {
        console.error(e);
      }
    }, 1000);
  }

  function skipCurrentFile(): void {
    skipRequested = true;
    if (progressBar && currentBarPct > 0) {
      progressBar.style.width = currentBarPct + '%';
    }
  }

  function advanceBatch(_reason: string): void {
    clearTimers();
    appState.update((s) => ({ ...s, batchIndex: s.batchIndex + 1 }));
    setTimeout(() => processNextInBatch(), 250);
  }

  function finishBatch(): void {
    clearTimers();
    batchActive = false;
    const current = $appState;
    const doneCount = current.batch.filter((b) => b.status === 'done').length;
    const failedCount = current.batch.filter((b) => b.status === 'failed' || b.status === 'skipped').length;
    if (procBox) procBox.classList.add('hidden');
    if (progressBar) progressBar.style.width = '100%';

    const total = current.batch.length;
    if (doneCount === total) {
      if (doneBox) doneBox.classList.remove('hidden');
      if (badge) badge.textContent = 'DONE';
      const tsEl = document.getElementById('done-timestamp');
      if (tsEl) tsEl.textContent = `ALL ${total} COMPLETE — ${new Date().toLocaleString()}`;
      const firstDone = current.batch.find((b) => b.status === 'done');
      if (firstDone) {
        const ridEl = document.getElementById('done-runid');
        if (ridEl) ridEl.textContent = firstDone.runId || '—';
        const mkEl = document.getElementById('done-markers');
        if (mkEl) mkEl.textContent = String(firstDone.markers_count || 0);
        const secEl = document.getElementById('done-sections');
        if (secEl) secEl.textContent = `${firstDone.sections_filled || 0} / 15`;
      }
      if (genBtn) { genBtn.disabled = false; genBtn.textContent = 'Regenerate'; isRegenerate = true; }
      if (nextBtn) { nextBtn.disabled = false; nextBtn.textContent = 'Review →'; }
    } else if (doneCount === 0) {
      if (failedBox) failedBox.classList.remove('hidden');
      if (badge) badge.textContent = 'FAILED';
      const fEl = document.getElementById('failed-msg');
      if (fEl) fEl.textContent = `All ${total} files failed.`;
      if (genBtn) { genBtn.disabled = false; genBtn.textContent = 'Regenerate'; isRegenerate = true; }
      if (nextBtn) nextBtn.disabled = true;
    } else {
      if (doneBox) doneBox.classList.remove('hidden');
      if (badge) badge.textContent = 'DONE';
      const tsEl = document.getElementById('done-timestamp');
      if (tsEl) tsEl.textContent = `${doneCount} OF ${total} COMPLETE — ${new Date().toLocaleString()}`;
      const firstDone = current.batch.find((b) => b.status === 'done');
      if (firstDone) {
        const ridEl = document.getElementById('done-runid');
        if (ridEl) ridEl.textContent = firstDone.runId || '—';
        const mkEl = document.getElementById('done-markers');
        if (mkEl) mkEl.textContent = String(firstDone.markers_count || 0);
        const secEl = document.getElementById('done-sections');
        if (secEl) secEl.textContent = `${firstDone.sections_filled || 0} / 15`;
      }
      if (genBtn) { genBtn.disabled = false; genBtn.textContent = 'Regenerate'; isRegenerate = true; }
      if (nextBtn) { nextBtn.disabled = false; nextBtn.textContent = 'Review →'; }
    }

    if (total > 1) {
      const finalList = doneCount === total
        ? chipListDone
        : (doneCount === 0 ? chipListFailed : chipListDone);
      if (chipList && finalList && finalList !== chipList) {
        finalList.innerHTML = chipList.innerHTML;
      }
    } else {
      if (chipList) chipList.innerHTML = '';
    }

    populateResultsPicker();
  }

  function populateResultsPicker(): void {
    const wrap = document.getElementById('results-picker-wrap');
    const sel = document.getElementById('results-picker') as HTMLSelectElement | null;
    if (!wrap || !sel) return;
    const successful = batch.filter((b) => b.status === 'done' && b.runId);

    const dlAllBtn = document.getElementById('download-all-btn') as HTMLButtonElement | null;
    const dlAllDone = document.getElementById('download-all-done');
    if (dlAllBtn) {
      if (successful.length >= 1) {
        dlAllBtn.classList.remove('hidden');
        dlAllBtn.textContent = successful.length > 1
          ? `Download all ${successful.length} files`
          : 'Download all files';
        dlAllBtn.disabled = false;
      } else {
        dlAllBtn.classList.add('hidden');
      }
    }
    if (dlAllDone) dlAllDone.classList.add('hidden');

    if (successful.length === 0) {
      wrap.classList.add('hidden');
      sel.innerHTML = '';
      return;
    }
    wrap.classList.remove('hidden');
    sel.innerHTML = '';
    successful.forEach((b) => {
      const opt = document.createElement('option');
      opt.value = b.runId!;
      opt.textContent = b.name;
      sel.appendChild(opt);
    });
    if (!activeRunId || !successful.find((b) => b.runId === activeRunId)) {
      setActiveRun(successful[0].runId, successful[0].name);
    }
    sel.value = $appState.activeRunId || '';
  }

  $effect(() => {
    if (!batchActive) {
      if (filenameEl) {
        if (files.length === 0) {
          filenameEl.textContent = '—';
        } else if (files.length === 1) {
          filenameEl.textContent = files[0].name;
        } else {
          filenameEl.textContent = `${files.length} files (sequential)`;
        }
      }
      if (countWrap) {
        if (files.length > 1) {
          countWrap.classList.remove('hidden');
          if (countEl) countEl.textContent = String(files.length);
        } else {
          countWrap.classList.add('hidden');
        }
      }
    }
  });

  onDestroy(clearTimers);
</script>

<section class="step-pane">
  <div class="mono-tag mb-3">Step 02</div>
  <h1 class="font-serif text-[clamp(1.75rem,4vw,2.5rem)] font-normal leading-[1.1] tracking-[-0.02em] mb-8">
    Generate <span class="em">Policy</span>
  </h1>

  <div class="hu-card mb-6">
    <div class="hu-card-header">
      <div class="mono-label">SOURCE FILES <span bind:this={countWrap} class="hidden">· <span bind:this={countEl} id="gen-file-count">1</span></span></div>
    </div>
    <div class="hu-card-body">
      <div bind:this={filenameEl} id="gen-filename" class="font-mono text-[13px] font-medium">—</div>
    </div>
  </div>

  <div class="hu-card mb-6">
    <div class="hu-card-header">
      <div class="mono-label">PIPELINE</div>
      <div bind:this={badge} id="gen-status-badge" class="badge-hu">READY</div>
    </div>
    <div class="hu-card-body">
      <div class="flex flex-wrap gap-3 mb-4">
        <button
          id="generate-btn"
          class="pill-btn"
          bind:this={genBtn}
          onclick={startGenerate}
        >Generate Policy</button>
        <button class="pill-btn-ghost" onclick={() => onReview && onReview()}>← Back</button>
      </div>

      <div bind:this={procBox} id="status-processing" class="hidden">
        <div class="flex items-center justify-between mb-2">
          <div bind:this={statusLine} id="batch-status-line" class="mono-label">
            PROCESSING — <span id="elapsed">0</span>s
          </div>
          <button class="mono-underline" onclick={skipCurrentFile}>Skip →</button>
        </div>
        <div class="progress-track">
          <div id="progress-bar" class="progress-fill" bind:this={progressBar} style="width: 0%"></div>
        </div>
        <ul bind:this={chipList} id="batch-chip-list" class="mt-4 space-y-2"></ul>
      </div>

      <div bind:this={doneBox} id="status-done" class="hidden">
        <div class="mono-label mb-2" id="done-timestamp">—</div>
        <div class="font-mono text-[12px] text-[rgba(17,17,17,0.82)]">
          Run ID: <span id="done-runid" class="text-[#111111]"></span>
        </div>
        <div class="font-mono text-[12px] text-[rgba(17,17,17,0.82)] mt-1">
          Markers: <span id="done-markers">0</span> &nbsp;·&nbsp; Sections filled: <span id="done-sections">0 / 0</span>
        </div>
        <ul bind:this={chipListDone} id="batch-chip-list-done" class="mt-4 space-y-2"></ul>
      </div>

      <div bind:this={failedBox} id="status-failed" class="hidden">
        <div class="mono-label mb-2" style="color: var(--accent);">FAILED</div>
        <div id="failed-msg" class="font-mono text-[12px] text-[rgba(17,17,17,0.82)]"></div>
        <ul bind:this={chipListFailed} id="batch-chip-list-failed" class="mt-4 space-y-2"></ul>
      </div>
    </div>
  </div>

  <div class="flex justify-end">
    <button
      id="next-2"
      class="pill-btn"
      bind:this={nextBtn}
      disabled
      onclick={onReview}
    >Review →</button>
  </div>
</section>
