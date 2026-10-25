import HyperFormula from 'hyperformula';

export const ROWS = 20000;
export const COLS = 256;
export const columnName = col => { let name = ''; for (let n = col + 1; n; n = Math.floor((n - 1) / 26)) name = String.fromCharCode(65 + (n - 1) % 26) + name; return name; };
export const cellName = (row, col) => `${columnName(col)}${row + 1}`;
export const keyOf = (row, col) => `${row}:${col}`;
export const parseKey = key => key.split(':').map(Number);
export const blankWorkbook = () => ({ title: 'Untitled workbook', sheets: [{ name: 'Sheet1', cells: {}, widths: {}, heights: {}, freezeRows: 0, freezeCols: 0, filters: null, charts: [] }] });
export const rangeBounds = (a, b) => ({ r1: Math.min(a.row, b.row), r2: Math.max(a.row, b.row), c1: Math.min(a.col, b.col), c2: Math.max(a.col, b.col) });
const engineFormula = value => typeof value === 'string' && value.startsWith('=')
  ? value.replace(/("(?:[^"]|"")*")|\b(TRUE|FALSE)\b(?!\s*\()/gi, (match, quoted, bool) => quoted || `${bool.toUpperCase()}()`)
  : value;
export const normalizeInput = value => {
  if (typeof value !== 'string') return value;
  if (value.startsWith("'")) return value.slice(1);
  if (/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:e[+-]?\d+)?$/i.test(value) && Number.isFinite(Number(value))) return Number(value);
  return value;
};

export class WorkbookModel {
  constructor(data = blankWorkbook()) { this.load(data); }
  load(data) {
    this.data = structuredClone(data);
    this.data.sheets ||= blankWorkbook().sheets;
    this.engine?.destroy();
    this.engine = HyperFormula.buildEmpty({ licenseKey: 'gpl-v3', maxRows: ROWS, maxColumns: COLS });
    for (const sheet of this.data.sheets) this.engine.addSheet(sheet.name);
    this.engine.batch(() => {
      this.data.sheets.forEach((sheet, index) => {
        for (const [key, cell] of Object.entries(sheet.cells || {})) {
          const [row, col] = parseKey(key);
          if (cell.v !== undefined && cell.v !== null && cell.v !== '') this.engine.setCellContents({ sheet: index, row, col }, engineFormula(cell.v));
        }
      });
    });
  }
  sheet(index) { return this.data.sheets[index]; }
  cell(sheet, row, col) { return this.sheet(sheet).cells[keyOf(row, col)] || {}; }
  raw(sheet, row, col) { return this.cell(sheet, row, col).v ?? ''; }
  value(sheet, row, col) {
    const value = this.engine.getCellValue({ sheet, row, col });
    return value && typeof value === 'object' && 'value' in value ? value.value : value;
  }
  set(sheet, row, col, raw) {
    raw = normalizeInput(raw);
    const cells = this.sheet(sheet).cells, key = keyOf(row, col);
    const cell = cells[key] || {};
    if (raw === '' || raw === null || raw === undefined) delete cell.v;
    else cell.v = raw;
    if (Object.keys(cell).length) cells[key] = cell;
    else delete cells[key];
    this.engine.setCellContents({ sheet, row, col }, engineFormula(raw ?? ''));
  }
  setStyle(sheet, bounds, patch) {
    const cells = this.sheet(sheet).cells;
    for (let row = bounds.r1; row <= bounds.r2; row++) for (let col = bounds.c1; col <= bounds.c2; col++) {
      const key = keyOf(row, col), cell = cells[key] || {};
      cell.s = { ...(cell.s || {}), ...patch };
      cells[key] = cell;
    }
  }
  addSheet() {
    let n = this.data.sheets.length + 1, name = `Sheet${n}`;
    while (this.data.sheets.some(sheet => sheet.name.toLowerCase() === name.toLowerCase())) name = `Sheet${++n}`;
    this.data.sheets.push({ name, cells: {}, widths: {}, heights: {}, freezeRows: 0, freezeCols: 0, filters: null, charts: [] });
    this.engine.addSheet(name);
    return this.data.sheets.length - 1;
  }
  renameSheet(index, name) { this.engine.renameSheet(index, name); this.sheet(index).name = name; }
  deleteSheet(index) { if (this.data.sheets.length < 2) return false; this.data.sheets.splice(index, 1); this.load(this.data); return true; }
  structural(sheet, axis, index, count, remove = false) {
    const s = this.sheet(sheet), isRow = axis === 'row';
    const transformed = {};
    for (const [key, cell] of Object.entries(s.cells)) {
      let [row, col] = parseKey(key), n = isRow ? row : col;
      if (remove && n >= index && n < index + count) continue;
      if (n >= index) isRow ? row += remove ? -count : count : col += remove ? -count : count;
      if (row < ROWS && col < COLS) transformed[keyOf(row, col)] = cell;
    }
    s.cells = transformed;
    const oldSizes = isRow ? s.heights : s.widths, sizes = {};
    for (const [key, value] of Object.entries(oldSizes)) {
      let n = Number(key);
      if (remove && n >= index && n < index + count) continue;
      if (n >= index) n += remove ? -count : count;
      if (n >= 0) sizes[n] = value;
    }
    isRow ? s.heights = sizes : s.widths = sizes;
    if (s.filters) s.filters = null;
    // HyperFormula adjusts formulas, including references from other sheets.
    const op = `${remove ? 'remove' : 'add'}${isRow ? 'Rows' : 'Columns'}`;
    this.engine[op](sheet, [index, count]);
    this.syncRaw();
  }
  syncRaw() {
    this.data.sheets.forEach((sheet, index) => {
      const serialized = this.engine.getSheetSerialized(index), cells = sheet.cells;
      for (const cell of Object.values(cells)) delete cell.v;
      serialized.forEach((row, r) => row.forEach((value, c) => {
        if (value === '' || value === null || value === undefined) return;
        (cells[keyOf(r, c)] ||= {}).v = value;
      }));
      for (const [key, cell] of Object.entries(cells)) if (!Object.keys(cell).length) delete cells[key];
    });
  }
  replaceRows(sheet, start, rows) {
    const s = this.sheet(sheet), width = Math.max(0, ...rows.map(row => row.length));
    this.engine.batch(() => {
      rows.forEach((values, offset) => values.forEach((value, col) => this.set(sheet, start + offset, col, value)));
    });
    this.syncRaw();
    return width;
  }
}
