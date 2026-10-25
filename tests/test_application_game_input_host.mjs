import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../stationnet/static/application_host.js", import.meta.url), "utf8");
const protocol = "stationnet-app-v1";
const listeners = new Map(), sent = [];
const contentWindow = { postMessage(message) { sent.push(message); } };
const frame = { dataset: { runtimeSrc: "about:blank" }, contentWindow, remove() {} };
const surface = { dataset: {}, querySelectorAll() { return []; }, addEventListener() {} };
const status = {};
let focused = true;
const document = {
  hidden: false, hasFocus: () => focused, addEventListener() {},
  querySelector(selector) {
    return selector === "[data-app-runtime]" ? frame :
      selector === "[data-app-surface]" ? surface :
      selector === "[data-app-status]" ? status : null;
  }
};
const pads = Array(4).fill(null);
pads[0] = { connected: true, buttons: Array.from({ length: 40 }, (_, i) => ({ value: i === 0 ? 2 : NaN })),
  axes: Array.from({ length: 20 }, (_, i) => i === 0 ? -2 : NaN) };
const host = {
  document, window: {}, navigator: { maxTouchPoints: 0, userAgent: "test", getGamepads: () => pads },
  crypto: { randomUUID: () => "test-id" }, atob, Uint8Array,
  setTimeout: () => 1, clearTimeout() {},
  addEventListener(type, callback) { listeners.set(type, callback); }
};
vm.runInNewContext(source, host);
const send = (data) => listeners.get("message")({ source: contentWindow, data: { protocol, ...data } });
send({ type: "gamepad-sample" });
const snapshot = sent.at(-1);
assert.equal(snapshot.type, "gamepad-state");
assert.equal(snapshot.gamepads.length, 4);
assert.equal(snapshot.gamepads[0].buttons.length, 32);
assert.equal(snapshot.gamepads[0].axes.length, 16);
assert.equal(snapshot.gamepads[0].buttons[0], 1);
assert.equal(snapshot.gamepads[0].buttons[1], 0);
assert.equal(snapshot.gamepads[0].axes[0], -1);
assert.equal(snapshot.gamepads[0].axes[1], 0);
assert.equal(snapshot.gamepads[1], null);

const key = (type, code, repeat = false) => listeners.get(type)({
  key: "a", code, repeat, target: { tagName: "BODY" }, preventDefault() {},
  ctrlKey: false, altKey: false, shiftKey: false, metaKey: false
});
key("keydown", "KeyA");
assert.deepEqual({ code: sent.at(-1).code, down: sent.at(-1).down }, { code: "KeyA", down: true });
const count = sent.length;
key("keydown", "KeyA", true);
assert.equal(sent.length, count);
key("keyup", "KeyA");
assert.deepEqual({ code: sent.at(-1).code, down: sent.at(-1).down }, { code: "KeyA", down: false });
key("keydown", "KeyA");
focused = false;
listeners.get("blur")();
assert.deepEqual({ code: sent.at(-2).code, down: sent.at(-2).down }, { code: "KeyA", down: false });

console.log("application host gamepad and keyboard bridge checks passed");
