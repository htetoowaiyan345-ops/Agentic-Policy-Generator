<script lang="ts">
  /**
   * ReviewEditor.svelte
   *
   * Step 03 Word-style editor. One CKEditor 5 instance via
   * `<CkEditor variant="unified">` covering the whole Brain document
   * top-to-bottom. CKEditor 5 renders its toolbar into the slot exposed
   * by `CkEditor.svelte` (`.ck-host-toolbar`), and that toolbar is
   * `position: sticky; top: 0` in `app.css` — it floats at the top of
   * the viewport while the user scrolls through the document and
   * unsticks naturally once the editable area scrolls off-screen.
   *
   * Each paragraph in `lines_json` carries a `slot` id (1..15) plus
   * `text` and `html`. `buildUnifiedInitialHtml` emits `<p data-slot="N">`
   * for each block. CKEditor 5's `GeneralHtmlSupport` plugin preserves
   * these attributes (plus `data-slot-bar` for the dividers) through
   * `setData` round-trips.
   *
   * Save round-trip:
   *   - `htmlToLines` walks the editor HTML payload, reads `data-slot`
   *     per paragraph (or `closest('[data-slot]')` for nested tags),
   *     emits `['p', {slot, text, html, footnotes?}]`.
   *   - When a block has no `data-slot`, inherit the slot of the
   *     preceding block so newly typed paragraphs keep section context.
   *
   * Mount policy: async (CKEditor 5 is ESM and loads its CSS / chunk
   * graph asynchronously). One-time mount. Version jumps use
   * `applyExternalContent(lines)` -> `ckEditorRef.setHtml(html)`.
   */
  import type { Editor as DecoupledEditor } from 'ckeditor5';
  import type { PreviewLine, SlotKind, RichParagraph } from './types';
  import { normalisePreviewLine } from './types';
  import CkEditor from './CkEditor.svelte';

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
    return JSON.parse(JSON.stringify(arr));
  }
  function pushHistory(prev: PreviewLine[]): void {
    history.push(snapshot(prev));
    if (history.length > MAX_HISTORY) history.shift();
    redoStack = [];
  }
  function emitChange(next: PreviewLine[]): void {
    lines = next;
    onChange?.(next);
  }

  export function reset(): void {
    history = [];
    redoStack = [];
  }
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

  function escapeHtml(s: string): string {
    return (s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  /** Virtual-slot id used for text-pattern classification. Decoupled
   *  from the backend's Brain slot id (which is 0 for all paragraphs
   *  on runs that the RAG chunker collapses). Used only to detect
   *  section boundaries within Step 03's UI; never persisted. */
  type VirtualSlot = -1 | 1 | 2 | 3 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14;

  /** Step 03 UI-only text classifier. Detects Brain-style section
   *  boundaries from paragraph text patterns:
   *   - Metadata sub-groups:
   *       slot 1:  Type, Policy Title, Policy Number
   *       slot 2:  Applicable Sector, Functional Area, Applies to
   *       slot 3:  Brief Description, Effective Date, Approved by,
   *                Prepared by, Responsible Function, Supersedes,
   *                Last Reviewed, Reason for Policy
   *     Different virtual slots per sub-group → bar emitted between
   *     sub-groups (e.g. under `Functional Area(s):` and `Applies to:`).
   *   - `[English]` page-break marker → virtual slot 1.
   *   - Section headings (INTRODUCTION, POLICY STATEMENT, DEFINITIONS,
   *     RELATED POLICIES..., HISTORY) → virtual slots 5 / 6 / 12 / 13 / 14.
   *   - Sub-headings (1. Purpose, 2. Scope, 3. Exclusions, 4. Award
   *     Structure, Policy Review Note) → virtual slots 7 / 8 / 9 / 10 / 11.
   *   - Unclassified text → -1 (no boundary emitted).
   *
   *  This is a UI-only heuristic — `lines_json` is never modified. The
   *  backend's slot routing is untouched. */
  const METADATA_GROUP_A_RE = /^(Type|Policy Title|Policy Number)\s*:/i;
  const METADATA_GROUP_B_RE = /^(Applicable Sector(?:\(s\))?|Functional Area(?:\(s\))?|Applies to)\s*:/i;
  const METADATA_GROUP_C_RE = /^(Brief Description|Effective Date(?:\/Period)?|Approved by|Prepared by|Responsible Function(?:s| Officer\(s\))?|Supersedes|Last Reviewed|Reason for Policy)\s*:/i;
  const HEADING_LITERALS: Set<string> = new Set([
    'INTRODUCTION',
    'POLICY STATEMENT',
    'DEFINITIONS',
    'RELATED POLICIES, PROCEDURES, FORMS, GUIDELINES & OTHER RESOURCES',
    'HISTORY'
  ]);

  function classifyText(text: string): VirtualSlot {
    const t = (text || '').trim();
    if (!t) return -1;
    if (/^\[English\]$/i.test(t)) return 1;
    if (METADATA_GROUP_A_RE.test(t)) return 1;
    if (METADATA_GROUP_B_RE.test(t)) return 2;
    if (METADATA_GROUP_C_RE.test(t)) return 3;
    const upper = t.toUpperCase();
    if (HEADING_LITERALS.has(upper)) {
      if (upper === 'INTRODUCTION') return 5;
      if (upper === 'POLICY STATEMENT') return 6;
      if (upper === 'DEFINITIONS') return 12;
      if (upper.startsWith('RELATED POLICIES')) return 13;
      if (upper === 'HISTORY') return 14;
      return 1;
    }
    if (/^1\.\s*Purpose\b/i.test(t)) return 7;
    if (/^2\.\s*Scope/i.test(t)) return 8;
    if (/^3\.\s*Exclusions\b/i.test(t)) return 9;
    if (/^4\.\s*Award Structure/i.test(t)) return 10;
    if (/^Policy Review Note\b/i.test(t)) return 11;
    return -1;
  }

  /** Build the unified initial HTML for the editor. Each paragraph /
   *  table cell carries its slot id as `data-slot`. Between adjacent
   *  non-zero slot transitions, an empty `<p data-slot-bar="bottom">`
   *  placeholder paragraph is injected — the CSS draws a 2 px solid
   *  black border below it with 28 px white space above and below.
   *
   *  Step 03 UI-only addition: when the backend's slot id is 0 (the
   *  RAG chunker collapses content into slot 0 for these runs), the
   *  text-pattern classifier above detects Brain-style section
   *  boundaries from paragraph text. A bar is emitted between each
   *  virtual-slot transition so the document reads like the brain
   *  framework template, even when `lines_json` carries no real slot ids.
   *
   *  No trailing bar is emitted after the last paragraph (per spec).
   *  The slot-bar paragraph carries no text; `htmlToLines` filters it
   *  out before round-tripping to `lines_json`. The user can still
   *  click + select + delete the bar in CKEditor 5. */
  function buildUnifiedInitialHtml(input: PreviewLine[] | null | undefined): string {
    type Bucket =
      | { kind: 'p'; rich: RichParagraph; slot: SlotKind }
      | { kind: 't'; slot: SlotKind; rows: { text: string; html: string }[][] };
    const buckets: Bucket[] = [];
    for (const ln of input || []) {
      const norm = normalisePreviewLine(ln);
      if (!norm) continue;
      const [kind, payload] = norm;
      if (kind === 'p') {
        const rich = payload as RichParagraph;
        const slot = (rich.slot ?? 0) as SlotKind;
        buckets.push({ kind: 'p', rich, slot });
      } else if (kind === 't') {
        const tbl = payload as { slot: SlotKind; rows: { text: string; html: string }[][] };
        const slot = ((tbl.slot ?? 0) as SlotKind) || 0;
        buckets.push({ kind: 't', slot, rows: tbl.rows || [] });
      }
    }
    if (buckets.length === 0) return '';
    const parts: string[] = [];
    let prevSlot: SlotKind | null = null;
    let prevSlotSawParagraph = false;
    let prevVirtualSlot: VirtualSlot = -1;
    let prevWasEnglish = false;

    for (let idx = 0; idx < buckets.length; idx++) {
      const b = buckets[idx];
      const slot = b.slot;

      // Backend-driven bar: closes the previous non-zero slot when the
      // chunker routes slot ids correctly. Defensive — most runs have
      // slot 0 throughout, so this branch rarely fires.
      if (
        prevSlot !== null &&
        prevSlot !== 0 &&
        slot !== prevSlot &&
        prevSlotSawParagraph
      ) {
        parts.push(`<p data-slot="${prevSlot}" data-slot-bar="bottom"></p>`);
      }

      // Determine the virtual slot of THIS bucket (text-pattern based).
      const bucketText = b.kind === 'p' ? (b.rich.text || '') : '';
      const isEnglish = /^\[English\]$/i.test(bucketText.trim());
      const vslot = classifyText(bucketText);

      // Bar under [English]: emit a bar when the previous paragraph is
      // the [English] page-break marker, regardless of vslot transition.
      // Sync prevVirtualSlot with vslot so the regular transition check
      // below does NOT also fire (preventing double divider emission).
      if (prevWasEnglish) {
        parts.push(`<p data-slot-bar="bottom"></p>`);
        if (vslot !== -1) prevVirtualSlot = vslot;
        prevWasEnglish = false;
      }

      // UI-driven spacer between consecutive classified paragraphs.
      // Only emitted when at least one side is a heading or sub-heading
      // (virtual slot ≥ 5). Metadata slots (1–3) produce no transition
      // bar or spacer — all bars come from explicit text matches below
      // and the [English] handler above.
      if (
        prevVirtualSlot !== -1 &&
        vslot !== -1 &&
        vslot !== prevVirtualSlot &&
        (prevVirtualSlot >= 5 || vslot >= 5)
      ) {
        parts.push(`<p data-slot-bar="space"></p>`);
      }
      if (vslot !== -1) prevVirtualSlot = vslot;
      prevWasEnglish = isEnglish;

      if (b.kind === 't') {
        const headerRow = b.rows[0] || [];
        const bodyRows = b.rows.slice(1);
        const thead = headerRow.length
          ? `<thead><tr>${headerRow
              .map((c) => `<th>${c.html || escapeHtml(c.text || '')}</th>`)
              .join('')}</tr></thead>`
          : '';
        const tbody = bodyRows.length
          ? `<tbody>${bodyRows
              .map((r) => `<tr>${r.map((c) => `<td>${c.html || escapeHtml(c.text || '')}</td>`).join('')}</tr>`)
              .join('')}</tbody>`
          : '';
        parts.push(`<p data-slot="${slot}"></p><table data-slot="${slot}">${thead}${tbody}</table>`);
        prevSlotSawParagraph = true;
      } else {
        const rich = b.rich;
        const text = rich.text || '';
        const inner =
          rich.html && rich.html.trim().length > 0
            ? rich.html.replace(/^<p[^>]*>([\s\S]*)<\/p>$/i, '$1')
            : escapeHtml(text);
        parts.push(`<p data-slot="${slot}">${inner}</p>`);

        if (/^Functional Area(?:\(s\))?\s*:/i.test(text)) {
          parts.push(`<p data-slot-bar="functional-area"></p>`);
        }
        if (/^Applies to\s*:/i.test(text)) {
          parts.push(`<p data-slot-bar="bottom"></p>`);
        }
        if (/^Reason for Policy\s*:/i.test(text)) {
          parts.push(`<p data-slot-bar="space"></p>`);
        }

        prevSlotSawParagraph = true;
      }
      prevSlot = slot;
    }
    // No trailing bar — by user spec, the last slot has no bar below it.
    return parts.join('');
  }

  /** Build lines_json from a CKEditor 5 HTML payload. Reads `data-slot`
   *  per paragraph (or `closest('[data-slot]')`), inherits slot from
   *  the preceding block when missing. Tables emit ONE row per visible
   *  row, grouped as a `t` payload. Decorative slot-bar paragraphs
   *  (`<p data-slot-bar="bottom">`) and any leftover `<hr
   *  data-slot-bar>` elements are skipped — they are visual dividers,
   *  not part of `lines_json`. */
  function htmlToLines(html: string, fallbackSlot: SlotKind, fallbackText: string): PreviewLine[] {
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    const blocks = Array.from(
      tpl.content.querySelectorAll(
        'p, h1, h2, h3, h4, h5, h6, li, blockquote, pre, hr, table, thead, tbody, tr, th, td'
      )
    ).filter((el) => {
      const tag = el.tagName.toLowerCase();
      // Skip decorative horizontal rules.
      if (tag === 'hr' && el.getAttribute('data-slot-bar') != null) {
        return false;
      }
      // Skip decorative slot-bar paragraphs (carry no text content).
      // Both bar (data-slot-bar="bottom") and spacer (data-slot-bar="space")
      // paragraphs are filtered — they are Step 03 UI only.
      if (tag === 'p' && el.getAttribute('data-slot-bar') != null) {
        return false;
      }
      return true;
    });
    const out: PreviewLine[] = [];
    let lastSlot: SlotKind | null = null;
    let currentTable: { slot: SlotKind; rows: { text: string; html: string }[][] } | null = null;
    function flushTable(): void {
      if (!currentTable) return;
      if (currentTable.rows.length > 0) out.push(['t', currentTable]);
      currentTable = null;
    }

    for (const el of blocks) {
      const tag = el.tagName.toLowerCase();
      const directSlotAttr = el.getAttribute && el.getAttribute('data-slot');
      const slotEl = el.closest('[data-slot]') as HTMLElement | null;
      const slotVal = slotEl?.getAttribute('data-slot');
      const slot: SlotKind = ((): SlotKind => {
        if (directSlotAttr != null) {
          const n = parseInt(directSlotAttr, 10);
          if (Number.isFinite(n)) return n as SlotKind;
        }
        if (slotVal != null) {
          const n = parseInt(slotVal, 10);
          if (Number.isFinite(n)) return n as SlotKind;
        }
        // Stage 1: never let `fallbackSlot=0` (free paragraph) overwrite a
        // non-zero `lastSlot`. User additions inherit the slot of the
        // nearest preceding section so they reach the correct slot in
        // the published .docx instead of being silently dropped into
        // the free-paragraph zone.
        if (lastSlot != null && lastSlot !== 0) return lastSlot;
        if (fallbackSlot !== 0) return fallbackSlot;
        if (lastSlot != null) return lastSlot;
        return 0 as SlotKind;
      })();
      lastSlot = slot;

      if (tag === 'table') {
        flushTable();
        const rows: { text: string; html: string }[][] = [];
        const trs = Array.from(el.querySelectorAll('tr'));
        for (const tr of trs) {
          const row: { text: string; html: string }[] = [];
          for (const cell of Array.from(tr.children)) {
            row.push({ text: (cell.textContent || '').trim(), html: cell.innerHTML });
          }
          rows.push(row);
        }
        out.push(['t', { slot, rows }]);
        currentTable = null;
        continue;
      }
      if (tag === 'thead' || tag === 'tbody' || tag === 'tr' || tag === 'th' || tag === 'td') {
        continue;
      }
      // User-inserted <hr> via CKEditor toolbar → preserve as a divider
      // marker. The renderer detects 'divider' kind entries and inserts
      // a divider paragraph with the appropriate margins. Decorative
      // `<hr data-slot-bar="...">` elements (used by the pipeline's
      // auto-bar injection) are filtered out in the .filter() above.
      if (tag === 'hr') {
        out.push(['divider', { slot }]);
        continue;
      }
      flushTable();
      const text = (el.textContent || '').replace(/\s+$/g, '');
      const cleanText = text.replace(/<br\s*\/?>/gi, '').trim();
      if (!cleanText && tag !== 'p') continue;
      const innerHtml = el.innerHTML;
      const wrapTag = (() => {
        if (tag === 'p') return 'p';
        if (/^h[1-6]$/.test(tag)) return tag;
        return 'p';
      })();
      const wrapped =
        wrapTag === 'p' ? `<p>${innerHtml}</p>` : `<${wrapTag}>${innerHtml}</${wrapTag}>`;
      out.push(['p', { slot, text: cleanText, html: wrapped, footnotes: [] }]);
    }
    flushTable();
    if (out.length === 0) {
      out.push(['p', { slot: fallbackSlot, text: fallbackText, html: `<p>${fallbackText}</p>`, footnotes: [] }]);
    }
    return out;
  }

  // ---------------------------- EXTERNAL CONTENT -----------------------
  let ckEditorRef: { setHtml(html: string): void; getEditor(): DecoupledEditor | null } | null =
    $state(null);

  /** Replace editor content with `newLines`. Used by Review.svelte on
   *  version jump. Resets history so undo doesn't cross version boundaries.
   *  Also sets `suppressNextChange = true` so the CKEditor "content
   *  change" event that fires from `setHtml` is ignored by `onChange`,
   *  preventing a spurious autosave. */
  export function applyExternalContent(newLines: PreviewLine[]): void {
    history = [];
    redoStack = [];
    suppressNextChange = true;
    if (ckEditorRef) ckEditorRef.setHtml(buildUnifiedInitialHtml(newLines));
    else initialHtml = buildUnifiedInitialHtml(newLines);
  }

  /** Initial html used on first mount only. */
  let initialHtml = $state(buildUnifiedInitialHtml(lines));

  /** Set true by `applyExternalContent` so the first `onSlotChange`
   *  triggered by CKEditor's `setHtml` is ignored. Prevents a
   *  programmatic load (V-switch / draft load) from triggering a
   *  spurious autosave on the host. */
  let suppressNextChange = $state(false);

  /** CkEditor calls this on every change. Translate to lines and emit
   *  to the host. */
  function onSlotChange(_slot: SlotKind, html: string, text: string): void {
    if (readonly) return;
    if (suppressNextChange) {
      suppressNextChange = false;
      // Still update internal state, but don't emit to the host so
      // the host's autosave isn't triggered for a programmatic load.
      return;
    }
    pushHistory(lines);
    const next = htmlToLines(html, 0, text);
    emitChange(next);
  }
</script>

<svelte:window onkeydown={onKeyDown} />

<!-- The CkEditor renders its own toolbar into `.ck-host-toolbar`, which
     is `position: sticky; top: 0` in `app.css` so it follows scroll
     until the editable area scrolls off-screen. No extra wrapper needed. -->
<article class="re-document" aria-label="Policy document preview">
  <section class="re-doc-body" aria-label="Document body">
    <CkEditor
      bind:this={ckEditorRef}
      slot={0}
      variant="unified"
      initialHtml={initialHtml}
      onChange={onSlotChange}
    />
  </section>
</article>

<style>
  .re-document {
    background: #fff;
    color: var(--ink);
    font-family: var(--sans);
    font-size: 14px;
    line-height: 1.6;
    border: 1px solid var(--ink);
    margin: 0;
    width: 100%;
    max-width: 816px;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
  }
  .re-doc-body {
    background: #fff;
    color: var(--ink);
    padding: 48px 36px 28px 36px;
    min-height: 50vh;
    font-family: var(--sans);
    font-size: 14px;
    line-height: 1.6;
  }

  /* -------- CKEditor 5 surface -------- */
  .re-doc-body :global(.ck.ck-editor) {
    width: 100%;
  }
  .re-doc-body :global(.ck-editor__main) {
    min-height: 50vh;
  }
  .re-doc-body :global(.ck-content) {
    background: #fff;
    color: var(--ink);
    font-family: var(--sans);
    font-size: 14px;
    line-height: 1.6;
    min-height: 50vh;
  }
  .re-doc-body :global(.ck-content p) { margin: 0 0 0.6em 0; }
  .re-doc-body :global(.ck-content h1) { font-size: 22px; font-weight: 700; margin: 0.5em 0 0.3em 0; }
  .re-doc-body :global(.ck-content h2) { font-size: 18px; font-weight: 700; margin: 0.5em 0 0.3em 0; }
  .re-doc-body :global(.ck-content h3) { font-size: 15px; font-weight: 600; margin: 0.5em 0 0.3em 0; }
  .re-doc-body :global(.ck-content ul),
  .re-doc-body :global(.ck-content ol) { padding-left: 1.4em; margin: 0 0 0.6em 0; }
  .re-doc-body :global(.ck-content ul li),
  .re-doc-body :global(.ck-content ol li) { margin: 0.1em 0; }
  .re-doc-body :global(.ck-content blockquote) {
    border-left: 3px solid var(--ink);
    padding-left: 10px;
    margin: 0.6em 0;
    color: rgba(17, 17, 17, 0.7);
  }
  .re-doc-body :global(.ck-content a) { color: var(--accent); text-decoration: underline; }
  .re-doc-body :global(.ck-content hr) {
    border: none;
    border-top: 1px solid var(--ink);
    margin: 10px 0;
  }
  .re-doc-body :global(.ck-content table) {
    border-collapse: collapse;
    margin: 8px 0;
    width: 100%;
  }
  .re-doc-body :global(.ck-content table td),
  .re-doc-body :global(.ck-content table th) {
    border: 1px solid var(--ink);
    padding: 6px 8px;
    vertical-align: top;
  }
  .re-doc-body :global(.ck-content table th) {
    background: var(--cream);
  }

  /* -------- Slot-bar visuals (HTML-preserved attrs) -------- */
  .re-doc-body :global(.ck-content p[data-slot-bar="bottom"]) {
    border: 0;
    border-bottom: 2px solid #000;
    height: 2px;
    min-height: 2px;
    margin: 10px 0;
    padding: 0;
    cursor: pointer;
  }
  .re-doc-body :global(.ck-content p[data-slot-bar="bottom"]:hover) {
    border-bottom-color: var(--accent);
  }
  .re-doc-body :global(.ck-content p[data-slot-bar="space"]) {
    border: 0;
    height: 0;
    min-height: 0;
    margin: 10px 0;
    padding: 0;
    cursor: default;
  }
  .re-doc-body :global(.ck-content p[data-slot-bar="functional-area"]) {
    border: 0;
    border-bottom: 2px solid #000;
    height: 2px;
    min-height: 2px;
    margin: 10px 0;
    padding: 0;
    cursor: pointer;
  }
  .re-doc-body :global(.ck-content p[data-slot-bar="functional-area"]:hover) {
    border-bottom-color: var(--accent);
  }
</style>