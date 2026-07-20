import { appState, setActiveRun, setFromHistory } from './stores';
import { getHistory, getPreview, downloadDocx } from './api';
import { get } from 'svelte/store';
import type { PreviewData, HistoryEntry, AppState } from './types';

export async function loadResultAndShow(runId: string): Promise<void> {
  const sel = document.getElementById('results-picker') as HTMLSelectElement | null;
  const wrap = document.getElementById('results-picker-wrap');
  if (sel) sel.innerHTML = '';
  if (wrap) wrap.classList.add('hidden');

  const dlAllBtn = document.getElementById('download-all-btn') as HTMLButtonElement | null;
  if (dlAllBtn) {
    dlAllBtn.classList.remove('hidden');
    dlAllBtn.textContent = 'Download all files';
    dlAllBtn.disabled = false;
  }
  const dlAllDone = document.getElementById('download-all-done');
  if (dlAllDone) dlAllDone.classList.add('hidden');

  setActiveRun(runId, null);

  let match: HistoryEntry | null = null;
  try {
    const hist = await getHistory();
    match = (hist || []).find((h) => h.run_id === runId) || null;
  } catch {
    match = null;
  }
  if (match) {
    setActiveRun(runId, match.filename);
  }

  if (sel && wrap && match) {
    wrap.classList.remove('hidden');
    const opt = document.createElement('option');
    opt.value = runId;
    opt.textContent = match.filename;
    sel.appendChild(opt);
    sel.value = runId;
  }

  try {
    const data: PreviewData = await getPreview(runId);
    renderSlots(data);
    const dlBtn = document.getElementById('download-btn');
    if (dlBtn) {
      const current = get(appState);
      const name = current.activeFilename || 'Policy.docx';
      const stem = name.replace(/\.[^/.]+$/, '');
      dlBtn.textContent = `Download ${stem}.docx`;
    }
  } catch (e) {
    const container = document.getElementById('slots-container');
    if (container) container.textContent = 'Failed to load result: ' + (e instanceof Error ? e.message : String(e));
  }
}

const MARKER = 'Data is not found in source file';

const SLOT_LABEL_MAP: Record<string, number> = {
  'Type': 1, 'Policy Title': 1, 'Policy Number': 1,
  'Applicable Sector(s)': 1, 'Functional Area(s)': 1,
  'Brief Description': 2,
  'Effective Date/Period': 3, 'Approved by': 3, 'Prepared by': 3,
  'Responsible Function(s)': 3, 'Responsible Function Officer(s)': 3,
  'Supersedes': 3, 'Last Reviewed': 3, 'Applies to': 3,
  'Reason for Policy': 4,
  'POLICY STATEMENT': 6,
  '1. Purpose': 7,
  '2. Scope & Beneficiaries': 8,
  '3. Exclusions': 9,
  '4. Award Structure & Payout Tiers': 10,
  'Policy Review Note': 11,
  'DEFINITIONS': 12,
  'RELATED POLICIES, PROCEDURES, FORMS, GUIDELINES & OTHER RESOURCES': 13,
  'RELATED POLICIES': 13, 'OTHER RESOURCES': 13,
  'HISTORY': 14,
};

function tableSlot(rows: string[][] | undefined): number | null {
  if (!rows || !rows.length) return null;
  const firstRow = rows[0] || [];
  const firstCell = (firstRow[0] || '').toLowerCase();
  if (firstCell.includes('version') || firstCell.includes('history')) return 14;
  if (firstCell.includes('award') || firstCell.includes('tier')) return 10;
  return null;
}

function slotForLine(text: string): number | null {
  if (!text) return null;
  for (const [label, sid] of Object.entries(SLOT_LABEL_MAP)) {
    if (text === label) return sid;
    if (text.startsWith(label + ' ')) return sid;
    if (text.startsWith(label + ':')) return sid;
  }
  return null;
}

export function renderSlots(data: PreviewData): void {
  const container = document.getElementById('slots-container');
  if (!container) {
    console.warn('[renderSlots] #slots-container not in DOM yet — preview will be empty');
    return;
  }
  container.innerHTML = '';

  const lines = data.lines || [];
  let activeSlot = 0;
  let appendedCount = 0;

  lines.forEach((item) => {
    if (!item || item.length !== 2) return;
    const kind = item[0];
    const payload = item[1];

    if (kind === 'p') {
      const payloadRecord = payload as Record<string, unknown>;
      const text = (typeof payloadRecord['text'] === 'string'
        ? (payloadRecord['text'] as string)
        : '') || (typeof payload === 'string' ? (payload as string) : '');
      const sid = slotForLine(text);
      if (sid !== null && sid !== activeSlot && activeSlot !== 0) {
        const div = document.createElement('div');
        div.className = 'brain-rule';
        container.appendChild(div);
        activeSlot = sid;
      } else if (sid !== null && activeSlot === 0) {
        activeSlot = sid;
      }
      container.appendChild(buildLine(text));
      appendedCount += 1;
    } else if (kind === 't') {
      const payloadRecord = payload as Record<string, unknown>;
      const rowsRaw = payloadRecord['rows'];
      const rows = (Array.isArray(rowsRaw) ? rowsRaw : payload) as unknown as string[][];
      const sid = tableSlot(rows);
      if (sid !== null && sid !== activeSlot && activeSlot !== 0) {
        const div = document.createElement('div');
        div.className = 'brain-rule';
        container.appendChild(div);
        activeSlot = sid;
      } else if (sid !== null && activeSlot === 0) {
        activeSlot = sid;
      }
      container.appendChild(buildTable(rows) as Node);
      appendedCount += 1;
    }
  });

  console.log(
    `[renderSlots] appended ${appendedCount} child node(s) to #slots-container (data.lines: ${lines.length})`
  );
}

function buildLine(text: string): HTMLElement {
  const p = document.createElement('p');
  p.className = 'brain-p';

  if (text === MARKER) {
    p.className = 'brain-p brain-marker';
    p.textContent = text;
    return p;
  }

  const m = text.match(/^([A-Z][A-Za-z0-9 ()/\-.]+?):\s*(.*)$/);
  if (m) {
    const label = m[1];
    const value = m[2];
    const lab = document.createElement('span');
    lab.className = 'brain-p-label';
    lab.textContent = label + ':';
    p.appendChild(lab);
    p.appendChild(document.createTextNode(' '));
    if (value === MARKER) {
      const mk = document.createElement('span');
      mk.className = 'brain-marker-inline';
      mk.textContent = value;
      p.appendChild(mk);
    } else {
      p.appendChild(document.createTextNode(value));
    }
    return p;
  }

  p.textContent = text;
  return p;
}

function buildTable(rows: string[][] | undefined): HTMLElement {
  if (!rows || !rows.length) {
    return buildLine('(empty table)');
  }
  const wrap = document.createElement('div');
  wrap.className = 'brain-table-wrap';
  const table = document.createElement('table');
  table.className = 'brain-table';

  const header = rows[0];
  const bodyRows = rows.slice(1);
  if (header && header.length) {
    const thead = document.createElement('thead');
    const trh = document.createElement('tr');
    header.forEach((h) => {
      const th = document.createElement('th');
      th.textContent = h == null ? '' : String(h);
      trh.appendChild(th);
    });
    thead.appendChild(trh);
    table.appendChild(thead);
  }
  if (bodyRows && bodyRows.length) {
    const tbody = document.createElement('tbody');
    bodyRows.forEach((r) => {
      const tr = document.createElement('tr');
      r.forEach((c) => {
        const td = document.createElement('td');
        td.textContent = c == null ? '' : String(c);
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
  }
  wrap.appendChild(table);
  return wrap;
}
