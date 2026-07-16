<script lang="ts">
  import type { PreviewLine } from './types';
  import { escapeHtml } from './escape';

  interface Props {
    lines: PreviewLine[];
    readonly?: boolean;
    onChange?: (lines: PreviewLine[]) => void;
  }
  let {
    lines = $bindable(),
    readonly = false,
    onChange
  }: Props = $props();

  // ---------------------------- HISTORY -------------------------------
  const MAX_HISTORY = 50;
  let history = $state<PreviewLine[][]>([]);
  let redoStack = $state<PreviewLine[][]>([]);

  function snapshot(arr: PreviewLine[]): PreviewLine[] {
    // PreviewLine is plain JSON data (arrays + primitives only); JSON
    // round-trip is safer than structuredClone on Svelte 5 reactive proxies.
    return JSON.parse(JSON.stringify(arr));
  }

  function pushHistory(prev: PreviewLine[]): void {
    history.push(snapshot(prev));
    if (history.length > MAX_HISTORY) history.shift();
    redoStack = [];
  }

  function emitChange(next: PreviewLine[]): void {
    lines = next;
    if (onChange) onChange(next);
  }

  // Reset clears undo/redo history. Used by parent's "Discard edits" button.
  export function reset(): void {
    history = [];
    redoStack = [];
  }

  // Undo/redo exposed via export so the parent can call them from action-bar
  // buttons (`editorRef.undo()` / `editorRef.redo()`).
  export function undo(): void {
    const prev = history.pop();
    if (!prev) return;
    redoStack.push(snapshot(lines));
    if (redoStack.length > MAX_HISTORY) redoStack.shift();
    emitChange(prev);
  }

  export function redo(): void {
    const next = redoStack.pop();
    if (!next) return;
    history.push(snapshot(lines));
    if (history.length > MAX_HISTORY) history.shift();
    emitChange(next);
  }

  export function debug(): { historyLen: number; redoLen: number; readonly: boolean } {
    return {
      historyLen: history.length,
      redoLen: redoStack.length,
      readonly
    };
  }

  // ----------------------- KEYBOARD SHORTCUTS ------------------------
  function onKeyDown(e: KeyboardEvent): void {
    if (readonly) return;
    const meta = (e.ctrlKey || e.metaKey) && !e.altKey;
    if (!meta) return;
    const key = (e.key || '').toLowerCase();
    if (key === 'z' && !e.shiftKey) {
      e.preventDefault();
      undo();
    } else if (key === 'y' || (key === 'z' && e.shiftKey)) {
      e.preventDefault();
      redo();
    }
  }

  // ----------------------- TEXT EDIT COMMIT --------------------------
  function commitP(idx: number, newText: string): void {
    const prev = lines[idx];
    if (!prev || prev[0] !== 'p' || prev[1] === newText) return;
    pushHistory(lines);
    const next = lines.slice();
    next[idx] = ['p', newText];
    emitChange(next);
    console.log('[ReviewEditor] commitP', idx, '→', newText);
  }

  function commitTValue(idx: number, ri: number, ci: number, newText: string): void {
    const cur = lines[idx];
    if (!cur || cur[0] !== 't') return;
    const rows = cur[1] as string[][];
    if (!rows[ri] || rows[ri][ci] === newText) return;
    pushHistory(lines);
    const newRows = rows.map((r, i) =>
      i === ri ? r.map((c, j) => (j === ci ? newText : c)) : r
    );
    const next = lines.slice();
    next[idx] = ['t', newRows];
    emitChange(next);
    console.log('[ReviewEditor] commitTValue', idx, ri, ci, '→', newText);
  }

  // ----------------------- STRUCTURAL EDITS --------------------------
  function addParagraphAfter(idx: number): void {
    pushHistory(lines);
    const next = lines.slice();
    next.splice(idx + 1, 0, ['p', '']);
    emitChange(next);
  }

  /**
   * Remove the paragraph at idx. If it's the last paragraph, replace
   * it with a single empty editable paragraph so the preview stays
   * well-formed (lines[] invariant: never empty).
   */
  function removeParagraph(idx: number): void {
    const cur = lines[idx];
    if (!cur || cur[0] !== 'p') return;
    pushHistory(lines);
    const next = lines.slice();
    if (next.length === 1) {
      // Last paragraph - replace with empty placeholder.
      next[0] = ['p', ''];
      emitChange(next);
      return;
    }
    next.splice(idx, 1);
    emitChange(next);
  }

  function addRow(idx: number): void {
    const cur = lines[idx];
    if (!cur || cur[0] !== 't') return;
    const rows = cur[1] as string[][];
    const template = rows[rows.length - 1];
    const width = template ? template.length : 1;
    pushHistory(lines);
    const newRows = rows.slice();
    newRows.push(new Array(width).fill(''));
    const next = lines.slice();
    next[idx] = ['t', newRows];
    emitChange(next);
  }

  /**
   * Remove row `ri` from table at idx. If it's the last row, replace
   * it with a single empty row of the same column count.
   */
  function removeRow(idx: number, ri: number): void {
    const cur = lines[idx];
    if (!cur || cur[0] !== 't') return;
    const rows = cur[1] as string[][];
    pushHistory(lines);
    let newRows: string[][];
    if (rows.length <= 1) {
      const width = rows[0] ? rows[0].length : 1;
      newRows = [new Array(width).fill('')];
    } else {
      newRows = rows.filter((_, i) => i !== ri);
    }
    const next = lines.slice();
    next[idx] = ['t', newRows];
    emitChange(next);
  }

  /**
   * Delete the entire table at idx. If it's the only block, leave an
   * empty paragraph in its place so the preview isn't blank.
   */
  function removeTable(idx: number): void {
    const cur = lines[idx];
    if (!cur || cur[0] !== 't') return;
    pushHistory(lines);
    const next = lines.slice();
    next.splice(idx, 1);
    if (next.length === 0) {
      next.push(['p', '']);
    }
    emitChange(next);
  }

  // ------------------------- DETECT LABEL-VALUE -----------------------
  const LABEL_PREFIX_RE = /^([A-Z][A-Za-z0-9 \-/().,&]+?):\s*(.*)$/;
  function splitLabelValue(text: string): { label: string; value: string } | null {
    const m = text.match(LABEL_PREFIX_RE);
    if (!m) return null;
    return { label: m[1].trim(), value: m[2] };
  }
</script>

<svelte:window onkeydown={onKeyDown} />

{#if !readonly && lines && lines.length > 0}
  <div class="re-banner mono-tag">
    EDIT MODE — edits are kept until you click "Save as new version".
  </div>
{:else if !readonly}
  <div class="re-banner mono-tag">
    EDIT MODE — preview content not loaded yet. Reopen or pick another version.
  </div>
{/if}

<div class="re-list">
  {#each lines as item, i (i + ':' + item[0])}
    {#if item[0] === 'p' && typeof item[1] === 'string'}
      {@const lv = splitLabelValue(item[1])}
      <div class="re-row" data-kind="p">
        {#if lv}
          <p class="brain-p">
            <span class="brain-p-label re-label-fixed">{lv.label}:</span>
            {#if readonly}
              <span class="re-value-static">{lv.value}</span>
            {:else}
              <input
                type="text"
                class="re-input re-value"
                value={lv.value}
                oninput={(e) => {
                  const v = (e.target as HTMLInputElement).value;
                  commitP(i, `${lv.label}: ${v}`);
                }}
              />
            {/if}
          </p>
        {:else if item[1].startsWith('   ')}
          <p class="brain-p re-list-item">
            {#if readonly}
              <span>{item[1]}</span>
            {:else}
              <textarea
                class="re-textarea"
                rows="3"
                oninput={(e) => commitP(i, (e.target as HTMLTextAreaElement).value)}
              >{item[1]}</textarea>
            {/if}
          </p>
        {:else}
          <p class="brain-p">
            {#if readonly}
              <span>{item[1]}</span>
            {:else}
              <textarea
                class="re-textarea"
                rows="2"
                oninput={(e) => commitP(i, (e.target as HTMLTextAreaElement).value)}
              >{item[1]}</textarea>
            {/if}
          </p>
        {/if}
        {#if !readonly}
          <div class="re-actions">
            {#if item[1].startsWith('   ')}
              <span class="re-tag mono-underline">bulleted paragraph</span>
            {:else if lv}
              <span class="re-tag mono-underline">label-value</span>
            {:else}
              <span class="re-tag mono-underline">paragraph</span>
            {/if}
            <button
              type="button"
              class="re-add-btn mono-underline"
              onclick={() => addParagraphAfter(i)}
            >+ paragraph below</button>
            <button
              type="button"
              class="re-del-btn mono-underline"
              onclick={() => removeParagraph(i)}
            >× remove</button>
          </div>
        {/if}
      </div>
    {:else if item[0] === 't' && Array.isArray(item[1])}
      <div class="re-row" data-kind="t">
        <div class="brain-table-wrap">
          <table class="brain-table">
            {#if item[1][0] && item[1][0].length}
              <thead>
                <tr>
                  {#each item[1][0] as h, hi (hi)}
                    <th>{h == null ? '' : String(h)}</th>
                  {/each}
                </tr>
              </thead>
            {/if}
            {#if item[1].length > 1}
              <tbody>
                {#each item[1].slice(1) as r, ri (ri)}
                  <tr>
                    {#each r as c, ci (ci)}
                      <td>
                        {#if readonly}
                          {c == null ? '' : String(c)}
                        {:else}
                          <input
                            type="text"
                            class="re-cell-input"
                            value={c == null ? '' : String(c)}
                            oninput={(e) => {
                              const v = (e.target as HTMLInputElement).value;
                              commitTValue(i, ri + 1, ci, v);
                            }}
                          />
                        {/if}
                      </td>
                    {/each}
                  </tr>
                {/each}
              </tbody>
            {/if}
          </table>
        </div>
        {#if !readonly}
          <div class="re-actions">
            <button
              type="button"
              class="re-add-btn mono-underline"
              onclick={() => addRow(i)}
            >+ row</button>
            <button
              type="button"
              class="re-del-btn mono-underline"
              onclick={() => removeRow(i, (item[1] as string[][]).length - 1)}
            >× last row</button>
            <button
              type="button"
              class="re-del-btn mono-underline"
              onclick={() => removeTable(i)}
            >× delete table</button>
          </div>
        {/if}
      </div>
    {/if}
  {/each}
</div>
