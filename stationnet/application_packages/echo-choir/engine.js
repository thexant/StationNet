export const MAX_ECHOES = 5;
export const SAVE_KEY = "echo-choir-save-v1";

const key = (p) => `${p.x},${p.y}`;
const same = (a, b) => a.x === b.x && a.y === b.y;
const copy = (p) => ({ x: p.x, y: p.y });

export function parseChapter(chapter) {
  const map = chapter.map;
  const pads = [], stars = [], portals = [];
  let spawn = null, exit = null, ghost = null;
  map.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) {
      const symbol = row[x], p = { x, y };
      if (symbol === "S") spawn = p;
      else if (symbol === "E") exit = p;
      else if (/^[1-4]$/.test(symbol)) pads.push({ ...p, number: Number(symbol) });
      else if (symbol === "g") ghost = p;
      else if (symbol === "*") stars.push(p);
      else if (symbol === "O") portals.push(p);
    }
  });
  if (!spawn || !exit || (portals.length !== 0 && portals.length !== 2)) throw new Error(`Invalid chapter: ${chapter.name}`);
  pads.sort((a, b) => a.number - b.number);
  return { ...chapter, spawn, exit, ghost, pads, stars, portals, width: Math.max(...map.map(row => row.length)), height: map.length };
}

export function createGame(chapter) {
  const board = parseChapter(chapter);
  return { board, player: copy(board.spawn), tape: [copy(board.spawn)], echoes: [], beat: 0,
    collected: [], won: false, facing: { x: 1, y: 0 } };
}

export function cell(game, x, y) { return (game.board.map[y] || "")[x] || " "; }
export function echoAt(path, beat) { return path[Math.min(beat, path.length - 1)]; }
export function padActive(game, pad, beat = game.beat, candidate = game.player) {
  return same(pad, candidate) || game.echoes.some(path => same(pad, echoAt(path, beat)));
}
export function powered(game, count, beat = game.beat, candidate = game.player) {
  for (let number = 1; number <= count; number++) {
    const pad = game.board.pads.find(p => p.number === number);
    if (!pad || !padActive(game, pad, beat, candidate)) return false;
  }
  return true;
}
export function ghostActive(game, beat = game.beat) {
  return !game.board.ghost || game.echoes.some(path => same(game.board.ghost, echoAt(path, beat)));
}
export function shutterOpen(beat) { return beat % 4 < 2; }
export function exitReady(game, beat = game.beat, candidate = game.player, collected = game.collected) {
  return powered(game, game.board.pads.length, beat, candidate) && ghostActive(game, beat)
    && collected.length === game.board.stars.length;
}
export function requirements(game, beat = game.beat + 1, candidate = game.player) {
  const pads = game.board.pads.map(p => ({ number: p.number, active: padActive(game, p, beat, candidate) }));
  return { pads, ghost: ghostActive(game, beat), shutter: shutterOpen(beat),
    fragments: game.collected.length, totalFragments: game.board.stars.length };
}

function rewind(game) {
  game.player = copy(game.board.spawn);
  game.tape = [copy(game.board.spawn)];
  game.beat = 0;
  game.collected = [];
  game.facing = { x: 1, y: 0 };
}

export function move(game, dx, dy) {
  if (game.won) return { ok: false, message: "Chapter complete. Choose another chapter or replay." };
  if (game.beat >= game.board.beats) return { ok: false, message: "Tape full. Record or undo a beat." };
  const time = game.beat + 1;
  const entry = { x: game.player.x + dx, y: game.player.y + dy };
  const symbol = cell(game, entry.x, entry.y);
  if (symbol === "#" || symbol === " ") return { ok: false, message: "Solid wall. No beat spent." };
  let destination = entry;
  if (symbol === "O" && game.board.portals.length === 2) {
    destination = game.board.portals.find(p => !same(p, entry));
  }
  if (/^[A-D]$/.test(symbol) && !powered(game, symbol.charCodeAt(0) - 64, time, destination))
    return { ok: false, message: `Gate ${symbol} needs pads 1–${symbol.charCodeAt(0) - 64} on beat ${time}.` };
  if (symbol === "G" && !ghostActive(game, time))
    return { ok: false, message: `Gate G needs a recorded echo on g at beat ${time}.` };
  if (symbol === "|" && !shutterOpen(time))
    return { ok: false, message: `Shutter closed on beat ${time}. Wait to shift the rhythm.` };
  const collected = game.collected.slice();
  if (cell(game, destination.x, destination.y) === "*" && !collected.includes(key(destination)))
    collected.push(key(destination));
  if (symbol === "E" && !exitReady(game, time, destination, collected))
    return { ok: false, message: `Exit needs all ${game.board.pads.length} pads, ${game.board.stars.length} fragments${game.board.ghost ? ", and a ghost echo" : ""} on beat ${time}.` };
  game.player = copy(destination);
  game.tape.push(copy(destination));
  game.beat = time;
  game.collected = collected;
  if (dx || dy) game.facing = { x: dx, y: dy };
  if (symbol === "E") game.won = true;
  return { ok: true, won: game.won, portal: symbol === "O", message: game.won ? "Signal restored!" :
    game.beat === game.board.beats ? "Tape full. Record or undo a beat." : `Beat ${game.beat}/${game.board.beats}.` };
}

export function record(game) {
  if (game.won) return { ok: false, message: "Chapter complete." };
  if (!game.beat) return { ok: false, message: "Move or wait at least one beat before recording." };
  if (game.echoes.length >= MAX_ECHOES) return { ok: false, message: "Five echoes maximum. Erase the last echo to make room." };
  game.echoes.push(game.tape.map(copy));
  rewind(game);
  return { ok: true, message: `Echo ${game.echoes.length} recorded. It replays the path and holds its final square.` };
}

export function undo(game) {
  if (game.won) return { ok: false, message: "Chapter complete." };
  if (!game.beat) return { ok: false, message: "At beat zero. Erase Last removes a recorded echo." };
  game.tape.pop();
  game.beat--;
  game.player = copy(game.tape[game.beat]);
  game.collected = [...new Set(game.tape.filter(p => cell(game, p.x, p.y) === "*").map(key))];
  const prev = game.tape[Math.max(0, game.beat - 1)];
  game.facing = game.beat && !same(prev, game.player)
    ? { x: Math.sign(game.player.x - prev.x), y: Math.sign(game.player.y - prev.y) } : { x: 1, y: 0 };
  return { ok: true, message: `Undid a beat. Timeline at ${game.beat}/${game.board.beats}.` };
}

export function erase(game) {
  if (game.won) return { ok: false, message: "Chapter complete." };
  if (!game.echoes.length) return { ok: false, message: "No echo to erase." };
  game.echoes.pop();
  rewind(game);
  return { ok: true, message: "Last echo erased. Live timeline rewound." };
}

export function score(game) {
  const steps = game.echoes.reduce((n, path) => n + path.length - 1, game.beat);
  return Math.max(200, 1500 + game.board.stars.length * 130 - game.echoes.length * 140 - steps * 7);
}

export function snapshot(game) {
  return { chapter: game.board.name, echoes: game.echoes.map(path => path.map(copy)), tape: game.tape.map(copy), won: game.won };
}

export function restore(chapter, saved) {
  const game = createGame(chapter);
  if (!saved || saved.chapter !== chapter.name || !Array.isArray(saved.echoes) || !Array.isArray(saved.tape)
    || saved.echoes.length > MAX_ECHOES || typeof saved.won !== "boolean") return null;
  const validPath = (path) => Array.isArray(path) && path.length >= 1 && path.length <= game.board.beats + 1
    && path[0]?.x === game.board.spawn.x && path[0]?.y === game.board.spawn.y
    && path.every(p => Number.isInteger(p?.x) && Number.isInteger(p?.y) && !["#", " "].includes(cell(game, p.x, p.y)));
  if (![...saved.echoes, saved.tape].every(validPath)) return null;
  const replay = path => {
    for (let i = 1; i < path.length; i++) {
      const next = path[i];
      const direction = [[0,-1],[-1,0],[0,1],[1,0],[0,0]].find(([dx,dy]) => {
        const entry = { x: game.player.x + dx, y: game.player.y + dy };
        const destination = cell(game, entry.x, entry.y) === "O" && game.board.portals.length === 2
          ? game.board.portals.find(p => !same(p,entry)) : entry;
        return same(destination,next);
      });
      if (!direction || !move(game,...direction).ok || !same(game.player,next)) return false;
    }
    return true;
  };
  for (const path of saved.echoes) {
    if (!replay(path) || !record(game).ok) return null;
  }
  if (!replay(saved.tape) || game.won !== saved.won) return null;
  return game;
}
