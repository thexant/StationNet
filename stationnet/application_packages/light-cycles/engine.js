// Pure, fixed-tick rules shared by the game and its collision tests.
export const ARENAS = { compact: [40, 26], standard: [56, 36], wide: [72, 46] };
export const DIRECTIONS = [[1, 0], [0, 1], [-1, 0], [0, -1]];
export const safeInset = (ticks) => Math.max(0, Math.floor((ticks - 180) / 10));

function inside(round, x, y, inset) {
  return x >= inset && x < round.width - inset && y >= inset && y < round.height - inset;
}

export function createRound(count, arena = "standard") {
  const [width, height] = ARENAS[arena] || ARENAS.standard;
  const starts = [
    [Math.floor(width * .18), Math.floor(height * .26), 0],
    [Math.floor(width * .82), Math.floor(height * .74), 2],
    [Math.floor(width * .73), Math.floor(height * .24), 1],
    [Math.floor(width * .27), Math.floor(height * .76), 3]
  ];
  const occupied = new Uint8Array(width * height);
  const riders = starts.slice(0, Math.max(1, Math.min(4, count))).map(([x, y, direction], id) => {
    occupied[y * width + x] = id + 1;
    return { id, x, y, direction, queued: direction, alive: true, survived: 0 };
  });
  return { width, height, occupied, riders, ticks: 0, over: false, winner: null };
}

export function queueDirection(round, id, direction) {
  const rider = round.riders[id];
  if (!rider || !rider.alive || !Number.isInteger(direction) || direction < 0 || direction > 3) return false;
  // A cycle cannot reverse, including after another turn was queued this tick.
  if ((direction + 2) % 4 === rider.direction) return false;
  rider.queued = direction;
  return true;
}

export function turn(round, id, side) {
  const rider = round.riders[id];
  if (!rider) return false;
  return queueDirection(round, id, (rider.direction + (side === "left" ? 3 : 1)) % 4);
}

export function step(round) {
  if (round.over) return { eliminated: [], winner: round.winner, over: true };
  const proposals = round.riders.map((rider) => {
    if (!rider.alive) return null;
    const direction = rider.queued;
    const [dx, dy] = DIRECTIONS[direction];
    return { x: rider.x + dx, y: rider.y + dy, direction };
  });
  const crashes = new Set();
  const targets = new Map();
  const inset = safeInset(round.ticks + 1);
  proposals.forEach((next, id) => {
    if (!next) return;
    if (!inside(round, next.x, next.y, inset) ||
        round.occupied[next.y * round.width + next.x]) {
      crashes.add(id);
      return;
    }
    const cell = next.y * round.width + next.x;
    if (targets.has(cell)) {
      crashes.add(id);
      crashes.add(targets.get(cell));
    } else targets.set(cell, id);
  });
  proposals.forEach((next, id) => {
    if (!next) return;
    const rider = round.riders[id];
    if (crashes.has(id)) { rider.alive = false; return; }
    rider.x = next.x;
    rider.y = next.y;
    rider.direction = next.direction;
    rider.queued = next.direction;
    rider.survived += 1;
    round.occupied[next.y * round.width + next.x] = id + 1;
  });
  round.ticks += 1;
  const alive = round.riders.filter((rider) => rider.alive);
  if (round.riders.length === 1 ? alive.length === 0 : alive.length <= 1) {
    round.over = true;
    round.winner = round.riders.length > 1 && alive.length === 1 ? alive[0].id : null;
  }
  return { eliminated: [...crashes].sort(), winner: round.winner, over: round.over };
}

function clearRun(round, x, y, direction) {
  const [dx, dy] = DIRECTIONS[direction];
  const inset = safeInset(round.ticks + 1);
  let distance = 0;
  while (distance < 16) {
    x += dx; y += dy;
    if (!inside(round, x, y, inset) || round.occupied[y * round.width + x]) break;
    distance += 1;
  }
  return distance;
}

function openArea(round, x, y) {
  const inset = safeInset(round.ticks + 1);
  const seen = new Set([y * round.width + x]);
  const queue = [[x, y]];
  for (let index = 0; index < queue.length && queue.length < 80; index += 1) {
    const [cx, cy] = queue[index];
    for (const [dx, dy] of DIRECTIONS) {
      const nx = cx + dx, ny = cy + dy, cell = ny * round.width + nx;
      if (inside(round, nx, ny, inset) &&
          !round.occupied[cell] && !seen.has(cell)) { seen.add(cell); queue.push([nx, ny]); }
    }
  }
  return queue.length;
}

export function chooseComputerDirection(round, id, random = Math.random) {
  const rider = round.riders[id];
  if (!rider || !rider.alive) return null;
  let best = rider.direction, bestScore = -Infinity;
  const inset = safeInset(round.ticks + 1);
  for (const direction of [rider.direction, (rider.direction + 3) % 4, (rider.direction + 1) % 4]) {
    const [dx, dy] = DIRECTIONS[direction], x = rider.x + dx, y = rider.y + dy;
    if (!inside(round, x, y, inset) || round.occupied[y * round.width + x]) continue;
    const run = clearRun(round, x, y, direction);
    const area = openArea(round, x, y);
    const sideOpen = [3, 1].reduce((sum, offset) => sum + Math.min(4, clearRun(round, x, y, (direction + offset) % 4)), 0);
    const score = run * 1.5 + area * .16 + sideOpen * 1.3 + (direction === rider.direction ? 3 : 0) + random() * 2;
    if (score > bestScore) { bestScore = score; best = direction; }
  }
  return best;
}
