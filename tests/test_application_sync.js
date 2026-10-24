"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

test("fixed ticks validate movement and publish host state", async () => {
  const sent = [], listeners = {}, intervals = new Map();
  let nextTimer = 1;
  const context = {
    __stationnetPackageFiles: {},
    postMessage: (value) => sent.push(value),
    addEventListener: (kind, handler) => { listeners[kind] = handler; },
    setInterval: (handler, delay) => { intervals.set(nextTimer, { handler, delay }); return nextTimer++; },
    clearInterval: (id) => intervals.delete(id),
    setTimeout, clearTimeout, Promise, URL, Blob, TextDecoder, Uint8Array
  };
  context.self = context;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../stationnet/static/application_runtime.js"), "utf8"), context);
  const ui = context.StationNet;
  const dispatch = (value) => listeners.message({ data: { protocol: "stationnet-app-v1", ...value } });
  const created = ui.multiplayer.create({ name: "Movement" });
  const createRequest = sent.at(-1);
  dispatch({ type: "multiplayer-result", request: createRequest.request, ok: true,
    value: { session: { id: "room" }, player_id: "player:1" } });
  await created;
  const sync = ui.multiplayer.createSync({ tickRate: 20,
    validateInput: (input) => Number.isInteger(input.dx) && Math.abs(input.dx) <= 1,
    onTick: ({ state, inputs, isHost }) => isHost ? { x: (state?.x || 0) + inputs.reduce((sum, item) => sum + item.data.dx, 0) } : undefined
  });
  const starting = sync.start("player:1");
  const connectRequest = sent.at(-1);
  dispatch({ type: "realtime-result", request: connectRequest.request, ok: true,
    value: { connected: true, sync: { type: "sync", epoch: 1, host_id: "player:1", tick: -1, state: { x: 0 } } } });
  dispatch({ type: "realtime-status", connected: true });
  await starting;
  dispatch({ type: "realtime-event", value: { type: "input", epoch: 1, player: { id: "player:2" }, data: { dx: 1 } } });
  dispatch({ type: "realtime-event", value: { type: "input", epoch: 1, player: { id: "player:2" }, data: { dx: 500 } } });
  await Promise.resolve();
  const tick = [...intervals.values()].find((item) => item.delay === 50);
  assert.ok(tick);
  tick.handler();
  const published = sent.at(-1);
  assert.equal(published.kind, "state");
  assert.equal(published.tick, 0);
  assert.equal(published.data.x, 1);
  assert.equal(sync.sample().x, 1);
  const stopped = sync.stop();
  dispatch({ type: "realtime-result", request: sent.at(-1).request, ok: true, value: { connected: false } });
  await stopped;
});
