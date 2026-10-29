import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../stationnet/application_packages/wirestorm-rally/main.js", import.meta.url), "utf8");
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };

async function launch({ saved = {}, failLoad = false, viewport = { width: 1366, height: 700, orientation: "landscape" } } = {}) {
  const data = new Map(Object.entries(saved).map(([key, value]) => [key, JSON.parse(JSON.stringify(value))]));
  const writes = [], submitted = [], held = new Set();
  let rendered = [], step = null, frame = null, keyHandler = null, timers = [];
  const canvases = new Map();
  const remember = (component) => { if (component && component.type === "canvas" && component.id) canvases.set(component.id, component); };
  const walk = (nodes) => nodes.forEach(n => { remember(n); if (n && n.components) walk(n.components); });
  const ui = {
    storage: {
      async get(key) { if (failLoad) throw Error("load failed"); return data.has(key) ? JSON.parse(JSON.stringify(data.get(key))) : null; },
      async set(key, value) { const copy = JSON.parse(JSON.stringify(value)); data.set(key, copy); writes.push({ key, value: copy }); return true; }
    },
    scores: {
      async submit(score) { submitted.push(score); return { ok: true, personal_best: score, leaderboard: [{ username: "tester", score }] }; },
      async leaderboard() { return { ok: true, personal_best: submitted.at(-1) ?? null, leaderboard: [{ username: "tester", score: submitted.at(-1) ?? 0 }] }; }
    },
    sound: {
      createSample() { return { sample: true }; }, playSample() { return 1; }, stopSample() {},
      playTone() {}, play() {}, sequence() {}, stop() {}
    },
    input: { isDown: (code) => held.has(code), gamepad: () => ({ buttons: [], axes: [] }) },
    display: { async viewport() { return viewport; } },
    presentation: { mode: "standard", fullscreen: false, onChange() {}, enterImmersive() { this.mode = "immersive"; },
      exitImmersive() { this.mode = "standard"; }, async requestFullscreen() {}, async exitFullscreen() {} },
    timeout(fn) { timers.push(fn); return () => { timers = timers.filter(t => t !== fn); }; },
    fixedStep(_hz, fn) { step = fn; return () => {}; },
    animate(fn) { frame = fn; return () => {}; },
    onKey(fn) { keyHandler = fn; return () => {}; },
    onVisibility() { return () => {}; },
    onCleanup() {},
    render(...components) { rendered = components; walk(components); },
    update(_id, component) { remember(component); },
    canvas(width, height, commands, options = {}) { return { type: "canvas", width, height, commands, ...options }; },
    text(value, options = {}) { return { type: "text", value, ...options }; },
    title(label) { return { type: "title", label }; }
  };
  let started;
  vm.runInNewContext(source, { StationNet: { start(fn) { started = fn(ui); } }, console, Math, Promise, Map, Set, Array, Object, Number, String, JSON });
  await started;
  await flush();
  const app = {
    data, writes, submitted, held,
    get rendered() { return rendered; },
    canvas(id = "race") { return canvases.get(id); },
    texts(id = "race") { return app.canvas(id).commands.filter(c => c.type === "text").map(c => c.text); },
    hasText(pattern, id = "race") { return app.texts(id).some(t => pattern.test(t)); },
    async runTimers() { const pending = timers; timers = []; pending.forEach(fn => fn()); await flush(); },
    frame(delta = 1 / 60) { frame({ delta, time: 0 }); },
    steps(n) { for (let i = 0; i < n; i++) step({ delta: 1 / 60, tick: i + 1 }); app.frame(); },
    key(code) { keyHandler({ code, key: code, phase: "down", repeat: false }); keyHandler({ code, key: code, phase: "up", repeat: false }); app.frame(); },
    pointer(event, id = "race") { app.canvas(id).onPointer({ pointerId: 1, pointers: 1, pointerType: "mouse", ...event }); app.frame(); },
    // Taps the centre of a garage button found by its label.
    tap(label, pointerType = "mouse") {
      const c = app.canvas();
      const text = c.commands.find(cmd => cmd.type === "text" && cmd.text === label);
      assert.ok(text, `button "${label}" is drawn`);
      app.pointer({ phase: "start", x: text.x, y: text.y, pointerType });
      app.pointer({ phase: "end", x: text.x, y: text.y, pointerType });
    }
  };
  return app;
}

function budget(canvas, where) {
  const cmds = canvas.commands;
  assert.ok(cmds.length <= 600, `${where}: ${cmds.length} commands exceed 600`);
  const polys = cmds.filter(c => c.type === "polygon");
  assert.ok(polys.length <= 256, `${where}: ${polys.length} polygons exceed 256`);
  const vertices = polys.reduce((n, c) => n + c.points.length, 0);
  assert.ok(vertices <= 20000, `${where}: ${vertices} polygon vertices`);
  assert.ok(polys.every(c => c.points.length >= 3 && c.points.length <= 4096), `${where}: polygon size`);
  const segments = cmds.filter(c => c.type === "lines").reduce((n, c) => n + c.segments.length, 0);
  assert.ok(segments <= 10000, `${where}: ${segments} line segments exceed 10000`);
  for (const c of cmds) {
    if (c.type === "text") assert.ok(String(c.text).length <= 200, `${where}: text too long: ${c.text}`);
    for (const key of ["color", "fill", "stroke"]) if (c[key] !== undefined)
      assert.match(c[key], /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i, `${where}: bad ${key} ${c[key]}`);
  }
}
// Drives with throttle only; the app's own resets keep the car moving forward.
function race(app, maxSteps) {
  app.held.add("KeyW");
  for (let i = 0; i < maxSteps && !app.hasText(/COMPLETE|VICTORY|RESULT/); i += 600) app.steps(600);
  app.held.delete("KeyW");
}

{ // A 1366x700 laptop window keeps the desktop layout; touch is adopted on first touch.
  const app = await launch();
  assert.equal(app.canvas().width, 1000, "desktop canvas is full size");
  assert.ok(app.hasText(/START RACE/));
  budget(app.canvas(), "garage");
  app.key("Enter");
  assert.equal(app.canvas("touchpad"), undefined, "no touch dashboard on a desktop-sized window");
  app.key("KeyG");
  app.tap("START RACE  ▶", "touch");
  assert.ok(app.canvas("touchpad"), "touch dashboard appears after a touch");
  assert.ok(app.canvas().width < 1000, "race view shrinks to leave room for the dashboard");
}
{ // Garage rows, pages, settings persistence and per-track rendering budgets.
  const app = await launch();
  app.key("ArrowUp"); app.key("ArrowRight"); // mode -> TIME TRIAL
  assert.ok(app.hasText(/START TIME TRIAL/));
  app.key("ArrowDown"); app.key("ArrowRight"); // next track
  assert.ok(app.hasText(/SCARP PASS/));
  await app.runTimers();
  const settings = app.data.get("settings");
  assert.equal(settings.mode, 1);
  assert.equal(settings.track, 1);
  app.key("KeyH");
  assert.ok(app.hasText(/HOW TO RACE/));
  budget(app.canvas(), "help");
  app.key("KeyR");
  await new Promise(setImmediate); app.frame();
  assert.ok(app.hasText(/STATION LEADERBOARD/));
  budget(app.canvas(), "records");
  app.tap("◀  BACK");
  assert.ok(app.hasText(/TRACK INTEL/));
  for (let t = 0; t < 14; t++) {
    app.key("Enter");
    app.held.add("KeyW"); app.held.add("Space");
    app.steps(400);
    budget(app.canvas(), `track ${t} race`);
    app.held.clear();
    app.key("KeyG");
    app.key("ArrowRight");
  }
  const reloaded = await launch({ saved: { settings: app.data.get("settings") } });
  assert.ok(reloaded.hasText(/START TIME TRIAL/), "mode restored from storage");
}
{ // A failed load never overwrites stored data.
  const app = await launch({ failLoad: true });
  assert.ok(app.hasText(/COULD NOT BE LOADED/));
  app.key("ArrowRight");
  await app.runTimers();
  assert.equal(app.writes.length, 0);
}
{ // Time trial: finishing saves a record, a ghost and a rating, then the ghost is raced.
  const app = await launch({ saved: { settings: { mode: 1, track: 7, vehicle: 3 } } });
  app.key("Enter");
  await flush();
  race(app, 60000);
  assert.ok(app.hasText(/TIME TRIAL COMPLETE/), "time trial finishes");
  budget(app.canvas(), "time trial result");
  await app.runTimers();
  const records = app.data.get("records");
  assert.ok(records["saltblade-flats:RALLY CAR"].t > 0, "record saved");
  const ghost = app.data.get("ghost:saltblade-flats");
  assert.ok(ghost && ghost.frames.length % 4 === 0 && ghost.frames.length > 100, "ghost saved");
  assert.ok(app.submitted.length === 1 && app.submitted[0] > 0, "rally rating submitted");
  app.key("Enter"); // restart
  await app.runTimers();
  app.steps(300);
  assert.ok(app.hasText(/^GHOST$/), "ghost car is drawn");
  assert.ok(app.hasText(/^GHOST [−+]/), "ghost delta is shown");
  assert.ok(app.data.size <= 100, "storage key budget");
}
{ // Championship: rivals finish, points are awarded and progress persists.
  const app = await launch({ saved: { settings: { mode: 2, cup: 0, vehicle: 3, difficulty: 0 } } });
  assert.ok(app.hasText(/START CUP/));
  app.key("Enter");
  await flush();
  race(app, 90000);
  assert.ok(app.hasText(/DUST CUP — RACE RESULT/));
  for (let i = 0; i < 30 && !app.hasText(/AFTER ROUND 1\/4/); i++) app.steps(120);
  assert.ok(app.hasText(/AFTER ROUND 1\/4/), "points table appears once results are final");
  const rows = app.canvas().commands.filter(c => c.type === "text" && /^P[1-8]$/.test(c.text));
  assert.equal(rows.length, 8, "all eight racers are listed");
  budget(app.canvas(), "cup result");
  const career = app.data.get("career");
  assert.equal(career.active.round, 1);
  assert.equal(Object.values(career.active.points).reduce((a, b) => a + b, 0), 39);
  app.key("Enter"); // next round
  assert.ok(app.hasText(/ROUND 2 \/ 4/));
  app.key("KeyG");
  assert.ok(app.hasText(/CONTINUE ROUND 2/));
}
{ // Touch players get restart and next buttons after the finish.
  const app = await launch({ saved: { settings: { mode: 1, track: 7, vehicle: 3, controls: 2 } } });
  app.key("Enter");
  await flush();
  race(app, 60000);
  const pad = app.texts("touchpad");
  assert.ok(pad.includes("RESTART") && pad.includes("NEXT TRACK"), "finish actions on the touch dashboard");
}

console.log("Wirestorm Rally checks passed");
