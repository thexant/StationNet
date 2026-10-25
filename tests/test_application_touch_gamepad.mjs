import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const protocol = "stationnet-app-v1";
const runtimeSource = readFileSync(new URL("../stationnet/static/application_runtime.js", import.meta.url), "utf8");
const hostSource = readFileSync(new URL("../stationnet/static/application_host.js", import.meta.url), "utf8");

const runtimeMessages = [], workerListeners = new Map();
const worker = { __stationnetPackageFiles: {}, postMessage(message) { runtimeMessages.push(message); },
  addEventListener(type, callback) { workerListeners.set(type, callback); }, atob, TextDecoder, Uint8Array, setTimeout, clearTimeout };
worker.self = worker;
vm.runInNewContext(runtimeSource, worker);
const canvas = worker.StationNet.canvas(320, 200, [], { touchLayout: "gamepad",
  controls: Array.from({ length: 6 }, (_, index) => ({ label: `Action ${index}`, code: "KeyA" })) });
assert.equal(canvas.touchLayout, "gamepad");
assert.equal(canvas.controls.length, 4);
assert.equal(canvas.controls[0].code, "KeyA");
assert.equal(worker.StationNet.canvas(320, 200, []).touchLayout, undefined);
assert.equal(worker.StationNet.canvas(320, 200, [], { touchLayout: "unknown" }).touchLayout, undefined);

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.listeners = new Map();
    this.className = ""; this.style = { setProperty() {}, getPropertyValue() { return "1.6"; } };
    this.classList = { contains: name => this.className.split(" ").includes(name),
      add: name => { this.className += ` ${name}`; } };
  }
  get childElementCount() { return this.children.length; }
  append(...nodes) {
    for (const node of nodes) {
      if (node.tagName === "FRAGMENT") this.append(...node.children);
      else { node.parent = this; this.children.push(node); }
    }
  }
  replaceChildren(...nodes) { for (const child of this.children) child.parent = null; this.children = []; this.append(...nodes); }
  remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
  replaceWith(node) { if (!this.parent) return; const index = this.parent.children.indexOf(this); this.parent.children[index] = node; node.parent = this.parent; this.parent = null; }
  querySelectorAll(selector) {
    const result = [];
    const match = node => selector === "canvas" ? node.tagName === "CANVAS" :
      selector === "button" ? node.tagName === "BUTTON" :
      selector === "[data-app-component]" ? node.dataset.appComponent != null :
      selector.startsWith(".") ? node.classList.contains(selector.slice(1)) : false;
    const visit = node => { for (const child of node.children) { if (match(child)) result.push(child); visit(child); } };
    visit(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, callback) { const list = this.listeners.get(type) || []; list.push(callback); this.listeners.set(type, list); }
  fire(type, details = {}) {
    const event = { button: 0, pointerId: 1, key: "", code: "", repeat: false,
      preventDefault() {}, stopPropagation() {}, ...details };
    for (const callback of this.listeners.get(type) || []) callback(event);
  }
  setAttribute() {}
  setPointerCapture() {}
  getBoundingClientRect() { return { left: 0, top: 0, width: 320, height: 200 }; }
  contains(node) { return this === node || this.children.some(child => child.contains(node)); }
  getContext() { return { clearRect() {}, fillRect() {}, save() {}, beginPath() {}, rect() {}, clip() {}, restore() {} }; }
}

function makeHost(touch) {
  const listeners = new Map(), sent = [], timers = new Map(); let nextTimer = 1;
  const frame = { dataset: {}, contentWindow: { postMessage(message) { sent.push(message); } }, remove() {} };
  const surface = new Element("div"), status = {};
  const document = { hidden: false, hasFocus: () => true, addEventListener() {},
    createElement: tag => new Element(tag), createDocumentFragment: () => new Element("fragment"),
    querySelector(selector) { return selector === "[data-app-runtime]" ? frame :
      selector === "[data-app-surface]" ? surface : selector === "[data-app-status]" ? status : null; } };
  const viewportWindow = { innerWidth: 390, innerHeight: 844 };
  surface.clientWidth = 390;
  const host = { document, window: viewportWindow, navigator: { maxTouchPoints: touch ? 5 : 0, userAgent: "test" },
    crypto: { randomUUID: () => "test-id" }, atob, Uint8Array,
    setTimeout(callback, delay) { const id = nextTimer++; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); }, addEventListener(type, callback) { listeners.set(type, callback); } };
  vm.runInNewContext(hostSource, host);
  return { surface, sent, listeners, document, viewportWindow,
    advance(delay) { for (const [id, timer] of [...timers]) if (timer.delay <= delay) { timers.delete(id); timer.callback(); } },
    send(data) { listeners.get("message")({ source: frame.contentWindow, data: { protocol, ...data } }); } };
}

const touch = makeHost(true);
touch.send({ type: "keyboard", action: "key-handler", enabled: true });
touch.send({ type: "render", mode: "replace", components: [{ type: "canvas", id: "game", width: 320, height: 200,
  commands: [], touchLayout: "gamepad", controls: [
    { label: "A", code: "KeyQ" }, { label: "B", code: "invalid-code" },
    { label: "C" }, { label: "D" }, { label: "ignored" }
  ] }] });
const wrapper = touch.surface.querySelector(".app-canvas-wrap");
const overlay = wrapper.querySelector(".app-canvas-controls");
assert.ok(overlay.classList.contains("app-canvas-gamepad"));
assert.equal(overlay.querySelectorAll("button").length, 10);
const up = overlay.querySelector(".app-gamepad-up"), action = overlay.querySelector(".app-gamepad-action-1");
up.fire("pointerdown", { pointerId: 1 });
action.fire("pointerdown", { pointerId: 2 });
assert.deepEqual(touch.sent.filter(item => item.type === "input-key").map(({ code, down }) => [code, down]),
  [["ArrowUp", true], ["KeyQ", true]]);
assert.deepEqual(touch.sent.filter(item => item.type === "interaction").map(({ code, phase }) => [code, phase]),
  [["ArrowUp", "down"], ["KeyQ", "down"]]);
up.fire("pointerup", { pointerId: 1 });
assert.deepEqual(touch.sent.filter(item => item.type === "input-key").at(-1).code, "ArrowUp");
assert.equal(touch.sent.filter(item => item.type === "input-key").at(-1).down, false);
const fallback = overlay.querySelector(".app-gamepad-action-2");
fallback.fire("pointerdown", { pointerId: 3 });
assert.equal(touch.sent.filter(item => item.type === "input-key").at(-1).code, "KeyX");
fallback.fire("pointercancel", { pointerId: 3 });
assert.equal(touch.sent.filter(item => item.type === "input-key").at(-1).down, false);
overlay.querySelector(".app-gamepad-start").fire("pointerdown", { pointerId: 4 });
assert.equal(touch.sent.filter(item => item.type === "input-key").at(-1).code, "Enter");
overlay.querySelector(".app-gamepad-start").fire("lostpointercapture", { pointerId: 4 });
overlay.querySelector(".app-gamepad-select").fire("pointerdown", { pointerId: 5 });
assert.equal(touch.sent.filter(item => item.type === "input-key").at(-1).code, "ShiftRight");
overlay.querySelector(".app-gamepad-select").fire("pointerup", { pointerId: 5 });
touch.send({ type: "render", mode: "update", target: "game", components: [
  { type: "canvas", width: 320, height: 200, commands: [], touchLayout: "gamepad" }] });
assert.equal(wrapper.querySelector(".app-canvas-controls"), overlay);
touch.send({ type: "render", mode: "remove", target: "game", components: [] });
assert.equal(touch.surface.querySelector(".app-canvas-wrap"), null);
assert.deepEqual(touch.sent.filter(item => item.type === "input-key").at(-1).code, "KeyQ");
assert.equal(touch.sent.filter(item => item.type === "input-key").at(-1).down, false);
touch.send({ type: "render", mode: "replace", components: [{ type: "canvas", id: "game", width: 320,
  height: 200, commands: [], touchLayout: "gamepad" }] });
const freshUp = touch.surface.querySelector(".app-gamepad-up");
freshUp.fire("pointerdown", { pointerId: 7 });
touch.document.hidden = true;
touch.listeners.get("blur")();
assert.deepEqual(touch.sent.filter(item => item.type === "input-key").at(-1).code, "ArrowUp");
assert.equal(touch.sent.filter(item => item.type === "input-key").at(-1).down, false);

const desktop = makeHost(false);
desktop.send({ type: "render", mode: "replace", components: [{ type: "canvas", width: 320, height: 200,
  commands: [], touchLayout: "gamepad", controls: [{ label: "A" }] }] });
assert.equal(desktop.surface.querySelector(".app-canvas-controls"), null);
desktop.send({ type: "render", mode: "replace", components: [{ type: "canvas", width: 320, height: 200,
  commands: [], controls: [{ label: "Play", action: "play" }] }] });
assert.equal(desktop.surface.querySelector(".app-canvas-controls").className, "app-canvas-controls");
assert.equal(desktop.surface.querySelectorAll("button").length, 1);
desktop.send({ type: "render", mode: "replace", components: [{ type: "canvas", width: 320,
  height: 200, commands: [], action: "mouse", hover: true }] });
const mouse = desktop.surface.querySelector("canvas");
mouse.fire("pointermove", { pointerId: 9, pointerType: "mouse", clientX: 12, clientY: 16, buttons: 0 });
mouse.fire("pointerdown", { pointerId: 9, pointerType: "mouse", clientX: 12, clientY: 16 });
mouse.fire("pointerup", { pointerId: 9, pointerType: "mouse", clientX: 12, clientY: 16 });
assert.deepEqual(desktop.sent.filter(item => item.action === "mouse").map(item => item.phase),
  ["move", "start", "end", "gesture"]);

const capped = makeHost(true);
capped.send({ type: "render", mode: "replace", components: Array.from({ length: 17 }, () =>
  ({ type: "canvas", width: 320, height: 200, commands: [], touchLayout: "gamepad" })) });
assert.equal(capped.surface.querySelectorAll(".app-canvas-gamepad").length, 16);

const mixed = makeHost(true);
mixed.send({ type: "render", mode: "replace", components: [{ type: "canvas", width: 320, height: 200,
  commands: [], touchLayout: "gamepad", controls: [{ label: "A", code: "KeyA" }] }] });
const mixedButton = mixed.surface.querySelector(".app-gamepad-action-1");
mixedButton.fire("pointerdown", { pointerId: 11 });
const physical = type => mixed.listeners.get(type)({ key: "a", code: "KeyA", repeat: false,
  target: { tagName: "BODY" }, preventDefault() {}, ctrlKey: false, altKey: false,
  shiftKey: false, metaKey: false });
physical("keydown");
mixedButton.fire("pointerup", { pointerId: 11 });
assert.deepEqual(mixed.sent.filter(item => item.type === "input-key").map(({ down }) => down), [true]);
physical("keyup");
assert.deepEqual(mixed.sent.filter(item => item.type === "input-key").map(({ down }) => down), [true, false]);

const gestures = makeHost(true);
gestures.send({ type: "render", mode: "replace", components: [{ type: "canvas", id: "field",
  width: 320, height: 200, commands: [], action: "pointer-action" }] });
const field = gestures.surface.querySelector("canvas");
const pointer = (type, pointerId, x, y) => field.fire(type, { pointerId, pointerType: "touch", clientX: x, clientY: y });
pointer("pointerdown", 21, 10, 10);
pointer("pointerdown", 22, 30, 10);
pointer("pointermove", 22, 50, 10);
pointer("pointerup", 21, 10, 10);
pointer("pointercancel", 22, 50, 10);
const events = gestures.sent.filter(item => item.action === "pointer-action");
assert.equal(events.find(item => item.phase === "start").pointerId, 21);
assert.equal(events.find(item => item.gesture === "pinch" && item.scale > 1).scale, 2);
assert.ok(events.some(item => item.phase === "cancel" && item.pointerId === 22));
pointer("pointerdown", 23, 5, 5); pointer("pointerup", 23, 5, 5);
assert.ok(gestures.sent.some(item => item.gesture === "tap"));
pointer("pointerdown", 24, 5, 5); gestures.advance(500); pointer("pointerup", 24, 5, 5);
assert.ok(gestures.sent.some(item => item.gesture === "longpress"));
pointer("pointerdown", 25, 5, 5); pointer("pointermove", 25, 100, 5); pointer("pointerup", 25, 100, 5);
assert.ok(gestures.sent.some(item => item.gesture === "drag" && item.pointerId === 25));
assert.ok(gestures.sent.some(item => item.gesture === "swipe" && item.direction === "right"));
pointer("pointerdown", 26, 5, 5); gestures.document.hidden = true; gestures.listeners.get("blur")();
assert.ok(gestures.sent.some(item => item.phase === "cancel" && item.pointerId === 26));

const analogConfig = worker.StationNet.canvas(320, 200, [], { id: "game", touchLayout: {
  type: "gamepad", dpad: false, joystick: { name: "move", size: 96, x: 20, y: 50 },
  steering: { name: "steer", size: 90 }
}, controls: [{ label: "Fire", code: "KeyZ", x: 80, y: 35, size: 60 }] });
assert.equal(analogConfig.touchLayout.joystick.size, 96);
assert.equal(analogConfig.controls[0].x, 80);
const analog = makeHost(true);
analog.send({ type: "render", mode: "replace", components: [analogConfig] });
const stick = analog.surface.querySelector(".app-gamepad-joystick");
const steering = analog.surface.querySelector(".app-gamepad-steering");
assert.ok(stick && steering);
stick.fire("pointerdown", { pointerId: 41, clientX: 250, clientY: 100 });
steering.fire("pointerdown", { pointerId: 42, clientX: 0, clientY: 100 });
analog.send({ type: "render", mode: "update", target: "game", components: [
  { type: "canvas", width: 320, height: 200, commands: [] }] });
assert.equal(analog.surface.querySelector(".app-gamepad-joystick"), stick);
assert.ok(analog.sent.some(item => item.type === "input-axis" && item.name === "move" && item.x > 0));
assert.ok(analog.sent.some(item => item.type === "input-axis" && item.name === "steer" && item.x < 0));
assert.ok(analog.sent.some(item => item.type === "input-key" && item.code === "ArrowRight" && item.down));
assert.ok(analog.sent.some(item => item.type === "input-key" && item.code === "ArrowLeft" && item.down));
analog.document.hidden = true; analog.listeners.get("blur")();
assert.equal(analog.sent.filter(item => item.type === "input-axis" && item.name === "move").at(-1).x, 0);
assert.equal(analog.sent.filter(item => item.type === "input-key" && item.down === false).length, 2);
analog.send({ type: "capability", operation: "viewport", request: "portrait" });
assert.equal(analog.sent.at(-1).value.orientation, "portrait");
analog.viewportWindow.innerWidth = 844; analog.viewportWindow.innerHeight = 390;
analog.surface.clientWidth = 844;
analog.send({ type: "capability", operation: "viewport", request: "landscape" });
assert.equal(analog.sent.at(-1).value.orientation, "landscape");
assert.equal(analog.sent.at(-1).value.width, 812);

workerListeners.get("message")({ data: { protocol, type: "input-axis", name: "move", x: 0.7, y: -0.3 } });
assert.equal(worker.StationNet.input.axis("move").x, 0.7);
workerListeners.get("message")({ data: { protocol, type: "input-key", code: "ArrowRight", down: true } });
assert.equal(worker.StationNet.input.isDown("ArrowRight"), true);

console.log("application touch gamepad checks passed");
