import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { MidiPlaybackEngine } from "../stationnet/static/midi/engine.js";

test("MIDI renderer loads, seeks, resyncs and releases notes on track changes", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(4) });
  try {
    const events = new Map();
    const sequencer = {
      paused: true, midiData: null, currentTime: 0, isFinished: false,
      eventHandler: {
        addEvent: (name, id, fn) => events.set(`${name}:${id}`, fn),
        removeEvent: (name, id) => events.delete(`${name}:${id}`),
      },
      loadNewSongList() {
        queueMicrotask(() => {
          this.midiData = {};
          for (const [name, listener] of events) if (name.startsWith("songChange:")) listener();
        });
      },
      pause() { this.paused = true; },
      play() { this.paused = false; },
    };
    let stopped = 0;
    const context = { currentTime: 1 };
    const engine = new MidiPlaybackEngine(context, {}, "/static/midi");
    engine.initialize = async () => {
      engine.sequencer = sequencer;
      engine.synth = { stopAll: () => stopped++ };
      engine.gain = { gain: { setTargetAtTime() {} } };
    };
    await engine.sync("1:1", "/radio/manage/audio/1", 12.5);
    assert.equal(sequencer.currentTime, 12.5);
    assert.equal(sequencer.paused, false);
    sequencer.currentTime = 10;
    await engine.sync("1:1", "/radio/manage/audio/1", 13);
    assert.equal(sequencer.currentTime, 13);
    await engine.sync("2:2", "/radio/manage/audio/2", 3);
    assert.equal(sequencer.currentTime, 3);
    assert.ok(stopped >= 2);
    engine.stop();
    assert.equal(engine.key, null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("HTTP LAN fallback synthesizes sound without AudioWorklet", async () => {
  const originalFetch = globalThis.fetch;
  const soundFont = await readFile(new URL("../stationnet/static/midi/gm.sf2", import.meta.url));
  const events = Buffer.from([0, 0xc0, 0, 0, 0x90, 60, 100, 0x83, 0x60, 0x80, 60, 0, 0, 0xff, 0x2f, 0]);
  const midi = Buffer.concat([
    Buffer.from([0x4d, 0x54, 0x68, 0x64, 0, 0, 0, 6, 0, 0, 0, 1, 1, 0xe0]),
    Buffer.from([0x4d, 0x54, 0x72, 0x6b, 0, 0, 0, events.length]), events,
  ]);
  globalThis.fetch = async url => ({
    ok: true,
    arrayBuffer: async () => Uint8Array.from(String(url).endsWith("gm.sf2") ? soundFont : midi).buffer,
  });
  let audioNode;
  const context = {
    sampleRate: 44100,
    currentTime: 0,
    createGain: () => ({ connect() {}, gain: { setTargetAtTime() {} } }),
    createScriptProcessor: () => (audioNode = { connect() {}, onaudioprocess: null }),
  };
  try {
    const engine = new MidiPlaybackEngine(context, {}, "/static/midi");
    await engine.sync("1:1", "/radio/manage/audio/1", 0);
    let peak = 0;
    for (let frame = 0; frame < 6; frame++) {
      const channels = [new Float32Array(2048), new Float32Array(2048)];
      audioNode.onaudioprocess({ outputBuffer: { getChannelData: index => channels[index] } });
      for (const sample of channels[0]) peak = Math.max(peak, Math.abs(sample));
    }
    assert.ok(peak > 0, "fallback rendered silent audio");
    engine.stop();
  } finally {
    globalThis.fetch = originalFetch;
  }
});
