// Trusted host for installed BASIC applications (Games & Apps display page).
// The program runs in the same sandboxed QBJS runner as Office BASIC. This page
// fetches the package's files, loads and saves the user's localStorage through
// the application storage API, and provides stop, restart, immersive, and
// fullscreen controls. Nothing from the runner is rendered as HTML.
import { decodeBas, storageItems, STORAGE_KEY } from './model.js';
import { createRunnerSession } from './session.js';

const display = document.querySelector('.application-display[data-app-runtime-kind="basic"]');
if (display) start();

function start() {
  const $ = selector => display.querySelector(selector);
  const screen = $('[data-basic-screen]'), status = $('[data-app-status]'), idle = $('[data-basic-idle]');
  const consoleList = $('[data-basic-console]'), consolePanel = $('[data-basic-console-panel]');
  const stopButton = $('[data-basic-stop]'), restartButton = $('[data-basic-restart]');
  const immersiveButton = $('[data-app-immersive]'), fullscreenButton = $('[data-app-fullscreen]');
  const immersiveExit = document.querySelector('[data-app-immersive-exit]'), fullscreenExit = $('[data-app-fullscreen-exit]');
  const { basicProgramUrl, appStorageUrl, appCsrf } = screen.dataset;
  let program = null, saved = {}, saveTimer = null, unsaved = null, immersive = false, closed = false;

  function log(kind, text) {
    const li = document.createElement('li'); li.dataset.kind = kind; li.textContent = text; consoleList.append(li);
    while (consoleList.childElementCount > 500) consoleList.firstElementChild.remove();
    consoleList.scrollTop = consoleList.scrollHeight;
    if (kind === 'error') consolePanel.open = true;
    return li;
  }
  const setStatus = label => { status.textContent = `PROGRAM STATUS / ${label}`; };

  async function storageRequest(body, keepalive = false) {
    const response = await fetch(appStorageUrl, {
      method: 'POST', credentials: 'same-origin', keepalive,
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': appCsrf },
      body: JSON.stringify({ scope: 'user', key: STORAGE_KEY, ...body }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.ok) throw new Error(result.error || `Request failed (${response.status}).`);
    return result;
  }
  // Saves are coalesced; the last snapshot from the runner always wins.
  function queueSave(items) { saved = items; unsaved = items; clearTimeout(saveTimer); saveTimer = setTimeout(flushSave, 250); }
  async function flushSave(keepalive = false) {
    clearTimeout(saveTimer); saveTimer = null;
    if (!unsaved) return;
    const items = unsaved; unsaved = null;
    try { await storageRequest({ operation: 'set', value: items }, keepalive); }
    catch (error) { if (!closed) log('error', `Could not save program data: ${error.message}`); }
  }

  const session = createRunnerSession({
    container: screen, runnerUrl: screen.dataset.basicRunnerUrl, title: `${display.dataset.appName} program screen`, onLog: log,
    onState: (state, label) => {
      setStatus(label);
      stopButton.disabled = !session.active || state === 'ended' || state === 'error';
      if (state === 'running' && display.dataset.appPresentation === 'immersive' && !immersive) setImmersive(true);
      if (state === 'ended') log('info', 'Program finished.');
      if (!session.active) { idle.textContent = 'Program stopped. Press RUN AGAIN to start it.'; idle.hidden = false; }
    },
    onMessage: data => {
      if (data.type === 'warnings') {
        for (const item of data.items) log(item.error ? 'error' : 'warning', `${item.error ? 'ERROR' : 'WARN'} ${item.module ? `${item.module} ` : ''}line ${item.line}: ${item.text}`);
      } else if (data.type === 'error') log('error', `RUNTIME ERROR${data.line ? ` (line ${data.line})` : ''}: ${data.text}`);
      else if (data.type === 'echo') log('output', data.text);
      else if (data.type === 'storage') queueSave(data.items);
      else if (data.type === 'file') {
        const li = log('info', `The program offered a file: ${data.name} (${data.data.byteLength} bytes)`), button = document.createElement('button');
        button.type = 'button'; button.className = 'secondary'; button.textContent = 'SAVE FILE';
        button.onclick = () => download(new Blob([data.data], { type: 'application/octet-stream' }), data.name);
        li.append(' ', button); consolePanel.open = true;
      }
    },
  });

  function download(blob, name) {
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = name; link.hidden = true; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }
  const bytes = value => Uint8Array.from(atob(value), char => char.charCodeAt(0));

  async function load() {
    setStatus('LOADING');
    const response = await fetch(basicProgramUrl, { credentials: 'same-origin' });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.ok || typeof result.files?.[result.entrypoint] !== 'string') throw new Error('The program files could not be loaded.');
    const files = {};
    for (const [path, value] of Object.entries(result.files)) files[path] = bytes(value).buffer;
    const source = decodeBas(new Uint8Array(files[result.entrypoint]));
    try { const stored = await storageRequest({ operation: 'get' }); saved = storageItems(stored.value) || {}; }
    catch (error) { saved = {}; log('warning', `Saved program data is unavailable: ${error.message}`); }
    program = { source, files };
  }
  async function run() {
    if (closed) return;
    restartButton.disabled = true;
    try {
      await flushSave();
      if (!program) await load();
      idle.hidden = true;
      session.run(program.source, { files: program.files, storage: saved });
    } catch (error) {
      setStatus('UNAVAILABLE'); idle.textContent = error.message; idle.hidden = false; log('error', error.message);
    } finally { restartButton.disabled = false; }
  }
  function stop() { session.stop(); flushSave(); }

  function setImmersive(value) {
    immersive = Boolean(value);
    document.body.classList.toggle('application-immersive', immersive);
    immersiveButton?.setAttribute('aria-pressed', String(immersive));
    if (immersiveExit) immersiveExit.hidden = !immersive;
    session.focus();
  }
  const fullscreenSupported = () => Boolean(document.fullscreenEnabled && typeof display.requestFullscreen === 'function');
  function toggleFullscreen() {
    if (document.fullscreenElement === display) document.exitFullscreen().catch(() => {});
    else {
      display.dataset.fullscreenTarget = 'application';
      display.requestFullscreen().then(() => session.focus(), error => { fullscreenButton.title = String(error.message || error); });
    }
  }
  document.addEventListener('fullscreenchange', () => {
    const active = document.fullscreenElement === display;
    fullscreenButton?.setAttribute('aria-pressed', String(active));
    if (fullscreenButton) fullscreenButton.textContent = active ? 'EXIT FULLSCREEN' : 'FULLSCREEN';
    if (fullscreenExit) fullscreenExit.hidden = !active;
    if (!active) delete display.dataset.fullscreenTarget;
    session.focus();
  });
  document.addEventListener('keydown', event => {
    // Keys typed into the program go to the sandbox frame; this only sees keys
    // pressed while the page itself has focus.
    if (event.key === 'Escape' && immersive && document.fullscreenElement !== display) { event.preventDefault(); setImmersive(false); }
  });
  immersiveButton?.addEventListener('click', () => setImmersive(!immersive));
  immersiveExit?.addEventListener('click', () => setImmersive(false));
  if (fullscreenButton) {
    if (!fullscreenSupported()) { fullscreenButton.disabled = true; fullscreenButton.title = 'Browser fullscreen is unavailable on this device'; }
    fullscreenButton.addEventListener('click', toggleFullscreen);
  }
  fullscreenExit?.addEventListener('click', () => document.exitFullscreen().catch(() => {}));
  stopButton.addEventListener('click', stop);
  restartButton.addEventListener('click', run);
  screen.addEventListener('pointerdown', () => session.focus());
  addEventListener('pagehide', () => { closed = true; session.stop(); flushSave(true); }, { once: true });

  // The same capability check as JavaScript applications: requirements that
  // are not met pause launch with an explanation and a Launch anyway option.
  const parse = name => { try { const value = JSON.parse(display.dataset[name] || '[]'); return Array.isArray(value) ? value : []; } catch { return []; } };
  const media = query => typeof matchMedia === 'function' && matchMedia(query).matches;
  const finePointer = media('(any-pointer: fine)'), touch = navigator.maxTouchPoints > 0 || 'ontouchstart' in window;
  const mobile = navigator.userAgentData?.mobile ?? /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);
  const inputs = {
    keyboard: finePointer || !mobile, pointer: finePointer || media('(any-pointer: coarse)'), 'fine-pointer': finePointer, touch,
    'pointer-lock': 'pointerLockElement' in document, gamepad: typeof navigator.getGamepads === 'function',
  };
  const missing = parse('appInputs').filter(name => !inputs[name]), platforms = parse('appPlatforms');
  const platformMatch = !platforms.length || (platforms.includes('touch') && touch) || (platforms.includes('desktop') && (inputs.keyboard || finePointer));
  const compatibility = $('[data-app-compatibility]'), warning = $('[data-app-launch-warning]');
  if (!missing.length && platformMatch) {
    compatibility.firstElementChild.textContent = 'COMPATIBILITY / SUPPORTED ON THIS DEVICE';
    run();
  } else {
    compatibility.classList.add('warning'); compatibility.firstElementChild.textContent = 'COMPATIBILITY / REQUIREMENTS NOT MET';
    const reasons = [];
    if (!platformMatch) reasons.push('this device does not match the declared desktop/touch platforms');
    if (missing.length) reasons.push(`missing input capabilities: ${missing.join(', ')}`);
    warning.querySelector('[data-app-launch-message]').textContent = `This application may not work correctly: ${reasons.join('; ')}.`;
    warning.hidden = false; setStatus('WAITING');
    warning.querySelector('[data-app-launch-anyway]').addEventListener('click', () => { warning.hidden = true; run(); }, { once: true });
  }
}
