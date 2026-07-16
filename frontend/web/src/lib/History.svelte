<script lang="ts">
  import { onMount } from 'svelte';
  import { getHistory, downloadDocx } from './api';
  import { loadResultAndShow } from './page-actions';
  import { escapeHtml } from './escape';
  import type { HistoryEntry } from './types';

  interface Props {
    open: boolean;
    onClose: () => void;
  }
  let { open, onClose }: Props = $props();

  let runs = $state<HistoryEntry[]>([]);
  let loaded = $state(false);
  let error = $state<string | null>(null);

  function fmtTimestamp(iso: string | undefined): string {
    if (!iso) return '';
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  export async function refresh(): Promise<void> {
    error = null;
    try {
      runs = (await getHistory()) || [];
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      error = msg;
      runs = [];
    }
    loaded = true;
  }

  $effect(() => {
    if (open && !loaded) {
      refresh();
    }
  });

  function onRowClick(e: MouseEvent) {
    const target = e.target as HTMLElement | null;
    if (!target) return;
    const btn = target.closest('button[data-act]') as HTMLButtonElement | null;
    if (!btn) return;
    const runId = btn.getAttribute('data-run');
    const act = btn.getAttribute('data-act');
    if (!runId || !act) return;
    if (act === 'dl') {
      downloadDocx(runId);
    } else if (act === 'load') {
      loadResultAndShow(runId);
    }
  }

  onMount(() => {
    if (open) refresh();
  });
</script>

<div
  id="history-panel"
  class="border border-[#111111] mb-8 bg-white"
  class:hidden={!open}
>
  <div class="flex items-center justify-between px-5 py-4 border-b border-[rgb(216,212,212)]">
    <div class="mono-label">RECENT RUNS</div>
    <button class="mono-underline" onclick={onClose}>Close</button>
  </div>
  <div class="p-5">
    {#if error}
      <div class="font-mono text-[12px] text-[rgba(17,17,17,0.58)]">
        Could not load history.
      </div>
    {:else if loaded && runs.length === 0}
      <div class="text-sm text-[rgba(17,17,17,0.58)] py-4">No runs yet.</div>
    {:else}
      <div id="history-list" class="space-y-3" onclick={onRowClick} role="presentation">
        {#each runs.slice(0, 10) as r (r.run_id)}
          <div class="hu-card hu-card-soft">
            <div class="hu-card-body">
              <div class="font-mono text-[13px] font-medium break-all text-[#111111]">
                {r.filename}
              </div>
              <div class="font-mono text-[10px] tracking-[0.1em] uppercase text-[rgba(17,17,17,0.58)] mt-1">
                {fmtTimestamp(r.created_at)} · {(r.status || 'done').toUpperCase()} · {r.sections_filled}/15 · {r.markers_count} markers
              </div>
              <div class="flex flex-wrap gap-3 mt-3">
                <button class="mono-underline" data-act="dl" data-run={r.run_id}>
                  Re-download .docx
                </button>
                <button class="mono-underline" data-act="load" data-run={r.run_id}>
                  Load result
                </button>
              </div>
            </div>
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>
