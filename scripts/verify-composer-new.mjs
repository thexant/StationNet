import assert from "node:assert/strict";

let components = [];
let failSave = false;
const ui = {
  modules: { load: async () => import("../stationnet/application_packages/midi-composer/midi.js") },
  display: { viewport: async () => ({ width: 1100, height: 800 }) },
  storage: {
    get: async () => null,
    set: async () => { if (failSave) throw new Error("Storage full"); return true; },
    delete: async () => true,
  },
  midi: { stop: async () => {}, load: async () => {}, play: async () => {}, pause: async () => {}, seek: async () => {}, position: async () => 0 },
  timeout: () => () => {}, animate: () => {}, onCleanup: () => {}, onKey: () => {},
  render: (...items) => { components = items; },
  update: (id, item) => { const i = components.findIndex(component => component.id === id); if (i >= 0) components[i] = item; },
};
for (const method of ["title", "text", "menu", "input", "button", "select", "numberInput", "canvas"])
  ui[method] = (...args) => ({ type: method, label: args[0], callback: method === "menu" ? args[2] : method === "button" ? args[1] : null,
    onPointer: method === "canvas" ? args[3]?.onPointer : null, id: method === "canvas" ? args[3]?.id : null });
globalThis.StationNet = { start: handler => handler(ui) };
await import("../stationnet/application_packages/midi-composer/main.js");
await new Promise(resolve => setImmediate(resolve));
const roll = () => components.find(component => component.type === "canvas");
const start = () => components.find(component => component.label === "Start a composition");
const summary = () => components.find(component => component.type === "text" && String(component.label).includes("notes on"));

roll().onPointer({ x: 100, y: 325, phase: "start" });
roll().onPointer({ x: 100, y: 325, phase: "end" });
assert.match(summary().label, /1 notes on/);
failSave = true;
await start().callback({ value: "new" });
assert.match(summary().label, /1 notes on/, "failed save must retain current score");
await start().callback({ value: "discard" });
assert.match(summary().label, /0 notes on/, "discard must open an empty score");
console.log("Composer New: blank score and failed-save recovery verified");
