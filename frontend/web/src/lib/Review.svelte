<script lang="ts">
  import { onMount, tick } from 'svelte';
  import {
    appState,
    setActiveRun,
    setVersions,
    setCurrentVersionNo,
    setReviewAudit
  } from './stores';
  import {
    getPreview,
    downloadDocx,
    fetchDocxBlob,
    fetchAllFilesBlob,
    triggerBlobDownload,
    listVersions,
    getVersion,
    getAudit,
    saveVersion,
    submitForReview,
    reviewVersion,
    publishVersion
  } from './api';
  import type { PreviewData, BatchEntry, VersionEntry, PreviewLine } from './types';
  import VersionTimeline from './VersionTimeline.svelte';
  import ReviewComments from './ReviewComments.svelte';
  import ReviewEditor from './ReviewEditor.svelte';

  interface Props {
    onBack: () => void;
    onReset: () => void;
    onAddAnother: () => void;
  }
  let { onBack, onReset, onAddAnother }: Props = $props();

  let batch = $derived($appState.batch);
  let activeRunId = $derived($appState.activeRunId);
  let activeFilename = $derived($appState.activeFilename);

  let downloadBtn: HTMLButtonElement | null = $state(null);
  let downloadAllBtn: HTMLButtonElement | null = $state(null);
  let downloadDoneBox: HTMLDivElement | null = $state(null);
  let downloadAllDoneBox: HTMLDivElement | null = $state(null);

  // Reactive state for the preview data
  let previewData: PreviewData | null = $state(null);
  let previewError: string | null = $state(null);
  let previewLoading: boolean = $state(false);
  let lastLoadedRunId: string | null = null;

  let hasResults = $derived(batch.some((b) => b.status === 'done' && !!b.runId));

  // Marker styling helpers
  const MARKER = 'Data is not found in source file';
  const SLOT_LABEL_MAP: Record<string, number> = {
    'Type': 1, 'Policy Title': 1, 'Policy Number': 1,
    'Applicable Sector(s)': 1, 'Functional Area(s)': 1,
    'Brief Description': 2,
    'Effective Date/Period': 3, 'Approved by': 3, 'Prepared by': 3,
    'Responsible Function(s)': 3, 'Responsible Function Officer(s)': 3,
    'Supersedes': 3, 'Last Reviewed': 3, 'Applies to': 3,
    'Reason for Policy': 4,
    'POLICY STATEMENT': 6,
    '1. Purpose': 7,
    '2. Scope & Beneficiaries': 8,
    '3. Exclusions': 9,
    '4. Award Structure & Payout Tiers': 10,
    'Policy Review Note': 11,
    'DEFINITIONS': 12,
    'RELATED POLICIES, PROCEDURES, FORMS, GUIDELINES & OTHER RESOURCES': 13,
    'RELATED POLICIES': 13, 'OTHER RESOURCES': 13,
    'HISTORY': 14,
  };

  function tableSlot(rows: string[][] | undefined): number | null {
    if (!rows || !rows.length) return null;
    const firstRow = rows[0] || [];
    const firstCell = (firstRow[0] || '').toLowerCase();
    const flat = rows
      .slice(0, 3)
      .map((r) => (r || []).join(' ').toLowerCase())
      .join(' | ');
    if (
      firstCell.includes('version') ||
      firstCell.includes('history') ||
      flat.includes('history') ||
      flat.includes('version date') ||
      flat.includes('revision') ||
      flat.includes('approved date') ||
      flat.includes('effective date') ||
      flat.includes('change description')
    ) {
      return 14;
    }
    return null;
  }

  function slotForLine(text: string): number | null {
    if (!text) return null;
    for (const [label, sid] of Object.entries(SLOT_LABEL_MAP)) {
      if (text === label) return sid;
      if (text.startsWith(label + ' ')) return sid;
      if (text.startsWith(label + ':')) return sid;
    }
    return null;
  }

  // Computed: each line tagged with its slot id (0 = before first slot)
  type TaggedLine = {
    kind: 'p' | 't' | 'r';
    text?: string;
    rows?: string[][];
    slotId: number;
  };

  let taggedLines = $derived.by<TaggedLine[]>(() => {
    if (!previewData) return [];
    const lines = previewData.lines || [];
    const out: TaggedLine[] = [];
    let activeSlot = 0;
    let firstContentEmitted = false;
    for (const item of lines) {
      if (!item || item.length !== 2) continue;
      const kind = item[0];
      const payload = item[1] as any;
      let sid: number | null = null;
      if (kind === 'p') {
        sid = slotForLine(payload as string);
      } else if (kind === 't') {
        sid = tableSlot(payload as string[][]);
      }
      if (sid !== null && sid !== activeSlot && firstContentEmitted) {
        out.push({ kind: 'r', slotId: activeSlot });
        activeSlot = sid;
      } else if (sid !== null && activeSlot === 0) {
        activeSlot = sid;
        firstContentEmitted = true;
      } else if (sid === null && activeSlot === 0) {
        activeSlot = 1;
        firstContentEmitted = true;
      }
      if (kind === 'p') {
        out.push({ kind: 'p', text: payload as string, slotId: activeSlot });
      } else if (kind === 't') {
        out.push({ kind: 't', rows: payload as string[][], slotId: activeSlot });
      }
    }
    return out;
  });

  // Helper: detect "Label: Value" form for the brain-p-label rendering
  function labelValueSplit(text: string): { label: string; value: string } | null {
    const m = text.match(/^([A-Z][A-Za-z0-9 ()/\-.]+?):\s*(.*)$/);
    if (!m) return null;
    return { label: m[1], value: m[2] };
  }

  async function loadPreview(runId: string): Promise<void> {
    const MAX_ATTEMPTS = 12;
    const RETRY_DELAY_MS = 2000;
    previewLoading = true;
    previewError = null;
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      try {
        const data = await getPreview(runId);
        await tick();
        previewData = data;
        previewError = null;
        previewLoading = false;
        return;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        const notReady = /not ready/i.test(msg);
        if (notReady && attempt < MAX_ATTEMPTS) {
          await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
          continue;
        }
        previewError = 'Failed to load result: ' + msg;
        previewData = null;
        previewLoading = false;
        return;
      }
    }
  }

  // Stage 4 - load the version / audit / timeline state for a run.
  let versionsLoaded = $state<VersionEntry[]>([]);
  let viewingVersionNo = $state<number | null>(null);

  async function loadReviewData(runId: string): Promise<void> {
    try {
      const vs = await listVersions(runId);
      versionsLoaded = vs;
      setVersions(vs);
    } catch {
      versionsLoaded = [];
      setVersions([]);
    }
    try {
      const events = await getAudit(runId);
      setReviewAudit(events);
    } catch {
      setReviewAudit([]);
    }
    const latest = versionsLoaded.length > 0
      ? versionsLoaded[versionsLoaded.length - 1].version_no
      : null;
    viewingVersionNo = latest;
    setCurrentVersionNo(latest);
  }

  async function onSelectVersion(no: number): Promise<void> {
    viewingVersionNo = no;
    setCurrentVersionNo(no);
    if (!editorReadonly) {
      editorReadonly = true;
      editableLines = [];
      baselineLines = [];
      editRevision = 0;
      savedRevision = 0;
      changeSummary = '';
    }
    try {
      const resp = await getVersion(activeRunId!, no);
      if (resp && Array.isArray(resp.lines_json)) {
        previewData = { lines: resp.lines_json };
      }
    } catch {
      if (activeRunId) {
        try {
          const data = await getPreview(activeRunId);
          previewData = data;
        } catch { /* keep current previewData */ }
      }
    }
  }

  /** Refresh the preview pane so it reflects the given version's lines_json.
   *  Used after every Save / Submit / Approve / Reject / Publish so the user
   *  does not have to manually click "Load this version" to see their edits.
   *  The DB-stored lines_json is the source of truth - we use it instead of
   *  /api/preview (which rebuilds from runs.docx_path = the V0 pipeline docx
   *  and stays stale until Publish repoints it).
   */
  async function jumpToVersion(no: number): Promise<void> {
    viewingVersionNo = no;
    setCurrentVersionNo(no);
    if (!activeRunId) return;
    try {
      const resp = await getVersion(activeRunId, no);
      if (resp && Array.isArray(resp.lines_json)) {
        previewData = { lines: resp.lines_json };
      }
    } catch (e) {
      console.warn('jumpToVersion refresh failed', e);
    }
  }

  // Stage 5 - editable preview + state-machine actions.

  let editableLines = $state<PreviewLine[]>([]);
  // `baselineLines` is the snapshot at which editableLines was last
  // considered "saved" - either the initial open, or after a successful
  // save/submit/approve/publish.
  let baselineLines = $state<PreviewLine[]>([]);
  // `editRevision` increments every time the editor emits a change. Combined
  // with a saved-marker, this gives a robust dirty signal that's immune to
  // Svelte 5 reactive proxy / structuredClone quirks.
  let editRevision = $state(0);
  let savedRevision = $state(0);
  let editorDirty = $derived(editRevision !== savedRevision);
  let editorReadonly = $state(true);
  // The editorRef is set via `bind:this` on the ReviewEditor component.
  // We only call `reset()`, `undo()`, `redo()` on it.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let editorRef: any = $state(null);
  let changeSummary = $state('');
  let savingVersion = $state(false);
  let submitting = $state(false);
  let reviewActing = $state(false);
  let errorBanner = $state<string | null>(null);
  let successBanner = $state<string | null>(null);
  let approveModalOpen = $state(false);
  let rejectModalOpen = $state(false);
  let approveNote = $state('');
  let rejectNote = $state('');
  let actionReviewer = $state<string>('');

  let currentVersionEntry = $derived(
    versionsLoaded.find((v) => v.version_no === viewingVersionNo) || null
  );
  let currentStatus = $derived(
    currentVersionEntry?.review_status ?? 'draft'
  );

  function rebuildEditableFromPreview(): PreviewLine[] {
    if (!previewData || !previewData.lines) return [];
    return previewData.lines.slice();
  }

  async function refreshAfterVersionMutation(): Promise<void> {
    if (!activeRunId) return;
    try {
      const vs = await listVersions(activeRunId);
      versionsLoaded = vs;
      setVersions(vs);
      const events = await getAudit(activeRunId);
      setReviewAudit(events);
    } catch (e) {
      console.warn('post-mutation refresh failed', e);
    }
    // NOTE: previewData is NOT refreshed here. /api/preview reads
    // runs.docx_path which stays pointing at the V0 pipeline docx
    // until Publish repoints it; that overwrites the correct lines_json
    // set by jumpToVersion. Each handler now drives the preview via
    // jumpToVersion (DB-stored lines_json for the relevant version).
  }

  /** Refresh the audit log only. Called when a comment is added or
   *  resolved so the workflow-tracker's comment count stays accurate
   *  without needing a full version-mutation refresh.
   */
  async function onCommentChange(): Promise<void> {
    if (!activeRunId) return;
    try {
      const events = await getAudit(activeRunId);
      setReviewAudit(events);
    } catch (e) {
      console.warn('audit refresh after comment change failed', e);
    }
  }

  async function onSaveVersion(): Promise<void> {
    if (!activeRunId) return;
    const summary = changeSummary.trim();
    if (!summary) {
      errorBanner = 'Please enter a change summary before saving.';
      return;
    }
    savingVersion = true;
    errorBanner = null;
    successBanner = null;
    try {
      const linesJson = JSON.stringify(editableLines);
      const v = await saveVersion(activeRunId, {
        lines_json: linesJson,
        change_summary: summary,
        actor: actionReviewer.trim() || 'user'
      });
      changeSummary = '';
      snapshotBaselineFromCurrent();
      editorReadonly = true;
      successBanner = `Saved V${v.version_no}.`;
      await jumpToVersion(v.version_no);
      await refreshAfterVersionMutation();
    } catch (e) {
      errorBanner = e instanceof Error ? e.message : String(e);
    } finally {
      savingVersion = false;
    }
  }

  async function onSubmit(): Promise<void> {
    if (!activeRunId || !viewingVersionNo) return;
    submitting = true;
    errorBanner = null;
    successBanner = null;
    try {
      const updated = await submitForReview(
        activeRunId,
        viewingVersionNo,
        actionReviewer.trim() || 'user'
      );
      successBanner = `V${updated.version_no} is now In Review.`;
      editorReadonly = true;
      snapshotBaselineFromCurrent();
      await jumpToVersion(viewingVersionNo);
      await refreshAfterVersionMutation();
    } catch (e) {
      errorBanner = e instanceof Error ? e.message : String(e);
    } finally {
      submitting = false;
    }
  }

  async function onApprove(): Promise<void> {
    if (!activeRunId || !viewingVersionNo) return;
    reviewActing = true;
    errorBanner = null;
    try {
      const updated = await reviewVersion(activeRunId, viewingVersionNo, {
        action: 'approve',
        reviewer: actionReviewer.trim() || 'reviewer',
        note: approveNote.trim() || undefined
      });
      approveModalOpen = false;
      approveNote = '';
      successBanner = `V${updated.version_no} approved.`;
      snapshotBaselineFromCurrent();
      await jumpToVersion(viewingVersionNo);
      await refreshAfterVersionMutation();
    } catch (e) {
      errorBanner = e instanceof Error ? e.message : String(e);
    } finally {
      reviewActing = false;
    }
  }

  async function onReject(): Promise<void> {
    if (!activeRunId || !viewingVersionNo) return;
    if (!rejectNote.trim()) {
      errorBanner = 'Rejection reason is required.';
      return;
    }
    reviewActing = true;
    errorBanner = null;
    try {
      const updated = await reviewVersion(activeRunId, viewingVersionNo, {
        action: 'reject',
        reviewer: actionReviewer.trim() || 'reviewer',
        note: rejectNote.trim()
      });
      rejectModalOpen = false;
      rejectNote = '';
      successBanner = `V${updated.version_no} rejected — ready for revisions.`;
      snapshotBaselineFromCurrent();
      await jumpToVersion(viewingVersionNo);
      await refreshAfterVersionMutation();
    } catch (e) {
      errorBanner = e instanceof Error ? e.message : String(e);
    } finally {
      reviewActing = false;
    }
  }

  // Stage 6 - publish (approved -> published, generates final docx).

  let publishing = $state(false);

  async function onPublish(): Promise<void> {
    if (!activeRunId || !viewingVersionNo) return;
    publishing = true;
    errorBanner = null;
    successBanner = null;
    try {
      const resp = await publishVersion(
        activeRunId,
        viewingVersionNo,
        actionReviewer.trim() || 'user'
      );
      successBanner = `V${resp.version_no} published. Downloads are now enabled.`;
      snapshotBaselineFromCurrent();
      await jumpToVersion(viewingVersionNo);
      await refreshAfterVersionMutation();
    } catch (e) {
      errorBanner = e instanceof Error ? e.message : String(e);
    } finally {
      publishing = false;
    }
  }

  function openEditor(): void {
    try {
      const fresh = rebuildEditableFromPreview();
      const freshClone = clonePreviewLines(fresh);
      editableLines = fresh;
      baselineLines = freshClone;
      editRevision = 0;
      savedRevision = 0;
      editorReadonly = false;
      errorBanner = fresh.length === 0
        ? 'Preview not loaded yet. Click the Edit button again in a moment.'
        : null;
      successBanner = null;
      editorRef?.reset();
    } catch (e) {
      const m = e instanceof Error ? e.message : String(e);
      errorBanner = `openEditor error: ${m}`;
    }
  }

  function cancelEditor(): void {
    changeSummary = '';
    editorRef?.reset();
    editableLines = clonePreviewLines(baselineLines);
    // Discarding edits clears the dirty signal so Save disables again.
    editRevision = 0;
    savedRevision = 0;
  }

  /** Close the editor entirely: reset lines back to baseline, clear the
   *  change-summary, and flip back to the read-only action bar for the
   *  current version. Used by the "Close" button.
   */
  function closeEditor(): void {
    cancelEditor();
    editorReadonly = true;
    successBanner = null;
    errorBanner = null;
  }

  /** After a mutation that should treat current editableLines as the new
   *  baseline (save/submit/approve/publish), call this so editorDirty goes
   *  back to false. */
  function snapshotBaselineFromCurrent(): void {
    baselineLines = clonePreviewLines(editableLines);
    savedRevision = editRevision;
  }

  // PreviewLine[] contains nested arrays (e.g. ['p','Type: HR Policy'],
  // or ['t', rows: [ [cells: ['a','b']] ] ]). Svelte 5 reactive proxies wrap
  // these arrays, which makes structuredClone throw
  //   "Failed to execute 'structuredClone' on 'Window': [object Array] could not be cloned."
  // JSON round-trip is safe because PreviewLine is plain data (no functions,
  // no Date, no Map) and that limitation matches our editing model.
  function clonePreviewLines(src: PreviewLine[]): PreviewLine[] {
    return JSON.parse(JSON.stringify(src));
  }

  function onEditorChange(updated: PreviewLine[]): void {
    editableLines = updated;
    editRevision = editRevision + 1;
  }



  function refreshDownloadLabel(): void {
    if (!downloadBtn) return;
    const name = activeFilename || 'Policy.docx';
    const stem = name.replace(/\.[^/.]+$/, '');
    downloadBtn.textContent = `Download ${stem}.docx`;
  }

  $effect(() => {
    if (hasResults && !activeRunId) {
      const first = batch.find((b) => b.status === 'done' && b.runId);
      if (first) setActiveRun(first.runId!, first.name);
    }
  });

  $effect(() => {
    if (activeRunId && activeRunId !== lastLoadedRunId) {
      lastLoadedRunId = activeRunId;
      loadPreview(activeRunId);
      loadReviewData(activeRunId);
      refreshDownloadLabel();
    }
  });

  function onPickerChange(e: Event): void {
    const target = e.target as HTMLSelectElement | null;
    if (!target) return;
    const runId = target.value;
    if (!runId) return;
    const entry = batch.find((b) => b.runId === runId);
    setActiveRun(runId, entry ? entry.name : null);
  }

  function doDownload(): void {
    const runId = activeRunId;
    if (!runId) return;
    // Stage 6 - require a published version
    if (currentStatus !== 'published') {
      const msg =
        currentStatus === 'approved'
          ? 'Publish & Generate DOCX is required before download is enabled.'
          : 'Download is only enabled after the version is approved and published.';
      alert(msg);
      return;
    }
    const sourceName = activeFilename;
    let customName: string | undefined;
    if (sourceName) {
      const stem = sourceName.replace(/\.[^/.]+$/, '');
      customName = `${stem}.docx`;
    }
    downloadDocx(runId, customName);
    if (downloadBtn) downloadBtn.classList.add('hidden');
    if (downloadDoneBox) downloadDoneBox.classList.remove('hidden');
    const ts = document.getElementById('dl-timestamp');
    if (ts) ts.textContent = 'DOWNLOADED ' + new Date().toLocaleString();
  }

  async function doDownloadAll(): Promise<void> {
    const items: BatchEntry[] = batch.filter((b) => b.status === 'done' && b.runId);
    if (items.length === 0) return;
    if (downloadAllBtn) {
      downloadAllBtn.disabled = true;
      downloadAllBtn.textContent = `Downloading 0 / ${items.length}…`;
    }
    let okCount = 0;
    let failCount = 0;
    const failures: string[] = [];
    for (let i = 0; i < items.length; i++) {
      const entry = items[i];
      const stem = (entry.name || entry.runId || 'output').replace(/\.[^/.]+$/, '');
      const filename = `${stem}_all_files.zip`;
      if (downloadAllBtn) downloadAllBtn.textContent = `Downloading ${i + 1} / ${items.length}…`;
      try {
        // Stage 6 - backend gate returns 409 if not published. detect and record.
        const blob = await fetchAllFilesBlob(entry.runId!);
        triggerBlobDownload(blob, filename);
        okCount += 1;
      } catch (e) {
        failCount += 1;
        const msg = e instanceof Error ? e.message : String(e);
        failures.push(`${entry.name}: ${msg}`);
      }
      await new Promise((r) => setTimeout(r, 350));
    }
    if (downloadAllBtn) {
      downloadAllBtn.disabled = false;
      downloadAllBtn.textContent = 'Download all files';
    }
    if (downloadAllDoneBox) downloadAllDoneBox.classList.remove('hidden');
    const ts = document.getElementById('dl-all-timestamp');
    if (ts) {
      const summary = `DOWNLOADED ${okCount} / ${items.length}` + (failCount > 0 ? ` · ${failCount} FAILED` : '');
      ts.textContent = `${summary} — ${new Date().toLocaleString()}`;
    }
    if (failures.length > 0) {
      console.warn('Download failures:', failures);
    }
  }

  function doDownloadAgain(): void {
    if (downloadBtn) downloadBtn.classList.remove('hidden');
    if (downloadDoneBox) downloadDoneBox.classList.add('hidden');
    doDownload();
  }

  function doDownloadAllAgain(): void {
    if (downloadAllDoneBox) downloadAllDoneBox.classList.add('hidden');
    doDownloadAll();
  }

  function goBack(): void {
    onBack();
  }

  function resetAll(): void {
    onReset();
  }

  function addAnotherFile(): void {
    onAddAnother();
  }

  onMount(() => {
    if (activeRunId) {
      loadPreview(activeRunId);
      refreshDownloadLabel();
    }
  });
</script>

<section class="step-pane">
  <div class="mono-tag mb-3">Step 03</div>
  <h1 class="font-serif text-[clamp(1.75rem,4vw,2.5rem)] font-normal leading-[1.1] tracking-[-0.02em] mb-8">
    Review <span class="em">Policy</span>
  </h1>

  <div
    id="results-picker-wrap"
    class="max-w-4xl mb-6 flex items-center gap-3"
    class:hidden={!hasResults}
  >
    <div class="mono-label">RESULTS</div>
    <select
      id="results-picker"
      class="flex-1 border border-[#111111] bg-white px-3 py-2 font-mono text-[13px] font-medium focus:outline-none"
      bind:value={activeRunId}
      onchange={onPickerChange}
    >
      {#each batch.filter((b) => b.status === 'done' && b.runId) as b (b.runId)}
        <option value={b.runId}>{b.name}</option>
      {/each}
    </select>
  </div>

  <div id="slots-container" class="max-w-3xl mb-12">
    {#if previewLoading && !previewData}
      <p class="brain-p brain-marker">Loading preview…</p>
    {:else if previewError}
      <p class="brain-p brain-marker">{previewError}</p>
    {/if}
    {#each taggedLines as line, i (i)}
      {#if line.kind === 'r'}
        <div class="brain-rule"></div>
      {:else if line.kind === 'p' && line.text !== undefined}
        {#if line.text === MARKER}
          <p class="brain-p brain-marker">{line.text}</p>
        {:else}
          {@const lv = labelValueSplit(line.text)}
          {#if lv}
            <p class="brain-p">
              <span class="brain-p-label">{lv.label}:</span>
              {#if lv.value === MARKER}
                {' '}<span class="brain-marker-inline">{lv.value}</span>
              {:else}
                {' '}{lv.value}
              {/if}
            </p>
          {:else}
            <p class="brain-p">{line.text}</p>
          {/if}
        {/if}
      {:else if line.kind === 't' && line.rows}
        <div class="brain-table-wrap">
          <table class="brain-table">
            {#if line.rows[0] && line.rows[0].length}
              <thead>
                <tr>
                  {#each line.rows[0] as h, hi (hi)}
                    <th>{h == null ? '' : String(h)}</th>
                  {/each}
                </tr>
              </thead>
            {/if}
            {#if line.rows.length > 1}
              <tbody>
                {#each line.rows.slice(1) as r, ri (ri)}
                  <tr>
                    {#each r as c, ci (ci)}
                      <td>{c == null ? '' : String(c)}</td>
                    {/each}
                  </tr>
                {/each}
              </tbody>
            {/if}
          </table>
        </div>
      {/if}
    {/each}
  </div>

  <!-- Stage 4 - workflow / version panels. -->
  {#if activeRunId && versionsLoaded.length > 0}
    <div id="review-panels" class="max-w-4xl grid gap-6 mb-12" style="grid-template-columns: 1fr 1fr;">
      <div>
        <VersionTimeline
          versions={versionsLoaded}
          selectedVersionNo={viewingVersionNo}
          onSelect={onSelectVersion}
        />
      </div>
      <div>
        <ReviewComments
          runId={activeRunId}
          versionNo={viewingVersionNo}
          onCommentChange={onCommentChange}
        />
      </div>
    </div>
  {/if}

  <!-- Stage 5/6 - state-machine action bar + editor + modals. -->
  {#if activeRunId && currentVersionEntry}
    <div id="review-action-bar" class="max-w-4xl mb-12">
      <div class="ra-status-row">
        <span class="mono-label">CURRENT STATE</span>
        <span class="ra-status-badge ra-status-{currentStatus}" data-status={currentStatus}>
          {currentStatus.toUpperCase().replace('_', ' ')} (V{viewingVersionNo})
        </span>
      </div>

      {#if errorBanner}
        <div class="ra-banner ra-banner-error mono-underline">{errorBanner}</div>
      {/if}
      {#if successBanner}
        <div class="ra-banner ra-banner-success mono-underline">{successBanner}</div>
      {/if}

      {#if currentStatus === 'rejected' && currentVersionEntry?.review_note}
        <div class="ra-banner ra-banner-rejected mono-underline">
          REJECTED: {currentVersionEntry.review_note} — Edit and save to create a new version.
        </div>
      {/if}

      <div class="ra-actor-row">
        <input
          class="ra-actor-input"
          type="text"
          placeholder="Your name (used for audit & comments)"
          bind:value={actionReviewer}
          maxlength="60"
        />
      </div>

      <div class="ra-actions">
        {#if !editorReadonly}
          <input
            type="text"
            class="ra-summary-input"
            placeholder="Describe what changed in this version (required)"
            bind:value={changeSummary}
            maxlength="200"
          />
          <button
            class="pill-btn"
            onclick={onSaveVersion}
            disabled={!editorDirty || !changeSummary.trim() || savingVersion}
          >
            {savingVersion ? 'Saving…' : 'Save as new version'}
          </button>
          <button class="pill-btn-ghost" onclick={cancelEditor} disabled={savingVersion}>
            Discard edits
          </button>
          <button
            class="pill-btn-ghost"
            onclick={closeEditor}
            disabled={savingVersion}
            title="Close the editor (any unsaved edits are discarded)"
          >
            Close
          </button>
          <span class="ra-undo-group">
            <button
              class="pill-btn-ghost"
              onclick={() => editorRef?.undo?.()}
              disabled={savingVersion}
              title="Undo (Ctrl+Z)"
            >↶ Undo</button>
            <button
              class="pill-btn-ghost"
              onclick={() => editorRef?.redo?.()}
              disabled={savingVersion}
              title="Redo (Ctrl+Y)"
            >↷ Redo</button>
          </span>
        {:else if currentStatus === 'draft'}
          <button class="pill-btn" onclick={openEditor}>Edit this version</button>
          <button
            class="pill-btn"
            onclick={onSubmit}
            disabled={submitting}
          >
            {submitting ? 'Submitting…' : 'Submit for Review'}
          </button>
        {:else if currentStatus === 'rejected'}
          <button class="pill-btn" onclick={openEditor}>
            Edit & create a new version
          </button>
        {:else if currentStatus === 'in_review'}
          <button
            class="pill-btn"
            onclick={() => (approveModalOpen = true)}
            disabled={reviewActing}
          >
            Approve…
          </button>
          <button
            class="pill-btn-ghost"
            onclick={() => (rejectModalOpen = true)}
            disabled={reviewActing}
          >
            Request Changes…
          </button>
        {:else if currentStatus === 'approved'}
          <button
            class="pill-btn"
            onclick={onPublish}
            disabled={publishing}
          >
            {publishing ? 'Publishing…' : 'Publish & Generate DOCX'}
          </button>
          <button
            class="pill-btn-ghost"
            onclick={() => (rejectModalOpen = true)}
            disabled={reviewActing}
          >
            Send back for revisions…
          </button>
        {:else if currentStatus === 'published'}
          <span class="ra-banner ra-banner-success mono-underline">
            V{viewingVersionNo} is final. Editing creates V{(viewingVersionNo ?? 0) + 1} as a new draft.
          </span>
          <button class="pill-btn" onclick={openEditor}>
            Edit &amp; create a new version
          </button>
        {/if}
      </div>

      {#if !editorReadonly}
        <div class="ra-editor-wrap">
          <ReviewEditor
            bind:this={editorRef}
            bind:lines={editableLines}
            readonly={false}
            onChange={onEditorChange}
          />
        </div>
      {/if}
    </div>

    {#if approveModalOpen}
      <div
        class="ra-modal-backdrop"
        onclick={() => (approveModalOpen = false)}
        onkeydown={(e) => { if (e.key === 'Escape') approveModalOpen = false; }}
        role="presentation"
      >
        <div
          class="ra-modal"
          onclick={(e) => e.stopPropagation()}
          onkeydown={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          tabindex="-1"
        >
          <div class="ra-modal-header">Approve V{viewingVersionNo}</div>
          <textarea
            class="ra-modal-textarea"
            placeholder="Optional note (visible to the author)"
            bind:value={approveNote}
            rows="4"
          ></textarea>
          <div class="ra-modal-actions">
            <button
              class="pill-btn"
              onclick={onApprove}
              disabled={reviewActing}
            >
              {reviewActing ? 'Approving…' : 'Confirm Approve'}
            </button>
            <button
              class="pill-btn-ghost"
              onclick={() => (approveModalOpen = false)}
              disabled={reviewActing}
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    {/if}

    {#if rejectModalOpen}
      <div
        class="ra-modal-backdrop"
        onclick={() => (rejectModalOpen = false)}
        onkeydown={(e) => { if (e.key === 'Escape') rejectModalOpen = false; }}
        role="presentation"
      >
        <div
          class="ra-modal"
          onclick={(e) => e.stopPropagation()}
          onkeydown={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          tabindex="-1"
        >
          <div class="ra-modal-header">Reject V{viewingVersionNo} (back to draft)</div>
          <textarea
            class="ra-modal-textarea"
            placeholder="Rejection reason (required)"
            bind:value={rejectNote}
            rows="4"
          ></textarea>
          <div class="ra-modal-actions">
            <button
              class="pill-btn"
              onclick={onReject}
              disabled={reviewActing || !rejectNote.trim()}
            >
              {reviewActing ? 'Rejecting…' : 'Confirm Reject'}
            </button>
            <button
              class="pill-btn-ghost"
              onclick={() => (rejectModalOpen = false)}
              disabled={reviewActing}
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    {/if}
  {/if}

  <div class="border-t border-[#111111] mt-8 pt-12 max-w-4xl">
    <div class="flex flex-wrap gap-3">
      <button
        id="download-btn"
        class="pill-btn"
        bind:this={downloadBtn}
        onclick={doDownload}
        disabled={versionsLoaded.length > 0 && currentStatus !== 'published'}
        title={versionsLoaded.length > 0 && currentStatus !== 'published'
          ? (currentStatus === 'approved'
              ? 'Publish & Generate DOCX is required to enable download.'
              : 'Download becomes available after the version is approved and published.')
          : ''}
      >
        {versionsLoaded.length > 0 && currentStatus === 'published'
          ? 'Download approved docx'
          : 'Download docx (publish required)'}
      </button>
      <button
        id="download-all-btn"
        class="pill-btn"
        bind:this={downloadAllBtn}
        onclick={doDownloadAll}
        disabled={versionsLoaded.length > 0 && currentStatus !== 'published'}
        title={versionsLoaded.length > 0 && currentStatus !== 'published'
          ? 'Publish approved version first.'
          : ''}
      >Download all files</button>
      <button class="pill-btn-ghost" onclick={goBack}>← Back</button>
      <button class="pill-btn-ghost" onclick={addAnotherFile}>Add Another File</button>
      <button class="pill-btn-ghost" onclick={resetAll}>Start Over</button>
    </div>
    <div id="download-done" class="hidden mt-4" bind:this={downloadDoneBox}>
      <div class="mono-label mb-2" id="dl-timestamp">—</div>
      <button id="download-again-btn" class="pill-btn" onclick={doDownloadAgain}>Download Again</button>
    </div>
    <div id="download-all-done" class="hidden mt-4" bind:this={downloadAllDoneBox}>
      <div class="mono-label mb-2" id="dl-all-timestamp">—</div>
      <button id="download-all-again-btn" class="pill-btn" onclick={doDownloadAllAgain}>Download all again</button>
    </div>
  </div>
</section>
