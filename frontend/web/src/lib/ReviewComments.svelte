<script lang="ts">
  import type { ReviewComment } from './types';
  import { addComment, resolveComment, listComments } from './api';

  interface Props {
    runId: string;
    versionNo: number | null;
    /** Optional: invoked after a comment is added or resolved so the parent
     *  can refresh derived UI (e.g. the workflow-tracker's comment count). */
    onCommentChange?: () => void;
  }
  let { runId, versionNo, onCommentChange }: Props = $props();

  let comments = $state<ReviewComment[]>([]);
  let loading = $state(false);
  let error = $state<string | null>(null);

  let newBody = $state('');
  let newAnchorKind = $state<'slot' | 'paragraph' | 'general' | ''>('general');
  let newAnchorKey = $state('');
  let author = $state<string>('');
  let submitting = $state(false);

  const ANCHOR_OPTIONS: { value: string; label: string }[] = [
    { value: 'slot:Type', label: 'Slot: Type' },
    { value: 'slot:Policy Title', label: 'Slot: Policy Title' },
    { value: 'slot:Policy Number', label: 'Slot: Policy Number' },
    { value: 'slot:Applicable Sector(s)', label: 'Slot: Applicable Sector(s)' },
    { value: 'slot:Functional Area(s)', label: 'Slot: Functional Area(s)' },
    { value: 'slot:Brief Description', label: 'Slot: Brief Description' },
    { value: 'slot:Effective Date/Period', label: 'Slot: Effective Date/Period' },
    { value: 'slot:Approved by', label: 'Slot: Approved by' },
    { value: 'slot:Prepared by', label: 'Slot: Prepared by' },
    { value: 'slot:Responsible Function(s)', label: 'Slot: Responsible Function(s)' },
    { value: 'slot:Responsible Function Officer(s)', label: 'Slot: Responsible Function Officer(s)' },
    { value: 'slot:Supersedes', label: 'Slot: Supersedes' },
    { value: 'slot:Last Reviewed/Updated', label: 'Slot: Last Reviewed/Updated' },
    { value: 'slot:Applies to', label: 'Slot: Applies to' },
    { value: 'slot:Reason for Policy', label: 'Slot: Reason for Policy' },
    { value: 'slot:POLICY STATEMENT', label: 'Slot: POLICY STATEMENT' },
    { value: 'slot:1. Purpose', label: 'Slot: 1. Purpose' },
    { value: 'slot:2. Scope & Beneficiaries', label: 'Slot: 2. Scope & Beneficiaries' },
    { value: 'slot:3. Exclusions', label: 'Slot: 3. Exclusions' },
    { value: 'slot:4. Award Structure & Payout Tiers', label: 'Slot: 4. Award Structure & Payout Tiers' },
    { value: 'slot:Policy Review Note', label: 'Slot: Policy Review Note' },
    { value: 'slot:DEFINITIONS', label: 'Slot: DEFINITIONS' },
    { value: 'slot:RELATED POLICIES', label: 'Slot: RELATED POLICIES' },
    { value: 'slot:HISTORY', label: 'Slot: HISTORY' }
  ];

  async function refresh(): Promise<void> {
    if (!runId || !versionNo) {
      comments = [];
      return;
    }
    loading = true;
    error = null;
    try {
      comments = await listComments(runId, versionNo);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
      comments = [];
    } finally {
      loading = false;
    }
  }

  async function onAnchorChange(e: Event): Promise<void> {
    const v = (e.target as HTMLSelectElement).value;
    if (!v) {
      newAnchorKind = '';
      newAnchorKey = '';
      return;
    }
    const [kind, key] = v.split(':');
    newAnchorKind = kind as 'slot' | 'paragraph' | 'general';
    newAnchorKey = key || '';
  }

  async function onSubmit(e: Event): Promise<void> {
    e.preventDefault();
    if (!newBody.trim() || !runId || !versionNo) return;
    submitting = true;
    error = null;
    try {
      await addComment(runId, versionNo, {
        body: newBody.trim(),
        anchor_kind: newAnchorKind || null,
        anchor_key: newAnchorKey || null,
        author: author.trim() || 'user'
      });
      newBody = '';
      newAnchorKind = 'general';
      newAnchorKey = '';
      await refresh();
      onCommentChange?.();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      submitting = false;
    }
  }

  async function onResolve(commentId: number): Promise<void> {
    if (!runId || !versionNo) return;
    try {
      await resolveComment(runId, versionNo, commentId);
      await refresh();
      onCommentChange?.();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    }
  }

  function fmt(iso: string | null | undefined): string {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const pad = (n: number) => String(n).padStart(2, '0');
      return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch {
      return '';
    }
  }

  function authorFor(c: ReviewComment): string {
    return c.author || 'user';
  }

  function displayAnchor(c: ReviewComment): string {
    if (!c.anchor_kind) return '';
    if (c.anchor_kind === 'slot' && c.anchor_key) {
      return `Slot: ${c.anchor_key}`;
    }
    if (c.anchor_kind === 'paragraph' && c.anchor_key) {
      return `Paragraph: ${c.anchor_key}`;
    }
    return 'General';
  }

  $effect(() => {
    refresh();
  });
</script>

<div class="rc-wrap" aria-label="Comments">
  <div class="rc-header mono-label">COMMENTS</div>

  <form class="rc-form" onsubmit={onSubmit}>
    <input
      type="text"
      class="rc-author-input"
      placeholder="Your name (for audit)"
      bind:value={author}
      maxlength="40"
    />
    <select
      class="rc-anchor-select"
      onchange={onAnchorChange}
      value={newAnchorKind && newAnchorKey
        ? `${newAnchorKind}:${newAnchorKey}`
        : ''}
    >
      <option value="">General (no anchor)</option>
      {#each ANCHOR_OPTIONS as opt (opt.value)}
        <option value={opt.value}>{opt.label}</option>
      {/each}
    </select>
    <textarea
      class="rc-body-textarea"
      placeholder="Add a comment or feedback..."
      bind:value={newBody}
      rows="3"
    ></textarea>
    <button
      class="pill-btn rc-submit"
      type="submit"
      disabled={!newBody.trim() || !runId || !versionNo || submitting}
    >
      {submitting ? 'Posting…' : 'Post Comment'}
    </button>
  </form>

  {#if error}
    <div class="rc-error mono-underline">Error: {error}</div>
  {/if}

  <div class="rc-list">
    {#if loading}
      <div class="rc-empty mono-underline">Loading comments…</div>
    {:else if comments.length === 0}
      <div class="rc-empty mono-underline">No comments yet on this version.</div>
    {:else}
      {#each comments as c (c.comment_id)}
        <div class="rc-item" data-resolved={!!c.resolved}>
          <div class="rc-item-head">
            <span class="rc-author">{authorFor(c)}</span>
            <span class="rc-when mono-tag">{fmt(c.created_at)}</span>
          </div>
          {#if displayAnchor(c)}
            <div class="rc-anchor mono-underline">{displayAnchor(c)}</div>
          {/if}
          <div class="rc-body">{c.body}</div>
          {#if !c.resolved}
            <button
              type="button"
              class="mono-underline rc-resolve-btn"
              onclick={() => onResolve(c.comment_id)}
            >
              Resolve
            </button>
          {:else}
            <div class="rc-resolved mono-underline">RESOLVED</div>
          {/if}
        </div>
      {/each}
    {/if}
  </div>
</div>
