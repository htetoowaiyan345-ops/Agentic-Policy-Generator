// Test: navigate to Step 03 with a run injected, verify exactly ONE toolbar.
const WebSocket = require('ws');
const http = require('http');

function getJSON(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve(JSON.parse(data)));
    }).on('error', reject);
  });
}

async function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

(async () => {
  const pages = await getJSON('http://localhost:9222/json');
  const target = pages.find(p => p.url.startsWith('http://127.0.0.1:5173/'));
  if (!target) { console.error('no target'); return; }

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  ws.on('message', (data) => {
    const msg = JSON.parse(data.toString());
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  });
  function send(method, params = {}) {
    const cmdId = ++id;
    return new Promise((resolve) => {
      pending.set(cmdId, resolve);
      ws.send(JSON.stringify({ id: cmdId, method, params }));
    });
  }
  function eval_(expr) {
    return send('Runtime.evaluate', { expression: expr, returnByValue: true });
  }

  await new Promise((r) => ws.on('open', r));
  console.log('WS connected');
  await send('Page.enable');
  await send('Runtime.enable');

  // Navigate fresh
  await send('Page.navigate', { url: 'http://127.0.0.1:5173/' });
  await sleep(4000);

  // Test: fetch via console with full response
  const r = await eval_(`(async () => {
    try {
      const res = await fetch('http://127.0.0.1:8000/api/history');
      const text = await res.text();
      return JSON.stringify({ status: res.status, ok: res.ok, len: text.length, head: text.slice(0, 200) });
    } catch (e) {
      return 'err: ' + String(e);
    }
  })()`);
  console.log('fetch test:', r.result?.result?.value);

  // Parse runs
  const r2 = await eval_(`(async () => {
    try {
      const res = await fetch('http://127.0.0.1:8000/api/history');
      const runs = await res.json();
      const done = runs.filter(r => r.status === 'done' && r.run_id).slice(0, 2);
      return JSON.stringify(done.map(r => ({ id: r.run_id, name: r.filename })));
    } catch (e) {
      return 'err: ' + String(e);
    }
  })()`);
  const runsJson = r2.result?.result?.value;
  console.log('runs:', runsJson);
  if (!runsJson || runsJson.startsWith('err')) { process.exit(1); }
  const runs = JSON.parse(runsJson);

  // Inject store state with first run, then navigate to step 3
  const r3 = await eval_(`(async () => {
    try {
      const mod = await import('/src/lib/stores.ts');
      const first = ${JSON.stringify(runs[0])};
      mod.appState.set({
        ...mod.appState,
        runId: first.id,
        files: [{ name: first.name, size: 0 }],
        filename: first.name,
        fromHistory: true,
        activeRunId: first.id,
        activeFilename: first.name,
        batch: [{ file: { name: first.name, size: 0 }, name: first.name, runId: first.id, status: 'done', sections_filled: 0, markers_count: 0, reason: '' }],
        batchIndex: 0,
        versions: [],
        currentVersionNo: null,
        reviewAudit: []
      });
      // Force step to 3 by clicking the History panel then the load button.
      // Actually we need a more direct path. Try setting currentStep via the
      // page component's exported function.
      return 'set ' + first.id;
    } catch (e) {
      return 'err: ' + String(e) + ' ' + (e?.stack || '');
    }
  })()`);
  console.log('state:', r3.result?.result?.value);

  await sleep(1500);

  // Use the UI: click History toggle (which is "View history" button)
  const r4 = await eval_(`(() => {
    const histBtn = document.getElementById('nav-history');
    if (histBtn) histBtn.click();
    return histBtn ? 'clicked' : 'not found';
  })()`);
  console.log('history:', r4.result?.result?.value);

  await sleep(500);

  // Check the History panel for any "load" buttons
  const r5 = await eval_(`(() => {
    const buttons = Array.from(document.querySelectorAll('button')).map(b => ({
      text: b.textContent?.trim().slice(0, 30),
      id: b.id
    }));
    return JSON.stringify(buttons.slice(0, 30));
  })()`);
  console.log('buttons:', r5.result?.result?.value);

  ws.close();
  process.exit(0);
})();