"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.join(__dirname, "..");
const RUNTIME = path.join(ROOT, "stationnet/static/application_runtime.js");
const CLOCK = path.join(ROOT, "stationnet/application_packages/clock/main.js");

function launchClock() {
  const sent = [], listeners = {};
  const context = {
    __stationnetPackageFiles: {}, postMessage: (message) => sent.push(message),
    addEventListener: (type, handler) => { listeners[type] = handler; },
    atob: (value) => Buffer.from(value, "base64").toString("binary"),
    TextDecoder, Uint8Array, Promise, URL, Blob, setTimeout, clearTimeout,
    setInterval: () => 1, clearInterval, Date, Intl, Math
  };
  context.self = context;
  vm.runInNewContext(fs.readFileSync(RUNTIME, "utf8"), context);
  vm.runInNewContext(fs.readFileSync(CLOCK, "utf8"), context);
  return { sent, listeners };
}

async function ready(app) {
  await new Promise(setImmediate);
  const storage = app.sent.find((message) => message.type === "storage" && message.operation === "get");
  assert.ok(storage, "Clock requests its persisted preferences during startup");
  app.listeners.message({ data: { protocol: "stationnet-app-v1", type: "storage-result", request: storage.request, ok: true, value: null } });
  await new Promise(setImmediate);
}

test("Clock starts from empty preferences and renders its time console", async () => {
  const app = launchClock();
  await ready(app);
  const render = app.sent.find((message) => message.type === "render");
  assert.ok(render);
  assert.ok(render.components.some((component) => component.type === "title" && component.text === "Clock"));
  assert.ok(render.components.some((component) => component.type === "canvas" && component.label === "Analog clock"));
  assert.ok(render.components.some((component) => component.type === "menu" && component.label === "Clock tools"));
});

test("Clock navigation exposes the timer workflow", async () => {
  const app = launchClock();
  await ready(app);
  const initial = app.sent.find((message) => message.type === "render");
  const navigation = initial.components.find((component) => component.type === "menu" && component.label === "Clock tools");
  app.listeners.message({ data: { protocol: "stationnet-app-v1", type: "interaction", action: navigation.action, value: "timers", values: {} } });
  await new Promise(setImmediate);
  const renders = app.sent.filter((message) => message.type === "render" && message.mode === "replace");
  const timerView = renders.at(-1);
  assert.ok(timerView.components.some((component) => component.type === "heading" && component.text === "Countdown timers"));
  assert.ok(timerView.components.some((component) => component.type === "number" && component.name === "timerMinutes"));
});
