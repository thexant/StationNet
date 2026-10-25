import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const protocol = "stationnet-app-v1";
const source = readFileSync(new URL("../stationnet/static/application_runtime.js", import.meta.url), "utf8");
const sent = [], listeners = new Map();
const worker = {
  __stationnetPackageFiles: {},
  postMessage(message) { sent.push(message); },
  addEventListener(type, callback) { listeners.set(type, callback); },
  setTimeout, clearTimeout, setInterval, clearInterval
};
worker.self = worker;
vm.runInNewContext(source, worker);
const ui = worker.StationNet;
const dispatch = async (data) => {
  listeners.get("message")({ data: { protocol, ...data } });
  await new Promise(setImmediate);
};
const frame = async (delta, gamepads = []) => {
  const active = new Map();
  sent.filter((item) => item.type === "animation").forEach(({ action, enabled }) => active.set(action, enabled));
  const frames = [...active].filter(([, enabled]) => enabled)
    .map(([action]) => ({ action, time: 1000, delta }));
  await dispatch({ type: "animation-frame", frames, gamepads });
};

assert.equal(ui.input("name", "Name").type, "input");
assert.throws(() => ui.input.isDown(" "), /keyboard code/);
assert.throws(() => ui.input.gamepad(4), /Gamepad index/);
assert.throws(() => ui.input.gamepad(0, { deadzone: 1 }), /deadzone/);

const observations = [];
const animation = ui.animate(() => {
  observations.push([ui.input.isDown("KeyA"), ui.input.wasPressed("KeyA"),
    ui.input.wasReleased("KeyA"), ui.input.gamepad(0, { deadzone: .2 })]);
});
await dispatch({ type: "input-key", code: "KeyA", down: true });
await dispatch({ type: "input-key", code: "KeyA", down: true });
assert.equal(ui.input.wasPressed("KeyA"), true);
await frame(0, [{ buttons: [2, NaN], axes: [.1, -.7, 2] }]);
assert.deepEqual(observations[0].slice(0, 3), [true, true, false]);
assert.deepEqual(Array.from(observations[0][3].buttons), [1, 0]);
assert.deepEqual(Array.from(observations[0][3].axes), [0, -.7, 1]);
assert.equal(ui.input.wasPressed("KeyA"), false);
await frame(1 / 60);
assert.deepEqual(observations[1].slice(0, 3), [true, false, false]);
await dispatch({ type: "input-key", code: "KeyA", down: false });
await frame(1 / 60);
assert.deepEqual(observations[2].slice(0, 3), [false, false, true]);
assert.equal(ui.input.wasReleased("KeyA"), false);
animation();

let releaseFrame;
const waiting = new Promise((resolve) => { releaseFrame = resolve; });
const lateEdges = [];
const delayed = ui.animate(async () => {
  lateEdges.push(ui.input.wasPressed("KeyB"));
  if (lateEdges.length === 1) await waiting;
});
await frame(0);
await dispatch({ type: "input-key", code: "KeyB", down: true });
releaseFrame();
await new Promise(setImmediate);
await frame(1 / 60);
assert.deepEqual(lateEdges, [false, true]);
delayed();

const ticks = [];
const fixed = ui.fixedStep(60, ({ delta, tick }) => ticks.push([delta, tick]));
assert.throws(() => ui.fixedStep(9, () => {}), /10-120/);
assert.throws(() => ui.fixedStep(60, null), /callback/);
await frame(0);
await frame(1 / 30);
assert.equal(ticks.length, 2);
await frame(1);
assert.equal(ticks.length, 7);
assert.deepEqual(ticks.map((item) => item[1]), [1, 2, 3, 4, 5, 6, 7]);
assert.ok(ticks.every((item) => item[0] === 1 / 60));
fixed.pause(); fixed.resume();
await frame(0);
assert.equal(ticks.length, 7);
fixed();

ui.random.seed(123);
const sequence = [ui.random.next(), ui.random.next(), ui.random.int(2, 4)];
ui.random.seed(123);
assert.deepEqual([ui.random.next(), ui.random.next(), ui.random.int(2, 4)], sequence);
assert.ok(sequence[0] >= 0 && sequence[0] < 1);
assert.ok(sequence[2] >= 2 && sequence[2] <= 4);
assert.throws(() => ui.random.seed(Infinity), /safe integer/);
assert.throws(() => ui.random.int(4, 2), /bounds/);

console.log("application game input and timing checks passed");
