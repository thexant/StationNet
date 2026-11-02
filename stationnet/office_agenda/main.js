import { pbkdf2Async } from '@noble/hashes/pbkdf2.js';
import { sha256 } from '@noble/hashes/sha2.js';
import { gcm } from '@noble/ciphers/aes.js';
import { rememberOfficeKey, readOfficeKey, forgetOfficeKey } from '../office_vault.js';
import { blankAgenda, dayKey, parseDay, addDays, occurrences, onDay, searchable } from './model.js';

const app = document.querySelector('.agenda-app');
if (app) initialize();

function initialize() {
  document.body.classList.add('agenda-page');
  const $ = selector => app.querySelector(selector);
  const api = app.dataset.api, csrf = app.dataset.csrf;
  const encoder = new TextEncoder(), decoder = new TextDecoder();
  const dateFormat = new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  const monthFormat = new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' });
  const shortFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  const weekdayFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short' });
  let key = null, data = blankAgenda(), revision = null, dirty = false, saving = false, conflict = false, generation = 0, saveTimer = null;
  let view = 'today', selected = dayKey(new Date()), editId = null, fired = new Set();
  const b64 = bytes => { let value = ''; for (let i = 0; i < bytes.length; i += 8192) value += String.fromCharCode(...bytes.subarray(i, i + 8192)); return btoa(value); };
  const unb64 = value => Uint8Array.from(atob(value), char => char.charCodeAt(0));
  const encrypt = value => { const iv = crypto.getRandomValues(new Uint8Array(12)); return { v: 1, iv: b64(iv), data: b64(gcm(key, iv).encrypt(encoder.encode(JSON.stringify(value)))) }; };
  const decrypt = envelope => JSON.parse(decoder.decode(gcm(key, unb64(envelope.iv)).decrypt(unb64(envelope.data))));
  const el = (tag, className = '', content = '') => { const node = document.createElement(tag); if (className) node.className = className; node.textContent = content; return node; };
  const fmt = (date, format = shortFormat) => parseDay(date) ? format.format(parseDay(date)) : date;
  const today = () => dayKey(new Date());
  const status = (value, kind = '') => { $('#agenda-status').textContent = value; $('#agenda-status').dataset.kind = kind; };
  const message = value => { const box = $('#agenda-message'); box.replaceChildren(); box.textContent = value; box.hidden = !value; };
  async function request(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, ...options });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(result.error || `Request failed (${response.status}).`); error.status = response.status; throw error; }
    return result;
  }
  function normalize(value) {
    if (!value || typeof value !== 'object' || value.version !== 1) throw new Error('Unsupported Agenda data.');
    return { version: 1, events: Array.isArray(value.events) ? value.events : [], tasks: Array.isArray(value.tasks) ? value.tasks : [], contacts: Array.isArray(value.contacts) ? value.contacts : [], settings: { notifications: !!value.settings?.notifications } };
  }
  function changed() {
    if (!key || conflict) return;
    dirty = true; generation++; status('UNSAVED', 'pending'); clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 650);
    render();
  }
  async function save() {
    clearTimeout(saveTimer);
    if (!key || !dirty || saving || conflict) return;
    saving = true; const currentGeneration = generation;
    status('SAVING…', 'saving');
    try {
      const envelope = encrypt(data);
      const result = revision === null
        ? await request(api, { method: 'POST', body: JSON.stringify({ envelope }) })
        : await request(api, { method: 'PUT', body: JSON.stringify({ envelope, revision }) });
      revision = result.revision;
      dirty = generation !== currentGeneration;
      status(dirty ? 'UNSAVED' : 'SAVED', dirty ? 'pending' : 'saved'); message('');
    } catch (error) {
      if (error.status === 409) {
        conflict = true; status('REVISION CONFLICT', 'error'); showConflict();
      } else { status('SAVE ERROR', 'error'); message(`Could not save: ${error.message} Your changes remain in this tab. Press SAVE NOW to retry.`); }
    } finally { saving = false; if (dirty && !conflict && generation !== currentGeneration) saveTimer = setTimeout(save, 650); }
  }
  function showConflict() {
    const box = $('#agenda-message'); box.replaceChildren(); box.hidden = false;
    box.append(document.createTextNode('Agenda changed in another tab or device. Your edits remain here. '));
    const reload = el('button', 'secondary', 'LOAD SAVED VERSION'); reload.type = 'button';
    reload.onclick = async () => { if (!confirm('Discard changes in this tab and load the saved Agenda?')) return; await load(); };
    const copy = el('button', 'secondary', 'DOWNLOAD MY CHANGES'); copy.type = 'button';
    copy.onclick = () => { const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'agenda-conflict-copy.json'; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000); };
    box.append(reload, copy);
  }
  async function load() {
    try {
      const result = await request(api);
      data = result.agenda ? normalize(decrypt(result.agenda.envelope)) : blankAgenda();
      revision = result.agenda?.revision ?? null;
      dirty = false; conflict = false; message(''); status('SAVED', 'saved'); render();
    } catch (error) { message(`Could not load Agenda: ${error.message}`); status('LOAD ERROR', 'error'); }
  }
  function setUnlocked() {
    $('#agenda-password').value = ''; $('#agenda-confirm').value = ''; $('#agenda-confirm-row').hidden = true; $('#agenda-confirm').required = false;
    $('#agenda-unlock').hidden = true; $('#agenda-workspace').hidden = false; $('#agenda-lock').hidden = false;
    status('SAVED', 'saved'); render(); checkReminders();
  }
  async function unlock(event) {
    event.preventDefault(); const password = $('#agenda-password').value, errorBox = $('#agenda-unlock-error');
    if (password.length < 12) return;
    errorBox.hidden = true; status('UNLOCKING…', 'saving');
    try {
      if (!crypto?.getRandomValues) throw new Error('Secure browser cryptography is unavailable.');
      const vault = await request(app.dataset.vault, { method: 'POST', body: '{}' });
      const [agenda, documents, sheets, presentations, drawings, programs] = await Promise.all([request(api), request(app.dataset.documentsApi), request(app.dataset.sheetsApi), request(app.dataset.presentationsApi), request(app.dataset.drawApi), request(app.dataset.basicApi)]);
      const first = agenda.agenda?.envelope || documents.documents[0]?.envelope || sheets.sheets[0]?.envelope || presentations.presentations[0]?.envelope || drawings.drawings[0]?.envelope || programs.programs[0]?.envelope;
      if (!first) {
        if ($('#agenda-confirm-row').hidden) { $('#agenda-confirm-row').hidden = false; $('#agenda-confirm').required = true; $('#agenda-confirm').focus(); status('CONFIRM NEW PASSWORD', 'pending'); return; }
        if ($('#agenda-confirm').value !== password) throw new Error('The new passwords do not match.');
      }
      key = await pbkdf2Async(sha256, encoder.encode(password), unb64(vault.salt), { c: 210000, dkLen: 32, asyncTick: 20 });
      if (first) decrypt(first);
      data = agenda.agenda ? normalize(decrypt(agenda.agenda.envelope)) : blankAgenda();
      revision = agenda.agenda?.revision ?? null;
      rememberOfficeKey(key, csrf, $('#agenda-remember').checked); setUnlocked();
    } catch (error) { key?.fill(0); key = null; errorBox.textContent = `Could not unlock. Check your OFFICE Password and try again. ${error.message}`; errorBox.hidden = false; status('LOCKED', 'error'); }
  }
  function lock() {
    if (dirty || saving || conflict) { save(); message('Wait for SAVED before locking, or resolve the revision conflict.'); return; }
    forgetOfficeKey(); key?.fill(0); key = null; data = blankAgenda(); revision = null; fired.clear();
    $('#agenda-workspace').hidden = true; $('#agenda-unlock').hidden = false; $('#agenda-lock').hidden = true; status('LOCKED');
  }
  const option = (value, label) => { const item = document.createElement('option'); item.value = value; item.textContent = label; return item; };
  function fillLinks(select, items, current = '') { select.replaceChildren(option('', 'None')); for (const item of items) select.append(option(item.id, item.title || item.name)); select.value = current || ''; }
  function dateButton(date, label = null, className = '') {
    const button = el('button', className, label || fmt(date)); button.type = 'button'; button.dataset.date = date; return button;
  }
  function itemButton(item, compact = false, date = item.startDate) {
    const label = `${date > item.startDate ? '↳ ' : item.startTime ? `${item.startTime} ` : ''}${item.title}`;
    const button = el('button', `agenda-item agenda-${item.kind}${item.source?.done ? ' done' : ''}${compact ? ' compact' : ''}`, label);
    button.type = 'button'; button.dataset.kind = item.kind; button.dataset.id = item.id;
    button.title = `${item.kind.toUpperCase()} · ${item.title}`;
    return button;
  }
  function render() {
    if (!key || $('#agenda-workspace').hidden) return;
    app.querySelectorAll('[data-view]').forEach(button => { button.setAttribute('aria-current', button.dataset.view === view ? 'page' : 'false'); });
    $('#agenda-notifications').textContent = data.settings.notifications ? 'DISABLE REMINDERS' : 'ENABLE REMINDERS';
    $('#agenda-notification-note').textContent = !('Notification' in window) ? 'This browser does not support notifications.' : 'Reminders appear only while Agenda is open and unlocked.';
    const content = $('#agenda-content'); content.replaceChildren();
    $('#agenda-prev').hidden = view === 'tasks' || view === 'contacts'; $('#agenda-next').hidden = view === 'tasks' || view === 'contacts'; $('#agenda-current').hidden = view === 'tasks' || view === 'contacts';
    if (view === 'month') renderMonth(content);
    else if (view === 'week') renderWeek(content);
    else if (view === 'day') renderDay(content);
    else if (view === 'tasks') renderTasks(content);
    else if (view === 'contacts') renderContacts(content);
    else renderToday(content);
    renderSearch();
  }
  function renderMonth(content) {
    const date = parseDay(selected), first = new Date(date.getFullYear(), date.getMonth(), 1);
    const start = dayKey(new Date(first.getFullYear(), first.getMonth(), 1 - first.getDay()));
    const end = addDays(start, 41); $('#agenda-heading').textContent = monthFormat.format(first);
    const items = occurrences(data, start, end).filter(item => item.onMonth);
    const grid = el('div', 'agenda-month-grid');
    for (let day = 0; day < 7; day++) grid.append(el('div', 'agenda-weekday', weekdayFormat.format(parseDay(addDays(start, day)))));
    for (let offset = 0; offset < 42; offset++) {
      const key = addDays(start, offset), cell = el('div', 'agenda-month-cell');
      if (parseDay(key).getMonth() !== date.getMonth()) cell.classList.add('outside');
      if (key === today()) cell.classList.add('today');
      const header = dateButton(key, String(parseDay(key).getDate()), 'agenda-date-number'); header.setAttribute('aria-label', `Open ${fmt(key, dateFormat)} planner`); cell.append(header);
      const matches = items.filter(item => onDay(item, key));
      for (const item of matches.slice(0, 4)) cell.append(itemButton(item, true, key));
      if (matches.length > 4) cell.append(dateButton(key, `+${matches.length - 4} more`, 'agenda-more'));
      grid.append(cell);
    }
    content.append(grid);
  }
  function weekStart(date) { const parsed = parseDay(date); return addDays(date, -parsed.getDay()); }
  function renderWeek(content) {
    const start = weekStart(selected), end = addDays(start, 6); $('#agenda-heading').textContent = `${fmt(start)} – ${fmt(end)}`;
    const items = occurrences(data, start, end), grid = el('div', 'agenda-week-grid');
    for (let index = 0; index < 7; index++) {
      const date = addDays(start, index), column = el('section', 'agenda-week-day'); if (date === today()) column.classList.add('today');
      column.append(dateButton(date, fmt(date), 'agenda-week-date'));
      const matches = items.filter(item => onDay(item, date));
      if (!matches.length) column.append(el('p', 'agenda-empty', 'No entries'));
      for (const item of matches) column.append(itemButton(item, false, date));
      grid.append(column);
    }
    content.append(grid);
  }
  function renderDay(content) {
    $('#agenda-heading').textContent = fmt(selected, dateFormat);
    const items = occurrences(data, selected, selected).filter(item => onDay(item, selected));
    const top = el('div', 'agenda-day-top'); top.append(el('h2', '', 'DAILY PLANNER'));
    const add = el('button', 'secondary', '+ ENTRY'); add.type = 'button'; add.dataset.action = 'add-event'; top.append(add); content.append(top);
    const allDay = items.filter(item => item.allDay), timed = items.filter(item => !item.allDay);
    const all = el('section', 'agenda-day-section'); all.append(el('h3', '', 'ALL DAY & DEADLINES'));
    if (!allDay.length) all.append(el('p', 'agenda-empty', 'Nothing scheduled.'));
    for (const item of allDay) all.append(detailRow(item));
    content.append(all);
    const timeline = el('section', 'agenda-day-section'); timeline.append(el('h3', '', 'TIMED ENTRIES'));
    if (!timed.length) timeline.append(el('p', 'agenda-empty', 'No timed entries.'));
    for (const item of timed) timeline.append(detailRow(item));
    content.append(timeline);
    const notes = el('p', 'agenda-hint', 'Calendar entries and tasks update here immediately. Use “Show on month calendar” to keep minor events in Day and Week only.'); content.append(notes);
  }
  function detailRow(item) {
    const row = el('div', `agenda-detail agenda-${item.kind}${item.source?.done ? ' done' : ''}`);
    const when = item.allDay ? item.kind === 'task' ? 'DUE' : 'ALL DAY' : selected > item.startDate ? selected === item.endDate ? `UNTIL ${item.endTime || ''}` : 'CONTINUES' : `${item.startTime || ''}${item.endDate === item.startDate && item.endTime ? `–${item.endTime}` : ''}`;
    row.append(el('span', 'agenda-detail-time', when));
    const body = el('div'); body.append(itemButton(item, false, selected));
    if (item.kind === 'event' && item.source.location) body.append(el('small', '', item.source.location));
    if (item.kind === 'event' && item.source.contactId) {
      const contact = data.contacts.find(value => value.id === item.source.contactId);
      if (contact) body.append(el('small', '', `With ${contact.name}`));
    }
    if (item.kind === 'task') {
      const count = (item.source.subtasks || []).filter(value => value.done).length;
      if (item.source.subtasks?.length) body.append(el('small', '', `${count}/${item.source.subtasks.length} subtasks complete`));
    }
    row.append(body); return row;
  }
  function renderToday(content) {
    const current = today(), end = addDays(current, 13); $('#agenda-heading').textContent = `TODAY · ${fmt(current)}`;
    const items = occurrences(data, current, end), todayItems = items.filter(item => onDay(item, current));
    const overview = el('div', 'agenda-overview');
    const todayPanel = el('section'); todayPanel.append(el('h2', '', 'TODAY'));
    if (!todayItems.length) todayPanel.append(el('p', 'agenda-empty', 'Your day is clear.'));
    for (const item of todayItems) todayPanel.append(detailRow(item));
    const openDay = dateButton(current, 'OPEN DAILY PLANNER →', 'secondary'); todayPanel.append(openDay);
    const tasksPanel = el('section'); tasksPanel.append(el('h2', '', 'OPEN TASKS'));
    const openTasks = [...data.tasks].filter(task => !task.done && task.showOnCalendar !== false).sort((a, b) => (a.dueDate || '9999').localeCompare(b.dueDate || '9999')).slice(0, 8);
    if (!openTasks.length) tasksPanel.append(el('p', 'agenda-empty', 'No open tasks.'));
    for (const task of openTasks) tasksPanel.append(taskRow(task, true));
    overview.append(todayPanel, tasksPanel); content.append(overview);
    const upcoming = el('section', 'agenda-upcoming'); upcoming.append(el('h2', '', 'UPCOMING · NEXT 14 DAYS'));
    let found = false;
    for (let offset = 1; offset < 14; offset++) {
      const date = addDays(current, offset), daily = items.filter(item => onDay(item, date));
      if (!daily.length) continue;
      found = true; const group = el('div', 'agenda-upcoming-day'); group.append(dateButton(date, fmt(date), 'agenda-upcoming-date'));
      const list = el('div'); for (const item of daily) list.append(itemButton(item, false, date)); group.append(list); upcoming.append(group);
    }
    if (!found) upcoming.append(el('p', 'agenda-empty', 'Nothing coming up in the next two weeks.'));
    content.append(upcoming);
  }
  function taskRow(task, brief = false) {
    const row = el('div', `agenda-task-row${task.done ? ' done' : ''}`), check = document.createElement('input');
    check.type = 'checkbox'; check.checked = !!task.done; check.dataset.taskDone = task.id; check.setAttribute('aria-label', `Complete ${task.title}`);
    row.append(check);
    const main = el('button', 'agenda-task-main', task.title); main.type = 'button'; main.dataset.kind = 'task'; main.dataset.id = task.id; row.append(main);
    const side = el('div', 'agenda-task-side');
    if (!brief && task.done) {
      const clear = el('button', 'agenda-task-clear', 'CLEAR');
      clear.type = 'button'; clear.dataset.clearTask = task.id; clear.setAttribute('aria-label', `Clear completed task ${task.title}`);
      side.append(clear);
    }
    const metaText = [task.priority === 'high' ? 'HIGH' : task.priority === 'low' ? 'LOW' : '', task.dueDate ? fmt(task.dueDate) : ''].filter(Boolean).join(' · ');
    if (metaText) side.append(el('span', `agenda-task-meta priority-${task.priority || 'normal'}`, metaText));
    row.append(side);
    if (!brief && (task.eventId || task.contactId)) {
      const linkedEvent = data.events.find(item => item.id === task.eventId), contact = data.contacts.find(item => item.id === task.contactId);
      const links = [linkedEvent?.title, contact?.name].filter(Boolean);
      if (links.length) row.append(el('small', 'agenda-task-links', `Linked: ${links.join(' · ')}`));
    }
    if (!brief && task.subtasks?.length) {
      const subs = el('div', 'agenda-subtasks');
      for (const sub of task.subtasks) { const line = el('label'); const box = document.createElement('input'); box.type = 'checkbox'; box.checked = !!sub.done; box.dataset.subtaskTask = task.id; box.dataset.subtaskId = sub.id; line.append(box, document.createTextNode(` ${sub.title}`)); subs.append(line); }
      row.append(subs);
    }
    return row;
  }
  function renderTasks(content) {
    $('#agenda-heading').textContent = 'TASKS';
    const toolbar = el('div', 'agenda-section-head'); toolbar.append(el('h2', '', `${data.tasks.filter(task => !task.done).length} OPEN TASKS`));
    const add = el('button', 'secondary', '+ TASK'); add.type = 'button'; add.dataset.action = 'add-task'; toolbar.append(add); content.append(toolbar);
    const ordered = [...data.tasks].sort((a, b) => Number(a.done) - Number(b.done) || (a.dueDate || '9999').localeCompare(b.dueDate || '9999') || ({ high: 0, normal: 1, low: 2 }[a.priority] - { high: 0, normal: 1, low: 2 }[b.priority]));
    if (!ordered.length) content.append(el('p', 'agenda-empty', 'No tasks yet. Add one to track a deadline or checklist.'));
    for (const task of ordered) content.append(taskRow(task));
  }
  function renderContacts(content) {
    $('#agenda-heading').textContent = 'CONTACTS';
    const toolbar = el('div', 'agenda-section-head'); toolbar.append(el('h2', '', `${data.contacts.length} CONTACTS`));
    const add = el('button', 'secondary', '+ CONTACT'); add.type = 'button'; add.dataset.action = 'add-contact'; toolbar.append(add); content.append(toolbar);
    if (!data.contacts.length) content.append(el('p', 'agenda-empty', 'No contacts yet. Birthdays and anniversaries will appear on the calendar.'));
    const grid = el('div', 'agenda-contact-grid');
    for (const contact of [...data.contacts].sort((a, b) => a.name.localeCompare(b.name))) {
      const card = el('article', 'agenda-contact-card'); const name = el('button', '', contact.name); name.type = 'button'; name.dataset.kind = 'contact'; name.dataset.id = contact.id; card.append(name);
      if (contact.email) card.append(el('p', '', contact.email)); if (contact.phone) card.append(el('p', '', contact.phone));
      if (contact.birthday) card.append(el('small', '', `Birthday · ${fmt(contact.birthday)}`));
      if (contact.anniversary) card.append(el('small', '', `Anniversary · ${fmt(contact.anniversary)}`));
      const count = data.tasks.filter(task => task.contactId === contact.id && !task.done).length;
      if (count) card.append(el('small', '', `${count} open linked task${count === 1 ? '' : 's'}`));
      grid.append(card);
    }
    content.append(grid);
  }
  function renderSearch() {
    const query = $('#agenda-search').value, area = $('#agenda-search-results'), list = $('#agenda-results-list'); list.replaceChildren();
    if (!query.trim()) { area.hidden = true; return; }
    area.hidden = false; const results = searchable(data, query).filter(result => view === 'tasks' || result.kind !== 'task' || result.item.showOnCalendar !== false);
    if (!results.length) list.append(el('p', 'agenda-empty', 'No matches.'));
    for (const result of results) {
      const button = el('button', 'agenda-search-result'); button.type = 'button'; button.dataset.kind = result.kind; button.dataset.id = result.item.id;
      button.append(el('strong', '', result.item.title || result.item.name), el('small', '', result.kind.toUpperCase())); list.append(button);
    }
  }
  function openEditor(kind, id = null) {
    editId = id; const item = id ? data[`${kind}s`].find(value => value.id === id) : null;
    if (kind === 'event') {
      const form = $('#agenda-event-form'); form.reset();
      $('#agenda-event-heading').textContent = item ? 'EDIT EVENT' : 'NEW EVENT'; $('#agenda-delete-event').hidden = !item;
      fillLinks($('#agenda-event-contact'), data.contacts, item?.contactId);
      form.elements.title.value = item?.title || ''; form.elements.startDate.value = item?.startDate || selected;
      form.elements.endDate.value = item?.endDate || item?.startDate || selected;
      form.elements.startTime.value = item?.startTime || '09:00'; form.elements.endTime.value = item?.endTime || '10:00';
      form.elements.allDay.checked = !!item?.allDay; form.elements.showOnMonth.checked = item?.showOnMonth !== false;
      form.elements.frequency.value = item?.repeat?.frequency || 'none'; form.elements.interval.value = item?.repeat?.interval || 1;
      form.elements.until.value = item?.repeat?.until || ''; form.elements.reminder.value = item?.reminder ?? '';
      form.elements.location.value = item?.location || ''; form.elements.notes.value = item?.notes || '';
      const weekdays = $('#agenda-weekdays'); weekdays.replaceChildren();
      ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'].forEach((name, index) => { const label = el('label', 'agenda-weekday-option'); const box = document.createElement('input'); box.type = 'checkbox'; box.value = String(index); box.checked = item?.repeat?.weekdays?.includes(index) ?? index === parseDay(form.elements.startDate.value).getDay(); label.append(box, document.createTextNode(name)); weekdays.append(label); });
      updateEventFields(); $('#agenda-event-dialog').showModal();
    } else if (kind === 'task') {
      const form = $('#agenda-task-form'); form.reset(); $('#agenda-task-heading').textContent = item ? 'EDIT TASK' : 'NEW TASK'; $('#agenda-delete-task').hidden = !item;
      fillLinks($('#agenda-task-event'), data.events, item?.eventId); fillLinks($('#agenda-task-contact'), data.contacts, item?.contactId);
      form.elements.title.value = item?.title || ''; form.elements.dueDate.value = item?.dueDate || (view === 'day' ? selected : '');
      form.elements.dueTime.value = item?.dueTime || ''; form.elements.priority.value = item?.priority || 'normal'; form.elements.reminder.value = item?.reminder ?? '';
      form.elements.showOnCalendar.checked = item?.showOnCalendar !== false;
      form.elements.notes.value = item?.notes || ''; const rows = $('#agenda-subtask-rows'); rows.replaceChildren();
      for (const sub of item?.subtasks || []) addSubtaskRow(sub, false);
      $('#agenda-task-dialog').showModal();
      $('.agenda-task-scroll').scrollTop = 0;
    } else {
      const form = $('#agenda-contact-form'); form.reset(); $('#agenda-contact-heading').textContent = item ? 'EDIT CONTACT' : 'NEW CONTACT'; $('#agenda-delete-contact').hidden = !item;
      for (const field of ['name', 'email', 'phone', 'birthday', 'anniversary', 'notes']) form.elements[field].value = item?.[field] || '';
      $('#agenda-contact-dialog').showModal();
    }
  }
  function updateEventFields() {
    const form = $('#agenda-event-form'), allDay = form.elements.allDay.checked, repeating = form.elements.frequency.value !== 'none';
    for (const field of ['startTime', 'endTime']) { form.elements[field].disabled = allDay; form.elements[field].required = !allDay; }
    form.elements.interval.disabled = !repeating; form.elements.until.disabled = !repeating;
    $('.agenda-weekdays').hidden = form.elements.frequency.value !== 'weekly';
  }
  function addSubtaskRow(sub = {}, focus = true) {
    const row = el('div', 'agenda-subtask-row'); row.dataset.id = sub.id || crypto.randomUUID();
    const check = document.createElement('input'); check.type = 'checkbox'; check.checked = !!sub.done; check.setAttribute('aria-label', 'Subtask complete');
    const input = document.createElement('input'); input.type = 'text'; input.maxLength = 160; input.value = sub.title || ''; input.placeholder = 'Subtask'; input.setAttribute('aria-label', 'Subtask title');
    const remove = el('button', 'secondary', '×'); remove.type = 'button'; remove.setAttribute('aria-label', 'Remove subtask'); remove.onclick = () => row.remove();
    row.append(check, input, remove); $('#agenda-subtask-rows').append(row); if (focus) input.focus();
  }
  function saveEvent(event) {
    event.preventDefault(); const form = event.currentTarget, fields = form.elements;
    const startDate = fields.startDate.value, endDate = fields.endDate.value;
    if (!parseDay(startDate) || !parseDay(endDate) || endDate < startDate) return message('The end date must be on or after the start date.');
    if (!fields.allDay.checked && startDate === endDate && fields.endTime.value <= fields.startTime.value) return message('The end time must follow the start time.');
    if (fields.until.value && fields.until.value < startDate) return message('Repeat until must be on or after the start date.');
    const old = editId ? data.events.find(value => value.id === editId) : null;
    const entry = { id: old?.id || crypto.randomUUID(), title: fields.title.value.trim(), startDate, endDate, allDay: fields.allDay.checked,
      startTime: fields.allDay.checked ? '' : fields.startTime.value, endTime: fields.allDay.checked ? '' : fields.endTime.value,
      showOnMonth: fields.showOnMonth.checked, repeat: { frequency: fields.frequency.value, interval: Number(fields.interval.value) || 1,
        until: fields.until.value, weekdays: [...$('#agenda-weekdays').querySelectorAll('input:checked')].map(input => Number(input.value)) },
      reminder: fields.reminder.value, contactId: fields.contactId.value, location: fields.location.value.trim(), notes: fields.notes.value.trim() };
    if (!entry.title) return;
    if (old) Object.assign(old, entry); else data.events.push(entry);
    $('#agenda-event-dialog').close(); message(''); changed();
  }
  function saveTask(event) {
    event.preventDefault(); const fields = event.currentTarget.elements, old = editId ? data.tasks.find(value => value.id === editId) : null;
    if (fields.reminder.value !== '' && !fields.dueDate.value) return message('Set a deadline for this reminder.');
    const subtasks = [...$('#agenda-subtask-rows').children].map(row => ({ id: row.dataset.id, title: row.querySelector('input[type=text]').value.trim(), done: row.querySelector('input[type=checkbox]').checked })).filter(sub => sub.title);
    const entry = { id: old?.id || crypto.randomUUID(), title: fields.title.value.trim(), dueDate: fields.dueDate.value, dueTime: fields.dueTime.value,
      priority: fields.priority.value, reminder: fields.reminder.value, showOnCalendar: fields.showOnCalendar.checked, eventId: fields.eventId.value, contactId: fields.contactId.value,
      notes: fields.notes.value.trim(), subtasks, done: old?.done || false };
    if (!entry.title) return;
    if (old) Object.assign(old, entry); else data.tasks.push(entry);
    $('#agenda-task-dialog').close(); message(''); changed();
  }
  function saveContact(event) {
    event.preventDefault(); const fields = event.currentTarget.elements, old = editId ? data.contacts.find(value => value.id === editId) : null;
    const entry = { id: old?.id || crypto.randomUUID(), name: fields.name.value.trim(), email: fields.email.value.trim(), phone: fields.phone.value.trim(),
      birthday: fields.birthday.value, anniversary: fields.anniversary.value, notes: fields.notes.value.trim() };
    if (!entry.name) return;
    if (old) Object.assign(old, entry); else data.contacts.push(entry);
    $('#agenda-contact-dialog').close(); message(''); changed();
  }
  function deleteEntry(kind) {
    if (!editId) return; const label = kind === 'contact' ? 'contact and its calendar dates' : kind === 'event' ? 'event series' : 'task';
    if (!confirm(`Delete this ${label}?`)) return;
    data[`${kind}s`] = data[`${kind}s`].filter(item => item.id !== editId);
    if (kind === 'contact') { for (const event of data.events) if (event.contactId === editId) event.contactId = ''; for (const task of data.tasks) if (task.contactId === editId) task.contactId = ''; }
    if (kind === 'event') for (const task of data.tasks) if (task.eventId === editId) task.eventId = '';
    $(`#agenda-${kind}-dialog`).close(); editId = null; changed();
  }
  function navigate(direction) {
    if (view === 'month') { const date = parseDay(selected); date.setMonth(date.getMonth() + direction, 1); selected = dayKey(date); }
    else selected = addDays(selected, direction * (view === 'week' ? 7 : 1));
    render();
  }
  function checkReminders() {
    if (!key || !data.settings.notifications || !('Notification' in window) || Notification.permission !== 'granted') return;
    const now = Date.now(), from = addDays(today(), -2), to = addDays(today(), 2);
    for (const item of occurrences(data, from, to, { includeHiddenTasks: true })) {
      const minutes = item.source.reminder;
      if (minutes === '' || minutes == null || item.kind === 'contact' || (item.kind === 'task' && item.source.done)) continue;
      const time = item.kind === 'task' ? item.source.dueTime || '09:00' : item.startTime || '09:00';
      const due = new Date(`${item.startDate}T${time}:00`).getTime() - Number(minutes) * 60000;
      const marker = `${item.kind}:${item.id}:${item.startDate}:${minutes}`;
      if (now >= due && now - due < 5 * 60000 && !fired.has(marker)) {
        try {
          const notification = new Notification(item.title, { body: item.kind === 'task' ? 'Task deadline' : `Event · ${fmt(item.startDate)}`, tag: marker });
          fired.add(marker);
          notification.onclick = () => { window.focus(); openEditor(item.kind, item.id); notification.close(); };
        } catch { /* Permission may have changed while Agenda was open. */ }
      }
    }
  }
  async function toggleNotifications() {
    if (data.settings.notifications) { data.settings.notifications = false; changed(); return; }
    if (!('Notification' in window)) return message('This browser does not support notifications.');
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') return message('Browser notifications were not enabled. You can change permission in browser settings.');
    data.settings.notifications = true; message(''); changed(); checkReminders();
  }

  $('#agenda-unlock-form').addEventListener('submit', unlock); $('#agenda-lock').onclick = lock;
  $('#agenda-save').onclick = save; $('#agenda-notifications').onclick = toggleNotifications;
  $('#agenda-prev').onclick = () => navigate(-1); $('#agenda-next').onclick = () => navigate(1); $('#agenda-current').onclick = () => { selected = today(); render(); };
  $('#agenda-add-event').onclick = () => openEditor('event'); $('#agenda-add-task').onclick = () => openEditor('task'); $('#agenda-add-contact').onclick = () => openEditor('contact');
  $('#agenda-search').oninput = renderSearch;
  app.addEventListener('click', event => {
    const viewButton = event.target.closest('[data-view]'); if (viewButton) { view = viewButton.dataset.view; if (view === 'today') selected = today(); render(); return; }
    const date = event.target.closest('[data-date]'); if (date) { selected = date.dataset.date; view = 'day'; render(); return; }
    const clear = event.target.closest('[data-clear-task]');
    if (clear) {
      const task = data.tasks.find(value => value.id === clear.dataset.clearTask);
      if (task?.done) { data.tasks = data.tasks.filter(value => value.id !== task.id); changed(); }
      return;
    }
    const action = event.target.closest('[data-action]'); if (action) { openEditor(action.dataset.action === 'add-task' ? 'task' : 'event'); return; }
    const item = event.target.closest('[data-kind][data-id]'); if (item) { openEditor(item.dataset.kind, item.dataset.id); return; }
    const close = event.target.closest('[data-close]'); if (close) close.closest('dialog').close();
  });
  app.addEventListener('change', event => {
    const target = event.target;
    if (target.dataset.taskDone) { const task = data.tasks.find(value => value.id === target.dataset.taskDone); if (task) { task.done = target.checked; changed(); } }
    if (target.dataset.subtaskTask) { const task = data.tasks.find(value => value.id === target.dataset.subtaskTask); const sub = task?.subtasks?.find(value => value.id === target.dataset.subtaskId); if (sub) { sub.done = target.checked; changed(); } }
  });
  $('#agenda-event-form').onsubmit = saveEvent; $('#agenda-task-form').onsubmit = saveTask; $('#agenda-contact-form').onsubmit = saveContact;
  $('#agenda-event-form').onchange = updateEventFields;
  $('#agenda-delete-event').onclick = () => deleteEntry('event'); $('#agenda-delete-task').onclick = () => deleteEntry('task'); $('#agenda-delete-contact').onclick = () => deleteEntry('contact');
  $('#agenda-add-subtask').onclick = () => addSubtaskRow();
  window.addEventListener('beforeunload', event => { if (dirty || saving || conflict) { event.preventDefault(); event.returnValue = ''; } });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkReminders(); });
  setInterval(checkReminders, 30000);
  (async () => {
    key = readOfficeKey(csrf); if (!key) return;
    let badKey = false;
    try {
      const result = await request(api);
      if (result.agenda) { try { data = normalize(decrypt(result.agenda.envelope)); } catch (error) { badKey = true; throw error; } }
      else data = blankAgenda();
      revision = result.agenda?.revision ?? null; setUnlocked();
    } catch (error) { key?.fill(0); key = null; if (badKey) forgetOfficeKey(); status('LOCKED'); message(`Could not load Agenda: ${error.message}`); }
  })();
}
