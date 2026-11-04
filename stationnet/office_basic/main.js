import { pbkdf2Async } from '@noble/hashes/pbkdf2.js';
import { sha256 } from '@noble/hashes/sha2.js';
import { gcm } from '@noble/ciphers/aes.js';
import JSZip from 'jszip';
import { rememberOfficeKey, readOfficeKey, forgetOfficeKey } from '../office_vault.js';
import {
  MAX_SOURCE_CHARS, blankProgram, normalizeProgram, cleanTitle, decodeBas, encodeBas, basFilename, titleFromFilename,
  PACKAGE_ENTRYPOINT, PACKAGE_MAX_FILES, PACKAGE_MAX_FILE_BYTES, PACKAGE_MAX_EXPANDED_BYTES, assetPath, buildManifest, defaultPackage, packageSlug,
} from './model.js';
import { createRunnerSession } from './session.js';
import { EXAMPLES } from './examples.js';

const app = document.querySelector('.basic-app');
if (app) start();

function start() {
  /* global CodeMirror */
  document.body.classList.add('basic-page');
  const $ = selector => app.querySelector(selector), api = app.dataset.api, csrf = app.dataset.csrf;
  const enc = new TextEncoder(), dec = new TextDecoder();
  const bytes64 = bytes => { let result = ''; for (let i = 0; i < bytes.length; i += 8192) result += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(result); };
  const un64 = value => Uint8Array.from(atob(value), char => char.charCodeAt(0));
  let key = null, records = [], id = null, revision = null, program = blankProgram();
  let dirty = false, saving = false, conflict = false, generation = 0, timer = null, loading = false;
  let markers = [];

  // CodeMirror measures its gutter once, so it is created when the workspace is
  // first shown and web fonts are ready rather than while the page is hidden.
  let editor = null;
  async function createEditor() {
    if (editor) return;
    await document.fonts?.ready;
    editor = CodeMirror.fromTextArea($('#basic-source'), {
      mode: 'qbjs', theme: 'station', lineNumbers: true, styleActiveLine: true, indentUnit: 4, tabSize: 4, indentWithTabs: false,
      lineWrapping: false, gutters: ['CodeMirror-linenumbers', 'basic-errors'], viewportMargin: 40,
      extraKeys: {
        F5: () => run(), 'Ctrl-Enter': () => run(), 'Cmd-Enter': () => run(), 'Ctrl-S': () => saveNow(), 'Cmd-S': () => saveNow(), Esc: () => stop(),
        Tab: cm => cm.somethingSelected() ? cm.indentSelection('add') : cm.replaceSelection(' '.repeat(cm.getOption('indentUnit')), 'end'),
        'Shift-Tab': cm => cm.indentSelection('subtract'),
      },
    });
    editor.on('change', () => { if (loading) return; if (markers.length) clearMarkers(); markDirty(); });
  }

  function status(value, kind = '') { $('#basic-status').textContent = value; $('#basic-status').dataset.kind = kind; }
  function message(value) { const node = $('#basic-message'); node.textContent = value; node.hidden = !value; }
  function encrypt(value) { const iv = crypto.getRandomValues(new Uint8Array(12)); return { v: 1, iv: bytes64(iv), data: bytes64(gcm(key, iv).encrypt(enc.encode(JSON.stringify(value)))) }; }
  function decrypt(value) { return JSON.parse(dec.decode(gcm(key, un64(value.iv)).decrypt(un64(value.data)))); }
  async function request(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, ...options });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(result.error || `Request failed (${response.status}).`); error.status = response.status; throw error; }
    return result;
  }

  // Files and saving follow the other OFFICE programs: encrypted in the browser,
  // autosaved after edits, and protected against overwriting newer revisions.
  function current() { return { version: 1, title: cleanTitle($('#basic-title').value), source: editor.getValue() }; }
  function show(value, nextId, nextRevision, state) {
    loading = true; program = value; id = nextId; revision = nextRevision; dirty = false; conflict = false;
    $('#basic-title').value = program.title; editor.setValue(program.source); editor.clearHistory(); clearMarkers(); loading = false;
    status(...state); message('');
  }
  function markDirty() { if (!key || conflict || loading) return; dirty = true; generation++; status('UNSAVED', 'pending'); clearTimeout(timer); timer = setTimeout(save, 850); }
  function saveNow() { if (key && !conflict && id === null) { dirty = true; generation++; } return save(); }
  async function save() {
    clearTimeout(timer); if (!key || !dirty || saving || conflict) return;
    const value = current();
    if (value.source.length > MAX_SOURCE_CHARS) { status('TOO LARGE', 'error'); message('Programs are limited to 2 MB of text. Remove some code to save.'); return; }
    saving = true; const target = id, currentRevision = revision, savedGeneration = generation; status('SAVING…', 'saving');
    try {
      const envelope = encrypt(value);
      const result = target === null ? await request(api, { method: 'POST', body: JSON.stringify({ envelope }) }) : await request(`${api}/${target}`, { method: 'PUT', body: JSON.stringify({ envelope, revision: currentRevision }) });
      if (id === target) { if (target === null) id = result.id; revision = result.revision; }
      dirty = generation !== savedGeneration; status(dirty ? 'UNSAVED' : 'SAVED', dirty ? 'pending' : 'saved');
      if (!dirty) message(''); await refreshList();
    } catch (error) {
      if (error.status === 409) { conflict = true; status('REVISION CONFLICT', 'error'); conflictMessage(); }
      else { status('SAVE ERROR', 'error'); message(`Could not save: ${error.message} Edits remain in this tab. Press SAVE to retry.`); }
    } finally { saving = false; if (dirty && !conflict && generation !== savedGeneration) timer = setTimeout(save, 850); }
  }
  function conflictMessage() {
    const box = $('#basic-message'); box.replaceChildren(); box.hidden = false;
    box.append(document.createTextNode('This program changed in another tab or device. Your edits are still here. '));
    const add = (text, fn) => { const button = document.createElement('button'); button.type = 'button'; button.textContent = text; button.onclick = fn; box.append(button); };
    add('SAVE AS COPY', async () => { id = null; revision = null; conflict = false; $('#basic-title').value = cleanTitle(`${$('#basic-title').value} (copy)`); markDirty(); await save(); });
    add('LOAD REMOTE', async () => { if (confirm('Discard edits here and load the saved program?')) { conflict = false; dirty = false; await load(id, true); } });
  }
  async function refreshList() {
    records = (await request(api)).programs; const list = $('#basic-list'); list.replaceChildren();
    for (const record of records) {
      const li = document.createElement('li'), button = document.createElement('button'); button.type = 'button';
      try { button.textContent = cleanTitle(decrypt(record.envelope).title); } catch { button.textContent = 'Unable to decrypt'; }
      button.className = record.id === id ? 'selected' : ''; button.title = `Updated ${window.StationNet?.localTimestamp ? window.StationNet.localTimestamp(record.updated_at) : `${record.updated_at} UTC`}`; button.onclick = () => load(record.id); li.append(button); list.append(li);
    }
    if (!records.length) { const li = document.createElement('li'); li.textContent = 'No saved programs yet.'; list.append(li); }
  }
  async function flush() { if (dirty && !conflict) await save(); return !dirty && !saving && !conflict; }
  async function load(nextId, force = false) {
    if (id === nextId && !force) return; if (!force && !await flush()) return;
    try { const result = await request(`${api}/${nextId}`); show(normalizeProgram(decrypt(result.envelope)), nextId, result.revision, ['SAVED', 'saved']); await refreshList(); }
    catch (error) { message(`Could not open program: ${error.message}`); }
  }
  async function openUnsaved(value, label) {
    if (!await flush()) return false;
    show(value, null, null, [label, 'pending']); await refreshList(); editor.focus(); return true;
  }
  async function makeNew() { if (await openUnsaved(blankProgram(), 'NEW')) { markDirty(); await save(); } }

  // Console and error markers.
  const consoleList = $('#basic-console');
  function log(kind, text, line = 0) {
    const li = document.createElement('li'); li.dataset.kind = kind;
    if (line > 0) { const button = document.createElement('button'); button.type = 'button'; button.textContent = `Line ${line}: ${text}`; button.onclick = () => gotoLine(line); li.append(button); }
    else li.textContent = text;
    consoleList.append(li);
    while (consoleList.childElementCount > 1000) consoleList.firstElementChild.remove();
    consoleList.scrollTop = consoleList.scrollHeight;
    return li;
  }
  function gotoLine(line) {
    showView('code');
    const index = Math.max(0, Math.min(editor.lineCount() - 1, line - 1));
    editor.focus(); editor.setCursor({ line: index, ch: 0 }); editor.scrollIntoView({ line: index, ch: 0 }, 80);
  }
  function mark(line) {
    if (line < 1 || line > editor.lineCount()) return;
    const handle = editor.getLineHandle(line - 1), marker = document.createElement('span');
    marker.className = 'basic-gutter-marker'; marker.textContent = '●'; marker.title = 'Error on this line';
    editor.setGutterMarker(handle, 'basic-errors', marker); editor.addLineClass(handle, 'background', 'basic-error-line'); markers.push(handle);
  }
  function clearMarkers() { for (const handle of markers) { editor.setGutterMarker(handle, 'basic-errors', null); editor.removeLineClass(handle, 'background', 'basic-error-line'); } markers = []; }

  // Programs run through the shared runner session (session.js).
  const session = createRunnerSession({
    container: $('#basic-screen'), runnerUrl: app.dataset.runner, onLog: log, onMessage: receive,
    onState: (state, label) => {
      const node = $('#basic-run-state'); node.dataset.state = state; node.textContent = label;
      $('#basic-stop').disabled = !session.active;
      if (state === 'ended') log('info', 'Program finished.');
      if (!session.active) { $('#basic-screen-idle').textContent = 'Program stopped. Press RUN (F5) to start it again.'; $('#basic-screen-idle').hidden = false; }
    },
  });
  function run() {
    if (!key) return;
    clearMarkers();
    const source = editor.getValue();
    if (source.length > MAX_SOURCE_CHARS) { log('error', 'Programs are limited to 2 MB of text.'); return; }
    $('#basic-screen-idle').hidden = true;
    log('info', `— RUN ${cleanTitle($('#basic-title').value)} —`);
    session.run(source); showView('run');
  }
  function stop() { session.stop(); }
  function receive(data) {
    if (data.type === 'warnings') {
      for (const item of data.items) {
        const where = item.module ? `${item.module} line ${item.line}: ` : '';
        log(item.error ? 'error' : 'warning', `${item.error ? 'ERROR' : 'WARN'} ${where}${item.text}`, item.module ? 0 : item.line);
        if (item.error && !item.module) mark(item.line);
      }
    } else if (data.type === 'error') { log('error', `RUNTIME ERROR: ${data.text}`, data.line); mark(data.line); }
    else if (data.type === 'echo') log('output', data.text);
    else if (data.type === 'file') {
      const li = log('info', `The program offered a file: ${data.name} (${data.data.byteLength} bytes)`), button = document.createElement('button');
      button.type = 'button'; button.className = 'basic-console-save'; button.textContent = 'SAVE FILE';
      button.onclick = () => download(new Blob([data.data], { type: 'application/octet-stream' }), data.name); li.append(button);
    }
  }
  function showView(view) {
    $('.basic-body').dataset.view = view;
    for (const tab of app.querySelectorAll('[data-view]')) if (tab.tagName === 'BUTTON') tab.setAttribute('aria-selected', String(tab.dataset.view === view));
    if (view === 'code') editor?.refresh();
  }

  function download(blob, name) { const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000); }
  function exportBas() { download(new Blob([encodeBas(editor.getValue())], { type: 'text/plain;charset=utf-8' }), basFilename($('#basic-title').value)); }

  // Export as a .bas file or as a StationNet Package: a ZIP with a generated
  // manifest that a sysop can install through Manage Programs. Exporting never
  // installs anything; the ZIP is only downloaded.
  const exportDialog = $('#basic-export-dialog'), exportForm = $('#basic-export-form');
  let slugEdited = false;
  function exportFormat() { return exportForm.elements.format.value; }
  function showExportFields() { $('#basic-package-fields').hidden = exportFormat() !== 'package'; }
  function exportError(text) { const node = $('#basic-export-error'); node.textContent = text; node.hidden = !text; }
  function openExport() {
    const defaults = defaultPackage($('#basic-title').value, app.dataset.author), fields = exportForm.elements;
    for (const name of ['name', 'slug', 'description', 'author', 'version', 'category', 'platforms', 'presentation']) fields[name].value = defaults[name];
    for (const box of exportForm.querySelectorAll('input[name=inputs]')) box.checked = false;
    fields.assets.value = ''; slugEdited = false;
    for (const node of exportForm.querySelectorAll('[data-error-for]')) node.textContent = '';
    exportError(''); showExportFields(); exportDialog.showModal();
  }
  function packageAssets(files) {
    if (files.length > PACKAGE_MAX_FILES - 2) throw new Error(`Packages can include at most ${PACKAGE_MAX_FILES - 2} asset files.`);
    const result = new Map(); let total = 0;
    for (const file of files) {
      const path = assetPath(file.name);
      if ([...result.keys()].some(item => item.toLowerCase() === path.toLowerCase())) throw new Error(`Two assets would both be named ${path}.`);
      if (file.size > PACKAGE_MAX_FILE_BYTES) throw new Error(`${file.name} is larger than the 1 MiB per-file limit.`);
      total += file.size; result.set(path, file);
    }
    return { files: result, total };
  }
  async function exportPackage() {
    const fields = exportForm.elements;
    const { manifest, errors } = buildManifest({
      name: fields.name.value, slug: fields.slug.value, description: fields.description.value, author: fields.author.value,
      version: fields.version.value, category: fields.category.value, platforms: fields.platforms.value,
      presentation: fields.presentation.value, inputs: [...exportForm.querySelectorAll('input[name=inputs]:checked')].map(box => box.value),
    });
    for (const node of exportForm.querySelectorAll('[data-error-for]')) node.textContent = errors[node.dataset.errorFor] || '';
    if (!manifest) { exportError('Check the highlighted fields.'); return false; }
    const source = encodeBas(editor.getValue());
    if (!editor.getValue().trim()) { exportError('Write a program before exporting it as a package.'); return false; }
    if (source.byteLength > PACKAGE_MAX_FILE_BYTES) { exportError('Packaged programs are limited to 1 MiB of source.'); return false; }
    const { files, total } = packageAssets([...fields.assets.files]);
    const manifestBytes = new TextEncoder().encode(`${JSON.stringify(manifest, null, 2)}\n`);
    if (total + source.byteLength + manifestBytes.byteLength > PACKAGE_MAX_EXPANDED_BYTES) { exportError('Packages are limited to 4 MiB of files in total.'); return false; }
    const zip = new JSZip();
    zip.file('manifest.json', manifestBytes); zip.file(PACKAGE_ENTRYPOINT, source);
    for (const [path, file] of files) zip.file(path, await file.arrayBuffer(), { createFolders: false });
    download(await zip.generateAsync({ type: 'blob', compression: 'DEFLATE', mimeType: 'application/zip' }), `${manifest.slug}.zip`);
    log('info', `Exported ${manifest.name} as ${manifest.slug}.zip. A sysop can install it from Manage Programs.`);
    return true;
  }
  exportForm.onchange = event => { if (event.target.name === 'format') showExportFields(); };
  exportForm.elements.slug.oninput = () => { slugEdited = true; };
  exportForm.elements.name.oninput = () => { if (!slugEdited) exportForm.elements.slug.value = packageSlug(exportForm.elements.name.value); };
  for (const button of exportDialog.querySelectorAll('[data-close]')) button.onclick = () => exportDialog.close();
  exportForm.onsubmit = async event => {
    event.preventDefault(); exportError('');
    try {
      if (exportFormat() === 'bas') { exportBas(); exportDialog.close(); }
      else if (await exportPackage()) exportDialog.close();
    } catch (error) { exportError(`Export failed: ${error.message}`); }
  };
  async function importBas(file) {
    if (!file) return;
    try {
      if (file.size > 8 * 1024 * 1024) throw new Error('Choose a BASIC file no larger than 2 MB of text.');
      const source = decodeBas(new Uint8Array(await file.arrayBuffer()));
      if (await openUnsaved(blankProgram(titleFromFilename(file.name), source), 'IMPORTED')) { markDirty(); await save(); }
    } catch (error) { message(`Import failed: ${error.message}`); }
  }

  async function unlock(event) {
    event.preventDefault(); const pass = $('#basic-password').value, errorBox = $('#basic-unlock-error'); errorBox.hidden = true; if (pass.length < 12) return; status('UNLOCKING…', 'saving');
    try {
      const vault = await request(app.dataset.vault, { method: 'POST', body: '{}' });
      const [programs, documents, sheets, presentations, agenda, drawings] = await Promise.all([request(api), request(app.dataset.documentsApi), request(app.dataset.sheetsApi), request(app.dataset.presentationsApi), request(app.dataset.agendaApi), request(app.dataset.drawApi)]);
      const first = programs.programs[0]?.envelope || documents.documents[0]?.envelope || sheets.sheets[0]?.envelope || presentations.presentations[0]?.envelope || agenda.agenda?.envelope || drawings.drawings[0]?.envelope;
      if (!first) { if ($('#basic-confirm-row').hidden) { $('#basic-confirm-row').hidden = false; $('#basic-confirm').required = true; status('CONFIRM NEW PASSWORD', 'pending'); $('#basic-confirm').focus(); return; } if ($('#basic-confirm').value !== pass) throw new Error('The new passwords do not match.'); }
      key = await pbkdf2Async(sha256, enc.encode(pass), un64(vault.salt), { c: 210000, dkLen: 32, asyncTick: 20 }); if (first) decrypt(first); rememberOfficeKey(key, csrf, $('#basic-remember').checked);
      $('#basic-password').value = ''; $('#basic-confirm').value = ''; $('#basic-confirm-row').hidden = true;
      await openWorkspace();
    } catch (error) { key?.fill(0); key = null; errorBox.textContent = `Could not unlock. Check your OFFICE Password and try again. ${error.message}`; errorBox.hidden = false; status('LOCKED', 'error'); }
  }
  async function openWorkspace() {
    $('#basic-unlock').hidden = true; $('#basic-workspace').hidden = false; $('#basic-lock').hidden = false;
    if (editor) editor.refresh(); else await createEditor();
    await refreshList();
    if (records.length) await load(records[0].id);
    else await openUnsaved(blankProgram(EXAMPLES[0].title, EXAMPLES[0].source), 'EXAMPLE — NOT SAVED');
  }
  function lock() {
    if (dirty || saving || conflict) { save(); message('Wait for the SAVED indicator before locking.'); return; }
    stop(); forgetOfficeKey(); key?.fill(0); key = null; records = []; show(blankProgram(), null, null, ['LOCKED', '']);
    $('#basic-list').replaceChildren(); consoleList.replaceChildren(); $('#basic-workspace').hidden = true; $('#basic-unlock').hidden = false; $('#basic-lock').hidden = true;
  }

  for (const example of EXAMPLES) { const option = document.createElement('option'); option.value = example.id; option.textContent = example.title; option.title = example.summary; $('#basic-examples').append(option); }
  $('#basic-examples').onchange = async event => {
    const example = EXAMPLES.find(item => item.id === event.target.value); event.target.value = '';
    if (example) await openUnsaved(blankProgram(example.title, example.source), 'EXAMPLE — NOT SAVED');
  };
  $('#basic-unlock-form').onsubmit = unlock; $('#basic-lock').onclick = lock; $('#basic-save').onclick = saveNow; $('#basic-new').onclick = makeNew; $('#basic-title').oninput = markDirty;
  $('#basic-run').onclick = () => run(); $('#basic-stop').onclick = () => stop(); $('#basic-export').onclick = openExport;
  $('#basic-fullscreen').onclick = () => { const screen = $('#basic-screen'); (document.fullscreenElement ? document.exitFullscreen() : screen.requestFullscreen?.())?.catch?.(() => {}); session.focus(); };
  $('#basic-console-clear').onclick = () => consoleList.replaceChildren();
  $('#basic-import').onclick = () => $('#basic-import-file').click();
  $('#basic-import-file').onchange = event => { const file = event.target.files[0]; event.target.value = ''; importBas(file); };
  $('#basic-delete').onclick = async () => {
    if (id === null) return; if (!confirm('Delete this program permanently?')) return;
    try { await request(`${api}/${id}`, { method: 'DELETE' }); dirty = false; id = null; await refreshList(); if (records.length) await load(records[0].id, true); else await openUnsaved(blankProgram(), 'NEW — NOT SAVED'); }
    catch (error) { message(error.message); }
  };
  for (const tab of app.querySelectorAll('button[data-view]')) tab.onclick = () => showView(tab.dataset.view);
  document.addEventListener('keydown', event => {
    if ($('#basic-workspace').hidden) return;
    if (event.key === 'F5') { event.preventDefault(); run(); }
    else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); saveNow(); }
  });
  window.addEventListener('beforeunload', event => { if (dirty || saving || conflict) { event.preventDefault(); event.returnValue = ''; } });
  (async () => { key = readOfficeKey(csrf); if (!key) return; let badKey = false; try { const result = await request(api); if (result.programs[0]) { try { decrypt(result.programs[0].envelope); } catch (error) { badKey = true; throw error; } } await openWorkspace(); }
    catch (error) { key?.fill(0); key = null; if (badKey) forgetOfficeKey(); status('LOCKED'); $('#basic-workspace').hidden = true; $('#basic-unlock').hidden = false; $('#basic-lock').hidden = true; message(`Could not load BASIC: ${error.message}`); } })();
}
