import assert from "node:assert/strict";
import { blank, normalize, uid, writeMidi, readMidiDetailed } from
  "../stationnet/application_packages/midi-composer/midi.js";

const note = (id, tick, pitch, duration = 480) => ({ id, tick, pitch, duration, velocity:96 });
const restored = normalize({ ...blank(), tracks:[{
  ...blank().tracks[0], notes:[note("n1", 0, 60), note("n1", 480, 64)]
}] });
assert.equal(new Set(restored.tracks[0].notes.map(n => n.id)).size, 2, "duplicate stored IDs must be repaired");
assert.ok(!restored.tracks[0].notes.some(n => n.id === uid()), "new IDs must not collide with loaded IDs");

const score = blank("Round trip");
score.tempo = [{ tick:0, bpm:108 }, { tick:1920, bpm:132 }];
score.numerator = 6; score.denominator = 8;
score.tracks[0].instrument = 73;
score.tracks[0].volume = 88; score.tracks[0].pan = 42;
score.tracks[0].notes = [note(uid(), 0, 72, 720), note(uid(), 1440, 74, 240)];
score.tracks.push({ ...blank().tracks[0], id:"drums", name:"Drums", percussion:true,
  notes:[note(uid(), 0, 36, 120), note(uid(), 480, 38, 120)] });
const bytes = writeMidi(score);
const imported = readMidiDetailed(bytes);
assert.deepEqual(imported.warnings, []);
assert.equal(imported.doc.numerator, 6);
assert.equal(imported.doc.denominator, 8);
assert.deepEqual(imported.doc.tempo.map(t => t.bpm), [108,132]);
assert.equal(imported.doc.tracks.find(t => !t.percussion).instrument, 73);
assert.equal(imported.doc.tracks.find(t => !t.percussion).volume, 88);
assert.equal(imported.doc.tracks.find(t => !t.percussion).pan, 42);
assert.deepEqual(imported.doc.tracks.find(t => t.percussion).notes.map(n => n.pitch), [36,38]);

const sixteen = blank();
sixteen.tracks = Array.from({ length:16 }, (_, i) => ({ ...blank().tracks[0], id:`track-${i}`,
  notes:[note(uid(), 0, 60 + i)] }));
assert.throws(() => writeMidi(sixteen), /15 simultaneous melodic instruments/);
assert.throws(() => normalize({ ...blank(), tracks:[{ ...blank().tracks[0],
  notes:Array.from({ length:20001 }, (_, i) => note(`n-${i}`, i, 60)) }] }), /20,000 notes/);

const fifteen = { ...sixteen, tracks:sixteen.tracks.slice(0, 15) };
const legal = writeMidi(fifteen);
let p = 14, lastTrack;
while (p < legal.length) {
  const length = legal[p+4] * 16777216 + legal[p+5] * 65536 + legal[p+6] * 256 + legal[p+7];
  lastTrack = legal.slice(p, p + 8 + length); p += 8 + length;
}
const importedTooMany = Uint8Array.from([...legal, ...lastTrack]);
importedTooMany[10] = 0; importedTooMany[11] = 17;
assert.throws(() => readMidiDetailed(importedTooMany), /more than 15 melodic tracks/);

const controller = [0, 0xb0, 64, 127];
const trackStart = 14 + 8 + (bytes[18] * 16777216 + bytes[19] * 65536 + bytes[20] * 256 + bytes[21]);
const trackLength = bytes[trackStart+4] * 16777216 + bytes[trackStart+5] * 65536 +
  bytes[trackStart+6] * 256 + bytes[trackStart+7];
const end = trackStart + 8 + trackLength;
const expressive = Uint8Array.from([...bytes.slice(0, end-4), ...controller, ...bytes.slice(end-4)]);
const newLength = trackLength + controller.length;
for (let i = 0; i < 4; i++) expressive[trackStart+4+i] = (newLength >>> (24 - i*8)) & 255;
assert.match(readMidiDetailed(expressive).warnings.join(" "), /Controller changes/);

console.log("Composer MIDI identity, round trip, warnings, and limits verified");
