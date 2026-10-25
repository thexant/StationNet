import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const hostSource = readFileSync(new URL("../stationnet/static/application_host.js", import.meta.url), "utf8");
const runtimeSource = readFileSync(new URL("../stationnet/static/application_runtime.js", import.meta.url), "utf8");
const protocol = "stationnet-app-v1";

class Element {
  constructor() {
    this.dataset = {}; this.listeners = new Map(); this.children = []; this.hidden = false;
    this.classList = { values: new Set(), toggle: (name, value) => value ? this.classList.values.add(name) : this.classList.values.delete(name),
      contains: name => this.classList.values.has(name) };
  }
  addEventListener(type, callback) { this.listeners.set(type, callback); }
  click() { this.listeners.get("click")?.(); }
  append(...nodes) { this.children.push(...nodes); }
  remove() { this.removed = true; }
  focus() {}
  setAttribute(name, value) { this[name] = value; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
}

function makeHost({ touch = false, supported = true } = {}) {
  const sent = [], windowListeners = new Map(), documentListeners = new Map();
  const frame = new Element(); frame.dataset.runtimeSrc = "about:blank";
  frame.contentWindow = { postMessage(message) { sent.push(message); } };
  const surface = new Element(); surface.clientWidth = touch ? 390 : 1280; surface.clientHeight = touch ? 844 : 800;
  const display = new Element(); display.dataset.appPlatforms = '["desktop","touch"]'; display.dataset.appInputs = "[]";
  const status = new Element(), fullscreenButton = new Element(), fullscreenExit = new Element();
  const immersiveButton = new Element(), immersiveExit = new Element();
  const body = new Element(); const promptNodes = [];
  display.insertBefore = node => promptNodes.push(node);
  const document = {
    body, fullscreenEnabled: supported, fullscreenElement: null, hidden: false,
    hasFocus: () => true, addEventListener(type, callback) { documentListeners.set(type, callback); },
    createElement() { return new Element(); },
    querySelector(selector) {
      return ({ "[data-app-runtime]": frame, "[data-app-surface]": surface,
        "[data-app-status]": status, ".application-display[data-app-platforms]": display,
        "[data-app-fullscreen]": fullscreenButton, "[data-app-fullscreen-exit]": fullscreenExit,
        "[data-app-immersive]": immersiveButton, "[data-app-immersive-exit]": immersiveExit,
        "[data-app-fullscreen-prompt]": promptNodes.find(node => !node.removed) })[selector] || null;
    },
    async exitFullscreen() { this.fullscreenElement = null; documentListeners.get("fullscreenchange")(); }
  };
  let calls = 0;
  display.requestFullscreen = async () => {
    calls++;
    document.fullscreenElement = display;
    documentListeners.get("fullscreenchange")();
  };
  const viewport = { innerWidth: touch ? 390 : 1280, innerHeight: touch ? 844 : 800 };
  const host = { document, window: viewport, HTMLElement: Element,
    navigator: { maxTouchPoints: touch ? 5 : 0, userAgent: "test" },
    crypto: { randomUUID: () => "test-id" }, atob, Uint8Array,
    setTimeout: (callback, delay) => { if (delay === 0) queueMicrotask(callback); return 1; }, clearTimeout() {},
    getComputedStyle: () => ({ paddingLeft: "16px", paddingRight: "16px", paddingTop: "16px", paddingBottom: "16px" }),
    scrollX: 0, scrollY: 0, scrollTo() {},
    addEventListener(type, callback) { windowListeners.set(type, callback); } };
  vm.runInNewContext(hostSource, host);
  return { sent, frame, surface, display, document, body, viewport, promptNodes, fullscreenButton, fullscreenExit,
    get calls() { return calls; },
    send(data) { windowListeners.get("message")({ source: frame.contentWindow, data: { protocol, ...data } }); },
    resize(width, height) { viewport.innerWidth = width; viewport.innerHeight = height; surface.clientWidth = width; surface.clientHeight = height; windowListeners.get("resize")(); },
    terminate() { windowListeners.get("pagehide")(); } };
}

const desktop = makeHost();
desktop.send({ type: "capability", operation: "presentation-enter-immersive", request: "immersive" });
assert.equal(desktop.body.classList.contains("application-immersive"), true);
desktop.fullscreenButton.click();
await Promise.resolve();
assert.equal(desktop.calls, 1);
assert.equal(desktop.document.fullscreenElement, desktop.display);
assert.equal(desktop.fullscreenExit.hidden, false);
assert.equal(desktop.body.classList.contains("application-immersive"), true);
assert.equal(desktop.sent.findLast(item => item.type === "presentation-event").fullscreen, true);
desktop.send({ type: "capability", operation: "viewport", request: "viewport-1" });
assert.equal(desktop.sent.at(-1).value.fullscreen, true);
assert.equal(desktop.sent.at(-1).value.width, 1248);
desktop.fullscreenExit.click();
await Promise.resolve();
assert.equal(desktop.document.fullscreenElement, null);
assert.equal(desktop.body.classList.contains("application-immersive"), true);
assert.equal(desktop.fullscreenButton["aria-pressed"], "false");

const mobile = makeHost({ touch: true });
mobile.send({ type: "capability", operation: "presentation-request-fullscreen", request: "request-1" });
assert.equal(mobile.calls, 0, "an app message cannot spend browser user activation");
assert.equal(mobile.promptNodes.length, 1);
mobile.promptNodes[0].children[1].click();
await Promise.resolve();
assert.equal(mobile.calls, 1);
assert.equal(mobile.sent.find(item => item.request === "request-1")?.ok, true);
mobile.resize(844, 390);
await Promise.resolve();
mobile.send({ type: "capability", operation: "viewport", request: "viewport-2" });
assert.equal(mobile.sent.at(-1).value.orientation, "landscape");
assert.equal(mobile.sent.at(-1).value.height, 358);
assert.equal(mobile.sent.findLast(item => item.type === "presentation-event").viewport.orientation, "landscape");
mobile.send({ type: "capability", operation: "presentation-exit-fullscreen", request: "exit-1" });
await new Promise(setImmediate);
assert.equal(mobile.document.fullscreenElement, null);
assert.equal(mobile.sent.find(item => item.request === "exit-1")?.ok, true,
  JSON.stringify(mobile.sent.find(item => item.request === "exit-1")));
mobile.fullscreenButton.click();
await Promise.resolve();
mobile.terminate();
assert.equal(mobile.document.fullscreenElement, null);
assert.equal(mobile.body.classList.contains("application-immersive"), false);

const unsupported = makeHost({ supported: false });
assert.equal(unsupported.fullscreenButton.disabled, true);
unsupported.send({ type: "capability", operation: "presentation-request-fullscreen", request: "unsupported" });
assert.equal(unsupported.sent.at(-1).ok, false);
const cancelled = makeHost();
cancelled.send({ type: "capability", operation: "presentation-request-fullscreen", request: "cancelled" });
cancelled.promptNodes[0].children[2].click();
assert.equal(cancelled.calls, 0);
assert.equal(cancelled.sent.at(-1).ok, false);

const messages = [], listeners = new Map();
const worker = { __stationnetPackageFiles: {}, postMessage(message) { messages.push(message); },
  addEventListener(type, callback) { listeners.set(type, callback); }, atob, TextDecoder, Uint8Array, setTimeout, clearTimeout };
worker.self = worker;
vm.runInNewContext(runtimeSource, worker);
const ui = worker.StationNet;
assert.equal(ui.presentation.fullscreen, false);
const changes = [];
ui.presentation.onChange((mode, viewport) => changes.push([mode, viewport]));
const request = ui.presentation.requestFullscreen();
assert.equal(messages.at(-1).operation, "presentation-request-fullscreen");
listeners.get("message")({ data: { protocol, type: "presentation-event", mode: "immersive", fullscreen: true,
  viewport: { width: 390, height: 700, orientation: "portrait" } } });
await Promise.resolve();
assert.equal(ui.presentation.fullscreen, true);
assert.equal(changes.at(-1)[0], "immersive");
assert.equal(changes.at(-1)[1].width, 390);
listeners.get("message")({ data: { protocol, type: "capability-result", request: messages.at(-1).request, ok: true, value: true } });
await request;
const exit = ui.presentation.exitFullscreen();
assert.equal(messages.at(-1).operation, "presentation-exit-fullscreen");
listeners.get("message")({ data: { protocol, type: "capability-result", request: messages.at(-1).request, ok: true, value: true } });
await exit;
console.log("application fullscreen host and worker checks passed");
