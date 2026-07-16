<script lang="ts">
  import type { VersionEntry } from './types';
  import { escapeHtml } from './escape';

  interface Props {
    versions: VersionEntry[];
    selectedVersionNo: number | null;
    onSelect?: (versionNo: number) => void;
  }
  let { versions, selectedVersionNo, onSelect }: Props = $props();

  let ordered = $derived(
    versions ? [...versions].sort((a, b) => b.version_no - a.version_no) : []
  );

  function fmt(iso: string | null | undefined): string {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const pad = (n: number) => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch {
      return '';
    }
  }

  function statusLabel(s: string): string {
    return (s || '').toUpperCase().replace('_', ' ');
  }
</script>

<div class="vt-wrap" aria-label="Version timeline">
  <div class="vt-header mono-label">VERSION HISTORY</div>
  {#if ordered.length === 0}
    <div class="vt-empty mono-underline">
      No versions yet.
    </div>
  {:else}
    <ul class="vt-list">
      {#each ordered as v (v.version_no)}
        <li class="vt-row" data-active={v.version_no === selectedVersionNo}>
          <div class="vt-row-header">
            <span class="vt-num mono-tag">V{v.version_no}</span>
            <span class="vt-status badge-hu">{statusLabel(v.review_status)}</span>
          </div>
          <div class="vt-meta">
            {fmt(v.modified_at)} &nbsp;·&nbsp; by {escapeHtml(v.modified_by || 'unknown')}
          </div>
          {#if v.change_summary}
            <div class="vt-summary">{escapeHtml(v.change_summary)}</div>
          {/if}
          {#if v.review_note}
            <div class="vt-review-note">
              <span class="mono-underline">Reviewer note</span>: {escapeHtml(v.review_note)}
            </div>
          {/if}
          <div class="vt-actions">
            <button
              type="button"
              class="mono-underline"
              onclick={() => onSelect && onSelect(v.version_no)}
              disabled={v.version_no === selectedVersionNo}
            >
              {v.version_no === selectedVersionNo ? 'Currently viewing' : 'Load this version'}
            </button>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>
