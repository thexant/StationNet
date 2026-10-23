import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_node(script, *paths):
    subprocess.run(
        ["node", "-e", script, *(str(path) for path in paths)],
        check=True, capture_output=True, text=True,
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_animation_api_delta_pause_resume_cleanup_and_backwards_compatible_timers():
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

(async () => {
  const sent = [], listeners = {}, intervals = [], timeouts = [];
  const self = { __stationnetPackageFiles: {} };
  const context = {
    self, postMessage: message => sent.push(message),
    addEventListener: (type, handler) => { listeners[type] = handler; },
    setInterval: (handler, delay) => (intervals.push({ handler, delay, cleared: false }), intervals.length - 1),
    clearInterval: id => { intervals[id].cleared = true; },
    setTimeout: (handler, delay) => (timeouts.push({ handler, delay, cleared: false }), timeouts.length - 1),
    clearTimeout: id => { timeouts[id].cleared = true; },
    TextDecoder, Uint8Array, atob, URL, Blob, Promise
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
  const ui = self.StationNet;

  let timerCalls = 0;
  const stopInterval = ui.interval(() => { timerCalls += 1; }, 1);
  const stopTimeout = ui.timeout(() => { timerCalls += 1; }, 7);
  assert.equal(intervals[0].delay, 10);
  assert.equal(timeouts[0].delay, 7);
  intervals[0].handler(); timeouts[0].handler();
  await Promise.resolve();
  assert.equal(timerCalls, 2);

  const frames = [];
  let finish;
  const animation = ui.animate(frame => {
    frames.push(frame);
    return new Promise(resolve => { finish = resolve; });
  });
  const registration = sent.find(message => message.type === 'animation' && message.enabled);
  assert.ok(registration.action.startsWith('animation-'));
  listeners.message({ data: { protocol: 'stationnet-app-v1', type: 'animation-frame', action: registration.action, time: 100, delta: .016 } });
  await Promise.resolve();
  assert.equal(frames[0].time, 100);
  assert.equal(frames[0].delta, .016);
  assert.equal(sent.filter(message => message.type === 'animation-ready').length, 0);
  finish(); await Promise.resolve(); await Promise.resolve();
  assert.equal(sent.filter(message => message.type === 'animation-ready').length, 1);

  animation.pause();
  assert.equal(animation.paused, true);
  assert.equal(sent.at(-1).enabled, false);
  animation.resume();
  assert.equal(animation.paused, false);
  assert.equal(sent.at(-1).enabled, true);
  animation();
  assert.equal(sent.at(-1).enabled, false);

  stopInterval(); stopTimeout();
  assert.ok(intervals[0].cleared);
  listeners.message({ data: { protocol: 'stationnet-app-v1', type: 'terminate' } });
  assert.equal(sent.at(-1).type, 'terminated');
  assert.doesNotThrow(() => animation.resume());
})();
"""
    run_node(script, ROOT / "stationnet" / "static" / "application_runtime.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_frame_scheduler_backpressure_coalescing_visibility_and_termination():
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const listeners = {}, documentListeners = {}, parentMessages = [], raf = new Map();
let nextRaf = 1, worker;
class FakeWorker {
  constructor() { this.listeners = {}; this.posts = []; this.terminated = false; worker = this; }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  postMessage(message) { this.posts.push(message); }
  emit(message) { this.listeners.message({ data: message }); }
  terminate() { this.terminated = true; }
}
const elements = {
  'stationnet-worker-source': { textContent: Buffer.from('// runtime').toString('base64') },
  'stationnet-application-source': { textContent: Buffer.from('// app').toString('base64') },
  'stationnet-package-files': { textContent: '{}' }
};
const parent = { postMessage: message => parentMessages.push(message) };
const context = {
  Worker: FakeWorker, parent, self: {}, TextDecoder, Uint8Array, atob, Blob,
  URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
  document: { hidden: false, getElementById: id => elements[id], addEventListener: (type, handler) => { documentListeners[type] = handler; } },
  addEventListener: (type, handler) => { listeners[type] = handler; },
  requestAnimationFrame: handler => { const id = nextRaf++; raf.set(id, handler); return id; },
  cancelAnimationFrame: id => raf.delete(id), setTimeout: handler => handler()
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const protocol = 'stationnet-app-v1';
const emit = message => worker.emit({ protocol, ...message });
const runFrame = time => { const [id, callback] = raf.entries().next().value; raf.delete(id); callback(time); };

emit({ type: 'animation', action: 'animation-1', enabled: true });
assert.equal(raf.size, 1);
runFrame(10);
assert.equal(worker.posts.at(-1).protocol, protocol);
assert.equal(worker.posts.at(-1).type, 'animation-frame');
assert.equal(worker.posts.at(-1).action, 'animation-1');
assert.equal(worker.posts.at(-1).time, 10);
assert.equal(worker.posts.at(-1).delta, 0);
assert.equal(raf.size, 0, 'no frames queue while the callback is pending');

emit({ type: 'render', mode: 'update', target: 'field', components: [{ type: 'canvas', commands: [1] }] });
emit({ type: 'render', mode: 'update', target: 'field', components: [{ type: 'canvas', commands: [2] }] });
assert.equal(parentMessages.length, 0);
emit({ type: 'animation-ready', action: 'animation-1' });
assert.equal(raf.size, 1);
const frameMessages = worker.posts.length;
runFrame(20);
assert.equal(worker.posts.length, frameMessages, 'high-refresh displays are capped near 60 callbacks per second');
assert.equal(raf.size, 1);
runFrame(27);
assert.equal(parentMessages.length, 1);
assert.equal(parentMessages[0].components[0].commands.length, 1);
assert.equal(parentMessages[0].components[0].commands[0], 2);
assert.ok(Math.abs(worker.posts.at(-1).delta - .017) < .000001);

listeners.message({ source: parent, data: { protocol, type: 'animation-visibility', visible: false } });
assert.equal(raf.size, 0);
emit({ type: 'animation-ready', action: 'animation-1' });
assert.equal(raf.size, 0);
listeners.message({ source: parent, data: { protocol, type: 'animation-visibility', visible: true } });
assert.equal(raf.size, 1);
runFrame(2000);
assert.equal(worker.posts.at(-1).delta, 0, 'resume does not include hidden time');

listeners.pagehide();
assert.equal(worker.terminated, true);
assert.equal(raf.size, 0);
"""
    run_node(script, ROOT / "stationnet" / "static" / "application_frame.js")
