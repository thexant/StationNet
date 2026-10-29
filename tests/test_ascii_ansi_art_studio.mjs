import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import * as art from "../stationnet/application_packages/ascii_ansi_art_studio/art.js";

const source = readFileSync(new URL("../stationnet/application_packages/ascii_ansi_art_studio/main.js", import.meta.url), "utf8");
const colored = art.parseArt("\u001b[1;31;44mR\u001b[22mN\u001b[91;103m!", "ansi");
assert.deepEqual(colored.cells[0].slice(0, 3), [
  { char: "R", fg: 9, bg: 4 }, { char: "N", fg: 1, bg: 4 }, { char: "!", fg: 9, bg: 11 }
]);
assert.throws(() => art.parseArt("\u001b[2Jbad", "ansi"), /Unsupported ANSI cursor/);
assert.throws(() => art.parseArt("\u001b[31m", "text"), /Unsupported ANSI/);
const roundTrip = art.parseArt(art.ansiText(colored.cells), "ansi");
assert.deepEqual(roundTrip.cells, colored.cells);
const clipped = art.parseArt("X".repeat(81) + "\n" + Array(50).fill("Y").join("\n"), "text");
assert.equal(clipped.width, 80);
assert.equal(clipped.height, 50);
assert.equal(clipped.clipped, true);
const payload = art.projectPayload("Test", colored.cells);
assert.deepEqual(art.validateProject(payload).cells, colored.cells);
assert.throws(() => art.validateProject({ ...payload, fg: [[-1, 0, 0, 0]] }), /invalid/);
assert.throws(() => art.validateProject({ ...payload, chars: [] }), /invalid/);

function harness(storage = new Map(), choice = "yes") {
  let tree = [], keyHandler, timer, clipboard = "", downloaded = [], ready;
  const make = (type, fields = {}) => ({ type, ...fields });
  const ui = {
    modules: { load: async () => art },
    title: value => make("title", { value }), heading: value => make("heading", { value }),
    text: (value, options = {}) => make("text", { value, id: options.id }),
    statusBar: (value, options = {}) => make("status", { value, id: options.id }),
    preformatted: (value, options = {}) => make("pre", { value, id: options.id }),
    input: (name, label, options = {}) => make("input", { name, label, options }),
    numberInput: (name, label, options = {}) => make("number", { name, label, options }),
    textarea: (name, label, options = {}) => make("textarea", { name, label, options }),
    button: (label, onClick, options = {}) => make("button", { label, onClick, options }),
    select: (name, label, choices, options = {}) => make("select", { name, label, choices, options }),
    radio: (name, label, choices, options = {}) => make("radio", { name, label, choices, options, id: options.id }),
    row: (components, options = {}) => make("row", { components, id: options.id }),
    panel: (label, components, options = {}) => make("panel", { label, components, id: options.id }),
    tabs: (labels, onSelect, options = {}) => make("tabs", { labels, onSelect, id: options.id }),
    menuBar: (menus, onSelect) => make("menu-bar", { menus, onSelect }),
    cellGrid: (rows, options = {}) => make("cell-grid", { rows, options, id: options.id }),
    dialog: async (title) => title === "Restore working draft?" ? choice : "yes",
    onKey: handler => { keyHandler = handler; },
    timeout: callback => { timer = callback; return () => { timer = null; }; },
    render: (...components) => { tree = components; },
    update: (id, replacement) => {
      const replace = nodes => nodes.map(node => {
        if (node.id === id) return replacement;
        if (node.components) return { ...node, components: replace(node.components) };
        return node;
      });
      tree = replace(tree);
    },
    storage: {
      get: async key => storage.get(key) ?? null,
      set: async (key, value) => { storage.set(key, structuredClone(value)); return true; },
      delete: async key => storage.delete(key)
    },
    clipboard: { writeText: async value => { clipboard = value; return true; } },
    download: {
      text: async (name, value) => { downloaded.push({ name, value }); return true; },
      cellImage: async (name, rows) => { downloaded.push({ name, rows }); return true; }
    },
    files: { open: async () => { throw new Error("No file supplied"); } }
  };
  vm.runInNewContext(source, {
    StationNet: { start(callback) { ready = callback(ui); } },
    Date, TextDecoder, Uint8Array, console
  });
  const all = () => {
    const result = [];
    const walk = nodes => nodes.forEach(node => {
      result.push(node);
      if (node.components) walk(node.components);
    });
    walk(tree); return result;
  };
  return {
    ready: () => ready,
    find: predicate => all().find(predicate),
    key: event => keyHandler({ phase: "down", key: "", code: "", ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, values: {}, ...event }),
    flushDraft: async () => { if (timer) { const action = timer; timer = null; await action(); } },
    storage, clipboard: () => clipboard, downloaded: () => downloaded
  };
}

const session = harness();
await session.ready();
const grid = () => session.find(node => node.id === "art-canvas");
session.key({ key: "u", code: "KeyU" });
session.key({ key: "u", code: "KeyU", phase: "up" });
assert.equal(grid().rows[0][0].char, "u");
assert.equal(grid().rows[0][1].char, " ");
session.key({ key: "z", code: "KeyZ", ctrlKey: true });
assert.equal(grid().rows[0][0].char, " ");
session.key({ key: "z", code: "KeyZ", ctrlKey: true, shiftKey: true });
assert.equal(grid().rows[0][0].char, "u");
grid().options.onCell({ phase: "start", x: 1, y: 0, values: {} });
grid().options.onCell({ phase: "move", x: 4, y: 0, values: {} });
grid().options.onCell({ phase: "end", x: 4, y: 0, values: {} });
assert.equal(grid().rows[0][4].char, "#");
session.key({ key: "z", code: "KeyZ", ctrlKey: true });
assert.equal(grid().rows[0][1].char, " ");
assert.equal(grid().rows[0][4].char, " ");
assert.equal(grid().rows[0][0].char, "u");
session.find(node => node.name === "tool").options.onChange({ value: "rectangle", values: {} });
grid().options.onCell({ phase: "start", x: 1, y: 1, values: {} });
grid().options.onCell({ phase: "end", x: 1, y: 1, values: {} });
grid().options.onCell({ phase: "start", x: 3, y: 3, values: {} });
grid().options.onCell({ phase: "end", x: 3, y: 3, values: {} });
assert.equal(grid().rows[1][1].char, "#");
assert.equal(grid().rows[2][2].char, " ");
session.find(node => node.name === "tool").options.onChange({ value: "fill", values: {} });
grid().options.onCell({ phase: "start", x: 2, y: 2, values: {} });
grid().options.onCell({ phase: "end", x: 2, y: 2, values: {} });
assert.equal(grid().rows[2][2].char, "#");
session.key({ key: "z", code: "KeyZ", ctrlKey: true });
assert.equal(grid().rows[2][2].char, " ");
session.find(node => node.name === "tool").options.onChange({ value: "text", values: { stampText: "HI" } });
grid().options.onCell({ phase: "start", x: 5, y: 2, values: { stampText: "HI" } });
assert.equal(grid().rows[2][5].char, "H");
assert.equal(grid().rows[2][6].char, "I");
session.key({ key: "z", code: "KeyZ", ctrlKey: true });
assert.equal(grid().rows[2][5].char, " ");
await session.flushDraft();
assert.equal(session.storage.get("draft").payload.chars[0][0], "u");

const restored = harness(session.storage, "restore");
await restored.ready();
assert.equal(restored.find(node => node.id === "art-canvas").rows[0][0].char, "u");
await restored.find(node => node.type === "button" && node.label === "Save").onClick({ values: { projectName: "Alpha" } });
assert.equal(restored.storage.get("project:alpha").chars[0][0], "u");
restored.key({ key: "b", code: "KeyB" });
await restored.find(node => node.type === "button" && node.label === "Save As").onClick({ values: { projectName: "Beta" } });
assert.equal(restored.storage.get("project:alpha").chars[0][0], "u");
assert.equal(restored.storage.get("project:beta").chars[0][0], "b");
restored.find(node => node.name === "savedProject").options.onChange({ value: "alpha" });
restored.key({ key: "c", code: "KeyC" });
await restored.find(node => node.type === "button" && node.label === "Save").onClick({ values: { projectName: "Beta" } });
assert.equal(restored.storage.get("project:beta").chars[0][1], "c");
assert.equal(restored.storage.get("project:alpha").chars[0][1], " ");

const damaged = new Map([["projects", [{ id: "bad", name: "Bad" }]], ["project:bad", { ...payload, chars: [] }]]);
const broken = harness(damaged);
await broken.ready();
await broken.find(node => node.type === "button" && node.label === "Load selected").onClick();
assert.match(broken.find(node => node.id === "studio-status").value, /Load failed/);
assert.equal(broken.find(node => node.id === "art-canvas").rows[0][0].char, " ");
console.log("ASCII/ANSI Art Studio checks passed");
