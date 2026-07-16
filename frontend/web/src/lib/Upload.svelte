<script lang="ts">
  import { appState, setFiles, setRejected } from './stores';
  import { fmtMB, escapeHtml } from './escape';
  import type { RejectedFile } from './types';

  const ALLOWED_EXTS = ['pdf', 'docx', 'txt'];
  const MAX_BYTES = 50 * 1024 * 1024;

  interface Props {
    onProceed: () => void;
  }
  let { onProceed }: Props = $props();

  let files = $derived($appState.files);
  let rejected = $derived($appState.rejected);

  let uploadArea: HTMLDivElement | null = $state(null);
  let fileInput: HTMLInputElement | null = $state(null);
  let listEl: HTMLUListElement | null = $state(null);
  let rejectedListEl: HTMLUListElement | null = $state(null);

  function validate(file: File): { ok: boolean; reason?: string } {
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    if (!ALLOWED_EXTS.includes(ext)) return { ok: false, reason: 'unsupported type' };
    if (file.size > MAX_BYTES) return { ok: false, reason: 'too large (' + fmtMB(file.size) + ')' };
    if (file.size === 0) return { ok: false, reason: 'empty file' };
    return { ok: true };
  }

  function addFiles(rawList: FileList | File[] | null): void {
    const incoming = Array.from(rawList || []);
    const next: File[] = [...files];
    const nextRej: RejectedFile[] = [...rejected];
    for (const f of incoming) {
      if (next.some((x) => x.name === f.name && x.size === f.size)) continue;
      const v = validate(f);
      if (v.ok) {
        next.push(f);
      } else {
        if (!nextRej.some((x) => x.name === f.name && x.size === f.size && x.reason === v.reason)) {
          nextRej.push({ name: f.name, size: f.size, reason: v.reason || 'invalid' });
        }
      }
    }
    setFiles(next);
    setRejected(nextRej);
  }

  function removeAt(i: number): void {
    const next = [...files];
    next.splice(i, 1);
    setFiles(next);
  }

  function dismissRejected(i: number): void {
    const next = [...rejected];
    next.splice(i, 1);
    setRejected(next);
  }

  function clearAll(): void {
    setFiles([]);
    setRejected([]);
    if (fileInput) fileInput.value = '';
  }

  function onDragOver(e: DragEvent): void {
    e.preventDefault();
    if (uploadArea) uploadArea.style.background = '#FFFFFF';
  }
  function onDragLeave(): void {
    if (uploadArea) uploadArea.style.background = '';
  }
  function onDrop(e: DragEvent): void {
    e.preventDefault();
    if (uploadArea) uploadArea.style.background = '';
    if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
  }
  function onPick(e: Event): void {
    const t = e.target as HTMLInputElement;
    if (t.files) addFiles(t.files);
    t.value = '';
  }

  function onListClick(e: MouseEvent): void {
    const target = e.target as HTMLElement | null;
    if (!target) return;
    const b = target.closest('button[data-rm]') as HTMLButtonElement | null;
    if (!b) return;
    const i = parseInt(b.getAttribute('data-rm') || '-1', 10);
    if (!Number.isNaN(i) && i >= 0) removeAt(i);
  }
  function onRejectedListClick(e: MouseEvent): void {
    const target = e.target as HTMLElement | null;
    if (!target) return;
    const b = target.closest('button[data-rj]') as HTMLButtonElement | null;
    if (!b) return;
    const i = parseInt(b.getAttribute('data-rj') || '-1', 10);
    if (!Number.isNaN(i) && i >= 0) dismissRejected(i);
  }

  function generateLabel(): string {
    if (files.length === 0) return 'Generate →';
    if (files.length === 1) return 'Generate →';
    return `Generate ${files.length} files →`;
  }

  let canProceed = $derived(files.length > 0);
  let nextLabel = $derived(generateLabel());
  let totalBytes = $derived(files.reduce((s, f) => s + f.size, 0));
</script>

<section class="step-pane">
  <div class="mono-tag mb-3">Policy Platform</div>
  <h1 class="font-serif text-[clamp(1.75rem,4vw,2.5rem)] font-normal leading-[1.1] tracking-[-0.02em] mb-8">
    Agentic <span class="em">Policy</span> Generator
  </h1>

  <div
    id="upload-area"
    class="border border-[#111111] p-16 text-center mb-8 bg-[#F7F5F2] cursor-pointer hover:bg-white transition-all"
    onclick={() => fileInput?.click()}
    ondragover={onDragOver}
    ondragleave={onDragLeave}
    ondrop={onDrop}
    role="button"
    tabindex="0"
  >
    <div class="font-mono text-3xl text-[rgba(17,17,17,0.7)] mb-4">[ ]</div>
    <div class="font-serif text-xl text-[rgba(17,17,17,0.58)] mb-2">
      Drop one or more policy files here, or click to browse
    </div>
    <div class="font-mono text-[11px] tracking-[0.1em] uppercase text-[rgba(17,17,17,0.7)]">
      Supports .pdf, .docx, .txt — Up to 50 MB per file
    </div>
    <input
      bind:this={fileInput}
      type="file"
      id="file-input"
      class="hidden"
      accept=".pdf,.docx,.txt"
      multiple
      onchange={onPick}
    />
  </div>

  <div
    id="file-queue"
    class="border border-[#111111] mb-8 bg-[#F7F5F2]"
    class:hidden={files.length === 0}
  >
    <div class="flex items-center justify-between px-5 py-4 border-b border-[rgb(216,212,212)]">
      <div class="mono-label">SELECTED FILES · <span id="queue-count">{files.length}</span></div>
      <button class="mono-underline" onclick={clearAll}>Clear all</button>
    </div>
    <ul id="file-list" class="divide-y divide-[rgb(216,212,212)]" onclick={onListClick}>
      {#each files as f, i (f.name + i)}
        <li class="flex items-center gap-4 px-5 py-3">
          <div class="font-mono text-[13px] font-medium flex-1 truncate" title={f.name}>
            {f.name}
          </div>
          <div class="font-mono text-[10px] text-[rgba(17,17,17,0.7)]">{fmtMB(f.size)}</div>
          <button class="mono-underline" data-rm={i}>Remove</button>
        </li>
      {/each}
    </ul>
    <div class="px-5 py-3 border-t border-[rgb(216,212,212)]">
      <div class="font-mono text-[11px] text-[rgba(17,17,17,0.7)]">
        {files.length} {files.length === 1 ? 'file' : 'files'} · {fmtMB(totalBytes)} total
      </div>
    </div>
  </div>

  <div
    id="rejected-block"
    class="border border-[#D62828] mb-8 bg-white"
    class:hidden={rejected.length === 0}
  >
    <div class="flex items-center justify-between px-5 py-4 border-b border-[#D62828]">
      <div class="mono-label" style="color: var(--accent);">
        REJECTED · <span id="rejected-count">{rejected.length}</span>
      </div>
      <button class="mono-underline" onclick={clearAll}>Clear rejected</button>
    </div>
    <ul id="rejected-list" class="divide-y divide-[rgb(216,212,212)]" onclick={onRejectedListClick}>
      {#each rejected as r, i (r.name + i)}
        <li class="flex items-center gap-4 px-5 py-3" style="border-left: 3px solid var(--accent);">
          <div class="font-mono text-[13px] font-medium flex-1 truncate" style="color: var(--accent);" title={r.name}>
            {r.name}
          </div>
          <div class="font-mono text-[10px]" style="color: var(--accent);">{r.reason}</div>
          <button class="mono-underline" data-rj={i} style="color: var(--accent);">Dismiss</button>
        </li>
      {/each}
    </ul>
  </div>

  <div class="flex justify-end gap-3 mb-8">
    <button
      id="next-1"
      class="pill-btn"
      disabled={!canProceed}
      onclick={onProceed}
    >{nextLabel}</button>
  </div>
</section>
