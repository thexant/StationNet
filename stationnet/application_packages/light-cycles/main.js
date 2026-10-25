"use strict";

StationNet.start(async (ui) => {
  const { ARENAS, DIRECTIONS, createRound, queueDirection, turn, step, chooseComputerDirection, safeInset } = await ui.modules.load("engine.js");
  const WIDTH = 640, HEIGHT = 400, BUFFER_W = 320, BUFFER_H = 200;
  const COLORS = ["#55eaff", "#ffbd52", "#ff6ad9", "#a6ff61"];
  const DIM_COLORS = ["#167f91", "#95691f", "#8f286f", "#56872c"];
  const NAMES = ["CYAN", "AMBER", "MAGENTA", "LIME"];
  const SETTINGS = { riders: 4, humans: 1, arena: "standard", speed: "arcade", difficulty: "arcade", rounds: 5 };
  const PERIOD = { cruise: 118, arcade: 88, turbo: 66 };
  const KEYS = [
    { ArrowRight: 0, ArrowDown: 1, ArrowLeft: 2, ArrowUp: 3 },
    { KeyD: 0, KeyS: 1, KeyA: 2, KeyW: 3 },
    { KeyL: 0, KeyK: 1, KeyJ: 2, KeyI: 3 },
    { KeyH: 0, KeyG: 1, KeyF: 2, KeyT: 3 }
  ];
  const EMPTY_STATS = { matches: 0, wins: 0, rounds: 0, roundWins: 0, crashes: 0, bestRide: 0 };
  const PALETTE = [
    [1, 4, 9], [3, 13, 23], [7, 26, 39], [10, 52, 68],
    [22, 127, 145], [149, 105, 31], [143, 40, 111], [86, 135, 44],
    [85, 234, 255], [255, 189, 82], [255, 106, 217], [166, 255, 97],
    [75, 8, 18], [255, 57, 88]
  ];

  let stats = { ...EMPTY_STATS }, scores = [0, 0, 0, 0], round = null, roundNumber = 0;
  let mode = "setup", pausedMode = "running", accumulator = 0, countdownMs = 0, countdownBeat = 0, notice = "";
  let frame = null, pointerStart = null, effects = [], hudClock = 0, saveQueue = Promise.resolve();
  let paintedLengths = [], paintedInset = -1, renderOrigins = [];

  function persist() {
    const snapshot = { ...stats };
    saveQueue = saveQueue.then(() => ui.storage.set("light-cycles-stats", snapshot)).catch(() => {});
  }

  function tone(kind, value = 0) {
    if (kind === "turn") ui.sound.playTone(420, 28, { waveform: "triangle", volume: .025, attack: 2, release: 18 });
    else if (kind === "count") ui.sound.playTone(value ? 330 : 660, value ? 75 : 130, { waveform: "square", volume: .045, attack: 4, release: 45 });
    else if (kind === "crash") ui.sound.play([{ frequency: 155, duration: 150, waveform: "sawtooth", volume: .065 }, { frequency: 74, delay: 45, duration: 240, waveform: "square", volume: .04 }]);
    else if (kind === "win") ui.sound.sequence([392, 523, 659, 784].map((frequency) => ({ frequency, duration: 105, waveform: "triangle", volume: .06 })));
  }

  function updateStats() {
    ui.update("stats", ui.text(`PERSONAL // ${stats.matches} matches · ${stats.wins} match wins · ${stats.roundWins} round wins · ${stats.crashes} crashes · ${stats.bestRide} cells best`, { id: "stats", tone: "muted" }));
  }

  function updateSummary() {
    ui.update("summary", ui.text(`${SETTINGS.riders} riders · ${SETTINGS.humans} local · ${SETTINGS.riders - SETTINGS.humans} CPU · ${SETTINGS.rounds} rounds`, { id: "summary", emphasis: true }));
  }

  function setup() {
    mode = "setup";
    const setting = (name, label, items, value, apply) => ui.select(name, label, items, {
      id: name === "humans" ? "human-setting" : undefined,
      value: String(value), onChange: ({ value: selected }) => { apply(selected); updateSummary(); }
    });
    ui.render(
      ui.title("Light Cycles // Neon Arena"),
      ui.text("Cut across the grid at full speed. Your light wall is permanent: one touch of a border, trail, or rival ends the ride.", { tone: "muted" }),
      ui.text("", { id: "summary", emphasis: true }),
      setting("riders", "Participants", ["1", "2", "3", "4"], SETTINGS.riders, (value) => {
        SETTINGS.riders = Number(value); SETTINGS.humans = Math.min(SETTINGS.humans, SETTINGS.riders);
        ui.update("human-setting", setting("humans", "Local players", ["1", "2", "3", "4"].slice(0, SETTINGS.riders), SETTINGS.humans, (next) => { SETTINGS.humans = Number(next); updateSummary(); }));
      }),
      setting("humans", "Local players", ["1", "2", "3", "4"], SETTINGS.humans, (value) => { SETTINGS.humans = Number(value); }),
      setting("arena", "Arena size", [{ label: "Compact", value: "compact" }, { label: "Standard", value: "standard" }, { label: "Wide", value: "wide" }], SETTINGS.arena, (value) => { SETTINGS.arena = value; }),
      setting("speed", "Cycle speed", [{ label: "Cruise", value: "cruise" }, { label: "Arcade", value: "arcade" }, { label: "Turbo", value: "turbo" }], SETTINGS.speed, (value) => { SETTINGS.speed = value; }),
      setting("difficulty", "CPU skill", [{ label: "Casual", value: "casual" }, { label: "Arcade", value: "arcade" }, { label: "Elite", value: "elite" }], SETTINGS.difficulty, (value) => { SETTINGS.difficulty = value; }),
      setting("rounds", "Match length", ["3", "5", "7"], SETTINGS.rounds, (value) => { SETTINGS.rounds = Number(value); }),
      ui.button("Enter the grid", startMatch),
      ui.text("P1: arrows (or WASD in solo) · P2: WASD · P3: IJKL · P4: TFGH · Space pauses · Swipe or use the touch pad", { tone: "muted" }),
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

  function ensureFrame() {
    if (frame) return;
    frame = ui.graphics.framebuffer("light-cycles-arena-v2", BUFFER_W, BUFFER_H, { format: "indexed" });
    for (let i = 0; i < PALETTE.length; i += 1) frame.palette.set([...PALETTE[i], 255], i * 4);
  }

  function bufferRect(left, top, right, bottom, color) {
    frame.fillRect(Math.floor(left), Math.floor(top), Math.max(1, Math.ceil(right - left)), Math.max(1, Math.ceil(bottom - top)), color);
  }

  function bufferLine(x1, y1, x2, y2, color, width) {
    const distance = Math.max(1, Math.ceil(Math.hypot(x2 - x1, y2 - y1)));
    for (let i = 0; i <= distance; i += 1) {
      const t = i / distance, x = x1 + (x2 - x1) * t, y = y1 + (y2 - y1) * t;
      bufferRect(x - width / 2, y - width / 2, x + width / 2, y + width / 2, color);
    }
  }

  function rebuildArena(force = false) {
    if (!round) return;
    ensureFrame();
    const sx = BUFFER_W / round.width, sy = BUFFER_H / round.height;
    const inset = safeInset(round.ticks);
    const full = force || inset !== paintedInset || paintedLengths.length !== round.riders.length;
    if (full) {
      frame.clear(0); paintedLengths = round.riders.map(() => 1); paintedInset = inset;
      for (let x = 0; x < BUFFER_W; x += Math.max(8, Math.round(sx * 4))) frame.fillRect(x, 0, 1, BUFFER_H, 2);
      for (let y = 0; y < BUFFER_H; y += Math.max(8, Math.round(sy * 4))) frame.fillRect(0, y, BUFFER_W, 1, 2);
    }
    round.riders.forEach((rider) => {
      const path = rider.path || [[rider.x, rider.y]];
      const start = full ? 1 : Math.max(1, paintedLengths[rider.id] || 1);
      for (let i = start; i < path.length; i += 1) {
        const a = path[i - 1], b = path[i];
        bufferLine((a[0] + .5) * sx, (a[1] + .5) * sy, (b[0] + .5) * sx, (b[1] + .5) * sy, 3 + rider.id, 3.4);
        bufferLine((a[0] + .5) * sx, (a[1] + .5) * sy, (b[0] + .5) * sx, (b[1] + .5) * sy, 8 + rider.id, 1.15);
      }
      paintedLengths[rider.id] = path.length;
    });
    // Collapse walls are repainted only when their cell boundary advances.
    if (full && inset) {
      bufferRect(0, 0, inset * sx, BUFFER_H, 12); bufferRect(BUFFER_W - inset * sx, 0, BUFFER_W, BUFFER_H, 12);
      bufferRect(inset * sx, 0, BUFFER_W - inset * sx, inset * sy, 12);
      bufferRect(inset * sx, BUFFER_H - inset * sy, BUFFER_W - inset * sx, BUFFER_H, 12);
    }
    frame.present();
  }

  const rotated = (cx, cy, forward, side, x, y) => [cx + forward[0] * x + side[0] * y, cy + forward[1] * x + side[1] * y];

  function bikeCommands(rider, alpha) {
    const [dx, dy] = DIRECTIONS[rider.direction], cellW = WIDTH / round.width, cellH = HEIGHT / round.height;
    const origin = renderOrigins[rider.id] || rider;
    const cx = (origin.x + (rider.x - origin.x) * alpha + .5) * cellW;
    const cy = (origin.y + (rider.y - origin.y) * alpha + .5) * cellH;
    const forward = [dx, dy], side = [-dy, dx], length = Math.max(10, Math.min(18, Math.min(cellW, cellH) * 1.75));
    const width = Math.max(5, Math.min(9, length * .48)), color = COLORS[rider.id];
    const body = [[.62, 0], [.18, -.5], [-.5, -.44], [-.64, 0], [-.5, .44], [.18, .5]]
      .map(([x, y]) => rotated(cx, cy, forward, side, x * length, y * width));
    const nose = [rotated(cx, cy, forward, side, length * .74, 0), rotated(cx, cy, forward, side, length * .34, -width * .3), rotated(cx, cy, forward, side, length * .34, width * .3)];
    const commands = [
      { type: "line", x1: (origin.x + .5) * cellW, y1: (origin.y + .5) * cellH, x2: cx, y2: cy, width: Math.max(3, width * .55), color: DIM_COLORS[rider.id] },
      { type: "line", x1: (origin.x + .5) * cellW, y1: (origin.y + .5) * cellH, x2: cx, y2: cy, width: 1.5, color },
      { type: "circle", x: cx, y: cy, radius: width * .9, color: `${color}28` },
      { type: "polygon", points: body, fill: "#061018", stroke: color, width: 2.5 },
      { type: "polygon", points: nose, fill: "#ffffff", stroke: color, width: 1 },
      { type: "circle", x: cx - forward[0] * length * .12, y: cy - forward[1] * length * .12, radius: width * .25, color }
    ];
    return commands;
  }

  function draw() {
    if (!round) return;
    const interpolating = mode === "running" || (mode === "paused" && pausedMode === "running");
    const alpha = interpolating ? Math.min(1, accumulator / PERIOD[SETTINGS.speed]) : 0;
    const commands = [ui.graphics.frame(frame, 0, 0, WIDTH, HEIGHT)];
    round.riders.forEach((rider) => { if (rider.alive) commands.push(...bikeCommands(rider, alpha)); });
    effects.forEach((effect) => {
      const progress = Math.min(1, effect.age / .55), radius = 8 + progress * 30;
      commands.push({ type: "circle", x: effect.x, y: effect.y, radius, color: `${effect.color}${Math.round((1 - progress) * 110).toString(16).padStart(2, "0")}` });
      commands.push({ type: "circle", x: effect.x, y: effect.y, radius: Math.max(0, radius - 5), color: "#010409" });
      for (let i = 0; i < 8; i += 1) {
        const angle = i * Math.PI / 4 + effect.seed;
        commands.push({ type: "line", x1: effect.x + Math.cos(angle) * 5, y1: effect.y + Math.sin(angle) * 5,
          x2: effect.x + Math.cos(angle) * radius * 1.5, y2: effect.y + Math.sin(angle) * radius * 1.5, width: 2, color: effect.color });
      }
    });
    commands.push(
      { type: "rect", x: 0, y: 0, width: WIDTH, height: 3, color: "#1dd6c1" },
      { type: "rect", x: 0, y: HEIGHT - 3, width: WIDTH, height: 3, color: "#1dd6c1" },
      { type: "rect", x: 0, y: 0, width: 3, height: HEIGHT, color: "#1dd6c1" },
      { type: "rect", x: WIDTH - 3, y: 0, width: 3, height: HEIGHT, color: "#1dd6c1" }
    );
    if (mode === "countdown") {
      const label = countdownMs > 650 ? String(Math.ceil((countdownMs - 650) / 1000)) : "GO";
      commands.push({ type: "rect", x: 0, y: 0, width: WIDTH, height: HEIGHT, color: "#00000088" });
      commands.push({ type: "text", x: WIDTH / 2, y: HEIGHT / 2, text: label, size: label === "GO" ? 44 : 64, align: "center", color: "#ffffff" });
    } else if (mode === "paused") {
      commands.push({ type: "rect", x: 0, y: 0, width: WIDTH, height: HEIGHT, color: "#00000088" });
      commands.push({ type: "text", x: WIDTH / 2, y: HEIGHT / 2, text: "PAUSED", size: 42, align: "center", color: "#55eaff" });
    }
    // The pointer action is installed once on the initial canvas. Omitting it
    // here avoids allocating a new action handler on every animation frame.
    ui.update("arena", ui.canvas(WIDTH, HEIGHT, commands, { id: "arena", label: "Light Cycles neon arena. Swipe to steer player one." }));
  }

  function updateHud() {
    if (!round) return;
    const riders = round.riders.map((rider) => `P${rider.id + 1} ${rider.id < SETTINGS.humans ? "LOCAL" : "CPU"} ${scores[rider.id]} ${rider.alive ? "●" : "×"}`).join("    ");
    ui.update("score", ui.text(riders, { id: "score", emphasis: true }));
    ui.update("round", ui.text(`ROUND ${roundNumber} / ${SETTINGS.rounds} · ${Math.round(1000 / PERIOD[SETTINGS.speed])} CELLS/SEC · ${SETTINGS.difficulty.toUpperCase()} CPU`, { id: "round", tone: "muted" }));
    const closing = mode === "running" && round.ticks >= 250;
    ui.update("status", ui.text(closing ? "WARNING // ARENA COLLAPSING" : notice, { id: "status", tone: closing ? "warning" : (mode === "roundover" || mode === "matchover" ? "accent" : "muted") }));
  }

  function updateAction() {
    const label = mode === "paused" ? "Resume" : mode === "roundover" ? "Next round" : mode === "matchover" ? "New match" : "Pause";
    ui.update("action", ui.button(label, () => {
      if (mode === "paused") resume();
      else if (mode === "roundover") nextRound();
      else if (mode === "matchover") setup();
      else if (mode === "running" || mode === "countdown") pause();
    }, { id: "action" }));
  }

  function steer(id, direction) {
    if (mode !== "running" || !round || !queueDirection(round, id, direction)) return;
    tone("turn");
  }

  function touchControls() {
    const controls = [ui.menu("P1 // SWIPE OR TAP", [
      { label: "▲", value: "3" }, { label: "◀", value: "2" }, { label: "▼", value: "1" }, { label: "▶", value: "0" }
    ], ({ value }) => steer(0, Number(value)), { id: "touch-p1", layout: "dpad" })];
    for (let id = 1; id < SETTINGS.humans; id += 1) {
      controls.push(ui.menu(`P${id + 1} // ${NAMES[id]} TURN`, [{ label: "↶ LEFT", value: "left" }, { label: "RIGHT ↷", value: "right" }], ({ value }) => {
        if (mode === "running" && turn(round, id, value)) tone("turn");
      }));
    }
    return controls;
  }

  function startMatch() {
    scores = [0, 0, 0, 0]; roundNumber = 0;
    ui.storage.set("light-cycles-settings", { ...SETTINGS }).catch(() => {});
    nextRound(true);
  }

  function nextRound(first = false) {
    roundNumber += 1; round = createRound(SETTINGS.riders, SETTINGS.arena);
    mode = "countdown"; accumulator = 0; countdownMs = 3650; countdownBeat = 4; effects = [];
    notice = "GET READY";
    paintedLengths = []; paintedInset = -1;
    renderOrigins = round.riders.map((rider) => ({ x: rider.x, y: rider.y }));
    rebuildArena(true);
    if (first) {
      ui.render(
        ui.title("Light Cycles // Neon Arena"),
        ui.text("", { id: "round", tone: "muted" }),
        ui.text("", { id: "score", emphasis: true }),
        ui.text("", { id: "status", tone: "accent" }),
        ui.canvas(WIDTH, HEIGHT, [], { id: "arena", label: "Light Cycles neon arena. Swipe to steer player one.", onPointer: pointer }),
        ui.button("Pause", () => pause(), { id: "action" }),
        ...touchControls(),
        ui.text("Arrow keys / WASD · swipe or tap the direction pad · Space pauses · reverse turns are blocked", { tone: "muted" }),
        ui.text("", { id: "stats", tone: "muted" })
      );
      updateStats();
    }
    updateHud(); updateAction(); draw();
  }

  function resume() {
    if (mode !== "paused") return;
    mode = pausedMode === "countdown" ? "countdown" : "running";
    notice = mode === "countdown" ? "GET READY" : "RUNNING"; updateHud(); updateAction(); draw();
  }

  function pause(message = "PAUSED") {
    if (mode !== "running" && mode !== "countdown") return;
    pausedMode = mode; mode = "paused"; notice = message; updateHud(); updateAction(); draw();
  }

  function finishRound() {
    if (mode !== "running") return;
    mode = roundNumber >= SETTINGS.rounds ? "matchover" : "roundover";
    stats.rounds += 1;
    round.riders.slice(0, SETTINGS.humans).forEach((rider) => {
      stats.bestRide = Math.max(stats.bestRide, rider.survived);
      if (!rider.alive) stats.crashes += 1;
    });
    if (round.winner !== null) {
      scores[round.winner] += 1;
      if (round.winner < SETTINGS.humans) stats.roundWins += 1;
      notice = `ROUND ${roundNumber} // P${round.winner + 1} WINS`; tone("win");
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
    persist(); updateStats(); updateHud(); updateAction(); draw();
  }

  function pointer(event) {
    if (event.phase === "start") pointerStart = [event.x, event.y];
    if (event.phase !== "end" || !pointerStart) return;
    const dx = event.x - pointerStart[0], dy = event.y - pointerStart[1]; pointerStart = null;
    if (Math.max(Math.abs(dx), Math.abs(dy)) < 16) return;
    steer(0, Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 0 : 2) : (dy > 0 ? 1 : 3));
  }

  function addCrashEffects(ids) {
    const cellW = WIDTH / round.width, cellH = HEIGHT / round.height;
    ids.forEach((id) => {
      const rider = round.riders[id];
      const [dx, dy] = DIRECTIONS[rider.queued];
      effects.push({ x: Math.max(4, Math.min(WIDTH - 4, (rider.x + .5 + dx) * cellW)),
        y: Math.max(4, Math.min(HEIGHT - 4, (rider.y + .5 + dy) * cellH)), color: COLORS[id], age: 0, seed: Math.random() * Math.PI });
    });
  }

  ui.onKey(({ code, phase, repeat }) => {
    if (phase !== "down" || repeat) return;
    if (code === "Space") { if (mode === "running" || mode === "countdown") pause(); else if (mode === "paused") resume(); return; }
    if (code === "Enter") { if (mode === "paused") resume(); else if (mode === "roundover") nextRound(); return; }
    if (mode !== "running") return;
    if (SETTINGS.humans === 1 && KEYS[1][code] !== undefined) { steer(0, KEYS[1][code]); return; }
    for (let id = 0; id < SETTINGS.humans; id += 1) if (KEYS[id][code] !== undefined) { steer(id, KEYS[id][code]); return; }
  });

  ui.onVisibility(({ value }) => { if (value === "hidden") pause("PAUSED // WINDOW INACTIVE"); });

  ui.animate(({ delta }) => {
    const dt = Math.min(.05, delta);
    effects.forEach((effect) => { effect.age += dt; }); effects = effects.filter((effect) => effect.age < .55);
    if (mode === "countdown") {
      countdownMs -= dt * 1000;
      const beat = Math.max(0, Math.ceil((countdownMs - 650) / 1000));
      if (beat < countdownBeat) { countdownBeat = beat; tone("count", beat); }
      if (countdownMs <= 0) { mode = "running"; countdownMs = 0; accumulator = PERIOD[SETTINGS.speed]; notice = "RUNNING"; updateHud(); updateAction(); }
      draw(); return;
    }
    if (mode !== "running") { if (effects.length) draw(); return; }
    accumulator += dt * 1000; hudClock += dt * 1000;
    const period = PERIOD[SETTINGS.speed];
    let steps = 0;
    while (accumulator >= period && steps < 5 && mode === "running") {
      accumulator -= period; steps += 1;
      for (let id = SETTINGS.humans; id < SETTINGS.riders; id += 1) {
        const direction = chooseComputerDirection(round, id, Math.random, SETTINGS.difficulty);
        if (direction !== null) queueDirection(round, id, direction);
      }
      const before = round.riders.map((rider) => ({ x: rider.x, y: rider.y }));
      const result = step(round); renderOrigins = before; rebuildArena();
      if (result.eliminated.length) { addCrashEffects(result.eliminated); tone("crash"); updateHud(); }
      if (result.over) finishRound();
    }
    if (hudClock >= 500) { hudClock = 0; updateHud(); }
    draw();
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
    if (["casual", "arcade", "elite"].includes(storedSettings.difficulty)) SETTINGS.difficulty = storedSettings.difficulty;
    if ([3, 5, 7].includes(storedSettings.rounds)) SETTINGS.rounds = storedSettings.rounds;
  }
  setup();
});
