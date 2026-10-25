import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const protocol = "stationnet-app-v1";
const runtimeSource = readFileSync(new URL("../stationnet/static/application_runtime.js", import.meta.url), "utf8");
const hostSource = readFileSync(new URL("../stationnet/static/application_host.js", import.meta.url), "utf8");
const outbound = [], workerListeners = new Map();
const worker = { __stationnetPackageFiles: {}, postMessage(message) { outbound.push(message); },
  addEventListener(type, callback) { workerListeners.set(type, callback); }, setTimeout, clearTimeout, atob, Uint8Array };
worker.self = worker;
vm.runInNewContext(runtimeSource, worker);
const ui = worker.StationNet;
const received = [];
const tabs = ui.tabs(["<b>File</b>", "Edit"], ({ value }) => received.push(value), { id: "tabs", selected: 1 });
const radio = ui.radio("mode", "Mode", [{ label: "Safe", value: "safe" }, { label: "Fast", value: "fast" }], { id: "radio", value: "fast" });
const slider = ui.slider("volume", "Volume", { id: "slider", min: 0, max: 10, step: .5, value: 4 });
const menu = ui.menuBar([{ label: "File", items: [{ label: "Open", value: "open" }] }], ({ value }) => received.push(value), { id: "menu" });
const dialog = ui.dialog("Confirm", [ui.text("<script>plain text</script>")], [{ label: "OK", value: "ok" }], { id: "confirm" });
assert.equal(structuredClone(dialog).type, "dialog");
ui.render(ui.row([tabs, ui.column([radio, slider], { id: "column" })], { id: "row" }), menu,
  ui.statusBar("Ready", { id: "status" }), dialog);
assert.equal(outbound.at(-1).components[0].type, "row");
ui.update("status", ui.statusBar("Busy", { id: "status" }));
ui.remove("status");
assert.equal(outbound.at(-1).mode, "remove");

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.parent = null; this.dataset = {};
    this.listeners = new Map(); this.attributes = {}; this.className = ""; this.textContent = "";
    this.style = { setProperty() {} }; this.classList = { contains: name => this.className.split(" ").includes(name),
      add: name => { this.className += ` ${name}`; } };
  }
  append(...nodes) { for (const node of nodes) {
    if (node.tagName === "FRAGMENT") this.append(...node.children);
    else { node.parent = this; this.children.push(node); }
  } }
  replaceChildren(...nodes) { this.children.forEach(child => { child.parent = null; }); this.children = []; this.append(...nodes); }
  replaceWith(node) { const index = this.parent.children.indexOf(this); this.parent.children[index] = node; node.parent = this.parent; this.parent = null; }
  remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
  querySelectorAll(selector) {
    const matches = node => selector === "[data-app-component]" ? node.dataset.appComponent != null :
      selector === "[data-app-input]" ? Object.hasOwn(node.dataset, "appInput") :
      selector === "dialog.app-dialog" ? node.tagName === "DIALOG" && node.classList.contains("app-dialog") :
      selector.startsWith(".") ? node.classList.contains(selector.slice(1)) : node.tagName === selector.toUpperCase();
    const result = []; const visit = node => { for (const child of node.children) { if (matches(child)) result.push(child); visit(child); } };
    visit(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  matches(selector) { return selector === "dialog.app-dialog" && this.tagName === "DIALOG" && this.classList.contains("app-dialog"); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(type, callback) { this.listeners.set(type, [...(this.listeners.get(type) || []), callback]); }
  fire(type) { for (const callback of this.listeners.get(type) || []) callback({ preventDefault() {} }); }
  showModal() { this.open = true; }
  close() { this.open = false; }
}
const hostListeners = new Map(), sent = [];
const frame = { dataset: {}, contentWindow: { postMessage(message) { sent.push(message); } }, remove() {} };
const surface = new Element("div"), status = {};
const document = { hidden: false, hasFocus: () => true, addEventListener() {},
  createElement: tag => new Element(tag), createDocumentFragment: () => new Element("fragment"),
  querySelector(selector) { return selector === "[data-app-runtime]" ? frame :
    selector === "[data-app-surface]" ? surface : selector === "[data-app-status]" ? status : null; } };
vm.runInNewContext(hostSource, { document, window: {}, navigator: { maxTouchPoints: 0, userAgent: "test" },
  crypto: { randomUUID: () => "test-id" }, atob, Uint8Array,
  setTimeout: () => 1, clearTimeout() {}, addEventListener(type, callback) { hostListeners.set(type, callback); } });
const hostSend = data => hostListeners.get("message")({ source: frame.contentWindow, data: { protocol, ...data } });
hostSend({ type: "render", mode: "replace", components: outbound.find(message => message.mode === "replace").components });
assert.equal(surface.querySelector("dialog").open, true);
assert.equal(surface.querySelector(".app-tabs").querySelectorAll("button")[0].textContent, "<b>File</b>");
assert.equal(surface.querySelector(".app-dialog-body").querySelector("p").textContent, "<script>plain text</script>");
const radioInputs = surface.querySelector(".app-radio").querySelectorAll("input");
assert.equal(radioInputs[0].checked, false);
assert.equal(radioInputs[1].checked, true);
const range = surface.querySelector(".app-slider").querySelector("input");
assert.equal(range.type, "range");
assert.equal(range.value, "4");
surface.querySelector(".app-tabs").querySelectorAll("button")[0].fire("click");
assert.equal(sent.at(-1).value, "0");
assert.equal(sent.at(-1).values.mode, "fast");
assert.equal(sent.at(-1).values.volume, 4);
workerListeners.get("message")({ data: sent.at(-1) });
await new Promise(setImmediate);
assert.equal(received.at(-1), "0");
surface.querySelector(".app-menu-bar").querySelector("button").fire("click");
workerListeners.get("message")({ data: sent.at(-1) });
await new Promise(setImmediate);
assert.equal(received.at(-1), "open");
const chosen = dialog.result;
surface.querySelector(".app-dialog-actions").querySelector("button").fire("click");
workerListeners.get("message")({ data: sent.at(-1) });
assert.equal(await chosen, "ok");
hostSend(outbound.at(-1));
assert.equal(surface.querySelector("dialog"), null);

const direct = ui.dialog("Direct", [], ["Yes"], { id: "direct" });
const awaiting = Promise.resolve(direct);
await new Promise(setImmediate);
assert.equal(outbound.at(-1).mode, "append");
assert.equal(outbound.at(-1).components[0].id, "direct");
ui.remove("direct");
assert.equal(await awaiting, null);

for (const component of [tabs, ui.row([ui.text("row")], { id: "row-check" }),
  ui.column([ui.text("column")], { id: "column-check" }), slider, radio, dialog, menu,
  ui.statusBar("Ready", { id: "status-check" })]) {
  hostSend({ type: "render", mode: "replace", components: [component] });
  hostSend({ type: "render", mode: "update", target: component.id, components: [component] });
  assert.equal(surface.querySelectorAll("[data-app-component]").length > 0, true);
  hostSend({ type: "render", mode: "remove", target: component.id, components: [] });
  assert.equal(surface.querySelectorAll("[data-app-component]").length, 0);
}

hostSend({ type: "render", mode: "replace", components: [
  { type: "slider", name: "bounded", label: "Bounded", min: -99999999, max: Infinity, step: 0, value: Infinity },
  { type: "radio", name: "short", label: "Short", items: Array.from({ length: 100 }, (_, i) => ({ label: String(i), value: String(i) })) },
  { type: "menu-bar", menus: Array.from({ length: 12 }, () => ({ label: "Menu", items: Array.from({ length: 20 }, () => ({ label: "Item", value: "x" })) })) }
] });
const bounded = surface.querySelector(".app-slider").querySelector("input");
assert.equal(bounded.min, "-1000000");
assert.equal(bounded.max, "0");
assert.equal(bounded.step, "1");
assert.equal(surface.querySelector(".app-radio").querySelectorAll("input").length, 32);
assert.equal(surface.querySelector(".app-menu-bar").querySelectorAll("details").length, 8);

const capped = Array.from({ length: 200 }, () => ui.text("child"));
hostSend({ type: "render", mode: "replace", components: [ui.column(capped)] });
assert.ok(surface.querySelectorAll("p").length <= 100);
console.log("application UI component checks passed");
