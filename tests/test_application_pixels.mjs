import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const protocol = "stationnet-app-v1";
const runtimeSource = readFileSync(new URL("../stationnet/static/application_runtime.js", import.meta.url), "utf8");
const hostSource = readFileSync(new URL("../stationnet/static/application_host.js", import.meta.url), "utf8");
const png = Buffer.alloc(24);
Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(png);
png.write("IHDR", 12, "ascii");
png.writeUInt32BE(2, 16); png.writeUInt32BE(1, 20);

const runtimeMessages = [];
const runtimeListeners = new Map();
const worker = {
  __stationnetPackageFiles: { "assets/wall.png": png.toString("base64") },
  postMessage(message) { runtimeMessages.push(message); },
  addEventListener(type, callback) { runtimeListeners.set(type, callback); },
  atob, TextDecoder, Uint8Array, setTimeout, clearTimeout
};
worker.self = worker;
vm.runInNewContext(runtimeSource, worker);
const { graphics, assets } = worker.StationNet;

const a = graphics.framebuffer("a", 3, 2, { format: "indexed", transparentIndex: 0 });
const b = graphics.framebuffer("b", 3, 2, { format: "indexed" });
assert.equal(a.palette[3], 0);
a.pixels.set([1, 0, 2, 3, 4, 5]);
b.clear(9);
b.blit(a, 0, 0, 3, 2, 1, 0, { flipX: true });
assert.deepEqual(Array.from(b.pixels), [9, 2, 9, 9, 5, 4]);
b.copyFrom(a);
assert.deepEqual(Array.from(b.pixels), [1, 2, 2, 3, 4, 5]);
b.scroll(1, 0, 7);
assert.deepEqual(Array.from(b.pixels), [7, 1, 2, 7, 3, 4]);
b.blit(b, 0, 0, 2, 2, 1, 0);
assert.deepEqual(Array.from(b.pixels), [7, 7, 1, 7, 7, 3]);
assert.throws(() => b.blit(a, 0, 0, 2, 1, 0, 0, { colorKey: 300 }), /colorKey/);
assert.throws(() => b.scroll(Infinity, 0, 0), /Scroll offsets/);

const rgba = graphics.framebuffer("rgba", 3, 1);
rgba.clear([10, 20, 30, 255]);
const decoded = { width: 2, height: 1, pixels: Uint8Array.from([255, 0, 0, 255, 0, 0, 0, 0]) };
rgba.blit(decoded, 0, 0, 2, 1, 1, 0);
assert.deepEqual(Array.from(rgba.pixels), [10, 20, 30, 255, 255, 0, 0, 255, 10, 20, 30, 255]);
rgba.scroll(-1, 0, [1, 2, 3, 4]);
assert.deepEqual(Array.from(rgba.pixels), [255, 0, 0, 255, 10, 20, 30, 255, 1, 2, 3, 4]);
rgba.clear(0);
assert.deepEqual(Array.from(rgba.pixels), Array(12).fill(0));
a.palette.set([40, 50, 60, 255], 8);
rgba.blit(a, 0, 0, 3, 1, -1, 0);
assert.deepEqual(Array.from(rgba.pixels.slice(0, 8)), [0, 0, 0, 0, 40, 50, 60, 255]);
assert.throws(() => b.blit(decoded, 0, 0, 1, 1, 0, 0), /Indexed destinations/);

const pending = assets.decodeImage("assets/wall.png");
const request = runtimeMessages.find(message => message.type === "graphics-decode-image");
assert.equal(request.path, "assets/wall.png");
runtimeListeners.get("message")({ data: { protocol, type: "graphics-decode-image-result",
  request: request.request, path: request.path, ok: true, width: 2, height: 1,
  pixels: Uint8Array.from([1, 2, 3, 4, 5, 6, 7, 8]) } });
const first = await pending;
first.pixels[0] = 99;
const second = await assets.decodeImage("assets/wall.png");
assert.equal(second.pixels[0], 1);
assert.equal(runtimeMessages.filter(message => message.type === "graphics-decode-image").length, 1);
await assert.rejects(assets.decodeImage("assets/missing.png"));
await assert.rejects(assets.decodeImage("assets/wall.jpg"));

const hostListeners = new Map();
const hostResponses = [];
const frame = { dataset: {}, contentWindow: { postMessage(message) { hostResponses.push(message); } } };
const surface = { clientWidth: 320, dataset: {}, addEventListener() {}, querySelectorAll() { return []; } };
const status = {};
const document = {
  hidden: false, querySelector(selector) {
    return selector === "[data-app-runtime]" ? frame : selector === "[data-app-surface]" ? surface :
      selector === "[data-app-status]" ? status : null;
  },
  addEventListener() {}, hasFocus() { return true; },
  createElement(tag) {
    assert.equal(tag, "canvas");
    return { getContext() { return { drawImage() {}, getImageData() {
      return { data: Uint8ClampedArray.from([1, 2, 3, 4, 5, 6, 7, 8]) };
    } }; } };
  }
};
const host = { document, navigator: { maxTouchPoints: 0, userAgent: "test" },
  window: { innerHeight: 300 }, Blob, Uint8Array, Uint8ClampedArray, atob,
  crypto: { randomUUID: () => "test-id" },
  createImageBitmap: async (blob) => {
    const bytes = Buffer.from(await blob.arrayBuffer());
    return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20), close() {} };
  },
  addEventListener(type, callback) { hostListeners.set(type, callback); },
  setTimeout() { return 1; }, clearTimeout() {}
};
vm.runInNewContext(hostSource, host);
const hostMessage = hostListeners.get("message");
hostMessage({ source: frame.contentWindow, data: { protocol, type: "graphics-decode-image",
  request: "image-1", path: "assets/wall.png", data: png.toString("base64") } });
await new Promise(resolve => setImmediate(resolve));
const success = hostResponses.find(message => message.type === "graphics-decode-image-result");
assert.equal(success.ok, true);
assert.equal(success.width, 2);
assert.deepEqual(Array.from(success.pixels), [1, 2, 3, 4, 5, 6, 7, 8]);
hostMessage({ source: frame.contentWindow, data: { protocol, type: "graphics-decode-image",
  request: "image-2", path: "assets/again.png", data: Buffer.from("bad").toString("base64") } });
await new Promise(resolve => setImmediate(resolve));
assert.equal(hostResponses.at(-1).ok, false);
const oversized = Buffer.from(png);
oversized.writeUInt32BE(2049, 16);
hostMessage({ source: frame.contentWindow, data: { protocol, type: "graphics-decode-image",
  request: "image-3", path: "assets/large.png", data: oversized.toString("base64") } });
await new Promise(resolve => setImmediate(resolve));
assert.equal(hostResponses.at(-1).ok, false);
assert.match(hostResponses.at(-1).error, /pixel budget/);
const full = Buffer.from(png);
full.writeUInt32BE(2048, 16); full.writeUInt32BE(2048, 20);
hostMessage({ source: frame.contentWindow, data: { protocol, type: "graphics-image",
  path: "assets/atlas.png", data: full.toString("base64") } });
await new Promise(resolve => setImmediate(resolve));
hostMessage({ source: frame.contentWindow, data: { protocol, type: "graphics-decode-image",
  request: "image-4", path: "assets/full.png", data: full.toString("base64") } });
await new Promise(resolve => setImmediate(resolve));
assert.equal(hostResponses.at(-1).ok, false);
assert.match(hostResponses.at(-1).error, /pixel budget/);

console.log("Application pixel and image decode tests passed");
