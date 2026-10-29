import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

process.env.TZ = "America/Edmonton";
const source = readFileSync(new URL("../stationnet/application_packages/clock/main.js", import.meta.url), "utf8");
const flush = () => new Promise(setImmediate);
const local = (year, month, day, hour, minute = 0, second = 0) =>
  new Date(year, month - 1, day, hour, minute, second).getTime();

async function launch({ saved = null, start = local(2026, 10, 29, 6, 59, 59), loadError = false, saveError = false } = {}) {
  let now = start, rendered = [], tick, visible, dialogChoice = "reset", failSaves = saveError;
  const writes = [], sounds = [], updates = new Map();
  class FakeDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const node = (type, data = {}) => ({ type, ...data });
  const ui = {
    storage: {
      async get() { if (loadError) throw Error("load failed"); return saved; },
      async set(_key, value) { if (failSaves) throw Error("save failed"); writes.push(JSON.parse(JSON.stringify(value))); return true; }
    },
    sound: { sequence(tones, options) { sounds.push({ tones, options }); }, stop() { sounds.push({ stop: true }); } },
    interval(fn) { tick = fn; return () => {}; },
    onVisibility(fn) { visible = fn; return () => {}; },
    onCleanup() {},
    render(...components) { rendered = components; },
    update(id, component) { updates.set(id, component); },
    async dialog() { return dialogChoice; },
    title(label) { return node("title", { label }); },
    heading(label) { return node("heading", { label }); },
    text(value, options = {}) { return node("text", { value, ...options }); },
    statusBar(value, options = {}) { return node("status", { value, ...options }); },
    canvas(width, height, commands, options = {}) { return node("canvas", { width, height, commands, ...options }); },
    tabs(labels, onSelect, options = {}) { return node("tabs", { labels, onSelect, ...options }); },
    button(label, onClick, options = {}) { return node("button", { label, onClick, ...options }); },
    menu(label, items, onSelect, options = {}) { return node("menu", { label, items, onSelect, ...options }); },
    panel(label, components, options = {}) { return node("panel", { label, components, ...options }); },
    row(components, options = {}) { return node("row", { components, ...options }); },
    table(headers, rows, options = {}) { return node("table", { headers, rows, ...options }); },
    select(name, label, items, options = {}) { return node("select", { name, label, items, ...options }); },
    input(name, label, options = {}) { return node("input", { name, label, ...options }); },
    numberInput(name, label, options = {}) { return node("number", { name, label, ...options }); },
    dateInput(name, label, options = {}) { return node("date", { name, label, ...options }); },
    timeInput(name, label, options = {}) { return node("time", { name, label, ...options }); },
    checkbox(name, label, options = {}) { return node("checkbox", { name, label, ...options }); },
    slider(name, label, options = {}) { return node("slider", { name, label, ...options }); }
  };
  let started;
  vm.runInNewContext(source, { StationNet: { start(fn) { started = fn(ui); } }, Date: FakeDate, Intl, console });
  await started;
  await flush();
  const walk = (nodes = rendered) => nodes.flatMap((item) => [item, ...walk(item.components || [])]);
  const find = (type, label) => walk().find((item) => item.type === type && (label == null || item.label === label));
  return {
    get rendered() { return rendered; }, get writes() { return writes; }, get sounds() { return sounds; },
    get updates() { return updates; }, walk, find,
    async tab(index) { find("tabs").onSelect({ value: String(index) }); await flush(); },
    async advance(at) { now = at; tick(); await flush(); },
    async resume(at) { now = at; visible({ value: "visible" }); await flush(); },
    setSaveError(value) { failSaves = value; }, setDialog(value) { dialogChoice = value; }
  };
}

function snapshot({ alarms = [], timers = [], stopwatch, worldZones } = {}) {
  return { format24: false, analog: true, alarms, timers,
    stopwatch: stopwatch || { elapsed: 0, startedAt: null, laps: [] },
    worldZones: worldZones || ["UTC", "Asia/Tokyo"] };
}
const alarm = (id, time = "07:00", repeat = "12345") =>
  ({ id, time, label: id, repeat, enabled: true, snoozeUntil: null });
const timer = (id, endAt, durationMs = 60000) =>
  ({ id, label: id, remainingMs: durationMs, durationMs, endAt, running: true });

{
  const app = await launch({ loadError: true });
  await app.advance(local(2026, 10, 29, 7, 1));
  assert.equal(app.writes.length, 0, "a failed load must never overwrite stored data");
  assert.match(app.find("status").value, /could not be loaded/);
  await app.tab(1);
  assert.equal(app.find("button", "New alarm").disabled, true);
}
{
  const app = await launch({ saveError: true });
  await app.tab(1);
  app.find("button", "New alarm").onClick();
  await app.find("menu", "Alarm form").onSelect({ value: "save", values: {
    alarmTime: "07:30", alarmLabel: "Wake", alarmMode: "weekdays" } });
  assert.match(app.find("status").value, /Could not save changes/);
  app.setSaveError(false);
  await app.find("menu", "Alarm actions").onSelect({ value: "toggle" });
  assert.equal(app.writes.at(-1).alarms[0].enabled, false);
  assert.match(app.find("status").value, /Changes saved/);
}
{
  const app = await launch();
  await app.tab(1);
  app.find("button", "New alarm").onClick();
  app.find("select", "Repeat").onChange({ values: {
    alarmTime: "08:15", alarmLabel: "Custom", alarmMode: "custom" } });
  assert.equal(app.walk().filter((item) => item.type === "checkbox").length, 7);
  await app.find("menu", "Alarm form").onSelect({ value: "save", values: {
    alarmTime: "08:15", alarmLabel: "Custom", alarmMode: "custom", day1: true, day3: true
  } });
  assert.equal(app.writes.at(-1).alarms[0].repeat, "13");
  const id = app.writes.at(-1).alarms[0].id;
  await app.find("menu", "Alarm actions").onSelect({ value: "edit" });
  await app.find("menu", "Alarm form").onSelect({ value: "save", values: {
    alarmTime: "08:30", alarmLabel: "Edited", alarmMode: "custom", day3: true
  } });
  assert.equal(app.writes.at(-1).alarms[0].id, id);
  assert.equal(app.writes.at(-1).alarms[0].repeat, "3");
}
{
  const app = await launch({ saved: snapshot({ alarms: [alarm("wake")],
    timers: [timer("tea", local(2026, 10, 29, 7))] }) });
  await app.advance(local(2026, 10, 29, 7));
  assert.equal(app.find("panel", "Active alerts (2)").components.length, 2);
  assert.equal(app.sounds.filter((item) => item.tones).length, 1, "simultaneous alerts share one cue");
  assert.equal(app.find("menu", "Timer: tea").items.length, 1, "timers cannot be snoozed");
  await app.find("menu", "Alarm: wake").onSelect({ value: "snooze" });
  assert.equal(app.find("panel", "Active alerts (1)").components.length, 1);
  assert.equal(app.writes.at(-1).alarms[0].enabled, true);
  await app.find("menu", "Timer: tea").onSelect({ value: "dismiss" });
  await app.resume(local(2026, 10, 29, 7, 5, 1));
  assert.equal(app.find("panel", "Active alerts (1)").components.length, 1);
  await app.find("menu", "Alarm: wake").onSelect({ value: "dismiss" });
  await app.advance(local(2026, 10, 29, 7, 6));
  assert.equal(app.find("panel", "Active alerts (1)"), undefined, "an alarm rings once per date");
}
{
  const end = local(2026, 10, 29, 7);
  const app = await launch({ saved: snapshot({ timers: [timer("old", end, 300000)] }),
    start: end + 3600000 });
  assert.equal(app.sounds.filter((item) => item.tones).length, 0, "old timer completions stay silent");
  await app.tab(2);
  assert.match(app.find("text", undefined).value, /24 saved|saved|Timers/);
  await app.find("menu", "Timer actions").onSelect({ value: "reset" });
  assert.equal(app.writes.at(-1).timers[0].remainingMs, 300000);
  await app.find("menu", "Timer actions").onSelect({ value: "restart" });
  assert.equal(app.writes.at(-1).timers[0].endAt, end + 3600000 + 300000);
}
{
  const start = local(2026, 10, 29, 7);
  const legacy = { id: "legacy", label: "Legacy", remainingMs: 300000, endAt: start + 300000, running: true };
  const app = await launch({ saved: snapshot({ timers: [legacy] }), start });
  await app.tab(2);
  await app.advance(start + 60000);
  await app.find("menu", "Timer actions").onSelect({ value: "pause" });
  assert.equal(app.writes.at(-1).timers[0].remainingMs, 240000);
  await app.advance(start + 120000);
  await app.find("menu", "Timer actions").onSelect({ value: "resume" });
  assert.equal(app.writes.at(-1).timers[0].endAt, start + 360000);
  await app.find("menu", "Timer actions").onSelect({ value: "reset" });
  assert.equal(app.writes.at(-1).timers[0].remainingMs, 300000, "legacy timer duration is migrated");
}
{
  const due = local(2026, 10, 29, 7);
  const app = await launch({ saved: snapshot({
    alarms: Array.from({ length: 24 }, (_, i) => alarm(`alarm${i}`)),
    timers: Array.from({ length: 24 }, (_, i) => timer(`timer${i}`, due))
  }) });
  await app.tab(2);
  await app.advance(due);
  assert.equal(app.find("panel", "Active alerts (48)").components[0].items.length, 72);
  assert.ok(app.walk().length <= 100, "all overlapping alerts fit beside maximum timer view");
  await app.advance(due + 11000);
  assert.equal(app.sounds.filter((item) => item.tones).length, 2, "the cue repeats while alerts are active");
  await app.advance(due + 61000);
  assert.equal(app.sounds.filter((item) => item.tones).length, 2, "the cue stops repeating after one minute");
}
{
  const start = local(2026, 10, 29, 12);
  const app = await launch({ start });
  await app.tab(3);
  await app.find("button", "Start").onClick();
  await app.advance(start + 1000);
  await app.find("button", "Lap").onClick();
  await app.advance(start + 2500);
  await app.find("button", "Lap").onClick();
  assert.deepEqual(Array.from(app.find("table").rows, (row) => Array.from(row)),
    [["2", "00:00:01.5", "00:00:02.5"], ["1", "00:00:01.0", "00:00:01.0"]]);
  app.setDialog("cancel");
  await app.find("button", "Reset").onClick();
  assert.equal(app.writes.at(-1).stopwatch.laps.length, 2, "cancel retains laps");
}
{
  const app = await launch({ saved: snapshot({ alarms: Array.from({ length: 24 }, (_, i) =>
    alarm(`a${i}`, `${String(i).padStart(2, "0")}:00`)) }) });
  await app.tab(1);
  assert.ok(app.walk().length <= 100, "maximum alarm view fits component budget");
  app.find("button", "New alarm").onClick();
  await app.find("menu", "Alarm form").onSelect({ value: "save", values: {
    alarmTime: "07:45", alarmMode: "once", alarmLabel: "extra" } });
  assert.match(app.updates.get("clock-status").value, /limit reached/);
  assert.equal(app.writes.length, 0);
}
{
  const due = local(2026, 10, 30, 7);
  const app = await launch({ saved: snapshot({
    timers: Array.from({ length: 24 }, (_, i) => timer(`t${i}`, due)),
    worldZones: ["UTC", "America/Edmonton", "America/Vancouver", "America/Los_Angeles",
      "America/Denver", "America/Chicago", "America/New_York", "America/Sao_Paulo",
      "Europe/London", "Europe/Paris", "Africa/Cairo", "Asia/Tokyo"]
  }) });
  await app.tab(2);
  assert.ok(app.walk().length <= 100, "maximum timer view fits component budget");
  assert.equal(app.find("button", "Start timer").disabled, true);
  assert.ok(app.find("menu", "Presets").items.every((item) => item.disabled));
  await app.tab(4);
  assert.equal(app.find("select", "Add a place"), undefined);
  assert.match(app.find("text").value, /12\/12 places/);
}
{
  const app = await launch({ saved: snapshot({ worldZones: ["UTC", "Asia/Tokyo", "UTC", "Bad/Zone"] }) });
  await app.tab(4);
  assert.equal(app.find("table").rows.length, 3, "invalid and duplicate zones are removed");
  app.find("button", "Compare").onClick({ values: { compareDate: "2026-03-08", compareTime: "02:30" } });
  assert.match(app.find("panel", "Compare a local date and time").components.find((item) => item.type === "text" && item.tone === "warning").value,
    /does not exist/);
  app.find("button", "Compare").onClick({ values: { compareDate: "2026-10-29", compareTime: "23:00" } });
  const result = app.find("panel", "Compare a local date and time").components.find((item) => item.type === "table");
  assert.ok(result.rows.find((row) => row[0] === "Tokyo")[3].includes("+1"));
}
{
  const app = await launch({ saved: snapshot({ alarms: [alarm("bad", "99:99")],
    timers: [{ id: "bad", remainingMs: -1, running: true }],
    stopwatch: { elapsed: -10, startedAt: Infinity, laps: [1000, -5, NaN] } }) });
  await app.tab(1);
  assert.equal(app.find("panel", "99:99 · bad"), undefined);
  await app.tab(2);
  assert.equal(app.find("panel", "bad"), undefined);
  await app.tab(3);
  assert.equal(app.find("table").rows.length, 1);
}

console.log("Clock application checks passed");
