import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import Image from '@tiptap/extension-image';
import { TableKit } from '@tiptap/extension-table';
import TextAlign from '@tiptap/extension-text-align';
import { pbkdf2Async } from '@noble/hashes/pbkdf2.js';
import { sha256 } from '@noble/hashes/sha2.js';
import { gcm } from '@noble/ciphers/aes.js';
import { rememberOfficeKey, readOfficeKey, forgetOfficeKey } from '../office_vault.js';
import { cleanHtml, importFile, exportFile } from './formats.js';

const app = document.querySelector('.documents-app');
if (app) initialize();

function initialize() {
  document.body.classList.add('documents-page');
  const $ = selector => app.querySelector(selector);
  const api = app.dataset.api;
  const csrf = app.dataset.csrf;
  const textEncoder = new TextEncoder();
  const textDecoder = new TextDecoder();
  let key = null, editor = null, documents = [], currentId = null, revision = null;
  let dirty = false, saving = false, conflict = false, saveTimer = null, findIndex = -1;
  let generation = 0;

  const status = (value, kind = '') => { $('#document-status').textContent = value; $('#document-status').dataset.kind = kind; };
  const message = (value, kind = 'error') => {
    const target = $('#document-message');
    target.replaceChildren();
    target.textContent = value;
    target.dataset.kind = kind;
    target.hidden = !value;
  };
  const b64 = bytes => {
    let result = '';
    for (let i = 0; i < bytes.length; i += 8192) result += String.fromCharCode(...bytes.subarray(i, i + 8192));
    return btoa(result);
  };
  const unb64 = value => Uint8Array.from(atob(value), char => char.charCodeAt(0));
  const encrypt = data => {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ciphertext = gcm(key, iv).encrypt(textEncoder.encode(JSON.stringify(data)));
    return { v: 1, iv: b64(iv), data: b64(ciphertext) };
  };
  const decrypt = envelope => JSON.parse(textDecoder.decode(gcm(key, unb64(envelope.iv)).decrypt(unb64(envelope.data))));
  const requestJson = async (url, options = {}) => {
    const response = await fetch(url, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      ...options,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(result.error || `Request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return result;
  };
  const docData = () => ({ title: $('#document-title').value.trim() || 'Untitled document', html: cleanHtml(editor.getHTML()) });

  function scheduleSave() {
    if (!key || conflict) return;
    dirty = true;
    generation++;
    status('UNSAVED', 'pending');
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 750);
  }

  async function save() {
    clearTimeout(saveTimer);
    if (!key || !dirty || saving || conflict) return;
    saving = true;
    const savedGeneration = generation;
    const targetId = currentId;
    const targetRevision = revision;
    status('SAVING…', 'saving');
    try {
      const envelope = encrypt(docData());
      const result = targetId === null
        ? await requestJson(api, { method: 'POST', body: JSON.stringify({ envelope }) })
        : await requestJson(`${api}/${targetId}`, { method: 'PUT', body: JSON.stringify({ envelope, revision: targetRevision }) });
      if (currentId === targetId) {
        if (targetId === null) currentId = result.id;
        revision = result.revision;
      }
      dirty = generation !== savedGeneration;
      status(dirty ? 'UNSAVED' : 'SAVED', dirty ? 'pending' : 'saved');
      message('');
      await refreshList();
    } catch (error) {
      if (error.status === 409) {
        conflict = true;
        status('REVISION CONFLICT', 'error');
        showConflict();
      } else {
        status('SAVE ERROR', 'error');
        message(`Could not save: ${error.message} Your edits are still in this tab. Press SAVE to retry.`);
      }
    } finally {
      saving = false;
      if (dirty && !conflict && key && generation !== savedGeneration) saveTimer = setTimeout(save, 750);
    }
  }

  function showConflict() {
    const box = $('#document-message');
    box.replaceChildren();
    box.hidden = false;
    box.append(document.createTextNode('This document changed in another tab or device. Your edits are still here. '));
    const copy = document.createElement('button');
    copy.type = 'button'; copy.textContent = 'SAVE AS COPY';
    copy.onclick = async () => {
      currentId = null; revision = null; conflict = false;
      $('#document-title').value += ' (conflict copy)';
      generation++; dirty = true; message(''); await save();
    };
    const reload = document.createElement('button');
    reload.type = 'button'; reload.textContent = 'LOAD REMOTE';
    reload.onclick = async () => {
      if (!confirm('Discard edits in this tab and load the latest saved revision?')) return;
      conflict = false; dirty = false; message(''); await load(currentId, true);
    };
    box.append(copy, reload);
  }

  async function refreshList() {
    const result = await requestJson(api);
    documents = result.documents;
    const list = $('#document-list');
    list.replaceChildren();
    for (const record of documents) {
      let title = 'Unable to decrypt';
      try { title = decrypt(record.envelope).title || 'Untitled document'; } catch { /* Other key or damaged record. */ }
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = record.id === currentId ? 'selected' : '';
      button.textContent = title;
      button.title = `Updated ${record.updated_at} UTC`;
      button.onclick = () => load(record.id);
      item.append(button);
      list.append(item);
    }
    if (!documents.length) {
      const item = document.createElement('li');
      item.className = 'documents-empty'; item.textContent = 'No saved documents yet.'; list.append(item);
    }
  }

  async function load(id, force = false) {
    if (id === currentId && !force) return;
    if ((dirty || conflict) && !force) {
      await save();
      if (dirty || conflict) return;
    }
    try {
      const record = await requestJson(`${api}/${id}`);
      const data = decrypt(record.envelope);
      currentId = id; revision = record.revision; dirty = false; conflict = false;
      $('#document-title').value = data.title || 'Untitled document';
      editor.commands.setContent(cleanHtml(data.html || '<p></p>'), { emitUpdate: false });
      status('SAVED', 'saved'); message(''); updateWords(); await refreshList();
    } catch (error) {
      message(`Could not open document: ${error.message}`);
    }
  }

  async function newDocument() {
    if (dirty || conflict) {
      await save();
      if (dirty || conflict) return;
    }
    currentId = null; revision = null; dirty = false; conflict = false;
    $('#document-title').value = 'Untitled document';
    editor.commands.setContent('<p></p>', { emitUpdate: false });
    status('NEW DOCUMENT', 'pending'); message(''); updateWords(); await refreshList();
    editor.commands.focus();
  }

  function updateWords() {
    if (!editor) return;
    const words = editor.getText().trim().match(/\S+/g)?.length || 0;
    $('#document-word-count').textContent = `${words} ${words === 1 ? 'word' : 'words'}`;
  }

  function matches() {
    const needle = $('#document-find-text').value.toLowerCase();
    if (!needle) return [];
    const result = [];
    let text = '';
    const positions = [];
    let previousEnd = null;
    editor.state.doc.descendants((node, pos) => {
      if (!node.isText) return;
      if (previousEnd !== null && pos > previousEnd) { text += '\n'; positions.push(null); }
      for (let i = 0; i < node.text.length; i++) { text += node.text[i]; positions.push(pos + i); }
      previousEnd = pos + node.text.length;
    });
    const haystack = text.toLowerCase();
    let offset = 0;
    while ((offset = haystack.indexOf(needle, offset)) !== -1) {
      const span = positions.slice(offset, offset + needle.length);
      if (span.every(value => value !== null)) result.push({ from: span[0], to: span.at(-1) + 1 });
      offset += Math.max(needle.length, 1);
    }
    $('#document-find-count').textContent = `${result.length} found`;
    return result;
  }

  function nextMatch() {
    const found = matches();
    if (!found.length) return;
    const selection = editor.state.selection;
    findIndex = found.findIndex(match => match.from > selection.from);
    if (findIndex < 0) findIndex = 0;
    editor.chain().focus().setTextSelection(found[findIndex]).run();
  }

  function replaceOne() {
    const found = matches();
    const current = found.find(match => match.from === editor.state.selection.from && match.to === editor.state.selection.to);
    if (!current) return nextMatch();
    editor.chain().focus().insertContentAt(current, $('#document-replace-text').value).run();
    matches();
  }

  function replaceAll() {
    const found = matches();
    const replacement = $('#document-replace-text').value;
    for (const match of found.reverse()) editor.commands.insertContentAt(match, replacement);
    matches();
  }

  function command(name) {
    const chain = editor.chain().focus();
    const commands = {
      bold: () => chain.toggleBold().run(), italic: () => chain.toggleItalic().run(),
      underline: () => chain.toggleUnderline().run(), strike: () => chain.toggleStrike().run(),
      bulletList: () => chain.toggleBulletList().run(), orderedList: () => chain.toggleOrderedList().run(),
      alignLeft: () => chain.setTextAlign('left').run(), alignCenter: () => chain.setTextAlign('center').run(), alignRight: () => chain.setTextAlign('right').run(),
      blockquote: () => chain.toggleBlockquote().run(), codeBlock: () => chain.toggleCodeBlock().run(),
      horizontalRule: () => chain.setHorizontalRule().run(), table: () => chain.insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(),
      addRow: () => chain.addRowAfter().run(), addColumn: () => chain.addColumnAfter().run(), deleteTable: () => chain.deleteTable().run(),
      undo: () => chain.undo().run(), redo: () => chain.redo().run(),
      link: () => {
        const url = prompt('Link URL (https://, http://, or mailto:)', editor.getAttributes('link').href || 'https://');
        if (url === null) return;
        if (!url) return chain.unsetLink().run();
        if (!/^(https?:\/\/|mailto:)/i.test(url)) return message('Links must start with https://, http://, or mailto:.');
        chain.setLink({ href: url }).run();
      },
      image: () => $('#document-image').click(),
    };
    commands[name]?.();
  }

  async function unlock(event) {
    event.preventDefault();
    const passphrase = $('#document-passphrase').value;
    if (passphrase.length < 12) return;
    const errorBox = $('#document-unlock-error');
    errorBox.hidden = true; status('UNLOCKING…', 'saving');
    try {
      if (!crypto?.getRandomValues) throw new Error('This browser lacks secure random number generation.');
      const vault = await requestJson(app.dataset.vault, { method: 'POST', body: '{}' });
      const result = await requestJson(api);
      const sheets = await requestJson(app.dataset.sheetsApi);
      const presentations = await requestJson(app.dataset.presentationsApi);
      const agenda = await requestJson(app.dataset.agendaApi);
      if (!result.documents.length && !sheets.sheets.length && !presentations.presentations.length && !agenda.agenda) {
        const confirmRow = $('#document-confirm-row');
        if (confirmRow.hidden) {
          confirmRow.hidden = false;
          $('#document-passphrase-confirm').required = true;
          status('CONFIRM NEW PASSPHRASE', 'pending');
          $('#document-passphrase-confirm').focus();
          return;
        }
        if ($('#document-passphrase-confirm').value !== passphrase) throw new Error('The new passphrases do not match.');
      }
      key = await pbkdf2Async(sha256, textEncoder.encode(passphrase), unb64(vault.salt), { c: 210000, dkLen: 32, asyncTick: 20 });
      if (result.documents.length) decrypt(result.documents[0].envelope);
      else if (sheets.sheets.length) decrypt(sheets.sheets[0].envelope);
      else if (presentations.presentations.length) decrypt(presentations.presentations[0].envelope);
      else if (agenda.agenda) decrypt(agenda.agenda.envelope);
      rememberOfficeKey(key, csrf);
      $('#document-passphrase').value = '';
      $('#document-passphrase-confirm').value = '';
      $('#document-confirm-row').hidden = true;
      $('#document-passphrase-confirm').required = false;
      $('#document-unlock').hidden = true;
      $('#document-workspace').hidden = false;
      $('#document-lock').hidden = false;
      await refreshList();
      if (documents.length) await load(documents[0].id);
      else await newDocument();
    } catch (error) {
      key?.fill(0); key = null;
      errorBox.textContent = 'Could not unlock. Check your OFFICE Password and try again. ' + error.message;
      errorBox.hidden = false; status('LOCKED', 'error');
    }
  }

  function lock() {
    if (dirty || saving) { save(); message('Wait for the SAVED indicator before locking.'); return; }
    forgetOfficeKey();
    key?.fill(0); key = null; documents = []; currentId = null; revision = null;
    editor.commands.setContent('<p></p>', { emitUpdate: false });
    $('#document-title').value = '';
    $('#document-workspace').hidden = true; $('#document-unlock').hidden = false;
    $('#document-lock').hidden = true; status('LOCKED');
  }

  editor = new Editor({
    element: $('#document-editor'),
    extensions: [StarterKit.configure({ link: { openOnClick: false } }), Image.configure({ allowBase64: true }), TableKit, TextAlign.configure({ types: ['heading', 'paragraph'] })],
    content: '<p></p>',
    onUpdate: () => { updateWords(); scheduleSave(); matches(); },
    onSelectionUpdate: () => {
      $('#document-block').value = editor.isActive('heading', { level: 1 }) ? 'h1' : editor.isActive('heading', { level: 2 }) ? 'h2' : editor.isActive('heading', { level: 3 }) ? 'h3' : 'paragraph';
      app.querySelectorAll('[data-command]').forEach(button => { button.setAttribute('aria-pressed', editor.isActive(button.dataset.command) ? 'true' : 'false'); });
    },
  });
  updateWords();

  $('#document-unlock-form').addEventListener('submit', unlock);
  $('#document-lock').onclick = lock;
  $('#document-save').onclick = save;
  $('#document-new').onclick = newDocument;
  $('#document-title').addEventListener('input', scheduleSave);
  $('#document-delete').onclick = async () => {
    if (currentId === null) return newDocument();
    if (!confirm('Delete this document permanently?')) return;
    try {
      await requestJson(`${api}/${currentId}`, { method: 'DELETE' });
      dirty = false; conflict = false; await newDocument();
    } catch (error) { message(`Could not delete document: ${error.message}`); }
  };
  $('#document-import').onclick = () => $('#document-file').click();
  $('#document-file').onchange = async event => {
    const file = event.target.files[0];
    event.target.value = '';
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) return message('Imports must be 20 MB or smaller.');
    if (dirty || conflict) { await save(); if (dirty || conflict) return; }
    try {
      const html = await importFile(file);
      currentId = null; revision = null;
      $('#document-title').value = file.name.replace(/\.[^.]+$/, '').slice(0, 160);
      editor.commands.setContent(html || '<p></p>', { emitUpdate: false });
      generation++; dirty = true; await save(); updateWords();
    } catch (error) { message(`Import failed: ${error.message}`); }
  };
  $('#document-image').onchange = async event => {
    const file = event.target.files[0]; event.target.value = '';
    if (!file) return;
    if (!/^image\/(png|jpeg|gif|webp)$/.test(file.type) || file.size > 2 * 1024 * 1024) return message('Choose a PNG, JPEG, GIF, or WebP image under 2 MB.');
    const bytes = new Uint8Array(await file.arrayBuffer());
    editor.chain().focus().setImage({ src: `data:${file.type};base64,${b64(bytes)}`, alt: file.name }).run();
  };
  app.querySelectorAll('[data-command]').forEach(button => button.addEventListener('click', () => command(button.dataset.command)));
  $('#document-block').onchange = event => {
    const value = event.target.value;
    if (value === 'paragraph') editor.chain().focus().setParagraph().run();
    else editor.chain().focus().setHeading({ level: Number(value[1]) }).run();
  };
  $('#document-find-toggle').onclick = () => { $('#document-find').hidden = !$('#document-find').hidden; if (!$('#document-find').hidden) $('#document-find-text').focus(); };
  $('#document-find-text').oninput = () => { findIndex = -1; matches(); };
  $('#document-find-next').onclick = nextMatch;
  $('#document-replace-one').onclick = replaceOne;
  $('#document-replace-all').onclick = replaceAll;
  $('#document-print').onclick = () => window.print();
  $('#document-theme').onclick = () => {
    const printTheme = app.classList.toggle('print-theme');
    $('#document-theme').setAttribute('aria-pressed', String(printTheme));
    $('#document-theme').textContent = printTheme ? 'STATION THEME' : 'PRINT THEME';
  };
  $('#document-export').onclick = () => $('#document-export-dialog').showModal();
  $('#document-export-dialog').addEventListener('close', async event => {
    const format = event.target.returnValue;
    if (!['docx','pdf','odt','html','md','txt'].includes(format)) return;
    try {
      status('EXPORTING…', 'saving');
      await exportFile(format, $('#document-title').value.trim() || 'Untitled document', editor.getHTML(), editor.getText());
      status(dirty ? 'UNSAVED' : 'SAVED', dirty ? 'pending' : 'saved');
    } catch (error) { status('EXPORT ERROR', 'error'); message(`Export failed: ${error.message}`); }
  });
  document.addEventListener('keydown', event => {
    if (!app.contains(event.target) || !(event.ctrlKey || event.metaKey)) return;
    const keyName = event.key.toLowerCase();
    if (keyName === 's') { event.preventDefault(); save(); }
    if (keyName === 'f') { event.preventDefault(); $('#document-find').hidden = false; $('#document-find-text').focus(); }
    if (keyName === 'p') { event.preventDefault(); window.print(); }
    if (keyName === 'k') { event.preventDefault(); command('link'); }
  });
  window.addEventListener('beforeunload', event => { if (dirty || saving || conflict) { event.preventDefault(); event.returnValue = ''; } });
  (async () => {
    key = readOfficeKey(csrf);
    if (!key) return;
    let badKey = false;
    try {
      const result = await requestJson(api);
      if (result.documents.length) { try { decrypt(result.documents[0].envelope); } catch (error) { badKey = true; throw error; } }
      $('#document-unlock').hidden = true; $('#document-workspace').hidden = false; $('#document-lock').hidden = false;
      await refreshList();
      if (documents.length) await load(documents[0].id); else await newDocument();
    } catch {
      key?.fill(0); key = null; if (badKey) forgetOfficeKey(); status('LOCKED');
      $('#document-workspace').hidden = true; $('#document-unlock').hidden = false;
    }
  })();
}
