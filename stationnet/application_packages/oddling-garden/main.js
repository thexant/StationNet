// ODDLING GARDEN - StationNet package 1, no bundled assets required.
// Lifetime, need decay, and happiness are calculated from wall-clock time.
StationNet.start(async (ui) => {
  const KEY = "oddling_garden_save_v1";
  const { MIN, DAY, INCUBATION, SPECIES, byId, clamp, round, mood, stage,
    advancePet, freshState, normalizeSave } = await ui.modules.load("simulation.js");
  const W = 360, H = 240;
  const PIXEL_SIZE = 4; // 90x60 internal frame enlarged with nearest-neighbor scaling.
  const date = ms => new Date(ms).toLocaleDateString();
  const ageText = (ms) => {
    const m = Math.max(0, Math.floor(ms / MIN));
    if (m < 60) return m + "m";
    if (m < 1440) return Math.floor(m / 60) + "h " + (m % 60) + "m";
    return Math.floor(m / 1440) + "d " + Math.floor((m % 1440) / 60) + "h";
  };
  // Keep the original per-account key. v1 payloads are normalized in place.
  let state = freshState();
  let chosen = SPECIES[0].id, page = "home", game = null, message = "";
  let saveQueue = Promise.resolve();
  let saveRevision = 0;
  let loadFailed = false, loadMessage = "", saveMessage = "", draftName = "", rawSave = null;
  let memorialPage = 0, selectedMemorial = 0;
  let frameTime = 0, lastStatusRefresh = 0, lastPersist = 0;
  let displayedStage = null;
  let reaction = null; // Transient visual flourish; never stored as pet state.
  let displayedDeath = false;
  let sparkBursts = [];
  let tab = 0; // Compact desktop/mobile care tabs.
  let portrait = false;
  let compact = false;
  let petZoom = 1;
  let pinchBase = null;
  const touches = new Map();
  let gamepadCatchDown = false;
  let gamepadBackDown = false;
  let lastCatchPointer = 0;
  // Use StationNet's random source with a changing seed (the default is fixed).
  ui.random.seed(Date.now());


  function saveNotice() {
    return ui.text(saveMessage || "", { id: "save-status", tone: saveMessage.startsWith("Could not") ? "warning" : "muted" });
  }
  function snapshotSave() {
    if (loadFailed) return Promise.resolve(false);
    const copy = JSON.parse(JSON.stringify(state));
    const revision = ++saveRevision;
    saveMessage = "Saving…";
    ui.update("save-status", saveNotice());
    saveQueue = saveQueue.then(async () => {
      try {
        await ui.storage.set(KEY, copy);
        if (revision === saveRevision) {
          saveMessage = "Saved";
          ui.update("save-status", saveNotice());
        }
        return true;
      } catch (error) {
        if (revision === saveRevision) {
          saveMessage = "Could not save changes: " + String(error.message || error);
          ui.update("save-status", saveNotice());
        }
        return false;
      }
    });
    return saveQueue;
  }
  function record(p, text, at = Date.now()) {
    p.journal ||= [];
    p.journal.push({ at, text });
    p.journal = p.journal.slice(-32);
  }
  function growPlant(p, at = Date.now()) {
    if (!p.plant) return;
    const stageTier = { Egg: 0, Baby: 0, Child: 1, Adult: 2, Elder: 3 }[stage(p, at)] || 0;
    const careTier = p.careCount >= 40 ? 4 : p.careCount >= 22 ? 3 :
      p.careCount >= 10 ? 2 : p.careCount >= 3 ? 1 : 0;
    const target = Math.max(stageTier, careTier);
    if (target > p.plant.growth) {
      p.plant.growth = target;
      record(p, "The " + plantName(p.plant.kind) + " grew to its next form.", at);
    }
  }
  function recordStages(p, from, to) {
    p.milestones ||= {};
    const changes = [
      ["Baby", p.hatchAt, "hatched from its egg"],
      ["Child", p.createdAt + DAY, "grew into a child"],
      ["Adult", p.createdAt + 4 * DAY, "grew into an adult"],
      ["Elder", p.createdAt + 11 * DAY, "grew into an elder"]
    ];
    for (const [name, at, text] of changes) {
      if (from < at && to >= at && !p.milestones[name]) {
        p.milestones[name] = true;
        record(p, p.name + " " + text + ".", at);
      }
    }
    growPlant(p, to);
  }
  function recordCareMilestones(p) {
    for (const threshold of [10, 25, 50]) {
      const key = "care" + threshold;
      if (p.careCount >= threshold && !p.milestones[key]) {
        p.milestones[key] = true;
        record(p, p.name + " received " + threshold + " moments of care.");
      }
    }
  }
  function plantName(kind) {
    return { moonfern: "Moonfern", sunbud: "Sunbud", starvine: "Starvine" }[kind] || "plant";
  }
  function createPet(name) {
    if (loadFailed) return;
    const now = Date.now();
    // All accounts are private: StationNet scopes storage to the signed-in player.
    state.pet = {
      name: Array.from(String(name || "").trim()).slice(0, 20).join("") || byId(chosen).name,
      species: chosen, createdAt: now, hatchAt: now + INCUBATION,
      lastUpdate: now, hunger: 90, clean: 95, energy: 90,
      fun: 85, social: 85, health: 100, sleeping: false,
      happyWeighted: 0, livedMs: 0, bestGame: 0,
      careCount: 0, warmth: 0, journal: [], memory: "", plant: null, milestones: {}
    };
    record(state.pet, state.pet.name + " arrived as a mystery egg.", now);
    draftName = "";
    page = "home";
    game = null;
    for (const track of touches.values()) stopHold(track);
    touches.clear(); pinchBase = null;
    tab = 0;
    petZoom = 1;
    reaction = null;
    message = "Your egg is resting. Tap it to keep it warm!";
    snapshotSave();
    show();
    chirp("birth");
  }
  function markDeath(p, cause, at) {
    if (p.death) return;
    const needs = [["fullness", p.hunger], ["hygiene", p.clean], ["fun", p.fun],
      ["company", p.social]].filter(([, value]) => value < 18).map(([name]) => name);
    const reason = cause === "neglect" && needs.length ? "Unmet " + needs.join(", ") + " needs" : "";
    p.death = { cause, at, reason };
    record(p, cause === "old age" ? "A full fourteen-day life came to an end." :
      "The little nest fell quiet after prolonged unmet needs.", at);
    p.lastUpdate = at;
    p.sleeping = false;
    reaction = null;
    game = null;
    page = "home"; // Always show the remains before the memorial.
    message = cause === "neglect"
      ? "Your Oddling could not survive without care."
      : "Your Oddling's fourteen days have come to an end.";
    snapshotSave();
  }
  function layToRest() {
    const p = state.pet;
    if (!p || !p.death) return;
    const d = p.death;
    state.memorials.unshift({
      name: p.name, species: p.species, hatched: p.hatchAt,
      created: p.createdAt, passed: d.at, cause: d.cause,
      happiness: Math.round(p.livedMs ? p.happyWeighted / p.livedMs : 0),
      bestGame: p.bestGame || 0, careCount: p.careCount || 0,
      lifetimeDays: Math.max(0, (d.at - p.createdAt) / DAY),
      lastStage: stage(p, d.at), reason: d.reason || "", journal: p.journal || [],
      memory: p.memory || "", plant: p.plant || null
    });
    chosen = p.species; // The next egg can still be a different species.
    state.pet = null;
    game = null;
    for (const track of touches.values()) stopHold(track);
    touches.clear(); pinchBase = null;
    reaction = null;
    page = "memorial";
    message = "A little life remembered.";
    snapshotSave();
    chirp("farewell");
    show();
  }
  function advance(now = Date.now()) {
    const p = state.pet;
    if (!p || p.death) return false;
    const from = p.lastUpdate;
    const changed = advancePet(p, now, (cause, at) => {
      recordStages(p, from, at);
      markDeath(p, cause, at);
    });
    if (!p.death) recordStages(p, from, p.lastUpdate);
    return changed;
  }
  function add(p, changes) {
    for (const [key, delta] of Object.entries(changes)) p[key] = clamp(p[key] + delta);
  }
  function chirp(kind) {
    // Each action has an individual melodic/percussive signature.
    const cues = {
      birth:   [[392,90],[523,90],[659,90],[784,180]],
      hatch:   [[392,80],[523,80],[659,80],[784,90],[1047,230]],
      warm:    [[330,65],[440,65],[550,105]],
      meal:    [[185,65],[247,75],[185,80],[330,125]],
      snack:   [[659,65],[880,70],[784,115]],
      bath:    [[520,55],[690,55],[820,55],[1035,120]],
      tidy:    [[270,50],[340,50],[430,50],[540,110]],
      cuddle:  [[523,120],[659,120],[784,180]],
      talk:    [[420,55],[540,85],[470,60],[680,110]],
      medicine:[[250,100],[330,90],[480,140]],
      sleep:   [[523,130],[440,140],[330,210]],
      wake:    [[440,75],[660,75],[880,170]],
      warn:    [[250,125],[196,200]],
      play:    [[740,65],[988,80],[1175,145]],
      star:    [[988,55],[1319,105]],
      farewell:[[392,160],[330,170],[262,350]],
      pet:     [[440,75],[550,80],[660,110]]
    };
    const volume = Math.max(0, Math.min(100, Number(state.settings?.sound ?? 65))) / 100;
    if (!volume) return;
    const pattern = cues[kind] || cues.warm;
    const waveform = ["meal","tidy","talk","warn"].includes(kind) ? "square" : "triangle";
    ui.sound.sequence(pattern.map(([frequency,duration]) => ({
      frequency, duration, waveform, volume: .055 * volume, attack: 8, release: 28
    })), { duration: 90 });
  }
  function react(kind, duration = 1400) {
    reaction = { kind, started: Date.now(), duration };
  }
  function touchEgg() {
    advance();
    const p = state.pet;
    if (!p || stage(p) !== "Egg") return;
    p.hatchAt = Math.max(Date.now() + 1000, p.hatchAt - 30000);
    p.warmth++;
    message = "Warmth " + p.warmth + "! The shell wiggles.";
    snapshotSave();
    react("warm", 850);
    chirp("warm");
    refreshHome();
  }
  function act(which) {
    advance();
    const p = state.pet;
    if (!p || p.death) { show(); return; }
    if (stage(p) === "Egg") { touchEgg(); return; }
    const wasSleeping = p.sleeping;
    const actions = {
      meal:     { d: { hunger: 37, clean: -5, energy: 3 }, text: "A hearty meal! Nom nom nom." },
      snack:    { d: { hunger: 12, fun: 10, clean: -2 }, text: "An exciting little treat!" },
      bath:     { d: { clean: 65, health: 4, fun: -3 }, text: "Bubbles everywhere! All clean." },
      tidy:     { d: { clean: 28, fun: 1 }, text: "The nest has been swept spotless." },
      cuddle:   { d: { social: 26, fun: 8 }, text: "A squeaky cuddle and a happy wiggle!" },
      talk:     { d: { social: 17, fun: 7 }, text: "Bip-borp! Your Oddling answers back." },
      medicine: { d: { health: 29, fun: -5 }, text: "The yucky tonic helps them recover." },
      pet:      { d: { social: 8, fun: 5 }, text: "A gentle stroke makes your Oddling purr!" }
    };
    if (which === "sleep") {
      p.sleeping = !p.sleeping;
      const kind = p.sleeping ? "sleep" : "wake";
      react(kind, 1700);
      message = p.sleeping ? "Lights out. Sweet dreams." : "Rise and shine!";
      chirp(kind);
    } else {
      const a = actions[which];
      if (!a) return;
      if ((which === "meal" || which === "snack") && p.hunger >= 97) {
        message = "Too full for another bite.";
        react("warn", 800);
        chirp("warn");
        refreshHome();
        return;
      }
      p.sleeping = false;
      add(p, a.d);
      p.careCount++;
      growPlant(p);
      recordCareMilestones(p);
      message = a.text;
      react(which);
      chirp(which);
    }
    snapshotSave();
    refreshHome(which === "sleep" || (wasSleeping && tab === 2));
  }
  function startGame() {
    advance();
    const p = state.pet;
    if (!p || p.death || stage(p) === "Egg") return;
    p.sleeping = false;
    snapshotSave();
    for (const track of touches.values()) stopHold(track);
    touches.clear(); pinchBase = null;
    game = { endAt: Date.now() + 20000, remainingMs: 20000, paused: false, hits: 0,
      cursorX: 180, cursorY: 124, stars: [] };
    for (let i = 0; i < 4; i++) game.stars.push(newStar());
    page = "game";
    message = "Catch the drifting sparks! Tap them, or use arrows/WASD + Space.";
    reaction = null;
    sparkBursts = [];
    chirp("play");
    show();
  }
  function newStar() {
    return { x: 35 + ui.random.next() * 290, y: 48 + ui.random.next() * 130,
      phase: ui.random.next() * Math.PI * 2, speed: .65 + ui.random.next() * 1.2 };
  }
  function starAt(s, t) {
    return { x: s.x + Math.sin(t * .0015 * s.speed + s.phase) * 8,
      y: s.y + Math.sin(t * .002 * s.speed + s.phase) * 6 };
  }
  function catchSpark(x, y) {
    if (!game) return;
    const t = Date.now();
    if (t >= game.endAt) { endGame(true); return; }
    for (let i = 0; i < game.stars.length; i++) {
      const q = starAt(game.stars[i], t);
      if (Math.hypot(q.x - x, q.y - y) <= 21) {
        game.hits++;
        sparkBursts.push({ x: q.x, y: q.y, at: t });
        game.stars[i] = newStar();
        chirp("star");
        return;
      }
    }
    chirp("warn");
  }
  function endGame(completed = false) {
    if (!game) return;
    const hits = game.hits;
    game = null;
    sparkBursts = [];
    page = "home";
    tab = 3;
    advance();
    const p = state.pet;
    if (p && !p.death && completed && hits > 0) {
      add(p, { fun: Math.min(55, 7 + hits * 4), social: Math.min(20, hits * 2),
        energy: -9, hunger: -5, clean: -3 });
      if (hits > p.bestGame) record(p, "A new Spark Chase record: " + hits + " sparks.");
      p.bestGame = Math.max(p.bestGame, hits);
      p.careCount++;
      growPlant(p);
      recordCareMilestones(p);
      message = "Caught " + hits + " sparks! " + p.name + " had fun.";
      react("play", 1750);
      snapshotSave();
      chirp("play");
    } else message = completed ? "No sparks caught this round. Try again!" :
      "Spark Chase ended early; no care bonus was awarded.";
    show();
  }
  function insidePet(x, y) {
    const range = 77 * petZoom;
    return x >= 180 - range && x <= 180 + range && y >= 55 && y <= 197;
  }
  // Work from raw start/move/end events, which are available on both touch and
  // desktop pointers. Gesture events are supported as an enhancement, not a
  // requirement: a mobile long-press and stroke still work without them.
  function stopHold(track) {
    if (track && track.stopHold) { track.stopHold(); track.stopHold = null; }
  }
  function consume(track) {
    if (!track) return;
    track.used = true;
    stopHold(track);
  }
  function locateTrack(e) {
    if (e.pointerId != null && touches.has(e.pointerId)) return touches.get(e.pointerId);
    // Some gesture events omit the id of the originating pointer.
    const candidates = Array.from(touches.values());
    if (!candidates.length) return null;
    if (Number.isFinite(e.x) && Number.isFinite(e.y)) {
      candidates.sort((a,b) => Math.hypot(a.x-e.x,a.y-e.y) - Math.hypot(b.x-e.x,b.y-e.y));
    }
    return candidates[0];
  }
  function updatePinch() {
    if (touches.size < 2) return;
    const [a,b] = Array.from(touches.values());
    const distance = Math.max(1, Math.hypot(a.x-b.x,a.y-b.y));
    if (!pinchBase) pinchBase = { zoom: petZoom, distance };
    petZoom = Math.max(.85, Math.min(1.3, pinchBase.zoom * distance / pinchBase.distance));
  }
  function scenePointer(e) {
    if (game && page === "game") {
      if (e.phase === "start") catchSpark(e.x, e.y);
      else if (e.phase === "move" && (e.pointers || 0) <= 1 &&
               Date.now() - lastCatchPointer > 110) {
        lastCatchPointer = Date.now();
        const near = game.stars.some(star => {
          const q = starAt(star, Date.now());
          return Math.hypot(q.x - e.x, q.y - e.y) < 21;
        });
        if (near) catchSpark(e.x, e.y);
      }
      return;
    }
    const p = state.pet;
    if (page !== "home" || !p || p.death) return;
    const id = e.pointerId ?? 0;
    if (e.phase === "start") {
      // Expire a pointer if the host had to replace the canvas mid-gesture.
      const prior = touches.get(id);
      stopHold(prior);
      const track = { id, startX: e.x, startY: e.y, x: e.x, y: e.y,
        began: Date.now(), used: false, moved: false, stopHold: null };
      touches.set(id,track);
      if (touches.size > 1 || (e.pointers || 1) > 1) {
        for (const other of touches.values()) consume(other);
        pinchBase = null;
        updatePinch();
      } else if (insidePet(e.x,e.y)) {
        track.stopHold = ui.timeout(() => {
          if (touches.get(id) !== track || track.used || track.moved ||
              page !== "home" || !state.pet || state.pet.death) return;
          consume(track);
          if (stage(state.pet) === "Egg") touchEgg(); else act("talk");
        }, 550);
      } else track.used = true;
      return;
    }
    if (e.phase === "move") {
      const track = touches.get(id) || locateTrack(e);
      if (!track) return;
      if (Number.isFinite(e.x)) track.x=e.x;
      if (Number.isFinite(e.y)) track.y=e.y;
      if (touches.size > 1) {
        for (const other of touches.values()) consume(other);
        updatePinch();
        return;
      }
      const distance = Math.hypot(track.x-track.startX,track.y-track.startY);
      if (distance > 15) {
        track.moved = true;
        stopHold(track);
        if (!track.used && (insidePet(track.x,track.y) || insidePet(track.startX,track.startY))) {
          consume(track);
          if (stage(p) === "Egg") touchEgg(); else act("pet");
        }
      }
      return;
    }
    if (e.phase === "gesture") {
      const track = locateTrack(e);
      if (e.gesture === "pinch") {
        for (const other of touches.values()) consume(other);
        if (!pinchBase) pinchBase = { zoom: petZoom, distance: 1 };
        if (Number.isFinite(e.scale) && e.scale > 0)
          petZoom = Math.max(.85, Math.min(1.3, pinchBase.zoom * e.scale));
      } else if (e.gesture === "longpress") {
        if (track && !track.used && !track.moved &&
            insidePet(track.startX,track.startY)) {
          consume(track);
          if (stage(p) === "Egg") touchEgg(); else act("talk");
        }
      } else if (e.gesture === "drag" || e.gesture === "swipe") {
        if (track && !track.used && insidePet(track.startX,track.startY) &&
            Math.max(Math.hypot(e.dx || 0,e.dy || 0),
              Math.hypot((e.x ?? track.x)-track.startX,(e.y ?? track.y)-track.startY)) > 15) {
          consume(track);
          if (stage(p) === "Egg") touchEgg(); else act("pet");
        }
      }
      return;
    }
    if (e.phase === "end" || e.phase === "cancel") {
      const track = touches.get(id) || locateTrack(e);
      if (!track) return;
      touches.delete(track.id);
      stopHold(track);
      if (touches.size < 2) pinchBase = null;
      if (e.phase === "cancel" || track.used || track.moved ||
          !insidePet(track.startX,track.startY) ||
          (Number.isFinite(e.x) && Number.isFinite(e.y) &&
           Math.hypot(e.x-track.startX,e.y-track.startY)>15)) return;
      // A long hold that ended before a delayed timer/gesture still means TALK.
      if (stage(p) === "Egg") touchEgg();
      else if (Date.now()-track.began >= 520) act("talk");
      else act("cuddle");
    }
  }
  // Render shapes into a low-resolution framebuffer instead of using anti-aliased
  // Canvas circles/curves. Every creature, skeleton, bubble, heart and spark is
  // genuinely drawn in a chunky 90x60 pixel grid and enlarged 4x, pixel-perfect.
  // Text uses StationNet's built-in VGA bitmap fonts (no bundled sprite assets).
  const { createPixelRenderer } = await ui.modules.load("pixel.js");
  const pixelScene = createPixelRenderer(ui, W, H, PIXEL_SIZE);
  const { createSceneRenderer } = await ui.modules.load("renderer.js");
  const sceneCanvas = createSceneRenderer(ui, W, H, byId, mood, stage, starAt, pixelScene,
    () => ({ state, reaction, petZoom, game, sparkBursts, compact, page,
      setSparkBursts: value => { sparkBursts = value; } }), scenePointer);
  function petThought(p) {
    if (p.death) return "The little nest is silent.";
    if (stage(p) === "Egg") return "A soft scratching sound comes from inside the shell.";
    if (p.health < 35) return "Seriously ill! Treat low needs now and give medicine.";
    if (p.health < 60) return "Feeling unwell. Keep all needs satisfied to recover.";
    if (p.sleeping) return p.name + " is snoozing peacefully.";
    if (p.hunger < 25) return "Tiny tummy noises. Definitely time for food.";
    if (p.clean < 25) return "A rather questionable smell surrounds the nest.";
    if (p.energy < 25) return "Those eyelids are getting very heavy.";
    if (p.social < 25) return "Your Oddling seems lonely and wants attention.";
    if (p.fun < 25) return "A bored little creature is staring at you.";
    if (mood(p) > 80) return p.name + " looks delighted to see you!";
    return p.name + " burbles something unintelligible.";
  }
  function statusLine(p) { return "Stage: " + stage(p); }
  function focusLine(p) {
    if (p.health < 60) return "Health is low. Restore depleted needs and use medicine.";
    const needs = [["fullness", p.hunger], ["hygiene", p.clean], ["energy", p.energy],
      ["fun", p.fun], ["company", p.social]];
    const [name, value] = needs.reduce((lowest, item) => item[1] < lowest[1] ? item : lowest);
    return value < 45 ? "Focus on " + name + " (" + round(value) + "%)." :
      "All needs are comfortable.";
  }
  function summary(p) {
    return ui.panel("At a glance", [
      ui.text("Mood " + round(mood(p)) + "% | Health " + round(p.health) + "%"),
      ui.text(focusLine(p), { tone: p.health < 60 ? "warning" : "muted" })
    ], { id: "summary" });
  }
  function stats(p) {
    return [ui.table(["Need", "Level"], [
      ["Fullness", round(p.hunger) + "%"], ["Hygiene", round(p.clean) + "%"],
      ["Energy", round(p.energy) + "%"], ["Fun", round(p.fun) + "%"],
      ["Bond", round(p.social) + "%"], ["Health", round(p.health) + "%"]]),
      ui.text("Lifetime happiness: " +
        Math.round(p.livedMs ? p.happyWeighted / p.livedMs : mood(p)) + "%")];
  }
  function refreshHome(refreshControls = false) {
    const p = state.pet;
    if (!p || p.death || page !== "home") { show(); return; }
    if (stage(p) !== displayedStage) { show(); return; }
    ui.update("status", ui.text(statusLine(p), { id: "status" }));
    ui.update("summary", summary(p));
    ui.update("message", ui.statusBar(message || "Tap: cuddle | Stroke: pet | Hold: chat", { id: "message" }));
    ui.update("thought", ui.text(petThought(p), { id: "thought" }));
    if (stage(p) !== "Egg") ui.update("vitals", ui.panel("Wellbeing", stats(p), { id: "vitals" }));
    if ((refreshControls || tab === 4) && stage(p) !== "Egg") ui.update("care-content", carePanel(p));
    ui.update("scene", sceneCanvas(false));
  }
  function rememberDraft(event) {
    if (!state.pet && event?.values) draftName = String(event.values.petname || "").slice(0, 20);
  }
  function fullscreen(event) {
    rememberDraft(event);
    const request = ui.presentation.fullscreen ? ui.presentation.exitFullscreen() :
      ui.presentation.requestFullscreen();
    request.catch(err => {
      message = "Fullscreen unavailable: " + String(err.message || err);
      ui.update("message", ui.statusBar(message, { id: "message" }));
    });
  }
  function viewControls() {
    return ui.row([
      ...(state.pet && !state.pet.death ? [ui.button("Life journal", () => { page = "journal"; show(); })] : []),
      ui.button("Memorials", event => { rememberDraft(event); advance(); page = "memorial"; show(); }),
      ui.button(ui.presentation.fullscreen ? "Exit fullscreen" : "Fullscreen", fullscreen),
      ui.button(ui.presentation.mode === "immersive" ? "Standard view" : "Immersive",
        event => {
          rememberDraft(event);
          if (ui.presentation.mode === "immersive") ui.presentation.exitImmersive();
          else ui.presentation.enterImmersive();
        })
    ], { id: "view-controls" });
  }
  function setTab(value) {
    tab = Math.max(0, Math.min(5, Number(value) || 0));
    const p = state.pet;
    if (!p || p.death) return;
    ui.update("care-tabs", careTabs());
    ui.update("care-content", carePanel(p));
  }
  function careTabs() {
    return ui.tabs(["Food", "Hygiene", "Company", "Play", "Garden", "Options"],
      ({ value }) => setTab(value), { selected: tab, id: "care-tabs" });
  }
  function choosePlant(kind) {
    const p = state.pet;
    if (!p || p.death || stage(p) === "Egg" || p.plant ||
        !["moonfern", "sunbud", "starvine"].includes(kind)) return;
    p.plant = { kind, growth: 0 };
    record(p, "A " + plantName(kind) + " seed was planted.");
    growPlant(p);
    message = "Your " + plantName(kind) + " has a place in the garden.";
    snapshotSave();
    ui.update("care-content", carePanel(p));
    refreshHome();
  }
  function zoom(delta) {
    petZoom = delta === 0 ? 1 : Math.max(.85, Math.min(1.3, Math.round((petZoom + delta) * 100) / 100));
    ui.update("scene", sceneCanvas(false));
  }
  function carePanel(p) {
    const content = [
      ui.row([ui.button("Feed meal", () => act("meal")), ui.button("Give treat", () => act("snack"))]),
      ui.row([ui.button("Bath", () => act("bath")), ui.button("Tidy nest", () => act("tidy")),
        ui.button("Medicine", () => act("medicine"))]),
      ui.row([ui.button("Cuddle", () => act("cuddle")), ui.button("Talk", () => act("talk")),
        ui.button("Stroke", () => act("pet")),
        ui.button(p.sleeping ? "Wake up" : "Go to sleep", () => act("sleep"))]),
      ui.column([ui.button("Play Spark Chase (20 seconds)", startGame),
        ui.text("Best catch: " + (p.bestGame || 0) + " sparks. Tap directly, or aim with the joystick and Catch button.")]),
      p.plant ? ui.column([
        ui.text(plantName(p.plant.kind) + " | Growth " + p.plant.growth + " of 4"),
        ui.text("Your plant grows as you care for your Oddling and as it matures.")
      ]) : ui.menu("Choose one plant for this life", [
        { label: "Moonfern", value: "moonfern" }, { label: "Sunbud", value: "sunbud" },
        { label: "Starvine", value: "starvine" }
      ], ({ value }) => choosePlant(value)),
      ui.column([
        ui.slider("sound", "Sound effects", { min: 0, max: 100, step: 5,
          value: state.settings.sound, onChange(value) {
            const next = Number(value?.value ?? value);
            state.settings.sound = Number.isFinite(next) ? clamp(next) : 65;
            snapshotSave();
          } }),
        ui.checkbox("reducedMotion", "Reduce animation", { checked: state.settings.reducedMotion,
          onChange: ({ value }) => { state.settings.reducedMotion = Boolean(value); snapshotSave(); } }),
        ui.button("Test sound", () => chirp("hatch")),
        ui.row([ui.button("Zoom out", () => zoom(-.1)), ui.button("Reset zoom", () => zoom(0)),
          ui.button("Zoom in", () => zoom(.1))]),
        ui.text("Tap: cuddle | Stroke/swipe: pet | Hold: talk | Two fingers: resize"),
        ui.text("Keyboard: F food | B bath | C cuddle | T talk | S sleep | M medicine | P play")
      ])
    ];
    return ui.panel("Care", [content[tab]], { id: "care-content" });
  }
  function keepsakeText(item) {
    const lines = [item.name + " the " + byId(item.species).name,
      "Arrived: " + date(item.createdAt ?? item.created),
      item.passed ? "Passed: " + date(item.passed) : "Current stage: " + stage(item),
      "Best Spark Chase: " + (item.bestGame || 0), "Care given: " + (item.careCount || 0)];
    if (item.plant) lines.push("Garden: " + plantName(item.plant.kind) + " (growth " + item.plant.growth + "/4)");
    if (item.memory) lines.push("Favorite memory: " + item.memory);
    lines.push("", "Life journal:", ...(item.journal || []).map(entry => date(entry.at) + " - " + entry.text));
    return lines.join("\n");
  }
  async function exportKeepsake(item) {
    try { await ui.download.text("oddling-" + item.name.toLowerCase().replace(/[^a-z0-9]+/g, "-") + ".txt", keepsakeText(item)); }
    catch (error) { message = "Could not download keepsake: " + String(error.message || error); show(); }
  }
  function show() {
    if (loadFailed) {
      ui.render(ui.title("ODDLING GARDEN // SAVE NEEDS ATTENTION"),
        ui.text(loadMessage, { tone: "warning" }),
        ui.text("Your existing data has not been changed."),
        ...(rawSave == null ? [] : [ui.button("Download saved data", async () => {
          try { await ui.download.text("oddling-garden-backup.json", JSON.stringify(rawSave, null, 2)); }
          catch (error) { loadMessage = "Could not download backup: " + String(error.message || error); show(); }
        })]),
        ui.row([ui.button("Retry loading", retryLoad), ui.button("Start fresh...", startFresh)]));
      return;
    }
    const p = state.pet;
    if (p && p.death) {
      page = "home";
      displayedStage = null;
      displayedDeath = true;
      const old = p.death.cause === "old age";
      ui.render(ui.title("ODDLING GARDEN // GOODBYE"),
        ui.text(old ? p.name + " reached the end of a full fourteen-day life." :
          p.name + " became too ill after needs went unmet for too long."),
        sceneCanvas(),
        ui.text("Passed " + date(p.death.at) + " | Age " + ageText(p.death.at - p.createdAt) +
          " | Cause: " + (old ? "old age" : "neglect")),
        ...(p.death.reason ? [ui.text(p.death.reason + ".")] : []),
        ui.text("Their skeleton rests in the habitat until you are ready to say goodbye."),
        ui.button("Say goodbye / view memorial", layToRest), saveNotice(), viewControls());
      return;
    }
    displayedDeath = false;
    if (page === "memorial") {
      const items = state.memorials;
      memorialPage = Math.max(0, Math.min(Math.ceil(items.length / 8) - 1, memorialPage));
      selectedMemorial = Math.max(0, Math.min(items.length - 1, selectedMemorial));
      const start = memorialPage * 8;
      const selected = items[selectedMemorial];
      ui.render(ui.title("ODDLING GARDEN // MEMORIAL"),
        ui.text("Every life leaves a little glow behind."),
        items.length ? ui.menu("Companions | page " + (memorialPage + 1) + " of " + Math.ceil(items.length / 8),
          items.slice(start, start + 8).map((m, index) => ({
            label: (start + index === selectedMemorial ? "* " : "") + m.name + " the " + byId(m.species).name,
            value: String(start + index) })), ({ value }) => { selectedMemorial = Number(value); show(); }) :
          ui.text("No departed companions yet."),
        ...(items.length > 8 ? [ui.row([
          ...(memorialPage > 0 ? [ui.button("Previous page", () => { memorialPage--; selectedMemorial = memorialPage * 8; show(); })] : []),
          ...(start + 8 < items.length ? [ui.button("Next page", () => { memorialPage++; selectedMemorial = memorialPage * 8; show(); })] : [])
        ])] : []),
        ...(selected ? [ui.panel(selected.name + " the " + byId(selected.species).name, [
          ui.text("Lifetime happiness: " + selected.happiness + "% | " + ageText(selected.passed - selected.created)),
          ui.text(date(selected.created) + " - " + date(selected.passed) + " | Final stage: " + selected.lastStage),
          ui.text("Cause: " + selected.cause + (selected.reason ? " | " + selected.reason : "")),
          ui.text("Care given: " + selected.careCount + " | Best Spark Chase: " + selected.bestGame),
          ...(selected.plant ? [ui.text("Garden: " + plantName(selected.plant.kind) + " | Growth " + selected.plant.growth + "/4")] : []),
          ...(selected.memory ? [ui.text("Favorite memory: " + selected.memory)] : []),
          ui.list((selected.journal || []).map(entry => date(entry.at) + " - " + entry.text)),
          ui.button("Download keepsake", () => exportKeepsake(selected))
        ])] : []),
        ui.button(p ? "Return to " + p.name : "Adopt a new egg", () => { page = "home"; message = ""; show(); }),
        saveNotice(), viewControls());
      return;
    }
    if (!p) {
      displayedStage = null;
      ui.render(ui.title("ODDLING GARDEN"),
        ui.text("Raise a strange little companion. Oddlings live up to 14 real days. Check in regularly: severe unmet needs can end a life early."),
        ui.heading("Choose your mystery egg"),
        ui.menu("Species", SPECIES.map(s => ({ label: (s.id === chosen ? "* " : "") + s.name, value: s.id })),
          ({ value, values }) => { draftName = String(values?.petname || draftName).slice(0, 20); chosen = value; show(); }),
        ui.text(byId(chosen).description + " Trait: " + byId(chosen).trait + ". " + byId(chosen).care),
        ui.input("petname", "Give your Oddling a name", { value: draftName, maxLength: 20 }),
        ui.button("Adopt egg", ({ values }) => createPet(values?.petname ?? draftName)),
        ui.text("Hatches in about 2 minutes. Warm the egg to speed things up."),
        saveNotice(), viewControls());
      return;
    }
    displayedStage = stage(p);
    if (page === "journal") {
      ui.render(ui.title("ODDLING GARDEN // LIFE JOURNAL"),
        ui.text(p.name + " the " + byId(p.species).name),
        ui.list((p.journal || []).map(entry => date(entry.at) + " - " + entry.text)),
        ui.input("memory", "One favorite memory (140 characters)", { value: p.memory || "", maxLength: 140 }),
        ui.button("Save memory", ({ values }) => {
          p.memory = String(values?.memory || "").trim().slice(0, 140);
          message = "Favorite memory saved."; snapshotSave(); show();
        }),
        ui.button("Download keepsake", () => exportKeepsake(p)),
        ui.button("Return to habitat", () => { page = "home"; show(); }),
        saveNotice(), viewControls());
      return;
    }
    if (page === "game" && game) {
      ui.render(ui.title("ODDLING GARDEN // SPARK CHASE"),
        ui.statusBar(message, { id: "message" }), sceneCanvas(),
        ui.text("Catch sparks for 20 seconds. Tap or sweep; touch joystick + Catch; arrows/WASD + Space; gamepad stick + A."),
        ui.row([ui.button("End round early", () => endGame(false)),
          ui.button(ui.presentation.fullscreen ? "Exit fullscreen" : "Fullscreen", fullscreen)]),
        saveNotice());
      return;
    }
    const egg = stage(p) === "Egg";
    ui.render(ui.title("ODDLING GARDEN"),
      ui.text(p.name + " the " + byId(p.species).name + " | " + byId(p.species).trait),
      sceneCanvas(),
      ui.text(statusLine(p), { id: "status" }),
      ...(egg ? [ui.row([ui.button("Warm egg", touchEgg),
        ui.text("Tap or stroke the shell to warm it.")])] : [summary(p)]),
      ui.statusBar(message || (egg ? "An egg waits quietly." : "Tap: cuddle | Stroke: pet | Hold: talk"), { id: "message" }),
      ui.text(petThought(p), { id: "thought" }),
      ...(!egg ? [careTabs(), carePanel(p), ui.panel("Wellbeing", stats(p), { id: "vitals" })] : []),
      saveNotice(), viewControls());
  }
  async function retryLoad() {
    try {
      const loaded = await ui.storage.get(KEY);
      rawSave = loaded;
      const recovered = normalizeSave(loaded);
      state = recovered;
      rawSave = null;
      loadFailed = false;
      loadMessage = "";
      if (state.pet) advance();
      show();
      snapshotSave();
    } catch (error) {
      loadFailed = true;
      loadMessage = "Could not load your saved garden: " + String(error.message || error);
      show();
    }
  }
  async function startFresh() {
    const choice = await ui.dialog("Replace unreadable save?", [
      ui.text("Starting fresh will replace the saved garden and memorials. This cannot be undone.")
    ], [{ label: "Start fresh", value: "replace" }, { label: "Cancel", value: "cancel" }]);
    if (choice !== "replace") return;
    state = freshState(); loadFailed = false; loadMessage = "";
    if (!await snapshotSave()) {
      loadFailed = true;
      loadMessage = saveMessage + ". Your previous save has not been replaced.";
    } else rawSave = null;
    show();
  }
  try {
    const viewport = await ui.display.viewport();
    portrait = viewport.orientation === "portrait";
    compact = portrait && viewport.width < 600;
  } catch (_) { /* Automatic responsive scaling remains available. */ }
  try {
    const loaded = await ui.storage.get(KEY);
    rawSave = loaded;
    state = normalizeSave(loaded);
    rawSave = null;
  }
  catch (err) {
    loadFailed = true;
    loadMessage = "Could not load your saved garden: " + String(err.message || err);
  }
  if (!loadFailed && state.pet) advance();
  show();
  ui.onKey(({ code, phase, repeat }) => {
    if (loadFailed || phase !== "down" || repeat) return;
    if (page === "game" && game) {
      if (code === "Space") catchSpark(game.cursorX,game.cursorY);
      if (code === "KeyQ" || code === "Escape") { endGame(false); return; }
    } else if (page === "home" && state.pet && !state.pet.death) {
      const actions = { KeyF:"meal",KeyB:"bath",KeyC:"cuddle",KeyT:"talk",
        KeyS:"sleep",KeyM:"medicine",KeyP:"play" };
      if (actions[code] === "play") startGame();
      else if (actions[code]) act(actions[code]);
    }
  });
  ui.animate(({ time, delta }) => {
    if (loadFailed) return;
    if (game?.paused) return;
    if (game) {
      // The host's mobile joystick and a physical controller are both optional.
      let ax = 0, ay = 0;
      if (ui.input && typeof ui.input.axis === "function") {
        const stick = ui.input.axis("oddling-aim");
        ax += stick.x || 0; ay += stick.y || 0;
      }
      if (ui.input && typeof ui.input.gamepad === "function") {
        const pad = ui.input.gamepad(0, { deadzone: .16 });
        ax += (pad.axes?.[0] || 0); ay += (pad.axes?.[1] || 0);
        const a = (pad.buttons?.[0] || 0) > .55;
        const b = (pad.buttons?.[1] || 0) > .55;
        if (a && !gamepadCatchDown) {
          catchSpark(game.cursorX,game.cursorY);
          if (!game) return;
        }
        if (b && !gamepadBackDown) { endGame(false); return; }
        gamepadCatchDown = a; gamepadBackDown = b;
      }
      if (Math.abs(ax) <= .08 && Math.abs(ay) <= .08 && ui.input?.isDown) {
        ax = Number(ui.input.isDown("ArrowRight") || ui.input.isDown("KeyD")) -
          Number(ui.input.isDown("ArrowLeft") || ui.input.isDown("KeyA"));
        ay = Number(ui.input.isDown("ArrowDown") || ui.input.isDown("KeyS")) -
          Number(ui.input.isDown("ArrowUp") || ui.input.isDown("KeyW"));
      }
      const speed = Math.min(.05, Math.max(0, delta || 0)) * 165;
      if (Math.abs(ax) > .08 || Math.abs(ay) > .08) {
        game.cursorX = Math.max(25,Math.min(335,game.cursorX + ax * speed));
        game.cursorY = Math.max(47,Math.min(183,game.cursorY + ay * speed));
      }
    } else { gamepadCatchDown = false; gamepadBackDown = false; }
    if (time - frameTime < (game ? 33 : state.settings.reducedMotion ? 200 : 90)) return;
    frameTime = time;
    if (game && Date.now() >= game.endAt) { endGame(true); return; }
    if (state.pet && Date.now() - lastStatusRefresh >= 10000) {
      lastStatusRefresh = Date.now();
      const before = displayedStage;
      advance();
      if (state.pet.death && !displayedDeath) { show(); return; }
      if (!state.pet.death && page === "home") {
        if (stage(state.pet) !== before) {
          message = before === "Egg" ? "The egg cracked open! Meet " + state.pet.name + "!" :
            state.pet.name + " has grown into a " + stage(state.pet).toLowerCase() + "!";
          react("hatch", 1750);
          chirp("hatch");
          snapshotSave();
          show();
        } else refreshHome();
      }
    }
    if (state.pet && !state.pet.death && Date.now() - lastPersist >= 30000) {
      lastPersist = Date.now();
      advance();
      snapshotSave();
    }
    if (page === "home" && state.pet || page === "game" && game) {
      ui.update("scene",sceneCanvas(false));
    }
  });
  ui.onVisibility(({ value }) => {
    if (loadFailed) return;
    if (value === "visible") {
      const p = state.pet;
      const before = p && !p.death ? stage(p,p.lastUpdate) : null;
      advance();
      if (state.pet && state.pet.death) {
        page = "home";
        game = null;
        reaction = null;
        show();
      } else if (game?.paused) {
        game.endAt = Date.now() + game.remainingMs;
        game.paused = false;
        if (game.remainingMs <= 0) endGame(true);
      } else if (state.pet && before === "Egg" && stage(state.pet) !== "Egg") {
        message = state.pet.name + " has hatched!";
        react("hatch", 1750);
        chirp("hatch");
        show();
      } else if (state.pet && page === "home") {
        if (stage(state.pet) !== displayedStage) show(); else refreshHome();
      }
      snapshotSave();
    } else {
      if (game) {
        game.remainingMs = Math.max(0, game.endAt - Date.now());
        game.paused = true;
      }
      advance();
      snapshotSave();
    }
  });
  ui.presentation.onChange((_mode, viewport) => {
    if (viewport) {
      portrait = viewport.orientation === "portrait";
      compact = portrait && viewport.width < 600;
    }
    ui.update("view-controls", viewControls());
    if (state.pet && (page === "home" || page === "game"))
      ui.update("scene", sceneCanvas(true));
  });
  ui.onCleanup(() => {
    for (const track of touches.values()) stopHold(track);
    touches.clear();
    if (!loadFailed) { advance(); snapshotSave(); }
  });
});
