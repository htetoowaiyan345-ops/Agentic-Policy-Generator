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
          'horizontalLine',
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
      // `editor.getData()` round-trips.
      htmlSupport: {
        allow: [
          {
            name: /^(p|h[1-6]|div|figure|table|thead|tbody|tr|td|th|figcaption|span)$/,
            attributes: {
              'data-slot': /.*/,
              'data-slot-bar': /.*/
            },
            classes: {
              'ck-widget': true,
              'ck-widget_selected': true
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

        editor.model.document.on('change:data', () => {
          if (readonly) return;
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
    // Avoid resetting the document if the new HTML matches what we already
    // have — prevents cursor jumps and undo stack wipes.
    if (next === lastAppliedInitial) return;
    lastAppliedInitial = next;
    editorInstance.setData(next);
  }

  /** Expose the editor handle to host (ReviewEditor) for source-view reading. */
  export function getEditor(): DecoupledEditor | null {
    return editorInstance;
  }
</script>

<div class="ck-host" data-slot={slot} data-variant={variant}>
  <div bind:this={toolbarElement} class="ck-host-toolbar"></div>
  <div bind:this={contentElement} class="ck-host-content"></div>
</div>

<style>
  .ck-host {
    display: flex;
    flex-direction: column;
  }
  .ck-host-toolbar:empty {
    display: none;
  }
</style>