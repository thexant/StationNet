import test from 'node:test';
import assert from 'node:assert/strict';
import ExcelJS from 'exceljs/dist/exceljs.min.js';
import { WorkbookModel } from '../stationnet/office_sheets/model.js';
import { importWorkbook, exportWorkbook, parseDelimited } from '../stationnet/office_sheets/formats.js';

test('formulas recalculate across sheets and support lookups and errors', () => {
  const model = new WorkbookModel();
  model.set(0, 0, 0, 'Alice'); model.set(0, 0, 1, 10);
  model.set(0, 1, 0, 'Bob'); model.set(0, 1, 1, 20);
  const second = model.addSheet();
  model.set(second, 0, 0, '=SUM(Sheet1!B1:B2)');
  model.set(second, 1, 0, '=VLOOKUP("Bob",Sheet1!A1:B2,2,FALSE)');
  model.set(second, 2, 0, '=XLOOKUP("Alice",Sheet1!A1:A2,Sheet1!B1:B2)');
  model.set(second, 3, 0, '=INDEX(Sheet1!B1:B2,MATCH("Bob",Sheet1!A1:A2,0))');
  model.set(second, 4, 0, '=1/0');
  assert.deepEqual([0, 1, 2, 3].map(row => model.value(second, row, 0)), [30, 20, 10, 20]);
  assert.equal(model.value(second, 4, 0), '#DIV/0!');
  model.set(0, 0, 1, 15);
  assert.equal(model.value(second, 0, 0), 35);
  assert.equal(model.value(second, 2, 0), 15);
});

test('row insertion updates formula references and sparse row 10001 works', () => {
  const model = new WorkbookModel();
  model.set(0, 0, 0, 1); model.set(0, 1, 0, 2);
  model.set(0, 0, 1, '=SUM(A1:A2)');
  model.structural(0, 'row', 1, 1);
  assert.equal(model.raw(0, 0, 1), '=SUM(A1:A3)');
  assert.equal(model.value(0, 0, 1), 3);
  model.set(0, 10000, 0, 42);
  assert.equal(model.value(0, 10000, 0), 42);
  assert.equal(Object.keys(model.sheet(0).cells).length, 4);
});

test('text import handles quoted fields and a chosen legacy encoding', async () => {
  assert.deepEqual(parseDelimited('name,note\r\nA,"line 1\nline 2, yes"', ','), [['name', 'note'], ['A', 'line 1\nline 2, yes']]);
  const bytes = Uint8Array.from([78, 97, 109, 101, 44, 67, 97, 102, 233]);
  const file = { name: 'legacy.csv', arrayBuffer: async () => bytes.buffer };
  const data = await importWorkbook(file, 'windows-1252', ',');
  assert.equal(data.sheets[0].cells['0:1'].v, 'Café');
});

test('XLSX import keeps formulas, sheets, and common formatting', async () => {
  const book = new ExcelJS.Workbook();
  const first = book.addWorksheet('Input'), second = book.addWorksheet('Totals');
  first.getCell('A1').value = 5;
  first.getCell('A1').numFmt = '$#,##0.00';
  first.getCell('A1').font = { bold: true };
  second.getCell('A1').value = { formula: 'Input!A1*2', result: 10 };
  const bytes = await book.xlsx.writeBuffer();
  const data = await importWorkbook({ name: 'report.xlsx', arrayBuffer: async () => bytes });
  assert.deepEqual(data.sheets.map(sheet => sheet.name), ['Input', 'Totals']);
  assert.equal(data.sheets[0].cells['0:0'].s.bold, true);
  assert.equal(data.sheets[0].cells['0:0'].s.numFmt, '$#,##0.00');
  assert.equal(data.sheets[1].cells['0:0'].v, '=Input!A1*2');
});

test('XLSX and ODS exports round trip sheet names and formulas', async () => {
  const model = new WorkbookModel();
  model.data.title = 'Round trip';
  model.set(0, 0, 0, 12);
  model.setStyle(0, { r1: 0, r2: 0, c1: 0, c2: 0 }, { bold: true, numFmt: '$#,##0.00' });
  const second = model.addSheet();
  model.set(second, 0, 0, '=Sheet1!A1*2');
  const previousDocument = globalThis.document, previousCreate = URL.createObjectURL, previousRevoke = URL.revokeObjectURL, previousTimeout = globalThis.setTimeout;
  let output;
  globalThis.document = { createElement: () => ({ click() {} }) };
  URL.createObjectURL = blob => { output = blob; return 'blob:test'; };
  URL.revokeObjectURL = () => {};
  globalThis.setTimeout = () => 0;
  try {
    for (const format of ['xlsx', 'ods']) {
      await exportWorkbook(model, 0, format);
      const data = await importWorkbook({ name: `roundtrip.${format}`, arrayBuffer: () => output.arrayBuffer() });
      assert.deepEqual(data.sheets.map(sheet => sheet.name), ['Sheet1', 'Sheet2']);
      assert.equal(data.sheets[1].cells['0:0'].v, '=Sheet1!A1*2');
      if (format === 'xlsx') assert.equal(data.sheets[0].cells['0:0'].s.bold, true);
    }
  } finally {
    globalThis.document = previousDocument; URL.createObjectURL = previousCreate;
    URL.revokeObjectURL = previousRevoke; globalThis.setTimeout = previousTimeout;
  }
});
