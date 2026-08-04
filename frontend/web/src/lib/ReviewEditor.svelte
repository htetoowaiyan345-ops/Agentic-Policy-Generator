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
  import { tick } from 'svelte';
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
    // Sync the CKEditor 5 HTML so the visible editor reflects the
    // restored state. Without this, Ctrl+Z would update the `lines`
    // state but leave the editor's HTML unchanged, making the undo
    // appear to "do nothing" to the user.
    if (ckEditorRef) {
      // Set suppressNextChange BEFORE setHtml because setHtml fires
      // change:data synchronously, which would otherwise pollute the
      // history stack with the restored state.
      suppressNextChange = true;
      ckEditorRef.setHtml(buildUnifiedInitialHtml(prev));
    }
    emitChange(prev);
  }
  export function redo(): void {
    const next = redoStack.pop();
    if (!next) return;
    history.push(snapshot(lines));
    if (history.length > MAX_HISTORY) history.shift();
    // Sync the CKEditor 5 HTML so the visible editor reflects the
    // restored state. Without this, Ctrl+Y/Ctrl+Shift+Z would update
    // the `lines` state but leave the editor's HTML unchanged.
    if (ckEditorRef) {
      // Set suppressNextChange BEFORE setHtml because setHtml fires
      // change:data synchronously, which would otherwise pollute the
      // history stack with the restored state.
      suppressNextChange = true;
      ckEditorRef.setHtml(buildUnifiedInitialHtml(next));
    }
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
      | { kind: 't'; slot: SlotKind; rows: { text: string; html: string }[][] }
      | { kind: 'divider'; slot: SlotKind };
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
      } else if (kind === 'divider') {
        // User-inserted <hr> via CKEditor toolbar. Carry the slot so
        // `htmlToLines` can place the divider back in the same slot when
        // round-tripping through a version switch. Without this, dividers
        // were silently dropped from the editor on version switch.
        const divPayload = payload as { slot?: SlotKind } | undefined;
        const slot = ((divPayload?.slot ?? 0) as SlotKind) || 0;
        buckets.push({ kind: 'divider', slot });
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
      } else if (b.kind === 'divider') {
        // User-inserted <hr> via CKEditor toolbar. Emit as <hr data-slot="X">
        // so `htmlToLines` can place the divider back in the same slot when
        // round-tripping through a version switch. The `.filter()` in
        // htmlToLines only drops `<hr data-slot-bar="...">`, not
        // `<hr data-slot="X">`, so this survives the round-trip.
        parts.push(`<hr data-slot="${slot}">`);
        prevSlotSawParagraph = true;
      } else {
        const rich = b.rich;
        const text = rich.text || '';
        const rawHtml = (rich.html || '').trim();
        // Detect rows whose `html` carries a block-level element
        // other than `<p>`: lists, headings, blockquotes, etc.
        // These MUST NOT be wrapped in `<p>` (invalid HTML; CKEditor
        // 5 lifts them out via model normalisation and the empty
        // outer `<p>` would render as a stray blank line plus
        // duplicate the block content). We re-emit each shape as a
        // standalone block, with `data-slot` lifted onto the first
        // element so `htmlToLines` can map it back on round-trip.
        const listMatch = /^<(ul|ol)[\s>]/i.exec(rawHtml);
        const headingMatch = /^(h[1-6])[\s>]/i.exec(rawHtml);
        const blockquoteMatch = /^<blockquote[\s>]/i.exec(rawHtml);
        if (listMatch) {
          // Lift `data-slot="N"` onto the list's first element so the
          // reverse walk in `htmlToLines` puts the row back in the
          // correct slot when the editor's `getData` fires after.
          const lifted = rawHtml.replace(
            /^<(ul|ol)/i,
            (_match, tag) => `<${tag} data-slot="${slot}"`
          );
          parts.push(lifted);
        } else if (headingMatch) {
          // Headings: re-emit as-is with data-slot on the heading
          // element so subsequent htmlToLines walks retrieve the
          // right slot. Wrapping `<h*>` inside `<p>` would cause
          // CKEditor 5 to split them on parse, producing duplicates.
          const lifted = rawHtml.replace(
            /^<h[1-6]/i,
            (match) => `${match} data-slot="${slot}"`
          );
          parts.push(lifted);
        } else if (blockquoteMatch) {
          // Blockquotes (single-line wrapping preserved) — emit as-is
          // with data-slot on the `<blockquote>` element.
          const lifted = rawHtml.replace(
            /^<blockquote/i,
            (match) => `${match} data-slot="${slot}"`
          );
          parts.push(lifted);
        } else {
          // Detect legacy rows where a block-level element (heading,
          // blockquote) was wrapped inside `<p>...</p>` from earlier
          // broken round-trips. We must lift the block back out so
          // CKEditor 5 doesn't split it via model normalisation and
          // leave an empty trailing paragraph (a duplicate that
          // persists through version switches).
          let unwrapped = rawHtml;
          const legacyWrap = /^<p[^>]*>([\s\S]*)<\/p>$/i.exec(rawHtml);
          let lifted = false;
          if (legacyWrap && /^<(h[1-6]|blockquote|ul|ol)[\s>]/i.test(legacyWrap[1])) {
            unwrapped = legacyWrap[1];
            lifted = true;
          }
          const inner =
            unwrapped.length > 0 && !lifted
              ? unwrapped.replace(/^<p[^>]*>([\s\S]*)<\/p>$/i, '$1')
              : unwrapped;
          if (lifted && /^<(h[1-6])[\s>]/i.test(unwrapped)) {
            // Re-emit the heading standalone with data-slot lifted
            // onto it.
            const tag = /^<(h[1-6])/i.exec(unwrapped)![1];
            parts.push(
              `<${tag} data-slot="${slot}">${unwrapped.replace(/^<h[1-6][^>]*>/i, '').replace(/<\/h[1-6]>$/i, '')}</${tag}>`
            );
          } else if (lifted && /^<blockquote[\s>]/i.test(unwrapped)) {
            parts.push(
              `<blockquote data-slot="${slot}">${unwrapped.replace(/^<blockquote[^>]*>/i, '').replace(/<\/blockquote>$/i, '')}</blockquote>`
            );
          } else {
            parts.push(`<p data-slot="${slot}">${inner}</p>`);
          }
        }

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
        'p, h1, h2, h3, h4, h5, h6, li, ul, ol, blockquote, pre, hr, table, thead, tbody, tr, th, td'
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
      // Drop CKEditor 5's empty bogus paragraph that follows a list
      // (bulletedList / numberedList / todoList). CKEditor appends an
      // empty `<p class="ck-list-bogus-paragraph">` after each list to
      // host the cursor; without this filter the empty `<p>` is
      // emitted in addition to the `<li>`, producing a duplicate empty
      // paragraph in `lines_json` on every toolbar click. The class is
      // already preserved through `setData`/`getData` by the
      // `htmlSupport.allow.classes` allow-list in `CkEditor.svelte`.
      // Blockquote is intentionally NOT covered here (per user scope).
      if (
        tag === 'p' &&
        el.classList.contains('ck-list-bogus-paragraph')
      ) {
        return false;
      }
      // Drop a `<p>` whose closest list/block-container ancestor is a
      // `<li>` — because the FIRST sibling `<li>` already emits the
      // WHOLE `<ul>`/`<ol>` (including its `<p>` inner content) as one
      // `lines_json` row. Without this filter the inner `<p>` would
      // also be emitted as its own row, duplicating the list text in
      // `lines_json` (visible as duplicates after toolbar clicks AND
      // after version-switch round-trips). Scope is narrow: only
      // `<p>` inside `<li>` is filtered. `<p>` inside `<blockquote>`,
      // top-level paragraphs, and the trailing `ck-list-bogus-
      // paragraph` are unaffected (handled above).
      if (tag === 'p') {
        const inListItem = el.closest('li');
        if (inListItem) {
          return false;
        }
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
      // Skip <ul>/<ol> container elements themselves — their first
      // <li> child already emits the whole list as one row below. If
      // we let `<ul>` fall through to the regular wrap, it would emit
      // a SEPARATE row that wraps the same `<li>` text — duplicating
      // the list in `lines_json` and producing visible duplicates
      // after every toolbar click AND after every version switch
      // round-trip. Blockquote / paragraph fallback paths are
      // untouched.
      if (tag === 'ul' || tag === 'ol') {
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
      // Group consecutive <li> siblings under one <ul>/<ol> into a
      // single `lines_json` row. The FIRST <li> of a list emits the
      // WHOLE list once (its outerHTML), carrying the slot attribute
      // lifted onto the list element so `buildUnifiedInitialHtml` can
      // re-emit the bullets verbatim when `setData` round-trips.
      // Subsequent <li> siblings AND nested <li>s (inside another
      // <li>'s subtree) skip — they would otherwise duplicate the
      // list markup because the OUTER `<li>`'s row already contains
      // the entire `<ul>`/`<ol>` HTML recursively.
      if (tag === 'li') {
        // Skip nested <li> — the OUTER `<li>`'s row already
        // captured the entire subtree via parentList.outerHTML.
        if (el.closest('li') !== el) {
          flushTable();
          continue;
        }
        const listParent = el.parentElement;
        if (listParent) {
          const pTag = listParent.tagName.toLowerCase();
          if (pTag === 'ul' || pTag === 'ol') {
            // Check whether a previous <li> already emitted this list.
            let prev = el.previousElementSibling;
            let prevIsLi = false;
            while (prev) {
              if (prev.tagName.toLowerCase() === 'li') {
                prevIsLi = true;
                break;
              }
              prev = prev.previousElementSibling;
            }
            if (prevIsLi) {
              flushTable();
              continue;
            }
            // First <li> — capture the whole list once.
            // Strip TRAILING empty/whitespace-only <li>s that CKEditor
            // 5 emits at end-of-list to host the cursor; otherwise the
            // editor renders a stray empty bullet (•) after the real
            // items, and the .docx writer emits an empty `<w:p>` with
            // `<w:numPr>`. "Empty" includes <li> with only `<br>` or
            // `&nbsp;` — CKEditor 5 uses those to keep the cursor
            // visible inside the empty item; from the user's POV
            // they're empty. Inner empty `<li>`s (between siblings)
            // are preserved; only the TAIL is trimmed. If every
            // `<li>` was empty, we skip emitting a row entirely so
            // we don't write a stray empty bullet line to
            // lines_json.
            let listHtml = listParent.outerHTML;
            try {
              const clone = document.createElement('div');
              clone.innerHTML = listHtml;
              const clonedList = clone.firstElementChild;
              if (clonedList) {
                while (clonedList.lastElementChild) {
                  const lastChild = clonedList.lastElementChild;
                  if (lastChild.tagName.toLowerCase() !== 'li') break;
                  // Empty-or-whitespace-only detector: trims ASCII
                  // whitespace, also normalises &nbsp; (U+00A0) so a
                  // `<li>&nbsp;</li>` is treated as empty. `<br>`
                  // counted as empty (it carries no text). An `<li>`
                  // containing a `<br>` and whitespace still counts
                  // as "empty" because the user can't see the
                  // difference and Word would render it as a blank
                  // bullet.
                  const rawText = (lastChild.textContent || '');
                  const trimmed = rawText.replace(/\u00A0/g, ' ').trim();
                  if (trimmed) break;
                  clonedList.removeChild(lastChild);
                }
                if (!clonedList.firstElementChild) {
                  flushTable();
                  continue;
                }
                listHtml = clonedList.outerHTML;
              }
            } catch {
              /* on DOM parse failure, keep the original listHtml */
            }
            const cleanListText = (listParent.textContent || '')
              .replace(/\s+$/g, '')
              .trim();
            out.push([
              'p',
              {
                slot,
                text: cleanListText,
                html: listHtml,
                footnotes: []
              }
            ]);
            flushTable();
            continue;
          }
        }
        // <li> not under a real list — fall through to the regular
        // wrap-below path.
      }
      flushTable();
      const text = (el.textContent || '').replace(/\s+$/g, '');
      const cleanText = text.replace(/<br\s*\/?>/gi, '').trim();
      if (!cleanText && tag !== 'p' && tag !== 'blockquote') continue;
      const innerHtml = el.innerHTML;
      // Pick the wrap tag. Preserve the original block-level tag for
      // <p>, headings, and <blockquote> so the rich writer can pick up
      // the blockquote / heading context. Other tags default to <p>.
      const wrapTag = ((): string => {
        if (tag === 'p') return 'p';
        if (/^h[1-6]$/.test(tag)) return tag;
        if (tag === 'blockquote') return 'blockquote';
        return 'p';
      })();
      // Lift `data-slot="N"` onto the wrapping element so the saved
      // `lines_json` row carries the slot. Without this the slot is
      // embedded only in a parent `<p>` that may not survive
      // CKEditor 5's normalisation, and the row would round-trip to
      // slot 0 on the next version switch.
      let wrapped: string;
      if (wrapTag === 'p') {
        if (directSlotAttr != null) {
          wrapped = `<p data-slot="${slot}">${innerHtml}</p>`;
        } else {
          wrapped = `<p>${innerHtml}</p>`;
        }
      } else if (directSlotAttr != null) {
        wrapped = `<${wrapTag} data-slot="${slot}">${innerHtml}</${wrapTag}>`;
      } else {
        wrapped = `<${wrapTag}>${innerHtml}</${wrapTag}>`;
      }
      out.push(['p', { slot, text: cleanText, html: wrapped, footnotes: [] }]);
    }
    flushTable();
    // Drop TRAILING empty `<p>` row(s) — CKEditor 5 emits them as
    // cursor hosts after lists, blockquotes, headings, and
    // dividers. They surface in the editor as a stray empty cursor
    // line below the toolbar-inserted item (the user sees it BEFORE
    // any version switch — once they switch, `applyExternalContent`
    // re-renders from `lines_json` and the autosave round-trip has
    // already produced the same shape, so the line stays). Without
    // this post-pass the user sees an empty line under their
    // list/quote/heading that disappears only on a fresh page load.
    while (out.length > 0) {
      const last = out[out.length - 1];
      if (!Array.isArray(last) || last[0] !== 'p') break;
      const payload = last[1] as { text?: string; html?: string };
      const txt = (payload?.text || '').replace(/\u00A0/g, ' ').trim();
      if (txt) break;
      out.pop();
    }
    // Drop LEADING empty `<p>` row(s) for the same reason — CKEditor
    // 5 emits an empty `<p data-slot="N">` BEFORE a heading or
    // blockquote when the user toggles a normal paragraph into a
    // heading (the previous paragraph element is left empty as a
    // cursor host). Without this, the user sees a stray blank line
    // ABOVE their heading that persists through version switches.
    while (out.length > 0) {
      const first = out[0];
      if (!Array.isArray(first) || first[0] !== 'p') break;
      const payload = first[1] as { text?: string; html?: string };
      const txt = (payload?.text || '').replace(/\u00A0/g, ' ').trim();
      if (txt) break;
      out.shift();
    }
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
   *  Sets `suppressNextChange = true` for the entire synchronous burst of
   *  `change:data` events that CKEditor 5 fires per `setData` call. The
   *  flag is released on the next microtask via `tick()` so ALL echoing
   *  back to the host is suppressed — not just the first one. Without
   *  this, a multi-event setData (e.g. multiple model mutations from
   *  schema conversion) leaves the editor's DOM reflecting the new V# but
   *  `editableLines` overwritten by a stale serialisation — making the
   *  previous version's content appear in the new V#'s view. */
  export function applyExternalContent(newLines: PreviewLine[]): void {
    history = [];
    redoStack = [];
    suppressNextChange = true;
    if (ckEditorRef) ckEditorRef.setHtml(buildUnifiedInitialHtml(newLines));
    else initialHtml = buildUnifiedInitialHtml(newLines);
    // Release suppression on the next microtask so all synchronous
    // change:data events from setData are dropped.
    tick().then(() => {
      suppressNextChange = false;
    });
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
    // [DIAG] start — diagnostic logging only, no logic change
    if (readonly) return;
    if (suppressNextChange) {
      // [DIAG] programmatic load (version switch) — change suppressed
      console.log('[DIAG-RE] suppressed change: editor emitted HTML length=' + html.length);
      // DO NOT release the flag here — `applyExternalContent` releases it
      // on the next microtask via `tick().then()`. CKEditor 5 may fire
      // multiple `change:data` events per setData; we want to drop ALL
      // events in the synchronous burst, not just the first.
      return;
    }
    pushHistory(lines);
    const next = htmlToLines(html, 0, text);
    // [DIAG] log what came in and what came out
    console.log('[DIAG-RE] onSlotChange', {
      editor_html_length: html.length,
      editor_html_has_strong: html.includes('<strong>'),
      editor_html_has_italic: html.includes('<em>') || html.includes('<i>'),
      editor_html_has_color: html.includes('color:'),
      editor_html_has_hr: html.includes('<hr'),
      htmlToLines_total: next.length,
      htmlToLines_dividers: next.filter((l) => Array.isArray(l) && l[0] === 'divider').length,
      htmlToLines_with_strong: next.filter((l) => Array.isArray(l) && l[0] === 'p' && (l[1]?.html?.includes('<strong>') || l[1]?.html?.includes('<b>'))).length,
      htmlToLines_with_italic: next.filter((l) => Array.isArray(l) && l[0] === 'p' && (l[1]?.html?.includes('<em>') || l[1]?.html?.includes('<i>'))).length,
      htmlToLines_with_color: next.filter((l) => Array.isArray(l) && l[0] === 'p' && l[1]?.html?.includes('color:')).length,
      htmlToLines_with_hr_in_p: next.filter((l) => Array.isArray(l) && l[0] === 'p' && l[1]?.html?.includes('<hr')).length
    });
    // [DIAG] end
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