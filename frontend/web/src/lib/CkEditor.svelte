<script lang="ts">
  /**
   * CkEditor.svelte
   *
   * CKEditor 5 (DecoupledEditor) wrapper for the Step 03 unified document
   * editor. Replaces the former TipTap-based `SlotEditor.svelte`.
   *
   * Why DecoupledEditor (not ClassicEditor):
   *   - We render the toolbar into a separate host element that we control
   *     absolutely. Combined with `position: sticky` on `.ck.ck-toolbar` in
   *     `app.css`, the toolbar floats at the top of the viewport while the
   *     user scrolls within the editor surface, and stops floating once the
   *     editable area scrolls off-screen — exactly the "sticky until the
   *     end of the editable text field" behaviour requested.
   *   - ClassicEditor hard-codes its toolbar inside the editor root, which
   *     makes that level of CSS control awkward.
   *
   * Slot-bar / slot attributes:
   *   The pipeline emits HTML with `<p data-slot="N">` and
   *   `<p data-slot-bar="bottom|space|functional-area">` placeholders. We
   *   preserve them through the editor's data pipeline via the
   *   `GeneralHtmlSupport` plugin (`htmlSupport.allow`). CKEditor 5 treats
   *   these as HTML-allowed attributes on paragraphs/headings/tables; the
   *   `htmlToLines` consumer filters them back out before persisting.
   *
   * Mount policy:
   *   Mounts ONCE per content element. The editor instance is created
   *   inside a `$effect` whose only reactive dependencies are the bound
   *   `toolbarElement` / `contentElement` divs. `initialHtml` is read via
   *   `untrack` so the effect does NOT re-fire when the prop changes —
   *   subsequent prop updates are routed through `setHtml()` instead.
   *   This is the fix for the "duplicate editor on file switch" bug.
   */
  import { untrack } from 'svelte';
  import {
    DecoupledEditor,
    Essentials,
    Paragraph,
    Heading,
    Bold,
    Italic,
    Underline,
    Strikethrough,
    Font,
    FontFamily,
    FontSize,
    FontColor,
    Alignment,
    List,
    TodoList,
    Link,
    Autoformat,
    BlockQuote,
    HorizontalLine,
    Table,
    TableToolbar,
    GeneralHtmlSupport,
    Undo,
    Indent,
    IndentBlock,
    RemoveFormat,
    type EditorConfig
  } from 'ckeditor5';
  import type { SlotKind } from './types';

  // CSS is imported here so the consuming app does not need to know about it.
  import 'ckeditor5/ckeditor5.css';

  interface Props {
    slot: SlotKind;
    variant?: 'unified' | 'card';
    initialHtml?: string;
    readonly?: boolean;
    onChange?: (slot: SlotKind, html: string, text: string) => void;
  }
  let {
    slot,
    variant = 'card',
    initialHtml = '',
    readonly = false,
    onChange
  }: Props = $props();

  let toolbarElement: HTMLDivElement | undefined = $state();
  let contentElement: HTMLDivElement | undefined = $state();
  let editorInstance = $state<DecoupledEditor | null>(null);
  // Track last applied initial HTML so setData is only called when it
  // actually changes — avoids re-rendering the whole document on every
  // reactive update.
  let lastAppliedInitial = '';

  // Force the editor to remount on every fresh page load. The schema
  // extension (which is needed for toolbar formatting to survive
  // setData/getData) only runs ONCE at editor mount time. If the
  // browser keeps the editor instance alive across HMR updates or
  // page navigations, the new schema is never applied. Adding a
  // unique key per session guarantees the editor is destroyed and
  // recreated — so the user's CKEditor always has the latest schema.
  const mountKey = $state(Symbol().toString());

  $effect(() => {
    // Only depend on the bound DOM elements, NOT on initialHtml.
    if (!toolbarElement || !contentElement) return;

    let destroyed = false;
    let inflight: DecoupledEditor | null = null;

    // Snapshot initialHtml without subscribing to it.
    const initialHtmlSnapshot = untrack(() => initialHtml);

    const config: EditorConfig = {
      plugins: [
        Essentials,
        Paragraph,
        Heading,
        Bold,
        Italic,
        Underline,
        Strikethrough,
        Font,
        FontFamily,
        FontSize,
        FontColor,
        Alignment,
        List,
        TodoList,
        Link,
        Autoformat,
        BlockQuote,
        HorizontalLine,
        Table,
        TableToolbar,
        GeneralHtmlSupport,
        Undo,
        Indent,
        IndentBlock,
        RemoveFormat
      ],
      toolbar: {
        items: [
          'undo',
          'redo',
          '|',
          'fontFamily',
          'fontSize',
          'fontColor',
          '|',
          'bold',
          'italic',
          'underline',
          'strikethrough',
          'removeFormat',
          '|',
          'heading',
          '|',
          'bulletedList',
          'numberedList',
          'todoList',
          '|',
          'alignment',
          '|',
          'link',
          'blockQuote',
          'insertTable',
          '|',
          'outdent',
          'indent'
        ],
        shouldNotGroupWhenFull: true
      },
      fontFamily: {
        options: [
          'Inter',
          'Calibri',
          'Times New Roman',
          'Arial',
          'Helvetica',
          'Georgia',
          'Courier New'
        ],
        supportAllValues: true
      },
      fontSize: {
        options: [10, 11, 12, 13, 14, 'default', 16, 18, 20, 24, 28, 32, 36, 48],
        supportAllValues: true
      },
      heading: {
        options: [
          { model: 'paragraph', title: 'Paragraph', class: 'ck-heading_paragraph' },
          { model: 'heading1', view: 'h1', title: 'Heading 1', class: 'ck-heading_heading1' },
          { model: 'heading2', view: 'h2', title: 'Heading 2', class: 'ck-heading_heading2' },
          { model: 'heading3', view: 'h3', title: 'Heading 3', class: 'ck-heading_heading3' }
        ]
      },
      link: {
        addTargetToExternalLinks: true
      },
      table: {
        contentToolbar: ['tableColumn', 'tableRow', 'mergeTableCells']
      },
      // Preserve the slot-anchor and slot-bar attributes injected by the
      // pipeline so the CSS rules in `app.css` for `p[data-slot]`,
      // `p[data-slot-bar="bottom"]`, etc. keep matching through
      // `editor.getData()` round-trips. `hr` is included so user-inserted
      // dividers (CKEditor 'horizontalLine' toolbar button) survive the
      // round-trip through `setData`/`getData`. `style` is included so
      // toolbar-driven inline formatting (fontColor, fontBackgroundColor,
      // fontFamily, fontSize) survives the round-trip.
      //
      // The element name regex now covers every toolbar-relevant tag so
      // the editor does NOT silently strip user-applied formatting when
      // it re-parses the HTML:
      //   - inline:           strong, em, u, s, b, i, sub, sup, code, mark
      //   - structure:        blockquote, a
      //   - lists (defensive): ul, ol, li  (toolbar exposes bulletedList,
      //                       numberedList, todoList)
      //   - tables:           table, thead, tbody, tr, td, th, caption
      //   - data-slot bus:    p, h1..h6, hr, div, figure, figcaption, span
      // `href` is needed so Link-plugin output survives the round-trip,
      // `colspan`/`rowspan` keep table-merge formatting, `class` lets
      // CKEditor widget markers (todo-list class, ck-widget) survive.
      htmlSupport: {
        allow: [
          {
            name: /^(p|h[1-6]|hr|div|figure|figcaption|span|strong|em|u|s|b|i|sub|sup|code|mark|blockquote|a|ul|ol|li|table|thead|tbody|tr|td|th|caption)$/,
            attributes: {
              'data-slot': /.*/,
              'data-slot-bar': /.*/,
              'style': /.*/,
              'href': /.*/,
              'lang': /.*/,
              'colspan': /.*/,
              'rowspan': /.*/
            },
            classes: {
              'ck-widget': true,
              'ck-widget_selected': true,
              'todo-list': true,
              'todo-list__label': true,
              'ck-list-bogus-paragraph': true
            }
          }
        ]
      },
      initialData: initialHtmlSnapshot && initialHtmlSnapshot.length > 0 ? initialHtmlSnapshot : '<p></p>',
      // CKEditor 5 v40+ requires explicit license acknowledgement. We use
      // the GPL key because this project is AGPLv3 — see LICENCE.md for
      // the user-facing copy. If you later buy a commercial licence,
      // swap this for the licence key string CKSource issues.
      licenseKey: 'GPL'
    };

    DecoupledEditor.create(contentElement, config)
      .then((editor) => {
        if (destroyed) {
          editor.destroy();
          return;
        }
        inflight = editor;
        editorInstance = editor;
        lastAppliedInitial = initialHtmlSnapshot;

        // Extend the model's schema so user-inserted horizontalLine (the
        // `horizontalLine` toolbar button → `<hr>`) and inline coloring
        // (CKEditor uses `<span style="color: ...">`) survive the
        // `setData`/`getData` round-trip with our custom `data-slot`
        // attribute and inline `style` attributes. Without this, the
        // plugins would DROP these attributes when re-parsing the HTML,
        // causing dividers to lose their slot context and toolbar color
        // changes to be silently stripped.
        try {
          editor.model.schema.extend('horizontalLine', {
            allowAttributes: ['data-slot', 'data-slot-bar']
          });
        } catch (e) {
          // horizontalLine not registered yet — ignore
        }
        // Generously allow style + data-slot on the standard inline
        // elements that CKEditor uses for text formatting. The schema
        // needs this so the model retains these attributes when the
        // HTML is re-parsed.
        for (const name of ['$text', 'paragraph', 'span', 'heading']) {
          try {
            editor.model.schema.extend(name, {
              allowAttributes: ['data-slot', 'data-slot-bar', 'style']
            });
          } catch (e) {
            // Some schemas may not support attribute extension — ignore
          }
        }
        // Allow `style` and `data-slot` on every formatting tag that the
        // toolbar produces, so user-applied bold/italic/underline/colour
        // survive the setData/getData round-trip. Without these extends
        // the model strips the attributes on re-parse and the saved
        // lines_json drifts toward plain text.
        for (const name of [
          'strong', 'em', 'u', 's', 'b', 'i',
          'sub', 'sup', 'code', 'mark',
          'blockquote', 'a'
        ]) {
          try {
            editor.model.schema.extend(name, {
              allowAttributes: ['data-slot', 'data-slot-bar', 'style', 'href']
            });
          } catch (e) {
            // Some plugins may not register every name — ignore
          }
        }
        // Tables: keep colspan/rowspan so table-merge formatting
        // survives the round-trip.
        for (const name of ['table', 'thead', 'tbody', 'tr', 'td', 'th', 'caption']) {
          try {
            editor.model.schema.extend(name, {
              allowAttributes: ['data-slot', 'data-slot-bar', 'style', 'colspan', 'rowspan']
            });
          } catch (e) {
            // Some plugins may not register every name — ignore
          }
        }
        // Lists: keep class attribute intact (todo-list uses class).
        for (const name of ['ul', 'ol', 'li']) {
          try {
            editor.model.schema.extend(name, {
              allowAttributes: ['data-slot', 'data-slot-bar', 'style', 'class']
            });
          } catch (e) {
            // Some plugins may not register every name — ignore
          }
        }

        // Move the toolbar into our external sticky container.
        // We move only the toolbar element (not the .ck-editor__top
        // wrapper), and we remove the now-empty .ck-editor__top from the
        // editor root so the user sees ONE toolbar, not two.
        const editorRoot = editor.ui.getEditableElement()?.closest('.ck-editor');
        const toolbarEl = editor.ui.view.toolbar.element;
        if (toolbarElement && toolbarEl) {
          toolbarElement.appendChild(toolbarEl);
        }
        if (editorRoot) {
          const topPanel = editorRoot.querySelector('.ck-editor__top');
          if (topPanel && topPanel.parentElement === editorRoot) {
            topPanel.remove();
          }
        }

        if (readonly) editor.enableReadOnlyMode('readonly-slot');

        // Re-entrancy guard for the live-DOM refresh path. When the
        // user inserts a toolbar item (bulletedList, numberedList,
        // todoList, blockQuote, horizontalLine, headings, etc.),
        // CKEditor 5 emits a trailing cursor-host node (empty
        // <li> or empty <p>) so the cursor has somewhere to land.
        // Without an immediate DOM refresh, the empty line lingers
        // in the visible editor until something else triggers a
        // setData() round-trip (e.g. version switch). The fix is
        // to schedule a `setData` on the next microtask — by then
        // CKEditor's `change:data` model-mutation has finished and
        // the DOM is in a stable state, so setData actually replaces
        // the markup. The guard prevents a `change:data` recursion
        // while the refresh's own mutation is in flight.
        let refreshScheduled = false;

          editor.model.document.on('change:data', () => {
          if (readonly) return;
          // Always re-read the editor's current data so we capture
          // the cleaned payload (in the same microtask the refresh
          // runs).
          if (!refreshScheduled && editorInstance) {
            const rawHtml = editor.getData();
            const cleaned = stripStrayCursorHosts(rawHtml);
            if (cleaned !== rawHtml) {
              refreshScheduled = true;
              // Defer the actual setData so we leave the current
              // `change:data` invocation cleanly. CKEditor 5's
              // document-change phase completes synchronously;
              // running setData in a microtask re-enters with a
              // stable DOM and produces a full replacement.
              queueMicrotask(() => {
                refreshScheduled = false;
                try {
                  if (editorInstance) {
                    editorInstance.setData(cleaned);
                  }
                } catch {
                  /* fall back to leaving DOM as-is */
                }
              });
            }
          }
          const html = editor.getData();
          const text = editor.ui.getEditableElement()?.textContent ?? '';
          onChange?.(slot, html, text);
        });
      })
      .catch((err) => {
        // Surface mount failure — Vite HMR and an empty placeholder both
        // log this; the host (ReviewEditor) keeps the last-good content.
        console.error('[CkEditor] mount failed', err);
      });

    return () => {
      destroyed = true;
      if (inflight) {
        inflight.destroy();
        inflight = null;
      }
      editorInstance = null;
      // Clear the host elements so the next mount starts clean.
      if (toolbarElement) toolbarElement.innerHTML = '';
      if (contentElement) contentElement.innerHTML = '';
    };
  });

  /** Imperative content replacement (used by ReviewEditor on version jump
   *  and file dropdown change). Uses `setData` rather than recreating the
   *  editor — recreating caused the duplicate-editor-on-file-switch bug. */
  export function setHtml(html: string): void {
    if (!editorInstance) return;
    const next = html && html.length > 0 ? html : '<p></p>';
    // Fix G4: strip stray cursor-host nodes BEFORE setData so
    // CKEditor 5 doesn't render an extra empty paragraph / white space
    // after every heading (`<h1>`/`<h2>`/`<h3>`) when reloading
    // content via buildUnifiedInitialHtml on a version switch.
    // Without this, the user sees "extra line/white space under
    // each heading" until the next change:data fires (which
    // self-heals via the same function). We now self-heal at the
    // setData boundary instead.
    const cleaned = stripStrayCursorHosts(next);
    // Always call setData, even when the new HTML appears identical to
    // the last applied HTML. Earlier we short-circuited identical
    // payloads to avoid cursor jumps, but that caused onClick version
    // switches to silently no-op: the editor DOM kept the previous
    // version's content (e.g. v2) when the click target was v1.
    // CKEditor 5's setData is internally idempotent for identical
    // inputs, so we can safely remove the wrapper deduplication.
    lastAppliedInitial = cleaned;
    editorInstance.setData(cleaned);
  }

  /** Expose the editor handle to host (ReviewEditor) for source-view reading. */
  export function getEditor(): DecoupledEditor | null {
    return editorInstance;
  }

  /** Strip stray cursor-host nodes that CKEditor 5 emits after
   *  toolbar inserts. Operates on the editor's HTML round-trip in
   *  four passes:
   *   (A) Drop empty `<p>` directly inside any `<li>` or any `<h*>`.
   *       CKEditor 5 v48 emits lists as
   *       `<ul><li><p data-slot="N">item</p></li>
   *        <li><p data-slot="N">&nbsp;</p></li></ul>`
   *       (the trailing empty bullet the user sees). After Pass A,
   *       that pattern becomes `<ul><li>item</li><li></li></ul>`.
   *       Headings show the same shape when toggled from a normal
   *       paragraph: CKEditor leaves the original `<p>` empty as
   //       a cursor host before lifting the content into the new
   //       `<h*>`. After Pass A, that `<p>` is gone.
   *   (B) Tail-trim empty `<li>` from EVERY `<ul>`/`<ol>` (recursive).
   *       After Pass A, empty `<li>` literally has no text content.
   *   (C) Tail-trim trailing empty `<p>` / `<h1>`...`<h6>` at the
   *       TOP LEVEL of the document (cursor hosts after `<hr>`,
   *       `</blockquote>`, headings, etc.).
   *   (D) Leading-trim empty `<p>` / `<h*>` at the TOP LEVEL of the
    *       document. When toggling Paragraph → Heading 1/2/3, CKEditor 5
    *       leaves an empty `<p data-slot="N">` BEFORE the heading
    *       (the original paragraph element, now empty). Without
    *       this, that empty paragraph survives in `lines_json`,
    //       round-trips into the editor on every version switch as
    //       a stray blank line ABOVE the heading, and persists
    //       until the next toolbar round-trip.
    *   (E) Unwrap `<h1>`…`<h6>` / `<blockquote>` trapped inside a
    *       single-item `<ul>`/`<ol>`. When the user toggles a heading
    *       on a list item, CKEditor 5 can leave the bogus wrapper
    *       `<ul><li><h1>X</h1></li></ul>` alongside a standalone
    *       `<h1>X</h1>`. Without this, the text appears twice
    *       (once per row) after every version switch.
    *  Nodes with TEXT (even one character) are left alone.
    *  Returns the input unchanged when nothing changed. */
  function stripStrayCursorHosts(input: string): string {
    if (!input || input.indexOf('<') < 0) return input;
    let touched = false;
    const tpl = document.createElement('template');
    tpl.innerHTML = input;
    const root = tpl.content;

    // (A) Drop empty `<p>` directly inside any `<li>` or any `<h*>`.
    //     After this, those containers carry no text content so
    //     Pass B can tail-trim them.
    const headContainers = Array.from(
      root.querySelectorAll('li, h1, h2, h3, h4, h5, h6')
    );
    for (const container of headContainers) {
      const firstChild = container.firstElementChild;
      if (!firstChild || firstChild.tagName.toLowerCase() !== 'p') continue;
      const txt = (firstChild.textContent || '')
        .replace(/\u00A0/g, ' ')
        .replace(/\s+/g, '')
        .trim();
      if (txt) continue;
      // Drop the empty <p> only when the container has no
      // additional text-bearing children. Defensive against
      // markup shapes we haven't seen.
      let hasTextSibling = false;
      for (const child of Array.from(container.children)) {
        if (child === firstChild) continue;
        hasTextSibling = true;
        break;
      }
      if (hasTextSibling) continue;
      container.removeChild(firstChild);
      touched = true;
    }

    // (B) Tail-trim empty `<li>` from EVERY `<ul>`/`<ol>`.
    const allLists = Array.from(root.querySelectorAll('ul, ol'));
    for (const list of allLists) {
      while (list.lastElementChild) {
        const last = list.lastElementChild;
        if (last.tagName.toLowerCase() !== 'li') break;
        const txt = (last.textContent || '')
          .replace(/\u00A0/g, ' ')
          .replace(/\s+/g, '')
          .trim();
        if (txt) break;
        list.removeChild(last);
        touched = true;
      }
    }

    // (C) Trailing empty <p> / <h*> at TOP LEVEL of the document.
    //     Inner <p> nested in <blockquote> / <li> / <td> was
    //     handled in Pass A.
    while (root.lastChild) {
      const last = root.lastChild as ChildNode | null;
      if (!last) break;
      if (last.nodeType === 3 /* TEXT_NODE */) {
        const txt = (last.textContent || '')
          .replace(/\u00A0/g, ' ')
          .replace(/\s+/g, '')
          .trim();
        if (txt) break;
        root.removeChild(last);
        touched = true;
        continue;
      }
      if (last.nodeType !== 1 /* ELEMENT_NODE */) break;
      const el = last as Element;
      const lastTag = el.tagName.toLowerCase();
      if (lastTag !== 'p' && lastTag !== 'h1' && lastTag !== 'h2' && lastTag !== 'h3' &&
          lastTag !== 'h4' && lastTag !== 'h5' && lastTag !== 'h6') {
        break;
      }
      const txt = (el.textContent || '')
        .replace(/\u00A0/g, ' ')
        .replace(/\s+/g, '')
        .trim();
      if (txt) break;
      root.removeChild(el);
      touched = true;
    }

    // (D) Leading-trim empty `<p>` / `<h*>` at TOP LEVEL of the
    //     document. Mirrors Pass C but at the head — handles the
    //     empty `<p>` CKEditor 5 leaves BEFORE a heading when
    //     toggling Paragraph → Heading 1/2/3. The same scenario
    //     exists for blockquote toggles (an empty `<p>` ahead of
    //     `<blockquote>`). Symmetric to Pass C: same tag-list, same
    //     empty-text definition, opposite direction.
    while (root.firstChild) {
      const first = root.firstChild as ChildNode | null;
      if (!first) break;
      if (first.nodeType === 3 /* TEXT_NODE */) {
        const txt = (first.textContent || '')
          .replace(/\u00A0/g, ' ')
          .replace(/\s+/g, '')
          .trim();
        if (txt) break;
        root.removeChild(first);
        touched = true;
        continue;
      }
      if (first.nodeType !== 1 /* ELEMENT_NODE */) break;
      const el = first as Element;
      const firstTag = el.tagName.toLowerCase();
      if (firstTag !== 'p' && firstTag !== 'h1' && firstTag !== 'h2' && firstTag !== 'h3' &&
          firstTag !== 'h4' && firstTag !== 'h5' && firstTag !== 'h6') {
        break;
      }
      const txt = (el.textContent || '')
        .replace(/\u00A0/g, ' ')
        .replace(/\s+/g, '')
        .trim();
      if (txt) break;
      root.removeChild(el);
      touched = true;
    }

    // (E) Unwrap headings / blockquotes trapped inside a list. When
    //     a user toggles Heading 1/2/3 on a list item, CKEditor 5
    //     can produce `<ul><li><h1>X</h1></li></ul>` — a bogus list
    //     wrapper around a heading. Without this pass the heading
    //     appears both inside the list row AND as a standalone row,
    //     producing visible text duplication after every version
    //     switch. For single-item lists whose only child is a
    //     heading or blockquote, we replace the list with the
    //     heading directly.
    const listsToUnwrap = Array.from(root.querySelectorAll('ul, ol'));
    for (const list of listsToUnwrap) {
      const lis = Array.from(list.children).filter(
        (c) => c.tagName.toLowerCase() === 'li'
      );
      if (lis.length !== 1) continue;
      const li = lis[0];
      const block = Array.from(li.children).find(
        (c) => /^(h[1-6]|blockquote)$/i.test(c.tagName)
      );
      if (!block) continue;
      if (li.children.length !== 1) continue;
      // Replace the bogus <ul>/<ol> with the trapped heading.
      list.parentNode!.replaceChild(block, list);
      touched = true;
    }

    if (!touched) return input;
    return tpl.innerHTML;
  }
</script>

{#key mountKey}
<div class="ck-host" data-slot={slot} data-variant={variant}>
  <div bind:this={toolbarElement} class="ck-host-toolbar"></div>
  <div bind:this={contentElement} class="ck-host-content"></div>
</div>
{/key}

<style>
  .ck-host {
    display: flex;
    flex-direction: column;
  }
  .ck-host-toolbar:empty {
    display: none;
  }
</style>