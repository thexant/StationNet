"use strict";

StationNet.start(async (ui) => {
  const { createGame, WIDTH, HEIGHT, PADDLE_Y, PADDLE_W, PADDLE_H, BALL_R } = await ui.modules.load("engine.js");
  ui.random.seed(Date.now());
  const game = createGame(() => ui.random.next());
  const state = game.state;
  const SETTINGS_KEY = "breakout.settings.v1";
  const PENDING_KEY = "breakout.pending-score.v1";
  let best = 0, soundOn = true, pendingScore = null, scoreBusy = false, scoreError = false, scoreVersion = 0;
  let pendingSave = Promise.resolve();
  let leftHeld = false, rightHeld = false, pointerTarget = null, modalOpen = false, hidden = false;
  let gamepadA = false, gamepadStart = false, flash = 0, dirty = true;
  const keys = new Set(), sparks = [], trail = [];
  const grid = [];
  for (let x = 0; x <= WIDTH; x += 40) grid.push([x, 0, x, HEIGHT]);
  for (let y = 0; y <= HEIGHT; y += 40) grid.push([0, y, WIDTH, y]);

  const saved = await Promise.allSettled([ui.storage.get(SETTINGS_KEY), ui.storage.get(PENDING_KEY)]);
  if (saved[0].status === "fulfilled" && saved[0].value?.sound === false) soundOn = false;
  if (saved[1].status === "fulfilled" && Number.isInteger(saved[1].value) && saved[1].value >= 0 && saved[1].value <= 1e9)
    pendingScore = saved[1].value;

  function cue(frequency, duration, waveform = "triangle", volume = 0.045) {
    if (!soundOn) return;
    try { ui.sound.playTone(frequency, duration, { waveform, volume }); } catch (_) { /* Audio is optional. */ }
  }

  function gameMessage() {
    if (state.mode === "running") return "RUNNING";
    if (state.mode === "paused") return "PAUSED — PRESS RESUME OR SPACE";
    if (state.mode === "gameover") return `GAME OVER — FINAL SCORE ${state.score}`;
    if (state.readyReason === "life") return `LIFE LOST — ${state.lives} ${state.lives === 1 ? "LIFE" : "LIVES"} LEFT — LAUNCH WHEN READY`;
    if (state.readyReason === "level") return `LEVEL ${state.level} READY — LAUNCH WHEN READY`;
    return "READY — PRESS LAUNCH OR SPACE";
  }

  function updateHud() {
    ui.update("hud", ui.text(`SCORE ${state.score}    LIVES ${state.lives}    LEVEL ${state.level}    PERSONAL BEST ${best}`,
      { id: "hud", emphasis: true }));
  }

  function updateActions() {
    const items = [
      { label: state.mode === "paused" ? "Resume" : "Pause", value: "pause", disabled: state.mode !== "running" && state.mode !== "paused" },
      { label: "New game", value: "new" },
      { label: soundOn ? "Sound on" : "Sound off", value: "sound" },
      { label: ui.presentation.mode === "immersive" ? "Exit focus" : "Focus view", value: "focus" },
      { label: "How to play", value: "help" }
    ];
    if (scoreError && pendingScore !== null) items.push({ label: "Retry score", value: "retry" });
    ui.update("actions", ui.menu("Game", items, ({ value }) => action(value), { id: "actions" }));
  }

  function updateScoreStatus(message, tone = "muted") {
    ui.update("score-status", ui.text(message, { id: "score-status", tone }));
    updateActions();
  }

  function applyScores(result) {
    if (!result) return;
    best = Math.max(best, Number(result.personal_best) || 0);
    updateHud();
    const rows = (result.leaderboard || []).map((entry, index) => [index + 1, entry.username, entry.score]);
    ui.update("leaderboard", ui.table(["#", "Player", "Score"], rows.length ? rows : [["—", "No scores yet", "—"]],
      { id: "leaderboard" }));
  }

  async function refreshScores() {
    const version = ++scoreVersion;
    updateScoreStatus("Loading station scores…");
    try {
      const result = await ui.scores.leaderboard();
      if (version !== scoreVersion) return;
      applyScores(result);
      updateScoreStatus(scoreError && pendingScore !== null ? "Station scores loaded; your unsaved score is still pending." :
        "Station scores are up to date.", scoreError && pendingScore !== null ? "warning" : "muted");
    } catch (_) {
      if (version === scoreVersion) updateScoreStatus("Station scores unavailable. Refresh to try again.", "warning");
    }
  }

  function persistPending() {
    const value = pendingScore;
    pendingSave = pendingSave.then(() => value !== null ? ui.storage.set(PENDING_KEY, value) : ui.storage.delete(PENDING_KEY))
      .catch(() => { /* Retry remains available for this session. */ });
    return pendingSave;
  }

  async function flushPending() {
    if (scoreBusy || pendingScore === null) return;
    scoreBusy = true; scoreError = false;
    while (pendingScore !== null) {
      const submitted = pendingScore;
      ++scoreVersion;
      updateScoreStatus("Submitting your score…");
      try {
        const result = await ui.scores.submit(submitted);
        ++scoreVersion; // The POST result supersedes any GET started while it was in flight.
        applyScores(result);
        if (pendingScore <= submitted) pendingScore = null;
        await persistPending();
        if (pendingScore === null) updateScoreStatus("Score saved to the station leaderboard.");
      } catch (_) {
        scoreError = true;
        updateScoreStatus("Score could not be saved. Use Retry score when the station is available.", "warning");
        break;
      }
    }
    scoreBusy = false;
  }

  async function queueScore(score) {
    pendingScore = Math.max(pendingScore ?? 0, score);
    await persistPending();
    void flushPending();
  }

  function addSparks(event) {
    for (let index = 0; index < 6; index += 1) {
      const angle = index * Math.PI / 3;
      sparks.push({ x: event.x, y: event.y, vx: Math.cos(angle) * 95, vy: Math.sin(angle) * 95,
        life: 0.23, color: event.color });
    }
    if (sparks.length > 24) sparks.splice(0, sparks.length - 24);
  }

  function updateEffects(dt) {
    flash = Math.max(0, flash - dt);
    for (let i = sparks.length - 1; i >= 0; i -= 1) {
      const item = sparks[i];
      item.x += item.vx * dt; item.y += item.vy * dt; item.life -= dt;
      if (item.life <= 0) sparks.splice(i, 1);
    }
    if (flash || sparks.length) dirty = true;
  }

  function draw(changeControls = false) {
    const commands = [
      { type: "rect", x: 0, y: 0, width: WIDTH, height: HEIGHT, color: "#010901" },
      { type: "lines", segments: grid, color: "#092315", width: 1 },
      { type: "line", x1: 0, y1: PADDLE_Y + PADDLE_H + 8, x2: WIDTH, y2: PADDLE_Y + PADDLE_H + 8, color: "#14613e", width: 1 }
    ];
    state.bricks.forEach((brick) => {
      commands.push({ type: "rect", ...brick });
      commands.push({ type: "rect", x: brick.x + 3, y: brick.y + 3, width: brick.width - 6, height: 2, color: "#ffffff88" });
    });
    trail.forEach((point, index) => commands.push({ type: "circle", x: point.x, y: point.y,
      radius: Math.max(1.5, BALL_R * (index + 1) / (trail.length + 2)), color: "#55ffff55" }));
    sparks.forEach((item) => commands.push({ type: "circle", x: item.x, y: item.y, radius: 2.5, color: item.color }));
    commands.push({ type: "rect", x: state.paddleX, y: PADDLE_Y, width: PADDLE_W, height: PADDLE_H, color: "#55ffff" });
    commands.push({ type: "rect", x: state.paddleX + 5, y: PADDLE_Y + 2, width: PADDLE_W - 10, height: 2, color: "#ffffff" });
    commands.push({ type: "circle", x: state.ball.x, y: state.ball.y, radius: BALL_R, color: "#ffffff" });
    if (flash > 0) commands.push({ type: "rect", x: 0, y: 0, width: WIDTH, height: HEIGHT, color: "#ffffff18" });
    if (state.mode !== "running") {
      const heading = state.mode === "paused" ? "PAUSED" : state.mode === "gameover" ? "GAME OVER" :
        state.readyReason === "level" ? `LEVEL ${state.level}` : state.readyReason === "life" ? "LIFE LOST" : "BREAKOUT";
      const detail = state.mode === "gameover" ? `FINAL SCORE ${state.score}` :
        state.mode === "paused" ? "TAKE YOUR TIME" : state.readyReason === "level" ? "CLEAR THE NEXT WALL" :
        state.readyReason === "life" ? `${state.lives} ${state.lives === 1 ? "LIFE" : "LIVES"} REMAINING` : "CLEAR THE BRICKS";
      commands.push({ type: "rect", x: 130, y: 257, width: 380, height: 88, color: "#010c07dd" });
      commands.push({ type: "rect", x: 130, y: 257, width: 380, height: 3, color: "#55ffff" });
      commands.push({ type: "text", x: WIDTH / 2, y: 283, text: heading, size: 30, align: "center", color: "#ffffff" });
      commands.push({ type: "text", x: WIDTH / 2, y: 321, text: detail, size: 20, align: "center", color: "#55ffff" });
    }
    const options = { id: "playfield", label: "Breakout playfield", hover: true };
    if (changeControls) options.controls = state.mode === "running" ? [] : [{
      label: state.mode === "paused" ? "RESUME" : state.mode === "gameover" ? "PLAY AGAIN" :
        state.readyReason === "start" ? "LAUNCH" : "CONTINUE",
      onClick: primaryAction
    }];
    ui.update("playfield", ui.canvas(WIDTH, HEIGHT, commands, options));
    dirty = false;
  }

  function syncState() {
    updateHud();
    ui.update("game-status", ui.text(gameMessage(), { id: "game-status", tone: state.mode === "gameover" ? "warning" : "accent" }));
    updateActions();
    dirty = true;
    draw(true);
  }

  function primaryAction() {
    if (modalOpen || hidden) return;
    if (state.mode === "gameover") { game.reset(); trail.length = 0; game.launch(); }
    else if (state.mode === "ready") game.launch();
    else if (state.mode === "paused") game.resume();
    syncState();
  }

  function togglePause() {
    if (modalOpen || hidden) return;
    if (state.mode === "running") game.pause();
    else if (state.mode === "paused") game.resume();
    else return;
    syncState();
  }

  async function modal(title, content, buttons) {
    if (modalOpen) return null;
    const wasRunning = state.mode === "running";
    if (wasRunning) { game.pause(); syncState(); }
    modalOpen = true;
    let choice = null;
    try { choice = await ui.dialog(title, [ui.text(content)], buttons); }
    finally {
      modalOpen = false;
      if (wasRunning && choice !== "new" && !hidden) { game.resume(); syncState(); }
    }
    return choice;
  }

  async function newGame() {
    if (state.mode === "gameover" || state.mode === "ready" && state.readyReason === "start") {
      game.reset(); trail.length = 0; sparks.length = 0; syncState(); return;
    }
    const choice = await modal("Start a new game?", "Your current run will be discarded.",
      [{ label: "New game", value: "new" }, { label: "Keep playing", value: "cancel" }]);
    if (choice === "new") { game.reset(); trail.length = 0; sparks.length = 0; syncState(); }
  }

  async function action(value) {
    if (value === "pause") togglePause();
    else if (value === "new") await newGame();
    else if (value === "sound") {
      soundOn = !soundOn; updateActions();
      try { await ui.storage.set(SETTINGS_KEY, { sound: soundOn }); } catch (_) { /* Session setting still works. */ }
      if (soundOn) cue(660, 50);
    }
    else if (value === "focus") {
      if (ui.presentation.mode === "immersive") ui.presentation.exitImmersive();
      else ui.presentation.enterImmersive();
      updateActions();
    }
    else if (value === "help") await modal("How to play",
      "Move with ←/→, A/D, mouse, touch drag, the touch buttons, or a gamepad. Space or Enter launches and resumes; P pauses. On gamepad, A launches and Start pauses. Clear every brick to advance. You have three lives.",
      [{ label: "Close", value: "close" }]);
    else if (value === "retry") void flushPending();
  }

  function pointer(event) {
    if (event.phase !== "start" && event.phase !== "move") return;
    pointerTarget = Math.max(0, Math.min(WIDTH - PADDLE_W, event.x - PADDLE_W / 2));
    dirty = true;
  }

  function touchDirection({ value, phase }) {
    if (value === "left") leftHeld = phase === "start";
    if (value === "right") rightHeld = phase === "start";
    if (phase === "start") pointerTarget = null;
  }

  function key({ code, key: label, phase, repeat }) {
    if (["ArrowLeft", "ArrowRight", "KeyA", "KeyD"].includes(code)) {
      if (phase === "down") { keys.add(code); pointerTarget = null; }
      else keys.delete(code);
      return;
    }
    if (phase !== "down" || repeat || modalOpen) return;
    if (code === "Space" || code === "Enter" || label === " ") {
      if (state.mode !== "running") primaryAction();
    } else if (code === "KeyP" || label === "p" || label === "P") togglePause();
  }

  function gamepad() {
    const pad = ui.input.gamepad(0, { deadzone: 0.18 });
    const a = (pad.buttons[0] || 0) > 0.5, start = (pad.buttons[9] || 0) > 0.5;
    if (a && !gamepadA && state.mode !== "running") primaryAction();
    if (start && !gamepadStart) togglePause();
    gamepadA = a; gamepadStart = start;
    const horizontal = pad.axes[0] || ((pad.buttons[15] || 0) > 0.5 ? 1 : 0) - ((pad.buttons[14] || 0) > 0.5 ? 1 : 0);
    if (Math.abs(horizontal) > 0.18) pointerTarget = null;
    return horizontal;
  }

  function handleEvents(events) {
    let changed = false;
    for (const event of events) {
      if (event.type === "brick") { cue(550 + state.score % 240, 35, "square", 0.03); addSparks(event); flash = 0.035; changed = true; }
      else if (event.type === "paddle") cue(250, 45);
      else if (event.type === "life") { cue(180, 180, "sawtooth", 0.04); trail.length = 0; changed = true; }
      else if (event.type === "level") { cue(880, 150); trail.length = 0; changed = true; }
      else if (event.type === "gameover") { cue(120, 260, "sawtooth", 0.04); trail.length = 0; changed = true; void queueScore(state.score); }
    }
    if (changed) syncState();
  }

  ui.render(
    ui.text("Move: ←/→, A/D, or drag. Space: launch or resume. P: pause.", { tone: "muted" }),
    ui.text("", { id: "hud", emphasis: true }),
    ui.text("", { id: "game-status", tone: "accent" }),
    ui.canvas(WIDTH, HEIGHT, [], { id: "playfield", label: "Breakout playfield", onPointer: pointer, hover: true }),
    ui.menu("Game", [], ({ value }) => action(value), { id: "actions" }),
    ui.menu("Touch paddle", [{ label: "◀ HOLD LEFT", value: "left" }, { label: "HOLD RIGHT ▶", value: "right" }], null,
      { id: "game-controls", onPress: touchDirection, onRelease: touchDirection }),
    ui.panel("Station leaderboard", [
      ui.text("Loading station scores…", { id: "score-status", tone: "muted" }),
      ui.table(["#", "Player", "Score"], [["—", "Loading…", "—"]], { id: "leaderboard" }),
      ui.button("Refresh scores", () => { void refreshScores(); }, { id: "refresh-scores" })
    ])
  );
  ui.onKey(key);
  ui.onVisibility(({ value }) => {
    hidden = value === "hidden";
    if (hidden) {
      keys.clear(); leftHeld = false; rightHeld = false; pointerTarget = null;
      gamepadA = false; gamepadStart = false;
      if (game.pause()) syncState();
    }
  });
  ui.presentation.onChange(() => updateActions());
  ui.fixedStep(60, ({ delta }) => {
    const pad = gamepad();
    const left = keys.has("ArrowLeft") || keys.has("KeyA") || leftHeld;
    const right = keys.has("ArrowRight") || keys.has("KeyD") || rightHeld;
    const direction = Math.max(-1, Math.min(1, (right ? 1 : 0) - (left ? 1 : 0) + pad));
    const beforeX = state.paddleX;
    const events = game.tick(delta, direction, pointerTarget);
    if (state.mode === "running") {
      trail.push({ x: state.ball.x, y: state.ball.y });
      if (trail.length > 7) trail.shift();
      dirty = true;
    }
    if (beforeX !== state.paddleX) dirty = true;
    updateEffects(delta);
    if (events.length) handleEvents(events);
  });
  ui.animate(() => { if (dirty) draw(); });
  syncState();
  void refreshScores();
  if (pendingScore !== null) void flushPending();
});
