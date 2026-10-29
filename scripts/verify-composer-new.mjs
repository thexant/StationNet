import assert from "node:assert/strict";
import { blank, uid, writeMidi, readMidiDetailed } from
  "../stationnet/application_packages/midi-composer/midi.js";

const data = new Map();
let failSave = false, failIndexOnly = false, dialogChoice = "discard", run = 0;
let nextFile = null;
const downloads = [];
const flush = () => new Promise(resolve => setImmediate(resolve));

async function launch(width = 1100) {
  let components = [], keyHandler, animateHandler, midiLoads = 0, midiStops = 0;
  let toneCount = 0, soundPlays = 0, renderCount = 0;
  const locate = (predicate, items = components) => {
    for (const item of items) {
      if (predicate(item)) return item;
      const found = locate(predicate, item.components || []);
      if (found) return found;
    }
    return null;
  };
  const replace = (items, id, replacement) => {
    for (let i = 0; i < items.length; i++) {
      if (items[i].id === id) { items[i] = replacement; return true; }
      if (replace(items[i].components || [], id, replacement)) return true;
    }
    return false;
  };
  const node = (type, label, opts = {}) => ({ type, label, id:opts.id });
  const ui = {
    modules: { load: async () => import("../stationnet/application_packages/midi-composer/midi.js") },
    display: { viewport: async () => ({ width, height:800 }) },
    storage: {
      get: async key => structuredClone(data.get(key) ?? null),
      set: async (key, value) => {
        if ((failSave && key !== "composer-draft-v1") || (failIndexOnly && key === "composer-index-v1"))
          throw Error("Storage namespace has reached its size limit.");
        data.set(key, structuredClone(value)); return true;
      },
      delete: async key => data.delete(key),
      keys: async () => [...data.keys()]
    },
    midi: {
      stop: async () => { midiStops++; },
      load: async () => { midiLoads++; }, play: async () => {}, pause: async () => {},
      seek: async () => {}, position: async () => 0
    },
    sound: { stop: () => {}, playTone: () => { toneCount++; }, play: () => { soundPlays++; } },
    files: { open: async () => nextFile },
    download: { binary: async (name, bytes, mime) => { downloads.push({ name, bytes, mime }); return true; } },
    timeout: (handler, milliseconds) => {
      if (milliseconds > 1000) queueMicrotask(handler);
      return () => {};
    },
    animate: handler => { animateHandler = handler; }, onCleanup: () => {},
    onKey: handler => { keyHandler = handler; },
    dialog: async () => dialogChoice,
    render: (...items) => { components = items; renderCount++; },
    update: (id, item) => { replace(components, id, item); },
    title: value => node("title", value),
    text: (value, opts) => node("text", value, opts),
    statusBar: (value, opts) => node("statusBar", value, opts),
    row: (items, opts) => ({ ...node("row", "", opts), components:items }),
    panel: (label, items, opts) => ({ ...node("panel", label, opts), components:items }),
    input: (name, label, opts) => node("input", label, opts),
    numberInput: (name, label, opts) => node("numberInput", label, opts),
    select: (name, label, items, opts = {}) => ({ ...node("select", label, opts), callback:opts.onChange, value:opts.value }),
    checkbox: (name, label, opts = {}) => ({ ...node("checkbox", label, opts), callback:opts.onChange }),
    button: (label, callback, opts) => ({ ...node("button", label, opts), callback }),
    menu: (label, items, callback, opts) => ({ ...node("menu", label, opts), items, callback }),
    menuBar: (menus, callback, opts) => ({ ...node("menuBar", "", opts), menus, callback }),
    tabs: (labels, callback, opts) => ({ ...node("tabs", "", opts), labels, callback }),
    canvas: (width, height, commands, opts) => ({ ...node("canvas", "", opts), width, height, commands,
      onPointer:opts.onPointer })
  };
  let started;
  globalThis.StationNet = { start: handler => { started = handler(ui); } };
  await import(`../stationnet/application_packages/midi-composer/main.js?test=${++run}`);
  await started;
  const find = (type, label) => locate(item => item.type === type && (label === undefined || item.label === label));
  const roll = () => find("canvas");
  const noteCount = () => roll().commands.filter(c => ["#61c98c","#ae84d1","#efc76f"].includes(c.color))
    .reduce((total, c) => total + c.segments.length, 0);
  const pointer = (phase, x = 140, y = 150, pointerId = 1, pointers = phase === "end" || phase === "cancel" ? 0 : 1) =>
    roll().onPointer({ phase, x, y, pointerId, pointers });
  const file = async value => { await find("menuBar").callback({ value }); await flush(); };
  const tab = value => find("tabs").callback({ value:String(value) });
  return { find, roll, noteCount, pointer, file, tab, key: event => keyHandler({ phase:"down", ...event }),
    transport: async value => { find("menu", "Transport").callback({ value }); await flush(); },
    frame: time => animateHandler({ time }),
    stats: () => ({ midiLoads, midiStops, toneCount, soundPlays, renderCount }) };
}

const app = await launch();
app.pointer("start");
app.pointer("cancel");
assert.equal(app.noteCount(), 0, "cancelled drawing must roll back");
app.pointer("start");
app.pointer("end");
assert.equal(app.noteCount(), 1);
app.key({ code:"KeyZ", ctrlKey:true });
assert.equal(app.noteCount(), 0, "undo must remove drawn note");
app.key({ code:"KeyZ", ctrlKey:true, shiftKey:true });
assert.equal(app.noteCount(), 1, "redo must restore drawn note");
const rendersBeforeSave = app.stats().renderCount;
await app.file("save");
assert.equal(app.stats().renderCount, rendersBeforeSave, "saving must not rebuild the form");
app.pointer("start");
app.pointer("end");
assert.match(app.find("text").label, /Saved/, "clicking a note without moving must not mark it dirty");
app.pointer("start", 240, 150);
app.pointer("end", 240, 150);

failSave = true;
await app.file("new");
assert.equal(app.noteCount(), 2, "failed save must retain the current score");
assert.match(app.find("statusBar").label, /storage is full/i);
app.tab(3);
await app.find("menu", "Project").callback({ value:"discard" });
assert.equal(app.noteCount(), 0, "explicit discard must start a blank score");

failSave = false;
await app.file("demo");
assert.ok(app.noteCount() > 0, "starter score should have visible notes");
await app.file("save");
const demoId = [...data.entries()].find(([key, value]) => key.startsWith("composer-project-") &&
  value.name === "Night Signal · starter score")?.[0];
assert.ok(demoId);
await app.transport("play");
const beforePreview = app.stats();
app.pointer("start", 30, 110);
assert.equal(app.stats().midiLoads, beforePreview.midiLoads, "preview must not replace MIDI playback");
assert.equal(app.stats().toneCount, beforePreview.toneCount + 1);
await app.file("new");
await app.file("save");
app.tab(3);
const beforeOpen = app.stats().midiStops;
app.find("select", "Saved compositions").callback({ value:demoId.slice("composer-project-".length) });
await flush();
assert.ok(app.stats().midiStops > beforeOpen, "opening a score must stop the previous sequence");
assert.match(app.find("text").label, /Night Signal/);

app.pointer("start", 500, 60);
app.pointer("end", 500, 60);
failSave = true;
await app.file("save");
assert.ok(data.has("composer-draft-v1"), "failed save should store a recovery draft");
failSave = false;
const projectKeys = [...data.keys()].filter(key => key.startsWith("composer-project-")).length;
const reopened = await launch();
assert.match(reopened.find("statusBar").label, /Recovered an unsaved draft/);
await reopened.file("save");
assert.equal([...data.keys()].filter(key => key.startsWith("composer-project-")).length, projectKeys,
  "recovered draft must reuse its project identity");

data.clear();
const touch = await launch(390);
assert.equal(touch.roll().width, 372);
assert.equal(touch.roll().height, 426);
touch.pointer("start");
touch.pointer("start", 170, 150, 2, 2);
touch.pointer("end", 140, 150, 1, 1);
touch.pointer("end", 170, 150, 2, 0);
assert.equal(touch.noteCount(), 0, "second touch must cancel an in-progress note");
assert.ok(touch.find("panel", "Notes"), "touch layout must retain note entry controls");

data.clear();
const exchange = await launch();
const score = blank("Roundtrip");
score.tempo = [{ tick:0, bpm:108 }, { tick:1920, bpm:132 }];
score.tracks[0].instrument = 73;
score.tracks[0].notes = [{ id:uid(), tick:0, duration:480, pitch:72, velocity:90 }];
score.tracks.push({ ...blank().tracks[0], id:"test-drums", name:"Drums", percussion:true,
  notes:[{ id:uid(), tick:0, duration:120, pitch:36, velocity:100 }] });
nextFile = { name:"Roundtrip.mid", bytes:Array.from(writeMidi(score)) };
await exchange.file("import");
assert.match(exchange.find("text").label, /Roundtrip/);
await exchange.file("save");
const importedId = [...data.entries()].find(([key, value]) =>
  key.startsWith("composer-project-") && value.name === "Roundtrip")?.[0];
assert.ok(importedId);
await exchange.file("new");
await exchange.file("save");
exchange.tab(3);
exchange.find("select", "Saved compositions").callback({ value:importedId.slice("composer-project-".length) });
await flush();
await exchange.transport("play");
assert.ok(exchange.stats().midiLoads > 0);
await exchange.file("export");
const exported = readMidiDetailed(downloads.at(-1).bytes).doc;
assert.deepEqual(exported.tempo.map(t => t.bpm), [108,132]);
assert.equal(exported.tracks.find(t => !t.percussion).instrument, 73);
assert.equal(exported.tracks.find(t => t.percussion).notes[0].pitch, 36);
assert.equal(downloads.at(-1).mime, "audio/midi");

data.clear();
const orphaned = await launch();
orphaned.pointer("start");
orphaned.pointer("end");
failIndexOnly = true;
await orphaned.file("save");
assert.equal([...data.keys()].filter(key => key.startsWith("composer-project-")).length, 1);
assert.equal(data.has("composer-index-v1"), false, "index failure should leave recoverable project data");
failIndexOnly = false;
const repaired = await launch();
assert.match(repaired.find("statusBar").label, /Recovered an unsaved draft/);
await repaired.file("save");
assert.equal(data.get("composer-index-v1").length, 1);
dialogChoice = "cancel";
await repaired.file("delete");
assert.equal([...data.keys()].filter(key => key.startsWith("composer-project-")).length, 1);
dialogChoice = "delete";
await repaired.file("delete");
assert.equal([...data.keys()].filter(key => key.startsWith("composer-project-")).length, 0);

data.clear();
const musical = await launch();
await musical.file("demo");
musical.tab(2);
musical.find("checkbox", "One-bar count-in before Play").callback({ value:true });
musical.find("checkbox", "Metronome during playback").callback({ value:true });
await musical.transport("play");
assert.ok(musical.stats().soundPlays > 0, "count-in should schedule audible clicks");
const clicksBeforeFrame = musical.stats().soundPlays;
await musical.frame(200);
assert.ok(musical.stats().soundPlays > clicksBeforeFrame, "metronome should schedule playback clicks");
assert.ok(musical.roll().commands.length < 600, "the piano roll should stay within the API command budget");

console.log("Composer editing, recovery, switching, and preview verified");
