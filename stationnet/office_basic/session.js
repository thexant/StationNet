// One BASIC program run, shared by the Office BASIC editor and installed BASIC
// applications. Programs run in a sandboxed, opaque-origin frame. Each run
// gets a fresh frame; stopping discards it, which ends timers, audio, and
// runaway code.
import { PROTOCOL, readRunnerMessage } from './model.js';

export const RUN_LABELS = Object.freeze({
  starting: 'STARTING…', compiling: 'COMPILING…', running: 'RUNNING', ended: 'FINISHED', error: 'ERROR',
  stalled: 'NOT RESPONDING — PRESS STOP', idle: 'STOPPED',
});

// onState(state, label) follows the run; onMessage(data) receives the runner's
// other messages (warnings, error, echo, file, storage) after validation.
export function createRunnerSession({ container, runnerUrl, title = 'BASIC program screen', onState = () => {}, onLog = () => {}, onMessage = () => {} }) {
  let frame = null, runId = null, pending = null, beatAt = 0, watchdog = null, state = 'idle';

  function setState(next, label = RUN_LABELS[next]) { state = next; onState(next, label); }
  function destroyFrame() { clearInterval(watchdog); watchdog = null; pending = null; runId = null; frame?.remove(); frame = null; }
  function mountFrame() {
    frame?.remove(); beatAt = Date.now();
    frame = document.createElement('iframe');
    frame.setAttribute('sandbox', 'allow-scripts');
    frame.setAttribute('allow', 'autoplay; fullscreen; gamepad');
    frame.setAttribute('referrerpolicy', 'no-referrer');
    frame.title = title;
    frame.src = runnerUrl;
    container.append(frame);
  }
  // extras: { files: {path: ArrayBuffer}, storage: {key: string} } for installed programs.
  function run(source, extras = {}) {
    destroyFrame();
    runId = crypto.randomUUID(); pending = { source, ...extras };
    let attempts = 0;
    mountFrame();
    setState('starting');
    watchdog = setInterval(() => {
      const quiet = Date.now() - beatAt;
      if (state === 'starting' && quiet > 4000) {
        // A stopped runaway program can keep the browser's sandbox process busy
        // for a few seconds; a fresh frame starts once the browser reclaims it.
        if (attempts++ < 5) { if (attempts === 1) onLog('info', 'Waiting for the program sandbox…'); mountFrame(); }
        else { onLog('error', 'The program sandbox did not start. Press RUN to try again, or reload the page.'); stop('ERROR', 'error'); }
      } else if ((state === 'running' || state === 'compiling') && quiet > 5000) setState('stalled');
    }, 1000);
  }
  function stop(label = RUN_LABELS.idle, next = 'idle') {
    if (!frame) return false;
    try { frame.contentWindow?.postMessage({ protocol: PROTOCOL, type: 'stop', runId }, '*'); } catch { /* The frame is discarded below. */ }
    destroyFrame();
    setState(next, label);
    return true;
  }
  function focus() {
    if (!frame) return;
    frame.focus(); frame.contentWindow?.postMessage({ protocol: PROTOCOL, type: 'focus', runId }, '*');
  }
  function receive(event) {
    if (!frame || event.source !== frame.contentWindow || event.origin !== 'null') return;
    const data = readRunnerMessage(event.data, runId);
    if (!data) return;
    beatAt = Date.now();
    if (data.type === 'ready') {
      if (pending === null) return;
      // Files are cloned, not transferred, so the host can run the program again.
      const { source, files, storage } = pending, message = { protocol: PROTOCOL, type: 'run', runId, source };
      if (files) message.files = files;
      if (storage) message.storage = storage;
      frame.contentWindow.postMessage(message, '*'); pending = null;
    } else if (data.type === 'heartbeat') { if (state === 'stalled') setState('running'); }
    else if (data.type === 'state') { setState(data.state); if (data.state === 'running') focus(); }
    else onMessage(data);
  }
  window.addEventListener('message', receive);
  return { run, stop, focus, get active() { return Boolean(frame); }, get state() { return state; } };
}
