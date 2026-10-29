export const PPQ = 480;
export const GM = (`Acoustic Grand Piano|Bright Acoustic Piano|Electric Grand Piano|Honky-tonk Piano|Electric Piano 1|Electric Piano 2|Harpsichord|Clavinet|Celesta|Glockenspiel|Music Box|Vibraphone|Marimba|Xylophone|Tubular Bells|Dulcimer|Drawbar Organ|Percussive Organ|Rock Organ|Church Organ|Reed Organ|Accordion|Harmonica|Tango Accordion|Acoustic Guitar (nylon)|Acoustic Guitar (steel)|Electric Guitar (jazz)|Electric Guitar (clean)|Electric Guitar (muted)|Overdriven Guitar|Distortion Guitar|Guitar Harmonics|Acoustic Bass|Electric Bass (finger)|Electric Bass (pick)|Fretless Bass|Slap Bass 1|Slap Bass 2|Synth Bass 1|Synth Bass 2|Violin|Viola|Cello|Contrabass|Tremolo Strings|Pizzicato Strings|Orchestral Harp|Timpani|String Ensemble 1|String Ensemble 2|SynthStrings 1|SynthStrings 2|Choir Aahs|Voice Oohs|Synth Voice|Orchestra Hit|Trumpet|Trombone|Tuba|Muted Trumpet|French Horn|Brass Section|SynthBrass 1|SynthBrass 2|Soprano Sax|Alto Sax|Tenor Sax|Baritone Sax|Oboe|English Horn|Bassoon|Clarinet|Piccolo|Flute|Recorder|Pan Flute|Blown Bottle|Shakuhachi|Whistle|Ocarina|Lead 1 (square)|Lead 2 (sawtooth)|Lead 3 (calliope)|Lead 4 (chiff)|Lead 5 (charang)|Lead 6 (voice)|Lead 7 (fifths)|Lead 8 (bass + lead)|Pad 1 (new age)|Pad 2 (warm)|Pad 3 (polysynth)|Pad 4 (choir)|Pad 5 (bowed)|Pad 6 (metallic)|Pad 7 (halo)|Pad 8 (sweep)|FX 1 (rain)|FX 2 (soundtrack)|FX 3 (crystal)|FX 4 (atmosphere)|FX 5 (brightness)|FX 6 (goblins)|FX 7 (echoes)|FX 8 (sci-fi)|Sitar|Banjo|Shamisen|Koto|Kalimba|Bag pipe|Fiddle|Shanai|Tinkle Bell|Agogo|Steel Drums|Woodblock|Taiko Drum|Melodic Tom|Synth Drum|Reverse Cymbal|Guitar Fret Noise|Breath Noise|Seashore|Bird Tweet|Telephone Ring|Helicopter|Applause|Gunshot`).split("|");
let nextId = 1;
const usedNoteIds = new Set();
export function uid() {
  let id;
  do { id = `n${nextId++}`; } while (usedNoteIds.has(id));
  usedNoteIds.add(id);
  return id;
}
export function blank(name = "Untitled composition") {
  return { name, bpm: 120, numerator: 4, denominator: 4, tempo: [{ tick: 0, bpm: 120 }],
    tracks: [{ id: "t1", name: "Piano", instrument: 0, volume: 100, pan: 64, mute: false, solo: false, percussion: false, notes: [] }] };
}
export function clone(value) { return JSON.parse(JSON.stringify(value)); }
export function normalize(raw) {
  if (!raw || !Array.isArray(raw.tracks)) throw new Error("Invalid composition.");
  if (raw.tracks.length > 32) throw new Error("A composition can contain at most 32 tracks.");
  if (Array.isArray(raw.tempo) && raw.tempo.length > 1000) throw new Error("A composition can contain at most 1,000 tempo changes.");
  const doc = blank(String(raw.name || "Untitled composition").slice(0, 100));
  doc.numerator = Math.max(1, Math.min(16, Math.trunc(raw.numerator) || 4));
  doc.denominator = [2, 4, 8, 16].includes(raw.denominator) ? raw.denominator : 4;
  doc.tempo = (Array.isArray(raw.tempo) ? raw.tempo : []).filter(e => e && Number.isFinite(e.tick) && Number.isFinite(e.bpm))
    .map(e => ({ tick: Math.max(0, Math.round(e.tick)), bpm: Math.max(20, Math.min(300, Math.round(e.bpm))) })).sort((a,b) => a.tick-b.tick);
  if (!doc.tempo.length || doc.tempo[0].tick !== 0) doc.tempo.unshift({ tick: 0, bpm: Math.max(20, Math.min(300, Math.round(raw.bpm) || 120)) });
  doc.bpm = doc.tempo[0].bpm;
  const seenIds = new Set();
  doc.tracks = raw.tracks.map((t,i) => {
    if (Array.isArray(t.notes) && t.notes.length > 20000) throw new Error(`Track ${i + 1} has more than 20,000 notes.`);
    return ({
    id: String(t.id || `t${i+1}`).slice(0,40), name: String(t.name || `Track ${i+1}`).slice(0,80),
    instrument: Math.max(0, Math.min(127, Math.trunc(t.instrument) || 0)),
    volume: Math.max(0, Math.min(127, Math.trunc(t.volume ?? 100))), pan: Math.max(0, Math.min(127, Math.trunc(t.pan ?? 64))),
    mute: Boolean(t.mute), solo: Boolean(t.solo), percussion: Boolean(t.percussion),
    notes: (Array.isArray(t.notes) ? t.notes : []).filter(n => n && [n.tick,n.duration,n.pitch,n.velocity].every(Number.isFinite))
      .map(n => {
        const candidate = String(n.id || "").slice(0, 80);
        const id = candidate && !seenIds.has(candidate) ? candidate : uid();
        seenIds.add(id);
        usedNoteIds.add(id);
        return { id, tick: Math.max(0, Math.round(n.tick)), duration: Math.max(1, Math.round(n.duration)),
          pitch: Math.max(0, Math.min(127, Math.round(n.pitch))), velocity: Math.max(1, Math.min(127, Math.round(n.velocity))) };
      })
  }); });
  if (!doc.tracks.length) doc.tracks = blank().tracks;
  return doc;
}
export function tickSeconds(doc, tick) {
  let seconds = 0, previous = 0, bpm = doc.tempo[0].bpm;
  for (const item of doc.tempo.slice(1)) {
    if (item.tick >= tick) break;
    seconds += (item.tick - previous) * 60 / (PPQ * bpm); previous = item.tick; bpm = item.bpm;
  }
  return seconds + (tick - previous) * 60 / (PPQ * bpm);
}
export function secondsTick(doc, seconds) {
  let previous = 0, elapsed = 0, bpm = doc.tempo[0].bpm;
  for (const item of doc.tempo.slice(1)) {
    const segment = (item.tick - previous) * 60 / (PPQ * bpm);
    if (elapsed + segment >= seconds) break;
    elapsed += segment; previous = item.tick; bpm = item.bpm;
  }
  return Math.max(0, Math.round(previous + (seconds - elapsed) * PPQ * bpm / 60));
}
function vlq(value) {
  value = Math.max(0, Math.round(value)); const out = [value & 127];
  while (value >>= 7) out.unshift((value & 127) | 128);
  return out;
}
function chunk(events) {
  events.sort((a,b) => a.tick-b.tick || a.order-b.order);
  const data = []; let previous = 0;
  for (const event of events) { data.push(...vlq(event.tick-previous), ...event.bytes); previous = event.tick; }
  data.push(0,255,47,0);
  return [77,84,114,107,(data.length>>>24)&255,(data.length>>>16)&255,(data.length>>>8)&255,data.length&255,...data];
}
function metaText(kind, value) {
  const bytes = Array.from(new TextEncoder().encode(String(value))).slice(0, 200);
  return [255,kind,...vlq(bytes.length),...bytes];
}
export function writeMidi(input) {
  const doc = normalize(input); const tracks = [];
  const conductor = [];
  for (const t of doc.tempo) {
    const micro = Math.round(60000000/t.bpm);
    conductor.push({ tick:t.tick, order:0, bytes:[255,81,3,(micro>>>16)&255,(micro>>>8)&255,micro&255] });
  }
  conductor.push({ tick:0, order:1, bytes:[255,88,4,doc.numerator,Math.log2(doc.denominator),24,8] });
  tracks.push(chunk(conductor));
  let melodic = 0;
  for (const t of doc.tracks) {
    const channel = t.percussion ? 9 : (melodic++ < 9 ? melodic-1 : melodic);
    if (channel > 15) throw new Error("MIDI supports at most 15 simultaneous melodic instruments.");
    const events = [
      {tick:0,order:0,bytes:metaText(3,t.name)},
      {tick:0,order:1,bytes:[0xc0|channel,t.instrument]},
      {tick:0,order:2,bytes:[0xb0|channel,7,t.volume]},
      {tick:0,order:3,bytes:[0xb0|channel,10,t.pan]}
    ];
    for (const n of t.notes) {
      events.push({tick:n.tick,order:10,bytes:[0x90|channel,n.pitch,n.velocity]});
      events.push({tick:n.tick+n.duration,order:5,bytes:[0x80|channel,n.pitch,0]});
    }
    tracks.push(chunk(events));
  }
  const header = [77,84,104,100,0,0,0,6,0,1,(tracks.length>>>8)&255,tracks.length&255,1,224];
  return Uint8Array.from([...header,...tracks.flat()]);
}
export function readMidi(data) { return readMidiDetailed(data).doc; }
export function readMidiDetailed(data) {
  const warnings = new Set();
  const b = data instanceof Uint8Array ? data : Uint8Array.from(data);
  if (b.length < 14 || b.length > 2*1024*1024 || String.fromCharCode(...b.slice(0,4)) !== "MThd") throw new Error("Invalid MIDI header.");
  const u16 = p => (b[p]<<8)|b[p+1]; const u32 = p => (b[p]*16777216+b[p+1]*65536+b[p+2]*256+b[p+3]);
  const format=u16(8), count=u16(10), division=u16(12);
  if (u32(4)!==6 || format>1 || count<1 || count>128 || (format===0 && count!==1) || !division || division&0x8000) throw new Error("Unsupported MIDI format.");
  const doc=blank(); doc.tracks=[]; doc.tempo=[]; let p=14, eventCount=0;
  const scale=t=>Math.round(t*PPQ/division);
  for (let k=0;k<count;k++) {
    if (p+8>b.length || String.fromCharCode(...b.slice(p,p+4))!=="MTrk") throw new Error("Missing MIDI track.");
    const end=p+8+u32(p+4); p+=8; if (end>b.length) throw new Error("Truncated MIDI track.");
    let tick=0,status=0,trackName=`Track ${k+1}`; const channels=new Map(); const active=new Map();
    const channel=c=>{ if (!channels.has(c)) channels.set(c,{id:`t${k+1}-${c}`,name:trackName, instrument:0,volume:100,pan:64,mute:false,solo:false,percussion:c===9,notes:[]}); return channels.get(c); };
    const variable=()=>{let n=0; for(let i=0;i<4;i++){if(p>=end)throw Error("Truncated MIDI value."); const v=b[p++]; n=(n<<7)|(v&127); if(!(v&128))return n;} throw Error("Invalid MIDI value.");};
    while(p<end) {
      tick+=variable(); if(++eventCount>200000)throw Error("MIDI has too many events.");
      if(p>=end)throw Error("Truncated MIDI event.");
      if(b[p]&128)status=b[p++]; else if(!status)throw Error("Invalid running status.");
      if(status===255) {
        if(p>=end)throw Error("Truncated meta event."); const type=b[p++],len=variable(); if(p+len>end)throw Error("Truncated meta event.");
        const v=b.slice(p,p+len); p+=len;
        if(type===3)trackName=new TextDecoder().decode(v).slice(0,80);
        if([1,2,5,6,7,89].includes(type))warnings.add("Text, lyrics, markers, and key signatures were omitted.");
        if(type===81 && len===3){const micro=(v[0]<<16)|(v[1]<<8)|v[2]; if(micro){
          const bpm=Math.round(60000000/micro);
          if(bpm<20||bpm>300)warnings.add("Tempos outside 20–300 BPM were clamped.");
          doc.tempo.push({tick:scale(tick),bpm:Math.max(20,Math.min(300,bpm))});
        }}
        if(type===88 && len>=2){if(tick===0){doc.numerator=v[0];doc.denominator=2**v[1];
          if(![2,4,8,16].includes(doc.denominator)||doc.numerator<1||doc.numerator>16)
            warnings.add("An unsupported time signature was adjusted to the editor's range.");
        }else warnings.add("Later time-signature changes are not editable and were omitted.");}
        if(type===47)break;
      } else if(status===240 || status===247) { const len=variable(); p+=len; if(p>end)throw Error("Truncated SysEx."); warnings.add("System Exclusive data was omitted."); }
      else if(status>=128 && status<240) {
        const op=status&240,c=status&15,one=b[p++]; if(one===undefined || one&128)throw Error("Invalid MIDI event.");
        const two=(op===192||op===208)?0:b[p++]; if(two===undefined || two&128)throw Error("Invalid MIDI event.");
        const t=channel(c);
        if(op===192){if(t.notes.length||tick>0)warnings.add("Instrument changes were reduced to one program per track.");t.instrument=one;}
        if(op===176 && one===7)t.volume=two;
        if(op===176 && one===10)t.pan=two;
        if(op===176 && (one===7||one===10) && tick>0)
          warnings.add("Volume and pan automation was reduced to one value per track.");
        if(op===176 && one!==7 && one!==10)warnings.add("Controller changes, including sustain, were omitted.");
        if(op===160 || op===208 || op===224)warnings.add("Aftertouch and pitch bends were omitted.");
        if(op===144 && two>0){const key=`${c}:${one}`,stack=active.get(key)||[];stack.push({tick:scale(tick),velocity:two});active.set(key,stack);}
        if(op===128 || op===144 && two===0){const stack=active.get(`${c}:${one}`);if(stack?.length){const start=stack.pop();t.notes.push({id:uid(),tick:start.tick,duration:Math.max(1,scale(tick)-start.tick),pitch:one,velocity:start.velocity});}}
      } else throw Error("Unsupported MIDI event.");
    }
    p=end; for(const t of channels.values()){t.name=trackName+(channels.size>1?` ch ${[...channels.values()].indexOf(t)+1}`:"");if(t.notes.length)doc.tracks.push(t);}
  }
  if(p!==b.length)throw Error("Trailing MIDI data.");
  if(!doc.tracks.length)throw Error("MIDI contains no notes.");
  if(doc.tracks.filter(t=>!t.percussion).length>15)throw Error("This MIDI contains more than 15 melodic tracks; import would not be exportable.");
  if(doc.tracks.filter(t=>t.percussion).length>1)warnings.add("Several drum tracks share MIDI channel 10; their mix settings may overlap on export.");
  return {doc:normalize(doc),warnings:[...warnings]};
}
