import * as XLSX from 'xlsx';
import ExcelJS from 'exceljs/dist/exceljs.min.js';
import { keyOf, parseKey, normalizeInput } from './model.js';

const extension = name => name.split('.').pop().toLowerCase();
const excelDate = date => (date.getTime() - Date.UTC(1899, 11, 30)) / 86400000;
const rgb = color => color?.replace('#', '').toUpperCase();
const styleFromExcel = cell => {
  const s = {};
  if (cell.font?.bold) s.bold = true;
  if (cell.font?.italic) s.italic = true;
  if (cell.font?.color?.argb) s.color = `#${cell.font.color.argb.slice(-6)}`;
  if (cell.fill?.fgColor?.argb) s.background = `#${cell.fill.fgColor.argb.slice(-6)}`;
  if (cell.alignment?.horizontal) s.align = cell.alignment.horizontal;
  if (cell.border && Object.values(cell.border).some(Boolean)) s.border = true;
  if (cell.numFmt) s.numFmt = cell.numFmt;
  return s;
};

export function parseDelimited(text, delimiter) {
  const rows = [], row = [];
  let field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (ch === '"') quoted = false;
      else field += ch;
    } else if (ch === '"' && !field) quoted = true;
    else if (ch === delimiter) { row.push(field); field = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(field); rows.push(row.splice(0)); field = '';
    } else field += ch;
  }
  row.push(field);
  if (row.length > 1 || row[0] !== '' || !rows.length) rows.push(row);
  return rows;
}

export function inferDelimiter(text) {
  const sample = text.split(/\r?\n/).slice(0, 10).join('\n');
  return [',', '\t', ';', '|'].map(d => [d, sample.split(d).length]).sort((a, b) => b[1] - a[1])[0][0];
}

export async function importWorkbook(file, encoding = 'utf-8', delimiter = 'auto') {
  const ext = extension(file.name), data = new Uint8Array(await file.arrayBuffer());
  const title = file.name.replace(/\.[^.]+$/, '').slice(0, 160) || 'Imported workbook';
  if (ext === 'csv' || ext === 'tsv') {
    const decoder = new TextDecoder(encoding);
    const text = decoder.decode(data).replace(/^\uFEFF/, '');
    const d = delimiter === 'auto' ? (ext === 'tsv' ? '\t' : inferDelimiter(text)) : delimiter;
    const rows = parseDelimited(text, d), cells = {};
    rows.forEach((values, row) => values.forEach((value, col) => {
      if (value !== '') cells[keyOf(row, col)] = { v: normalizeInput(value) };
    }));
    return { title, sheets: [{ name: 'Sheet1', cells, widths: {}, heights: {}, freezeRows: 0, freezeCols: 0, filters: null, charts: [] }] };
  }
  if (ext === 'xlsx') {
    try {
      const book = new ExcelJS.Workbook();
      await book.xlsx.load(data);
      const sheets = book.worksheets.map(ws => {
        const cells = {}, widths = {}, heights = {};
        ws.columns.forEach((column, index) => { if (column.width) widths[index] = Math.round(column.width * 8 + 8); });
        ws.eachRow({ includeEmpty: false }, (row, rowNumber) => {
          if (row.height) heights[rowNumber - 1] = Math.round(row.height * 1.33);
          row.eachCell({ includeEmpty: false }, (cell, colNumber) => {
            let value = cell.value;
            if (value instanceof Date) value = excelDate(value);
            else if (value && typeof value === 'object') {
              if (value.formula) value = `=${value.formula}`;
              else if (value.text) value = value.text;
              else value = String(value.result ?? '');
            }
            const s = styleFromExcel(cell);
            cells[keyOf(rowNumber - 1, colNumber - 1)] = { ...(value !== null && value !== '' ? { v: value } : {}), ...(Object.keys(s).length ? { s } : {}) };
          });
        });
        return { name: ws.name, cells, widths, heights, freezeRows: ws.views?.[0]?.ySplit || 0, freezeCols: ws.views?.[0]?.xSplit || 0, filters: null, charts: [] };
      });
      if (sheets.length) return { title, sheets };
    } catch { /* Fall back to SheetJS for unusual XLSX files. */ }
  }
  const book = XLSX.read(data, { type: 'array', cellFormula: true, cellStyles: true, cellNF: true, cellDates: false });
  return { title, sheets: book.SheetNames.map(name => {
    const ws = book.Sheets[name], cells = {}, widths = {}, heights = {};
    for (const [address, cell] of Object.entries(ws)) {
      if (address[0] === '!') continue;
      const { r, c } = XLSX.utils.decode_cell(address);
      const value = cell.f ? `=${cell.f}` : cell.v;
      const s = cell.z ? { numFmt: cell.z } : {};
      cells[keyOf(r, c)] = { ...(value !== undefined && value !== null ? { v: value } : {}), ...(Object.keys(s).length ? { s } : {}) };
    }
    (ws['!cols'] || []).forEach((col, index) => { if (col.wpx) widths[index] = col.wpx; });
    (ws['!rows'] || []).forEach((row, index) => { if (row.hpx) heights[index] = row.hpx; });
    return { name, cells, widths, heights, freezeRows: 0, freezeCols: 0, filters: null, charts: [] };
  }) };
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
const safeName = name => (name || 'Workbook').replace(/[\\/:*?"<>|]/g, '_');
const usedBounds = sheet => {
  let maxRow = 0, maxCol = 0;
  for (const key of Object.keys(sheet.cells)) { const [r, c] = parseKey(key); maxRow = Math.max(maxRow, r); maxCol = Math.max(maxCol, c); }
  return { maxRow, maxCol };
};

export async function exportWorkbook(model, active, format) {
  const filename = `${safeName(model.data.title)}.${format}`;
  if (format === 'csv' || format === 'tsv') {
    const sheet = model.sheet(active), { maxRow, maxCol } = usedBounds(sheet), delimiter = format === 'csv' ? ',' : '\t';
    const rows = [];
    for (let r = 0; r <= maxRow; r++) {
      const values = [];
      for (let c = 0; c <= maxCol; c++) {
        let value = model.value(active, r, c);
        if (value === null || value === undefined) value = '';
        let text = String(value);
        if (text.includes('"') || text.includes(delimiter) || /[\r\n]/.test(text)) text = `"${text.replaceAll('"', '""')}"`;
        values.push(text);
      }
      rows.push(values.join(delimiter));
    }
    download(new Blob(['\uFEFF', rows.join('\r\n')], { type: 'text/plain;charset=utf-8' }), filename);
    return;
  }
  if (format === 'xlsx') {
    const book = new ExcelJS.Workbook();
    for (const sheet of model.data.sheets) {
      const ws = book.addWorksheet(sheet.name, { views: sheet.freezeRows || sheet.freezeCols ? [{ state: 'frozen', xSplit: sheet.freezeCols, ySplit: sheet.freezeRows }] : [] });
      for (const [index, width] of Object.entries(sheet.widths)) ws.getColumn(Number(index) + 1).width = Math.max(2, (width - 8) / 8);
      for (const [index, height] of Object.entries(sheet.heights)) ws.getRow(Number(index) + 1).height = height / 1.33;
      for (const [key, data] of Object.entries(sheet.cells)) {
        const [row, col] = parseKey(key), cell = ws.getCell(row + 1, col + 1), value = data.v;
        if (typeof value === 'string' && value.startsWith('=')) cell.value = { formula: value.slice(1) };
        else if (value !== undefined) cell.value = value;
        const s = data.s || {};
        if (s.numFmt) cell.numFmt = s.numFmt;
        if (s.bold || s.italic || s.color) cell.font = { bold: !!s.bold, italic: !!s.italic, ...(s.color ? { color: { argb: `FF${rgb(s.color)}` } } : {}) };
        if (s.background) cell.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: `FF${rgb(s.background)}` } };
        if (s.align) cell.alignment = { horizontal: s.align };
        if (s.border) cell.border = Object.fromEntries(['top', 'bottom', 'left', 'right'].map(side => [side, { style: 'thin', color: { argb: 'FF888888' } }]));
      }
    }
    download(new Blob([await book.xlsx.writeBuffer()], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }), filename);
    return;
  }
  if (format === 'ods') {
    const book = XLSX.utils.book_new();
    for (const sheet of model.data.sheets) {
      const ws = {}, { maxRow, maxCol } = usedBounds(sheet);
      for (const [key, data] of Object.entries(sheet.cells)) {
        if (data.v === undefined) continue;
        const [r, c] = parseKey(key), address = XLSX.utils.encode_cell({ r, c });
        ws[address] = typeof data.v === 'string' && data.v.startsWith('=')
          ? { t: 'n', f: data.v.slice(1), z: data.s?.numFmt }
          : { t: typeof data.v === 'number' ? 'n' : 's', v: data.v, z: data.s?.numFmt };
      }
      ws['!ref'] = XLSX.utils.encode_range({ s: { r: 0, c: 0 }, e: { r: maxRow, c: maxCol } });
      XLSX.utils.book_append_sheet(book, ws, sheet.name);
    }
    download(new Blob([XLSX.write(book, { type: 'array', bookType: 'ods' })], { type: 'application/vnd.oasis.opendocument.spreadsheet' }), filename);
  }
}
