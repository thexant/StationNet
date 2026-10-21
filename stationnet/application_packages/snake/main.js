"use strict";

StationNet.start(async (ui) => {
  const COLS = 30, ROWS = 20, CELL = 20, shared = {};
  const directions = { ArrowUp: [0, -1], w: [0, -1], W: [0, -1], ArrowDown: [0, 1], s: [0, 1], S: [0, 1], ArrowLeft: [-1, 0], a: [-1, 0], A: [-1, 0], ArrowRight: [1, 0], d: [1, 0], D: [1, 0] };
  let snake, food, direction, queued, score = 0, best = 0, state = "ready", lastMove = 0, pointerStart = null;

  const cell = (x, y) => ({ x, y });
  const same = (a, b) => a.x === b.x && a.y === b.y;
  const speed = () => Math.max(70, 155 - score * 4);

  function reset() {
    snake = [cell(8, 10), cell(7, 10), cell(6, 10), cell(5, 10)];
    direction = [1, 0]; queued = direction; score = 0; state = "ready"; placeFood();
    draw(true); updateHud(); updateStatus("READY — START OR PRESS AN ARROW"); updateControls();
  }

  function placeFood() {
    const open = [];
    for (let y = 0; y < ROWS; y += 1) for (let x = 0; x < COLS; x += 1) if (!snake.some((part) => part.x === x && part.y === y)) open.push(cell(x, y));
    food = open[Math.floor(Math.random() * open.length)];
  }

  function setDirection(next) {
    if (!next || next[0] === -direction[0] && next[1] === -direction[1]) return;
    queued = next;
    if (state === "ready") start();
  }

  function start() {
    if (state === "gameover") reset();
    if (state === "running") return;
    state = "running"; lastMove = Date.now(); updateStatus("RUNNING"); updateControls(); draw(true);
  }

  function togglePause() {
    if (state === "running") { state = "paused"; updateStatus("PAUSED"); }
    else if (state === "paused") start();
    updateControls();
  }

  function move() {
    direction = queued;
    const head = cell(snake[0].x + direction[0], snake[0].y + direction[1]);
    const eating = food && same(head, food);
    const body = eating ? snake : snake.slice(0, -1);
    if (head.x < 0 || head.x >= COLS || head.y < 0 || head.y >= ROWS || body.some((part) => same(part, head))) return gameOver();
    snake.unshift(head);
    if (eating) { score += 10; placeFood(); updateHud(); } else snake.pop();
    draw();
  }

  async function gameOver() {
    state = "gameover"; best = Math.max(best, score); updateHud(); updateControls();
    updateStatus(`GAME OVER — FINAL SCORE ${score}`); draw(true);
    await ui.scores.submit(score).then(showScores).catch(() => {});
  }

  function commands() {
    const output = [{ type: "rect", x: 0, y: 0, width: COLS * CELL, height: ROWS * CELL, color: "#010401" }];
    for (let x = 0; x <= COLS; x += 1) output.push({ type: "rect", x: x * CELL, y: 0, width: 1, height: ROWS * CELL, color: "#07300f" });
    for (let y = 0; y <= ROWS; y += 1) output.push({ type: "rect", x: 0, y: y * CELL, width: COLS * CELL, height: 1, color: "#07300f" });
    snake.forEach((part, index) => output.push({ type: "rect", x: part.x * CELL + 2, y: part.y * CELL + 2, width: CELL - 4, height: CELL - 4, color: index ? "#00ad35" : "#55ff77" }));
    if (food) output.push({ type: "circle", x: food.x * CELL + CELL / 2, y: food.y * CELL + CELL / 2, radius: 7, color: "#ffb000" });
    return output;
  }

  function draw(contextChanged = false) {
    const settings = { id: "playfield", label: "Snake playfield" };
    if (contextChanged) settings.controls = state === "ready" ? [{ label: "START", onClick: start }] : state === "gameover" ? [{ label: "RESTART", onClick: reset, variant: "secondary" }] : [];
    ui.update("playfield", ui.canvas(COLS * CELL, ROWS * CELL, commands(), settings));
  }
  function updateHud() { ui.update("hud", ui.text(`SCORE ${score}   PERSONAL BEST ${best}`, { id: "hud", emphasis: true })); }
  function updateStatus(message) { ui.update("game-status", ui.text(message, { id: "game-status", tone: state === "gameover" ? "warning" : "accent" })); }
  function updateControls() {
    ui.update("pause", ui.button(state === "paused" ? "Resume" : "Pause", togglePause, { id: "pause", disabled: state !== "running" && state !== "paused" }));
  }

  function pointer(event) {
    if (event.phase === "start") pointerStart = [event.x, event.y];
    if (event.phase !== "end" || !pointerStart) return;
    const dx = event.x - pointerStart[0], dy = event.y - pointerStart[1]; pointerStart = null;
    if (Math.max(Math.abs(dx), Math.abs(dy)) < 12) return;
    setDirection(Math.abs(dx) > Math.abs(dy) ? [Math.sign(dx), 0] : [0, Math.sign(dy)]);
  }

  async function showScores(result) {
    if (!result) return;
    best = Number(result.personal_best) || best;
    updateHud();
    const rows = (result.leaderboard || []).map((entry, index) => [index + 1, entry.username, entry.score]);
    ui.update("leaderboard", ui.table(["#", "Player", "Score"], rows.length ? rows : [["—", "No scores yet", "—"]], { id: "leaderboard" }));
  }

  ui.render(
    ui.title("Snake // Packet Run"),
    ui.text("Arrow keys or WASD. Swipe the playfield or use the touch controls. Collect packets; avoid walls and your own signal trail.", { tone: "muted" }),
    ui.text("SCORE 0   PERSONAL BEST 0", { id: "hud", emphasis: true }),
    ui.text("READY", { id: "game-status", tone: "accent" }),
    ui.canvas(COLS * CELL, ROWS * CELL, [], { id: "playfield", onPointer: pointer, label: "Snake playfield" }),
    ui.menu("Touch controls", [{ label: "▲", value: "up" }, { label: "◀", value: "left" }, { label: "▼", value: "down" }, { label: "▶", value: "right" }], ({ value }) => setDirection({ up: [0, -1], left: [-1, 0], down: [0, 1], right: [1, 0] }[value]), { layout: "dpad" }),
    ui.button("Pause", togglePause, { id: "pause" }),
    ui.heading("Station leaderboard", 3),
    ui.table(["#", "Player", "Score"], [["—", "Loading…", "—"]], { id: "leaderboard" })
  );
  ui.onKey(({ key }) => setDirection(directions[key]));
  ui.onVisibility(({ value }) => { if (value === "hidden" && state === "running") { state = "paused"; updateStatus("PAUSED — WINDOW INACTIVE"); updateControls(); } });
  ui.interval(() => { if (state === "running" && Date.now() - lastMove >= speed()) { lastMove = Date.now(); move(); } }, 20);
  reset(); ui.scores.leaderboard().then(showScores).catch(() => updateStatus("READY — LEADERBOARD UNAVAILABLE"));
});
