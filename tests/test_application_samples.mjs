import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const protocol = "stationnet-app-v1";
const runtimeSource = readFileSync(new URL("../stationnet/static/application_runtime.js", import.meta.url), "utf8");
const hostSource = readFileSync(new URL("../stationnet/static/application_host.js", import.meta.url), "utf8");

function wav(bits, channels, data, rate = 8000) {
  const bytes = Buffer.alloc(44 + data.length);
  bytes.write("RIFF", 0); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write("WAVEfmt ", 8);
  bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(channels, 22);
  bytes.writeUInt32LE(rate, 24); bytes.writeUInt32LE(rate * channels * bits / 8, 28);
  bytes.writeUInt16LE(channels * bits / 8, 32); bytes.writeUInt16LE(bits, 34);
  bytes.write("data", 36); bytes.writeUInt32LE(data.length, 40); data.copy(bytes, 44);
  return bytes.toString("base64");
}

const mono = wav(8, 1, Buffer.from([0, 128, 255]));
const messages = [], workerListeners = new Map();
const worker = { __stationnetPackageFiles: { "beep.wav": mono },
  postMessage(message) { messages.push(message); },
  addEventListener(type, callback) { workerListeners.set(type, callback); },
  atob, TextDecoder, setTimeout, clearTimeout };
worker.self = worker;
vm.createContext(worker); vm.runInContext(runtimeSource, worker);
const sound = worker.StationNet.sound;
const loading = sound.load("beep.wav");
const request = messages.at(-1);
assert.equal(request.operation, "load");
assert.equal(request.data, mono);
workerListeners.get("message")({ data: { protocol, type: "sample-load-result", handle: request.handle, ok: true } });
const loaded = await loading;
assert.equal(loaded, request.handle);
const generated = vm.runInContext("StationNet.sound.createSample(new Int16Array([-32768, 0, 32767]), 8000)", worker);
assert.equal(messages.at(-1).format, "int16");
const voice = sound.playSample(loaded, { volume: .4, pan: -.5, loop: true, rate: 2 });
assert.equal(messages.at(-1).voiceId, voice);
sound.stopSample(voice);
assert.equal(messages.at(-1).operation, "stop");
assert.throws(() => sound.playSample("missing"), /Unknown sample/);
assert.throws(() => sound.playSample(generated, { pan: 2 }), /Invalid sample/);
assert.throws(() => vm.runInContext("StationNet.sound.createSample(new Float32Array([NaN]), 8000)", worker), /Float32 samples/);
assert.throws(() => vm.runInContext("StationNet.sound.createSample(new Int8Array([1]), 7999)", worker), /Sample rate/);
await assert.rejects(sound.load("missing.wav"), /bundled WAV/);

const hostListeners = new Map(), responses = [], buffers = [], sources = [];
const context = { state: "suspended", currentTime: 0, destination: {}, resume: async () => {},
  createBuffer(channels, frames, rate) {
    const data = Array.from({ length: channels }, () => new Float32Array(frames));
    const buffer = { channels, frames, rate, getChannelData(channel) { return data[channel]; }, data };
    buffers.push(buffer); return buffer;
  },
  createBufferSource() {
    const source = { playbackRate: {}, stopped: false, connect(node) { return node; },
      disconnect() {}, start() {}, stop() { this.stopped = true; }, addEventListener() {} };
    sources.push(source); return source;
  },
  createGain() { return { gain: {}, connect(node) { return node; }, disconnect() {} }; },
  createStereoPanner() { return { pan: {}, connect(node) { return node; }, disconnect() {} }; }
};
const frame = { dataset: {}, remove() {}, contentWindow: { postMessage(message) { responses.push(message); } } };
const surface = { clientWidth: 320, dataset: {}, addEventListener() {}, querySelectorAll() { return []; } };
const document = { hidden: false, hasFocus: () => true, addEventListener() {},
  querySelector(selector) { return selector === "[data-app-runtime]" ? frame :
    selector === "[data-app-surface]" ? surface : selector === "[data-app-status]" ? {} : null; } };
const host = { document, window: { AudioContext: class { constructor() { return context; } }, innerHeight: 300 },
  navigator: { maxTouchPoints: 0, userAgent: "test" },
  crypto: { randomUUID: () => "test-id" }, atob, Uint8Array, Int8Array, Int16Array, Float32Array,
  addEventListener(type, callback) { hostListeners.set(type, callback); },
  setTimeout() { return 1; }, clearTimeout() {} };
vm.runInNewContext(hostSource, host);
const dispatch = data => hostListeners.get("message")({ source: frame.contentWindow, data: { protocol, ...data } });
dispatch({ type: "sample", operation: "load", handle: "sample-1", path: "beep.wav", data: mono });
assert.equal(responses.at(-1).ok, true);
assert.deepEqual(Array.from(buffers.at(-1).data[0]), [-1, 0, 127 / 128]);
const stereo = Buffer.alloc(8);
stereo.writeInt16LE(-32768, 0); stereo.writeInt16LE(32767, 2);
stereo.writeInt16LE(0, 4); stereo.writeInt16LE(16384, 6);
dispatch({ type: "sample", operation: "load", handle: "sample-2", path: "stereo.wav", data: wav(16, 2, stereo) });
assert.equal(responses.at(-1).ok, true);
assert.deepEqual(Array.from(buffers.at(-1).data[0]), [-1, 0]);
assert.deepEqual(Array.from(buffers.at(-1).data[1]), [32767 / 32768, .5]);
dispatch({ type: "sample", operation: "load", handle: "sample-3", path: "bad.wav", data: Buffer.from("bad").toString("base64") });
assert.equal(responses.at(-1).ok, false);
const badFormat = Buffer.from(mono, "base64"); badFormat.writeUInt16LE(3, 20);
dispatch({ type: "sample", operation: "load", handle: "sample-3", path: "bad.wav", data: badFormat.toString("base64") });
assert.equal(responses.at(-1).ok, false);
dispatch({ type: "sample", operation: "create", handle: "sample-3", format: "float32",
  sampleRate: 8000, samples: new Float32Array([NaN]) });
dispatch({ type: "sample", operation: "play", handle: "sample-3", voiceId: "voice-1",
  volume: 1, pan: 0, loop: false, rate: 1 });
assert.equal(sources.length, 0);
const play = (id, extra = {}) => dispatch({ type: "sample", operation: "play", handle: "sample-1",
  voiceId: `voice-${id}`, volume: 1, pan: 0, loop: true, rate: 1, ...extra });
for (let id = 1; id <= 8; id++) play(id);
assert.equal(sources.length, 8);
play(9);
assert.equal(sources.length, 9);
assert.equal(sources[0].stopped, true);
play(10, { pan: 2 });
assert.equal(sources.length, 9);
dispatch({ type: "sample", operation: "stop", voiceId: "voice-2" });
assert.equal(sources[1].stopped, true);
dispatch({ type: "sound", operation: "stop" });
assert.ok(sources.every(source => source.stopped));
play(11);
hostListeners.get("pagehide")();
assert.equal(sources.at(-1).stopped, true);

const limitResponses = [];
const limitFrame = { dataset: {}, contentWindow: { postMessage(message) { limitResponses.push(message); } } };
// A fresh host instance checks the decoded size before it allocates an AudioBuffer.
const limitListeners = new Map(), before = buffers.length;
const limitHost = { ...host, document: { ...document, querySelector(selector) {
  return selector === "[data-app-runtime]" ? limitFrame :
    selector === "[data-app-surface]" ? surface : selector === "[data-app-status]" ? {} : null;
} }, addEventListener(type, callback) { limitListeners.set(type, callback); } };
vm.runInNewContext(hostSource, limitHost);
const limitDispatch = data => limitListeners.get("message")({ source: limitFrame.contentWindow, data: { protocol, ...data } });
limitDispatch({ type: "sample", operation: "load", handle: "sample-1", path: "large.wav",
  data: wav(8, 2, Buffer.alloc(524290)) });
assert.equal(limitResponses.at(-1).ok, false);
assert.match(limitResponses.at(-1).error, /2 MiB/);
assert.equal(buffers.length, before);
for (let id = 1; id <= 32; id++) limitDispatch({ type: "sample", operation: "create",
  handle: `sample-${id}`, format: "int8", sampleRate: 8000, samples: new Int8Array([id]) });
limitDispatch({ type: "sample", operation: "load", handle: "sample-33", path: "beep.wav", data: mono });
assert.equal(limitResponses.at(-1).ok, false);
assert.match(limitResponses.at(-1).error, /limit/);

console.log("Application sample tests passed");
