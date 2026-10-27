// Office BASIC runner. This document is framed with sandbox="allow-scripts", so it
// has an opaque origin: no StationNet cookies, storage, or DOM. Its CSP closes
// network, frame, and worker access. One frame compiles and runs one program;
// the editor discards the frame to stop it or to run again.
import { PROTOCOL, MAX_ECHO_CHARS, readHostMessage, errorJsLine } from './model.js';
import { fonts, binaryFiles, textFiles } from './assets.js';

/* global QB, GX, _QBCompiler */
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const container = document.getElementById('gx-container');
let runId = null, started = false, echoCount = 0;

function post(type, data = {}, transfer = []) {
  window.parent.postMessage({ protocol: PROTOCOL, type, runId, ...data }, '*', transfer);
}

function lock(target, name, value) {
  try { Object.defineProperty(target, name, { value, writable: false, configurable: false }); } catch { /* Not present in this browser. */ }
}

function memoryStorage() {
  const items = new Map();
  return Object.freeze({
    get length() { return items.size; },
    key: index => [...items.keys()][index] ?? null,
    getItem: key => items.has(String(key)) ? items.get(String(key)) : null,
    setItem: (key, value) => { items.set(String(key), String(value)); },
    removeItem: key => { items.delete(String(key)); },
    clear: () => items.clear(),
  });
}

// Defence in depth. CSP already blocks fetch, sockets, frames, and workers;
// WebRTC is not covered by CSP, so its constructors are removed before any
// program code runs.
function harden() {
  for (const name of ['RTCPeerConnection', 'webkitRTCPeerConnection', 'RTCDataChannel', 'RTCSessionDescription', 'RTCIceCandidate',
    'WebTransport', 'WebSocket', 'EventSource', 'Worker', 'SharedWorker', 'PresentationRequest']) lock(window, name, undefined);
  lock(window, 'open', () => null);
  // Opaque origins have no web storage; lib/web/storage.bas gets per-run memory.
  lock(window, 'localStorage', memoryStorage());
  lock(window, 'sessionStorage', memoryStorage());
  // A same-origin about:blank child would expose fresh copies of the globals above.
  for (const type of ['HTMLIFrameElement', 'HTMLFrameElement', 'HTMLObjectElement', 'HTMLEmbedElement']) {
    if (!window[type]) continue;
    lock(window[type].prototype, 'contentWindow', null);
    lock(window[type].prototype, 'contentDocument', null);
  }
  new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) {
      if (node.nodeType !== 1) continue;
      for (const frame of [node, ...node.querySelectorAll('iframe,frame,object,embed')]) if (frame.matches('iframe,frame,object,embed')) frame.remove();
    }
  }).observe(document.documentElement, { childList: true, subtree: true });

  // QBJS reads imports and data files through fetch; serve only the virtual
  // file system and report anything else as not found.
  const vfsResponse = input => {
    const url = typeof input === 'string' ? input : String(input?.url ?? input ?? '');
    const vfs = GX.vfs(), file = vfs.getNode(url, GX.vfsCwd());
    if (file && file.type === vfs.FILE) return new Response(file.data.slice(0), { status: 200 });
    return new Response('', { status: 404, statusText: 'Network access is disabled in Office BASIC.' });
  };
  lock(window, 'fetch', async input => vfsResponse(input));

  // Downloads are blocked in the sandbox. Hand files to the editor instead,
  // where the user can choose to save them.
  QB.downloadFile = async (data, name) => {
    const buffer = await new Blob([data]).arrayBuffer();
    if (buffer.byteLength > 16 * 1024 * 1024) throw new Error('Files offered for download are limited to 16 MB.');
    post('file', { name: String(name || 'download'), data: buffer }, [buffer]);
  };

  const echo = prefix => (...args) => {
    if (echoCount++ > 5000) return;
    // Apply console-style %s/%d substitutions, as GX uses them for gamepad messages.
    if (typeof args[0] === 'string' && /%[sdifo]/.test(args[0])) {
      const rest = args.slice(1);
      args = [args[0].replace(/%[sdifo]/g, spec => rest.length ? (spec === '%d' || spec === '%i' ? String(Math.trunc(Number(rest.shift()))) : safeString(rest.shift())) : spec), ...rest];
    }
    post('echo', { text: (prefix + args.map(value => typeof value === 'string' ? value : safeString(value)).join(' ')).slice(0, MAX_ECHO_CHARS) });
  };
  console.log = echo(''); console.info = echo(''); console.warn = echo('WARN: '); console.error = echo('ERROR: ');
  QB.sub__Echo = msg => console.log(String(msg ?? ''));
}

function safeString(value) {
  try { return value instanceof Error ? value.message : typeof value === 'object' ? JSON.stringify(value) : String(value); }
  catch { return String(value); }
}

function writeFile(path, buffer) {
  const vfs = GX.vfs(), parts = path.split('/'), name = parts.pop();
  let dir = vfs.rootDirectory();
  for (const part of parts) dir = vfs.getNode(part, dir) || vfs.createDirectory(part, dir);
  const file = vfs.getNode(name, dir) || vfs.createFile(name, dir);
  file.data = new ArrayBuffer(0);
  vfs.writeData(file, buffer);
}

async function preload() {
  const vfs = GX.vfs();
  for (const [path, bytes] of Object.entries(binaryFiles)) writeFile(path, bytes.slice().buffer);
  for (const [path, text] of Object.entries(textFiles)) writeFile(path, vfs.textToData(text));
  await Promise.all(Object.entries(fonts).map(async ([name, bytes]) => {
    const face = new FontFace(name, bytes.slice().buffer);
    document.fonts.add(face);
    await face.load();
  }));
}

// Keep the program screen fitted to the frame. A transform (not CSS width)
// preserves QBJS's mouse coordinates, which come from event.offsetX/Y.
function fit() {
  const canvas = document.getElementById('gx-canvas');
  if (canvas && canvas.width && canvas.height) {
    const scale = Math.min(window.innerWidth / canvas.width, window.innerHeight / canvas.height) || 1;
    const size = `${canvas.width}x${canvas.height}x${scale}`;
    if (canvas.dataset.fit !== size) {
      canvas.dataset.fit = size;
      canvas.style.transform = `scale(${scale})`;
      canvas.style.left = `${Math.max(0, (window.innerWidth - canvas.width * scale) / 2)}px`;
      canvas.style.top = `${Math.max(0, (window.innerHeight - canvas.height * scale) / 2)}px`;
    }
  }
  requestAnimationFrame(fit);
}

function focusScreen() { (document.getElementById('gx-canvas') || container).focus({ preventScroll: true }); }

// Messages to the editor are delivered only when this frame's event loop turns,
// and compiled code can run for a long time on microtasks alone.
const nextTask = () => new Promise(resolve => setTimeout(resolve, 0));

async function run(source) {
  started = true;
  post('state', { state: 'compiling' });
  await nextTask();
  GX.reset();
  QB.start();
  QB.resize(window.innerWidth, window.innerHeight);
  let compiler = null, js;
  try {
    compiler = await _QBCompiler();
    js = await compiler.compile(source);
  } catch (error) {
    post('error', { line: 0, text: `Compiler failure: ${safeString(error)}` });
    post('state', { state: 'error' });
    QB.halt();
    return;
  }
  const warnings = compiler.getWarnings();
  post('warnings', { items: warnings.map(item => ({ mtype: item.mtype, line: item.line, text: String(item.text), module: item.module?.path || '' })) });
  if (warnings.some(item => item.mtype === 1)) {
    QB.halt(); GX.sceneStop();
    post('state', { state: 'error' });
    return;
  }
  post('state', { state: 'running' });
  focusScreen();
  await nextTask();
  try {
    await new AsyncFunction(js)();
    post('state', { state: 'ended' });
  } catch (error) {
    let line = 0;
    try { const jsLine = errorJsLine(error); line = jsLine ? Number(compiler.getSourceLine(jsLine)) || 0 : 0; } catch { line = 0; }
    post('error', { line, text: safeString(error) });
    QB.halt(); GX.sceneStop();
    post('state', { state: 'error' });
  }
}

window.addEventListener('message', event => {
  if (event.source !== window.parent || event.origin !== location.origin) return;
  const message = readHostMessage(event.data);
  if (!message) return;
  if (message.type === 'run' && !started) { runId = message.runId; run(message.source); }
  else if (message.runId !== runId) return;
  else if (message.type === 'stop') { QB.halt(); GX.sceneStop(); }
  else if (message.type === 'focus') focusScreen();
});
window.addEventListener('resize', () => { if (started) QB.resize(window.innerWidth, window.innerHeight); });
container.addEventListener('pointerdown', focusScreen);

harden();
preload().then(() => {
  requestAnimationFrame(fit);
  setInterval(() => { if (runId) post('heartbeat'); }, 1000);
  post('ready');
}, error => {
  post('ready');
  console.error(`Could not load BASIC resources: ${safeString(error)}`);
});
