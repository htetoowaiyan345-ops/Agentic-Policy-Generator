<script lang="ts">
  import { tick } from 'svelte';
  import {
    appState,
    currentUser,
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
    fetchAllFilesZip,
    triggerBlobDownload,
    listVersions,
    getVersion,
    getAudit,
    saveVersion,
    submitForReview,
    submitDraft,
    reviewVersion,
    publishVersion,
    listProjectMembers,
    markProjectSeen,
    getDraft,
    saveDraft,
    type AccessLevel
  } from './api';
  import type { PreviewData, BatchEntry, VersionEntry, PreviewLine } from './types';
  import { timeAgo } from './timeAgo';
  import VersionTimeline from './VersionTimeline.svelte';
  import ReviewComments from './ReviewComments.svelte';
  import ReviewEditor from './ReviewEditor.svelte';
  import ProjectSharing from './ProjectSharing.svelte';

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
        const err = e instanceof Error ? e : null;
        const cause = (err?.cause ?? {}) as {
          status?: number;
          error?: string;
          status_state?: string;
        };
        // Friendly message for the common "pipeline hasn't run yet"
        // case (backend returns 400 with `{error: 'result not ready',
        // status: 'uploaded'}`).
        if (
          err
          && (cause.error === 'result not ready'
              || /result not ready/i.test(err.message))
        ) {
          const runStatus = cause.status || 'unknown';
          showPreviewErrorToast(
            `Preview isn't ready yet — this run is in "${runStatus}" state. `
            + `Process the document first to generate the .docx, then the `
            + `preview will appear here.`
          );
        } else if (err) {
          showPreviewErrorToast('Preview request failed: ' + err.message);
        } else {
          showPreviewErrorToast('Preview request failed.');
        }
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
  // Per-file currently-viewing version. Updated whenever the user
  // selects a version in the timeline (see onSelectVersion). Used by
  // "Download all files" to bundle each file's currently-viewing
  // version's .docx (not just the active file's).
  let viewingVersionByRunId = $state<Map<string, number>>(new Map());

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
      // Pick the highest V# across BOTH drafts and frozen rows. Without
      // this, a racing `loadDraftAndApply` could leave `viewingVersionNo`
      // stuck at the last array item (often v1). Deterministic pick —
      // always show the latest version's content on load.
      let initialV: number | null = null;
      if (vs.length > 0) {
        initialV = vs.reduce(
          (max, v) => (v.version_no > max ? v.version_no : max),
          vs[0].version_no
        );
      }
      viewingVersionNo = initialV;
      setCurrentVersionNo(initialV);
      if (initialV != null) {
        viewingVersionByRunId = new Map(viewingVersionByRunId).set(
          runId,
          initialV
        );
      }
      // Always load the picked version's `lines_json` into the editor
      // so the user sees the latest version's content immediately.
      // Replaces the separate `loadDraftAndApply` race-prone path.
      if (initialV != null) {
        try {
          const resp = await getVersion(runId, initialV);
          if (resp && Array.isArray(resp.lines_json)) {
            previewData = { lines: resp.lines_json };
            editableLines = resp.lines_json as PreviewLine[];
            editRevision = editRevision + 1;
            savedRevision = editRevision;
            // Allow Svelte's `editableLines = ...` reactive update to
            // propagate to the bound `ReviewEditor.lines` prop before
            // we push the new content into CKEditor. Without this, the
            // `buildUnifiedInitialHtml` inside `applyExternalContent`
            // may serialize the previous `lines` value, producing a
            // mismatch between `editableLines` (server's V_n) and the
            // editor's DOM (v2 — the prior content).
            await tick();
            if (typeof editorRef?.applyExternalContent === 'function') {
              await editorRef.applyExternalContent(editableLines);
            }
          }
        } catch {
          /* keep current state on fetch failure */
        }
      }
    })().finally(() => {
      reviewDataInflight = null;
    });
    return reviewDataInflight;
  }

  async function onSelectVersion(no: number): Promise<void> {
    // Stage 4.12 — drafts are real V#s in `versionsLoaded` (computed as
    // `latest_frozen + edit_count`). No more -1 sentinel: every row in
    // the timeline has a real V#.

    // Stage 4.12 — when switching to a different V#, cancel any
    // pending/in-flight autosave and mark the editor clean so a stale
    // autosave from the previous view doesn't pollute the new one.
    // The new V#'s content is loaded below from the server (fresh).
    // Critical: flush pending autosave FIRST so divider/format edits
    // made within the 1.5s debounce window are persisted before the
    // server data for the new version overwrites the editor.
    // Guard the flush with `editorDirty`: when re-switching back to a
    // previously-edited version, `editableLines` now holds the
    // server-loaded content from the just-viewed version — flushing
    // it would overwrite the destination version's draft row with
    // the wrong content. Only flush when there are real unsaved edits.
    if (editorDirty) {
      try {
        await flushAutosave();
      } catch {
        /* keep current view intact on flush failure */
      }
    }
    clearAutosaveTimer();
    if (autosaveAbort) {
      autosaveAbort.abort();
      autosaveAbort = null;
    }
    autosaveInFlight = false;
    autosaveError = null;
    editorDirty = false;

    viewingVersionNo = no;
    setCurrentVersionNo(no);
    if (activeRunId) {
      // Record per-file current view version so "Download all files"
      // bundles each file's currently-viewing version's .docx.
      viewingVersionByRunId = new Map(viewingVersionByRunId).set(
        activeRunId,
        no
      );
    }
    try {
      const resp = await getVersion(activeRunId!, no);
      if (resp && Array.isArray(resp.lines_json)) {
        previewData = { lines: resp.lines_json };
        editableLines = resp.lines_json;
        // Allow the Svelte reactive `editableLines = resp.lines_json`
        // assignment to propagate to the bound `ReviewEditor.lines` prop
        // BEFORE we push the new content to CKEditor. Without this, the
        // first version-switch click fails to refresh the editor DOM
        // (the prior version's content lingers until the user clicks a
        // second time). See also: CkEditor.setHtml short-circuit was
        // removed in this round.
        await tick();
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
          await tick();
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
        // Same rationale as onSelectVersion: propagate the reactive
        // update through Svelte before pushing to CKEditor.
        await tick();
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
  // Stage 4.13 — autosave-draft state. `autosaveClientEditId` is
  // generated ONCE per session (per user, per run) and reused for
  // every save. Same id within 60s → server UPDATE-in-place (content
  // updates, V# stays stable). After 60s, the next save with the same
  // id... actually it still matches the last row so it updates again.
  // The 60s window only matters for network retries: a true retry
  // with the same edit_id within 60s gets the cached response.
  // `autosaveInFlight` gates the "Saving…" status; `autosaveError` is
  // shown when the most-recent attempt failed.
  let autosaveClientEditId = $state<string>(crypto.randomUUID());
  let autosaveEditId = $state<string | null>(null);
  let autosaveEditCount = $state<number>(0);
  let autosaveSavedAt = $state<string | null>(null);
  let autosaveDraftVersionNo = $state<number>(0);
  let autosaveInFlight = $state<boolean>(false);
  let autosaveError = $state<string | null>(null);
  // `nowTick` is bumped every 30s so `timeAgo(autosaveSavedAt)`
  // re-evaluates and the "Saved Ns ago" text stays current.
  let nowTick = $state<number>(Date.now());
  // Stage 3 — actor / reviewer identity is now derived from the
  // logged-in user (currentUser). The free-text "Your name" input
  // is gone; the action bar shows the current username read-only.
  let actorName = $derived($currentUser?.username ?? 'anonymous');
  let isAdmin = $derived(!!$currentUser?.is_admin);

  // Stage 4.5/4.7 — per-project access (server reports it back via
  // the share-popup endpoint). When `null` the badge hides.
  let yourAccess = $state<AccessLevel | null>(null);
  let shareOpen = $state(false);
  // Maps each batch file's runId to its access (populated whenever a
  // file becomes active). Drives the per-file Flow 3 badge on the
  // Results dropdown line.
  let yourAccessByRunId = $state<Map<string, AccessLevel>>(new Map());

  async function refreshYourAccess(runId: string): Promise<void> {
    if (!runId) return;
    try {
      const data = await listProjectMembers(runId);
      const lvl = (data.your_access ?? null) as AccessLevel | null;
      yourAccess = lvl;
      const next = new Map(yourAccessByRunId);
      if (lvl) next.set(runId, lvl);
      else next.delete(runId);
      yourAccessByRunId = next;
    } catch {
      yourAccess = null;
    }
  }

  async function onShareClick(): Promise<void> {
    if (!activeRunId) return;
    shareOpen = true;
    try {
      await markProjectSeen(activeRunId);
    } catch {
      /* non-fatal — the modal will still open */
    }
  }

  // Stage 4.7 — per-action visibility derived from `yourAccess`.
  // Viewer: read-only (no editor / no status-machine writes / no comments).
  // Editor: editor + submit + comments + reviewer-assign — no approve/publish.
  // Approver: every action allowed.
  let canEdit = $derived(yourAccess === 'editor' || yourAccess === 'approver');
  let canComment = $derived(yourAccess === 'editor' || yourAccess === 'approver');
  let canSubmit = $derived(yourAccess === 'editor' || yourAccess === 'approver');
  let canReview = $derived(yourAccess === 'approver');
  let canPublish = $derived(yourAccess === 'approver');

  // Share controls (badge + Share button) are visible whenever the
  // current user has approver access on this run, OR is an admin
  // (admins get implicit approver access). Independent of `hasResults`
  // so the controls still appear when the user opens the page from
  // a notification before any uploads populate the local batch.
  let showShareControls = $derived(
    yourAccess === 'approver' || (!yourAccess && isAdmin)
  );

  // Viewer role — read-only. They don't get the state-machine action
  // bar (CURRENT STATE / ACTOR / Submit / Approve / etc.). Only the
  // preview pane + comments are visible.
  let isViewer = $derived(yourAccess === 'viewer');

  let currentVersionEntry = $derived(
    versionsLoaded.find((v) => v.version_no === viewingVersionNo) || null
  );
  let currentStatus = $derived(
    currentVersionEntry?.review_status ?? 'draft'
  );

  // Stage 4.12 — drafting rows now live in `versionsLoaded` with
  // `kind: 'draft'` and `review_status: 'drafting'`. The CURRENT STATE
  // badge just reflects the row's actual status (no sentinel).
  let onDraft = $derived(currentVersionEntry?.kind === 'draft');
  let displayStatus = $derived(
    onDraft ? 'drafting' : currentStatus
  );
  let displayVersionNo = $derived(viewingVersionNo ?? 0);

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

  // Stage 4.11 — autosave: debounced POST to /api/runs/<id>/draft.
  // `AUTOSAVE_DEBOUNCE_MS` is the quiet-period after the last
  // keystroke before we POST. The same `client_edit_id` (stable
  // per session) is reused so a network-retry of the same intent
  // is idempotent server-side.
  const AUTOSAVE_DEBOUNCE_MS = 1500;
  let autosaveTimer: ReturnType<typeof setTimeout> | null = null;
  let autosaveAbort: AbortController | null = null;

  function clearAutosaveTimer(): void {
    if (autosaveTimer) {
      clearTimeout(autosaveTimer);
      autosaveTimer = null;
    }
  }

  async function flushAutosave(): Promise<void> {
    if (!activeRunId) return;
    if (autosaveAbort) {
      // Cancel any in-flight request so the newer state wins.
      autosaveAbort.abort();
    }
    const controller = new AbortController();
    autosaveAbort = controller;
    autosaveInFlight = true;
    autosaveError = null;
    try {
      const linesJson = JSON.stringify(editableLines);
      // [DIAG] start — diagnostic logging only, no logic change
      console.log('[DIAG-Review] autosave payload', {
        editableLines_count: editableLines.length,
        dividers: editableLines.filter((l) => Array.isArray(l) && l[0] === 'divider').length,
        with_strong: editableLines.filter((l) => Array.isArray(l) && l[0] === 'p' && (l[1]?.html?.includes('<strong>') || l[1]?.html?.includes('<b>'))).length,
        with_italic: editableLines.filter((l) => Array.isArray(l) && l[0] === 'p' && (l[1]?.html?.includes('<em>') || l[1]?.html?.includes('<i>'))).length,
        with_color: editableLines.filter((l) => Array.isArray(l) && l[0] === 'p' && l[1]?.html?.includes('color:')).length,
        with_hr_in_p: editableLines.filter((l) => Array.isArray(l) && l[0] === 'p' && l[1]?.html?.includes('<hr')).length,
        payload_chars: linesJson.length
      });
      // [DIAG] end
      // Stage 4.13 — use the stable per-session `autosaveClientEditId`.
      // Server uses the 60s idempotency window + UPDATE-in-place to
      // update the SAME draft row with new content. No new V# is
      // created for in-progress editing.
      const resp = await saveDraft(
        activeRunId, linesJson, autosaveClientEditId
      );
      // [DIAG] log backend response
      console.log('[DIAG-Review] autosave response', {
        edit_id: resp?.edit_id,
        edit_count: resp?.edit_count,
        draft_version_no: resp?.draft_version_no,
        success: !!resp
      });
      // [DIAG] end
      autosaveEditId = resp.edit_id;
      autosaveEditCount = resp.edit_count;
      autosaveSavedAt = resp.saved_at;
      // Stage 4.14 — refresh versionsLoaded so the latest timeline
      // state (including any new draft row created after a 60s gap)
      // is reflected in the UI. V# is computed from the server's
      // response: latest_frozen + edit_count.
      try {
        const vs = await listVersions(activeRunId);
        versionsLoaded = vs;
        setVersions(vs);
      } catch {
        /* non-fatal — fall back to resp.draft_version_no */
        autosaveDraftVersionNo = resp.draft_version_no;
      }
      autosaveDraftVersionNo = resp.draft_version_no;
      // Sync the editor's "Currently viewing" indicator with the V#
      // that the server assigned to this autosave. When the server
      // creates a fresh draft row (first save of the session, or
      // after a 60s idle gap), this jumps the viewer to that new V#
      // so the user does not have to manually click it in the
      // timeline. Otherwise `viewingVersionNo` stays at the V# the
      // user landed on at page load while a new V# silently appears
      // in the timeline — making the editor appear "off" the version
      // the user is actually editing.
      if (
        activeRunId != null &&
        resp.draft_version_no != null &&
        viewingVersionNo !== resp.draft_version_no
      ) {
        viewingVersionNo = resp.draft_version_no;
        setCurrentVersionNo(resp.draft_version_no);
        viewingVersionByRunId = new Map(viewingVersionByRunId).set(
          activeRunId,
          resp.draft_version_no
        );
      }
      savedRevision = editRevision;
      nowTick = Date.now();
    } catch (e) {
      // AbortError is intentional — don't show it as a failure.
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.toLowerCase().includes('abort')) return;
      autosaveError = msg;
    } finally {
      if (autosaveAbort === controller) {
        autosaveAbort = null;
      }
      autosaveInFlight = false;
    }
  }

  function scheduleAutosave(): void {
    if (!canEdit) return;  // viewers never autosave
    clearAutosaveTimer();
    autosaveTimer = setTimeout(() => {
      autosaveTimer = null;
      flushAutosave();
    }, AUTOSAVE_DEBOUNCE_MS);
  }

  // Derived: the human-readable save-status string shown in the action
  // bar. Pure presentation; never used for logic.
  let autosaveStatusText = $derived.by(() => {
    // Force re-evaluation of "Ns ago" every 30s.
    void nowTick;
    if (autosaveError) {
      return `Autosave failed: ${autosaveError}`;
    }
    if (autosaveInFlight) {
      return 'Saving…';
    }
    if (autosaveSavedAt) {
      const ago = timeAgo(autosaveSavedAt);
      return `Saved ${ago} · editing V${autosaveDraftVersionNo}`;
    }
    return 'No edits yet';
  });

  async function onSubmit(): Promise<void> {
    if (!activeRunId) return;
    submitting = true;
    errorBanner = null;
    successBanner = null;
    try {
      // Stage 4.13 — flush any pending autosave BEFORE submit so the
      // server's draft is the freshest possible snapshot. Submit
      // consumes the (single) draft into a new frozen version.
      clearAutosaveTimer();
      if (autosaveInFlight) {
        await new Promise<void>((resolve) => setTimeout(resolve, 300));
      }
      if (editorDirty) {
        await flushAutosave();
      }
      // Single draft row per run; the server picks it by default.
      const updated = await submitDraft(activeRunId, viewingVersionNo);
      successBanner = `V${updated.version_no} is now In Review.`;
      autosaveEditCount = 0;
      autosaveSavedAt = null;
      autosaveDraftVersionNo = 0;
      autosaveEditId = null;
      savedRevision = editRevision;
      await loadReviewData(activeRunId);
      if (updated.version_no != null) {
        await jumpToVersion(updated.version_no);
      }
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
        reviewer: actorName,
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
        reviewer: actorName,
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
      // Flush any pending autosave BEFORE publish so toolbar-inserted
      // bold/colour/divider/etc. are persisted into the draft row that
      // publish (or download-on-the-fly for approved rows) will read.
      // Without this, edits within the 1.5s debounce window are lost
      // in the .docx output.
      try {
        if (editorDirty) {
          await flushAutosave();
        }
      } catch {
        /* non-fatal — keep current view intact on flush failure */
      }
      const resp = await publishVersion(
        activeRunId,
        viewingVersionNo,
        actorName
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
    // Stage 4.11 — also clear any in-flight autosave so we don't
    // write back the discarded content.
    clearAutosaveTimer();
    if (autosaveAbort) {
      autosaveAbort.abort();
      autosaveAbort = null;
    }
    autosaveError = null;
  }

  function closeEditor(): void {
    cancelEditor();
    successBanner = null;
    errorBanner = null;
  }

  function onEditorChange(updated: PreviewLine[]): void {
    // [DIAG] start — diagnostic logging only, no logic change
    console.log('[DIAG-Review] onEditorChange', {
      total: updated.length,
      dividers: updated.filter((l) => Array.isArray(l) && l[0] === 'divider').length,
      with_strong: updated.filter((l) => Array.isArray(l) && l[0] === 'p' && (l[1]?.html?.includes('<strong>') || l[1]?.html?.includes('<b>'))).length,
      with_italic: updated.filter((l) => Array.isArray(l) && l[0] === 'p' && (l[1]?.html?.includes('<em>') || l[1]?.html?.includes('<i>'))).length,
      with_color: updated.filter((l) => Array.isArray(l) && l[0] === 'p' && l[1]?.html?.includes('color:')).length,
      with_hr_in_p: updated.filter((l) => Array.isArray(l) && l[0] === 'p' && l[1]?.html?.includes('<hr')).length
    });
    // [DIAG] end
    editableLines = updated;
    editRevision = editRevision + 1;
    scheduleAutosave();
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

  // When the logged-in user changes (login / logout / re-login), force
  // a full reload of the per-project state. Without this, a fresh login
  // into the same `activeRunId` skips `refreshYourAccess` because the
  // effect's gate `activeRunId !== lastLoadedRunId` is false.
  let lastSeenUserId: number | null = null;
  $effect(() => {
    const uid = $currentUser?.id ?? null;
    // Read both reactive deps up front so the effect re-runs on either change.
    const runId = activeRunId;
    if (uid !== lastSeenUserId) {
      lastSeenUserId = uid;
      lastLoadedRunId = null;
      yourAccess = null;
    }
    if (uid && runId && runId !== lastLoadedRunId) {
      lastLoadedRunId = runId;
      // Stage 4.13 — when switching to a new run, generate a fresh
      // `autosaveClientEditId` so a new draft row is created for the
      // new run (and no collision with the previous run's draft).
      // Also reset autosave state and clear pending timers.
      autosaveClientEditId = crypto.randomUUID();
      autosaveEditId = null;
      autosaveEditCount = 0;
      autosaveSavedAt = null;
      autosaveDraftVersionNo = 0;
      autosaveError = null;
      // Defensive: clear `viewingVersionNo` so `loadReviewData`'s
      // reduce picks the freshest V# without interference from any
      // stale value left by a previous mount/render of this
      // component. `loadReviewData` will set it to the highest V#
      // once the API response arrives.
      viewingVersionNo = null;
      clearAutosaveTimer();
      if (autosaveAbort) {
        autosaveAbort.abort();
        autosaveAbort = null;
      }
      loadPreview(runId);
      loadReviewData(runId);
      refreshDownloadLabel();
      refreshYourAccess(runId);
    }
  });

  // Stage 4.11 — refresh "Saved Ns ago" every 30s without re-renders
  // elsewhere.
  $effect(() => {
    const handle = setInterval(() => {
      nowTick = Date.now();
    }, 30_000);
    return () => clearInterval(handle);
  });

  function onPickerChange(e: Event): void {
    const target = e.target as HTMLSelectElement | null;
    if (!target) return;
    const runId = target.value;
    if (!runId) return;
    const entry = batch.find((b) => b.runId === runId);
    setActiveRun(runId, entry ? entry.name : null);
  }

  async function doDownload(): Promise<void> {
    const runId = activeRunId;
    if (!runId) return;
    // Always download the LATEST PUBLISHED version of this run —
    // never the viewing/draft version. Pass `null` so the backend
    // resolves `latest_published_version_no` itself. If no published
    // version exists yet, the backend returns 409 with a friendly
    // message and `downloadDocx` surfaces it via alert().
    const sourceName = activeFilename;
    let customName: string | undefined;
    if (sourceName) {
      const stem = sourceName.replace(/\.[^/.]+$/, '');
      customName = `${stem}.docx`;
    }
    // Flush any pending autosave BEFORE download so the latest edits
    // are in the draft row that Publish will pick up on the next
    // user click. Without this, edits within the 1.5s debounce window
    // miss the next publish step.
    try {
      if (editorDirty) {
        await flushAutosave();
      }
    } catch {
      /* non-fatal — keep current view intact on flush failure */
    }
    downloadDocx(runId, customName, null);
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
      downloadAllBtn.textContent = `Preparing ${items.length} file${items.length === 1 ? '' : 's'}…`;
    }
    try {
      // "Download all files" = bundle each run's LATEST PUBLISHED
      // version into one zip. Runs with no published version are
      // SKIPPED (not included, no error). mode='published' tells the
      // backend to use latest_published_version_no per run and skip
      // the rest. If ALL runs are unpublished, the backend returns
      // 409 and we surface a friendly alert below.
      const allItems = items.map((b) => ({
        runId: b.runId!,
        versionNo: null
      }));

      const blob = await fetchAllFilesZip(allItems, 'published');
      const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
      const filename = `all_files_${ts}.zip`;
      triggerBlobDownload(blob, filename);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      alert('Download failed: ' + msg);
      console.warn('Download all failed:', e);
    } finally {
      if (downloadAllBtn) {
        downloadAllBtn.disabled = false;
        const totalVersions = items.length;
        downloadAllBtn.textContent =
          totalVersions > 0
            ? `Download all files (${totalVersions} file${totalVersions === 1 ? '' : 's'})`
            : 'Download all files';
      }
      if (downloadAllDoneBox) downloadAllDoneBox.classList.remove('hidden');
      const ts = document.getElementById('dl-all-timestamp');
      if (ts) {
        ts.textContent = `DOWNLOADED — ${new Date().toLocaleString()}`;
      }
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

  // Stage 4.5 — Flow 1 popup (only opened from the Share button).
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

  <div
    id="share-row"
    class="max-w-4xl mb-6 flex items-center gap-3"
    class:hidden={!showShareControls}
  >
    {#if yourAccess === 'approver'}
      <span
        class="share-badge share-{yourAccess}"
        data-testid="share-badge"
        title="Your access level for this project"
      >{yourAccess}</span>
      <button
        type="button"
        id="share-btn"
        class="pill-btn"
        data-testid="share-btn"
        onclick={onShareClick}
      >Share</button>
    {/if}
    {#if !yourAccess && isAdmin}
      <span
        class="share-badge share-approver"
        data-testid="share-badge"
        title="Admin — full access"
      >approver</span>
      <button
        type="button"
        id="share-btn"
        class="pill-btn"
        data-testid="share-btn"
        onclick={onShareClick}
      >Share</button>
    {/if}
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
          editable={canComment}
        />
      </div>
    </div>
  {/if}

  <!-- Stage 5/6 - state-machine action bar + editor + modals. -->
  {#if activeRunId && (currentVersionEntry || onDraft) && !isViewer}
    <div id="review-action-bar" class="max-w-4xl mb-12">
      <div class="ra-status-row">
        <span class="mono-label">CURRENT STATE</span>
        <span class="ra-status-badge ra-status-{displayStatus}" data-status={displayStatus}>
          {displayStatus.toUpperCase().replace('_', ' ')} (V{displayVersionNo})
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

      <div class="ra-actor-row ra-actor-row-inline" data-testid="actor-row">
        <span class="mono-label">ACTOR</span>
        <span class="ra-actor-display" data-testid="actor-name">
          {actorName}{isAdmin ? ' · admin' : ''}
        </span>
        <span class="ra-divider">·</span>
        <span class="ra-saved-status-inline-text" data-testid="saved-text">{autosaveStatusText}</span>
        <span class="ra-actions-inline">
          {#if canSubmit && (currentStatus === 'draft' || currentStatus === 'drafting' || currentStatus === 'rejected')}
            <button
              class="pill-btn"
              onclick={onSubmit}
              disabled={submitting}
            >
              {submitting ? 'Submitting…' : 'Submit for Review'}
            </button>
          {:else if canReview && currentStatus === 'in_review'}
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
          {:else if canReview && currentStatus === 'approved'}
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
        </span>
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
          <div class="ra-modal-header">Reject V{viewingVersionNo}</div>
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
    </div>
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
          : `Download all files in the Results dropdown as ONE zip — each file's currently-viewing version.`}
      >{`Download all files (${batch.filter((b) => b.status === 'done' && b.runId).length})`}</button>
      <button class="pill-btn-ghost" onclick={goBack}>← Back</button>
      <button class="pill-btn-ghost" onclick={addAnotherFile}>Add Another File</button>
      <button class="pill-btn-ghost" onclick={resetAll}>Start Over</button>
    </div>
    <div id="download-done" class="hidden mt-4" bind:this={downloadDoneBox}>
      <div class="mono-label mb-2" id="dl-timestamp">—</div>
      <button id="download-again-btn" class="pill-btn" onclick={doDownloadAgain}>Download Again</button>
    </div>
    <div id="download-all-done" class="hidden mt-4" bind:this={downloadAllDoneBox}>
      <div class="mono-label mb-2" id="dl-all-timestamp">?"</div>
      <button id="download-all-again-btn" class="pill-btn" onclick={doDownloadAllAgain}>Download all again</button>
    </div>
  </div>
</section>

{#if shareOpen && activeRunId}
  <ProjectSharing
    runId={activeRunId}
    onClose={() => (shareOpen = false)}
  />
{/if}
