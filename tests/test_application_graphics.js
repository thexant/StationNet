"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function runtime() {
  const sent = [];
  const context = {
    __stationnetPackageFiles: { "assets/sheet.png": Buffer.from("image bytes").toString("base64") },
    postMessage: (value) => sent.push(value),
    addEventListener() {},
    atob: (value) => Buffer.from(value, "base64").toString("binary"),
    TextDecoder, Uint8Array, Promise, URL, Blob, setTimeout, clearTimeout,
    setInterval, clearInterval
  };
  context.self = context;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../stationnet/static/application_runtime.js"), "utf8"), context);
  return { ui: context.StationNet, sent };
}

test("packaged images register once and sprite frames crop a sheet", () => {
  const { ui, sent } = runtime();
  const image = ui.assets.image("assets/sheet.png");
  assert.equal(ui.assets.image("assets/sheet.png"), image);
  assert.equal(sent.filter((item) => item.type === "graphics-image").length, 1);
  const sheet = ui.graphics.sheet(image, 16, 24, { columns: 8 });
  const sprite = ui.graphics.sprite(sheet, 10, 40, 50, { width: 32, flipX: true });
  assert.equal(sprite.sx, 32);
  assert.equal(sprite.sy, 24);
  assert.equal(sprite.sw, 16);
  assert.equal(sprite.sh, 24);
  assert.equal(sprite.width, 32);
  assert.equal(sprite.flipX, true);
  assert.equal(ui.graphics.image(image, 0, 0, { sx: 4, sw: 8, opacity: .5 }).sx, 4);
  assert.throws(() => ui.assets.image("assets/missing.png"), /not found/);
});

test("tile arrays are registered once and viewports carry only camera data", () => {
  const { ui, sent } = runtime();
  const image = ui.assets.image("assets/sheet.png");
  ui.graphics.tileMap("level", [0, 1, -1, 2], {
    columns: 2, image, tileWidth: 16, tileHeight: 16, sheetColumns: 8
  });
  const layer = ui.graphics.tileLayer("level", 0, 0, 320, 180, { cameraX: 23 });
  assert.equal(layer.cameraX, 23);
  assert.equal(Object.hasOwn(layer, "tiles"), false);
  assert.equal(sent.filter((item) => item.type === "graphics-map").length, 1);
  assert.throws(() => ui.graphics.tileMap("bad", [1, 2, 3], { columns: 2 }), /rectangular/);
});

test("host renderer decodes once and draws only visible tiles with nearest-neighbor sampling", async () => {
  const calls = [];
  const drawing = {
    clearRect() {}, fillRect() {}, save() {}, restore() {}, beginPath() {},
    rect() {}, clip() {}, translate() {}, scale() {},
    drawImage(...args) { calls.push({ args, smooth: this.imageSmoothingEnabled }); }
  };
  const canvas = { width: 32, height: 32, dataset: {}, style: { setProperty() {} }, setAttribute() {}, getContext: () => drawing };
  const frame = { dataset: {}, contentWindow: {}, remove() {} };
  const surface = { querySelectorAll: () => [], addEventListener() {} };
  const status = {};
  const document = {
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
    setTimeout: () => 1, clearTimeout() {}, Blob, Uint8Array,
    atob: (value) => Buffer.from(value, "base64").toString("binary"),
    createImageBitmap: async () => ({ width: 32, height: 32, close() {} })
  };
  const source = fs.readFileSync(path.join(__dirname, "../stationnet/static/application_host.js"), "utf8")
    .replace(/\}\)\(\);\s*$/, "globalThis.__graphicsTest = { drawCanvas, registerImage, registerMap }; })();");
  vm.runInNewContext(source, context);
  const host = context.__graphicsTest;
  host.registerImage({ path: "assets/sheet.png", data: Buffer.from("image bytes").toString("base64") });
  await new Promise((resolve) => setImmediate(resolve));
  host.registerMap({ id: "level", image: "assets/sheet.png", columns: 2,
    tiles: [0, 1, 2, 3], tileWidth: 16, tileHeight: 16, sheetColumns: 2 });
  host.drawCanvas(canvas, { width: 32, height: 32, commands: [
    { type: "tilemap", map: "level", x: 0, y: 0, width: 16, height: 16, cameraX: 8 },
    { type: "image", image: "assets/sheet.png", x: 4, y: 5,
      sx: 16, sy: 0, sw: 16, sh: 16, width: 32, height: 32, flipX: true }
  ] });
  assert.equal(calls.length, 3);
  assert.deepEqual(calls[0].args.slice(1), [0, 0, 16, 16, -8, 0, 16, 16]);
  assert.deepEqual(calls[1].args.slice(1), [16, 0, 16, 16, 8, 0, 16, 16]);
  assert.equal(calls[2].smooth, false);
});
