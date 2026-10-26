import { pbkdf2Async } from '@noble/hashes/pbkdf2.js';
import { sha256 } from '@noble/hashes/sha2.js';
import { gcm } from '@noble/ciphers/aes.js';
import { rememberOfficeKey, readOfficeKey, forgetOfficeKey } from '../office_vault.js';
import { jsPDF } from 'jspdf';
import { exportPptx, importPptx } from './formats.js';

const app = document.querySelector('.presentations-app');
if (app) initialize();

function initialize() {
  document.body.classList.add('presentations-page');
  const $ = selector => app.querySelector(selector), api = app.dataset.api, csrf = app.dataset.csrf;
  const enc = new TextEncoder(), dec = new TextDecoder();
  let key = null, records = [], id = null, revision = null, data, active = 0, selected = new Set();
  let dirty = false, saving = false, conflict = false, generation = 0, timer = null, undo = [], redo = [], gesture = null, presenting = false, startTouch = null;
  const canvas = $('#presentations-canvas'), presenter = $('#presentations-presenter'), presentSlide = $('#presentations-presenter-slide');
  const status = (value, kind = '') => { $('#presentations-status').textContent = value; $('#presentations-status').dataset.kind = kind; };
  const message = value => { const box = $('#presentations-message'); box.replaceChildren(); box.textContent = value; box.hidden = !value; };
  const b64 = bytes => { let s = ''; for (let i = 0; i < bytes.length; i += 8192) s += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(s); };
  const unb64 = value => Uint8Array.from(atob(value), c => c.charCodeAt(0));
  const encrypt = value => { const iv = crypto.getRandomValues(new Uint8Array(12)); return { v: 1, iv: b64(iv), data: b64(gcm(key, iv).encrypt(enc.encode(JSON.stringify(value)))) }; };
  const decrypt = envelope => JSON.parse(dec.decode(gcm(key, unb64(envelope.iv)).decrypt(unb64(envelope.data))));
  const newId = () => crypto.randomUUID();
  data = blank();
  const slide = () => data.slides[active];
  const selectedObjects = () => slide().elements.filter(object => selected.has(object.id));
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  async function request(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, ...options });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(result.error || `Request failed (${response.status}).`); error.status = response.status; throw error; }
    return result;
  }
  function blank() { return { title: 'Untitled presentation', theme: 'station', slides: [newSlide('title')] }; }
  function newSlide(layout = 'blank') { return { id: newId(), layout, background: '', backgroundImage: null, transition: 'none', notes: '', elements: layoutElements(layout) }; }
  function layoutElements(layout) {
    const text = (value, x, y, w, h, size, align = 'left') => ({ id: newId(), type: 'text', text: value, x, y, w, h, rotation: 0, font: 'Arial', fontSize: size, color: '', align, list: 'none', bold: false, italic: false, underline: false });
    if (layout === 'title') return [text('Presentation title', 110, 150, 740, 90, 48, 'center'), text('Subtitle', 160, 285, 640, 60, 26, 'center')];
    if (layout === 'title-body') return [text('Slide title', 70, 45, 820, 72, 40), text('Click twice to edit this text', 80, 150, 800, 310, 28)];
    if (layout === 'two-column') return [text('Slide title', 70, 42, 820, 72, 40), text('Left column', 70, 155, 390, 300, 26), text('Right column', 505, 155, 390, 300, 26)];
    return [];
  }
  function themeColors() {
    const css = getComputedStyle(document.body);
    if (data.theme === 'light') return { bg: '#ffffff', text: '#142631', accent: '#008e83' };
    if (data.theme === 'dark') return { bg: '#14212b', text: '#f2f6f7', accent: '#4fe0ce' };
    if (data.theme === 'warm') return { bg: '#fff3df', text: '#47311e', accent: '#c66b31' };
    const value = name => css.getPropertyValue(name).trim();
    return { bg: value('--surface-field') || '#101c20', text: value('--green-bright') || '#c4ffd7', accent: value('--cyan') || '#00d4d0' };
  }
  const background = s => s.background || themeColors().bg;
  const foreground = object => object.color || themeColors().text;
  function normalize(value) {
    if (!value || !Array.isArray(value.slides) || !value.slides.length) throw new Error('Invalid presentation data.');
    value.theme ||= 'station'; value.title ||= 'Untitled presentation';
    for (const s of value.slides) { s.id ||= newId(); s.layout ||= 'blank'; s.elements ||= []; s.notes ||= ''; s.transition ||= 'none'; for (const object of s.elements) object.id ||= newId(); }
    return value;
  }
  function markDirty() { if (!key || conflict) return; dirty = true; generation++; status('UNSAVED', 'pending'); clearTimeout(timer); timer = setTimeout(save, 800); }
  async function save() {
    clearTimeout(timer); if (!key || !dirty || saving || conflict) return;
    saving = true; const target = id, rev = revision, savedGeneration = generation; status('SAVING…', 'saving');
    try {
      data.title = $('#presentations-title').value.trim() || 'Untitled presentation';
      const envelope = encrypt(data);
      const result = target === null ? await request(api, { method: 'POST', body: JSON.stringify({ envelope }) }) : await request(`${api}/${target}`, { method: 'PUT', body: JSON.stringify({ envelope, revision: rev }) });
      if (id === target) { if (target === null) id = result.id; revision = result.revision; }
      dirty = generation !== savedGeneration; status(dirty ? 'UNSAVED' : 'SAVED', dirty ? 'pending' : 'saved'); if (!dirty) message('');
      await refreshList();
    } catch (error) {
      if (error.status === 409) { conflict = true; status('REVISION CONFLICT', 'error'); showConflict(); }
      else { status('SAVE ERROR', 'error'); message(`Could not save: ${error.message} Your edits remain in this tab. Press SAVE to retry.`); }
    } finally { saving = false; if (dirty && !conflict && key && generation !== savedGeneration) timer = setTimeout(save, 800); }
  }
  function showConflict() {
    const box = $('#presentations-message'); box.replaceChildren(); box.hidden = false;
    box.append(document.createTextNode('This presentation changed in another tab or device. Your edits are still here. '));
    const copy = document.createElement('button'); copy.textContent = 'SAVE AS COPY'; copy.onclick = async () => { id = null; revision = null; conflict = false; $('#presentations-title').value += ' (conflict copy)'; markDirty(); await save(); };
    const remote = document.createElement('button'); remote.textContent = 'LOAD REMOTE'; remote.onclick = async () => { if (confirm('Discard edits here and load the saved revision?')) { conflict = false; dirty = false; await load(id, true); } };
    box.append(copy, remote);
  }
  async function refreshList() {
    records = (await request(api)).presentations; const list = $('#presentations-list'); list.replaceChildren();
    for (const record of records) {
      let title = 'Unable to decrypt'; try { title = decrypt(record.envelope).title || 'Untitled presentation'; } catch { /* Another password. */ }
      const li = document.createElement('li'), button = document.createElement('button'); button.type = 'button'; button.textContent = title; button.className = record.id === id ? 'selected' : ''; button.title = `Updated ${record.updated_at} UTC`; button.onclick = () => load(record.id); li.append(button); list.append(li);
    }
    if (!records.length) { const li = document.createElement('li'); li.textContent = 'No saved presentations yet.'; list.append(li); }
  }
  async function flush() { if (dirty && !conflict) await save(); return !dirty && !saving && !conflict; }
  async function load(nextId, force = false) {
    if (nextId === id && !force) return; if (!force && !await flush()) return;
    try { const result = await request(`${api}/${nextId}`); data = normalize(decrypt(result.envelope)); id = nextId; revision = result.revision; active = 0; selected.clear(); dirty = false; conflict = false; undo = []; redo = []; $('#presentations-title').value = data.title; status('SAVED', 'saved'); message(''); render(); await refreshList(); }
    catch (error) { message(`Could not open presentation: ${error.message}`); }
  }
  async function makeNew() { if (!await flush()) return; data = blank(); id = null; revision = null; active = 0; selected.clear(); undo = []; redo = []; $('#presentations-title').value = data.title; dirty = false; message(''); render(); markDirty(); await save(); }
  function snapshot() { undo.push(JSON.stringify(data)); if (undo.length > 40) undo.shift(); redo = []; }
  function change(fn, rerender = true) { snapshot(); try { fn(); markDirty(); if (rerender) render(); } catch (error) { data = JSON.parse(undo.pop()); message(error.message); render(); } }
  function history(source, destination) { if (!source.length) return; destination.push(JSON.stringify(data)); data = normalize(JSON.parse(source.pop())); active = Math.min(active, data.slides.length - 1); selected.clear(); $('#presentations-title').value = data.title; markDirty(); render(); }
  function selectObject(objectId, additive = false) { if (!additive) selected.clear(); if (selected.has(objectId) && additive) selected.delete(objectId); else selected.add(objectId); renderCanvas(); syncFormatting(); }
  function syncFormatting() {
    const object = selectedObjects()[0]; if (!object) return;
    $('#presentations-font').value = object.font || 'Arial'; $('#presentations-font-size').value = object.fontSize || 28; $('#presentations-color').value = validColor(foreground(object), '#142631'); $('#presentations-fill').value = validColor(object.fill || themeColors().accent, '#00b8aa'); $('#presentations-align').value = object.align || 'left'; $('#presentations-list-style').value = object.list || 'none';
  }
  function validColor(value, fallback) { const context = document.createElement('canvas').getContext('2d'); context.fillStyle = fallback; context.fillStyle = value; const result = context.fillStyle; return /^#[0-9a-f]{6}$/i.test(result) ? result : fallback; }
  function render() { if ($('#presentations-workspace').hidden) return; renderSlideList(); renderCanvas(); syncOptions(); }
  function syncOptions() { const s = slide(); $('#presentations-layout').value = s.layout || 'blank'; $('#presentations-theme').value = data.theme; $('#presentations-background').value = validColor(background(s), '#ffffff'); $('#presentations-transition').value = s.transition || 'none'; $('#presentations-notes').value = s.notes || ''; }
  function renderSlideList() {
    const list = $('#presentations-slide-list'); list.replaceChildren(); data.slides.forEach((s, i) => {
      const li = document.createElement('li'); li.draggable = true; li.className = i === active ? 'selected' : ''; li.dataset.index = i; li.setAttribute('aria-label', `Slide ${i + 1}`); li.tabIndex = 0;
      const thumb = document.createElement('div'); thumb.className = 'thumb'; drawSlide(thumb, s, false); li.append(thumb); const label = document.createElement('span'); label.textContent = i + 1; li.append(label);
      li.onclick = () => { active = i; selected.clear(); render(); }; li.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); li.click(); } };
      li.ondragstart = e => { e.dataTransfer.setData('text/plain', String(i)); e.dataTransfer.effectAllowed = 'move'; };
      li.ondragover = e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; };
      li.ondrop = e => { e.preventDefault(); const from = Number(e.dataTransfer.getData('text/plain')); if (!Number.isInteger(from) || from < 0 || from >= data.slides.length || from === i) return; change(() => { const [moved] = data.slides.splice(from, 1); data.slides.splice(i, 0, moved); active = i; selected.clear(); }); };
      list.append(li);
    });
  }
  function drawSlide(target, s, interactive) {
    target.replaceChildren(); target.style.backgroundColor = background(s); target.style.backgroundImage = s.backgroundImage ? `url("${s.backgroundImage}")` : ''; target.style.backgroundSize = 'cover'; target.style.backgroundPosition = 'center';
    const width = target.clientWidth || (interactive ? 960 : 140), scale = width / 960;
    for (const object of s.elements) {
      const el = document.createElement('div'); el.className = `presentations-object ${object.type}${interactive && selected.has(object.id) ? ' selected' : ''}`; el.dataset.id = object.id;
      el.style.left = `${object.x / 960 * 100}%`; el.style.top = `${object.y / 540 * 100}%`; el.style.width = `${object.w / 960 * 100}%`; el.style.height = `${object.h / 540 * 100}%`;
      el.style.transform = `rotate(${object.rotation || 0}deg)`; el.style.fontFamily = object.font || 'Arial'; el.style.fontSize = `${(object.fontSize || 28) * scale}px`; el.style.color = foreground(object); el.style.fontWeight = object.bold ? 'bold' : 'normal'; el.style.fontStyle = object.italic ? 'italic' : 'normal'; el.style.textDecoration = object.underline ? 'underline' : 'none'; el.style.textAlign = object.align || 'left';
      if (object.type === 'text') el.textContent = decoratedText(object);
      else if (object.type === 'image') { const image = document.createElement('img'); image.src = object.src; image.alt = ''; el.append(image); }
      else if (object.type === 'table') { const table = document.createElement('table'); for (const row of object.cells || []) { const tr = table.insertRow(); for (const value of row) { const td = tr.insertCell(); td.textContent = value; if (interactive) { td.contentEditable = 'true'; td.onfocus = () => { td.dataset.original = td.textContent; }; td.onblur = () => { if (td.textContent !== td.dataset.original) change(() => { const r = tr.rowIndex, c = td.cellIndex; object.cells[r][c] = td.textContent; }, false); }; td.onpointerdown = e => e.stopPropagation(); } } } el.append(table); }
      else el.style.backgroundColor = object.type === 'line' ? 'transparent' : object.fill || themeColors().accent;
      if (object.type === 'line') { el.style.borderTop = `${Math.max(1, 3 * scale)}px solid ${object.fill || themeColors().accent}`; el.style.height = '2px'; }
      if (interactive) {
        el.onpointerdown = e => pointerStart(e, object);
        el.ondblclick = e => { if (object.type !== 'text') return; e.stopPropagation(); el.contentEditable = 'true'; el.classList.add('editing'); el.textContent = object.text || ''; el.focus(); const selection = window.getSelection(), range = document.createRange(); range.selectNodeContents(el); selection.removeAllRanges(); selection.addRange(range); };
        el.onblur = () => { if (!el.isContentEditable) return; const value = el.innerText.replace(/\n$/, ''); el.contentEditable = 'false'; el.classList.remove('editing'); if (value !== object.text) change(() => { object.text = value; }); else renderCanvas(); };
        if (selected.has(object.id)) for (const kind of ['resize', 'rotate']) { const handle = document.createElement('span'); handle.className = `presentations-handle ${kind}`; handle.dataset.handle = kind; el.append(handle); }
      }
      target.append(el);
    }
  }
  function decoratedText(object) { const lines = String(object.text || '').split('\n'); if (object.list === 'bullet') return lines.map(line => `• ${line}`).join('\n'); if (object.list === 'number') return lines.map((line, i) => `${i + 1}. ${line}`).join('\n'); return object.text || ''; }
  function renderCanvas() { if ($('#presentations-workspace').hidden) return; drawSlide(canvas, slide(), true); }
  function renderPresenter() { drawSlide(presentSlide, slide(), false); presentSlide.classList.remove('fade'); if (slide().transition === 'fade') { void presentSlide.offsetWidth; presentSlide.classList.add('fade'); } $('#presentations-counter').textContent = `${active + 1} / ${data.slides.length}`; }
  function pointerStart(event, object) {
    if (event.button !== 0 || event.currentTarget.isContentEditable || event.target.isContentEditable) return;
    event.preventDefault(); event.stopPropagation();
    const handle = event.target.dataset.handle || 'move'; if (!selected.has(object.id)) selectObject(object.id, event.shiftKey); else if (event.shiftKey) selectObject(object.id, true);
    if (!selected.has(object.id)) return;
    snapshot(); const rect = canvas.getBoundingClientRect(); gesture = { pointer: event.pointerId, mode: handle, startX: event.clientX, startY: event.clientY, centerX: rect.left + (object.x + object.w / 2) / 960 * rect.width, centerY: rect.top + (object.y + object.h / 2) / 540 * rect.height, before: selectedObjects().map(item => ({ id: item.id, x: item.x, y: item.y, w: item.w, h: item.h, rotation: item.rotation || 0 })) };
    canvas.setPointerCapture(event.pointerId);
  }
  function snap(value, targets, threshold = 7) { for (const target of targets) if (Math.abs(value - target) < threshold) return target; return value; }
  function pointerMove(event) {
    if (!gesture || event.pointerId !== gesture.pointer) return;
    const rect = canvas.getBoundingClientRect(), dx = (event.clientX - gesture.startX) * 960 / rect.width, dy = (event.clientY - gesture.startY) * 540 / rect.height;
    const guides = $('#presentations-guides'); guides.replaceChildren();
    for (const before of gesture.before) {
      const object = slide().elements.find(item => item.id === before.id); if (!object) continue;
      if (gesture.mode === 'move') {
        const peers = slide().elements.filter(item => !selected.has(item.id)); const xTargets = [0, 480, 960, ...peers.flatMap(item => [item.x, item.x + item.w / 2, item.x + item.w])]; const yTargets = [0, 270, 540, ...peers.flatMap(item => [item.y, item.y + item.h / 2, item.y + item.h])];
        let x = clamp(before.x + dx, -before.w + 10, 950), y = clamp(before.y + dy, -before.h + 10, 530);
        const sx = snap(x + before.w / 2, xTargets), sy = snap(y + before.h / 2, yTargets); if (sx !== x + before.w / 2) { x = sx - before.w / 2; addGuide('vertical', sx); } if (sy !== y + before.h / 2) { y = sy - before.h / 2; addGuide('horizontal', sy); }
        object.x = Math.round(x); object.y = Math.round(y);
      } else if (gesture.mode === 'resize') { object.w = Math.round(clamp(before.w + dx, 20, 960)); object.h = Math.round(clamp(before.h + dy, object.type === 'line' ? 2 : 20, 540)); }
      else { object.rotation = Math.round(Math.atan2(event.clientY - gesture.centerY, event.clientX - gesture.centerX) * 180 / Math.PI + 90); }
    }
    renderCanvas();
    function addGuide(type, value) { const line = document.createElement('div'); line.className = `presentations-guide ${type}`; if (type === 'vertical') line.style.left = `${value / 960 * 100}%`; else line.style.top = `${value / 540 * 100}%`; guides.append(line); }
  }
  function pointerEnd(event) { if (!gesture || event.pointerId !== gesture.pointer) return; const before = gesture.before; gesture = null; $('#presentations-guides').replaceChildren(); const changed = before.some(item => { const current = slide().elements.find(object => object.id === item.id); return JSON.stringify(current && [current.x, current.y, current.w, current.h, current.rotation]) !== JSON.stringify([item.x, item.y, item.w, item.h, item.rotation]); }); if (changed) { markDirty(); renderSlideList(); } else undo.pop(); }
  function addObject(type, src = null) {
    const base = { id: newId(), type, x: 240, y: 170, w: 400, h: 190, rotation: 0, color: '', fill: themeColors().accent, font: 'Arial', fontSize: 28, align: 'left', list: 'none', bold: false, italic: false, underline: false };
    if (type === 'text') Object.assign(base, { text: 'New text', fill: '' });
    if (type === 'ellipse' || type === 'rect') Object.assign(base, { x: 350, y: 185, w: 220, h: 150 });
    if (type === 'line') Object.assign(base, { x: 300, y: 270, w: 340, h: 2 });
    if (type === 'image') Object.assign(base, { x: 240, y: 100, w: 480, h: 320, src });
    if (type === 'table') Object.assign(base, { x: 180, y: 150, w: 600, h: 240, cells: [['Heading 1', 'Heading 2'], ['Cell', 'Cell'], ['Cell', 'Cell']], fontSize: 24 });
    change(() => { slide().elements.push(base); selected = new Set([base.id]); }); syncFormatting();
  }
  function action(name) {
    if (name === 'undo') return history(undo, redo); if (name === 'redo') return history(redo, undo);
    if (name === 'image') return $('#presentations-image-file').click();
    if (['text', 'rect', 'ellipse', 'line', 'table'].includes(name)) return addObject(name);
    const objects = selectedObjects(); if (!objects.length) return;
    if (name === 'deleteElement') return change(() => { slide().elements = slide().elements.filter(item => !selected.has(item.id)); selected.clear(); });
    if (name === 'front' || name === 'back') return change(() => { const rest = slide().elements.filter(item => !selected.has(item.id)); slide().elements = name === 'front' ? [...rest, ...objects] : [...objects, ...rest]; });
    if (name.startsWith('align')) return change(() => { const edge = name === 'alignLeft' ? Math.min(...objects.map(item => item.x)) : name === 'alignRight' ? Math.max(...objects.map(item => item.x + item.w)) : objects.length > 1 ? objects.reduce((a, item) => a + item.x + item.w / 2, 0) / objects.length : 480; for (const item of objects) item.x = Math.round(name === 'alignLeft' ? edge : name === 'alignRight' ? edge - item.w : edge - item.w / 2); });
    if (name === 'distribute' && objects.length > 2) return change(() => { const sorted = [...objects].sort((a, b) => a.x - b.x); const start = sorted[0].x, end = sorted.at(-1).x; sorted.forEach((item, i) => { item.x = Math.round(start + (end - start) * i / (sorted.length - 1)); }); });
  }
  async function readImage(file, callback) { if (!file || !/^image\/(png|jpeg|webp)$/.test(file.type)) return message('Use a PNG, JPEG, or WebP image.'); if (file.size > 6 * 1024 * 1024) return message('Images must be 6 MB or smaller.'); const reader = new FileReader(); reader.onload = () => callback(reader.result); reader.onerror = () => message('Could not read image.'); reader.readAsDataURL(file); }
  async function unlock(event) {
    event.preventDefault(); const pass = $('#presentations-password').value, errorBox = $('#presentations-unlock-error'); errorBox.hidden = true;
    if (pass.length < 12) return; status('UNLOCKING…', 'saving');
    try {
      if (!crypto?.getRandomValues) throw new Error('Secure browser cryptography is unavailable.');
      const vault = await request(app.dataset.vault, { method: 'POST', body: '{}' });
      const [presentations, documents, sheets, agenda, drawings] = await Promise.all([request(api), request(app.dataset.documentsApi), request(app.dataset.sheetsApi), request(app.dataset.agendaApi), request(app.dataset.drawApi)]);
      const first = presentations.presentations[0]?.envelope || documents.documents[0]?.envelope || sheets.sheets[0]?.envelope || agenda.agenda?.envelope || drawings.drawings[0]?.envelope;
      if (!first) {
        if ($('#presentations-confirm-row').hidden) { $('#presentations-confirm-row').hidden = false; $('#presentations-confirm').required = true; status('CONFIRM NEW PASSWORD', 'pending'); $('#presentations-confirm').focus(); return; }
        if ($('#presentations-confirm').value !== pass) throw new Error('The new passwords do not match.');
      }
      key = await pbkdf2Async(sha256, enc.encode(pass), unb64(vault.salt), { c: 210000, dkLen: 32, asyncTick: 20 });
      if (first) decrypt(first);
      rememberOfficeKey(key, csrf);
      $('#presentations-password').value = ''; $('#presentations-confirm').value = ''; $('#presentations-confirm-row').hidden = true; $('#presentations-unlock').hidden = true; $('#presentations-workspace').hidden = false; $('#presentations-lock').hidden = false;
      await refreshList(); if (records.length) await load(records[0].id); else await makeNew();
    } catch (error) { key?.fill(0); key = null; errorBox.textContent = `Could not unlock. Check your OFFICE Password and try again. ${error.message}`; errorBox.hidden = false; status('LOCKED', 'error'); }
  }
  function lock() { if (dirty || saving) { save(); message('Wait for the SAVED indicator before locking.'); return; } forgetOfficeKey(); key?.fill(0); key = null; records = []; id = null; revision = null; data = blank(); selected.clear(); undo = []; redo = []; $('#presentations-workspace').hidden = true; $('#presentations-unlock').hidden = false; $('#presentations-lock').hidden = true; status('LOCKED'); }
  async function importFile(file) {
    if (!file || !await flush()) return; if (file.size > 25 * 1024 * 1024) return message('PPTX imports must be 25 MB or smaller.');
    try { status('IMPORTING…', 'saving'); const result = await importPptx(file); data = normalize(result.data); id = null; revision = null; active = 0; selected.clear(); undo = []; redo = []; $('#presentations-title').value = data.title; render(); markDirty(); await save(); message(result.report); }
    catch (error) { status('IMPORT ERROR', 'error'); message(`Import failed: ${error.message}`); }
  }
  function download(blob, filename) { const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000); }
  const filename = ext => `${(data.title || 'Presentation').replace(/[\\/:*?"<>|]/g, '_')}.${ext}`;
  async function drawToCanvas(s, scale = 2) {
    const out = document.createElement('canvas'); out.width = 960 * scale; out.height = 540 * scale; const ctx = out.getContext('2d'); ctx.scale(scale, scale); ctx.fillStyle = background(s); ctx.fillRect(0, 0, 960, 540);
    if (s.backgroundImage) await drawImage(s.backgroundImage, 0, 0, 960, 540);
    for (const object of s.elements) {
      ctx.save(); ctx.translate(object.x + object.w / 2, object.y + object.h / 2); ctx.rotate((object.rotation || 0) * Math.PI / 180); ctx.translate(-object.w / 2, -object.h / 2); ctx.fillStyle = object.fill || themeColors().accent; ctx.strokeStyle = object.fill || themeColors().accent;
        if (object.type === 'rect') ctx.fillRect(0, 0, object.w, object.h);
        else if (object.type === 'ellipse') { ctx.beginPath(); ctx.ellipse(object.w / 2, object.h / 2, object.w / 2, object.h / 2, 0, 0, 2 * Math.PI); ctx.fill(); }
        else if (object.type === 'line') { ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(object.w, 0); ctx.stroke(); }
        else if (object.type === 'image') await drawImage(object.src, 0, 0, object.w, object.h);
        else if (object.type === 'text') { ctx.fillStyle = foreground(object); ctx.font = `${object.italic ? 'italic ' : ''}${object.bold ? 'bold ' : ''}${object.fontSize || 28}px ${object.font || 'Arial'}`; ctx.textAlign = object.align || 'left'; ctx.textBaseline = 'top'; const x = object.align === 'center' ? object.w / 2 : object.align === 'right' ? object.w - 5 : 5; wrapText(decoratedText(object), x, 5, object.w - 10, (object.fontSize || 28) * 1.2); }
        else if (object.type === 'table') { const rows = object.cells || [], cols = Math.max(1, ...rows.map(row => row.length)); ctx.strokeStyle = foreground(object); ctx.fillStyle = foreground(object); ctx.font = `${object.fontSize || 20}px Arial`; rows.forEach((row, r) => row.forEach((cell, c) => { const cw = object.w / cols, ch = object.h / rows.length; ctx.strokeRect(c * cw, r * ch, cw, ch); ctx.fillText(String(cell), c * cw + 5, r * ch + 5, cw - 10); })); }
      ctx.restore();
    }
    return out;
    function drawImage(src, x, y, w, h) { return new Promise(resolve => { const img = new Image(); img.onload = () => { ctx.drawImage(img, x, y, w, h); resolve(); }; img.onerror = resolve; img.src = src; }); }
    function wrapText(value, x, y, maxWidth, lineHeight) { let yy = y; for (const paragraph of String(value).split('\n')) { let line = ''; for (const word of paragraph.split(' ')) { const test = line ? `${line} ${word}` : word; if (ctx.measureText(test).width > maxWidth && line) { ctx.fillText(line, x, yy, maxWidth); yy += lineHeight; line = word; } else line = test; } ctx.fillText(line, x, yy, maxWidth); yy += lineHeight; } }
  }
  async function exportFormat(format) {
    try {
      data.title = $('#presentations-title').value.trim() || 'Untitled presentation';
      if (format === 'pptx') { const copy = structuredClone(data); for (const s of copy.slides) { if (!s.background) s.background = background(s); for (const o of s.elements) if (!o.color) o.color = foreground(o); } download(await exportPptx(copy), filename('pptx')); }
      else if (format === 'png') { const image = await drawToCanvas(slide()); const blob = await new Promise(resolve => image.toBlob(resolve, 'image/png')); download(blob, `${filename('slide-' + (active + 1))}.png`); }
      else { const pdf = new jsPDF({ orientation: 'landscape', unit: 'px', format: [960, 540], hotfixes: ['px_scaling'] }); for (let i = 0; i < data.slides.length; i++) { if (i) pdf.addPage([960, 540], 'landscape'); const image = await drawToCanvas(data.slides[i]); pdf.addImage(image.toDataURL('image/png'), 'PNG', 0, 0, 960, 540); } download(pdf.output('blob'), filename('pdf')); }
    } catch (error) { message(`Export failed: ${error.message}`); }
  }
  function showPresent() { presenting = true; presenter.hidden = false; renderPresenter(); presenter.requestFullscreen?.().catch(() => {}); }
  function exitPresent() { presenting = false; presenter.hidden = true; if (document.fullscreenElement === presenter) document.exitFullscreen?.().catch(() => {}); render(); }
  function navigate(delta) { const next = clamp(active + delta, 0, data.slides.length - 1); if (next !== active) { active = next; renderPresenter(); } }
  $('#presentations-unlock-form').onsubmit = unlock; $('#presentations-lock').onclick = lock; $('#presentations-save').onclick = () => save(); $('#presentations-new').onclick = makeNew; $('#presentations-title').oninput = markDirty;
  $('#presentations-delete').onclick = async () => { if (id === null) return makeNew(); if (!confirm('Delete this presentation permanently?')) return; try { await request(`${api}/${id}`, { method: 'DELETE' }); dirty = false; id = null; await makeNew(); await refreshList(); } catch (error) { message(error.message); } };
  $('#presentations-import').onclick = () => $('#presentations-file').click(); $('#presentations-file').onchange = e => { const file = e.target.files[0]; e.target.value = ''; importFile(file); };
  $('#presentations-export').onclick = () => exportFormat('pptx'); $('#presentations-pdf').onclick = () => exportFormat('pdf'); $('#presentations-png').onclick = () => exportFormat('png');
  app.querySelectorAll('[data-action]').forEach(button => button.onclick = () => action(button.dataset.action));
  app.querySelectorAll('[data-format]').forEach(button => button.onclick = () => { const name = button.dataset.format; if (selectedObjects().length) change(() => { for (const object of selectedObjects()) object[name] = !object[name]; }); });
  for (const [selector, property, convert] of [['#presentations-font', 'font', String], ['#presentations-font-size', 'fontSize', Number], ['#presentations-color', 'color', String], ['#presentations-fill', 'fill', String], ['#presentations-align', 'align', String], ['#presentations-list-style', 'list', String]]) $(selector).onchange = e => { if (selectedObjects().length) change(() => { for (const object of selectedObjects()) object[property] = convert(e.target.value); }); };
  $('#presentations-image-file').onchange = e => { readImage(e.target.files[0], src => addObject('image', src)); e.target.value = ''; };
  $('#presentations-background-image').onclick = () => $('#presentations-background-file').click(); $('#presentations-background-file').onchange = e => { readImage(e.target.files[0], src => change(() => { slide().backgroundImage = src; })); e.target.value = ''; };
  $('#presentations-clear-background').onclick = () => change(() => { slide().backgroundImage = null; });
  $('#presentations-layout').onchange = e => { const layout = e.target.value; if (slide().elements.length && !confirm('Replace this slide’s objects with the selected layout?')) { e.target.value = slide().layout || 'blank'; return; } change(() => { slide().layout = layout; slide().elements = layoutElements(layout); selected.clear(); }); };
  $('#presentations-theme').onchange = e => change(() => { data.theme = e.target.value; }); $('#presentations-background').onchange = e => change(() => { slide().background = e.target.value; }); $('#presentations-transition').onchange = e => change(() => { slide().transition = e.target.value; }); $('#presentations-notes').oninput = e => { slide().notes = e.target.value; markDirty(); };
  $('#presentations-add-slide').onclick = () => change(() => { data.slides.splice(active + 1, 0, newSlide('blank')); active++; selected.clear(); });
  $('#presentations-duplicate-slide').onclick = () => change(() => { const copy = structuredClone(slide()); copy.id = newId(); for (const object of copy.elements) object.id = newId(); data.slides.splice(active + 1, 0, copy); active++; selected.clear(); });
  $('#presentations-delete-slide').onclick = () => { if (data.slides.length < 2) return message('A presentation must have at least one slide.'); change(() => { data.slides.splice(active, 1); active = Math.min(active, data.slides.length - 1); selected.clear(); }); };
  canvas.onpointerdown = e => { if (e.target === canvas) { selected.clear(); renderCanvas(); } }; canvas.onpointermove = pointerMove; canvas.onpointerup = pointerEnd; canvas.onpointercancel = pointerEnd;
  canvas.onkeydown = e => { if (e.target.isContentEditable) return; if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); action('deleteElement'); } else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key) && selected.size) { e.preventDefault(); const step = e.shiftKey ? 10 : 1; change(() => { for (const object of selectedObjects()) { object.x += e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0; object.y += e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0; } }); } };
  document.addEventListener('keydown', e => { if (presenting) { if (['ArrowRight', 'PageDown', ' ', 'Enter'].includes(e.key)) { e.preventDefault(); navigate(1); } else if (['ArrowLeft', 'PageUp', 'Backspace'].includes(e.key)) { e.preventDefault(); navigate(-1); } else if (e.key === 'Escape') exitPresent(); return; } if ((e.ctrlKey || e.metaKey) && ['s', 'z', 'y'].includes(e.key.toLowerCase())) { if (e.key.toLowerCase() !== 's' && e.target.closest('input,textarea,[contenteditable="true"]')) return; e.preventDefault(); if (e.key.toLowerCase() === 's') save(); else if (e.key.toLowerCase() === 'y' || e.shiftKey) action('redo'); else action('undo'); } });
  $('#presentations-present').onclick = showPresent; $('#presentations-prev').onclick = () => navigate(-1); $('#presentations-next').onclick = () => navigate(1); $('#presentations-exit').onclick = exitPresent;
  presentSlide.onclick = () => navigate(1); presentSlide.ontouchstart = e => { startTouch = e.touches[0]?.clientX; }; presentSlide.ontouchend = e => { if (startTouch == null) return; const dx = e.changedTouches[0]?.clientX - startTouch; if (Math.abs(dx) > 40) { e.preventDefault(); navigate(dx < 0 ? 1 : -1); } startTouch = null; };
  document.addEventListener('fullscreenchange', () => { if (presenting && document.fullscreenElement !== presenter) exitPresent(); });
  new ResizeObserver(() => { if (!presenting && key) renderCanvas(); else if (presenting) renderPresenter(); }).observe(canvas);
  window.addEventListener('beforeunload', e => { if (dirty || saving || conflict) { e.preventDefault(); e.returnValue = ''; } });
  (async () => {
    key = readOfficeKey(csrf);
    if (!key) return;
    let badKey = false;
    try {
      const result = await request(api);
      if (result.presentations.length) { try { decrypt(result.presentations[0].envelope); } catch (error) { badKey = true; throw error; } }
      $('#presentations-unlock').hidden = true; $('#presentations-workspace').hidden = false; $('#presentations-lock').hidden = false;
      await refreshList(); if (records.length) await load(records[0].id); else await makeNew();
    } catch {
      key?.fill(0); key = null; if (badKey) forgetOfficeKey(); status('LOCKED');
      $('#presentations-workspace').hidden = true; $('#presentations-unlock').hidden = false;
    }
  })();
}
