"use strict";

StationNet.start(async (ui) => {
  const { ARENAS, createRound, queueDirection, turn, step, chooseComputerDirection, safeInset } = await ui.modules.load("engine.js");
  const COLORS = ["#55eaff", "#ffbd52", "#ff6ad9", "#a6ff61"];
  const PALETTE = [
    [0, 0, 0], [3, 14, 12], [8, 25, 21],
    [12, 102, 119], [130, 77, 15], [121, 23, 92], [61, 116, 24],
    [85, 234, 255], [255, 189, 82], [255, 106, 217], [166, 255, 97], [58, 16, 20]
  ];
  const SETTINGS = { riders: 4, humans: 1, arena: "standard", speed: "normal", rounds: 5 };
  const PERIOD = { relaxed: 210, normal: 150, rapid: 105 };
  const KEYS = [
    { ArrowRight: 0, ArrowDown: 1, ArrowLeft: 2, ArrowUp: 3 },
    { KeyD: 0, KeyS: 1, KeyA: 2, KeyW: 3 },
    { KeyL: 0, KeyK: 1, KeyJ: 2, KeyI: 3 },
    { KeyH: 0, KeyG: 1, KeyF: 2, KeyT: 3 }
  ];
  const EMPTY_STATS = { matches: 0, wins: 0, rounds: 0, roundWins: 0, crashes: 0, bestRide: 0 };
  let stats = { ...EMPTY_STATS }, round = null, scores = [0, 0, 0, 0], roundNumber = 0;
  let mode = "setup", elapsed = 0, frame = null, frameArena = "", notice = "", pointerStart = null;
  const frames = {};
  let saveQueue = Promise.resolve();

  function persist() {
    const snapshot = { ...stats };
    saveQueue = saveQueue.then(() => ui.storage.set("light-cycles-stats", snapshot)).catch(() => {});
  }

  function tone(kind) {
    if (kind === "turn") ui.sound.playTone(320, 35, { waveform: "triangle", volume: .025 });
    else if (kind === "crash") ui.sound.play([{ frequency: 130, duration: 130, waveform: "sawtooth", volume: .055 }, { frequency: 82, delay: 65, duration: 160, waveform: "square", volume: .035 }]);
    else if (kind === "win") ui.sound.sequence([440, 554, 659, 880].map((frequency) => ({ frequency, duration: 95, waveform: "triangle", volume: .06 })));
    else if (kind === "start") ui.sound.sequence([220, 330, 440].map((frequency) => ({ frequency, duration: 65, waveform: "square", volume: .035 })));
  }

  function updateStats() {
    ui.update("stats", ui.text(`PERSONAL // ${stats.matches} matches · ${stats.wins} match wins · ${stats.rounds} rounds · ${stats.roundWins} round wins · ${stats.crashes} crashes · ${stats.bestRide} cells best ride`, { id: "stats", tone: "muted" }));
  }

  function setup() {
    mode = "setup";
    const setting = (name, label, items, value, apply) => ui.select(name, label, items, {
      id: name === "humans" ? "human-setting" : undefined,
      value: String(value), onChange: ({ value: selected }) => { apply(selected); updateSummary(); }
    });
    ui.render(
      ui.title("Light Cycles // Arena"),
      ui.text("Ride continuously. Turn before you meet a wall, a trail, or another bike. Last rider moving wins the round. The arena closes in during long rounds.", { tone: "muted" }),
      ui.text("", { id: "summary", emphasis: true }),
      setting("riders", "Participants", ["1", "2", "3", "4"], SETTINGS.riders, (value) => {
        SETTINGS.riders = Number(value); SETTINGS.humans = Math.min(SETTINGS.humans, SETTINGS.riders);
        ui.update("human-setting", setting("humans", "Local players", ["1", "2", "3", "4"].slice(0, SETTINGS.riders), SETTINGS.humans, (next) => { SETTINGS.humans = Number(next); }));
      }),
      setting("humans", "Local players", ["1", "2", "3", "4"], SETTINGS.humans, (value) => { SETTINGS.humans = Number(value); }),
      setting("arena", "Arena size", [{ label: "Compact", value: "compact" }, { label: "Standard", value: "standard" }, { label: "Wide", value: "wide" }], SETTINGS.arena, (value) => { SETTINGS.arena = value; }),
      setting("speed", "Cycle speed", [{ label: "Relaxed", value: "relaxed" }, { label: "Normal", value: "normal" }, { label: "Rapid", value: "rapid" }], SETTINGS.speed, (value) => { SETTINGS.speed = value; }),
      setting("rounds", "Match length", ["3", "5", "7"], SETTINGS.rounds, (value) => { SETTINGS.rounds = Number(value); }),
      ui.button("Start match", startMatch),
      ui.text("KEYS // P1 arrows  ·  P2 WASD  ·  P3 IJKL  ·  P4 TFGH. Space pauses. Touch players use their own turn buttons; P1 can also swipe the arena.", { tone: "muted" }),
      ui.text("", { id: "stats", tone: "muted" }),
      ui.heading("Station leaderboard // best match round wins", 3),
      ui.table(["#", "Rider", "Wins"], [["—", "Loading…", "—"]], { id: "leaderboard" })
    );
    updateSummary(); updateStats();
    ui.scores.leaderboard().then(({ leaderboard }) => {
      const rows = (leaderboard || []).map((entry, index) => [index + 1, entry.username, entry.score]);
      ui.update("leaderboard", ui.table(["#", "Rider", "Wins"], rows.length ? rows : [["—", "No scores yet", "—"]], { id: "leaderboard" }));
    }).catch(() => ui.update("leaderboard", ui.text("Leaderboard unavailable", { id: "leaderboard", tone: "muted" })));
  }

  function updateSummary() {
    ui.update("summary", ui.text(`${SETTINGS.riders} riders  ·  ${SETTINGS.humans} local  ·  ${SETTINGS.riders - SETTINGS.humans} CPU  ·  ${SETTINGS.rounds} rounds`, { id: "summary", emphasis: true }));
  }

  function ensureFrame() {
    if (frameArena === SETTINGS.arena) return;
    const [width, height] = ARENAS[SETTINGS.arena];
    frame = frames[SETTINGS.arena];
    if (!frame) {
      frame = ui.graphics.framebuffer(`light-cycles-${SETTINGS.arena}`, width, height, { format: "indexed" });
      for (let i = 0; i < PALETTE.length; i += 1) frame.palette.set([...PALETTE[i], 255], i * 4);
      frames[SETTINGS.arena] = frame;
    }
    frameArena = SETTINGS.arena;
  }

  function draw() {
    if (!round) return;
    ensureFrame();
    const pixels = frame.pixels;
    const inset = safeInset(round.ticks);
    for (let y = 0; y < round.height; y += 1) for (let x = 0; x < round.width; x += 1) {
      const i = y * round.width + x, owner = round.occupied[i];
      pixels[i] = x < inset || x >= round.width - inset || y < inset || y >= round.height - inset ? 11 :
        owner ? owner + 2 : ((x % 6 === 0 || y % 6 === 0) ? 2 : 1);
    }
    for (const rider of round.riders) if (rider.alive) pixels[rider.y * round.width + rider.x] = rider.id + 7;
    frame.present();
    const cellW = 640 / round.width, cellH = 400 / round.height;
    const commands = [ui.graphics.frame(frame, 0, 0, 640, 400)];
    if (inset > 0) {
      const x = inset * cellW, y = inset * cellH;
      commands.push({ type: "rect", x, y, width: 640 - x * 2, height: 2, color: "#ff6363" },
        { type: "rect", x, y: 400 - y - 2, width: 640 - x * 2, height: 2, color: "#ff6363" },
        { type: "rect", x, y, width: 2, height: 400 - y * 2, color: "#ff6363" },
        { type: "rect", x: 640 - x - 2, y, width: 2, height: 400 - y * 2, color: "#ff6363" });
    }
    for (const rider of round.riders) if (rider.alive) {
      const cx = (rider.x + .5) * cellW, cy = (rider.y + .5) * cellH;
      commands.push({ type: "circle", x: cx, y: cy, radius: Math.min(cellW, cellH) * .75, color: COLORS[rider.id] });
      commands.push({ type: "circle", x: cx, y: cy, radius: Math.min(cellW, cellH) * .3, color: "#ffffff" });
    }
    commands.push({ type: "rect", x: 0, y: 0, width: 640, height: 3, color: "#299b73" },
      { type: "rect", x: 0, y: 397, width: 640, height: 3, color: "#299b73" },
      { type: "rect", x: 0, y: 0, width: 3, height: 400, color: "#299b73" },
      { type: "rect", x: 637, y: 0, width: 3, height: 400, color: "#299b73" });
    ui.update("arena", ui.canvas(640, 400, commands, { id: "arena", label: "Light Cycles arena", onPointer: pointer }));
  }

  function updateHud() {
    ui.update("score", ui.text(round.riders.map((rider) => `P${rider.id + 1} ${rider.id < SETTINGS.humans ? "LOCAL" : "CPU"} ${scores[rider.id]}${rider.alive ? " ●" : " ×"}`).join("    "), { id: "score", emphasis: true }));
    ui.update("round", ui.text(`ROUND ${roundNumber} / ${SETTINGS.rounds}  ·  ${PERIOD[SETTINGS.speed]} ms / cell`, { id: "round", tone: "muted" }));
    ui.update("status", ui.text(mode === "running" && round.ticks >= 170 ? "WARNING // ARENA CLOSING" : notice, { id: "status", tone: round.ticks >= 170 && mode === "running" ? "warning" : mode === "roundover" || mode === "matchover" ? "accent" : "muted" }));
  }

  function updateActions() {
    ui.update("action", ui.button(mode === "ready" ? "Start round" : mode === "paused" ? "Resume" : mode === "roundover" ? "Next round" : mode === "matchover" ? "New match" : "Pause", () => {
      if (mode === "ready" || mode === "paused") resume();
      else if (mode === "roundover") nextRound();
      else if (mode === "matchover") setup();
      else pause();
    }, { id: "action" }));
  }

  function controls() {
    const rows = [];
    for (let id = 0; id < SETTINGS.humans; id += 1) {
      const rider = id;
      rows.push(ui.menu(`P${id + 1} // ${["CYAN", "AMBER", "MAGENTA", "LIME"][id]} turn`, [
        { label: "↶ LEFT", value: "left" }, { label: "RIGHT ↷", value: "right" }
      ], ({ value }) => { if (mode === "running" && turn(round, rider, value)) tone("turn"); }));
    }
    return rows;
  }

  function startMatch() {
    scores = [0, 0, 0, 0]; roundNumber = 0;
    nextRound(true);
    ui.storage.set("light-cycles-settings", { ...SETTINGS }).catch(() => {});
  }

  function nextRound(first = false) {
    roundNumber += 1; round = createRound(SETTINGS.riders, SETTINGS.arena);
    mode = "ready"; elapsed = 0; notice = SETTINGS.riders === 1 ? "SOLO // SURVIVE AS LONG AS YOU CAN" : "READY // LAST RIDER MOVING WINS";
    if (first) {
      ui.render(
        ui.title("Light Cycles // Arena"),
        ui.text("", { id: "round", tone: "muted" }),
        ui.text("", { id: "score", emphasis: true }),
        ui.text("", { id: "status", tone: "accent" }),
        ui.canvas(640, 400, [], { id: "arena", label: "Light Cycles arena", onPointer: pointer }),
        ui.button("Start round", resume, { id: "action" }),
        ...controls(),
        ui.text("Space pauses · Swipe to steer P1 · A reverse turn is ignored", { tone: "muted" }),
        ui.text("", { id: "stats", tone: "muted" })
      );
      updateStats();
    }
    updateHud(); updateActions(); draw();
  }

  function resume() {
    if (mode !== "ready" && mode !== "paused") return;
    mode = "running"; elapsed = 0; notice = "RUNNING"; tone("start"); updateHud(); updateActions();
  }

  function pause(message = "PAUSED") {
    if (mode !== "running") return;
    mode = "paused"; elapsed = 0; notice = message; updateHud(); updateActions();
  }

  function finishRound() {
    if (mode !== "running") return;
    mode = roundNumber >= SETTINGS.rounds ? "matchover" : "roundover";
    stats.rounds += 1;
    for (const rider of round.riders.slice(0, SETTINGS.humans)) {
      stats.bestRide = Math.max(stats.bestRide, rider.survived);
      if (!rider.alive) stats.crashes += 1;
    }
    if (round.winner !== null) {
      scores[round.winner] += 1;
      if (round.winner < SETTINGS.humans) stats.roundWins += 1;
      notice = `ROUND ${roundNumber} // P${round.winner + 1} WINS`;
      tone("win");
    } else if (SETTINGS.riders === 1) {
      scores[0] += Math.floor(round.riders[0].survived / 10);
      notice = `SOLO RIDE // ${round.riders[0].survived} CELLS`;
    } else notice = `ROUND ${roundNumber} // DRAW`;
    if (mode === "matchover") {
      stats.matches += 1;
      const highest = Math.max(...scores.slice(0, SETTINGS.riders));
      const leaders = scores.slice(0, SETTINGS.riders).map((score, id) => score === highest ? id : -1).filter((id) => id >= 0);
      if (SETTINGS.riders > 1 && leaders.length === 1 && leaders[0] < SETTINGS.humans) stats.wins += 1;
      notice += SETTINGS.riders === 1 ? ` // TOTAL ${scores[0]} POINTS` : leaders.length === 1 ? ` // MATCH P${leaders[0] + 1} WINS` : " // MATCH DRAW";
      const localBest = Math.max(...scores.slice(0, SETTINGS.humans));
      if (SETTINGS.riders > 1 && localBest > 0) ui.scores.submit(localBest).catch(() => {});
    }
    persist(); updateStats(); updateHud(); updateActions(); draw();
  }

  function pointer(event) {
    if (event.phase === "start") pointerStart = [event.x, event.y];
    if (event.phase !== "end" || !pointerStart || mode !== "running") return;
    const dx = event.x - pointerStart[0], dy = event.y - pointerStart[1]; pointerStart = null;
    if (Math.max(Math.abs(dx), Math.abs(dy)) < 18) return;
    const direction = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 0 : 2) : (dy > 0 ? 1 : 3);
    if (queueDirection(round, 0, direction)) tone("turn");
  }

  ui.onKey(({ code, phase, repeat }) => {
    if (phase !== "down" || repeat) return;
    if (code === "Space") { if (mode === "running") pause(); else if (mode === "paused") resume(); return; }
    if (code === "Enter") { if (mode === "ready" || mode === "paused") resume(); else if (mode === "roundover") nextRound(); return; }
    if (mode !== "running") return;
    for (let id = 0; id < SETTINGS.humans; id += 1) {
      const direction = KEYS[id][code];
      if (direction !== undefined && queueDirection(round, id, direction)) tone("turn");
    }
  });
  ui.onVisibility(({ value }) => { if (value === "hidden") pause("PAUSED // WINDOW INACTIVE"); });
  ui.animate(({ delta }) => {
    if (mode !== "running") return;
    elapsed += Math.min(delta * 1000, 250);
    if (elapsed < PERIOD[SETTINGS.speed]) return;
    elapsed -= PERIOD[SETTINGS.speed];
    for (let id = SETTINGS.humans; id < SETTINGS.riders; id += 1) {
      const direction = chooseComputerDirection(round, id);
      if (direction !== null) queueDirection(round, id, direction);
    }
    const result = step(round);
    if (result.eliminated.length) tone("crash");
    if (result.over) finishRound();
    else { updateHud(); draw(); }
  });
  const [storedStats, storedSettings] = await Promise.all([
    ui.storage.get("light-cycles-stats").catch(() => null),
    ui.storage.get("light-cycles-settings").catch(() => null)
  ]);
  if (storedStats && typeof storedStats === "object") for (const key of Object.keys(EMPTY_STATS)) {
    const value = storedStats[key]; if (Number.isSafeInteger(value) && value >= 0) stats[key] = value;
  }
  if (storedSettings && typeof storedSettings === "object") {
    if ([1, 2, 3, 4].includes(storedSettings.riders)) SETTINGS.riders = storedSettings.riders;
    if ([1, 2, 3, 4].includes(storedSettings.humans)) SETTINGS.humans = Math.min(storedSettings.humans, SETTINGS.riders);
    if (ARENAS[storedSettings.arena]) SETTINGS.arena = storedSettings.arena;
    if (PERIOD[storedSettings.speed]) SETTINGS.speed = storedSettings.speed;
    if ([3, 5, 7].includes(storedSettings.rounds)) SETTINGS.rounds = storedSettings.rounds;
  }
  setup();
});
