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
    default: return null;
  }
}

export function readHostMessage(data) {
  if (!data || typeof data !== 'object' || data.protocol !== PROTOCOL || typeof data.runId !== 'string') return null;
  if (data.type === 'run' && typeof data.source === 'string' && data.source.length <= MAX_SOURCE_CHARS) return { type: 'run', runId: data.runId, source: data.source };
  if (data.type === 'stop' || data.type === 'focus') return { type: data.type, runId: data.runId };
  return null;
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
