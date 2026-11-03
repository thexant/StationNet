import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const root = new URL("../stationnet/application_packages/oddling-garden/", import.meta.url);
const source = readFileSync(new URL("main.js", root), "utf8");
const OriginalDate = globalThis.Date;
let now = OriginalDate.now();
class ClockDate extends OriginalDate {
  constructor(...args) { super(...(args.length ? args : [now])); }
  static now() { return now; }
}
globalThis.Date = ClockDate;
const flush = async () => { await new Promise(setImmediate); await new Promise(setImmediate); };

function pet(overrides = {}) {
  const createdAt = now - 3 * 60 * 60 * 1000;
  return { name: "Pip", species: "mimsy", createdAt, hatchAt: createdAt + 120000,
    lastUpdate: now, hunger: 80, clean: 80, energy: 80, fun: 75, social: 78,
    health: 95, sleeping: false, happyWeighted: 0, livedMs: 1, bestGame: 0,
    careCount: 0, warmth: 0, ...overrides };
}
function memorial(index) {
  return { name: `Pet ${index}`, species: "blorble", created: now - 14 * 86400000,
    hatched: now - 14 * 86400000 + 120000, passed: now - index * 86400000,
    cause: "old age", happiness: 82, bestGame: 5, careCount: 22, lastStage: "Elder" };
}
async function launch({ saved = null, loadError = false, saveError = false } = {}) {
  let rendered = [], animation, visibility, keyHandler, presentationChange;
  let failLoad = loadError, failSave = saveError, stored = saved, frame = 0;
  const writes = [], downloads = [], sounds = [];
  const node = (type, value, options = {}) => ({ type, value, ...options });
  const findIn = (children, id) => {
    for (let i = 0; i < children.length; i++) {
      const item = children[i];
      if (item?.id === id) return [children, i];
      const nested = item?.components && findIn(item.components, id);
      if (nested) return nested;
    }
    return null;
  };
  const ui = {
    modules: { load: path => import(new URL(path, root)) },
    storage: {
      async get() { if (failLoad) throw Error("load failed"); return stored; },
      async set(_key, value) { if (failSave) throw Error("save failed"); stored = structuredClone(value); writes.push(stored); return true; }
    },
    graphics: {
      framebuffer(_id, width, height) {
        return { pixels: new Uint8Array(width * height * 4), clear() {}, present() {} };
      },
      frame: () => ({ type: "frame" }),
      bitmapText: (value) => ({ type: "bitmapText", value }),
      fonts: { vga8x8: {}, vga8x16: {} }
    },
    random: { seed() {}, next: () => .5 },
    sound: { sequence(...args) { sounds.push(args); } },
    input: { axis: () => ({ x: 0, y: 0 }), gamepad: () => ({ buttons: [], axes: [] }), isDown: () => false },
    display: { viewport: async () => ({ width: 800, height: 600, orientation: "landscape" }) },
    presentation: { mode: "standard", fullscreen: false, onChange(fn) { presentationChange = fn; },
      enterImmersive() { this.mode = "immersive"; presentationChange?.(this.mode); },
      exitImmersive() { this.mode = "standard"; presentationChange?.(this.mode); },
      requestFullscreen() { this.fullscreen = true; presentationChange?.(this.mode); return Promise.resolve(); },
      exitFullscreen() { this.fullscreen = false; presentationChange?.(this.mode); return Promise.resolve(); } },
    download: { async text(filename, value) { downloads.push({ filename, value }); return true; } },
    dialog: async () => "replace",
    timeout: () => () => {},
    animate(fn) { animation = fn; return () => {}; },
    onVisibility(fn) { visibility = fn; fn({ value: "visible" }); return () => {}; },
    onCleanup() {},
    onKey(fn) { keyHandler = fn; return () => {}; },
    render(...components) { rendered = components; },
    update(id, component) {
      const target = findIn(rendered, id);
      if (!target) return;
      const old = target[0][target[1]];
      if (old.type === "canvas" && component.type === "canvas") {
        component.onPointer ??= old.onPointer;
        component.controls ??= old.controls;
      }
      target[0][target[1]] = component;
    },
    title(value, options) { return node("title", value, options); },
    heading(value, options) { return node("heading", value, options); },
    text(value, options) { return node("text", value, options); },
    statusBar(value, options) { return node("status", value, options); },
    button(value, onClick, options = {}) { return node("button", value, { onClick, ...options }); },
    menu(value, items, onSelect, options = {}) { return node("menu", value, { items, onSelect, ...options }); },
    tabs(labels, onSelect, options = {}) { return node("tabs", labels, { onSelect, ...options }); },
    panel(value, components, options = {}) { return node("panel", value, { components, ...options }); },
    row(components, options = {}) { return node("row", "", { components, ...options }); },
    column(components, options = {}) { return node("column", "", { components, ...options }); },
    table(headers, rows, options = {}) { return node("table", "", { headers, rows, ...options }); },
    list(items, options = {}) { return node("list", "", { items, ...options }); },
    input(name, label, options = {}) { return node("input", label, { name, ...options }); },
    slider(name, label, options = {}) { return node("slider", label, { name, ...options }); },
    checkbox(name, label, options = {}) { return node("checkbox", label, { name, ...options }); },
    canvas(width, height, commands, options = {}) { return node("canvas", "", { width, height, commands, ...options }); }
  };
  let started;
  vm.runInNewContext(source, { StationNet: { start(fn) { started = fn(ui); } },
    Date: ClockDate, Math, Uint8Array, Map, Promise, console });
  await started;
  await flush();
  const walk = (children = rendered) => children.flatMap(item => [item, ...walk(item.components || [])]);
  const find = (type, value) => walk().find(item => item.type === type && (value === undefined || item.value === value));
  const click = async (label, values = {}) => {
    const button = find("button", label);
    assert.ok(button, `missing button: ${label}`);
    await button.onClick({ values });
    await flush();
  };
  return { get rendered() { return rendered; }, get writes() { return writes; },
    get downloads() { return downloads; }, get sounds() { return sounds; }, walk, find, click,
    async menu(label, value, values = {}) { await find("menu", label).onSelect({ value, values }); await flush(); },
    async tab(index) { await find("tabs").onSelect({ value: String(index) }); await flush(); },
    async tick(milliseconds) { now += milliseconds; frame += milliseconds; animation({ time: frame, delta: milliseconds / 1000 }); await flush(); },
    async hide() { visibility({ value: "hidden" }); await flush(); },
    async show() { visibility({ value: "visible" }); await flush(); },
    async key(code) { keyHandler({ code, phase: "down", repeat: false }); await flush(); },
    setLoadError(value) { failLoad = value; }, setSaveError(value) { failSave = value; },
    presentationChange(viewport) { presentationChange?.("standard", viewport); }
  };
}

try {
  {
    const app = await launch({ loadError: true });
    assert.match(app.find("title").value, /SAVE NEEDS ATTENTION/);
    assert.equal(app.writes.length, 0, "failed load must never overwrite saved data");
    await app.tick(30000);
    assert.equal(app.writes.length, 0);
    app.setLoadError(false);
    await app.click("Retry loading");
    assert.equal(app.find("title").value, "ODDLING GARDEN");
  }
  {
    const app = await launch({ saved: { version: 1, pet: { createdAt: "bad" }, memorials: [] } });
    assert.match(app.find("title").value, /SAVE NEEDS ATTENTION/);
    assert.equal(app.writes.length, 0, "damaged data must not be overwritten on load");
    await app.click("Download saved data");
    assert.match(app.downloads.at(-1).value, /"createdAt": "bad"/);
    await app.click("Start fresh...");
    assert.equal(app.writes.at(-1).version, 2);
  }
  {
    const app = await launch({ saveError: true });
    await app.click("Adopt egg", { petname: "Pip" });
    assert.match(app.find("text", undefined).value, /Pip|Raise/);
    assert.match(app.walk().find(item => item.id === "save-status").value, /Could not save changes/);
  }
  {
    const app = await launch({ saved: { version: 1, pet: pet(), memorials: [], settings: { sound: 50 } } });
    assert.equal(app.writes.at(-1).version, 2);
    assert.equal(app.walk().find(item => item.id === "status").value, "Stage: Baby");
    await app.tab(4);
    await app.menu("Choose one plant for this life", "moonfern");
    assert.equal(app.writes.at(-1).pet.plant.kind, "moonfern");
    await app.click("Life journal");
    await app.click("Save memory", { memory: "A lovely day" });
    await app.click("Download keepsake");
    assert.match(app.downloads.at(-1).value, /A lovely day/);
  }
  {
    const app = await launch({ saved: { version: 1, pet: pet(), memorials: [] } });
    const pointer = event => app.find("canvas").onPointer(event);
    pointer({ phase: "start", x: 180, y: 125, pointerId: 1, pointers: 1 });
    pointer({ phase: "end", x: 180, y: 125, pointerId: 1, pointers: 0 });
    await flush();
    assert.equal(app.writes.at(-1).pet.careCount, 1, "tap cuddles once");
    pointer({ phase: "start", x: 180, y: 125, pointerId: 2, pointers: 1 });
    pointer({ phase: "move", x: 205, y: 125, pointerId: 2, pointers: 1 });
    pointer({ phase: "end", x: 205, y: 125, pointerId: 2, pointers: 0 });
    await flush();
    assert.equal(app.writes.at(-1).pet.careCount, 2, "stroke pets once");
    pointer({ phase: "start", x: 165, y: 120, pointerId: 3, pointers: 1 });
    pointer({ phase: "start", x: 195, y: 120, pointerId: 4, pointers: 2 });
    pointer({ phase: "move", x: 220, y: 120, pointerId: 4, pointers: 2 });
    pointer({ phase: "end", x: 165, y: 120, pointerId: 3, pointers: 1 });
    pointer({ phase: "end", x: 220, y: 120, pointerId: 4, pointers: 0 });
    await flush();
    assert.equal(app.writes.at(-1).pet.careCount, 2, "pinch does not trigger care");
  }
  {
    const app = await launch({ saved: { version: 1, pet: null,
      memorials: Array.from({ length: 40 }, (_, i) => memorial(i)), settings: { sound: 65 } } });
    await app.click("Memorials");
    assert.ok(app.walk().length < 100);
    await app.click("Next page");
    assert.match(app.find("menu").value, /page 2 of 5/);
    await app.click("Download keepsake");
    assert.match(app.downloads.at(-1).value, /Pet 8/);
  }
  {
    const app = await launch({ saved: { version: 1, pet: pet(), memorials: [], settings: { sound: 65 } } });
    await app.tab(3);
    await app.click("Play Spark Chase (20 seconds)");
    await app.click("End round early");
    assert.equal(app.writes.at(-1).pet.bestGame, 0);
    await app.tab(3);
    await app.click("Play Spark Chase (20 seconds)");
    app.find("canvas").onPointer({ phase: "start", x: 180, y: 113, pointerId: 1 });
    await app.hide();
    await app.tick(100000);
    await app.show();
    assert.match(app.find("title").value, /SPARK CHASE/, "hidden game should retain its remaining time");
    await app.tick(21000);
    assert.equal(app.writes.at(-1).pet.bestGame, 1);
  }
  {
    const createdAt = now - 14 * 86400000 - 1000;
    const app = await launch({ saved: { version: 1, pet: pet({ createdAt,
      hatchAt: createdAt + 120000, lastUpdate: now - 2000 }), memorials: [] } });
    assert.match(app.find("title").value, /GOODBYE/);
    assert.equal(app.writes.at(-1).pet.death.cause, "old age");
    await app.click("Say goodbye / view memorial");
    assert.equal(app.writes.at(-1).memorials.length, 1);
    assert.ok(app.writes.at(-1).memorials[0].journal.length);
  }
  {
    const app = await launch({ saved: { version: 1, pet: pet({
      createdAt: now - 2 * 86400000, hatchAt: now - 2 * 86400000 + 120000,
      lastUpdate: now - 3600000, hunger: 0, clean: 0, fun: 0, social: 0, health: .1
    }), memorials: [] } });
    assert.match(app.find("title").value, /GOODBYE/);
    assert.equal(app.writes.at(-1).pet.death.cause, "neglect");
    assert.match(app.writes.at(-1).pet.death.reason, /fullness/);
  }
  {
    const createdAt = now - 2 * 86400000;
    const app = await launch({ saved: { version: 1, pet: pet({ createdAt, hatchAt: createdAt + 120000,
      lastUpdate: now - 12 * 3600000, energy: 30, hunger: 90, sleeping: true }), memorials: [] } });
    const saved = app.writes.at(-1).pet;
    assert.equal(saved.sleeping, true, "a rested pet stays asleep until woken manually");
    assert.equal(saved.energy, 100);
    assert.ok(saved.hunger < 90, "needs still decay while asleep");
    await app.key("KeyS");
    assert.equal(app.writes.at(-1).pet.sleeping, false);
  }
  console.log("Oddling Garden checks passed");
} finally {
  globalThis.Date = OriginalDate;
}
