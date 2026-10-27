import { pbkdf2Async } from '@noble/hashes/pbkdf2.js';
import { sha256 } from '@noble/hashes/sha2.js';
import { gcm } from '@noble/ciphers/aes.js';
import { rememberOfficeKey, readOfficeKey, forgetOfficeKey } from '../office_vault.js';

const app = document.querySelector('.draw-app');
if (app) start();

function start() {
  document.body.classList.add('draw-page');
  const $ = selector => app.querySelector(selector), svg = $('#draw-canvas'), cursor = $('#draw-cursor'), ns = 'http://www.w3.org/2000/svg';
  const enc = new TextEncoder(), dec = new TextDecoder(), api = app.dataset.api, csrf = app.dataset.csrf;
  const uid = () => crypto.randomUUID(), copy = value => structuredClone(value);
  const clamp = (value, lo, hi) => Math.max(lo, Math.min(hi, value));
  const svgEl = (name, attrs = {}) => { const node = document.createElementNS(ns, name); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value)); return node; };
  const bytes64 = bytes => { let result = ''; for (let i = 0; i < bytes.length; i += 8192) result += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(result); };
  const un64 = value => Uint8Array.from(atob(value), char => char.charCodeAt(0));
  let key = null, records = [], id = null, revision = null, drawing = blank(), selected = new Set(), tool = 'select', zoom = 'fit';
  let dirty = false, saving = false, conflict = false, generation = 0, timer = null, undo = [], redo = [], gesture = null;
  function blank() { return { version: 1, title: 'Untitled drawing', width: 960, height: 680, background: '#ffffff', objects: [] }; }
  function status(value, kind = '') { $('#draw-status').textContent = value; $('#draw-status').dataset.kind = kind; }
  function message(value) { const node = $('#draw-message'); node.textContent = value; node.hidden = !value; }
  function encrypt(value) { const iv = crypto.getRandomValues(new Uint8Array(12)); return { v: 1, iv: bytes64(iv), data: bytes64(gcm(key, iv).encrypt(enc.encode(JSON.stringify(value)))) }; }
  function decrypt(value) { return JSON.parse(dec.decode(gcm(key, un64(value.iv)).decrypt(un64(value.data)))); }
  async function request(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, ...options });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(result.error || `Request failed (${response.status}).`); error.status = response.status; throw error; }
    return result;
  }
  function normalize(value) {
    if (!value || value.version !== 1 || !Array.isArray(value.objects)) throw new Error('Invalid drawing data.');
    value.width = clamp(Number(value.width) || 960, 100, 4000); value.height = clamp(Number(value.height) || 680, 100, 4000);
    value.title = String(value.title || 'Untitled drawing').slice(0, 160); value.background ||= '#ffffff';
    value.objects = value.objects.filter(o => o && ['rect', 'ellipse', 'triangle', 'diamond', 'line', 'arrow', 'connector', 'freehand', 'text', 'image'].includes(o.type));
    for (const o of value.objects) { o.id ||= uid(); o.x = Number(o.x) || 0; o.y = Number(o.y) || 0; o.w = Number(o.w) || 1; o.h = Number(o.h) || 1; o.rotation = Number(o.rotation) || 0; }
    return value;
  }
  function markDirty() { if (!key || conflict) return; dirty = true; generation++; status('UNSAVED', 'pending'); clearTimeout(timer); timer = setTimeout(save, 850); }
  async function save() {
    clearTimeout(timer); if (!key || !dirty || saving || conflict) return;
    saving = true; const target = id, currentRevision = revision, savedGeneration = generation; status('SAVING…', 'saving');
    try {
      drawing.title = $('#draw-title').value.trim() || 'Untitled drawing';
      const envelope = encrypt(drawing);
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
    const box = $('#draw-message'); box.replaceChildren(); box.hidden = false;
    box.append(document.createTextNode('This drawing changed in another tab or device. Your edits are still here. '));
    const add = (text, fn) => { const button = document.createElement('button'); button.type = 'button'; button.textContent = text; button.onclick = fn; box.append(button); };
    add('SAVE AS COPY', async () => { id = null; revision = null; conflict = false; $('#draw-title').value += ' (copy)'; markDirty(); await save(); });
    add('LOAD REMOTE', async () => { if (confirm('Discard edits here and load the saved drawing?')) { conflict = false; dirty = false; await load(id, true); } });
  }
  async function refreshList() {
    records = (await request(api)).drawings; const list = $('#draw-list'); list.replaceChildren();
    for (const record of records) { const li = document.createElement('li'), button = document.createElement('button'); button.type = 'button';
      try { button.textContent = decrypt(record.envelope).title || 'Untitled drawing'; } catch { button.textContent = 'Unable to decrypt'; }
      button.className = record.id === id ? 'selected' : ''; button.title = `Updated ${record.updated_at} UTC`; button.onclick = () => load(record.id); li.append(button); list.append(li); }
    if (!records.length) { const li = document.createElement('li'); li.textContent = 'No saved drawings yet.'; list.append(li); }
  }
  async function flush() { if (dirty && !conflict) await save(); return !dirty && !saving && !conflict; }
  async function load(nextId, force = false) {
    if (id === nextId && !force) return; if (!force && !await flush()) return;
    try { const result = await request(`${api}/${nextId}`); drawing = normalize(decrypt(result.envelope)); id = nextId; revision = result.revision; dirty = false; conflict = false; selected.clear(); undo = []; redo = []; $('#draw-title').value = drawing.title; status('SAVED', 'saved'); message(''); render(); await refreshList(); }
    catch (error) { message(`Could not open drawing: ${error.message}`); }
  }
  async function makeNew() { if (!await flush()) return; drawing = blank(); id = null; revision = null; dirty = false; selected.clear(); undo = []; redo = []; $('#draw-title').value = drawing.title; message(''); render(); markDirty(); await save(); }
  function snapshot() { undo.push(JSON.stringify(drawing)); if (undo.length > 50) undo.shift(); redo = []; }
  function change(fn) { snapshot(); fn(); markDirty(); render(); }
  function history(from, to) { if (!from.length) return; to.push(JSON.stringify(drawing)); drawing = normalize(JSON.parse(from.pop())); selected.clear(); $('#draw-title').value = drawing.title; markDirty(); render(); }
  const objects = () => drawing.objects.filter(o => selected.has(o.id));
  function select(object, additive = false) {
    const group = object.groupId ? drawing.objects.filter(o => o.groupId === object.groupId) : [object];
    if (!additive) selected.clear();
    const remove = additive && selected.has(object.id); for (const item of group) remove ? selected.delete(item.id) : selected.add(item.id);
    render();
  }
  function point(event) { const rect = svg.getBoundingClientRect(); return { x: clamp((event.clientX - rect.left) * drawing.width / rect.width, -2000, 6000), y: clamp((event.clientY - rect.top) * drawing.height / rect.height, -2000, 6000) }; }
  const center = o => ({ x: o.x + o.w / 2, y: o.y + o.h / 2 });
  function ends(o) {
    const from = drawing.objects.find(item => item.id === o.fromId), to = drawing.objects.find(item => item.id === o.toId);
    return { x1: from ? center(from).x : o.x, y1: from ? center(from).y : o.y, x2: to ? center(to).x : o.x + o.w, y2: to ? center(to).y : o.y + o.h };
  }
  function nearestShape(p) { return [...drawing.objects].reverse().find(o => !['line', 'arrow', 'connector', 'freehand'].includes(o.type) && p.x >= o.x - 8 && p.x <= o.x + o.w + 8 && p.y >= o.y - 8 && p.y <= o.y + o.h + 8); }
  function shapeNode(o) {
    const g = svgEl('g', { 'data-id': o.id, opacity: clamp(Number(o.opacity) || 1, .1, 1) });
    const lineLike = ['line', 'arrow', 'connector'].includes(o.type), freehand = o.type === 'freehand';
    const c = lineLike ? (() => { const e = ends(o); return { x: (e.x1 + e.x2) / 2, y: (e.y1 + e.y2) / 2 }; })() : center(o);
    g.setAttribute('transform', `rotate(${o.rotation || 0} ${c.x} ${c.y})`);
    const stroke = o.stroke || '#17353b', strokeWidth = clamp(Number(o.strokeWidth ?? 2), 0, 40);
    let node;
    if (o.type === 'rect') node = svgEl('rect', { x: o.x, y: o.y, width: o.w, height: o.h });
    else if (o.type === 'ellipse') node = svgEl('ellipse', { cx: o.x + o.w / 2, cy: o.y + o.h / 2, rx: o.w / 2, ry: o.h / 2 });
    else if (o.type === 'triangle') node = svgEl('polygon', { points: `${o.x + o.w / 2},${o.y} ${o.x + o.w},${o.y + o.h} ${o.x},${o.y + o.h}` });
    else if (o.type === 'diamond') node = svgEl('polygon', { points: `${o.x + o.w / 2},${o.y} ${o.x + o.w},${o.y + o.h / 2} ${o.x + o.w / 2},${o.y + o.h} ${o.x},${o.y + o.h / 2}` });
    else if (lineLike) { const e = ends(o); node = svgEl('line', { x1: e.x1, y1: e.y1, x2: e.x2, y2: e.y2, 'stroke-linecap': 'round' });
      if (o.type !== 'line') { const angle = Math.atan2(e.y2 - e.y1, e.x2 - e.x1), size = Math.max(9, strokeWidth * 4), a = angle + 2.55, b = angle - 2.55;
        const head = svgEl('polygon', { points: `${e.x2},${e.y2} ${e.x2 + size * Math.cos(a)},${e.y2 + size * Math.sin(a)} ${e.x2 + size * Math.cos(b)},${e.y2 + size * Math.sin(b)}`, fill: stroke, stroke: 'none', 'pointer-events': 'none' }); g.append(head); }
      const hit = svgEl('line', { x1: e.x1, y1: e.y1, x2: e.x2, y2: e.y2, stroke: 'transparent', 'stroke-width': Math.max(16, strokeWidth + 10), 'pointer-events': 'stroke' }); g.append(hit); }
    else if (freehand) node = svgEl('polyline', { points: (o.points || []).map(p => `${p[0]},${p[1]}`).join(' '), fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
    else if (o.type === 'text') { node = svgEl('text', { x: o.x + 3, y: o.y + (o.fontSize || 28), 'font-family': 'Arial, sans-serif', 'font-size': o.fontSize || 28 });
      String(o.text || '').split('\n').forEach((line, i) => { const span = svgEl('tspan', { x: o.x + 3, dy: i ? (o.fontSize || 28) * 1.2 : 0 }); span.textContent = line || ' '; node.append(span); }); }
    else if (o.type === 'image') { node = svgEl('image', { x: o.x, y: o.y, width: o.w, height: o.h, preserveAspectRatio: 'none' }); if (/^data:image\/(png|jpeg|webp);base64,[\w+/=]+$/i.test(o.src || '')) node.setAttribute('href', o.src); }
    node.setAttribute('fill', lineLike || freehand ? 'none' : o.type === 'image' ? 'none' : o.type === 'text' ? o.fill || '#17353b' : o.noFill ? 'none' : o.fill || '#22b8ad');
    node.setAttribute('stroke', o.type === 'text' || o.type === 'image' ? 'none' : stroke); node.setAttribute('stroke-width', strokeWidth);
    g.prepend(node); return g;
  }
  function renderCanvas() {
    const stage = $('#draw-stage');
    const padding = window.matchMedia('(max-width: 700px)').matches ? 26 : 50;
    const scale = zoom === 'fit' ? Math.min(3, Math.max(.15, Math.min(
      (stage.clientWidth - padding) / drawing.width,
      (stage.clientHeight - padding) / drawing.height,
    ))) : zoom;
    svg.setAttribute('viewBox', `0 0 ${drawing.width} ${drawing.height}`); svg.style.width = `${drawing.width * scale}px`; svg.style.height = `${drawing.height * scale}px`;
    svg.replaceChildren(svgEl('rect', { width: drawing.width, height: drawing.height, fill: drawing.background || '#ffffff', 'pointer-events': 'none' }));
    for (const o of drawing.objects) svg.append(shapeNode(o));
    for (const o of objects()) { const e = ['line', 'arrow', 'connector'].includes(o.type) ? ends(o) : null;
      const x = e ? Math.min(e.x1, e.x2) : o.x, y = e ? Math.min(e.y1, e.y2) : o.y, w = e ? Math.max(8, Math.abs(e.x2 - e.x1)) : Math.max(8, o.w), h = e ? Math.max(8, Math.abs(e.y2 - e.y1)) : Math.max(8, o.h);
      const box = svgEl('rect', { x, y, width: w, height: h, fill: 'none', stroke: '#00a9d6', 'stroke-width': 1.5, 'stroke-dasharray': '5 4', 'pointer-events': 'none', class: 'draw-selection' }); svg.append(box);
      const handle = svgEl('rect', { x: x + w - 6, y: y + h - 6, width: 12, height: 12, fill: '#ffffff', stroke: '#007ca3', 'data-handle': 'resize', 'data-id': o.id }); svg.append(handle);
      const rotate = svgEl('circle', { cx: x + w / 2, cy: y - 18, r: 7, fill: '#ffffff', stroke: '#007ca3', 'data-handle': 'rotate', 'data-id': o.id }); svg.append(rotate);
    }
  }
  function syncProperties() {
    const o = objects()[0], fields = ['fill', 'stroke', 'stroke-width', 'opacity', 'font-size', 'rotation', 'x', 'y', 'width', 'height'];
    $('#draw-selection-info').textContent = !o ? 'Select an object to edit it.' : `${objects().length} selected · ${o.type.toUpperCase()}`;
    for (const field of fields) $(`#draw-${field}`).disabled = !o;
    $('#draw-no-fill').disabled = !o; $('#draw-edit-text').disabled = !o || o.type !== 'text';
    if (!o) return;
    $('#draw-fill').value = o.fill || '#22b8ad'; $('#draw-no-fill').checked = !!o.noFill; $('#draw-stroke').value = o.stroke || '#17353b'; $('#draw-stroke-width').value = o.strokeWidth ?? 2;
    $('#draw-opacity').value = Math.round((o.opacity ?? 1) * 100); $('#draw-opacity-value').textContent = `${$('#draw-opacity').value}%`;
    $('#draw-font-size').value = o.fontSize || 28; $('#draw-rotation').value = o.rotation || 0;
    for (const [field, property] of [['x', 'x'], ['y', 'y'], ['width', 'w'], ['height', 'h']]) $(`#draw-${field}`).value = Math.round(o[property]);
  }
  function render() { if ($('#draw-workspace').hidden) return; renderCanvas(); syncProperties(); $('#draw-page-width').value = drawing.width; $('#draw-page-height').value = drawing.height; $('#draw-background').value = drawing.background; app.querySelectorAll('[data-tool]').forEach(b => b.setAttribute('aria-pressed', b.dataset.tool === tool ? 'true' : 'false')); }
  function newObject(type, p) { return { id: uid(), type, x: p.x, y: p.y, w: 1, h: 1, rotation: 0, fill: $('#draw-fill').value, stroke: $('#draw-stroke').value, strokeWidth: Number($('#draw-stroke-width').value) || 2, opacity: 1, fontSize: 28 }; }
  function dragStart(event) {
    if (event.button !== 0) return; const p = point(event), target = event.target.closest('[data-id]'), hit = target && drawing.objects.find(o => o.id === target.dataset.id);
    if (tool === 'select') {
      if (!hit) { selected.clear(); render(); return; }
      const mode = event.target.dataset.handle || 'move'; if (!selected.has(hit.id)) select(hit, event.shiftKey); else if (event.shiftKey) { select(hit, true); return; }
      if (!selected.has(hit.id)) return;
      snapshot(); const c = center(hit); gesture = { id: event.pointerId, mode, p, c, before: objects().map(o => ({ id: o.id, x: o.x, y: o.y, w: o.w, h: o.h, rotation: o.rotation, points: o.points ? copy(o.points) : null })) };
    } else if (tool === 'text') { const value = prompt('Text', 'New text'); if (value === null) return; change(() => { const o = newObject('text', p); o.text = value; o.w = 220; o.h = 60; drawing.objects.push(o); selected = new Set([o.id]); }); tool = 'select'; render(); return; }
    else { snapshot(); const o = newObject(tool, p); if (tool === 'freehand') o.points = [[p.x, p.y]]; if (tool === 'connector') o.fromId = nearestShape(p)?.id || null; drawing.objects.push(o); selected = new Set([o.id]); gesture = { id: event.pointerId, mode: 'draw', p, object: o }; render(); }
    svg.setPointerCapture(event.pointerId); event.preventDefault();
  }
  function dragMove(event) {
    if (!gesture || gesture.id !== event.pointerId) return; const p = point(event), dx = p.x - gesture.p.x, dy = p.y - gesture.p.y;
    if (gesture.mode === 'draw') { const o = gesture.object;
      if (o.type === 'freehand') { if (Math.hypot(dx, dy) > 1) o.points.push([p.x, p.y]); }
      else if (['line', 'arrow', 'connector'].includes(o.type)) { o.w = dx; o.h = dy; }
      else { o.x = Math.min(p.x, gesture.p.x); o.y = Math.min(p.y, gesture.p.y); o.w = Math.max(1, Math.abs(dx)); o.h = Math.max(1, Math.abs(dy)); }
    } else for (const old of gesture.before) { const o = drawing.objects.find(item => item.id === old.id); if (!o) continue;
      if (gesture.mode === 'move') { o.x = old.x + dx; o.y = old.y + dy; if (old.points) o.points = old.points.map(([x, y]) => [x + dx, y + dy]); }
      else if (gesture.mode === 'resize') { o.w = Math.max(4, old.w + dx); o.h = Math.max(4, old.h + dy); if (old.points) o.points = old.points.map(([x, y]) => [old.x + (x - old.x) * o.w / old.w, old.y + (y - old.y) * o.h / old.h]); }
      else if (gesture.mode === 'rotate') o.rotation = Math.round(Math.atan2(p.y - gesture.c.y, p.x - gesture.c.x) * 180 / Math.PI + 90);
    }
    renderCanvas(); syncProperties(); event.preventDefault();
  }
  function dragEnd(event) {
    if (!gesture || gesture.id !== event.pointerId) return;
    if (gesture.mode === 'draw') { const o = gesture.object; if (o.type === 'connector') o.toId = nearestShape(point(event))?.id || null;
      if (o.type === 'freehand') { const xs = o.points.map(p => p[0]), ys = o.points.map(p => p[1]); o.x = Math.min(...xs); o.y = Math.min(...ys); o.w = Math.max(1, Math.max(...xs) - o.x); o.h = Math.max(1, Math.max(...ys) - o.y); }
      if (o.w === 1 && o.h === 1) { o.w = 100; o.h = 70; }
      tool = 'select'; }
    gesture = null; markDirty(); render();
  }
  function command(name) {
    const items = objects(); if (name === 'undo') return history(undo, redo); if (name === 'redo') return history(redo, undo); if (!items.length) return;
    if (name === 'delete') return change(() => { drawing.objects = drawing.objects.filter(o => !selected.has(o.id)); selected.clear(); });
    if (name === 'duplicate') return change(() => { const clones = items.map(o => { const c = copy(o); c.id = uid(); c.x += 20; c.y += 20; c.groupId = null; return c; }); drawing.objects.push(...clones); selected = new Set(clones.map(o => o.id)); });
    if (name === 'group' && items.length > 1) return change(() => { const groupId = uid(); for (const o of items) o.groupId = groupId; });
    if (name === 'ungroup') return change(() => { for (const o of items) o.groupId = null; });
    if (['front', 'back', 'forward', 'backward'].includes(name)) return change(() => { const arr = drawing.objects;
      if (name === 'front' || name === 'back') drawing.objects = name === 'front' ? [...arr.filter(o => !selected.has(o.id)), ...items] : [...items, ...arr.filter(o => !selected.has(o.id))];
      else { const order = name === 'forward' ? [...arr].reverse() : [...arr]; for (const o of order) { const i = arr.indexOf(o), j = i + (name === 'forward' ? 1 : -1), other = arr[j]; if (other && !selected.has(other.id)) { arr[i] = other; arr[j] = o; } } } });
  }
  function align(name) { const items = objects(); if (!items.length) return; const minX = Math.min(...items.map(o => o.x)), maxX = Math.max(...items.map(o => o.x + o.w)), minY = Math.min(...items.map(o => o.y)), maxY = Math.max(...items.map(o => o.y + o.h));
    change(() => { for (const o of items) { if (name === 'left') o.x = items.length === 1 ? 0 : minX; if (name === 'right') o.x = (items.length === 1 ? drawing.width : maxX) - o.w; if (name === 'center') o.x = (items.length === 1 ? drawing.width / 2 : (minX + maxX) / 2) - o.w / 2; if (name === 'top') o.y = items.length === 1 ? 0 : minY; if (name === 'bottom') o.y = (items.length === 1 ? drawing.height : maxY) - o.h; if (name === 'middle') o.y = (items.length === 1 ? drawing.height / 2 : (minY + maxY) / 2) - o.h / 2; } }); }
  function editText() { const o = objects()[0]; if (!o || o.type !== 'text') return; const value = prompt('Edit text', o.text || ''); if (value !== null) change(() => { o.text = value; }); }
  function exportSvg() { const clean = svgEl('svg', { xmlns: ns, width: drawing.width, height: drawing.height, viewBox: `0 0 ${drawing.width} ${drawing.height}` }); clean.append(svgEl('rect', { width: drawing.width, height: drawing.height, fill: drawing.background })); for (const o of drawing.objects) clean.append(shapeNode(o)); return new XMLSerializer().serializeToString(clean); }
  function download(blob, ext) { const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = `${(drawing.title || 'Drawing').replace(/[\\/:*?"<>|]/g, '_')}.${ext}`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000); }
  async function exportFile(type) { try { drawing.title = $('#draw-title').value.trim() || 'Untitled drawing'; const source = new Blob([exportSvg()], { type: 'image/svg+xml;charset=utf-8' }); if (type === 'svg') return download(source, 'svg');
      const url = URL.createObjectURL(source), image = new Image(); image.src = url; await image.decode(); const canvas = document.createElement('canvas'); canvas.width = drawing.width; canvas.height = drawing.height; canvas.getContext('2d').drawImage(image, 0, 0); URL.revokeObjectURL(url); const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png')); if (!blob) throw new Error('PNG encoding failed.'); download(blob, 'png');
    } catch (error) { message(`Export failed: ${error.message}`); } }
  async function unlock(event) {
    event.preventDefault(); const pass = $('#draw-password').value, errorBox = $('#draw-unlock-error'); errorBox.hidden = true; if (pass.length < 12) return; status('UNLOCKING…', 'saving');
    try { const vault = await request(app.dataset.vault, { method: 'POST', body: '{}' });
      const [drawings, documents, sheets, presentations, agenda, programs] = await Promise.all([request(api), request(app.dataset.documentsApi), request(app.dataset.sheetsApi), request(app.dataset.presentationsApi), request(app.dataset.agendaApi), request(app.dataset.basicApi)]);
      const first = drawings.drawings[0]?.envelope || documents.documents[0]?.envelope || sheets.sheets[0]?.envelope || presentations.presentations[0]?.envelope || agenda.agenda?.envelope || programs.programs[0]?.envelope;
      if (!first) { if ($('#draw-confirm-row').hidden) { $('#draw-confirm-row').hidden = false; $('#draw-confirm').required = true; status('CONFIRM NEW PASSWORD', 'pending'); $('#draw-confirm').focus(); return; } if ($('#draw-confirm').value !== pass) throw new Error('The new passwords do not match.'); }
      key = await pbkdf2Async(sha256, enc.encode(pass), un64(vault.salt), { c: 210000, dkLen: 32, asyncTick: 20 }); if (first) decrypt(first); rememberOfficeKey(key, csrf);
      $('#draw-password').value = ''; $('#draw-confirm').value = ''; $('#draw-confirm-row').hidden = true; $('#draw-unlock').hidden = true; $('#draw-workspace').hidden = false; $('#draw-lock').hidden = false;
      await refreshList(); if (records.length) await load(records[0].id); else await makeNew();
    } catch (error) { key?.fill(0); key = null; errorBox.textContent = `Could not unlock. Check your OFFICE Password and try again. ${error.message}`; errorBox.hidden = false; status('LOCKED', 'error'); }
  }
  function lock() { if (dirty || saving || conflict) { save(); message('Wait for the SAVED indicator before locking.'); return; } forgetOfficeKey(); key?.fill(0); key = null; records = []; id = null; revision = null; drawing = blank(); selected.clear(); undo = []; redo = []; $('#draw-workspace').hidden = true; $('#draw-unlock').hidden = false; $('#draw-lock').hidden = true; status('LOCKED'); }
  $('#draw-unlock-form').onsubmit = unlock; $('#draw-lock').onclick = lock; $('#draw-save').onclick = save; $('#draw-new').onclick = makeNew; $('#draw-title').oninput = markDirty;
  $('#draw-delete').onclick = async () => { if (id === null) return; if (!confirm('Delete this drawing permanently?')) return; try { await request(`${api}/${id}`, { method: 'DELETE' }); dirty = false; id = null; await makeNew(); } catch (error) { message(error.message); } };
  $('#draw-export-svg').onclick = () => exportFile('svg'); $('#draw-export-png').onclick = () => exportFile('png');
  app.querySelectorAll('[data-tool]').forEach(button => button.onclick = () => { tool = button.dataset.tool; render(); });
  app.querySelectorAll('[data-command]').forEach(button => button.onclick = () => command(button.dataset.command));
  $('#draw-align').onchange = event => { if (event.target.value) align(event.target.value); event.target.value = ''; };
  for (const [id, property, convert] of [['fill', 'fill', String], ['stroke', 'stroke', String], ['stroke-width', 'strokeWidth', Number], ['opacity', 'opacity', v => Number(v) / 100], ['font-size', 'fontSize', Number], ['rotation', 'rotation', Number], ['x', 'x', Number], ['y', 'y', Number], ['width', 'w', Number], ['height', 'h', Number]]) $(`#draw-${id}`).onchange = event => { if (objects().length) change(() => { for (const o of objects()) { const value = convert(event.target.value), before = o[property]; if (o.points && ['x', 'y', 'w', 'h'].includes(property)) o.points = o.points.map(([x, y]) => property === 'x' ? [x + value - before, y] : property === 'y' ? [x, y + value - before] : property === 'w' ? [o.x + (x - o.x) * value / before, y] : [x, o.y + (y - o.y) * value / before]); o[property] = value; } }); };
  $('#draw-no-fill').onchange = event => change(() => { for (const o of objects()) o.noFill = event.target.checked; }); $('#draw-edit-text').onclick = editText;
  for (const [id, property] of [['page-width', 'width'], ['page-height', 'height']]) $(`#draw-${id}`).onchange = event => change(() => { drawing[property] = clamp(Number(event.target.value) || drawing[property], 100, 4000); });
  $('#draw-background').onchange = event => change(() => { drawing.background = event.target.value; }); $('#draw-zoom').onchange = event => { zoom = event.target.value === 'fit' ? 'fit' : Number(event.target.value); renderCanvas(); };
  window.addEventListener('resize', () => { if (zoom === 'fit' && !$('#draw-workspace').hidden) renderCanvas(); });
  $('#draw-insert-image').onclick = () => $('#draw-image-file').click(); $('#draw-image-file').onchange = event => { const file = event.target.files[0]; event.target.value = ''; if (!file) return;
    if (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 6 * 1024 * 1024) return message('Choose a PNG, JPEG, or WebP image no larger than 6 MB.');
    const reader = new FileReader(); reader.onload = () => change(() => { const o = newObject('image', { x: 50, y: 50 }); o.src = reader.result; o.w = 240; o.h = 180; drawing.objects.push(o); selected = new Set([o.id]); }); reader.onerror = () => message('Could not read image.'); reader.readAsDataURL(file); };
  function updateCursor(event) {
    const rect = svg.getBoundingClientRect();
    cursor.hidden = (event.pointerType !== 'mouse' && event.pointerType !== 'pen') || !!event.target.closest('[data-handle]') ||
      event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
    if (!cursor.hidden) { cursor.style.left = `${event.clientX}px`; cursor.style.top = `${event.clientY}px`; }
  }
  svg.onpointerdown = dragStart; svg.onpointerenter = updateCursor; svg.onpointermove = event => { updateCursor(event); dragMove(event); }; svg.onpointerleave = () => { cursor.hidden = true; }; svg.onpointerup = dragEnd; svg.onpointercancel = event => { cursor.hidden = true; dragEnd(event); };
  svg.ondblclick = event => { const target = event.target.closest('[data-id]'), o = target && drawing.objects.find(item => item.id === target.dataset.id); if (o?.type === 'text') { selected = new Set([o.id]); editText(); } };
  document.addEventListener('keydown', event => { if ($('#draw-workspace').hidden) return;
    if (event.ctrlKey && event.key.toLowerCase() === 's') { event.preventDefault(); save(); return; }
    if (/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) return;
    if (event.ctrlKey && event.key.toLowerCase() === 'z') { event.preventDefault(); history(undo, redo); return; } if (event.ctrlKey && event.key.toLowerCase() === 'y') { event.preventDefault(); history(redo, undo); return; }
    if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); command('delete'); } else if (event.key.startsWith('Arrow') && objects().length) { event.preventDefault(); const step = event.shiftKey ? 10 : 1; change(() => { for (const o of objects()) { o.x += event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0; o.y += event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0; } }); } });
  window.addEventListener('beforeunload', event => { if (dirty || saving || conflict) { event.preventDefault(); event.returnValue = ''; } });
  (async () => { key = readOfficeKey(csrf); if (!key) return; try { const result = await request(api); if (result.drawings[0]) decrypt(result.drawings[0].envelope); $('#draw-unlock').hidden = true; $('#draw-workspace').hidden = false; $('#draw-lock').hidden = false; await refreshList(); if (records.length) await load(records[0].id); else await makeNew(); }
    catch (error) { key?.fill(0); key = null; forgetOfficeKey(); status('LOCKED'); message(`Could not load Draw: ${error.message}`); } })();
}
