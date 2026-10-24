"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.join(__dirname, "..");
const tick = () => new Promise(setImmediate);
const key = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const day = (n) => { const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() + n); return key(d); };
function launch() {
  const sent = [], listeners = {};
  const context = { __stationnetPackageFiles: {}, postMessage: (m) => sent.push(m), addEventListener: (t, h) => { listeners[t] = h; }, atob: (v) => Buffer.from(v, "base64").toString("binary"), TextDecoder, Uint8Array, Promise, URL, Blob, setTimeout, clearTimeout, setInterval: () => 1, clearInterval, Date, Intl, Math };
  context.self = context;
  vm.runInNewContext(fs.readFileSync(path.join(root, "stationnet/static/application_runtime.js"), "utf8"), context);
  vm.runInNewContext(fs.readFileSync(path.join(root, "stationnet/application_packages/personal-calendar/main.js"), "utf8"), context);
  return { sent, listeners };
}
async function ready(app, stored = null) {
  await tick(); const req = app.sent.find((m) => m.type === "storage" && m.operation === "get");
  assert.equal(req.key, "calendar-v1"); assert.equal(req.scope, "user");
  app.listeners.message({ data: { protocol: "stationnet-app-v1", type: "storage-result", request: req.request, ok: true, value: stored } }); await tick();
}
const view = (app) => app.sent.filter((m) => m.type === "render").at(-1).components;
async function click(app, component, value, values = {}) {
  app.listeners.message({ data: { protocol: "stationnet-app-v1", type: "interaction", action: component.action, value, values } }); await tick();
}
async function saveClick(app, component, value, values = {}) {
  const count = app.sent.length; await click(app, component, value, values);
  const req = app.sent.slice(count).find((m) => m.type === "storage" && m.operation === "set");
  assert.ok(req, "change should be saved");
  app.listeners.message({ data: { protocol: "stationnet-app-v1", type: "storage-result", request: req.request, ok: true, value: true } }); await tick();
  return req.value;
}
test("busy month displays event titles and overflow; day agenda is ordered", async () => {
  const base = day(0), next = day(1), later = day(4);
  const stored = [
    { id: "a", title: "Team planning", date: base, start: "10:00", end: "11:00" },
    { id: "b", title: "Dentist", date: base, start: "14:00", end: "15:00" },
    { id: "c", title: "Groceries", date: base, allDay: true },
    { id: "d", title: "Review", date: next, start: "09:00", end: "09:30" },
    { id: "e", title: "Dinner", date: later, start: "18:00", end: "19:00" }
  ];
  const app = launch(); await ready(app, stored);
  const calendar = view(app).find((c) => c.type === "calendar");
  assert.ok(calendar); assert.ok([28, 35, 42].includes(calendar.days.length));
  const cell = calendar.days.find((d) => d.date === base);
  assert.equal(cell.events.length, 2); assert.equal(cell.extra, 1);
  assert.match(cell.events.join(" "), /Groceries/); assert.match(cell.events.join(" "), /Team planning/);
  assert.ok(calendar.days.find((d) => d.date === next).events.some((v) => v.includes("Review")));
  assert.ok(calendar.days.find((d) => d.date === later).events.some((v) => v.includes("Dinner")));
  assert.deepEqual(Array.from(view(app).find((c) => c.type === "panel").components.filter((c) => c.type === "menu"), (c) => c.items[0].label), ["Groceries", "Team planning", "Dentist"]);
  await click(app, calendar, next);
  assert.equal(view(app).find((c) => c.type === "panel").components.find((c) => c.type === "menu").items[0].label, "Review");
});
test("create, edit and delete recurring events in private account storage", async () => {
  const app = launch(); await ready(app);
  await click(app, view(app).find((c) => c.type === "panel").components.find((c) => c.type === "button"));
  let panel = view(app).find((c) => c.type === "panel");
  for (const name of ["eventAllDay", "eventRepeat", "eventNotes"]) assert.ok(panel.components.some((c) => c.name === name));
  let saved = await saveClick(app, panel.components.find((c) => c.type === "menu"), "save", { eventTitle: "Weekly check-in", eventDate: day(0), eventAllDay: false, eventStart: "09:30", eventEnd: "10:00", eventRepeat: "weekly", eventUntil: "", eventNotes: "Bring notes" });
  assert.equal(saved[0].repeat, "weekly");
  await click(app, view(app).find((c) => c.type === "calendar"), day(7));
  panel = view(app).find((c) => c.type === "panel");
  assert.equal(panel.components.find((c) => c.type === "menu").items[0].label, "Weekly check-in");
  await click(app, panel.components.find((c) => c.type === "menu"), "edit");
  panel = view(app).find((c) => c.type === "panel");
  saved = await saveClick(app, panel.components.find((c) => c.type === "menu"), "save", { eventTitle: "Updated check-in", eventDate: day(0), eventAllDay: true, eventStart: "09:30", eventEnd: "10:00", eventRepeat: "weekly", eventUntil: "", eventNotes: "" });
  assert.equal(saved[0].title, "Updated check-in"); assert.equal(saved[0].allDay, true);
  await click(app, view(app).find((c) => c.type === "panel").components.find((c) => c.type === "menu"), "edit");
  saved = await saveClick(app, view(app).find((c) => c.type === "panel").components.find((c) => c.type === "menu"), "delete");
  assert.equal(saved.length, 0);
});
test("a weekly occurrence can be skipped without deleting its series", async () => {
  const app = launch();
  await ready(app, [{ id: "weekly", title: "Training", date: day(0), start: "08:00", end: "09:00", repeat: "weekly" }]);
  if (!view(app).find((c) => c.type === "calendar").days.some((d) => d.date === day(7))) {
    await click(app, view(app).find((c) => c.type === "menu"), "next");
  }
  await click(app, view(app).find((c) => c.type === "calendar"), day(7));
  let agenda = view(app).find((c) => c.type === "panel");
  assert.equal(agenda.components.find((c) => c.type === "menu").items[0].label, "Training");
  const stored = await saveClick(app, agenda.components.find((c) => c.type === "menu"), "skip");
  assert.equal(stored.length, 1); assert.ok(stored[0].skips.includes(day(7)));
  assert.equal(view(app).find((c) => c.type === "panel").components.filter((c) => c.type === "menu").length, 0);
  if (!view(app).find((c) => c.type === "calendar").days.some((d) => d.date === day(14))) {
    await click(app, view(app).find((c) => c.type === "menu"), "next");
  }
  await click(app, view(app).find((c) => c.type === "calendar"), day(14));
  agenda = view(app).find((c) => c.type === "panel");
  assert.equal(agenda.components.find((c) => c.type === "menu").items[0].label, "Training");
});
test("daily agenda confirms deletion of an event", async () => {
  const app = launch(); await ready(app, [{ id: "appointment", title: "Appointment", date: day(0), start: "12:00", end: "13:00" }]);
  let agenda = view(app).find((c) => c.type === "panel");
  await click(app, agenda.components.find((c) => c.type === "menu"), "delete");
  agenda = view(app).find((c) => c.type === "panel");
  assert.equal(agenda.label, "Delete event?");
  const saved = await saveClick(app, agenda.components.find((c) => c.type === "menu"), "delete");
  assert.equal(saved.length, 0);
});

test("host renders a calendar day with overflow and forwards its selection", () => {
  const listeners = {};
  const element = (tagName) => ({
    tagName, children: [], attributes: {}, dataset: {},
    classList: { values: [], add(value) { this.values.push(value); } },
    append(...children) { this.children.push(...children); },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, handler) { this.listeners ??= {}; this.listeners[name] = handler; }
  });
  const frame = { dataset: { runtimeSrc: "/runtime" }, contentWindow: { postMessage(message) { listeners.interaction = message; } } };
  const surface = { querySelectorAll: () => [], addEventListener() {} };
  const status = {};
  const document = {
    createElement: element,
    querySelector(selector) {
      if (selector === "[data-app-runtime]") return frame;
      if (selector === "[data-app-surface]") return surface;
      if (selector === "[data-app-status]") return status;
      return null;
    },
    addEventListener() {}, hidden: false, hasFocus: () => true
  };
  const context = {
    document, window: {}, navigator: { maxTouchPoints: 0, userAgent: "Desktop" },
    crypto: { randomUUID: () => "test" }, addEventListener() {},
    setTimeout: () => 1, clearTimeout() {}
  };
  const source = fs.readFileSync(path.join(root, "stationnet/static/application_host.js"), "utf8")
    .replace(/\}\)\(\);\s*$/, "globalThis.__renderCalendar = renderComponent; })();");
  vm.runInNewContext(source, context);
  const calendar = context.__renderCalendar({ type: "calendar", month: "October 2026", action: "choose",
    days: [{ date: "2026-10-24", number: 24, selected: true, events: ["Meeting"], extra: 2 }] });
  const dayButton = calendar.children[0].children[7];
  assert.equal(dayButton.children[0].textContent, "24");
  assert.equal(dayButton.children[2].textContent, "+2 more");
  assert.match(dayButton.attributes["aria-label"], /Meeting, 2 more/);
  dayButton.listeners.click();
  assert.equal(listeners.interaction.action, "choose");
  assert.equal(listeners.interaction.value, "2026-10-24");
});
