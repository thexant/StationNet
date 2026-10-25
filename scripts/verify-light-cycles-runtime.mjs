import assert from "node:assert/strict";

let rendered = [], animationHandler, keyHandler, visibilityHandler, startPromise;
const updates = new Map();
const sounds = [];
let arenaUpdates = 0, framePresents = 0;

const component = (type, values = {}, settings = {}) => ({ type, ...values, ...settings });
const ui = {
  modules: { load: () => import("../stationnet/application_packages/light-cycles/engine.js") },
  storage: { get: async () => null, set: async () => undefined },
  scores: { leaderboard: async () => ({ leaderboard: [] }), submit: async () => ({}) },
  sound: {
    playTone: (...args) => sounds.push(["tone", ...args]),
    play: (...args) => sounds.push(["play", ...args]),
    sequence: (...args) => sounds.push(["sequence", ...args])
  },
  text: (text, settings) => component("text", { text }, settings),
  title: (text, settings) => component("title", { text }, settings),
  heading: (text, level, settings) => component("heading", { text, level }, settings),
  button: (label, onClick, settings) => component("button", { label, onClick }, settings),
  select: (name, label, items, settings) => component("select", { name, label, items, onChange: settings?.onChange }, settings),
  menu: (label, items, onSelect, settings) => component("menu", { label, items, onSelect }, settings),
  table: (headers, rows, settings) => component("table", { headers, rows }, settings),
  canvas: (width, height, commands, settings) => component("canvas", { width, height, commands, onPointer: settings?.onPointer }, settings),
  graphics: {
    framebuffer: (id, width, height) => ({
      id, width, height, pixels: new Uint8Array(width * height), palette: new Uint8Array(1024),
      clear() {}, fillRect() {}, present() { framePresents += 1; }
    }),
    frame: (buffer, x, y, width, height) => ({ type: "framebuffer", buffer: buffer.id, x, y, width, height })
  },
  render: (...items) => { rendered = items; },
  update: (id, value) => { updates.set(id, value); if (id === "arena") arenaUpdates += 1; },
  onKey: (handler) => { keyHandler = handler; },
  onVisibility: (handler) => { visibilityHandler = handler; },
  animate: (handler) => { animationHandler = handler; }
};

globalThis.StationNet = { start: (callback) => { startPromise = callback(ui); } };
await import(`../stationnet/application_packages/light-cycles/main.js?runtime-test=${Date.now()}`);
await startPromise;

assert.equal(typeof animationHandler, "function", "game registers StationNet's animation API");
assert.equal(typeof keyHandler, "function", "game registers keyboard controls");
assert.equal(typeof visibilityHandler, "function", "game pauses when hidden");

rendered.find((item) => item.type === "button" && item.label === "Enter the grid").onClick();
const swipeCanvas = rendered.find((item) => item.type === "canvas");
assert.equal(typeof swipeCanvas.onPointer, "function", "arena installs one persistent swipe handler");
const countdownLabel = () => updates.get("arena").commands.find((command) => command.type === "text")?.text;
assert.equal(countdownLabel(), "3", "match starts with a visible countdown");
animationHandler({ delta: 1 });
assert.equal(countdownLabel(), "3", "countdown advances from animation time");
updates.get("action").onClick();
assert.equal(updates.get("action").label, "Resume", "button pauses the countdown");
for (let frame = 0; frame < 90; frame += 1) animationHandler({ delta: 1 / 60 });
assert.equal(countdownLabel(), "PAUSED", "paused overlay is shown");
updates.get("action").onClick();
assert.equal(countdownLabel(), "3", "resume restores the frozen countdown immediately");
for (let frame = 0; frame < 65; frame += 1) animationHandler({ delta: 1 / 60 });
assert.equal(countdownLabel(), "2", "countdown advances after resuming");

for (let frame = 0; frame < 180; frame += 1) animationHandler({ delta: 1 / 60 });
assert.ok(arenaUpdates >= 220, "canvas is rendered at display cadence during countdown and play");
assert.ok(framePresents >= 2, "trail framebuffer is updated independently of the canvas overlay");
assert.equal(updates.get("arena").onPointer, undefined, "animation frames do not allocate replacement pointer handlers");
assert.ok(updates.get("arena").commands.some((command) => command.type === "polygon"), "directional cycle bodies are rendered");
assert.match(updates.get("status").text, /RUNNING|COLLAPSING/, "countdown transitions into live play");

const beforeKeyboard = sounds.length;
keyHandler({ code: "ArrowDown", phase: "down", repeat: false });
assert.ok(sounds.length > beforeKeyboard, "arrow input turns immediately and produces feedback");
for (let frame = 0; frame < 8; frame += 1) animationHandler({ delta: 1 / 60 });

const beforeSwipe = sounds.length;
swipeCanvas.onPointer({ phase: "start", x: 100, y: 100 });
swipeCanvas.onPointer({ phase: "end", x: 190, y: 100 });
assert.ok(sounds.length > beforeSwipe, "mobile swipe input turns immediately and produces feedback");

keyHandler({ code: "Space", phase: "down", repeat: false });
assert.equal(updates.get("action").label, "Resume", "space pauses live play");
const pausedArena = JSON.stringify(updates.get("arena").commands);
for (let frame = 0; frame < 30; frame += 1) animationHandler({ delta: 1 / 60 });
assert.equal(JSON.stringify(updates.get("arena").commands), pausedArena, "pause freezes the arena");
keyHandler({ code: "Space", phase: "down", repeat: false });
assert.equal(updates.get("action").label, "Pause", "space resumes live play");
visibilityHandler({ value: "hidden" });
assert.equal(updates.get("action").label, "Resume", "hiding the window pauses live play");

console.log("Light Cycles runtime checks passed");
