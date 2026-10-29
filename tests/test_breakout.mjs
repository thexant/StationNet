import assert from "node:assert/strict";
import * as engine from "../stationnet/application_packages/breakout/engine.js";

for (const [level, count] of [[1, 50], [2, 60], [3, 70], [4, 70], [5, 70], [6, 70], [7, 70]]) {
  const bricks = engine.makeBricks(level);
  assert.equal(bricks.length, count, `level ${level} keeps its point total`);
  assert.ok(bricks.every((brick) => brick.x >= 0 && brick.x + brick.width <= engine.WIDTH &&
    brick.y >= 0 && brick.y + brick.height < engine.PADDLE_Y));
}
assert.equal(engine.speedForLevel(100), 390);

const left = engine.createGame(() => 0.1);
const right = engine.createGame(() => 0.9);
assert.ok(left.launch() && right.launch());
assert.ok(left.state.ball.vx < 0 && right.state.ball.vx > 0, "launch chooses both sides");
assert.ok(left.state.ball.vy < 0 && right.state.ball.vy < 0);

const attached = engine.createGame();
const initialX = attached.state.paddleX;
attached.tick(1 / 60, 1);
assert.ok(Math.abs(attached.state.paddleX - initialX - engine.PADDLE_SPEED / 60) < 1e-9);
assert.equal(attached.state.ball.x, attached.state.paddleX + engine.PADDLE_W / 2);
attached.tick(1 / 60, 0, engine.WIDTH);
assert.ok(attached.state.paddleX - initialX <= engine.PADDLE_SPEED / 30 + 0.001,
  "direct pointer control respects the same movement cap");

const game = engine.createGame(() => 0.8);
game.launch();
game.state.bricks = [{ x: 300, y: 210, width: 56, height: 18, color: "#fff" }];
Object.assign(game.state.ball, { x: 320, y: 245, vx: 0, vy: -390 });
const clear = game.tick(0.05);
assert.deepEqual(clear.map((event) => event.type), ["brick", "level"]);
assert.equal(game.state.score, 10);
assert.equal(game.state.level, 2);
assert.equal(game.state.bricks.length, 60);
assert.equal(game.state.mode, "ready");
assert.equal(game.state.ball.x, game.state.paddleX + engine.PADDLE_W / 2);

game.launch();
game.state.bricks = [{ x: 300, y: 210, width: 56, height: 18, color: "#fff" },
  { x: 450, y: 210, width: 56, height: 18, color: "#fff" }];
Object.assign(game.state.ball, { x: 285, y: 219, vx: 390, vy: 0 });
const sideHit = game.tick(0.05);
assert.equal(sideHit.filter((event) => event.type === "brick").length, 1,
  "swept side collision hits a brick crossed inside one tick");
assert.ok(game.state.ball.vx < 0);

game.state.bricks.push({ x: 300, y: 210, width: 56, height: 18, color: "#fff" });
Object.assign(game.state.ball, { x: 280, y: 190, vx: 390, vy: 390 });
const cornerHit = game.tick(0.05);
assert.equal(cornerHit.filter((event) => event.type === "brick").length, 1,
  "a fast corner approach still hits its brick");
assert.equal(game.state.score, 30);

Object.assign(game.state.ball, { x: game.state.paddleX + engine.PADDLE_W / 2, y: 380, vx: 0, vy: 390 });
assert.ok(game.tick(0.05).some((event) => event.type === "paddle"));
assert.ok(game.state.ball.vy < 0);
const frozenY = game.state.ball.y;
assert.ok(game.pause());
game.tick(0.05, 1);
assert.equal(game.state.ball.y, frozenY, "pause freezes the ball");
assert.ok(game.resume());

for (let life = 2; life >= 0; life -= 1) {
  Object.assign(game.state.ball, { x: 10, y: engine.HEIGHT + engine.BALL_R + 1, vx: 0, vy: 390 });
  const events = game.tick(1 / 60);
  assert.equal(game.state.lives, life);
  assert.equal(events.at(-1).type, life === 0 ? "gameover" : "life");
  if (life) game.launch();
}
assert.equal(game.state.mode, "gameover");
game.reset();
assert.equal(game.state.score, 0);
assert.equal(game.state.lives, 3);
assert.equal(game.state.mode, "ready");

// Exercise the StationNet adapter, including keyboard launch, focus pause, and score retry.
let rendered, fixedStep, animation, onKey, onVisibility, runtimeGame, submitCalls = 0;
let pad = { axes: [], buttons: [] };
const updates = new Map(), store = new Map();
const component = (type, value = {}, options = {}) => ({ type, ...value, ...options });
const ui = {
  modules: { load: async () => ({ ...engine, createGame(random) { runtimeGame = engine.createGame(random); return runtimeGame; } }) },
  random: { seed() {}, next: () => 0.8 },
  storage: {
    get: async (key) => store.get(key) ?? null,
    set: async (key, value) => { store.set(key, value); return true; },
    delete: async (key) => store.delete(key)
  },
  scores: {
    leaderboard: async () => ({ personal_best: 0, leaderboard: [] }),
    submit: async (score) => {
      submitCalls += 1;
      if (submitCalls === 1) throw Error("offline");
      return { personal_best: score, leaderboard: [{ username: "player", score }] };
    }
  },
  input: { gamepad: () => pad },
  sound: { playTone() {} },
  presentation: { mode: "standard", onChange() {}, enterImmersive() {}, exitImmersive() {} },
  text: (text, options) => component("text", { text }, options),
  title: (text, options) => component("title", { text }, options),
  button: (label, onClick, options) => component("button", { label, onClick }, options),
  menu: (label, items, onSelect, options) => component("menu", { label, items, onSelect }, options),
  table: (headers, rows, options) => component("table", { headers, rows }, options),
  canvas: (width, height, commands, options) => component("canvas", { width, height, commands }, options),
  panel: (label, children, options) => component("panel", { label, children }, options),
  dialog: async () => "close",
  render: (...items) => { rendered = items; },
  update: (id, value) => { updates.set(id, value); },
  onKey: (handler) => { onKey = handler; },
  onVisibility: (handler) => { onVisibility = handler; },
  fixedStep: (_hz, handler) => { fixedStep = handler; },
  animate: (handler) => { animation = handler; }
};
let startup;
globalThis.StationNet = { start: (callback) => { startup = callback(ui); } };
await import(`../stationnet/application_packages/breakout/main.js?test=${Date.now()}`);
await startup;
assert.equal(rendered.find((item) => item.type === "canvas")?.onPointer instanceof Function, true);
assert.equal(typeof fixedStep, "function");
assert.equal(typeof animation, "function");
assert.equal(updates.get("playfield").controls[0].label, "LAUNCH");
const touch = rendered.find((item) => item.type === "menu" && item.label === "Touch paddle");
const beforeTouch = runtimeGame.state.paddleX;
touch.onPress({ value: "left", phase: "start" });
fixedStep({ delta: 1 / 60 });
touch.onRelease({ value: "left", phase: "end" });
assert.ok(runtimeGame.state.paddleX < beforeTouch, "held touch input moves the paddle");
onKey({ code: "Space", key: " ", phase: "down", repeat: false });
assert.equal(runtimeGame.state.mode, "running");
const beforePad = runtimeGame.state.paddleX;
pad = { axes: [1], buttons: [] };
fixedStep({ delta: 1 / 60 });
assert.ok(runtimeGame.state.paddleX > beforePad, "gamepad axis moves the paddle");
pad = { axes: [], buttons: [] };
onKey({ code: "KeyP", key: "p", phase: "down", repeat: false });
assert.equal(runtimeGame.state.mode, "paused");
onKey({ code: "Space", key: " ", phase: "down", repeat: false });
assert.equal(runtimeGame.state.mode, "running");
onVisibility({ value: "hidden" });
assert.equal(runtimeGame.state.mode, "paused");
onVisibility({ value: "visible" });
assert.equal(runtimeGame.state.mode, "paused", "focus return needs an explicit resume");
updates.get("playfield").controls[0].onClick();
assert.equal(runtimeGame.state.mode, "running");

for (let life = 0; life < 3; life += 1) {
  Object.assign(runtimeGame.state.ball, { x: 10, y: engine.HEIGHT + engine.BALL_R + 1, vx: 0, vy: 390 });
  fixedStep({ delta: 1 / 60 });
  if (life < 2) updates.get("playfield").controls[0].onClick();
}
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(runtimeGame.state.mode, "gameover");
assert.equal(submitCalls, 1);
assert.equal(store.get("breakout.pending-score.v1"), 0, "failed zero score is retained");
const retry = updates.get("actions");
assert.ok(retry.items.some((item) => item.value === "retry"));
await retry.onSelect({ value: "retry" });
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(submitCalls, 2);
assert.equal(store.has("breakout.pending-score.v1"), false);
assert.equal(updates.get("leaderboard").rows[0][1], "player");
pad = { axes: [], buttons: [1] };
fixedStep({ delta: 1 / 60 });
assert.equal(runtimeGame.state.mode, "running", "gamepad A starts the next run");

console.log("Breakout engine and runtime checks passed");
