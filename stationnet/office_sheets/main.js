import { pbkdf2Async } from '@noble/hashes/pbkdf2.js';
import { sha256 } from '@noble/hashes/sha2.js';
import { gcm } from '@noble/ciphers/aes.js';
import { rememberOfficeKey, readOfficeKey, forgetOfficeKey } from '../office_vault.js';
import { WorkbookModel, blankWorkbook, ROWS, COLS, cellName, columnName, keyOf, parseKey, rangeBounds } from './model.js';
import { importWorkbook, exportWorkbook, parseDelimited } from './formats.js';

const app = document.querySelector('.sheets-app');
if (app) initialize();

function initialize() {
  document.body.classList.add('sheets-page');
  const $ = selector => app.querySelector(selector), api = app.dataset.api, csrf = app.dataset.csrf;
  const encoder = new TextEncoder(), decoder = new TextDecoder();
  let key = null, records = [], id = null, revision = null, model = new WorkbookModel(), active = 0;
  let anchor = { row: 0, col: 0 }, focus = { row: 0, col: 0 }, dragging = false;
  let dirty = false, saving = false, conflict = false, generation = 0, saveTimer = null;
  let undoStack = [], redoStack = [], editInput = null, pendingFile = null, lastCopy = null;
  let rowOffsets = [], colOffsets = [], hiddenRows = new Set(), raf = 0;
  const grid = $('#sheets-grid'), cells = $('#sheets-cells');
  const status = (value, kind = '') => { $('#sheets-status').textContent = value; $('#sheets-status').dataset.kind = kind; };
  const message = value => { const box = $('#sheets-message'); box.replaceChildren(); box.textContent = value; box.hidden = !value; };
  const b64 = bytes => { let value = ''; for (let i = 0; i < bytes.length; i += 8192) value += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(value); };
  const unb64 = value => Uint8Array.from(atob(value), char => char.charCodeAt(0));
  const encrypt = data => { const iv = crypto.getRandomValues(new Uint8Array(12)); return { v: 1, iv: b64(iv), data: b64(gcm(key, iv).encrypt(encoder.encode(JSON.stringify(data)))) }; };
  const decrypt = envelope => JSON.parse(decoder.decode(gcm(key, unb64(envelope.iv)).decrypt(unb64(envelope.data))));
  async function request(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, ...options });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(result.error || `Request failed (${response.status}).`); error.status = response.status; throw error; }
    return result;
  }
  const bounds = () => rangeBounds(anchor, focus);
  const selectedSheet = () => model.sheet(active);
  const raw = (row, col) => model.raw(active, row, col);
  const numFmt = cell => cell.s?.numFmt || 'General';
  function display(row, col) {
    let value = model.value(active, row, col);
    if (value == null) return '';
    const fmt = numFmt(model.cell(active, row, col));
    if (typeof value === 'number') {
      if (fmt === '0.00%') return `${(value * 100).toFixed(2)}%`;
      if (fmt === '$#,##0.00') return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value);
      if (fmt === 'yyyy-mm-dd' || (/[yd]/i.test(fmt) && /y/i.test(fmt) && /d/i.test(fmt))) {
        const date = new Date(Date.UTC(1899, 11, 30) + value * 86400000);
        return Number.isNaN(date.getTime()) ? String(value) : date.toISOString().slice(0, 10);
      }
      if (fmt === '#,##0.00') return new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
      return String(value);
    }
    return String(value);
  }
  function markDirty() { if (!key || conflict) return; dirty = true; generation++; status('UNSAVED', 'pending'); clearTimeout(saveTimer); saveTimer = setTimeout(save, 800); }
  async function save() {
    clearTimeout(saveTimer);
    if (!key || !dirty || saving || conflict) return;
    saving = true;
    const target = id, rev = revision, savedGeneration = generation;
    status('SAVING…', 'saving');
    try {
      model.data.title = $('#sheets-title').value.trim() || 'Untitled workbook';
      const envelope = encrypt(model.data);
      const result = target === null
        ? await request(api, { method: 'POST', body: JSON.stringify({ envelope }) })
        : await request(`${api}/${target}`, { method: 'PUT', body: JSON.stringify({ envelope, revision: rev }) });
      if (id === target) { if (target === null) id = result.id; revision = result.revision; }
      dirty = generation !== savedGeneration;
      status(dirty ? 'UNSAVED' : 'SAVED', dirty ? 'pending' : 'saved'); message('');
      await refreshList();
    } catch (error) {
      if (error.status === 409) { conflict = true; status('REVISION CONFLICT', 'error'); showConflict(); }
      else { status('SAVE ERROR', 'error'); message(`Could not save: ${error.message} Your edits remain in this tab. Press SAVE to retry.`); }
    } finally { saving = false; if (dirty && !conflict && key && generation !== savedGeneration) saveTimer = setTimeout(save, 800); }
  }
  function showConflict() {
    const box = $('#sheets-message'); box.replaceChildren(); box.hidden = false;
    box.append(document.createTextNode('This workbook changed in another tab or device. Your edits are still here. '));
    const copy = document.createElement('button'); copy.textContent = 'SAVE AS COPY'; copy.onclick = async () => {
      id = null; revision = null; conflict = false; $('#sheets-title').value += ' (conflict copy)'; markDirty(); await save();
    };
    const remote = document.createElement('button'); remote.textContent = 'LOAD REMOTE'; remote.onclick = async () => {
      if (confirm('Discard edits in this tab and load the saved revision?')) { conflict = false; dirty = false; await load(id, true); }
    };
    box.append(copy, remote);
  }
  async function refreshList() {
    records = (await request(api)).sheets;
    const list = $('#sheets-list'); list.replaceChildren();
    for (const record of records) {
      let title = 'Unable to decrypt';
      try { title = decrypt(record.envelope).title || 'Untitled workbook'; } catch { /* Bad or old password. */ }
      const li = document.createElement('li'), button = document.createElement('button');
      button.type = 'button'; button.textContent = title; button.className = record.id === id ? 'selected' : '';
      button.title = `Updated ${record.updated_at} UTC`; button.onclick = () => load(record.id);
      li.append(button); list.append(li);
    }
    if (!records.length) { const li = document.createElement('li'); li.textContent = 'No saved workbooks yet.'; list.append(li); }
  }
  async function flushBeforeChange() {
    commitEdit();
    if (dirty && !conflict) await save();
    return !dirty && !saving && !conflict;
  }
  async function load(nextId, force = false) {
    if (nextId === id && !force) return;
    if (!force && !await flushBeforeChange()) return;
    try {
      const result = await request(`${api}/${nextId}`);
      model.load(decrypt(result.envelope)); id = nextId; revision = result.revision; active = 0;
      dirty = false; conflict = false; undoStack = []; redoStack = []; lastCopy = null;
      $('#sheets-title').value = model.data.title; status('SAVED', 'saved'); message('');
      resetView(); await refreshList();
    } catch (error) { message(`Could not open workbook: ${error.message}`); }
  }
  async function newWorkbook() {
    if (!await flushBeforeChange()) return;
    model.load(blankWorkbook()); id = null; revision = null; active = 0;
    undoStack = []; redoStack = []; lastCopy = null; $('#sheets-title').value = model.data.title;
    dirty = false; status('NEW WORKBOOK', 'pending'); message(''); resetView(); markDirty(); await save();
  }
  function snapshot() { undoStack.push(JSON.stringify(model.data)); if (undoStack.length > 40) undoStack.shift(); redoStack = []; }
  function restore(stack, other) {
    if (!stack.length) return;
    commitEdit(); other.push(JSON.stringify(model.data)); model.load(JSON.parse(stack.pop()));
    active = Math.min(active, model.data.sheets.length - 1); recompute(); renderTabs(); render(); markDirty();
  }
  function change(fn) {
    commitEdit(); const before = JSON.stringify(model.data), previousRedo = redoStack;
    snapshot();
    try { fn(); recompute(); renderTabs(); render(); markDirty(); }
    catch (error) { undoStack.pop(); redoStack = previousRedo; model.load(JSON.parse(before)); recompute(); renderTabs(); render(); message(error.message); }
  }
  function recompute() {
    const sheet = selectedSheet(); hiddenRows = new Set();
    if (sheet.filters) {
      const { col, value } = sheet.filters;
      const last = Math.max(0, ...Object.keys(sheet.cells).map(key => parseKey(key)[0]));
      for (let row = 1; row <= last; row++) if (String(model.value(active, row, col) ?? '') !== value) hiddenRows.add(row);
    }
    rowOffsets = new Float64Array(ROWS + 1); colOffsets = new Float64Array(COLS + 1);
    rowOffsets[0] = 42; colOffsets[0] = 48;
    for (let r = 0; r < ROWS; r++) rowOffsets[r + 1] = rowOffsets[r] + (hiddenRows.has(r) ? 0 : (sheet.heights?.[r] || 28));
    for (let c = 0; c < COLS; c++) colOffsets[c + 1] = colOffsets[c] + (sheet.widths?.[c] || 120);
    $('#sheets-spacer').style.width = `${colOffsets[COLS]}px`;
    $('#sheets-spacer').style.height = `${rowOffsets[ROWS]}px`;
  }
  function lowerBound(offsets, value) { let lo = 0, hi = offsets.length - 1; while (lo < hi) { const mid = (lo + hi) >> 1; if (offsets[mid] < value) lo = mid + 1; else hi = mid; } return Math.max(0, lo - 1); }
  function visibleIndices(offsets, count, start, end, frozen) {
    const result = [];
    for (let i = 0; i < frozen; i++) result.push(i);
    for (let i = Math.max(frozen, lowerBound(offsets, start) - 1); i < count && offsets[i] < end + 100; i++) if (offsets[i + 1] > offsets[i]) result.push(i);
    return result;
  }
  function render() {
    if ($('#sheets-workspace').hidden) return;
    const sheet = selectedSheet(), sx = grid.scrollLeft, sy = grid.scrollTop;
    const rows = visibleIndices(rowOffsets, ROWS, sy, sy + grid.clientHeight, sheet.freezeRows || 0);
    const cols = visibleIndices(colOffsets, COLS, sx, sx + grid.clientWidth, sheet.freezeCols || 0);
    const selected = bounds(), fragment = document.createDocumentFragment();
    function element(className, text, left, top, width, height, dataset = {}) {
      const div = document.createElement('div'); div.className = className; div.textContent = text;
      div.style.left = `${left}px`; div.style.top = `${top}px`; div.style.width = `${width}px`; div.style.height = `${height}px`;
      Object.assign(div.dataset, dataset); fragment.append(div); return div;
    }
    element('sheets-corner', '', sx, sy, 48, 42);
    for (const col of cols) {
      const frozen = col < (sheet.freezeCols || 0), left = colOffsets[col] + (frozen ? sx : 0);
      const header = element('sheets-col-head', columnName(col), left, sy, colOffsets[col + 1] - colOffsets[col], 42, { col });
      if (col >= selected.c1 && col <= selected.c2) header.classList.add('selected');
      if (frozen) header.classList.add('frozen');
    }
    for (const row of rows) {
      const frozenRow = row < (sheet.freezeRows || 0), top = rowOffsets[row] + (frozenRow ? sy : 0);
      const header = element('sheets-row-head', String(row + 1), sx, top, 48, rowOffsets[row + 1] - rowOffsets[row], { row });
      if (row >= selected.r1 && row <= selected.r2) header.classList.add('selected');
      if (frozenRow) header.classList.add('frozen');
      for (const col of cols) {
        const frozenCol = col < (sheet.freezeCols || 0), left = colOffsets[col] + (frozenCol ? sx : 0);
        const div = element('sheets-cell', display(row, col), left, top, colOffsets[col + 1] - colOffsets[col], rowOffsets[row + 1] - rowOffsets[row], { row, col });
        const s = model.cell(active, row, col).s || {};
        if (s.bold) div.style.fontWeight = 'bold'; if (s.italic) div.style.fontStyle = 'italic';
        if (s.color) div.style.color = s.color; if (s.background) div.style.backgroundColor = s.background;
        if (s.align) div.style.textAlign = s.align; if (s.border) div.classList.add('bordered');
        if (row >= selected.r1 && row <= selected.r2 && col >= selected.c1 && col <= selected.c2) div.classList.add('selected');
        if (row === focus.row && col === focus.col) div.classList.add('focused');
        if (frozenRow || frozenCol) div.classList.add('frozen');
      }
    }
    cells.replaceChildren(fragment);
    $('#sheets-name-box').value = selected.r1 === selected.r2 && selected.c1 === selected.c2 ? cellName(focus.row, focus.col) : `${cellName(selected.r1, selected.c1)}:${cellName(selected.r2, selected.c2)}`;
    if (document.activeElement !== $('#sheets-formula-input')) $('#sheets-formula-input').value = String(raw(focus.row, focus.col));
  }
  function scheduleRender() { if (!raf) raf = requestAnimationFrame(() => { raf = 0; render(); }); }
  function ensureVisible(row, col) {
    const sheet = selectedSheet();
    if (row >= (sheet.freezeRows || 0)) { if (rowOffsets[row] < grid.scrollTop + 42) grid.scrollTop = rowOffsets[row] - 42; else if (rowOffsets[row + 1] > grid.scrollTop + grid.clientHeight) grid.scrollTop = rowOffsets[row + 1] - grid.clientHeight; }
    if (col >= (sheet.freezeCols || 0)) { if (colOffsets[col] < grid.scrollLeft + 48) grid.scrollLeft = colOffsets[col] - 48; else if (colOffsets[col + 1] > grid.scrollLeft + grid.clientWidth) grid.scrollLeft = colOffsets[col + 1] - grid.clientWidth; }
  }
  function select(row, col, extend = false) {
    focus = { row: Math.max(0, Math.min(ROWS - 1, row)), col: Math.max(0, Math.min(COLS - 1, col)) };
    if (!extend) anchor = { ...focus };
    ensureVisible(focus.row, focus.col); render();
  }
  function resetView() { anchor = { row: 0, col: 0 }; focus = { ...anchor }; grid.scrollTo(0, 0); recompute(); renderTabs(); render(); }
  function renderTabs() {
    const list = $('#sheets-tab-list'); list.replaceChildren();
    model.data.sheets.forEach((sheet, index) => {
      const button = document.createElement('button'); button.type = 'button'; button.role = 'tab'; button.textContent = sheet.name;
      button.setAttribute('aria-selected', String(index === active)); button.onclick = () => { commitEdit(); active = index; resetView(); };
      list.append(button);
    });
  }
  function setCell(row, col, value) { change(() => model.set(active, row, col, value)); select(row, col); }
  function beginEdit(initial = null) {
    if (editInput) return;
    const row = focus.row, col = focus.col;
    editInput = document.createElement('input'); editInput.className = 'sheets-cell-editor';
    editInput.value = initial === null ? String(raw(row, col)) : initial;
    editInput.style.left = `${colOffsets[col]}px`; editInput.style.top = `${rowOffsets[row]}px`;
    editInput.style.width = `${Math.max(120, colOffsets[col + 1] - colOffsets[col])}px`;
    editInput.style.height = `${rowOffsets[row + 1] - rowOffsets[row]}px`;
    cells.append(editInput); editInput.focus(); if (initial === null) editInput.select();
    editInput.onkeydown = event => {
      if (event.key === 'Enter') { event.preventDefault(); commitEdit(); select(row + 1, col); grid.focus(); }
      else if (event.key === 'Tab') { event.preventDefault(); commitEdit(); select(row, col + (event.shiftKey ? -1 : 1)); grid.focus(); }
      else if (event.key === 'Escape') { editInput.remove(); editInput = null; grid.focus(); }
    };
    editInput.onblur = () => commitEdit();
  }
  function commitEdit() {
    if (!editInput) return;
    const input = editInput; editInput = null; input.remove();
    const old = raw(focus.row, focus.col), value = input.value;
    if (String(old) !== value) { snapshot(); model.set(active, focus.row, focus.col, value); render(); markDirty(); }
  }
  function applyStyle(patch) { change(() => model.setStyle(active, bounds(), patch)); }
  function clipboardText() {
    const b = bounds(), lines = [];
    for (let r = b.r1; r <= b.r2; r++) {
      const values = [];
      for (let c = b.c1; c <= b.c2; c++) values.push(String(raw(r, c)).replaceAll('\t', ' ').replaceAll('\n', ' '));
      lines.push(values.join('\t'));
    }
    return lines.join('\r\n');
  }
  function paste(text) {
    let rows = parseDelimited(text.replaceAll('\r\n', '\n'), '\t');
    const ownCopy = lastCopy && text === lastCopy.text;
    if (ownCopy) {
      const b = lastCopy.bounds;
      rows = model.engine.getFillRangeData(
        { start: { sheet: lastCopy.sheet, row: b.r1, col: b.c1 }, end: { sheet: lastCopy.sheet, row: b.r2, col: b.c2 } },
        { start: { sheet: active, row: focus.row, col: focus.col }, end: { sheet: active, row: focus.row + b.r2 - b.r1, col: focus.col + b.c2 - b.c1 } },
      );
    }
    change(() => model.engine.batch(() => rows.forEach((values, dr) => values.forEach((value, dc) => {
      if (focus.row + dr >= ROWS || focus.col + dc >= COLS) return;
      model.set(active, focus.row + dr, focus.col + dc, value);
      if (ownCopy) {
        const source = model.cell(lastCopy.sheet, lastCopy.bounds.r1 + dr, lastCopy.bounds.c1 + dc);
        if (source.s) (model.sheet(active).cells[keyOf(focus.row + dr, focus.col + dc)] ||= {}).s = structuredClone(source.s);
      }
    }))));
    anchor = { ...focus }; focus = { row: Math.min(ROWS - 1, focus.row + rows.length - 1), col: Math.min(COLS - 1, focus.col + Math.max(...rows.map(row => row.length)) - 1) }; render();
  }
  function fill(axis) {
    const b = bounds();
    if (axis === 'row' && b.r1 === b.r2 || axis === 'col' && b.c1 === b.c2) return;
    change(() => {
      const source = { start: { sheet: active, row: b.r1, col: b.c1 }, end: { sheet: active, row: axis === 'row' ? b.r1 : b.r2, col: axis === 'col' ? b.c1 : b.c2 } };
      const target = { start: { sheet: active, row: b.r1, col: b.c1 }, end: { sheet: active, row: b.r2, col: b.c2 } };
      const data = model.engine.getFillRangeData(source, target);
      model.engine.batch(() => data.forEach((values, dr) => values.forEach((value, dc) => model.set(active, b.r1 + dr, b.c1 + dc, value))));
    });
  }
  function sort(direction) {
    const b = bounds(), sheet = selectedSheet();
    let r1 = b.r1, r2 = b.r2;
    const used = Object.keys(sheet.cells).map(parseKey);
    if (r1 === r2) {
      r1 = 0; r2 = Math.max(0, ...used.map(([row]) => row));
      b.c1 = 0; b.c2 = Math.max(0, ...used.map(([, col]) => col));
      if (r2 > 0) r1 = 1; // Keep the header when sorting a full column.
    } else if (r2 === ROWS - 1) {
      r2 = Math.max(0, ...used.map(([row]) => row));
      if (r1 === 0 && r2 > 0) r1 = 1;
    }
    if (r2 <= r1) return;
    const sortCol = focus.col, rows = [];
    for (let r = r1; r <= r2; r++) {
      const data = {};
      for (let c = b.c1; c <= b.c2; c++) data[c] = structuredClone(model.cell(active, r, c));
      rows.push({ value: model.value(active, r, sortCol), data });
    }
    rows.sort((a, b) => (typeof a.value === 'number' && typeof b.value === 'number' ? a.value - b.value : String(a.value ?? '').localeCompare(String(b.value ?? ''), undefined, { numeric: true })) * direction);
    change(() => {
      model.engine.batch(() => rows.forEach((entry, offset) => {
        const row = r1 + offset;
        for (let c = b.c1; c <= b.c2; c++) {
          const cell = entry.data[c], key = keyOf(row, c);
          if (Object.keys(cell).length) sheet.cells[key] = cell; else delete sheet.cells[key];
          model.engine.setCellContents({ sheet: active, row, col: c }, cell.v ?? '');
        }
      }));
    });
  }
  function command(action) {
    const b = bounds();
    if (action === 'undo') return restore(undoStack, redoStack);
    if (action === 'redo') return restore(redoStack, undoStack);
    if (action === 'fillDown') return fill('row');
    if (action === 'fillRight') return fill('col');
    if (action === 'insertRow' || action === 'deleteRow') return change(() => model.structural(active, 'row', b.r1, b.r2 - b.r1 + 1, action === 'deleteRow'));
    if (action === 'insertCol' || action === 'deleteCol') return change(() => model.structural(active, 'col', b.c1, b.c2 - b.c1 + 1, action === 'deleteCol'));
    if (action === 'freeze') return change(() => { const s = selectedSheet(); s.freezeRows = focus.row; s.freezeCols = focus.col; });
    if (action === 'sortAsc' || action === 'sortDesc') return sort(action === 'sortAsc' ? 1 : -1);
    if (action === 'filter') {
      const value = prompt('Show rows where this column equals (leave blank to clear):', selectedSheet().filters?.value || '');
      if (value !== null) change(() => { selectedSheet().filters = value ? { col: focus.col, value } : null; });
      return;
    }
    if (['bar', 'line', 'pie'].includes(action)) {
      const range = bounds(); change(() => selectedSheet().charts.push({ type: action, range })); showChart({ type: action, range });
    }
  }
  function chartData(range) {
    const labels = [], values = [];
    for (let row = range.r1; row <= range.r2 && labels.length < 40; row++) {
      const numeric = [];
      for (let col = range.c1; col <= range.c2; col++) { const value = model.value(active, row, col); if (typeof value === 'number') numeric.push(value); }
      if (numeric.length) { labels.push(String(model.value(active, row, range.c1) ?? cellName(row, range.c1))); values.push(numeric[0]); }
    }
    return { labels, values };
  }
  function showChart(chart) {
    const { labels, values } = chartData(chart.range), svg = $('#sheets-chart-svg'); svg.replaceChildren();
    $('#sheets-chart').hidden = false;
    const ns = 'http://www.w3.org/2000/svg', max = Math.max(1, ...values.map(Math.abs));
    const add = (name, attrs, text = '') => { const el = document.createElementNS(ns, name); for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value); el.textContent = text; svg.append(el); return el; };
    if (!values.length) { add('text', { x: 20, y: 50, fill: 'currentColor' }, 'Select labels and numeric cells to chart.'); return; }
    const colors = ['#00c9b7', '#ffb448', '#e07bbd', '#80aaff', '#a8d35f'];
    if (chart.type === 'pie') {
      const total = values.reduce((sum, value) => sum + Math.max(0, value), 0) || 1; let start = -Math.PI / 2;
      values.forEach((value, i) => {
        const angle = Math.max(0, value) / total * Math.PI * 2, end = start + angle;
        if (angle > 0) add('path', { d: `M 180 140 L ${180 + Math.cos(start) * 100} ${140 + Math.sin(start) * 100} A 100 100 0 ${angle > Math.PI ? 1 : 0} 1 ${180 + Math.cos(end) * 100} ${140 + Math.sin(end) * 100} Z`, fill: colors[i % colors.length] });
        add('text', { x: 320, y: 35 + i * 20, fill: 'currentColor', 'font-size': 12 }, `${labels[i]}: ${value}`); start = end;
      });
    } else if (chart.type === 'bar') {
      values.forEach((value, i) => {
        const width = 500 / values.length, height = Math.abs(value) / max * 190;
        add('rect', { x: 55 + i * width, y: 225 - height, width: Math.max(3, width - 8), height, fill: colors[i % colors.length] });
        add('text', { x: 55 + i * width, y: 245, fill: 'currentColor', 'font-size': 10 }, labels[i].slice(0, 10));
      });
    } else {
      add('polyline', { points: values.map((value, i) => `${55 + i * (500 / Math.max(1, values.length - 1))},${225 - value / max * 190}`).join(' '), fill: 'none', stroke: colors[0], 'stroke-width': 3 });
      values.forEach((value, i) => add('text', { x: 55 + i * (500 / Math.max(1, values.length - 1)), y: 245, fill: 'currentColor', 'font-size': 10 }, labels[i].slice(0, 10)));
    }
  }
  async function unlock(event) {
    event.preventDefault(); const password = $('#sheets-password').value, errorBox = $('#sheets-unlock-error');
    if (password.length < 12) return;
    errorBox.hidden = true; status('UNLOCKING…', 'saving');
    try {
      if (!crypto?.getRandomValues) throw new Error('Secure browser cryptography is unavailable.');
      const vault = await request(app.dataset.vault, { method: 'POST', body: '{}' });
      const [sheets, documents, presentations, agenda, drawings, programs] = await Promise.all([request(api), request(app.dataset.documentsApi), request(app.dataset.presentationsApi), request(app.dataset.agendaApi), request(app.dataset.drawApi), request(app.dataset.basicApi)]);
      if (!sheets.sheets.length && !documents.documents.length && !presentations.presentations.length && !agenda.agenda && !drawings.drawings.length && !programs.programs.length) {
        if ($('#sheets-confirm-row').hidden) { $('#sheets-confirm-row').hidden = false; $('#sheets-confirm').required = true; status('CONFIRM NEW PASSWORD', 'pending'); $('#sheets-confirm').focus(); return; }
        if ($('#sheets-confirm').value !== password) throw new Error('The new passwords do not match.');
      }
      key = await pbkdf2Async(sha256, encoder.encode(password), unb64(vault.salt), { c: 210000, dkLen: 32, asyncTick: 20 });
      if (sheets.sheets.length) decrypt(sheets.sheets[0].envelope);
      else if (documents.documents.length) decrypt(documents.documents[0].envelope);
      else if (presentations.presentations.length) decrypt(presentations.presentations[0].envelope);
      else if (agenda.agenda) decrypt(agenda.agenda.envelope);
      else if (drawings.drawings.length) decrypt(drawings.drawings[0].envelope);
      else if (programs.programs.length) decrypt(programs.programs[0].envelope);
      rememberOfficeKey(key, csrf, $('#sheets-remember').checked);
      $('#sheets-password').value = ''; $('#sheets-confirm').value = ''; $('#sheets-confirm-row').hidden = true;
      $('#sheets-unlock').hidden = true; $('#sheets-workspace').hidden = false; $('#sheets-lock').hidden = false;
      await refreshList(); if (records.length) await load(records[0].id); else await newWorkbook();
    } catch (error) { key?.fill(0); key = null; errorBox.textContent = `Could not unlock. Check your OFFICE Password and try again. ${error.message}`; errorBox.hidden = false; status('LOCKED', 'error'); }
  }
  function lock() {
    if (dirty || saving) { save(); message('Wait for the SAVED indicator before locking.'); return; }
    forgetOfficeKey();
    key?.fill(0); key = null; records = []; id = null; revision = null; model.load(blankWorkbook());
    $('#sheets-workspace').hidden = true; $('#sheets-unlock').hidden = false; $('#sheets-lock').hidden = true; status('LOCKED');
  }
  async function handleImport(file) {
    if (!file || !await flushBeforeChange()) return;
    if (file.size > 20 * 1024 * 1024) return message('Imports must be 20 MB or smaller.');
    try {
      const delimiter = $('#sheets-delimiter').value === 'tab' ? '\t' : $('#sheets-delimiter').value;
      const data = await importWorkbook(file, $('#sheets-encoding').value, delimiter);
      model.load(data); id = null; revision = null; active = 0; undoStack = []; redoStack = []; lastCopy = null;
      $('#sheets-title').value = data.title; resetView(); markDirty(); await save();
    } catch (error) { message(`Import failed: ${error.message}`); }
  }
  async function printSheet() {
    const sheet = selectedSheet(), print = document.createElement('div'); print.className = 'sheets-print';
    const title = document.createElement('h1'); title.textContent = `${model.data.title} / ${sheet.name}`; print.append(title);
    let maxRow = 0, maxCol = 0;
    for (const key of Object.keys(sheet.cells)) { const [r, c] = parseKey(key); maxRow = Math.max(maxRow, r); maxCol = Math.max(maxCol, c); }
    const table = document.createElement('table');
    for (let r = 0; r <= maxRow; r++) { const tr = table.insertRow(); for (let c = 0; c <= maxCol; c++) { const td = tr.insertCell(); td.textContent = display(r, c); } }
    print.append(table); app.append(print);
    window.addEventListener('afterprint', () => print.remove(), { once: true }); window.print(); setTimeout(() => print.remove(), 120000);
  }

  $('#sheets-unlock-form').onsubmit = unlock; $('#sheets-lock').onclick = lock;
  $('#sheets-save').onclick = () => { commitEdit(); save(); }; $('#sheets-new').onclick = newWorkbook;
  $('#sheets-title').oninput = markDirty;
  $('#sheets-delete').onclick = async () => { if (id === null) return newWorkbook(); if (!confirm('Delete this workbook permanently?')) return; try { await request(`${api}/${id}`, { method: 'DELETE' }); dirty = false; await newWorkbook(); await refreshList(); } catch (error) { message(error.message); } };
  $('#sheets-import').onclick = () => $('#sheets-file').click();
  $('#sheets-file').onchange = event => { pendingFile = event.target.files[0]; event.target.value = ''; if (!pendingFile) return; if (/\.(csv|tsv)$/i.test(pendingFile.name)) $('#sheets-import-dialog').showModal(); else handleImport(pendingFile); };
  $('#sheets-import-dialog').onclose = event => { if (event.target.returnValue === 'import') handleImport(pendingFile); pendingFile = null; };
  $('#sheets-export').onclick = () => $('#sheets-export-dialog').showModal();
  $('#sheets-export-dialog').onclose = async event => { const format = event.target.returnValue; if (!['xlsx', 'ods', 'csv', 'tsv', 'pdf'].includes(format)) return; try { commitEdit(); if (format === 'pdf') await printSheet(); else await exportWorkbook(model, active, format); } catch (error) { message(`Export failed: ${error.message}`); } };
  app.querySelectorAll('[data-action]').forEach(button => button.onclick = () => command(button.dataset.action));
  app.querySelectorAll('[data-style]').forEach(button => button.onclick = () => { const name = button.dataset.style; applyStyle({ [name]: !model.cell(active, focus.row, focus.col).s?.[name] }); });
  $('#sheets-format').onchange = event => applyStyle({ numFmt: ({ general: 'General', number: '#,##0.00', currency: '$#,##0.00', percent: '0.00%', date: 'yyyy-mm-dd' })[event.target.value] });
  $('#sheets-color').onchange = event => applyStyle({ color: event.target.value }); $('#sheets-background').onchange = event => applyStyle({ background: event.target.value }); $('#sheets-align').onchange = event => applyStyle({ align: event.target.value });
  $('#sheets-add-tab').onclick = () => change(() => { active = model.addSheet(); anchor = { row: 0, col: 0 }; focus = { ...anchor }; });
  $('#sheets-rename-tab').onclick = () => { const name = prompt('Sheet name:', selectedSheet().name)?.trim(); if (name) change(() => model.renameSheet(active, name)); };
  $('#sheets-delete-tab').onclick = () => { if (model.data.sheets.length < 2) return message('A workbook must have at least one sheet.'); if (confirm(`Delete sheet ${selectedSheet().name}?`)) change(() => { model.deleteSheet(active); active = Math.min(active, model.data.sheets.length - 1); }); };
  $('#sheets-close-chart').onclick = () => { $('#sheets-chart').hidden = true; };
  $('#sheets-formula-input').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); setCell(focus.row, focus.col, event.target.value); grid.focus(); } else if (event.key === 'Escape') { event.target.value = String(raw(focus.row, focus.col)); grid.focus(); } };
  $('#sheets-formula-input').onblur = event => { if (event.target.value !== String(raw(focus.row, focus.col))) setCell(focus.row, focus.col, event.target.value); };
  $('#sheets-name-box').onkeydown = event => { if (event.key !== 'Enter') return; const match = /^([A-Z]+)(\d+)$/i.exec(event.target.value.trim()); if (!match) return; let col = 0; for (const ch of match[1].toUpperCase()) col = col * 26 + ch.charCodeAt(0) - 64; select(Number(match[2]) - 1, col - 1); grid.focus(); };
  grid.onscroll = scheduleRender;
  grid.onpointerdown = event => {
    const target = event.target.closest('[data-row], [data-col]'); if (!target) return;
    const row = target.dataset.row, col = target.dataset.col;
    if (target.classList.contains('sheets-col-head')) {
      const right = target.getBoundingClientRect().right;
      if (right - event.clientX < 8) { const start = event.clientX, width = colOffsets[Number(col) + 1] - colOffsets[Number(col)]; const move = e => { selectedSheet().widths[col] = Math.max(44, width + e.clientX - start); recompute(); render(); }; const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); markDirty(); }; window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); return; }
      anchor = { row: 0, col: Number(col) }; focus = { row: ROWS - 1, col: Number(col) }; render(); return;
    }
    if (target.classList.contains('sheets-row-head')) {
      const bottom = target.getBoundingClientRect().bottom;
      if (bottom - event.clientY < 8) { const start = event.clientY, height = rowOffsets[Number(row) + 1] - rowOffsets[Number(row)]; const move = e => { selectedSheet().heights[row] = Math.max(20, height + e.clientY - start); recompute(); render(); }; const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); markDirty(); }; window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); return; }
      anchor = { row: Number(row), col: 0 }; focus = { row: Number(row), col: COLS - 1 }; render(); return;
    }
    commitEdit(); select(Number(row), Number(col), event.shiftKey); dragging = true; grid.focus();
  };
  grid.onpointermove = event => { if (!dragging) return; const target = event.target.closest('.sheets-cell'); if (target) select(Number(target.dataset.row), Number(target.dataset.col), true); };
  window.addEventListener('pointerup', () => { dragging = false; });
  grid.ondblclick = event => { const target = event.target.closest('.sheets-cell'); if (target) { select(Number(target.dataset.row), Number(target.dataset.col)); beginEdit(); } };
  grid.onkeydown = event => {
    if (event.ctrlKey || event.metaKey) {
      const key = event.key.toLowerCase();
      if (key === 's') { event.preventDefault(); save(); } else if (key === 'z') { event.preventDefault(); command(event.shiftKey ? 'redo' : 'undo'); }
      else if (key === 'y') { event.preventDefault(); command('redo'); } else if (key === 'd') { event.preventDefault(); command('fillDown'); }
      else if (key === 'r') { event.preventDefault(); command('fillRight'); } else if (key === 'b') { event.preventDefault(); applyStyle({ bold: !model.cell(active, focus.row, focus.col).s?.bold }); }
      return;
    }
    const moves = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1], Enter: [1, 0], Tab: [0, event.shiftKey ? -1 : 1] };
    if (moves[event.key]) { event.preventDefault(); select(focus.row + moves[event.key][0], focus.col + moves[event.key][1], event.shiftKey && event.key !== 'Tab'); }
    else if (event.key === 'F2') { event.preventDefault(); beginEdit(); }
    else if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); const b = bounds(); change(() => model.engine.batch(() => { for (let r = b.r1; r <= b.r2; r++) for (let c = b.c1; c <= b.c2; c++) model.set(active, r, c, ''); })); }
    else if (event.key.length === 1 && !event.altKey) { event.preventDefault(); beginEdit(event.key); }
  };
  grid.oncopy = event => { event.preventDefault(); const text = clipboardText(); lastCopy = { text, sheet: active, bounds: bounds() }; event.clipboardData.setData('text/plain', text); };
  grid.onpaste = event => { event.preventDefault(); paste(event.clipboardData.getData('text/plain')); };
  new ResizeObserver(scheduleRender).observe(grid);
  window.addEventListener('beforeunload', event => { if (dirty || saving || conflict) { event.preventDefault(); event.returnValue = ''; } });
  (async () => {
    key = readOfficeKey(csrf);
    if (!key) return;
    let badKey = false;
    try {
      const result = await request(api);
      if (result.sheets.length) { try { decrypt(result.sheets[0].envelope); } catch (error) { badKey = true; throw error; } }
      $('#sheets-unlock').hidden = true; $('#sheets-workspace').hidden = false; $('#sheets-lock').hidden = false;
      await refreshList(); if (records.length) await load(records[0].id); else await newWorkbook();
    } catch {
      key?.fill(0); key = null; if (badKey) forgetOfficeKey(); status('LOCKED');
      $('#sheets-workspace').hidden = true; $('#sheets-unlock').hidden = false;
    }
  })();
}
