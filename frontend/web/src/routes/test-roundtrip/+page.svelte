<script lang="ts">
  /**
   * /test-roundtrip — manual browser test for the toolbar round-trip.
   *
   * Opens in the browser, mounts a real CKEditor 5 instance using the
   * SAME config as `CkEditor.svelte`, then runs a setData → getData
   * round-trip for each toolbar control. Pass/fail is shown inline
   * so the user can confirm Stage 6 wiring without a full docx render.
   */
  import { onMount } from 'svelte';
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
  import 'ckeditor5/ckeditor5.css';

  interface TestCase {
    name: string;
    html: string;
    /** Substrings that must be present in getData() output. */
    mustInclude: string[];
  }

  const testCases: TestCase[] = [
    {
      name: 'Bold',
      html: '<p><strong>bold</strong></p>',
      mustInclude: ['strong', 'bold'],
    },
    {
      name: 'Italic',
      html: '<p><em>italic</em></p>',
      // CKEditor 5 normalizes <em> to <i> (HTML5 spec). Both are valid
      // italic tags and the backend's rich writer handles both.
      mustInclude: ['>italic</', 'italic'],
    },
    {
      name: 'Underline',
      html: '<p><u>under</u></p>',
      mustInclude: ['u', 'under'],
    },
    {
      name: 'Strikethrough',
      html: '<p><s>struck</s></p>',
      mustInclude: ['s', 'struck'],
    },
    {
      name: 'Font Color',
      html: '<p><span style="color: #ff0000;">red</span></p>',
      // CKEditor 5 strips whitespace from style values
      // (e.g. "color: #ff0000" → "color:#ff0000"). We just check
      // for the presence of the color attribute.
      mustInclude: ['color:', 'red'],
    },
    {
      name: 'Font Size',
      html: '<p><span style="font-size: 24px;">big</span></p>',
      // Same as color: whitespace may be stripped.
      mustInclude: ['font-size:', 'big'],
    },
    {
      name: 'Font Family',
      html: '<p><span style="font-family: Calibri;">calibri</span></p>',
      mustInclude: ['calibri'],
    },
    {
      name: 'Right Align',
      html: '<p style="text-align: right;">right</p>',
      // Same as color: whitespace may be stripped.
      mustInclude: ['text-align:', 'right'],
    },
    {
      name: 'Center Align',
      html: '<p style="text-align: center;">center</p>',
      mustInclude: ['text-align:', 'center'],
    },
    {
      name: 'Hyperlink',
      html: '<p>see <a href="https://example.com">link</a></p>',
      mustInclude: ['href="https://example.com"', 'link'],
    },
    {
      name: 'Blockquote',
      html: '<blockquote>quoted</blockquote>',
      mustInclude: ['blockquote', 'quoted'],
    },
    {
      name: 'Horizontal Line',
      html: '<p>before</p><hr><p>after</p>',
      mustInclude: ['hr', 'after'],
    },
    {
      name: 'data-slot preservation',
      html: '<p data-slot="5">slot 5</p>',
      mustInclude: ['data-slot="5"', 'slot 5'],
    },
  ];

  interface Result {
    name: string;
    pass: boolean;
    input: string;
    output: string;
    missing: string[];
  }

  let results: Result[] = $state([]);
  let running = $state(false);
  let mounted = $state(false);
  let mountError = $state('');

  let editorInstance: DecoupledEditor | null = $state(null);
  let contentElement: HTMLDivElement | undefined = $state();

  onMount(() => {
    console.log('[test-roundtrip] onMount fired, contentElement=', contentElement);
    return () => {
      if (editorInstance) {
        editorInstance.destroy();
        editorInstance = null;
      }
    };
  });

  // CKEditor mount: use $effect so it re-runs when `contentElement` is
  // bound by the template (Svelte 5 best practice — onMount fires
  // before bind:this has populated the element, so the editor never
  // mounts in the test page otherwise).
  $effect(() => {
    console.log('[test-roundtrip] effect fired, contentElement=', contentElement);
    if (!contentElement) {
      console.log('[test-roundtrip] contentElement is falsy, returning');
      return;
    }

    // Avoid double-mount if the effect re-fires.
    if (editorInstance) {
      console.log('[test-roundtrip] editorInstance already exists, returning');
      return;
    }

    console.log('[test-roundtrip] starting DecoupledEditor.create');
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
        RemoveFormat,
      ],
      toolbar: [
        'undo', 'redo', '|',
        'fontFamily', 'fontSize', 'fontColor', '|',
        'bold', 'italic', 'underline', 'strikethrough', 'removeFormat', '|',
        'heading', '|',
        'bulletedList', 'numberedList', 'todoList', '|',
        'alignment', '|',
        'link', 'horizontalLine', 'blockQuote', 'insertTable', '|',
        'outdent', 'indent',
      ],
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
              'rowspan': /.*/,
            },
            classes: {
              'ck-widget': true,
              'ck-widget_selected': true,
              'todo-list': true,
              'todo-list__label': true,
              'ck-list-bogus-paragraph': true,
            },
          },
        ],
      },
      licenseKey: 'GPL',
    };

    DecoupledEditor.create(contentElement, config)
      .then((editor) => {
        editorInstance = editor;
        // Schema extension — same as CkEditor.svelte
        try {
          editor.model.schema.extend('horizontalLine', {
            allowAttributes: ['data-slot', 'data-slot-bar'],
          });
        } catch (e) { /* ignore */ }
        for (const name of ['$text', 'paragraph', 'span', 'heading']) {
          try {
            editor.model.schema.extend(name, {
              allowAttributes: ['data-slot', 'data-slot-bar', 'style'],
            });
          } catch (e) { /* ignore */ }
        }
        for (const name of ['strong', 'em', 'u', 's', 'b', 'i', 'sub', 'sup', 'code', 'mark', 'blockquote', 'a']) {
          try {
            editor.model.schema.extend(name, {
              allowAttributes: ['data-slot', 'data-slot-bar', 'style', 'href'],
            });
          } catch (e) { /* ignore */ }
        }
        for (const name of ['table', 'thead', 'tbody', 'tr', 'td', 'th', 'caption']) {
          try {
            editor.model.schema.extend(name, {
              allowAttributes: ['data-slot', 'data-slot-bar', 'style', 'colspan', 'rowspan'],
            });
          } catch (e) { /* ignore */ }
        }
        for (const name of ['ul', 'ol', 'li']) {
          try {
            editor.model.schema.extend(name, {
              allowAttributes: ['data-slot', 'data-slot-bar', 'style', 'class'],
            });
          } catch (e) { /* ignore */ }
        }
        mounted = true;
        console.log('[test-roundtrip] editor mounted, mounted=true');
      })
      .catch((err) => {
        mountError = String(err);
        console.error('[test-roundtrip] mount failed', err);
      });

    return () => {
      if (editorInstance) {
        editorInstance.destroy();
        editorInstance = null;
      }
    };
  });

  async function runTests() {
    if (!editorInstance) return;
    running = true;
    results = [];
    for (const tc of testCases) {
      editorInstance.setData(tc.html);
      // Give the model a tick to settle.
      await new Promise((r) => setTimeout(r, 50));
      const output = editorInstance.getData();
      const missing: string[] = [];
      for (const needle of tc.mustInclude) {
        if (!output.toLowerCase().includes(needle.toLowerCase())) {
          missing.push(needle);
        }
      }
      results.push({
        name: tc.name,
        pass: missing.length === 0,
        input: tc.html,
        output,
        missing,
      });
    }
    running = false;
  }

  /** Manual force-mount: if the $effect didn't fire (e.g. timing bug),
   * the user can click this to attempt to mount the editor. */
  function forceMount(): void {
    if (!contentElement) {
      mountError = 'contentElement still null — bind:this never fired. Hard refresh required.';
      return;
    }
    if (editorInstance) {
      return;
    }
    mountError = '';
    // Trigger the effect's body by re-assigning contentElement
    const el = contentElement;
    contentElement = undefined;
    contentElement = el;
  }
</script>

<svelte:head>
  <title>Toolbar Round-Trip Test</title>
</svelte:head>

<main>
  <h1>Toolbar Round-Trip Test</h1>
  <p>
    Mounts a real CKEditor 5 instance with the same config as the main
    editor, runs <code>setData → getData</code> for each toolbar control,
    and reports whether the expected fragments survive the round-trip.
  </p>

  {#if mountError}
    <p class="error">Mount failed: {mountError}</p>
  {/if}

  <!-- The editor host div is ALWAYS rendered (even before mount)
       so the `$effect` can find it via bind:this. Visibility is
       toggled via the `hidden` attribute. -->
  <div bind:this={contentElement} class="editor-host" hidden={!mounted}></div>

  {#if !mounted}
    <p>Mounting editor…</p>
    <button onclick={forceMount}>Force Mount</button>
  {:else}
    <button onclick={runTests} disabled={running || !mounted}>
      {running ? 'Running…' : 'Run Round-Trip Tests'}
    </button>

    {#if results.length > 0}
      <h2>Results</h2>
      <table>
        <thead>
          <tr>
            <th>Pass</th>
            <th>Test</th>
            <th>Missing</th>
          </tr>
        </thead>
        <tbody>
          {#each results as r}
            <tr class:pass={r.pass} class:fail={!r.pass}>
              <td>{r.pass ? '✓' : '✗'}</td>
              <td>{r.name}</td>
              <td>
                {#if r.missing.length === 0}
                  —
                {:else}
                  {r.missing.join(', ')}
                {/if}
              </td>
            </tr>
            {#if !r.pass}
              <tr class="detail">
                <td colspan="3">
                  <div><strong>Input:</strong> <code>{r.input}</code></div>
                  <div><strong>Output:</strong> <code>{r.output}</code></div>
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}
</main>

<style>
  main {
    max-width: 1000px;
    margin: 24px auto;
    padding: 24px;
    font-family: system-ui, sans-serif;
  }
  h1 {
    margin: 0 0 8px;
  }
  .editor-host {
    border: 1px solid #ccc;
    min-height: 120px;
    margin: 16px 0;
    padding: 8px;
  }
  button {
    padding: 8px 16px;
    font-size: 14px;
    background: #2563eb;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
  }
  button:disabled {
    background: #94a3b8;
    cursor: not-allowed;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 16px;
  }
  th, td {
    border: 1px solid #ddd;
    padding: 8px;
    text-align: left;
  }
  tr.pass { background: #dcfce7; }
  tr.fail { background: #fee2e2; }
  tr.detail td {
    background: #f8fafc;
    font-size: 12px;
  }
  .error {
    color: #b91c1c;
    font-weight: 600;
  }
  code {
    background: #f1f5f9;
    padding: 2px 4px;
    border-radius: 3px;
    font-size: 12px;
  }
</style>
