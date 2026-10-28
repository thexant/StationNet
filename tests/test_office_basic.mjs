import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  PROTOCOL, MAX_SOURCE_CHARS, blankProgram, normalizeProgram, cleanTitle, decodeBas, encodeBas, basFilename,
  titleFromFilename, readRunnerMessage, readHostMessage, errorJsLine,
  packageSlug, assetPath, defaultPackage, buildManifest, packageFiles, storageItems, safePackagePath,
  PACKAGE_MAX_FILES, PACKAGE_MAX_FILE_BYTES, MAX_STORAGE_BYTES,
} from '../stationnet/office_basic/model.js';
import { EXAMPLES } from '../stationnet/office_basic/examples.js';

// Program data.
assert.deepEqual(normalizeProgram(blankProgram('Demo', 'PRINT 1')), { version: 1, title: 'Demo', source: 'PRINT 1' });
assert.equal(normalizeProgram({ version: 1, title: '', source: '' }).title, 'Untitled program');
assert.equal(normalizeProgram({ version: 1, title: 'x'.repeat(500), source: '' }).title.length, 160);
assert.throws(() => normalizeProgram({ version: 2, title: 'x', source: '' }));
assert.throws(() => normalizeProgram({ version: 1, title: 'x' }));
assert.throws(() => normalizeProgram({ version: 1, title: 'x', source: 'a'.repeat(MAX_SOURCE_CHARS + 1) }));
assert.equal(cleanTitle('  tab\there\n '), 'tab here');

// .bas import: UTF-8, Latin-1 fallback, BOM, binary rejection.
assert.equal(decodeBas(new TextEncoder().encode('PRINT "héllo"')), 'PRINT "héllo"');
assert.equal(decodeBas(Uint8Array.from([0x50, 0x52, 0x49, 0x4e, 0x54, 0x20, 0xe9])), 'PRINT é');
assert.equal(decodeBas(Uint8Array.from([0xef, 0xbb, 0xbf, 0x43, 0x4c, 0x53])), 'CLS');
assert.throws(() => decodeBas(Uint8Array.from([0x00, 0x01, 0x02])), /BASIC text file/);

// .bas export uses CRLF line endings and UTF-8.
assert.equal(new TextDecoder().decode(encodeBas('CLS\nPRINT 1\r\nEND\r')), 'CLS\r\nPRINT 1\r\nEND\r\n');
assert.equal(basFilename('My: game/v2?'), 'My_ game_v2_.bas');
assert.equal(basFilename(''), 'Untitled program.bas');
assert.equal(basFilename('...hidden'), 'hidden.bas');
assert.equal(titleFromFilename('SNAKE.BAS'), 'SNAKE');
assert.equal(titleFromFilename('notes.txt'), 'notes');

// Runner messages are untrusted and reduced to expected shapes.
const msg = (type, data = {}) => ({ protocol: PROTOCOL, type, runId: 'r1', ...data });
assert.deepEqual(readRunnerMessage({ protocol: PROTOCOL, type: 'ready', runId: null }, 'r1'), { type: 'ready' });
assert.equal(readRunnerMessage(msg('heartbeat'), 'other'), null);
assert.equal(readRunnerMessage(msg('heartbeat'), null), null);
assert.equal(readRunnerMessage({ type: 'heartbeat', runId: 'r1' }, 'r1'), null);
assert.equal(readRunnerMessage(msg('state', { state: 'hacked' }), 'r1'), null);
assert.deepEqual(readRunnerMessage(msg('state', { state: 'running' }), 'r1'), { type: 'state', state: 'running' });
assert.equal(readRunnerMessage(msg('echo', { text: 'x'.repeat(10000) }), 'r1').text.length, 4000);
assert.deepEqual(readRunnerMessage(msg('error', { line: -4, text: '<b>bad</b>' }), 'r1'), { type: 'error', line: 0, text: '<b>bad</b>' });
assert.deepEqual(readRunnerMessage(msg('warnings', { items: [{ mtype: 1, line: 3, text: 'Syntax', module: '' }, { mtype: 2, line: 'x', text: 7 }] }), 'r1').items,
  [{ error: true, line: 3, text: 'Syntax', module: '' }, { error: false, line: 0, text: '7', module: '' }]);
assert.deepEqual(readRunnerMessage(msg('file', { name: '../evil.txt', data: new ArrayBuffer(3) }), 'r1').name, '.._evil.txt');
assert.equal(readRunnerMessage(msg('file', { name: 'a', data: 'not-a-buffer' }), 'r1'), null);
assert.equal(readRunnerMessage(msg('navigate', { url: 'https://example.com' }), 'r1'), null);

assert.deepEqual(readHostMessage({ protocol: PROTOCOL, type: 'run', runId: 'r1', source: 'PRINT 1' }), { type: 'run', runId: 'r1', source: 'PRINT 1' });
assert.equal(readHostMessage({ protocol: PROTOCOL, type: 'run', runId: 'r1', source: 7 }), null);
assert.deepEqual(readHostMessage({ protocol: PROTOCOL, type: 'stop', runId: 'r1' }), { type: 'stop', runId: 'r1' });
assert.equal(readHostMessage({ protocol: 'other', type: 'stop', runId: 'r1' }), null);

// Installed programs also send package files and saved localStorage.
const run = extra => readHostMessage({ protocol: PROTOCOL, type: 'run', runId: 'r1', source: 'CLS', ...extra });
const png = new ArrayBuffer(8);
assert.deepEqual(run({ files: { 'main.bas': png, 'assets/ship.png': png }, storage: { hi: '9' } }),
  { type: 'run', runId: 'r1', source: 'CLS', files: { 'main.bas': png, 'assets/ship.png': png }, storage: { hi: '9' } });
assert.equal(run({ files: { '../escape.bas': png } }), null);
assert.equal(run({ files: { '/abs.bas': png } }), null);
assert.equal(run({ files: { 'assets/CON.png': png } }), null);
assert.equal(run({ files: { 'a.bas': 'text' } }), null);
assert.equal(run({ files: { 'a.bas': new ArrayBuffer(PACKAGE_MAX_FILE_BYTES + 1) } }), null);
assert.equal(run({ files: Object.fromEntries(Array.from({ length: PACKAGE_MAX_FILES + 1 }, (_, i) => [`f${i}.bin`, png])) }), null);
assert.equal(run({ files: Object.fromEntries(Array.from({ length: 5 }, (_, i) => [`f${i}.bin`, new ArrayBuffer(PACKAGE_MAX_FILE_BYTES)])) }), null);
assert.equal(run({ storage: { n: 5 } }), null);
assert.equal(run({ storage: ['x'] }), null);
assert.deepEqual(packageFiles({}), {});
assert.equal(safePackagePath('assets/sub/ok-1.wav'), true);
assert.equal(safePackagePath('assets/trailing.'), false);
assert.deepEqual(storageItems({ a: 'b' }), { a: 'b' });
assert.equal(storageItems({ big: 'x'.repeat(MAX_STORAGE_BYTES) }), null);
assert.deepEqual(readRunnerMessage(msg('storage', { items: { score: '12' } }), 'r1'), { type: 'storage', items: { score: '12' } });
assert.equal(readRunnerMessage(msg('storage', { items: { score: 12 } }), 'r1'), null);
assert.equal(readRunnerMessage(msg('storage', { items: { score: '1' } }), 'other'), null);

// StationNet Package manifests mirror the server's validation.
assert.equal(packageSlug('Star Catcher!'), 'star-catcher');
assert.equal(packageSlug('  Ça va? Über__Game  '), 'ca-va-uber-game');
assert.equal(packageSlug('!!!'), 'basic-program');
assert.equal(packageSlug('Office Basic'), 'office-basic-program');
assert.ok(packageSlug('x'.repeat(200)).length <= 64);
assert.equal(assetPath('C:\\fakepath\\My Ship.png'), 'assets/My-Ship.png');
assert.equal(assetPath('theme.wav'), 'assets/theme.wav');
assert.throws(() => assetPath('...'), /Rename/);
assert.throws(() => assetPath('nul.txt'), /Rename/);
const defaults = defaultPackage('Star Catcher', 'coder');
assert.deepEqual(defaults, {
  name: 'Star Catcher', slug: 'star-catcher', description: 'A BASIC program by coder.', author: 'coder', version: '1.0.0',
  category: 'Games', platforms: 'universal', inputs: [], presentation: 'standard',
});
assert.equal(defaultPackage('', '').author, 'StationNet user');
const built = buildManifest({ ...defaults, inputs: ['keyboard', 'keyboard'], platforms: 'desktop', presentation: 'immersive' });
assert.deepEqual(built.errors, {});
assert.deepEqual(built.manifest, {
  package_format: 1, runtime: 'basic', slug: 'star-catcher', name: 'Star Catcher', description: 'A BASIC program by coder.',
  author: 'coder', version: '1.0.0', category: 'Games', entrypoint: 'main.bas', platforms: ['desktop'],
  required_inputs: ['keyboard'], preferred_presentation: 'immersive', fullscreen_target: 'application',
});
assert.equal(buildManifest(defaults).manifest.platforms, 'universal');
const invalid = buildManifest({ ...defaults, name: ' ', slug: 'Bad Slug', description: 'x'.repeat(501), author: '', version: '', category: 'Apps', platforms: 'tv', inputs: ['mind'], presentation: 'cinema' });
assert.equal(invalid.manifest, null);
assert.deepEqual(Object.keys(invalid.errors).sort(), ['author', 'category', 'description', 'inputs', 'name', 'platforms', 'presentation', 'slug', 'version']);
assert.match(buildManifest({ ...defaults, slug: 'office-draw' }).errors.slug, /reserved/);
assert.equal(buildManifest({ ...defaults, name: '  Padded  ' }).manifest.name, 'Padded');

// Stack lines from compiled AsyncFunction code map back like the QBJS IDE.
assert.equal(errorJsLine({ stack: 'Error: x\n    at eval (eval at run (https://h/static/office-basic-runner.js:1:99), <anonymous>:14:7)\n    at run (x.js:1:1)' }), 12);
assert.equal(errorJsLine({ stack: 'Error\n    at eval (eval at run (u), <anonymous>:5:1)\n    at eval (eval at run (u), <anonymous>:9:1)', _stackDepth: 1 }), 7);
assert.equal(errorJsLine({ stack: 'main@https://h/office/basic/runner line 2 > AsyncFunction:22:3\n' }), 20);
assert.equal(errorJsLine({ stack: 'no frames' }), 0);

// Examples are original, distinct, and stay within source limits.
assert.ok(EXAMPLES.length >= 4);
assert.equal(new Set(EXAMPLES.map(example => example.id)).size, EXAMPLES.length);
for (const example of EXAMPLES) {
  assert.ok(example.title && example.summary && example.source.includes('\r\n'));
  assert.deepEqual(normalizeProgram(blankProgram(example.title, example.source)).source, example.source);
}
assert.ok(EXAMPLES.some(example => /IMPORT \w+ FROM "lib\//i.test(example.source)), 'one example demonstrates bundled libraries');

// Every library the runner preloads exists in the vendored QBJS copy.
const assets = readFileSync(new URL('../stationnet/office_basic/assets.js', import.meta.url), 'utf8');
for (const [, path] of assets.matchAll(/from '\.\.\/static\/vendor\/qbjs\/([^']+)'/g)) {
  assert.ok(readFileSync(new URL(`../stationnet/static/vendor/qbjs/${path}`, import.meta.url)).length > 0, path);
}

console.log('office basic model tests passed');
