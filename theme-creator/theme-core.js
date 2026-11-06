/* Portable theme format and palette math. Shared by the UI and offline tests. */
(function () {
  'use strict';
  const data = globalThis.ThemeData;
  // Unlike Python \Z, JavaScript $ also matches before a final newline.
  const hex = /^#[0-9a-fA-F]{6}(?![\s\S])/;
  const idPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*(?![\s\S])/;
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const clone = value => JSON.parse(JSON.stringify(value));
  // Python len() counts Unicode code points, not UTF-16 code units.
  const length = text => Array.from(text).length;
  const whitespace = '[\\x09-\\x0d\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]';
  const strip = text => text.replace(new RegExp('^' + whitespace + '+|' + whitespace + '+$', 'g'), '');
  const rgb = color => [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16));
  function roundEven(number) {
    const floor = Math.floor(number);
    return number - floor === 0.5 ? floor + floor % 2 : Math.round(number);
  }
  const mix = (color, weight) => rgb(color).map(channel => roundEven(channel * weight));
  const rgbHex = values => '#' + values.map(value => value.toString(16).padStart(2, '0')).join('');
  function preset(id) {
    const entry = data.presets[id];
    if (!entry) throw new Error('Choose a known StationNet preset.');
    return {
      site: Object.fromEntries(data.siteKeys.map(key => [key, entry[key]])),
      masthead: Object.fromEntries(data.mastheadKeys.map(key => [key, entry[key]]))
    };
  }
  function initial() {
    return {version: 1, id: '', name: '', author: '', idEdited: false,
      includeSite: true, includeMasthead: true, ...preset('cyan')};
  }
  function slug(name) {
    return name.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
      .replace(/[^a-z0-9]+/g, '-').slice(0, 64).replace(/^-+|-+$/g, '');
  }
  function metadataErrors(state, exporting = true) {
    const errors = {};
    if (typeof state.id !== 'string' || length(state.id) > 64 || !idPattern.test(state.id)) {
      errors.id = 'Use lowercase letters and numbers separated by single hyphens (up to 64 characters).';
    } else if (exporting && state.id === 'template-theme') {
      errors.id = 'Choose another ID. StationNet reserves template-theme for its template.';
    }
    for (const key of ['name', 'author']) {
      if (typeof state[key] !== 'string' || !strip(state[key]) || length(state[key]) > 80) {
        errors[key] = `Enter a ${key} of 1 to 80 characters.`;
      }
    }
    if (!state.includeSite && !state.includeMasthead) errors.sections = 'Include interface colours, masthead colours, or both.';
    return errors;
  }
  function validateSection(values, keys, section, comments = false) {
    if (!object(values)) throw new Error(`The ${section} section must be a colour object.`);
    const clean = {...values};
    if (comments) for (const key of keys) delete clean['_comment_' + key];
    if (Object.keys(clean).length !== keys.length || keys.some(key => !own(clean, key))) {
      throw new Error(`The ${section} section must contain exactly these colours: ${keys.join(', ')}.`);
    }
    if (Object.values(clean).some(value => typeof value !== 'string' || !hex.test(value))) {
      throw new Error(`Every ${section} colour must be a six-digit hex value such as #00d8cf.`);
    }
    return Object.fromEntries(keys.map(key => [key, clean[key].toLowerCase()]));
  }
  function importTheme(text) {
    if (new TextEncoder().encode(text).length > 16384 || !text.length) throw new Error('Choose a nonempty UTF-8 JSON file up to 16 KiB.');
    let document;
    try { document = JSON.parse(text); } catch (_) { throw new Error('Theme must be valid UTF-8 JSON.'); }
    const allowed = ['id', 'name', 'author', 'site', 'masthead'];
    if (!object(document) || Object.keys(document).some(key => !allowed.includes(key))) {
      throw new Error('Theme must contain only id, name, author, site, and masthead fields.');
    }
    const state = {...initial(), id: document.id, name: document.name, author: document.author,
      idEdited: true, includeSite: own(document, 'site'), includeMasthead: own(document, 'masthead')};
    const errors = metadataErrors(state, false);
    if (Object.keys(errors).length) throw new Error(Object.values(errors).join(' '));
    state.name = strip(state.name);
    state.author = strip(state.author);
    for (const [section, keys] of [['site', data.siteKeys], ['masthead', data.mastheadKeys]]) {
      if (own(document, section)) state[section] = validateSection(document[section], keys, section, true);
    }
    return state;
  }
  function exportTheme(state) {
    const errors = metadataErrors(state);
    if (Object.keys(errors).length) throw new Error(Object.values(errors).join(' '));
    const document = {id: state.id, name: strip(state.name), author: strip(state.author)};
    if (state.includeSite) document.site = validateSection(state.site, data.siteKeys, 'site');
    if (state.includeMasthead) document.masthead = validateSection(state.masthead, data.mastheadKeys, 'masthead');
    return JSON.stringify(document, null, 2) + '\n';
  }
  function restoreDraft(text) {
    const draft = JSON.parse(text);
    if (!object(draft) || draft.version !== 1 || typeof draft.idEdited !== 'boolean' ||
        typeof draft.includeSite !== 'boolean' || typeof draft.includeMasthead !== 'boolean' ||
        ['id', 'name', 'author'].some(key => typeof draft[key] !== 'string')) throw new Error('Invalid draft.');
    const state = initial();
    for (const key of ['id', 'name', 'author', 'idEdited', 'includeSite', 'includeMasthead']) state[key] = draft[key];
    state.site = validateSection(draft.site, data.siteKeys, 'site');
    state.masthead = validateSection(draft.masthead, data.mastheadKeys, 'masthead');
    return state;
  }
  function variables(state) {
    const defaults = preset('cyan');
    const site = state.includeSite ? state.site : defaults.site;
    const masthead = state.includeMasthead ? state.masthead : defaults.masthead;
    const variables = {};
    for (const key of data.siteKeys.slice(0, 11)) variables['--' + key] = site[key];
    const legacy = key => site[key].toLowerCase() === data.legacy[key];
    for (const [key, weight] of [['field', 0.03], ['button', 0.115]]) {
      variables['--surface-' + key] = legacy(key) && !legacy('green') ? rgbHex(mix(site.green, weight)) : site[key];
    }
    for (const [name, key, weight, fallback] of data.derived) variables[name] = legacy(key) ? fallback : rgbHex(mix(site[key], weight));
    for (const [name, key, weight, fallback] of data.derivedRgb) variables[name] = legacy(key) ? fallback : mix(site[key], weight).join(',');
    variables['--theme-rgb'] = rgb(site.green).join(',');
    variables['--accent-rgb'] = rgb(site.cyan).join(',');
    variables['--masthead-color'] = masthead.masthead;
    variables['--masthead-shadow'] = masthead['masthead-shadow'];
    return variables;
  }
  globalThis.ThemeCore = {hex, clone, preset, initial, slug, metadataErrors, importTheme, exportTheme, restoreDraft, variables, roundEven};
})();
