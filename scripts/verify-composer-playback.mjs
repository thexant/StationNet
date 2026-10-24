import { readFile } from "node:fs/promises";
import { MidiPlaybackEngine } from "../stationnet/static/midi/engine.js";
import { blank, writeMidi } from "../stationnet/application_packages/midi-composer/midi.js";
import assert from "node:assert/strict";

const font = await readFile(new URL("../stationnet/static/midi/gm.sf2", import.meta.url));
const oldFetch = globalThis.fetch;
globalThis.fetch = async () => ({ ok: true, arrayBuffer: async () => Uint8Array.from(font).buffer });
let audioNode;
const context = {
  sampleRate: 44100, currentTime: 0,
  audioWorklet: { addModule: () => { throw new Error("Composer used the worklet path"); } },
  createGain: () => ({ connect() {}, gain: { setTargetAtTime() {} } }),
  createScriptProcessor: () => (audioNode = { connect() {}, onaudioprocess: null }),
};
try {
  const engine = new MidiPlaybackEngine(context, {}, "/static/midi", () => {},
    { preferScriptProcessor: true });
  const score = blank();
  score.tracks[0].notes = [{ id: "a", tick: 0, duration: 960, pitch: 60, velocity: 100 }];
  await engine.loadBinary(writeMidi(score).buffer);
  engine.seek(0);
  engine.play();
  let peak = 0;
  for (let i = 0; i < 8; i++) {
    const channels = [new Float32Array(2048), new Float32Array(2048)];
    audioNode.onaudioprocess({ outputBuffer: { getChannelData: channel => channels[channel] } });
    for (const sample of channels[0]) peak = Math.max(peak, Math.abs(sample));
  }
  assert.ok(peak > 0.01, "Composer MIDI playback was silent");
  assert.ok(engine.position > 0, "Composer MIDI playhead did not advance");
  console.log("Composer MIDI playback: audio and playhead verified");
  engine.stop();
} finally { globalThis.fetch = oldFetch; }
