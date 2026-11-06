'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const {core, data} = require('./load-core.cjs')();
const plain = value => JSON.parse(JSON.stringify(value));
function theme() { return {...core.initial(), id: 'midnight-relay', name: 'Midnight Relay', author: 'Nightshift'}; }

test('default theme and ten built-in presets have every documented key', () => {
  assert.equal(Object.keys(data.presets).length, 10);
  for (const id of Object.keys(data.presets)) {
    const state = {...theme(), ...core.preset(id)};
    const exported = JSON.parse(core.exportTheme(state));
    assert.deepEqual(Object.keys(exported.site), plain(data.siteKeys));
    assert.deepEqual(Object.keys(exported.masthead), plain(data.mastheadKeys));
    assert.equal(Object.values(exported.site).every(value => core.hex.test(value)), true);
  }
});
test('combined and partial themes round trip without draft or CSS fields', () => {
  for (const [includeSite, includeMasthead] of [[true, true], [true, false], [false, true]]) {
    const state = {...theme(), includeSite, includeMasthead};
    const text = core.exportTheme(state);
    const document = JSON.parse(text);
    const imported = core.importTheme(text);
    assert.equal(imported.includeSite, includeSite);
    assert.equal(imported.includeMasthead, includeMasthead);
    assert.equal(core.exportTheme(imported), text);
    assert.equal(Object.hasOwn(document, 'version'), false);
    assert.equal(text.includes('--surface'), false);
  }
});
test('documented comments are stripped; all other unknown keys rejected', () => {
  const document = JSON.parse(core.exportTheme(theme()));
  document.site._comment_green = 'Documentation';
  document.masthead['_comment_masthead-shadow'] = 'Documentation';
  const state = core.importTheme(JSON.stringify(document));
  assert.equal(core.exportTheme(state).includes('_comment'), false);
  document.site._comment_unexpected = 'Unknown';
  assert.throws(() => core.importTheme(JSON.stringify(document)), /exactly/);
  delete document.site._comment_unexpected;
  document.unexpected = true;
  assert.throws(() => core.importTheme(JSON.stringify(document)), /only/);
});
test('invalid metadata and absent sections cannot export', () => {
  for (const id of ['', '-a', 'a-', 'a--b', 'UPPER', 'a b', 'a/b', 'a'.repeat(65), 'a\n', 'template-theme']) {
    assert.throws(() => core.exportTheme({...theme(), id}));
  }
  for (const field of ['name', 'author']) {
    for (const value of ['', ' \n\t', '\u0085', '\u001c', 'x'.repeat(81), null, 42]) {
      assert.throws(() => core.exportTheme({...theme(), [field]: value}));
    }
  }
  assert.throws(() => core.exportTheme({...theme(), includeSite: false, includeMasthead: false}), /Include/);
  assert.equal(JSON.parse(core.exportTheme({...theme(), name: '\u0085 Unicode theme \u001c'})).name, 'Unicode theme');
  assert.equal(JSON.parse(core.exportTheme({...theme(), name: '🎨'.repeat(80)})).name, '🎨'.repeat(80));
  assert.throws(() => core.exportTheme({...theme(), name: '🎨'.repeat(81)}));
});
test('import allows the annotated reserved template but export requires renaming', () => {
  const document = JSON.parse(core.exportTheme(theme()));
  document.id = 'template-theme';
  const state = core.importTheme(JSON.stringify(document));
  assert.equal(state.id, 'template-theme');
  assert.throws(() => core.exportTheme(state), /reserves/);
});
test('reject incomplete palettes, CSS injection, arrays, and trailing newlines', () => {
  for (const invalid of ['red', '#fff', '#12345g', '#112233\n', '#112233 ', 'red;body{display:none}', 123, null]) {
    const document = JSON.parse(core.exportTheme(theme()));
    document.site.green = invalid;
    assert.throws(() => core.importTheme(JSON.stringify(document)), /six-digit/);
  }
  const document = JSON.parse(core.exportTheme(theme()));
  delete document.site.grey;
  assert.throws(() => core.importTheme(JSON.stringify(document)), /exactly/);
  document.site = [];
  assert.throws(() => core.importTheme(JSON.stringify(document)), /object/);
  for (const invalid of ['null', '[]', '{}', '{', '"text"', '\ufeff{}']) assert.throws(() => core.importTheme(invalid));
});
test('import limit is UTF-8 bytes, not characters', () => {
  const text = core.exportTheme(theme());
  const full = text + ' '.repeat(16384 - new TextEncoder().encode(text).length);
  assert.equal(core.importTheme(full).id, 'midnight-relay');
  assert.throws(() => core.importTheme(full + ' '), /16 KiB/);
  const document = JSON.parse(text);
  document.site._comment_green = '🎨'.repeat(4200);
  assert.throws(() => core.importTheme(JSON.stringify(document)), /16 KiB/);
  assert.throws(() => core.importTheme(''), /nonempty/);
});
test('default surfaces remain exact and deliberate field/button values survive', () => {
  const state = theme();
  const values = core.variables(state);
  assert.equal(values['--surface-field'], '#030703');
  assert.equal(values['--surface-button'], '#001b08');
  assert.equal(values['--surface-selected'], '#10351c');
  assert.equal(values['--surface-tint-rgb'], '0,12,3');
  state.site.green = '#969696';
  const changed = core.variables(state);
  assert.equal(changed['--surface-field'], '#040404'); // 150 × .03 = 4.5, ties to even.
  assert.notEqual(changed['--surface-button'], '#001b08');
  state.site.field = '#fefefe';
  state.site.button = '#010203';
  assert.equal(core.variables(state)['--surface-field'], '#fefefe');
  assert.equal(core.variables(state)['--surface-button'], '#010203');
  assert.equal(core.roundEven(5.5), 6);
  assert.equal(core.roundEven(4.5), 4);
});
test('excluded sections preview defaults without discarding edited palettes', () => {
  const state = {...theme(), ...core.preset('violet')};
  const before = core.clone(state);
  state.includeSite = false;
  state.includeMasthead = false;
  const variables = core.variables(state);
  assert.equal(variables['--green'], data.presets.cyan.green);
  assert.equal(variables['--masthead-color'], data.presets.cyan.masthead);
  assert.deepEqual(plain(state.site), plain(before.site));
  assert.deepEqual(plain(state.masthead), plain(before.masthead));
  state.includeSite = true;
  state.includeMasthead = true;
  assert.equal(core.variables(state)['--green'], data.presets.violet.green);
});
test('name slugging is safe, bounded, and transliterates diacritics', () => {
  assert.equal(core.slug('  Midnight / Rélay! '), 'midnight-relay');
  assert.equal(core.slug('N'.repeat(63) + ' --- rest'), 'n'.repeat(63));
  assert.equal(core.slug('🎨'), '');
});
test('draft restoration accepts unfinished metadata and retains excluded edits', () => {
  const state = core.initial();
  state.includeSite = false;
  state.includeMasthead = false;
  state.site.green = '#123456';
  assert.deepEqual(plain(core.restoreDraft(JSON.stringify(state))), plain(state));
  for (const invalid of ['null', '{}', '{', JSON.stringify({...state, version: 2}), JSON.stringify({...state, site: {}})]) {
    assert.throws(() => core.restoreDraft(invalid));
  }
});
