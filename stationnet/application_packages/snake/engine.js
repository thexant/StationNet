export const COLS = 30;
export const ROWS = 20;
export const SPRINT_TARGET = 20;

export const MODES = Object.freeze({
  classic: { name: "Classic", description: "Collect food and survive as long as you can." },
  sprint: { name: "Sprint", description: "Collect 20 foods as quickly as you can." },
  maze: { name: "Maze", description: "Navigate the station's fixed obstacle course." }
});

const cell = (x, y) => ({ x, y });
const key = (part) => `${part.x},${part.y}`;
const same = (a, b) => a.x === b.x && a.y === b.y;
const opposite = (a, b) => a[0] === -b[0] && a[1] === -b[1];

function mazeObstacles() {
  const result = [];
  for (const y of [4, 13]) {
    for (const x of [5, 22]) {
      for (let dy = 0; dy < 3; dy += 1) {
        for (let dx = 0; dx < 3; dx += 1) result.push(cell(x + dx, y + dy));
      }
    }
  }
  for (const y of [5, 14]) for (let x = 13; x <= 16; x += 1) result.push(cell(x, y));
  return result;
}

export function speedFor(mode, foods) {
  if (mode === "sprint") return 150;
  if (mode === "maze") return Math.max(110, 185 - Math.max(0, foods) * 2);
  return Math.max(95, 180 - Math.max(0, foods) * 3);
}

export function createGame(mode = "classic", random = Math.random) {
  if (!MODES[mode]) throw new Error(`Unknown Snake mode: ${mode}`);
  const obstacles = mode === "maze" ? mazeObstacles() : [];
  const obstacleSet = new Set(obstacles.map(key));
  const state = {
    mode, status: "ready", snake: [cell(8, 10), cell(7, 10), cell(6, 10), cell(5, 10)],
    direction: [1, 0], turns: [], food: null, foods: 0, score: 0, elapsedMs: 0,
    obstacles
  };

  function placeFood() {
    const occupied = new Set(state.snake.map(key));
    const open = [];
    for (let y = 0; y < ROWS; y += 1) {
      for (let x = 0; x < COLS; x += 1) {
        const part = cell(x, y);
        if (!occupied.has(key(part)) && !obstacleSet.has(key(part))) open.push(part);
      }
    }
    if (!open.length) {
      state.food = null;
      return false;
    }
    const value = Number(random());
    const index = Math.floor(Math.min(0.999999999, Math.max(0, Number.isFinite(value) ? value : 0)) * open.length);
    state.food = open[index];
    return true;
  }

  function turn(next) {
    if (!Array.isArray(next) || next.length !== 2 || !Number.isInteger(next[0]) ||
        !Number.isInteger(next[1]) || Math.abs(next[0]) + Math.abs(next[1]) !== 1 ||
        (state.status !== "ready" && state.status !== "running")) return false;
    const previous = state.turns.at(-1) || state.direction;
    if (state.turns.length >= 2 || same(cell(...previous), cell(...next)) || opposite(previous, next)) return false;
    state.turns.push([...next]);
    return true;
  }

  function start() {
    if (state.status !== "ready" && state.status !== "paused") return false;
    state.status = "running";
    return true;
  }

  function pause() {
    if (state.status !== "running") return false;
    state.status = "paused";
    return true;
  }

  function addElapsed(milliseconds) {
    if (state.status === "running" && Number.isFinite(milliseconds) && milliseconds > 0)
      state.elapsedMs += milliseconds;
  }

  function step() {
    if (state.status !== "running") return null;
    state.direction = state.turns.shift() || state.direction;
    const head = cell(state.snake[0].x + state.direction[0], state.snake[0].y + state.direction[1]);
    const eating = state.food && same(head, state.food);
    const body = eating ? state.snake : state.snake.slice(0, -1);
    if (head.x < 0 || head.x >= COLS || head.y < 0 || head.y >= ROWS ||
        obstacleSet.has(key(head)) || body.some((part) => same(part, head))) {
      state.status = "dead";
      return { type: "dead" };
    }
    state.snake.unshift(head);
    if (!eating) {
      state.snake.pop();
      return { type: "move" };
    }
    state.foods += 1;
    state.score += 10;
    if (mode === "sprint" && state.foods >= SPRINT_TARGET) {
      state.food = null;
      state.status = "won";
      return { type: "won" };
    }
    if (!placeFood()) {
      state.status = "won";
      return { type: "won" };
    }
    return { type: "eat" };
  }

  placeFood();
  return { state, turn, start, pause, addElapsed, step, placeFood };
}
