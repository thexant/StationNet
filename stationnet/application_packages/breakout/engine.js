// Pure Breakout rules. The StationNet adapter in main.js owns input, drawing, and persistence.
export const WIDTH = 640;
export const HEIGHT = 440;
export const PADDLE_Y = 405;
export const PADDLE_W = 96;
export const PADDLE_H = 12;
export const PADDLE_SPEED = 500;
export const BALL_R = 7;

const BRICK_W = 56;
const BRICK_H = 18;
const BRICK_X = 18;
const BRICK_Y = 35;
const BRICK_GAP = 5;
const PALETTE = ["#00ed45", "#00c8d7", "#ffb000", "#ff5555", "#55ff77", "#55ffff"];
const FULL = "##########";
const LAYOUTS = [
  [FULL, FULL, FULL, FULL, FULL],
  [".########.", "###.##.###", "####..####", "##..##..##", "####..####", "###.##.###", ".########.", "##..##..##"],
  [FULL, FULL, FULL, FULL, FULL, FULL, FULL],
  [FULL, "####..####", "###.##.###", "##.####.##", "#.######.#", "##.####.##", "###.##.###", "##..##..##", "###....###"],
  ["..######..", ".########.", FULL, ".########.", "..######..", ".########.", FULL, ".########.", "..######.."],
  [".########.", "##..##..##", "###.##.###", FULL, "..######..", FULL, "###.##.###", "##..##..##", ".########."]
];

export function speedForLevel(level) {
  return Math.min(390, 225 + (Math.max(1, level) - 1) * 18);
}

export function makeBricks(level) {
  const index = level <= 6 ? Math.max(0, level - 1) : 2 + (level - 3) % 4;
  const layout = LAYOUTS[index];
  const bricks = [];
  layout.forEach((row, y) => {
    for (let x = 0; x < row.length; x += 1) {
      if (row[x] !== "#") continue;
      bricks.push({ x: BRICK_X + x * (BRICK_W + BRICK_GAP), y: BRICK_Y + y * (BRICK_H + BRICK_GAP),
        width: BRICK_W, height: BRICK_H, color: PALETTE[y % PALETTE.length] });
    }
  });
  return bricks;
}

function clamp(value, low, high) { return Math.max(low, Math.min(high, value)); }

// Sweep the ball's centre against a rectangle expanded by its radius.
function sweepRect(ball, rectangle, seconds) {
  const left = rectangle.x - BALL_R, right = rectangle.x + rectangle.width + BALL_R;
  const top = rectangle.y - BALL_R, bottom = rectangle.y + rectangle.height + BALL_R;
  let enterX = -Infinity, exitX = Infinity, enterY = -Infinity, exitY = Infinity;
  if (ball.vx === 0) { if (ball.x < left || ball.x > right) return null; }
  else {
    const a = (left - ball.x) / ball.vx, b = (right - ball.x) / ball.vx;
    enterX = Math.min(a, b); exitX = Math.max(a, b);
  }
  if (ball.vy === 0) { if (ball.y < top || ball.y > bottom) return null; }
  else {
    const a = (top - ball.y) / ball.vy, b = (bottom - ball.y) / ball.vy;
    enterY = Math.min(a, b); exitY = Math.max(a, b);
  }
  const entry = Math.max(enterX, enterY);
  if (entry < -1e-8 || entry > seconds || entry > Math.min(exitX, exitY)) return null;
  const horizontal = enterX > enterY;
  return { time: Math.max(0, entry), nx: horizontal ? (ball.vx > 0 ? -1 : 1) : 0,
    ny: horizontal ? 0 : (ball.vy > 0 ? -1 : 1) };
}

export function createGame(random = Math.random) {
  const state = { mode: "ready", readyReason: "start", level: 1, lives: 3, score: 0,
    paddleX: (WIDTH - PADDLE_W) / 2, ball: { x: WIDTH / 2, y: PADDLE_Y - BALL_R - 2, vx: 0, vy: 0 }, bricks: [] };

  function attachBall() {
    state.ball.x = state.paddleX + PADDLE_W / 2;
    state.ball.y = PADDLE_Y - BALL_R - 2;
    state.ball.vx = 0; state.ball.vy = 0;
  }

  function reset() {
    state.mode = "ready"; state.readyReason = "start"; state.level = 1; state.lives = 3; state.score = 0;
    state.paddleX = (WIDTH - PADDLE_W) / 2;
    state.bricks = makeBricks(1);
    attachBall();
  }

  function launch() {
    if (state.mode !== "ready") return false;
    const choice = clamp(Number(random()) || 0, 0, 0.999999);
    const angle = 0.35 + (choice % 0.5) * 0.5; // 20–34 degrees from vertical.
    const side = choice < 0.5 ? -1 : 1;
    const speed = speedForLevel(state.level);
    state.ball.vx = side * Math.sin(angle) * speed;
    state.ball.vy = -Math.cos(angle) * speed;
    state.mode = "running";
    return true;
  }

  function pause() { if (state.mode === "running") { state.mode = "paused"; return true; } return false; }
  function resume() { if (state.mode === "paused") { state.mode = "running"; return true; } return false; }

  function tick(seconds, direction = 0, targetX = null) {
    const events = [];
    const dt = clamp(Number(seconds) || 0, 0, 1 / 20);
    if (state.mode === "ready" || state.mode === "running") {
      const amount = PADDLE_SPEED * dt;
      if (targetX != null && Number.isFinite(targetX)) {
        const difference = clamp(targetX, 0, WIDTH - PADDLE_W) - state.paddleX;
        state.paddleX += clamp(difference, -amount, amount);
      } else state.paddleX += clamp(Number(direction) || 0, -1, 1) * amount;
      state.paddleX = clamp(state.paddleX, 0, WIDTH - PADDLE_W);
      if (state.mode === "ready") attachBall();
    }
    if (state.mode !== "running" || dt === 0) return events;

    const ball = state.ball;
    let remaining = dt;
    for (let attempts = 0; attempts < 8 && remaining > 1e-7; attempts += 1) {
      let hit = null;
      const consider = (candidate) => {
        if (candidate && candidate.time >= 0 && candidate.time <= remaining && (!hit || candidate.time < hit.time - 1e-8)) hit = candidate;
      };
      if (ball.vx < 0) consider({ time: (BALL_R - ball.x) / ball.vx, nx: 1, ny: 0, type: "wall" });
      if (ball.vx > 0) consider({ time: (WIDTH - BALL_R - ball.x) / ball.vx, nx: -1, ny: 0, type: "wall" });
      if (ball.vy < 0) consider({ time: (BALL_R - ball.y) / ball.vy, nx: 0, ny: 1, type: "wall" });
      if (ball.vy > 0) {
        const paddleHit = sweepRect(ball, { x: state.paddleX, y: PADDLE_Y, width: PADDLE_W, height: PADDLE_H }, remaining);
        if (paddleHit?.ny === -1) consider({ ...paddleHit, type: "paddle" });
      }
      state.bricks.forEach((brick, index) => {
        const brickHit = sweepRect(ball, brick, remaining);
        if (brickHit) consider({ ...brickHit, type: "brick", index });
      });
      if (!hit) { ball.x += ball.vx * remaining; ball.y += ball.vy * remaining; remaining = 0; break; }
      ball.x += ball.vx * hit.time; ball.y += ball.vy * hit.time;
      remaining -= hit.time;
      ball.x += hit.nx * 0.01; ball.y += hit.ny * 0.01;
      if (hit.type === "brick") {
        const brick = state.bricks.splice(hit.index, 1)[0];
        state.score += 10;
        events.push({ type: "brick", x: brick.x + brick.width / 2, y: brick.y + brick.height / 2, color: brick.color });
        if (!state.bricks.length) {
          state.level += 1; state.bricks = makeBricks(state.level); state.mode = "ready"; state.readyReason = "level";
          attachBall(); events.push({ type: "level" }); return events;
        }
      } else if (hit.type === "paddle") {
        const offset = clamp((ball.x - state.paddleX - PADDLE_W / 2) / (PADDLE_W / 2), -1, 1);
        const angle = offset * 1.05; // At most 60 degrees from vertical.
        const speed = speedForLevel(state.level);
        ball.vx = Math.sin(angle) * speed;
        ball.vy = -Math.cos(angle) * speed;
        events.push({ type: "paddle" });
        continue;
      }
      if (hit.nx) ball.vx *= -1;
      if (hit.ny) ball.vy *= -1;
    }
    if (ball.y - BALL_R > HEIGHT) {
      state.lives -= 1;
      if (state.lives <= 0) { state.mode = "gameover"; events.push({ type: "gameover" }); }
      else { state.mode = "ready"; state.readyReason = "life"; attachBall(); events.push({ type: "life" }); }
    }
    return events;
  }

  reset();
  return { state, reset, launch, pause, resume, tick };
}
