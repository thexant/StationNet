(function () {
  'use strict';
  const core = globalThis.ThemeCore;
  const data = globalThis.ThemeData;
  const $ = id => document.getElementById(id);
  const storageKey = 'stationnet-theme-creator.draft.v1';
  const mobile = matchMedia('(max-width: 1099px)');
  let state = core.initial();
  let storageAvailable = true;
  let attemptedExport = false;
  const colourErrors = new Map();
  const labels = {
    green: 'Main text & borders', 'green-bright': 'Headings & emphasis', 'green-dim': 'Dividers & subtle borders',
    cyan: 'Links & accents', magenta: 'Hover & secondary accents', amber: 'Warnings & highlights',
    red: 'Errors & danger', 'red-dim': 'Muted danger', grey: 'Captions & metadata', white: 'Secondary text',
    black: 'Page background', field: 'Input background', button: 'Button background',
    masthead: 'Logo colour', 'masthead-shadow': 'Logo shadow'
  };
  const services = [
    ['BULLETIN BOARD', 'READ AND WRITE POSTS TO THE STATION BULLETIN BOARD'],
    ['ANNOUNCEMENTS', 'OFFICIAL STATION NOTICES'], ['EVENTS', 'UPCOMING STATION EVENTS AND RSVPS'],
    ['WIKI', "READ THE STATION'S OFFLINE KNOWLEDGE LIBRARY"], ['LIBRARY', 'READ STATION BOOKS'],
    ['ARCADE', 'PLAY STATION GAMES OFFLINE'], ['STATION CHAT', 'LIVE STATION CHAT'],
    ['PRIVATE MAIL', 'COMPOSE AND READ PRIVATE MESSAGES'], ['RADIO', 'AUDIO RECEIVER AND TRANSMISSION CONSOLE'],
    ['FILE ARCHIVE', 'SHARED STATION FILES'], ['USER DIRECTORY', 'LIST OF REGISTERED USERS ON THIS STATION'],
    ['PROGRAM MANAGER', 'STATION-HOSTED SOFTWARE AND GAMES DIRECTORY'], ['STATION MUD', 'TEXT BASED MULTIPLAYER RPG']
  ];
  for (const [index, [name, description]] of services.entries()) {
    const link = document.createElement('a');
    link.href = '#demo';
    // Fixed labels and descriptions from terminal.html; user metadata is never HTML.
    link.innerHTML = `<span class="service-number">[${String(index + 1).padStart(2, '0')}]</span><span class="service-copy"><strong>${name}</strong><small>${description}</small></span><span class="service-state">* ONLINE</span>`;
    $('service-list').append(link);
  }
  for (const [id, palette] of Object.entries(data.presets)) {
    const option = document.createElement('option');
    option.value = id;
    option.textContent = palette.name;
    $('preset').append(option);
  }
  const customOption = document.createElement('option');
  customOption.value = '';
  customOption.textContent = 'Custom palette';
  customOption.disabled = true;
  $('preset').prepend(customOption);
  $('preset').value = 'cyan';

  function message(text, error = false) {
    $('app-message').textContent = text;
    $('app-message').dataset.error = String(error);
  }
  function storageLabel() {
    $('storage-status').textContent = storageAvailable ? 'Saved in this browser' : 'Draft saving unavailable';
  }
  function save() {
    try { localStorage.setItem(storageKey, JSON.stringify(state)); storageAvailable = true; }
    catch (_) { storageAvailable = false; }
    storageLabel();
  }
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved) {
      try { state = core.restoreDraft(saved); $('preset').value = ''; message('Your last draft has been restored.'); }
      catch (_) { message('The saved draft could not be restored. Starting with default colours.', true); }
    }
  } catch (_) { storageAvailable = false; }
  storageLabel();

  function renderPreview() {
    const variables = core.variables(state);
    for (const [name, value] of Object.entries(variables)) $('station-preview').style.setProperty(name, value);
    for (const key of ['field', 'button']) {
      const note = $('automatic-' + key);
      if (!note) continue;
      const automatic = state.includeSite && state.site[key] === data.legacy[key] && state.site.green !== data.legacy.green;
      note.hidden = !automatic;
      note.textContent = `Preview tint: ${variables['--surface-' + key]} (from main colour)`;
    }
    $('site-colours').previousElementSibling.textContent = state.includeSite ? 'Text, accents, borders and surfaces.' : 'Excluded from export. Preview uses default interface colours.';
    $('masthead-colours').previousElementSibling.textContent = state.includeMasthead ? 'Logo colour and shadow.' : 'Excluded from export. Preview uses the default masthead.';
  }
  function colourInput(section, key, value, source) {
    const path = `${section}-${key}`;
    const input = $('hex-' + path);
    const error = $('error-' + path);
    const valid = core.hex.test(value);
    input.setAttribute('aria-invalid', String(!valid));
    error.hidden = valid;
    error.textContent = valid ? '' : 'Enter a six-digit hex colour, such as #00d8cf.';
    if (!valid) { colourErrors.set(path, input); return; }
    colourErrors.delete(path);
    state[section][key] = value.toLowerCase();
    if (source === 'picker') input.value = state[section][key];
    else $('picker-' + path).value = state[section][key];
    $('preset').value = '';
    message('');
    renderPreview();
    save();
  }
  function renderColours() {
    colourErrors.clear();
    for (const [section, keys] of [['site', data.siteKeys], ['masthead', data.mastheadKeys]]) {
      const container = $(section + '-colours');
      container.replaceChildren();
      for (const key of keys) {
        const path = section + '-' + key;
        const row = document.createElement('div');
        row.className = 'colour-row';
        row.innerHTML = `<div class="colour-row-heading"><label for="hex-${path}">${labels[key]}</label><span class="colour-key">${key}</span></div><div class="colour-inputs"><div class="colour-picker-wrap"><input type="color" id="picker-${path}" aria-label="Pick ${labels[key].toLowerCase()}" aria-describedby="description-${path}"></div><input type="text" id="hex-${path}" spellcheck="false" autocomplete="off" aria-invalid="false" aria-describedby="description-${path} error-${path}"></div><p class="colour-description" id="description-${path}"></p><p class="field-error" id="error-${path}" hidden></p>`;
        if (['field', 'button'].includes(key)) {
          const note = document.createElement('p');
          note.className = 'colour-automatic';
          note.id = 'automatic-' + key;
          note.hidden = true;
          row.append(note);
        }
        container.append(row);
        $('description-' + path).textContent = data.descriptions[key];
        $('hex-' + path).value = state[section][key];
        $('picker-' + path).value = state[section][key];
        $('hex-' + path).addEventListener('input', event => colourInput(section, key, event.target.value, 'hex'));
        $('picker-' + path).addEventListener('input', event => colourInput(section, key, event.target.value, 'picker'));
      }
    }
  }
  function showMetadataErrors() {
    const errors = attemptedExport ? core.metadataErrors(state) : {};
    for (const key of ['name', 'author', 'id']) {
      $('theme-' + key).setAttribute('aria-invalid', String(Boolean(errors[key])));
      $(key + '-error').hidden = !errors[key];
      $(key + '-error').textContent = errors[key] || '';
    }
    $('sections-error').hidden = !errors.sections;
    $('sections-error').textContent = errors.sections || '';
    return errors;
  }
  function syncControls() {
    for (const key of ['name', 'author', 'id']) $('theme-' + key).value = state[key];
    $('include-site').checked = state.includeSite;
    $('include-masthead').checked = state.includeMasthead;
    renderColours();
    showMetadataErrors();
    renderPreview();
  }
  function selectTab(name, focus = false) {
    for (const id of ['palette', 'details']) {
      const active = id === name;
      $(id + '-tab').setAttribute('aria-selected', String(active));
      $(id + '-tab').tabIndex = active ? 0 : -1;
      $(id + '-panel').hidden = !active;
    }
    if (focus) $(name + '-tab').focus();
  }
  for (const name of ['palette', 'details']) {
    $(name + '-tab').addEventListener('click', () => selectTab(name));
    $(name + '-tab').addEventListener('keydown', event => {
      let target;
      if (['ArrowRight', 'ArrowLeft'].includes(event.key)) target = name === 'palette' ? 'details' : 'palette';
      if (event.key === 'Home') target = 'palette';
      if (event.key === 'End') target = 'details';
      if (target) { event.preventDefault(); selectTab(target, true); }
    });
  }
  function expandDrawer(expanded) {
    const editor = $('editor');
    // Move focus before hiding any focused editor control.
    if (!expanded && mobile.matches && $('editor-body').contains(document.activeElement)) $('drawer-toggle').focus();
    editor.dataset.expanded = String(expanded);
    $('drawer-toggle').setAttribute('aria-expanded', String(expanded));
    $('drawer-toggle').children[1].textContent = expanded ? 'Close editor' : 'Edit your theme';
    $('drawer-toggle').children[2].textContent = expanded ? '↓' : '↑';
    $('editor-body').inert = mobile.matches && !expanded;
  }
  $('drawer-toggle').addEventListener('click', () => expandDrawer($('editor').dataset.expanded !== 'true'));
  $('editor').addEventListener('keydown', event => {
    if (event.key === 'Escape' && mobile.matches) { event.preventDefault(); expandDrawer(false); }
  });
  mobile.addEventListener('change', () => expandDrawer($('editor').dataset.expanded === 'true'));
  document.querySelector('.creator-skip').addEventListener('click', () => {
    if (mobile.matches) expandDrawer(true);
  });
  for (const key of ['name', 'author', 'id']) {
    $('theme-' + key).addEventListener('input', event => {
      state[key] = event.target.value;
      if (key === 'id') state.idEdited = true;
      if (key === 'name' && !state.idEdited) { state.id = core.slug(state.name); $('theme-id').value = state.id; }
      showMetadataErrors();
      message('');
      save();
    });
  }
  for (const [id, key] of [['include-site', 'includeSite'], ['include-masthead', 'includeMasthead']]) {
    $(id).addEventListener('change', event => {
      state[key] = event.target.checked;
      renderPreview();
      showMetadataErrors();
      message('');
      save();
    });
  }
  $('apply-preset').addEventListener('click', () => {
    if (!$('preset').value) { message('Choose a starting palette first.'); return; }
    Object.assign(state, core.preset($('preset').value));
    syncControls();
    save();
    message('Palette applied. Your theme details have been kept.');
  });
  $('export-theme').addEventListener('click', () => {
    attemptedExport = true;
    const errors = showMetadataErrors();
    if (colourErrors.size) {
      selectTab('palette');
      if (mobile.matches) expandDrawer(true);
      const input = colourErrors.values().next().value;
      input.closest('details').open = true;
      input.focus();
      message('Correct the highlighted hex colour before exporting.', true);
      return;
    }
    if (Object.keys(errors).length) {
      selectTab('details');
      if (mobile.matches) expandDrawer(true);
      const key = Object.keys(errors)[0];
      $(key === 'sections' ? 'include-site' : 'theme-' + key).focus();
      message('Complete the highlighted theme details before exporting.', true);
      return;
    }
    try {
      const text = core.exportTheme(state);
      const url = URL.createObjectURL(new Blob([text], {type: 'application/json;charset=utf-8'}));
      const link = document.createElement('a');
      link.href = url;
      link.download = state.id + '.json';
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      message(`Exported ${state.id}.json. Import it in Station settings.`);
      save();
    } catch (error) { message(error.message, true); }
  });
  $('import-theme').addEventListener('click', () => $('import-file').click());
  $('import-file').addEventListener('change', async event => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      if (!file.size || file.size > 16384) throw new Error('Choose a nonempty UTF-8 JSON file up to 16 KiB.');
      const bytes = await file.arrayBuffer();
      let text;
      // Preserve a BOM: Python's existing parser rejects it, so this does too.
      try { text = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(bytes); }
      catch (_) { throw new Error('Theme must be valid UTF-8 JSON.'); }
      const imported = core.importTheme(text);
      state = imported;
      attemptedExport = false;
      $('preset').value = '';
      syncControls();
      save();
      message(`Imported “${state.name}”. Ready to edit.`);
    } catch (error) { message(error.message, true); }
    finally { event.target.value = ''; }
  });
  $('reset-theme').addEventListener('click', () => {
    if (!confirm('Reset this theme? Your current draft will be cleared and default colours restored. Export it first to keep a copy.')) return;
    state = core.initial();
    attemptedExport = false;
    $('preset').value = 'cyan';
    try { localStorage.removeItem(storageKey); } catch (_) { storageAvailable = false; }
    syncControls();
    storageLabel();
    message('Draft cleared. Default colours restored.');
  });
  $('station-preview').addEventListener('click', event => {
    if (event.target.closest('a')) event.preventDefault();
  });
  $('vfx-toggle').addEventListener('click', event => {
    const enabled = event.currentTarget.getAttribute('aria-pressed') !== 'true';
    event.currentTarget.setAttribute('aria-pressed', String(enabled));
    event.currentTarget.textContent = 'VFX: ' + (enabled ? 'ON' : 'OFF');
    $('station-preview').dataset.vfx = String(enabled);
  });
  expandDrawer(false);
  syncControls();
})();
