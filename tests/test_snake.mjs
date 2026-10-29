import assert from "node:assert/strict";
import * as engine from "../stationnet/application_packages/snake/engine.js";

const { COLS, ROWS, createGame, speedFor } = engine;
assert.equal(speedFor("classic", 0), 180);
assert.equal(speedFor("classic", 3), 171);
assert.equal(speedFor("classic", 100), 95);
assert.equal(speedFor("sprint", 100), 150);
assert.equal(speedFor("maze", 0), 185);
assert.equal(speedFor("maze", 100), 110);

const queued = createGame("classic", () => 0);
assert.ok(queued.turn([0, -1]));
assert.ok(queued.turn([-1, 0]));
assert.equal(queued.turn([0, 1]), false, "buffer is limited to two turns");
queued.start();
queued.step();
assert.deepEqual(queued.state.direction, [0, -1]);
queued.step();
assert.deepEqual(queued.state.direction, [-1, 0]);
assert.equal(queued.turn([1, 0]), false, "reverse against current direction is rejected");
queued.addElapsed(250);
assert.equal(queued.state.elapsedMs, 250);
queued.pause();
queued.addElapsed(1000);
assert.equal(queued.state.elapsedMs, 250, "pause freezes the timer");

const tail = createGame();
tail.state.snake = [{ x: 8, y: 10 }, { x: 7, y: 10 }, { x: 7, y: 11 }, { x: 8, y: 11 }];
tail.state.food = { x: 20, y: 10 };
tail.turn([0, 1]); tail.start();
assert.equal(tail.step().type, "move", "moving into the vacating tail is legal");

const body = createGame();
body.state.snake = [{ x: 8, y: 10 }, { x: 8, y: 9 }, { x: 7, y: 9 }, { x: 7, y: 10 }];
body.state.food = { x: 20, y: 10 };
body.turn([0, -1]); body.start();
assert.equal(body.step().type, "dead", "body collisions end a run");

const wall = createGame();
wall.state.snake = [{ x: 29, y: 10 }, { x: 28, y: 10 }];
wall.start();
assert.equal(wall.step().type, "dead");

const maze = createGame("maze", () => 0.5);
const obstacleSet = new Set(maze.state.obstacles.map(({ x, y }) => `${x},${y}`));
assert.ok(maze.state.obstacles.length > 0);
assert.ok(!obstacleSet.has(`${maze.state.food.x},${maze.state.food.y}`));
const seen = new Set(["8,10"]), frontier = [{ x: 8, y: 10 }];
while (frontier.length) {
  const { x, y } = frontier.pop();
  for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const nx = x + dx, ny = y + dy, k = `${nx},${ny}`;
    if (nx < 0 || nx >= COLS || ny < 0 || ny >= ROWS || seen.has(k) || obstacleSet.has(k)) continue;
    seen.add(k); frontier.push({ x: nx, y: ny });
  }
}
assert.equal(seen.size + obstacleSet.size, COLS * ROWS, "all Maze open cells are connected");
maze.state.snake = [{ x: 4, y: 4 }, { x: 3, y: 4 }];
maze.state.food = { x: 20, y: 10 };
maze.start();
assert.equal(maze.step().type, "dead", "Maze obstacles are solid");

const sprint = createGame("sprint");
sprint.state.foods = 19;
sprint.state.food = { x: 9, y: 10 };
sprint.start(); sprint.addElapsed(12340);
assert.equal(sprint.step().type, "won");
assert.equal(sprint.state.foods, 20);
assert.equal(sprint.state.elapsedMs, 12340);

const full = createGame("classic");
full.state.snake = [{ x: 8, y: 10 }];
for (let y = 0; y < ROWS; y += 1) for (let x = 0; x < COLS; x += 1)
  if (!(x === 8 && y === 10) && !(x === 9 && y === 10)) full.state.snake.push({ x, y });
full.state.food = { x: 9, y: 10 };
full.start();
assert.equal(full.step().type, "won", "a filled board wins rather than leaving undefined food");
assert.equal(full.state.snake.length, COLS * ROWS);
assert.equal(full.state.food, null);

// Exercise the StationNet adapter without a browser, including key phases and score retry.
let rendered, onKey, onVisibility, fixedStep, animate, activeGame, submitCalls = 0, dialogAnswer = "leave";
const updates = new Map(), store = new Map();
const component = (type, properties = {}, options = {}) => ({ type, ...properties, ...options });
const ui = {
  modules: { load: async () => ({ ...engine, createGame(mode, random) { activeGame = createGame(mode, random); return activeGame; } }) },
  random: { seed() {}, next: () => 0.3 },
  storage: { get: async (key) => store.get(key) ?? null, set: async (key, value) => { store.set(key, structuredClone(value)); return true; },
    delete: async (key) => store.delete(key) },
  scores: { leaderboard: async () => ({ personal_best: 0, leaderboard: [] }), submit: async (score) => {
    submitCalls += 1;
    if (submitCalls === 1) throw Error("offline");
    return { personal_best: score, leaderboard: [{ username: "player", score }] };
  } },
  graphics: { framebuffer: () => ({ palette: new Uint8Array(1024), clear() {}, fillRect() {}, present() {} }),
    frame: () => ({ type: "framebuffer", buffer: "snake-board" }) },
  sound: { playTone() {}, sequence() {} },
  presentation: { mode: "standard", onChange() {}, enterImmersive() { this.mode = "immersive"; }, exitImmersive() { this.mode = "standard"; } },
  title: (label, options) => component("title", { label }, options),
  text: (text, options) => component("text", { text }, options),
  heading: (label, level, options) => component("heading", { label, level }, options),
  statusBar: (text, options) => component("statusBar", { text }, options),
  button: (label, onClick, options) => component("button", { label, onClick }, options),
  menu: (label, items, onSelect, options) => component("menu", { label, items, onSelect }, options),
  canvas: (width, height, commands, options) => component("canvas", { width, height, commands }, options),
  table: (headers, rows, options) => component("table", { headers, rows }, options),
  dialog: async () => dialogAnswer,
  render: (...items) => { rendered = items; }, update: (id, value) => { updates.set(id, value); },
  onKey: (handler) => { onKey = handler; }, onVisibility: (handler) => { onVisibility = handler; },
  fixedStep: (_hz, handler) => { fixedStep = handler; }, animate: (handler) => { animate = handler; }
};
globalThis.StationNet = { start: (callback) => { globalThis.snakeStartup = callback(ui); } };
await import(`../stationnet/application_packages/snake/main.js?test=${Date.now()}`);
await globalThis.snakeStartup;
assert.equal(rendered.find((item) => item.type === "canvas").touchLayout, "gamepad");
assert.equal(updates.get("playfield").commands.length, 5);
assert.equal(activeGame.state.status, "ready");
onKey({ code: "ArrowUp", phase: "up" });
assert.equal(activeGame.state.status, "ready", "key release cannot start or steer");
onKey({ code: "ArrowUp", phase: "down" });
assert.equal(activeGame.state.status, "running");
for (let i = 0; i < 11; i += 1) fixedStep({ delta: 1 / 60 });
assert.deepEqual(activeGame.state.direction, [0, -1]);
onKey({ code: "ArrowLeft", phase: "down" });
onKey({ code: "ArrowUp", phase: "up" });
for (let i = 0; i < 11; i += 1) fixedStep({ delta: 1 / 60 });
assert.deepEqual(activeGame.state.direction, [-1, 0], "key release leaves the queued turn intact");
const pointer = rendered.find((item) => item.type === "canvas").onPointer;
pointer({ pointerId: 1, phase: "start", x: 100, y: 100 });
pointer({ pointerId: 1, phase: "move", x: 100, y: 120 });
assert.deepEqual(activeGame.state.turns[0], [0, 1], "swipes steer before the pointer lifts");
pointer({ pointerId: 1, phase: "cancel", x: 100, y: 120 });
onKey({ code: "KeyP", phase: "down" });
assert.equal(activeGame.state.status, "paused");
onKey({ code: "KeyP", phase: "down" });
assert.equal(activeGame.state.status, "running");
onVisibility({ value: "hidden" });
assert.equal(activeGame.state.status, "paused");
assert.match(updates.get("game-status").text, /WINDOW INACTIVE/);

// A Classic death queues a score; an explicit retry uses the same score.
updates.get("primary").onClick();
activeGame.state.snake = [{ x: 29, y: 10 }, { x: 28, y: 10 }];
activeGame.state.direction = [1, 0]; activeGame.state.turns = [];
for (let i = 0; i < 11; i += 1) fixedStep({ delta: 1 / 60 });
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(activeGame.state.status, "dead");
assert.equal(submitCalls, 1);
assert.match(updates.get("game-status").text, /GAME OVER/, "score failure does not replace gameplay status");
assert.ok(updates.get("game-actions").items.some((item) => item.value === "retry"));
await updates.get("game-actions").onSelect({ value: "retry" });
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(submitCalls, 2);
assert.equal(store.has("snake.pending-classic-score.v1"), false);
updates.get("primary").onClick();
assert.equal(activeGame.state.status, "running", "Play again launches in one action");
onKey({ code: "ShiftRight", phase: "down" });
assert.equal(activeGame.state.status, "paused", "touch Select opens help and pauses gameplay");
updates.get("primary").onClick();

const modeMenu = rendered.find((item) => item.id === "mode");
dialogAnswer = "keep";
modeMenu.onSelect({ value: "sprint" });
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(activeGame.state.mode, "classic");
assert.equal(activeGame.state.status, "running", "cancelled mode change resumes the run");
dialogAnswer = "leave";
modeMenu.onSelect({ value: "sprint" });
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(activeGame.state.mode, "sprint");
assert.equal(activeGame.state.status, "ready");
updates.get("primary").onClick();
activeGame.state.foods = 19;
activeGame.state.food = { x: 9, y: 10 };
activeGame.addElapsed(5000);
for (let i = 0; i < 9; i += 1) fixedStep({ delta: 1 / 60 });
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(activeGame.state.status, "won");
assert.ok(store.get("snake.records.v1").sprintMs >= 5000);
assert.equal(submitCalls, 2, "Sprint does not submit to the Classic leaderboard");
assert.ok(updates.get("playfield").commands.length < 10, "board size cannot exceed the canvas command cap");
animate({ delta: 0.1 });

modeMenu.onSelect({ value: "maze" });
await new Promise((resolve) => setTimeout(resolve, 0));
updates.get("primary").onClick();
activeGame.state.score = 30;
activeGame.state.snake = [{ x: 29, y: 10 }, { x: 28, y: 10 }];
activeGame.state.direction = [1, 0];
for (let i = 0; i < 12; i += 1) fixedStep({ delta: 1 / 60 });
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(activeGame.state.status, "dead");
assert.equal(store.get("snake.records.v1").mazeScore, 30);
assert.equal(submitCalls, 2, "Maze keeps its record private");

modeMenu.onSelect({ value: "classic" });
await new Promise((resolve) => setTimeout(resolve, 0));
updates.get("primary").onClick();
activeGame.state.snake = [{ x: 8, y: 10 }];
for (let y = 0; y < ROWS; y += 1) for (let x = 0; x < COLS; x += 1)
  if (!(x === 8 && y === 10) && !(x === 9 && y === 10)) activeGame.state.snake.push({ x, y });
activeGame.state.food = { x: 9, y: 10 };
for (let i = 0; i < 11; i += 1) fixedStep({ delta: 1 / 60 });
assert.equal(activeGame.state.status, "won");
assert.ok(updates.get("playfield").commands.length < 10, "a full board still draws within the command cap");

console.log("Snake engine and adapter tests passed");
