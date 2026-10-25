import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../stationnet/static/application_frame.js", import.meta.url), "utf8");
const protocol = "stationnet-app-v1";
const parentMessages = [], workerMessages = [], listeners = new Map();
let worker, paint;
class FakeWorker {
  constructor() { this.listeners = new Map(); worker = this; }
  addEventListener(type, callback) { this.listeners.set(type, callback); }
  postMessage(message) { workerMessages.push(message); }
}
const document = {
  hidden: false,
  getElementById(id) { return { textContent: id === "stationnet-package-files" ? "{}" : "" }; },
  addEventListener() {}
};
const parent = { postMessage(message) { parentMessages.push(message); } };
const context = {
  document, parent, Worker: FakeWorker, Blob: class {},
  URL: { createObjectURL: () => "blob:test", revokeObjectURL() {} },
  atob, TextDecoder, Uint8Array,
  requestAnimationFrame(callback) { paint = callback; return 1; },
  cancelAnimationFrame() {},
  addEventListener(type, callback) { listeners.set(type, callback); }
};
vm.runInNewContext(source, context);
const fromWorker = (data) => worker.listeners.get("message")({ data: { protocol, ...data } });
const fromParent = (data) => listeners.get("message")({ source: parent, data: { protocol, ...data } });
fromWorker({ type: "animation", action: "animation-1", enabled: true });
paint(1000);
assert.equal(parentMessages.at(-1).type, "gamepad-sample");
assert.equal(workerMessages.length, 0);
fromWorker({ type: "animation", action: "animation-2", enabled: true });
paint(1016);
assert.equal(parentMessages.filter((item) => item.type === "gamepad-sample").length, 2);
fromParent({ type: "gamepad-state", gamepads: [{ buttons: [1], axes: [-.5] }] });
assert.equal(workerMessages.at(-1).type, "animation-frame");
assert.equal(workerMessages.at(-1).frames[0].action, "animation-1");
assert.equal(workerMessages.at(-1).gamepads[0].axes[0], -.5);
fromParent({ type: "gamepad-state", gamepads: [] });
assert.equal(workerMessages.at(-1).frames[0].action, "animation-2");
fromWorker({ type: "gamepad-sample" });
assert.equal(parentMessages.filter((item) => item.type === "gamepad-sample").length, 2);
fromWorker({ type: "animation-ready", action: "animation-1" });
assert.equal(typeof paint, "function");

console.log("application relay gamepad frame checks passed");
