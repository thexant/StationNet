// Pure helpers shared by the BASIC editor, its sandboxed runner, and tests.
export const PROTOCOL = 'stationnet-basic-1';
export const MAX_SOURCE_CHARS = 2 * 1024 * 1024;
export const MAX_TITLE_CHARS = 160;
export const MAX_ECHO_CHARS = 4000;
export const DEFAULT_TITLE = 'Untitled program';
export const DEFAULT_SOURCE = "' New BASIC program\r\nPRINT \"Hello from StationNet BASIC!\"\r\n";

export function blankProgram(title = DEFAULT_TITLE, source = DEFAULT_SOURCE) {
  return { version: 1, title, source };
}

export function normalizeProgram(value) {
  if (!value || value.version !== 1 || typeof value.source !== 'string') throw new Error('Invalid BASIC program data.');
  if (value.source.length > MAX_SOURCE_CHARS) throw new Error('BASIC program is too large.');
  return { version: 1, title: cleanTitle(value.title), source: value.source };
}

export function cleanTitle(value) {
  return String(value ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, MAX_TITLE_CHARS) || DEFAULT_TITLE;
}

// .bas files are plain text. Files from QBasic/QB64 are usually code page 437 or
// Latin-1; QBJS decodes non-UTF-8 files as ISO-8859-1, and so do we.
export function decodeBas(bytes) {
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let text;
  try { text = new TextDecoder('utf-8', { fatal: true }).decode(data); }
  catch { text = new TextDecoder('iso-8859-1').decode(data); }
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  if (text.includes('\u0000')) throw new Error('This does not look like a BASIC text file.');
  if (text.length > MAX_SOURCE_CHARS) throw new Error('BASIC files are limited to 2 MB of text.');
  return text;
}

// Export with DOS line endings, which QBasic, QB64, and QBJS all accept.
export function encodeBas(source) {
  return new TextEncoder().encode(String(source).replace(/\r\n?|\n/g, '\r\n'));
}

export function titleFromFilename(name) {
  return cleanTitle(String(name || '').replace(/\.(bas|bi|bm|txt)$/i, ''));
}

export function basFilename(title) {
  const base = cleanTitle(title).replace(/[\\/:*?"<>|]/g, '_').replace(/\s+/g, ' ').replace(/^\.+/, '').slice(0, 120) || 'program';
  return `${base}.bas`;
}

const isLine = value => Number.isInteger(value) && value >= 0 && value < 10_000_000;
const text = (value, limit = MAX_ECHO_CHARS) => String(value ?? '').slice(0, limit);

// Messages from the runner arrive from an opaque-origin frame that executes
// untrusted code, so only the expected shapes are passed on, as plain data.
export function readRunnerMessage(data, runId) {
  if (!data || typeof data !== 'object' || data.protocol !== PROTOCOL || typeof data.type !== 'string') return null;
  if (data.type === 'ready') return { type: 'ready' };
  if (data.runId !== runId || runId === null) return null;
  switch (data.type) {
    case 'heartbeat': return { type: 'heartbeat' };
    case 'state': return ['compiling', 'running', 'ended', 'error'].includes(data.state) ? { type: 'state', state: data.state } : null;
    case 'echo': return { type: 'echo', text: text(data.text) };
    case 'error': return { type: 'error', line: isLine(data.line) ? data.line : 0, text: text(data.text) };
    case 'warnings': return Array.isArray(data.items) ? { type: 'warnings', items: data.items.slice(0, 200).map(item => ({
      error: item?.mtype === 1, line: isLine(item?.line) ? item.line : 0, text: text(item?.text, 1000), module: text(item?.module, 300),
    })) } : null;
    case 'file': return typeof data.name === 'string' && data.data instanceof ArrayBuffer && data.data.byteLength <= 16 * 1024 * 1024
      ? { type: 'file', name: data.name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').slice(0, 120) || 'download', data: data.data } : null;
    case 'storage': { const items = storageItems(data.items); return items ? { type: 'storage', items } : null; }
    default: return null;
  }
}

export function readHostMessage(data) {
  if (!data || typeof data !== 'object' || data.protocol !== PROTOCOL || typeof data.runId !== 'string') return null;
  if (data.type === 'run' && typeof data.source === 'string' && data.source.length <= MAX_SOURCE_CHARS) {
    const message = { type: 'run', runId: data.runId, source: data.source };
    if (data.files !== undefined) { const files = packageFiles(data.files); if (!files) return null; message.files = files; }
    if (data.storage !== undefined) { const storage = storageItems(data.storage); if (!storage) return null; message.storage = storage; }
    return message;
  }
  if (data.type === 'stop' || data.type === 'focus') return { type: data.type, runId: data.runId };
  return null;
}

// Installed BASIC applications. These limits mirror the server's package
// validation in stationnet/applications.py and its application storage API.
export const PACKAGE_FORMAT = 1;
export const PACKAGE_ENTRYPOINT = 'main.bas';
export const PACKAGE_MAX_FILES = 64;
export const PACKAGE_MAX_FILE_BYTES = 1024 * 1024;
export const PACKAGE_MAX_EXPANDED_BYTES = 4 * 1024 * 1024;
export const PACKAGE_CATEGORIES = ['Games', 'Utilities', 'Creative', 'Educational', 'Productivity', 'Other'];
export const PACKAGE_INPUTS = ['keyboard', 'pointer', 'fine-pointer', 'touch', 'pointer-lock', 'gamepad'];
export const PACKAGE_PLATFORMS = { universal: 'universal', desktop: ['desktop'], touch: ['touch'] };
export const STORAGE_KEY = 'basic.localStorage';
export const MAX_STORAGE_BYTES = 2 * 1024 * 1024 - 64;
const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const PART_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/;
const DEVICE_NAMES = new Set(['CON', 'PRN', 'AUX', 'NUL', ...[1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap(i => [`COM${i}`, `LPT${i}`])]);
const RESERVED_SLUGS = new Set(['office-sheets', 'office-documents', 'office-presentations', 'office-agenda', 'office-draw', 'office-basic']);

const safePart = part => PART_RE.test(part) && !part.endsWith('.') && !DEVICE_NAMES.has(part.split('.')[0].toUpperCase());
export const safePackagePath = path => typeof path === 'string' && path.length <= 240 && path.split('/').length <= 8 && path.split('/').every(safePart);

// Files preloaded into an installed program's virtual file system.
export function packageFiles(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const entries = Object.entries(value);
  if (entries.length > PACKAGE_MAX_FILES) return null;
  let total = 0; const result = {};
  for (const [path, data] of entries) {
    if (!safePackagePath(path) || !(data instanceof ArrayBuffer) || data.byteLength > PACKAGE_MAX_FILE_BYTES) return null;
    total += data.byteLength; if (total > PACKAGE_MAX_EXPANDED_BYTES) return null;
    result[path] = data;
  }
  return result;
}

// A program's localStorage, saved as one JSON object of strings.
export function storageItems(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const result = {};
  for (const [key, item] of Object.entries(value)) { if (typeof item !== 'string') return null; result[key] = item; }
  return new TextEncoder().encode(JSON.stringify(result)).length <= MAX_STORAGE_BYTES ? result : null;
}

export function packageSlug(title) {
  const slug = cleanTitle(title).normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64).replace(/-+$/, '') || 'basic-program';
  return RESERVED_SLUGS.has(slug) ? `${slug}-program` : slug;
}

export function assetPath(name) {
  const base = String(name ?? '').split(/[\\/]/).pop().replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^[^A-Za-z0-9]+/, '').slice(0, 80).replace(/\.+$/, '');
  const path = `assets/${base}`;
  if (!base || !safePackagePath(path)) throw new Error(`Rename "${name}" to use letters, numbers, dots, dashes, or underscores.`);
  return path;
}

export function defaultPackage(title, author) {
  const name = cleanTitle(title).slice(0, 100), who = String(author ?? '').trim().slice(0, 100) || 'StationNet user';
  return {
    name, slug: packageSlug(name), description: `A BASIC program by ${who}.`, author: who, version: '1.0.0',
    category: 'Games', platforms: 'universal', inputs: [], presentation: 'standard',
  };
}

// Returns { manifest, errors }. Errors name the field so the dialog can show them.
export function buildManifest(fields) {
  const errors = {}, value = name => String(fields?.[name] ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').trim();
  const name = value('name'), slug = value('slug'), description = value('description'), author = value('author'), version = value('version');
  if (!name || name.length > 100) errors.name = 'Enter a name of 1-100 characters.';
  if (!SLUG_RE.test(slug) || slug.length > 80) errors.slug = 'Use lowercase letters, numbers, and single hyphens.';
  else if (RESERVED_SLUGS.has(slug)) errors.slug = 'This identifier is reserved by StationNet.';
  if (!description || description.length > 500) errors.description = 'Enter a description of 1-500 characters.';
  if (!author || author.length > 100) errors.author = 'Enter an author of 1-100 characters.';
  if (!version || version.length > 30) errors.version = 'Enter a version of 1-30 characters.';
  const category = PACKAGE_CATEGORIES.includes(fields?.category) ? fields.category : null;
  if (!category) errors.category = 'Choose a category.';
  const platforms = PACKAGE_PLATFORMS[fields?.platforms];
  if (!platforms) errors.platforms = 'Choose the supported devices.';
  const inputs = Array.isArray(fields?.inputs) ? [...new Set(fields.inputs)] : [];
  if (inputs.some(item => !PACKAGE_INPUTS.includes(item))) errors.inputs = 'Unknown input requirement.';
  const presentation = ['standard', 'immersive'].includes(fields?.presentation) ? fields.presentation : null;
  if (!presentation) errors.presentation = 'Choose a presentation.';
  if (Object.keys(errors).length) return { manifest: null, errors };
  return {
    errors,
    manifest: {
      package_format: PACKAGE_FORMAT, runtime: 'basic', slug, name, description, author, version, category,
      entrypoint: PACKAGE_ENTRYPOINT, platforms, required_inputs: inputs, preferred_presentation: presentation,
      fullscreen_target: 'application',
    },
  };
}

// Maps an exception from compiled QBJS code to a BASIC source line, following
// the QBJS IDE: generated code runs in an AsyncFunction whose body starts two
// lines after the function header.
export function errorJsLine(error) {
  const depth = Number.isInteger(error?._stackDepth) ? error._stackDepth : 0;
  const stack = String(error?.stack || '').split('\n');
  let found = 0;
  for (const frame of stack) {
    const match = frame.includes('eval at') ? frame.match(/<anonymous>:(\d+):\d+\)?\s*$/) : frame.match(/> AsyncFunction:(\d+):\d+/);
    if (!match) continue;
    if (found === depth) return Math.max(0, Number(match[1]) - 2);
    found++;
  }
  if (Number.isInteger(error?.line)) return Math.max(0, error.line - 1);
  return 0;
}
