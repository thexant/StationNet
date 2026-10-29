"use strict";

StationNet.start(async (ui) => {
  const { COLS, ROWS, MODES, SPRINT_TARGET, createGame, speedFor } = await ui.modules.load("engine.js");
  const SETTINGS = "snake.settings.v1", RECORDS = "snake.records.v1", PENDING = "snake.pending-classic-score.v1";
  const DIRS = { ArrowUp: [0, -1], KeyW: [0, -1], ArrowDown: [0, 1], KeyS: [0, 1],
    ArrowLeft: [-1, 0], KeyA: [-1, 0], ArrowRight: [1, 0], KeyD: [1, 0] };
  ui.random.seed(Date.now());
  const saved = await Promise.allSettled([ui.storage.get(SETTINGS), ui.storage.get(RECORDS), ui.storage.get(PENDING)]);
  let soundOn = saved[0].status !== "fulfilled" || saved[0].value?.sound !== false;
  const oldRecords = saved[1].status === "fulfilled" ? saved[1].value : null;
  const records = { sprintMs: Number.isFinite(oldRecords?.sprintMs) && oldRecords.sprintMs > 0 ? oldRecords.sprintMs : null,
    mazeScore: Number.isInteger(oldRecords?.mazeScore) && oldRecords.mazeScore >= 0 ? oldRecords.mazeScore : 0 };
  let pendingScore = saved[2].status === "fulfilled" && Number.isInteger(saved[2].value) && saved[2].value >= 0 ? saved[2].value : null;
  let game = createGame("classic", () => ui.random.next());
  let best = 0, scoreBusy = false, scoreError = false, scoreVersion = 0, pendingSave = Promise.resolve();
  let flashMs = 0, flashColor = "#55ff5522", pauseReason = "", lastTimeShown = -1, changing = false, movementMs = 0;
  const pointers = new Map();

  const board = ui.graphics.framebuffer("snake-board", 300, 200, { format: "indexed" });
  [[1, 7, 4], [5, 20, 11], [7, 25, 14], [8, 91, 43], [18, 145, 68],
    [113, 255, 146], [244, 255, 244], [157, 81, 13], [255, 188, 58],
    [21, 75, 83], [54, 147, 154]].forEach(([r, g, b], i) => board.palette.set([r, g, b, 255], i * 4));
  const frame = ui.graphics.frame(board, 0, 0, COLS * 20, ROWS * 20);
  const formatTime = (ms) => {
    const tenths = Math.floor(ms / 100);
    return `${String(Math.floor(tenths / 600)).padStart(2, "0")}:${String(Math.floor(tenths / 10) % 60).padStart(2, "0")}.${tenths % 10}`;
  };

  function cue(kind) {
    if (!soundOn) return;
    try {
      if (kind === "eat") ui.sound.playTone(660, 65, { waveform: "triangle", volume: 0.045 });
      else if (kind === "won") ui.sound.sequence([660, 880, 1100], { duration: 95, waveform: "triangle", volume: 0.05 });
      else if (kind === "dead") ui.sound.sequence([220, 150], { duration: 110, waveform: "square", volume: 0.035 });
    } catch (_) { /* Audio is optional. */ }
  }
  function notice(message, tone = "muted") { ui.update("notice", ui.text(message, { id: "notice", tone })); }

  function paintBoard() {
    board.clear(0);
    for (let y = 0; y < ROWS; y += 1) for (let x = 0; x < COLS; x += 1)
      board.fillRect(x * 10 + 1, y * 10 + 1, 8, 8, (x + y) % 2 ? 1 : 2);
    for (const part of game.state.obstacles) {
      const x = part.x * 10, y = part.y * 10;
      board.fillRect(x + 1, y + 1, 8, 8, 9); board.fillRect(x + 3, y + 3, 4, 4, 10);
    }
    game.state.snake.forEach((part, i) => {
      board.fillRect(part.x * 10 + 1, part.y * 10 + 1, 8, 8, i ? (i % 2 ? 3 : 4) : 5);
    });
    const head = game.state.snake[0], x = head.x * 10, y = head.y * 10;
    const [dx, dy] = game.state.direction;
    if (dx) {
      const eyeX = x + (dx > 0 ? 7 : 2);
      board.fillRect(eyeX, y + 3, 1, 1, 6); board.fillRect(eyeX, y + 6, 1, 1, 6);
    } else {
      const eyeY = y + (dy > 0 ? 7 : 2);
      board.fillRect(x + 3, eyeY, 1, 1, 6); board.fillRect(x + 6, eyeY, 1, 1, 6);
    }
    if (game.state.food) {
      const fx = game.state.food.x * 10, fy = game.state.food.y * 10;
      board.fillRect(fx + 3, fy + 1, 4, 8, 7); board.fillRect(fx + 1, fy + 3, 8, 4, 7);
      board.fillRect(fx + 3, fy + 3, 4, 4, 8);
    }
    board.present();
  }

  function drawCanvas() {
    const commands = [frame];
    if (flashMs > 0) commands.push({ type: "rect", x: 0, y: 0, width: 600, height: 400, color: flashColor });
    if (game.state.status !== "running") {
      const state = game.state;
      const heading = state.status === "ready" ? "SNAKE" : state.status === "paused" ? "PAUSED" :
        state.status === "won" ? "RUN COMPLETE" : "GAME OVER";
      const detail = state.status === "ready" ? "START OR CHOOSE A DIRECTION" :
        state.status === "paused" ? "PRESS RESUME TO CONTINUE" :
        state.mode === "sprint" ? `${state.foods}/${SPRINT_TARGET} FOOD  •  ${formatTime(state.elapsedMs)}` : `FINAL SCORE ${state.score}`;
      commands.push(
        { type: "rect", x: 115, y: 145, width: 370, height: 110, color: "#010b08ee" },
        { type: "rect", x: 115, y: 145, width: 370, height: 3, color: state.status === "dead" ? "#ff9955" : "#55ff99" },
        { type: "text", x: 300, y: 183, text: heading, size: 30, align: "center", color: "#eaffef" },
        { type: "text", x: 300, y: 224, text: detail, size: 16, align: "center", color: "#a9edca" }
      );
    }
    ui.update("playfield", ui.canvas(600, 400, commands, { id: "playfield", label: "Snake playfield" }));
  }

  function updateHud() {
    const state = game.state;
    let label = `${MODES[state.mode].name.toUpperCase()}  •  SCORE ${state.score}`;
    if (state.mode === "classic") label += `  •  PERSONAL BEST ${best}`;
    if (state.mode === "sprint") label += `  •  FOOD ${state.foods}/${SPRINT_TARGET}  •  TIME ${formatTime(state.elapsedMs)}  •  BEST ${records.sprintMs === null ? "—" : formatTime(records.sprintMs)}`;
    if (state.mode === "maze") label += `  •  PERSONAL BEST ${records.mazeScore}`;
    ui.update("hud", ui.text(label, { id: "hud", emphasis: true }));
  }
  function updateStatus() {
    const state = game.state;
    const label = state.status === "ready" ? "READY — START OR PRESS A DIRECTION" :
      state.status === "running" ? "RUNNING" : state.status === "paused" ? pauseReason || "PAUSED" :
      state.status === "won" ? (state.mode === "sprint" ? `SPRINT COMPLETE — ${formatTime(state.elapsedMs)}` : `BOARD CLEARED — ${state.score} POINTS`) :
      `GAME OVER — ${state.score} POINTS`;
    ui.update("game-status", ui.statusBar(label, { id: "game-status" }));
  }
  function updateActions() {
    const status = game.state.status;
    ui.update("primary", ui.button(status === "ready" ? "Start" : status === "paused" ? "Resume" :
      status === "running" ? "Playing" : "Play again", primaryAction, { id: "primary", disabled: status === "running" }));
    const items = [
      { label: status === "paused" ? "Resume" : "Pause", value: "pause", disabled: status !== "running" && status !== "paused" },
      { label: "New run", value: "new" }, { label: soundOn ? "Sound on" : "Sound off", value: "sound" },
      { label: ui.presentation.mode === "immersive" ? "Exit focus view" : "Focus view", value: "focus" },
      { label: "How to play", value: "help" }, { label: "Refresh scores", value: "refresh" }
    ];
    if (pendingScore !== null && scoreError) items.push({ label: "Retry score", value: "retry" });
    ui.update("game-actions", ui.menu("Game", items, ({ value }) => action(value), { id: "game-actions" }));
  }
  function refreshUI(repaint = false) {
    if (repaint) paintBoard();
    updateHud(); updateStatus(); updateActions();
    ui.update("mode-details", ui.text(`${MODES[game.state.mode].name}: ${MODES[game.state.mode].description}`, { id: "mode-details", tone: "muted" }));
    drawCanvas();
  }
  function makeGame(mode = game.state.mode, startNow = false) {
    pointers.clear(); flashMs = 0; pauseReason = ""; lastTimeShown = -1; movementMs = 0;
    game = createGame(mode, () => ui.random.next());
    if (startNow) game.start();
    refreshUI(true);
  }
  function primaryAction() {
    if (game.state.status === "dead" || game.state.status === "won") return makeGame(game.state.mode, true);
    if (game.start()) { pauseReason = ""; movementMs = 0; refreshUI(); }
  }
  function togglePause() {
    if (game.state.status === "running") { game.pause(); pauseReason = "PAUSED"; movementMs = 0; refreshUI(); }
    else if (game.state.status === "paused") primaryAction();
  }
  async function confirmAbandon(message) {
    if (game.state.status !== "running" && game.state.status !== "paused") return true;
    const wasRunning = game.state.status === "running";
    if (wasRunning) { game.pause(); pauseReason = "PAUSED — CONFIRM NEW RUN"; refreshUI(); }
    const answer = await ui.dialog("Leave this run?", [ui.text(message)],
      [{ label: "Leave run", value: "leave" }, { label: "Keep playing", value: "keep" }]);
    if (answer === "leave") return true;
    if (wasRunning) primaryAction();
    return false;
  }
  async function newRun(mode = game.state.mode) {
    if (changing) return;
    changing = true;
    try { if (await confirmAbandon(mode === game.state.mode ? "Start a new run?" : `Switch to ${MODES[mode].name}?`)) makeGame(mode); }
    finally { changing = false; }
  }
  function steer(direction) {
    if (!game.turn(direction)) return;
    if (game.state.status === "ready") primaryAction();
  }
  function pointer(event) {
    const id = event.pointerId;
    if (event.phase === "start") { pointers.set(id, { x: event.x, y: event.y, directed: false }); return; }
    if (event.phase === "end" || event.phase === "cancel") { pointers.delete(id); return; }
    if (event.phase !== "move") return;
    const start = pointers.get(id);
    if (!start || start.directed) return;
    const dx = event.x - start.x, dy = event.y - start.y;
    if (Math.max(Math.abs(dx), Math.abs(dy)) < 14) return;
    start.directed = true;
    steer(Math.abs(dx) > Math.abs(dy) ? [Math.sign(dx), 0] : [0, Math.sign(dy)]);
  }

  function showScores(result) {
    if (!result) return;
    best = Math.max(best, Number(result.personal_best) || 0);
    updateHud();
    const rows = (result.leaderboard || []).map((entry, i) => [i + 1, entry.username, entry.score]);
    ui.update("leaderboard", ui.table(["#", "Player", "Score"], rows.length ? rows : [["—", "No scores yet", "—"]], { id: "leaderboard" }));
  }
  function scoreStatus(message, tone = "muted") {
    ui.update("score-status", ui.text(message, { id: "score-status", tone })); updateActions();
  }
  async function refreshScores() {
    const version = ++scoreVersion;
    scoreStatus("Loading station scores…");
    try {
      const result = await ui.scores.leaderboard();
      if (version !== scoreVersion) return;
      showScores(result);
      scoreStatus(scoreError && pendingScore !== null ? "Scores loaded; your pending Classic score still needs a retry." : "Station scores are up to date.",
        scoreError && pendingScore !== null ? "warning" : "muted");
    } catch (_) {
      if (version === scoreVersion) scoreStatus("Station scores unavailable. Use Refresh scores to try again.", "warning");
    }
  }
  function persistPending() {
    const value = pendingScore;
    pendingSave = pendingSave.then(() => value === null ? ui.storage.delete(PENDING) : ui.storage.set(PENDING, value))
      .catch(() => notice("Score retry could not be saved between visits.", "warning"));
    return pendingSave;
  }
  async function flushPending() {
    if (scoreBusy || pendingScore === null) return;
    scoreBusy = true; scoreError = false;
    while (pendingScore !== null) {
      const submitted = pendingScore;
      ++scoreVersion; scoreStatus("Submitting Classic score…");
      try {
        const result = await ui.scores.submit(submitted);
        ++scoreVersion; showScores(result);
        if (pendingScore <= submitted) pendingScore = null;
        await persistPending();
        if (pendingScore === null) scoreStatus("Classic score saved to the station leaderboard.");
      } catch (_) {
        scoreError = true;
        scoreStatus("Classic score is pending. Use Retry score when the station is available.", "warning");
        break;
      }
    }
    scoreBusy = false; updateActions();
  }
  async function queueScore(score) {
    pendingScore = Math.max(pendingScore ?? 0, score);
    await persistPending(); void flushPending();
  }
  async function saveRecords() {
    try { await ui.storage.set(RECORDS, records); }
    catch (_) { notice("Personal record could not be saved between visits.", "warning"); }
  }
  function finish(event) {
    if (game.state.mode === "classic") { best = Math.max(best, game.state.score); void queueScore(game.state.score); }
    else if (game.state.mode === "sprint" && event.type === "won" &&
             (records.sprintMs === null || game.state.elapsedMs < records.sprintMs)) {
      records.sprintMs = game.state.elapsedMs; void saveRecords();
    } else if (game.state.mode === "maze" && game.state.score > records.mazeScore) {
      records.mazeScore = game.state.score; void saveRecords();
    }
    cue(event.type);
  }
  async function action(value) {
    if (value === "pause") togglePause();
    else if (value === "new") await newRun();
    else if (value === "sound") {
      soundOn = !soundOn;
      try { await ui.storage.set(SETTINGS, { sound: soundOn }); }
      catch (_) { notice("Sound preference could not be saved between visits.", "warning"); }
      updateActions();
    } else if (value === "focus") {
      if (ui.presentation.mode === "immersive") ui.presentation.exitImmersive();
      else ui.presentation.enterImmersive();
      updateActions();
    } else if (value === "help") {
      if (game.state.status === "running") { game.pause(); pauseReason = "PAUSED — HOW TO PLAY"; movementMs = 0; refreshUI(); }
      await ui.dialog("How to play", [
        ui.text("Steer with arrows, WASD, swipes, or the touch D-pad. Collect food and avoid your body, walls, and Maze obstacles."),
        ui.text("Enter or Space: start/resume. P: pause. R: new run. Touch Start resumes; Select opens this help."),
        ui.text("Classic uses the station leaderboard. Sprint and Maze keep private personal records.")
      ], [{ label: "Close", value: "close" }]);
    } else if (value === "refresh") await refreshScores();
    else if (value === "retry") void flushPending();
  }

  ui.render(
    ui.title("Snake"),
    ui.text("Steer with arrows, WASD, swipes, or the touch D-pad. Enter/Space starts; P pauses. Choose a mode and collect food.", { tone: "muted" }),
    ui.menu("Mode", Object.keys(MODES).map((value) => ({ label: MODES[value].name, value })),
      ({ value }) => { if (MODES[value] && value !== game.state.mode) void newRun(value); }, { id: "mode" }),
    ui.text("", { id: "mode-details", tone: "muted" }),
    ui.text("", { id: "hud", emphasis: true }),
    ui.statusBar("READY", { id: "game-status" }),
    ui.button("Start", primaryAction, { id: "primary" }),
    ui.canvas(600, 400, [], { id: "playfield", label: "Snake playfield: swipe to steer", onPointer: pointer,
      touchLayout: "gamepad", controls: [{ label: "Pause", code: "KeyP" }, { label: "New", code: "KeyR" }] }),
    ui.menu("Game", [], ({ value }) => action(value), { id: "game-actions" }),
    ui.text("", { id: "notice", tone: "muted" }),
    ui.heading("Classic station leaderboard", 3),
    ui.text("Earlier scores used the original, faster Classic speed curve.", { tone: "muted" }),
    ui.text("Loading station scores…", { id: "score-status", tone: "muted" }),
    ui.table(["#", "Player", "Score"], [["—", "Loading…", "—"]], { id: "leaderboard" })
  );

  ui.onKey(({ code, phase, repeat }) => {
    if (phase !== "down" || repeat) return;
    if (DIRS[code]) steer(DIRS[code]);
    else if (code === "Enter" || code === "Space") primaryAction();
    else if (code === "KeyP") togglePause();
    else if (code === "KeyR") void newRun();
    else if (code === "ShiftRight") void action("help");
  });
  ui.onVisibility(({ value }) => {
    if (value === "hidden" && game.state.status === "running") {
      game.pause(); pauseReason = "PAUSED — WINDOW INACTIVE"; refreshUI();
    }
  });
  ui.presentation.onChange(() => updateActions());
  ui.fixedStep(60, ({ delta }) => {
    if (game.state.status !== "running") { movementMs = 0; return; }
    movementMs += delta * 1000;
    const delay = speedFor(game.state.mode, game.state.foods);
    if (movementMs < delay) return;
    movementMs -= delay;
    const event = game.step();
    if (!event) return;
    if (event.type === "eat") { flashMs = 130; flashColor = "#55ff7722"; cue("eat"); }
    if (event.type === "dead" || event.type === "won") {
      movementMs = 0; flashMs = 200; flashColor = event.type === "dead" ? "#ff774433" : "#55ffff22";
      finish(event);
    }
    refreshUI(true);
  });
  ui.animate(({ delta }) => {
    if (game.state.status === "running" && game.state.mode === "sprint") {
      game.addElapsed(delta * 1000);
      const shown = Math.floor(game.state.elapsedMs / 100);
      if (shown !== lastTimeShown) { lastTimeShown = shown; updateHud(); }
    }
    if (flashMs > 0) { flashMs = Math.max(0, flashMs - delta * 1000); drawCanvas(); }
  });
  refreshUI(true);
  if (pendingScore !== null) void flushPending(); else void refreshScores();
});
