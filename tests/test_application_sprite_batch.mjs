import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const runtimeSource = readFileSync(new URL("../stationnet/static/application_runtime.js", import.meta.url), "utf8");
const hostSource = readFileSync(new URL("../stationnet/static/application_host.js", import.meta.url), "utf8");
const protocol = "stationnet-app-v1";

const worker = {
  __stationnetPackageFiles: {}, postMessage() {}, addEventListener() {},
  atob, TextDecoder, Uint8Array, setTimeout, clearTimeout
};
worker.self = worker;
vm.runInNewContext(runtimeSource, worker);
const graphics = worker.StationNet.graphics;
const sheet = graphics.sheet("actors.png", 8, 8, { columns: 4 });
assert.equal(graphics.sprites([{ sheet, frame: 2, x: 1, y: 2 }]).type, "sprites");
assert.throws(() => graphics.sprites({}), /array/);
assert.throws(() => graphics.sprites(Array(2001)), /2000/);
const font = graphics.bitmapFont("font.png", { charWidth: 8, charHeight: 8, firstChar: 65 });
assert.deepEqual({ ...graphics.measureText("AB\nC", font) }, { width: 16, height: 16 });
assert.deepEqual({ ...graphics.measureText("A\r\nB", font) }, { width: 8, height: 16 });
assert.deepEqual({ ...graphics.measureText("", graphics.fonts.vga8x16) }, { width: 0, height: 16 });
assert.throws(() => graphics.bitmapFont("vga8x8", { charWidth: 8, charHeight: 16 }), /fixed/);

const hostListeners = new Map();
const calls = [];
let fontPixels = 0;
const canvas = {
  width: 320, height: 200, dataset: {}, style: { setProperty() {} },
  setAttribute() {},
  getContext() { return context; }
};
const context = {
  clearRect() {}, fillRect() {}, save() {}, restore() {}, beginPath() {}, rect() {}, clip() {},
  translate(...args) { calls.push(["translate", ...args]); },
  rotate(...args) { calls.push(["rotate", ...args]); },
  scale(...args) { calls.push(["scale", ...args]); },
  drawImage(...args) { calls.push(["drawImage", ...args]); }
};
const wrapper = {
  dataset: { appComponent: "game" },
  classList: { contains(name) { return name === "app-canvas-wrap"; } },
  querySelector() { return canvas; }
};
const frame = { dataset: {}, contentWindow: { postMessage() {} } };
const surface = {
  clientWidth: 320, dataset: {}, addEventListener() {},
  querySelectorAll(selector) {
    if (selector === "[data-app-component]") return [wrapper];
    if (selector === ".app-canvas") return [canvas];
    return [];
  }
};
const status = {};
const document = {
  hidden: false,
  querySelector(selector) {
    return selector === "[data-app-runtime]" ? frame :
      selector === "[data-app-surface]" ? surface :
      selector === "[data-app-status]" ? status : null;
  },
  addEventListener() {}, hasFocus() { return true; },
  createElement(tag) {
    assert.equal(tag, "canvas");
    return { width: 0, height: 0, getContext() { return { fillRect() { fontPixels++; } }; } };
  }
};
const host = {
  document, navigator: { maxTouchPoints: 0, userAgent: "test" },
  window: { innerHeight: 300 }, Blob, Uint8Array, Uint8ClampedArray, atob,
  crypto: { randomUUID: () => "test-id" },
  createImageBitmap: async () => ({ width: 32, height: 16, close() {} }),
  addEventListener(type, callback) { hostListeners.set(type, callback); },
  setTimeout() { return 1; }, clearTimeout() {}
};
vm.runInNewContext(hostSource, host);
const message = hostListeners.get("message");
const send = data => message({ source: frame.contentWindow, data: { protocol, ...data } });
send({ type: "graphics-image", path: "actors.png", data: Buffer.from("image").toString("base64") });
send({ type: "graphics-image", path: "font.png", data: Buffer.from("font").toString("base64") });
await new Promise(resolve => setImmediate(resolve));
const render = commands => send({ type: "render", mode: "update", target: "game",
  components: [{ type: "canvas", width: 320, height: 200, commands }] });

render([graphics.sprites([{ sheet, frame: 2, x: 10, y: 20, width: 16, height: 8,
  rotation: Math.PI / 2, scale: 2, flipX: true, opacity: .5 }])]);
assert.equal(calls.filter(call => call[0] === "drawImage").length, 1);
assert.deepEqual(calls.find(call => call[0] === "translate").slice(1), [18, 24]);
assert.equal(calls.find(call => call[0] === "rotate")[1], Math.PI / 2);
assert.deepEqual(calls.find(call => call[0] === "scale").slice(1), [-2, 2]);
calls.length = 0;

const valid = { sheet, frame: 0, x: 0, y: 0 };
render([{ type: "sprites", sprites: Array.from({ length: 2001 }, () => valid) }]);
assert.equal(calls.filter(call => call[0] === "drawImage").length, 2000);
calls.length = 0;
render([graphics.sprites(Array.from({ length: 1500 }, () => valid)),
  graphics.sprites(Array.from({ length: 600 }, () => valid))]);
assert.equal(calls.filter(call => call[0] === "drawImage").length, 2000);
calls.length = 0;
render([graphics.sprites([{ ...valid, x: Infinity }, { ...valid, frame: -1 },
  { ...valid, sheet: { ...sheet, columns: 0 } }, valid])]);
assert.equal(calls.filter(call => call[0] === "drawImage").length, 1);
calls.length = 0;

render([graphics.bitmapText("AB\nCD", font, 4, 5)]);
assert.equal(calls.filter(call => call[0] === "drawImage").length, 4);
assert.deepEqual(calls.filter(call => call[0] === "drawImage").map(call => call.slice(-4)),
  [[4, 5, 8, 8], [12, 5, 8, 8], [4, 13, 8, 8], [12, 13, 8, 8]]);
calls.length = 0;
render([graphics.bitmapText("A!", graphics.fonts.vga8x8, 0, 0, { color: "#f00" })]);
assert.equal(calls.filter(call => call[0] === "drawImage").length, 2);
assert.ok(fontPixels > 0);
calls.length = 0;
render([graphics.bitmapText("A", graphics.fonts.vga8x16, 0, 0)]);
assert.equal(calls.filter(call => call[0] === "drawImage").length, 1);
calls.length = 0;

render(Array.from({ length: 11 }, () => graphics.bitmapText("A".repeat(200),
  graphics.fonts.vga8x8, 0, 0)));
assert.equal(calls.filter(call => call[0] === "drawImage").length, 2000);
calls.length = 0;

render([...Array.from({ length: 599 }, () => ({ type: "rect", x: 0, y: 0, width: 1, height: 1 })),
  graphics.sprites([valid]), graphics.sprites([valid])]);
assert.equal(calls.filter(call => call[0] === "drawImage").length, 1);

console.log("Application sprite batch and bitmap font tests passed");
