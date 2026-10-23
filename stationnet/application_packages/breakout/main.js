"use strict";

StationNet.start(async (ui) => {
  const WIDTH = 640, HEIGHT = 440, PADDLE_Y = 405, PADDLE_W = 96, BALL_R = 7;
  const colors = ["#00ed45", "#00c8d7", "#ffb000", "#ff5555", "#55ff77", "#55ffff"];
  let paddleX, ball, bricks, score = 0, best = 0, lives = 3, level = 1, state = "ready", readyLabel = "START", leftHeld = false, rightHeld = false;

  function makeBricks() {
    const rows = Math.min(7, 4 + level), columns = 10, gap = 5, width = 56, height = 18, startX = 18, startY = 38;
    bricks = [];
    for (let y = 0; y < rows; y += 1) for (let x = 0; x < columns; x += 1) bricks.push({ x: startX + x * (width + gap), y: startY + y * (height + gap), width, height, color: colors[y % colors.length] });
  }

  function resetBall() {
    paddleX = (WIDTH - PADDLE_W) / 2;
    const speed = 225 + (level - 1) * 18, angle = (Math.random() * .5 + .75) * Math.PI;
    ball = { x: WIDTH / 2, y: PADDLE_Y - 18, vx: Math.cos(angle) * speed, vy: -Math.abs(Math.sin(angle) * speed) };
  }

  function restart() {
    score = 0; lives = 3; level = 1; state = "ready"; readyLabel = "START"; makeBricks(); resetBall();
    updateHud(); updateStatus("READY — PRESS START"); updateControls(); draw(true);
  }

  function start() {
    if (state === "gameover") restart();
    if (state === "running") return;
    state = "running"; updateStatus("RUNNING"); updateControls(); draw(true);
  }

  function togglePause() {
    if (state === "running") { state = "paused"; updateStatus("PAUSED"); }
    else if (state === "paused") start();
    updateControls();
  }

  function updateControls() { ui.update("pause", ui.button(state === "paused" ? "Resume" : "Pause", togglePause, { id: "pause", disabled: state !== "running" && state !== "paused" })); }
  function updateHud() { ui.update("hud", ui.text(`SCORE ${score}   LIVES ${lives}   LEVEL ${level}   PERSONAL BEST ${best}`, { id: "hud", emphasis: true })); }
  function updateStatus(message) { ui.update("game-status", ui.text(message, { id: "game-status", tone: state === "gameover" ? "warning" : "accent" })); }

  function normalizeBall() {
    const speed = 225 + (level - 1) * 18;
    if (Math.abs(ball.vy) < speed * .42) ball.vy = Math.sign(ball.vy || -1) * speed * .42;
    const magnitude = Math.hypot(ball.vx, ball.vy) || speed;
    ball.vx = ball.vx / magnitude * speed; ball.vy = ball.vy / magnitude * speed;
  }

  function step(dt) {
    const previousX = ball.x, previousY = ball.y;
    ball.x += ball.vx * dt; ball.y += ball.vy * dt;
    if (ball.x < BALL_R) { ball.x = BALL_R; ball.vx = Math.abs(ball.vx); }
    if (ball.x > WIDTH - BALL_R) { ball.x = WIDTH - BALL_R; ball.vx = -Math.abs(ball.vx); }
    if (ball.y < BALL_R) { ball.y = BALL_R; ball.vy = Math.abs(ball.vy); }
    if (ball.vy > 0 && ball.y + BALL_R >= PADDLE_Y && previousY + BALL_R <= PADDLE_Y && ball.x >= paddleX - BALL_R && ball.x <= paddleX + PADDLE_W + BALL_R) {
      ball.y = PADDLE_Y - BALL_R; const impact = (ball.x - (paddleX + PADDLE_W / 2)) / (PADDLE_W / 2);
      ball.vx += impact * 170; ball.vy = -Math.abs(ball.vy); normalizeBall();
    }
    for (let index = 0; index < bricks.length; index += 1) {
      const brick = bricks[index];
      if (ball.x + BALL_R < brick.x || ball.x - BALL_R > brick.x + brick.width || ball.y + BALL_R < brick.y || ball.y - BALL_R > brick.y + brick.height) continue;
      bricks.splice(index, 1); score += 10;
      if (previousX <= brick.x - BALL_R || previousX >= brick.x + brick.width + BALL_R) ball.vx *= -1; else ball.vy *= -1;
      normalizeBall(); updateHud(); break;
    }
    if (!bricks.length) { level += 1; makeBricks(); resetBall(); state = "ready"; readyLabel = "CONTINUE"; updateHud(); updateStatus(`LEVEL ${level} READY — CONTINUE`); updateControls(); draw(true); }
    if (ball.y - BALL_R > HEIGHT) loseLife();
  }

  function loseLife() {
    lives -= 1; updateHud();
    if (lives <= 0) gameOver();
    else { resetBall(); state = "ready"; readyLabel = "CONTINUE"; updateStatus(`LIFE LOST — ${lives} REMAIN — CONTINUE`); updateControls(); draw(true); }
  }

  async function gameOver() {
    state = "gameover"; best = Math.max(best, score); updateHud(); updateControls(); updateStatus(`GAME OVER — FINAL SCORE ${score}`); draw(true);
    await ui.scores.submit(score).then(showScores).catch(() => {});
  }

  function draw(contextChanged = false) {
    const commands = [{ type: "rect", x: 0, y: 0, width: WIDTH, height: HEIGHT, color: "#010401" }];
    bricks.forEach((brick) => { commands.push({ type: "rect", ...brick }); commands.push({ type: "rect", x: brick.x + 3, y: brick.y + 3, width: brick.width - 6, height: 2, color: "#ffffff" }); });
    commands.push({ type: "rect", x: paddleX, y: PADDLE_Y, width: PADDLE_W, height: 12, color: "#55ffff" });
    commands.push({ type: "circle", x: ball.x, y: ball.y, radius: BALL_R, color: "#ffffff" });
    const settings = { id: "playfield", hover: true, label: "Breakout playfield" };
    if (contextChanged) settings.controls = state === "ready" ? [{ label: readyLabel, onClick: start }] : state === "gameover" ? [{ label: "RESTART", onClick: restart, variant: "secondary" }] : [];
    ui.update("playfield", ui.canvas(WIDTH, HEIGHT, commands, settings));
  }

  function pointer({ x }) { paddleX = Math.max(0, Math.min(WIDTH - PADDLE_W, x - PADDLE_W / 2)); draw(); }
  function moveHeld(value, phase) {
    if (value === "left") leftHeld = phase !== "end";
    if (value === "right") rightHeld = phase !== "end";
  }
  function key({ key, phase, repeat }) {
    if (["ArrowLeft", "a", "A"].includes(key)) moveHeld("left", phase === "up" ? "end" : "start");
    else if (["ArrowRight", "d", "D"].includes(key)) moveHeld("right", phase === "up" ? "end" : "start");
    else if ([" ", "Enter"].includes(key) && phase === "down" && !repeat) togglePause();
  }
  async function showScores(result) {
    if (!result) return; best = Number(result.personal_best) || best; updateHud();
    const rows = (result.leaderboard || []).map((entry, index) => [index + 1, entry.username, entry.score]);
    ui.update("leaderboard", ui.table(["#", "Player", "Score"], rows.length ? rows : [["—", "No scores yet", "—"]], { id: "leaderboard" }));
  }

  ui.render(
    ui.title("Breakout // Brick Breaker"),
    ui.text("Hold arrow keys, A/D, or touch controls to move the paddle. Mouse and touch movement work directly on the playfield. Clear every brick to advance.", { tone: "muted" }),
    ui.text("SCORE 0   LIVES 3   LEVEL 1   PERSONAL BEST 0", { id: "hud", emphasis: true }),
    ui.text("READY", { id: "game-status", tone: "accent" }),
    ui.canvas(WIDTH, HEIGHT, [], { id: "playfield", onPointer: pointer, hover: true, label: "Breakout playfield" }),
    ui.menu("Touch paddle", [{ label: "◀ LEFT", value: "left" }, { label: "RIGHT ▶", value: "right" }], null, { id: "game-controls", onPress: ({ value, phase }) => moveHeld(value, phase), onRelease: ({ value, phase }) => moveHeld(value, phase) }),
    ui.button("Pause", togglePause, { id: "pause" }),
    ui.heading("Station leaderboard", 3), ui.table(["#", "Player", "Score"], [["—", "Loading…", "—"]], { id: "leaderboard" })
  );
  ui.onKey(key);
  ui.onVisibility(({ value }) => { if (value === "hidden" && state === "running") { state = "paused"; updateStatus("PAUSED — WINDOW INACTIVE"); updateControls(); } });
  ui.animate(({ delta }) => { const dt = Math.min(.032, delta); if (state === "running") { paddleX += (rightHeld - leftHeld) * 380 * dt; paddleX = Math.max(0, Math.min(WIDTH - PADDLE_W, paddleX)); step(dt); draw(); } });
  restart(); ui.scores.leaderboard().then(showScores).catch(() => updateStatus("READY — LEADERBOARD UNAVAILABLE"));
});
