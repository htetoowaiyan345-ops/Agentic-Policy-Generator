<script lang="ts">
  import { tick } from 'svelte';
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
  let previewAttempt: number = $state(0);
  let lastLoadedRunId: string | null = null;
  let previewErrorToastTimer: ReturnType<typeof setTimeout> | null = null;

  let hasResults = $derived(batch.some((b) => b.status === 'done' && !!b.runId));

  // ---------------------------------------------------------------------------
  // Word-style CKEditor 5 editor is now ALWAYS MOUNTED. No more "Edit this
  // version" gate, no more `editorReadonly`, no more read-only preview
  // block, and no more `taggedLines` string-matching. Slot routing is
  // preserved through the editor's data pipeline via the
  // `GeneralHtmlSupport` plugin (data-slot / data-slot-bar attrs).
  // Backwards-compat: PreviewLine['p'] payload was upgraded in types.ts.
  // Legacy `lines_json` payloads are normalised at the boundary by
  // `normalisePreviewLine` (used by ReviewEditor).
  // ---------------------------------------------------------------------------

  /** Show an inline, auto-dismissing error toast — does NOT blank the editor.
   *  Keeps the last good preview visible so the user can keep working. */
  function showPreviewErrorToast(msg: string): void {
    previewError = msg;
    if (previewErrorToastTimer) clearTimeout(previewErrorToastTimer);
    previewErrorToastTimer = setTimeout(() => { previewError = null; }, 5000);
  }

  /** In-flight guard so rapid-fire calls (picker click, effect re-entry,
   *  version jump, …) share a single backend round-trip instead of
   *  stacking duplicate `/api/preview` requests. */
  let previewInflight: Promise<void> | null = null;
  let reviewDataInflight: Promise<void> | null = null;

  async function loadPreview(runId: string): Promise<void> {
    if (previewInflight) return previewInflight;
    previewInflight = (async () => {
      previewLoading = true;
      previewAttempt = (previewAttempt || 0) + 1;
      try {
        const data = await getPreview(runId);
        await tick();
        previewData = data;
        editableLines = data.lines || [];
        if (typeof editorRef?.applyExternalContent === 'function') {
          editorRef.applyExternalContent(editableLines);
        }
        previewError = null;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        showPreviewErrorToast('Preview request failed: ' + msg);
        previewAttempt = 0;
      } finally {
        previewLoading = false;
      }
    })().finally(() => {
      previewInflight = null;
    });
    return previewInflight;
  }

  // Stage 4 - load the version / audit / timeline state for a run.
  let versionsLoaded = $state<VersionEntry[]>([]);
  let viewingVersionNo = $state<number | null>(null);

  async function loadReviewData(runId: string): Promise<void> {
    if (reviewDataInflight) return reviewDataInflight;
    reviewDataInflight = (async () => {
      const [vsRes, evRes] = await Promise.allSettled([
        listVersions(runId),
        getAudit(runId)
      ]);
      const vs = vsRes.status === 'fulfilled' ? vsRes.value : [];
      const events = evRes.status === 'fulfilled' ? evRes.value : [];
      versionsLoaded = vs;
      setVersions(vs);
      setReviewAudit(events);
      const latest = vs.length > 0 ? vs[vs.length - 1].version_no : null;
      viewingVersionNo = latest;
      setCurrentVersionNo(latest);
    })().finally(() => {
      reviewDataInflight = null;
    });
    return reviewDataInflight;
  }

  async function onSelectVersion(no: number): Promise<void> {
    viewingVersionNo = no;
    setCurrentVersionNo(no);
    try {
      const resp = await getVersion(activeRunId!, no);
      if (resp && Array.isArray(resp.lines_json)) {
        previewData = { lines: resp.lines_json };
        editableLines = resp.lines_json;
        if (typeof editorRef?.applyExternalContent === 'function') {
          editorRef.applyExternalContent(editableLines);
        }
        // Editor is always editable; do not reset editRevision.
      }
    } catch {
      if (activeRunId) {
        try {
          const data = await getPreview(activeRunId);
          previewData = data;
          editableLines = data.lines || [];
          if (typeof editorRef?.applyExternalContent === 'function') {
            editorRef.applyExternalContent(editableLines);
          }
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
        editableLines = resp.lines_json;
        if (typeof editorRef?.applyExternalContent === 'function') {
          editorRef.applyExternalContent(editableLines);
        }
      }
    } catch (e) {
      console.warn('jumpToVersion refresh failed', e);
    }
  }

  // Stage 5 - editable preview + state-machine actions.

  let editableLines = $state<PreviewLine[]>([]);
  // `editRevision` increments every time the editor emits a change. Combined
  // with a saved-marker, this gives a robust dirty signal that's immune to
  // Svelte 5 reactive proxy / structuredClone quirks.
  let editRevision = $state(0);
  let savedRevision = $state(0);
  let editorDirty = $derived(editRevision !== savedRevision);
  // Editor is always editable — no more `editorReadonly` gate.
  let editorReadonly = false;
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
    // NOTE: editableLines is NOT refreshed here. Each handler now drives
    // the editor via jumpToVersion (DB-stored lines_json for the
    // relevant version), which sets previewData, which (via reactive
    // tracking in ReviewEditor) refreshes the editor's slot content.
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
      savedRevision = editRevision;
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
      await jumpToVersion(viewingVersionNo);
      await refreshAfterVersionMutation();
    } catch (e) {
      errorBanner = e instanceof Error ? e.message : String(e);
    } finally {
      publishing = false;
    }
  }

  /** Editor is always editable — no openEditor / cancelEditor / closeEditor. */
  function openEditor(): void {
    // Keep as no-op for backwards compatibility with any external callers.
    // The CKEditor 5 editor is mounted from frame one in Phase 1.
  }

  function cancelEditor(): void {
    changeSummary = '';
    editorRef?.reset();
    editRevision = 0;
    savedRevision = 0;
  }

  function closeEditor(): void {
    cancelEditor();
    successBanner = null;
    errorBanner = null;
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
    // Allow download whenever a version exists for this run — the
    // backend now resolves the .docx for the currently-viewing
    // version on the fly (approved → build, published → serve cached).
    // We still surface a friendly alert only when there is literally
    // no version yet to download.
    if (currentStatus === 'draft' && !viewingVersionNo) {
      alert(
        'Save your edits as a version before downloading. ' +
        'Enter a change summary and click "Save Version".'
      );
      return;
    }
    const sourceName = activeFilename;
    let customName: string | undefined;
    if (sourceName) {
      const stem = sourceName.replace(/\.[^/.]+$/, '');
      const v = viewingVersionNo != null ? `_v${viewingVersionNo}` : '';
      customName = `${stem}${v}.docx`;
    }
    // Pass viewingVersionNo so the backend serves THIS version's .docx.
    downloadDocx(runId, customName, viewingVersionNo);
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
      // Backend now bundles every version's .docx and marks the one
      // matching `viewingVersionNo` as `_CURRENT.docx` (the file the
      // user is currently looking at). The zip also includes a
      // `manifest.txt` enumerating every version + status.
      const versionLabel = viewingVersionNo != null ? `v${viewingVersionNo}` : 'all';
      const filename = `${stem}_${versionLabel}_files.zip`;
      if (downloadAllBtn) downloadAllBtn.textContent = `Downloading ${i + 1} / ${items.length}…`;
      try {
        // Stage 6 - backend gate returns 409 if not published. detect and record.
        const blob = await fetchAllFilesBlob(entry.runId!, viewingVersionNo);
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
      downloadAllBtn.textContent =
        viewingVersionNo != null
          ? `Download all files (current: v${viewingVersionNo})`
          : 'Download all files';
    }
    if (downloadAllDoneBox) downloadAllDoneBox.classList.remove('hidden');
    const ts = document.getElementById('dl-all-timestamp');
    if (ts) {
      const summary = `DOWNLOADED ${okCount} / ${items.length}` + (failCount > 0 ? ` · ${failCount} FAILED` : '');
      const note = viewingVersionNo != null
        ? ` · current view v${viewingVersionNo} marked _CURRENT.docx`
        : '';
      ts.textContent = `${summary}${note} — ${new Date().toLocaleString()}`;
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

  <div id="slots-container" class="max-w-4xl mb-12">
    {#if previewLoading && !previewData}
      <p class="brain-p brain-marker">Loading preview…</p>
    {/if}
    <!-- Phase 1: editor is ALWAYS mounted. Errors do NOT blank the editor
         — they surface as an inline toast that auto-dismisses. -->
    <div class="ra-editor-wrap">
      <ReviewEditor
        bind:this={editorRef}
        bind:lines={editableLines}
        readonly={editorReadonly}
        onChange={onEditorChange}
      />
    </div>
    {#if previewError}
      <div class="ra-banner ra-banner-error mono-underline mt-3" role="status">
        {previewError}
      </div>
    {/if}
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

        {#if currentStatus === 'draft' || currentStatus === 'rejected'}
          <button
            class="pill-btn"
            onclick={onSubmit}
            disabled={submitting}
          >
            {submitting ? 'Submitting…' : 'Submit for Review'}
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
        {/if}
      </div>
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
          : `Download all versions — the one you're viewing (v${viewingVersionNo ?? '?'}) is marked _CURRENT in the zip.`}
      >{versionsLoaded.length > 0 && viewingVersionNo != null
          ? `Download all files (current: v${viewingVersionNo})`
          : 'Download all files'}</button>
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
