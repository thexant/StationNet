"use strict";

StationNet.start(async (ui) => {
  const { PPQ, GM, uid, blank, clone, normalize, tickSeconds, secondsTick, writeMidi, readMidiDetailed } =
    await ui.modules.load("midi.js");
  const INDEX = "composer-index-v1", DRAFT = "composer-draft-v1", MAX_MIDI = 2 * 1024 * 1024;
  const DRUM_NAMES = {
    35:"Acoustic kick",36:"Kick",37:"Side stick",38:"Snare",39:"Hand clap",40:"Electric snare",
    41:"Low floor tom",42:"Closed hi-hat",43:"High floor tom",44:"Pedal hi-hat",45:"Low tom",
    46:"Open hi-hat",47:"Low-mid tom",48:"Hi-mid tom",49:"Crash cymbal",50:"High tom",
    51:"Ride cymbal",52:"Chinese cymbal",53:"Ride bell",54:"Tambourine",55:"Splash cymbal",
    56:"Cowbell",57:"Crash 2",58:"Vibraslap",59:"Ride 2",60:"High bongo",61:"Low bongo",
    62:"Mute conga",63:"Open conga",64:"Low conga",65:"High timbale",66:"Low timbale",
    67:"High agogo",68:"Low agogo",69:"Cabasa",70:"Maracas",71:"Short whistle",
    72:"Long whistle",73:"Short guiro",74:"Long guiro",75:"Claves",76:"High woodblock",
    77:"Low woodblock",78:"Mute cuica",79:"Open cuica",80:"Mute triangle",81:"Open triangle"
  };
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const projectKey = id => `composer-project-${id}`;
  let projects = [], projectId = null, doc = blank(), trackIndex = 0, selected = new Set();
  let undo = [], redo = [], clipboard = [], dirty = false, revision = 0, savePromise = null, saveTimer = null;
  let notice = "Draw a note to begin.", importWarnings = [], tab = 0, tool = "draw", snap = 120, duration = 480, velocity = 96;
  let zoom = 1, viewTick = 0, topPitch = 84, topDrumPitch = 51;
  let playing = false, loading = false, position = 0, transportToken = 0, lastPoll = 0, scheduledBeat = -1;
  let loop = false, loopStart = 0, loopEnd = 4 * PPQ * 4, metronome = false, countIn = false;
  let drag = null, pointerId = null, ignorePointers = false;
  let W = 840, ROW = 18, visibleRows = 26;
  const X = 96, Y = 42;
  const current = () => doc.tracks[trackIndex];
  const beatTicks = () => PPQ * 4 / doc.denominator;
  const measureTicks = () => beatTicks() * doc.numerator;
  const rows = () => current().percussion ? Math.min(visibleRows, 16) : visibleRows;
  const height = () => Y + ROW * rows();
  const pxBeat = () => 68 * zoom;
  const px = tick => X + (tick - viewTick) / PPQ * pxBeat();
  const xTick = x => Math.max(0, viewTick + (x - X) / pxBeat() * PPQ);
  const quantize = tick => snap ? Math.round(tick / snap) * snap : Math.round(tick);
  const rowPitch = y => clamp((current().percussion ? topDrumPitch : topPitch) - Math.floor((y - Y) / ROW), 0, 127);
  const pitchRow = pitch => (current().percussion ? topDrumPitch : topPitch) - pitch;
  const barOf = tick => Math.floor(tick / measureTicks()) + 1;
  const beatOf = tick => Math.floor((tick % measureTicks()) / beatTicks()) + 1;
  const noteName = pitch => current().percussion ? DRUM_NAMES[pitch] || `Drum ${pitch}` :
    `${["C","C♯","D","D♯","E","F","F♯","G","G♯","A","A♯","B"][pitch % 12]}${Math.floor(pitch / 12) - 1}`;
  const selectedNotes = () => current().notes.filter(n => selected.has(n.id));

  function status() {
    const summary = `${doc.name} · ${dirty ? "Unsaved" : "Saved"} · Bar ${barOf(position)} beat ${beatOf(position)} · ${current().name} · ${current().notes.length} notes`;
    ui.update("composer-summary", ui.text(summary, { id:"composer-summary", tone:"accent" }));
    ui.update("composer-status", ui.statusBar(
      importWarnings.length ? `${notice} · Import notes: ${importWarnings.join(" ")}` : notice,
      { id:"composer-status" }));
  }
  function say(message) { notice = message; status(); }
  function pushUndo(before) { undo.push(before); if (undo.length > 80) undo.shift(); redo = []; }
  function scheduleSave() {
    saveTimer?.();
    saveTimer = ui.timeout(() => { save().catch(error => say(`Save failed: ${error.message}`)); }, 900);
  }
  function markDirty() {
    revision++;
    dirty = true;
    if (playing || loading) {
      transportToken++;
      ui.midi.stop().catch(() => {});
      ui.sound.stop();
      playing = false;
      loading = false;
    }
    scheduleSave();
    status();
  }
  function edit(fn) {
    const before = clone(doc);
    fn();
    if (!same(before, doc)) { pushUndo(before); markDirty(); }
    render();
  }
  function refreshProjectPicker() {
    ui.update("composer-projects", ui.select("saved", "Saved compositions",
      [{ label:"Choose a composition…", value:"" }, ...projects.map(p => ({ label:p.name, value:p.id }))],
      { id:"composer-projects", value:projectId || "", onChange:({ value }) => { if (value && value !== projectId) void open(value); } }));
  }
  function storageMessage(error) {
    const message = String(error?.message || error);
    return /size limit|2 MiB|100-key/i.test(message) ?
      "StationNet application storage is full. Download a .mid backup or delete an old composition." : message;
  }
  function save() {
    if (savePromise) return savePromise;
    if (!dirty) return Promise.resolve();
    saveTimer?.(); saveTimer = null;
    const snapshot = clone(doc), savedRevision = revision;
    const id = projectId || `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;
    savePromise = (async () => {
      let success = false;
      try {
        await ui.storage.set(projectKey(id), snapshot);
        projectId = id;
        const next = [{ id, name:snapshot.name, updated:Date.now() }, ...projects.filter(p => p.id !== id)];
        await ui.storage.set(INDEX, next);
        projects = next;
        dirty = revision !== savedRevision;
        success = true;
        await ui.storage.delete(DRAFT).catch(() => {});
        notice = "Saved to your StationNet account.";
        refreshProjectPicker();
      } catch (error) {
        dirty = true;
        notice = `Save failed: ${storageMessage(error)}`;
        await ui.storage.set(DRAFT, { version:2, projectId:id, doc:clone(doc), updated:Date.now() }).catch(() => {});
      } finally {
        savePromise = null;
        status();
        if (dirty && success) scheduleSave();
      }
    })();
    return savePromise;
  }
  async function reconcileProjects() {
    const raw = await ui.storage.get(INDEX);
    const indexed = Array.isArray(raw) ? raw.filter(p => p && typeof p.id === "string") : [];
    const keys = new Set((await ui.storage.keys()).filter(k => k.startsWith("composer-project-")));
    projects = indexed.filter(p => keys.has(projectKey(p.id))).map(p => ({
      id:p.id, name:String(p.name || "Untitled composition").slice(0, 100), updated:Number(p.updated) || 0
    }));
    for (const key of keys) {
      const id = key.slice("composer-project-".length);
      if (projects.some(p => p.id === id)) continue;
      try {
        const value = await ui.storage.get(key);
        if (value) projects.push({ id, name:String(value.name || "Recovered composition").slice(0, 100), updated:0 });
      } catch (_) {}
    }
    projects.sort((a, b) => b.updated - a.updated);
  }
  async function open(id) {
    if (dirty) { await save(); if (dirty) return; }
    try {
      const value = await ui.storage.get(projectKey(id));
      if (!value) throw Error("Composition not found.");
      const opened = normalize(value);
      await stop(false);
      doc = opened; projectId = id; trackIndex = 0; selected.clear(); undo = []; redo = [];
      position = 0; viewTick = 0; loop = false; tab = 0; importWarnings = [];
      say(`Opened ${doc.name}.`); render();
    } catch (error) { say(`Open failed: ${error.message}`); }
  }
  async function newComposition(kind = "blank", discard = false) {
    if (savePromise) await savePromise;
    if (dirty && !discard) { await save(); if (dirty) return; }
    if (dirty && discard) {
      const choice = await ui.dialog("Discard unsaved changes?", [ui.text("This will replace the current unsaved score.")],
        [{ label:"Keep editing", value:"cancel" }, { label:"Discard and continue", value:"discard" }]);
      if (choice !== "discard") return;
    }
    saveTimer?.(); saveTimer = null;
    if (discard) await ui.storage.delete(DRAFT).catch(() => {});
    await stop(false);
    doc = kind === "demo" ? demoComposition() : blank();
    projectId = null; trackIndex = 0; selected.clear(); undo = []; redo = []; position = 0;
    viewTick = 0; loop = false; tab = 0; importWarnings = []; revision++; dirty = true;
    scheduleSave(); notice = kind === "demo" ? "Original starter score loaded. Press Play or edit a note." : "New blank composition. Draw a note on the grid.";
    render();
  }
  async function deleteProject() {
    if (!projectId) { say("This composition has not been saved yet."); return; }
    const choice = await ui.dialog("Delete composition?", [ui.text(`Delete “${doc.name}” from your StationNet account?`)],
      [{ label:"Cancel", value:"cancel" }, { label:"Delete", value:"delete" }]);
    if (choice !== "delete") return;
    try {
      await stop(false);
      await ui.storage.delete(projectKey(projectId));
      projects = projects.filter(p => p.id !== projectId);
      let indexError = null;
      try { await ui.storage.set(INDEX, projects); } catch (error) { indexError = error; }
      doc = blank(); projectId = null; selected.clear(); trackIndex = 0; undo = []; redo = [];
      dirty = false; position = 0; viewTick = 0; loop = false; importWarnings = [];
      await ui.storage.delete(DRAFT).catch(() => {});
      notice = indexError ? "Composition deleted. The project list will repair when you reopen the app." :
        "Composition deleted.";
      render();
    } catch (error) { say(`Delete failed: ${storageMessage(error)}`); }
  }
  async function importMidi() {
    try {
      const file = await ui.files.open({ accept:".mid,.midi,audio/midi" });
      const imported = readMidiDetailed(file.bytes);
      if (dirty) { await save(); if (dirty) return; }
      await stop(false);
      doc = imported.doc;
      doc.name = file.name.replace(/\.midi?$/i, "").slice(0, 100) || "Imported composition";
      projectId = null; trackIndex = 0; selected.clear(); undo = []; redo = [];
      position = 0; viewTick = 0; loop = false; tab = 0;
      revision++; dirty = true; scheduleSave();
      importWarnings = imported.warnings;
      notice = `Imported ${file.name}.`;
      render();
    } catch (error) {
      if (!/cancel/i.test(String(error?.message))) say(`Import failed: ${error.message}`);
    }
  }
  async function exportMidi() {
    try {
      const bytes = writeMidi(doc);
      if (bytes.length > MAX_MIDI) throw Error("This MIDI exceeds StationNet's 2 MiB download limit.");
      const filename = `${doc.name.replace(/[^a-z0-9_-]+/gi, "-") || "composition"}.mid`;
      await ui.download.binary(filename, bytes, "audio/midi");
      say(`Downloaded ${filename}. This file can also be uploaded to Radio.`);
    } catch (error) { say(`Export failed: ${error.message}`); }
  }
  function addTrack(percussion = false) {
    if (percussion && doc.tracks.some(t => t.percussion)) { say("Use the existing drum track."); return; }
    if (!percussion && doc.tracks.filter(t => !t.percussion).length >= 15) { say("General MIDI allows 15 melodic tracks plus drums."); return; }
    edit(() => {
      doc.tracks.push({ id:`t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`,
        name:percussion ? "Drums" : `Track ${doc.tracks.length + 1}`, instrument:0, volume:100, pan:64,
        mute:false, solo:false, percussion, notes:[] });
      trackIndex = doc.tracks.length - 1; selected.clear();
    });
  }
  function history(back) {
    const source = back ? undo : redo, target = back ? redo : undo;
    if (!source.length) return;
    target.push(clone(doc)); doc = source.pop();
    trackIndex = clamp(trackIndex, 0, doc.tracks.length - 1); selected.clear();
    markDirty(); render();
  }
  function deleteSelected() { if (selected.size) edit(() => { current().notes = current().notes.filter(n => !selected.has(n.id)); selected.clear(); }); }
  function copySelected() { clipboard = selectedNotes().map(n => ({ ...n })); say(`Copied ${clipboard.length} notes.`); }
  function pasteSelected() {
    if (!clipboard.length) return;
    edit(() => {
      const first = Math.min(...clipboard.map(n => n.tick));
      const copies = clipboard.map(n => ({ ...n, id:uid(), tick:Math.max(0, n.tick - first + quantize(position)) }));
      current().notes.push(...copies); selected = new Set(copies.map(n => n.id));
    });
  }
  function duplicateSelected() {
    const notes = selectedNotes(); if (!notes.length) return;
    const end = Math.max(...notes.map(n => n.tick + n.duration));
    const shift = Math.ceil(end / measureTicks()) * measureTicks() - Math.min(...notes.map(n => n.tick));
    edit(() => {
      const copies = notes.map(n => ({ ...n, id:uid(), tick:n.tick + shift }));
      current().notes.push(...copies); selected = new Set(copies.map(n => n.id));
    });
  }
  function transpose(amount) {
    if (!selected.size) return;
    edit(() => current().notes.forEach(n => { if (selected.has(n.id)) n.pitch = clamp(n.pitch + amount, 0, 127); }));
  }
  function nudge(amount, pitch = 0) {
    if (!selected.size) return;
    edit(() => current().notes.forEach(n => { if (selected.has(n.id)) {
      n.tick = Math.max(0, n.tick + amount); n.pitch = clamp(n.pitch + pitch, 0, 127);
    } }));
  }
  function quantizeSelected() {
    if (!snap || !selected.size) return;
    edit(() => current().notes.forEach(n => { if (selected.has(n.id)) {
      n.tick = quantize(n.tick); n.duration = Math.max(snap, quantize(n.duration));
    } }));
  }
  function projectMidi() {
    const output = clone(doc), solo = output.tracks.some(t => t.solo);
    output.tracks = output.tracks.filter(t => !t.mute && (!solo || t.solo));
    return writeMidi(output);
  }
  function hasAudibleNotes() {
    const solo = doc.tracks.some(t => t.solo);
    return doc.tracks.some(t => !t.mute && (!solo || t.solo) && t.notes.length);
  }
  async function play() {
    if (playing || loading) return;
    if (!hasAudibleNotes()) { say("Add a note to an audible track before playing."); return; }
    const token = ++transportToken;
    loading = true; say("Loading General MIDI sounds…");
    try {
      await ui.midi.load(projectMidi());
      if (token !== transportToken) return;
      if (position) await ui.midi.seek(tickSeconds(doc, position));
      if (countIn) {
        const beatMs = (tickSeconds(doc, position + beatTicks()) - tickSeconds(doc, position)) * 1000;
        ui.sound.play(Array.from({ length:doc.numerator }, (_, i) => ({
          frequency:i === 0 ? 1046 : 784, duration:55, delay:i * beatMs, volume:0.12
        })));
        say("Count-in…");
        await new Promise(resolve => ui.timeout(resolve, doc.numerator * beatMs));
        if (token !== transportToken) return;
      }
      await ui.midi.play();
      if (token !== transportToken) return;
      playing = true; scheduledBeat = Math.floor(position / beatTicks()) - 1;
      say("Playing");
    } catch (error) { say(`Playback failed: ${error.message}`); }
    finally { loading = false; render(); }
  }
  async function pause() {
    transportToken++; ui.sound.stop();
    await ui.midi.pause(); playing = false; render();
  }
  async function stop(renderNow = true) {
    transportToken++; ui.sound.stop();
    await ui.midi.stop(); playing = false; loading = false; position = 0; scheduledBeat = -1;
    if (renderNow) render();
  }
  async function seek(tick) {
    position = Math.max(0, Math.round(tick)); scheduledBeat = Math.floor(position / beatTicks()) - 1;
    ui.sound.stop();
    if (playing) await ui.midi.seek(tickSeconds(doc, position));
    draw(); status();
  }
  function preview(pitch) {
    ui.sound.playTone(440 * 2 ** ((pitch - 69) / 12), 145, { waveform:"triangle", volume:0.12 });
  }
  function scheduleMetronome(nowSeconds) {
    if (!metronome || !playing) return;
    let beat = Math.max(scheduledBeat + 1, Math.floor(position / beatTicks()));
    for (let i = 0; i < 4; i++, beat++) {
      const beatSeconds = tickSeconds(doc, beat * beatTicks());
      if (beatSeconds > nowSeconds + 0.22) break;
      if (beatSeconds >= nowSeconds - 0.04) ui.sound.play([{
        frequency:beat % doc.numerator === 0 ? 1046 : 784, duration:55,
        delay:Math.max(0, (beatSeconds - nowSeconds) * 1000), volume:0.1
      }]);
      scheduledBeat = beat;
    }
  }
  function fitNotes() {
    const notes = current().notes;
    if (!notes.length) { viewTick = 0; render(); return; }
    const first = Math.min(...notes.map(n => n.tick)), end = Math.max(...notes.map(n => n.tick + n.duration));
    viewTick = Math.max(0, Math.floor(first / measureTicks()) * measureTicks());
    zoom = clamp((W - X - 24) * PPQ / (Math.max(PPQ * 2, end - viewTick) * 68), 0.5, 3);
    const high = Math.max(...notes.map(n => n.pitch));
    if (current().percussion) topDrumPitch = clamp(high + 1, 35 + rows() - 1, 81);
    else topPitch = clamp(high + 1, rows() - 1, 127);
    render();
  }
  function noteAt(x, y) {
    if (y < Y || y >= height()) return null;
    const pitch = rowPitch(y), tick = xTick(x);
    return [...current().notes].reverse().find(n => n.pitch === pitch && tick >= n.tick && tick < n.tick + n.duration);
  }
  function cancelDrag() {
    if (drag?.before) doc = drag.before;
    if (drag?.beforeSelected) selected = drag.beforeSelected;
    drag = null; pointerId = null; draw(); status();
  }
  function pointer(e) {
    if (e.phase === "gesture") return;
    if (e.phase === "start") {
      if (e.pointers > 1 || pointerId !== null) { cancelDrag(); ignorePointers = true; return; }
      if (ignorePointers) return;
      pointerId = e.pointerId;
      if (e.y < Y) { pointerId = null; void seek(quantize(xTick(e.x))); return; }
      if (e.x < X) { pointerId = null; preview(rowPitch(e.y)); return; }
      if (e.y >= height()) { pointerId = null; return; }
      const hit = noteAt(e.x, e.y);
      if (tool === "erase") {
        if (hit) edit(() => { current().notes = current().notes.filter(n => n.id !== hit.id); selected.delete(hit.id); });
        pointerId = null; return;
      }
      if (hit) {
        const priorSelection = new Set(selected);
        if (e.shiftKey || e.ctrlKey || e.metaKey) {
          if (selected.has(hit.id)) selected.delete(hit.id); else selected.add(hit.id);
          pointerId = null; draw(); render(); return;
        }
        if (!selected.has(hit.id)) selected.clear();
        selected.add(hit.id);
        const endX = px(hit.tick + hit.duration);
        drag = { kind:endX - e.x < 12 ? "resize" : "move", startX:e.x, startY:e.y,
          before:clone(doc), beforeSelected:priorSelection, ids:new Set(selected) };
        draw(); return;
      }
      if (tool === "select") {
        selected.clear(); drag = { kind:"marquee", startX:e.x, startY:e.y, x:e.x, y:e.y }; draw(); return;
      }
      const priorSelection = new Set(selected);
      const note = { id:uid(), tick:Math.floor(xTick(e.x) / (snap || 1)) * (snap || 1),
        duration, pitch:rowPitch(e.y), velocity };
      drag = { kind:"draw", id:note.id, before:clone(doc), beforeSelected:priorSelection };
      current().notes.push(note); selected = new Set([note.id]); draw(); return;
    }
    if (ignorePointers) {
      if ((e.phase === "end" || e.phase === "cancel") && e.pointers === 0) ignorePointers = false;
      return;
    }
    if (e.pointerId !== pointerId) return;
    if (e.phase === "cancel") { cancelDrag(); return; }
    if (!drag) { if (e.phase === "end") pointerId = null; return; }
    if (e.phase === "move") {
      if (drag.kind === "draw") {
        const note = current().notes.find(n => n.id === drag.id);
        if (note) note.duration = Math.max(snap || 1, quantize(xTick(e.x)) - note.tick);
      } else if (drag.kind === "move" || drag.kind === "resize") {
        const dt = quantize((e.x - drag.startX) / pxBeat() * PPQ);
        const dp = -Math.round((e.y - drag.startY) / ROW);
        const original = drag.before.tracks[trackIndex].notes;
        current().notes = original.map(n => drag.ids.has(n.id) ? {
          ...n, tick:drag.kind === "move" ? Math.max(0, n.tick + dt) : n.tick,
          pitch:drag.kind === "move" ? clamp(n.pitch + dp, 0, 127) : n.pitch,
          duration:drag.kind === "resize" ? Math.max(snap || 1, n.duration + dt) : n.duration
        } : n);
      } else if (drag.kind === "marquee") { drag.x = e.x; drag.y = e.y; }
      draw(); return;
    }
    if (e.phase === "end") {
      if (drag.kind === "marquee") {
        const a = Math.min(drag.startX, e.x), b = Math.max(drag.startX, e.x);
        const c = Math.min(drag.startY, e.y), d = Math.max(drag.startY, e.y);
        selected = new Set(current().notes.filter(n => {
          const row = pitchRow(n.pitch), y = Y + row * ROW;
          return px(n.tick + n.duration) >= a && px(n.tick) <= b && y <= d && y + ROW >= c;
        }).map(n => n.id));
      } else if (!same(drag.before, doc)) { pushUndo(drag.before); markDirty(); }
      drag = null; pointerId = null; render();
    }
  }
  function canvasCommands() {
    const H = height(), commands = [
      { type:"rect", x:0, y:0, width:W, height:H, color:"#08130d" },
      { type:"rect", x:0, y:0, width:X, height:H, color:"#14241a" },
      { type:"rect", x:X, y:0, width:W - X, height:Y, color:"#193923" }
    ];
    const horizontal = [], major = [], minor = [], subdivisions = [];
    for (let row = 0; row < rows(); row++) {
      const pitch = (current().percussion ? topDrumPitch : topPitch) - row, y = Y + row * ROW;
      if (pitch < 0 || pitch > 127) continue;
      const black = [1,3,6,8,10].includes(pitch % 12);
      if (!current().percussion && pitch % 12 === 0)
        commands.push({ type:"rect", x:X, y, width:W - X, height:ROW, color:"#142f21" });
      if (black) commands.push({ type:"rect", x:0, y, width:X, height:ROW, color:"#0c1a11" });
      horizontal.push([0, y, W, y]);
      if (current().percussion || pitch % 12 === 0)
        commands.push({ type:"text", x:6, y:y + ROW / 2,
          text:current().percussion ? DRUM_NAMES[pitch] || String(pitch) : noteName(pitch),
          size:current().percussion ? 10 : 12, color:"#cae5cd" });
    }
    commands.push({ type:"lines", segments:horizontal, color:"#274432" });
    const unit = beatTicks(), first = Math.floor(viewTick / unit) * unit;
    const last = xTick(W) + unit;
    for (let tick = first; tick <= last; tick += unit) {
      const x = px(tick); if (x < X || x > W) continue;
      if (tick % measureTicks() === 0) {
        major.push([x, 0, x, H]);
        commands.push({ type:"text", x:x + 4, y:13, text:`BAR ${barOf(tick)}`, size:11, color:"#d7eacb" });
      } else {
        minor.push([x, Y, x, H]);
        commands.push({ type:"text", x:x + 3, y:29, text:String(beatOf(tick)), size:10, color:"#9bbda6" });
      }
      if (snap && snap < unit && pxBeat() * snap / PPQ >= 8)
        for (let t = tick + snap; t < tick + unit; t += snap) {
          const sx = px(t); if (sx >= X && sx <= W) subdivisions.push([sx, Y, sx, H]);
        }
    }
    commands.push({ type:"lines", segments:subdivisions, color:"#1e3927" },
      { type:"lines", segments:minor, color:"#385a41" },
      { type:"lines", segments:major, color:"#63916c", width:2 });
    const visible = n => {
      const row = pitchRow(n.pitch);
      return row >= 0 && row < rows() && px(n.tick + n.duration) >= X && px(n.tick) <= W;
    };
    const segment = n => [Math.max(X, px(n.tick)), Y + pitchRow(n.pitch) * ROW + ROW / 2,
      Math.max(X + 2, Math.min(W, px(n.tick + n.duration))), Y + pitchRow(n.pitch) * ROW + ROW / 2];
    if (!current().percussion) {
      const ghosts = doc.tracks.filter((t, i) => i !== trackIndex && !t.percussion && !t.mute)
        .flatMap(t => t.notes.filter(visible).map(segment));
      commands.push({ type:"lines", segments:ghosts, color:"#345544", width:Math.max(3, ROW - 7) });
    }
    const notes = current().notes.filter(visible);
    commands.push({ type:"lines", segments:notes.filter(n => !selected.has(n.id)).map(segment),
      color:current().percussion ? "#ae84d1" : "#61c98c", width:ROW - 4 });
    commands.push({ type:"lines", segments:notes.filter(n => selected.has(n.id)).map(segment),
      color:"#efc76f", width:ROW - 3 });
    if (loop) commands.push({ type:"lines", segments:[[px(loopStart), 0, px(loopStart), H],
      [px(loopEnd), 0, px(loopEnd), H]], color:"#eea850", width:2 });
    if (drag?.kind === "marquee") commands.push({ type:"rect", x:Math.min(drag.startX, drag.x),
      y:Math.min(drag.startY, drag.y), width:Math.abs(drag.x - drag.startX), height:Math.abs(drag.y - drag.startY),
      color:"#77997b66" });
    if (px(position) >= X && px(position) <= W)
      commands.push({ type:"line", x1:px(position), y1:0, x2:px(position), y2:H, color:"#ffdf83", width:2 });
    return commands;
  }
  function canvas() {
    return ui.canvas(W, height(), canvasCommands(), { id:"composer-roll",
      label:"Piano roll. Use Draw, Select, or Erase. Touch controls and a note-entry form are below.", onPointer:pointer });
  }
  function draw() { ui.update("composer-roll", canvas()); }
  function notePanel() {
    const one = selectedNotes().length === 1 ? selectedNotes()[0] : null;
    const baseTick = one?.tick ?? position;
    return ui.panel("Notes", [
      ui.text(selected.size ? `${selected.size} note${selected.size === 1 ? "" : "s"} selected${one ? ` · ${noteName(one.pitch)} · velocity ${one.velocity}` : ""}` :
        "Select a note to edit it, or enter a new note below."),
      ui.row([
        ui.select("duration", "Draw length", [[60,"1/32"],[120,"1/16"],[240,"1/8"],[480,"1/4"],[960,"1/2"],
          [measureTicks(),"1 bar"]].map(([value,label]) => ({ value:String(value), label })),
          { value:String(duration), onChange:({ value }) => { duration = Number(value); } }),
        ui.select("snap", "Grid / snap", [[0,"Off"],[30,"1/64"],[60,"1/32"],[120,"1/16"],[240,"1/8"],[480,"1/4"]]
          .map(([value,label]) => ({ value:String(value), label })),
          { value:String(snap), onChange:({ value }) => { snap = Number(value); draw(); } })
      ]),
      ui.row([
        ui.numberInput("notePitch", "MIDI pitch (0–127)", { value:one?.pitch ?? 60, min:0, max:127 }),
        ui.numberInput("noteBar", "Bar", { value:barOf(baseTick), min:1, max:100000 }),
        ui.numberInput("noteBeat", "Beat", { value:1 + (baseTick % measureTicks()) / beatTicks(), min:1,
          max:doc.numerator + 0.875, step:0.125 })
      ]),
      ui.row([
        ui.numberInput("noteLength", "Length in ticks", { value:one?.duration ?? duration, min:1, max:1000000 }),
        ui.numberInput("noteVelocity", "Velocity (1–127)", { value:one?.velocity ?? velocity, min:1, max:127 })
      ]),
      ui.menu("Note entry", [{ label:"Add note", value:"add" }, { label:"Update selected note", value:"update", disabled:!one }],
        ({ value, values }) => {
          const pitch = clamp(Math.round(Number(values.notePitch)), 0, 127);
          const bar = clamp(Math.round(Number(values.noteBar)), 1, 100000);
          const beat = clamp(Number(values.noteBeat), 1, doc.numerator + 0.875);
          const tick = Math.max(0, Math.round((bar - 1) * measureTicks() + (beat - 1) * beatTicks()));
          const length = clamp(Math.round(Number(values.noteLength)), 1, 1000000);
          const v = clamp(Math.round(Number(values.noteVelocity)), 1, 127);
          if (![pitch,bar,beat,tick,length,v].every(Number.isFinite)) { say("Enter valid note values."); return; }
          velocity = v; duration = length;
          edit(() => {
            if (value === "update" && one) Object.assign(current().notes.find(n => n.id === one.id), { pitch,tick,duration:length,velocity:v });
            else { const id = uid(); current().notes.push({ id,pitch,tick,duration:length,velocity:v }); selected = new Set([id]); }
          });
        }),
      ui.menu("Selection tools", [
        { label:"← Nudge", value:"left" }, { label:"Nudge →", value:"right" },
        { label:"Pitch ↑", value:"up" }, { label:"Pitch ↓", value:"down" },
        { label:"Quantize position + length", value:"quantize" },
        { label:"Duplicate to next bar", value:"duplicate" }, { label:"Delete", value:"delete" }
      ], ({ value }) => {
        if (value === "left") nudge(-(snap || 1));
        if (value === "right") nudge(snap || 1);
        if (value === "up") transpose(1);
        if (value === "down") transpose(-1);
        if (value === "quantize") quantizeSelected();
        if (value === "duplicate") duplicateSelected();
        if (value === "delete") deleteSelected();
      })
    ]);
  }
  function trackPanel() {
    const t = current();
    return ui.panel("Track", [
      ui.menu("Tracks", [
        { label:"Add instrument", value:"add" }, { label:"Add drums", value:"drums" },
        { label:"Remove current", value:"remove", disabled:doc.tracks.length < 2 },
        { label:t.mute ? "Unmute" : "Mute", value:"mute" }, { label:t.solo ? "Unsolo" : "Solo", value:"solo" }
      ], async ({ value }) => {
        if (value === "add") addTrack();
        if (value === "drums") addTrack(true);
        if (value === "remove" && doc.tracks.length > 1) {
          const choice = await ui.dialog("Remove track?", [ui.text(`Remove ${t.name} and all its notes?`)],
            [{ label:"Cancel", value:"cancel" }, { label:"Remove", value:"remove" }]);
          if (choice === "remove") edit(() => {
            doc.tracks.splice(trackIndex, 1); trackIndex = clamp(trackIndex, 0, doc.tracks.length - 1); selected.clear();
          });
        }
        if (value === "mute") edit(() => { t.mute = !t.mute; });
        if (value === "solo") edit(() => { t.solo = !t.solo; });
      }),
      ui.input("trackName", "Track name", { value:t.name, maxLength:80 }),
      ui.button("Rename track", ({ values }) => edit(() => { t.name = String(values.trackName || t.name).trim().slice(0, 80); })),
      t.percussion ? ui.text("Drum rows use General MIDI channel 10. Scroll pitch rows for additional sounds.") :
        ui.select("instrument", "General MIDI instrument", GM.map((name, i) => ({ value:String(i), label:`${i + 1}. ${name}` })),
          { value:String(t.instrument), onChange:({ value }) => edit(() => { t.instrument = Number(value); }) }),
      ui.row([
        ui.numberInput("volume", "Volume (0–127)", { value:t.volume, min:0, max:127 }),
        ui.numberInput("pan", "Pan (0–127; center 64)", { value:t.pan, min:0, max:127 })
      ]),
      ui.button("Apply mix", ({ values }) => edit(() => {
        t.volume = clamp(Math.round(Number(values.volume)), 0, 127);
        t.pan = clamp(Math.round(Number(values.pan)), 0, 127);
      }))
    ]);
  }
  function songPanel() {
    return ui.panel("Song and playback", [
      ui.row([
        ui.numberInput("tempo", "Starting tempo (BPM)", { value:doc.tempo[0].bpm, min:20, max:300 }),
        ui.numberInput("numerator", "Beats per bar", { value:doc.numerator, min:1, max:16 }),
        ui.select("denominator", "Beat unit", [2,4,8,16].map(n => ({ value:String(n), label:String(n) })),
          { value:String(doc.denominator) })
      ]),
      ui.button("Apply song settings", ({ values }) => edit(() => {
        const bpm = clamp(Math.round(Number(values.tempo)) || 120, 20, 300);
        const previousBar = measureTicks();
        doc.tempo[0].bpm = bpm; doc.bpm = bpm;
        doc.numerator = clamp(Math.round(Number(values.numerator)) || 4, 1, 16);
        doc.denominator = Number(values.denominator);
        if (duration === previousBar) duration = measureTicks();
      })),
      ui.numberInput("tempoAt", "Tempo at playhead (BPM)", { value:doc.tempo.find(e => e.tick === position)?.bpm || doc.bpm,
        min:20, max:300 }),
      ui.menu("Tempo map", [{ label:"Set change here", value:"set" }, { label:"Remove change here", value:"remove" }],
        ({ value, values }) => {
          const tick = quantize(position);
          if (value === "set" && doc.tempo.length >= 1000 && !doc.tempo.some(e => e.tick === tick)) {
            say("This composition already has 1,000 tempo changes."); return;
          }
          if (value === "set") edit(() => {
            const bpm = clamp(Math.round(Number(values.tempoAt)) || 120, 20, 300);
            doc.tempo = [...doc.tempo.filter(e => e.tick !== tick), { tick,bpm }].sort((a,b) => a.tick - b.tick);
            doc.bpm = doc.tempo[0].bpm;
          });
          if (value === "remove" && tick > 0) edit(() => { doc.tempo = doc.tempo.filter(e => e.tick !== tick); });
        }),
      ui.text(`Tempo changes: ${doc.tempo.map(e => `bar ${barOf(e.tick)}: ${e.bpm} BPM`).join(" · ")}`),
      ui.menu("Loop region", [
        { label:"Start at playhead", value:"start" }, { label:"End at playhead", value:"end" },
        { label:"Clear loop", value:"clear" }
      ], ({ value }) => {
        if (value === "start") { loopStart = position; loopEnd = Math.max(loopEnd, loopStart + beatTicks()); }
        if (value === "end") { loopEnd = Math.max(position, loopStart + beatTicks()); loop = true; }
        if (value === "clear") loop = false;
        render();
      }),
      ui.text(loop ? `Loop: bar ${barOf(loopStart)} to bar ${barOf(loopEnd)}` : "Loop is off."),
      ui.checkbox("metronome", "Metronome during playback", { checked:metronome,
        onChange:({ value }) => { metronome = Boolean(value); if (!metronome) ui.sound.stop(); } }),
      ui.checkbox("countIn", "One-bar count-in before Play", { checked:countIn,
        onChange:({ value }) => { countIn = Boolean(value); } })
    ]);
  }
  function projectsPanel() {
    return ui.panel("Compositions", [
      ui.select("saved", "Saved compositions",
        [{ label:"Choose a composition…", value:"" }, ...projects.map(p => ({ label:p.name, value:p.id }))],
        { id:"composer-projects", value:projectId || "", onChange:({ value }) => { if (value && value !== projectId) void open(value); } }),
      ui.input("projectName", "Composition name", { value:doc.name, maxLength:100 }),
      ui.menu("Project", [
        { label:"Rename", value:"rename" }, { label:"Save now", value:"save" },
        { label:"New blank", value:"new" }, { label:"Load starter score", value:"demo" },
        { label:"Discard and start blank", value:"discard" }
      ], async ({ value, values }) => {
        if (value === "rename") edit(() => { doc.name = String(values.projectName || "Untitled composition").trim().slice(0, 100); });
        if (value === "save") await save();
        if (value === "new") await newComposition();
        if (value === "demo") await newComposition("demo");
        if (value === "discard") await newComposition("blank", true);
      }),
      ui.text(`${projects.length} saved composition${projects.length === 1 ? "" : "s"}. Storage is private to your StationNet account.`)
    ]);
  }
  function helpPanel() {
    return ui.panel("How to compose", [
      ui.text("Draw on the grid to add notes. Drag a note to move it, or drag its right edge to resize. Select makes a box selection; Erase removes notes."),
      ui.text("Touch: choose a tool above the roll, tap or drag notes, then use Selection tools below for precise moves. The note-entry form works without a pointer."),
      ui.text("The ruler sets the playhead. Scroll bars and pitch rows with Navigation; Fit notes brings the active track into view. Pale notes are other melodic tracks."),
      ui.text("Tap the keyboard at the left of the roll for a pitch reference. It uses a short local tone so preview does not interrupt General MIDI playback."),
      ui.text("Shortcuts: Space Play/Pause; Ctrl/Cmd+A Select all, C Copy, V Paste, D Duplicate, Z Undo, Shift+Z Redo, S Save; Delete removes selected notes. D/S/E choose tools; arrows scroll."),
      ui.text("MIDI import supports note events, General MIDI instruments, volume, pan, tempo, and an initial time signature. Unsupported controller and expressive data is reported after import.")
    ]);
  }
  function menuAction(value) {
    if (value === "new") return newComposition();
    if (value === "demo") return newComposition("demo");
    if (value === "save") return save();
    if (value === "import") return importMidi();
    if (value === "export") return exportMidi();
    if (value === "delete") return deleteProject();
    if (value === "undo") return history(true);
    if (value === "redo") return history(false);
    if (value === "copy") return copySelected();
    if (value === "paste") return pasteSelected();
    if (value === "duplicate") return duplicateSelected();
    if (value === "quantize") return quantizeSelected();
    if (value === "transpose-up") return transpose(12);
    if (value === "transpose-down") return transpose(-12);
    if (value === "fit") return fitNotes();
    if (value === "help") { tab = 4; render(); }
  }
  function render() {
    const activeTrack = current();
    const panel = [notePanel, trackPanel, songPanel, projectsPanel, helpPanel][tab]();
    ui.render(
      ui.title("MIDI Composer"),
      ui.text("", { id:"composer-summary", tone:"accent" }),
      ui.menuBar([
        { label:"File", items:[
          { label:"New blank", value:"new" }, { label:"Starter score", value:"demo" },
          { label:"Save", value:"save" }, { label:"Import MIDI", value:"import" },
          { label:"Download .mid", value:"export" }, { label:"Delete composition", value:"delete" }
        ] },
        { label:"Edit", items:[
          { label:"Undo", value:"undo" }, { label:"Redo", value:"redo" },
          { label:"Copy", value:"copy" }, { label:"Paste", value:"paste" },
          { label:"Duplicate to next bar", value:"duplicate" },
          { label:"Quantize position + length", value:"quantize" },
          { label:"Transpose octave up", value:"transpose-up" },
          { label:"Transpose octave down", value:"transpose-down" }
        ] },
        { label:"View", items:[{ label:"Fit notes", value:"fit" }, { label:"Help", value:"help" }] }
      ], ({ value }) => { Promise.resolve(menuAction(value)).catch(error => say(error.message)); }),
      ui.menu("Transport", [
        { label:loading ? "Loading…" : playing ? "Pause" : "Play", value:"play", disabled:loading },
        { label:"Stop", value:"stop" }, { label:loop ? "Loop on" : "Loop off", value:"loop" },
        { label:"← Bar", value:"back" }, { label:"Bar →", value:"forward" }
      ], ({ value }) => {
        if (value === "play") void (playing ? pause() : play());
        if (value === "stop") void stop();
        if (value === "loop") { loop = !loop; render(); }
        if (value === "back") void seek(Math.max(0, position - measureTicks()));
        if (value === "forward") void seek(position + measureTicks());
      }),
      ui.select("track", "Active track", doc.tracks.map((t, i) => ({
        label:`${i + 1}. ${t.name}${t.mute ? " [M]" : ""}${t.solo ? " [S]" : ""}`, value:String(i)
      })), { value:String(trackIndex), onChange:({ value }) => {
        trackIndex = Number(value); selected.clear(); render();
      } }),
      ui.menu("Tools", ["draw","select","erase"].map(value => ({
        label:`${tool === value ? "● " : ""}${value[0].toUpperCase() + value.slice(1)}`, value
      })), ({ value }) => { tool = value; render(); }),
      ui.menu("Navigation", [
        { label:"← Timeline", value:"left" }, { label:"Timeline →", value:"right" },
        { label:"Pitch ↑", value:"up" }, { label:"Pitch ↓", value:"down" },
        { label:"Zoom +", value:"zin" }, { label:"Zoom −", value:"zout" },
        { label:"Fit notes", value:"fit" }
      ], ({ value }) => {
        if (value === "left") viewTick = Math.max(0, viewTick - measureTicks());
        if (value === "right") viewTick += measureTicks();
        if (value === "up") { if (activeTrack.percussion) topDrumPitch = clamp(topDrumPitch + 12, 35 + rows() - 1, 81);
          else topPitch = clamp(topPitch + 12, rows() - 1, 127); }
        if (value === "down") { if (activeTrack.percussion) topDrumPitch = clamp(topDrumPitch - 12, 35 + rows() - 1, 81);
          else topPitch = clamp(topPitch - 12, rows() - 1, 127); }
        if (value === "zin") zoom = clamp(zoom * 1.4, 0.5, 3);
        if (value === "zout") zoom = clamp(zoom / 1.4, 0.5, 3);
        if (value === "fit") { fitNotes(); return; }
        render();
      }),
      canvas(),
      ui.tabs(["Notes","Track","Song","Compositions","Help"], ({ value }) => { tab = Number(value); render(); },
        { selected:tab }),
      panel,
      ui.statusBar("", { id:"composer-status" })
    );
    status();
  }
  function demoComposition() {
    const sample = blank("Night Signal · starter score");
    sample.tempo = [{ tick:0, bpm:112 }, { tick:4 * 4 * PPQ, bpm:120 }]; sample.bpm = 112;
    const melody = sample.tracks[0]; melody.name = "Signal lead"; melody.instrument = 81;
    const bass = { id:"demo-bass", name:"Pulse bass", instrument:38, volume:96, pan:64,
      mute:false, solo:false, percussion:false, notes:[] };
    const pad = { id:"demo-pad", name:"Warm pad", instrument:89, volume:72, pan:70,
      mute:false, solo:false, percussion:false, notes:[] };
    const drums = { id:"demo-drums", name:"Drums", instrument:0, volume:100, pan:64,
      mute:false, solo:false, percussion:true, notes:[] };
    const add = (t, bar, beat, pitch, length, loudness = 95) =>
      t.notes.push({ id:uid(), tick:(bar * 4 + beat) * PPQ, duration:length, pitch, velocity:loudness });
    const roots = [48,44,41,43,48,44,41,43];
    for (let bar = 0; bar < 8; bar++) {
      const root = roots[bar];
      add(bass, bar, 0, root - 12, PPQ * 2, 93); add(bass, bar, 2, root - 12, PPQ * 2, 83);
      for (const interval of [0,3,7]) add(pad, bar, 0, root + interval, PPQ * 4, 58);
      for (let beat = 0; beat < 4; beat++) {
        add(drums, bar, beat, 42, PPQ / 4, 48);
        if (beat === 0 || beat === 2) add(drums, bar, beat, 36, PPQ / 4, 105);
        if (beat === 1 || beat === 3) add(drums, bar, beat, 38, PPQ / 4, 88);
      }
      [[0,12],[0.75,15],[1.5,19],[2.5,15],[3.25,10]].forEach(([beat, interval]) =>
        add(melody, bar, beat, root + interval, beat === 3.25 ? PPQ * 0.75 : PPQ * 0.5, 92));
    }
    sample.tracks.push(bass, pad, drums);
    return sample;
  }
  ui.onKey(({ code, phase, ctrlKey, metaKey, shiftKey }) => {
    if (phase !== "down") return;
    const mod = ctrlKey || metaKey;
    if (mod && code === "KeyS") void save();
    else if (mod && code === "KeyZ") history(!shiftKey);
    else if (mod && code === "KeyY") history(false);
    else if (mod && code === "KeyC") copySelected();
    else if (mod && code === "KeyV") pasteSelected();
    else if (mod && code === "KeyD") duplicateSelected();
    else if (mod && code === "KeyA") { selected = new Set(current().notes.map(n => n.id)); render(); }
    else if (code === "Delete" || code === "Backspace") deleteSelected();
    else if (code === "Space") void (playing ? pause() : play());
    else if (code === "KeyD" || code === "KeyS" || code === "KeyE") {
      tool = { KeyD:"draw", KeyS:"select", KeyE:"erase" }[code]; render();
    } else if (code === "ArrowLeft") { viewTick = Math.max(0, viewTick - PPQ); render(); }
    else if (code === "ArrowRight") { viewTick += PPQ; render(); }
    else if (code === "ArrowUp") {
      if (current().percussion) topDrumPitch = clamp(topDrumPitch + 12, 35 + rows() - 1, 81);
      else topPitch = clamp(topPitch + 12, rows() - 1, 127);
      render();
    } else if (code === "ArrowDown") {
      if (current().percussion) topDrumPitch = clamp(topDrumPitch - 12, 35 + rows() - 1, 81);
      else topPitch = clamp(topPitch - 12, rows() - 1, 127);
      render();
    }
  });
  ui.animate(async ({ time }) => {
    if (!playing || time - lastPoll < 100) return;
    lastPoll = time;
    try {
      const seconds = await ui.midi.position();
      position = secondsTick(doc, seconds);
      if (loop && position >= loopEnd) {
        await ui.midi.seek(tickSeconds(doc, loopStart));
        position = loopStart; scheduledBeat = Math.floor(position / beatTicks()) - 1;
        ui.sound.stop();
      }
      let end = 0;
      for (const t of doc.tracks) for (const n of t.notes) end = Math.max(end, n.tick + n.duration);
      if (!loop && end && position >= end) { await stop(false); render(); return; }
      scheduleMetronome(seconds);
      if (px(position) > W - 28 || px(position) < X) viewTick = Math.max(0, Math.floor(position / measureTicks()) * measureTicks());
      draw(); status();
    } catch (error) { playing = false; say(`Playback stopped: ${error.message}`); render(); }
  });
  ui.onCleanup(() => { transportToken++; ui.sound.stop(); ui.midi.stop().catch(() => {}); if (dirty) save().catch(() => {}); });
  try {
    const viewport = await ui.display.viewport();
    if (viewport.width < 700) {
      W = clamp(Math.floor(viewport.width) - 18, 300, 560);
      ROW = 24;
      visibleRows = clamp(Math.floor((viewport.height * 0.56 - Y) / ROW), 12, 18);
    }
    await reconcileProjects();
    const draft = await ui.storage.get(DRAFT);
    if (draft) {
      const wrapped = draft.version === 2 && draft.doc;
      const draftDoc = normalize(wrapped ? draft.doc : draft);
      const saved = wrapped && projects.find(p => p.id === draft.projectId);
      if (!saved || !wrapped || Number(draft.updated) > saved.updated) {
        doc = draftDoc; projectId = wrapped ? draft.projectId : null; dirty = true; revision++;
        notice = "Recovered an unsaved draft. Save when ready.";
      }
    }
    if (!dirty && projects.length) {
      for (const item of projects) {
        try {
          const value = await ui.storage.get(projectKey(item.id));
          if (value) { doc = normalize(value); projectId = item.id; notice = `Loaded ${doc.name}.`; break; }
        } catch (_) {}
      }
    }
  } catch (error) { notice = `Could not load saved compositions: ${error.message}`; }
  render();
});
