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

test("bounded framebuffers transfer snapshots and support palette-only updates", () => {
  const { ui, sent } = runtime();
  const fb = ui.graphics.framebuffer("scene", 320, 200, { format: "indexed" });
  assert.equal(fb.pixels.length, 64000);
  fb.fillRect(1, 1, 3, 2, 7);
  assert.equal(fb.pixels[321], 7);
  fb.palette.set([20, 30, 40, 255], 7 * 4);
  fb.present();
  const first = sent.find((item) => item.type === "graphics-frame");
  fb.pixel(1, 1, 8);
  assert.equal(first.pixels[321], 7);
  fb.present({ paletteOnly: true });
  assert.equal(sent.at(-1).pixels, null);
  assert.equal(ui.graphics.frame(fb, 0, 0, 640, 400).buffer, "scene");
  assert.throws(() => ui.graphics.framebuffer("large", 641, 400), /limit/);
  assert.throws(() => ui.graphics.framebuffer("scene", 1, 1), /unique/);
  ui.graphics.framebuffer("other", 640, 400);
  assert.throws(() => ui.graphics.framebuffer("excess", 640, 400), /limit/);
});

test("RGBA framebuffer writes opaque pixels and preserves full-frame bytes", () => {
  const { ui, sent } = runtime();
  const fb = ui.graphics.framebuffer("rgba", 2, 2);
  fb.pixel(0, 0, [3, 4, 5]);
  fb.fillRect(1, 0, 1, 2, [10, 20, 30, 40]);
  fb.present();
  assert.deepEqual(Array.from(sent.at(-1).pixels), [3, 4, 5, 255, 10, 20, 30, 40,
    0, 0, 0, 0, 10, 20, 30, 40]);
  assert.throws(() => fb.present({ paletteOnly: true }), /indexed/);
});

test("host renderer decodes once and draws only visible tiles with nearest-neighbor sampling", async () => {
  const calls = [];
  const drawing = {
    clearRect() {}, fillRect() {}, save() {}, restore() {}, beginPath() {},
    rect() {}, clip() {}, translate() {}, scale() {},
    putImageData(value) { calls.push({ imageData: value }); },
    drawImage(...args) { calls.push({ args, smooth: this.imageSmoothingEnabled }); }
  };
  const canvas = { width: 32, height: 32, dataset: {}, style: { setProperty() {} }, setAttribute() {}, getContext: () => drawing };
  const frame = { dataset: {}, contentWindow: {}, remove() {} };
  const surface = { querySelectorAll: () => [], addEventListener() {} };
  const status = {};
  const document = {
    createElement(name) { return name === "canvas" ? { width: 0, height: 0, getContext: () => drawing } : {}; },
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
    setTimeout: () => 1, clearTimeout() {}, Blob, Uint8Array, Uint8ClampedArray,
    ImageData: class { constructor(data, width, height) { this.data = data; this.width = width; this.height = height; } },
    atob: (value) => Buffer.from(value, "base64").toString("binary"),
    createImageBitmap: async () => ({ width: 32, height: 32, close() {} })
  };
  const source = fs.readFileSync(path.join(__dirname, "../stationnet/static/application_host.js"), "utf8")
    .replace(/\}\)\(\);\s*$/, "globalThis.__graphicsTest = { drawCanvas, registerImage, registerMap, registerFrame }; })();");
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
  const indices = new Uint8Array(4); indices[0] = 1;
  const palette = new Uint8Array(1024); palette.set([10, 20, 30, 255], 4);
  host.registerFrame({ id: "buffer", width: 2, height: 2, format: "indexed", pixels: indices, palette });
  assert.deepEqual(Array.from(calls.at(-1).imageData.data.slice(0, 4)), [10, 20, 30, 255]);
  host.registerFrame({ id: "buffer", width: 2, height: 2, format: "indexed", pixels: null,
    palette: new Uint8Array(1024).fill(80) });
  assert.equal(calls.at(-1).imageData.data[0], 80);
  host.drawCanvas(canvas, { width: 32, height: 32, commands: [{ type: "framebuffer", buffer: "buffer", x: 0, y: 0, width: 32, height: 32 }] });
  assert.equal(calls.at(-1).smooth, false);
  const count = calls.length;
  host.registerFrame({ id: "invalid", width: 641, height: 2, format: "rgba", pixels: new Uint8Array(641 * 2 * 4), palette: null });
  assert.equal(calls.length, count);
});
