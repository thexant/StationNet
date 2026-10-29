import assert from "node:assert/strict";
import { chapters } from "../stationnet/application_packages/echo-choir/levels.js";
import * as gameRules from "../stationnet/application_packages/echo-choir/engine.js";

function shortestPath(game, target, final = false) {
  const board = game.board, stars = board.stars;
  const moves = [[0,-1],[1,0],[0,1],[-1,0],[0,0]];
  const queue = [{ x: board.spawn.x, y: board.spawn.y, beat: 0, mask: 0, path: [] }];
  const seen = new Set([queue[0].x + "," + queue[0].y + ",0,0"]);
  for (let i = 0; i < queue.length; i++) {
    const state = queue[i];
    if (state.x === target.x && state.y === target.y &&
        (!final || state.mask === (1 << stars.length) - 1)) return state.path;
    if (state.beat >= board.beats) continue;
    const beat = state.beat + 1;
    for (const [dx,dy] of moves) {
      const entry = { x: state.x + dx, y: state.y + dy };
      const tile = gameRules.cell(game,entry.x,entry.y);
      if (tile === "#" || tile === " ") continue;
      let dest = entry;
      if (tile === "O") dest = board.portals.find(p => p.x !== entry.x || p.y !== entry.y);
      if (/^[A-D]$/.test(tile) && !gameRules.powered(game,tile.charCodeAt(0)-64,beat,dest)) continue;
      if (tile === "G" && !gameRules.ghostActive(game,beat)) continue;
      if (tile === "|" && !gameRules.shutterOpen(beat)) continue;
      let mask = state.mask;
      const star = stars.findIndex(p => p.x === dest.x && p.y === dest.y);
      if (star >= 0) mask |= 1 << star;
      if (tile === "E" && (!final || !gameRules.exitReady(game,beat,dest,
        stars.filter((_,n) => mask & (1 << n)).map(p => p.x + "," + p.y)))) continue;
      const id = dest.x + "," + dest.y + "," + beat + "," + mask;
      if (seen.has(id)) continue;
      seen.add(id);
      queue.push({ x: dest.x, y: dest.y, beat, mask, path: [...state.path,[dx,dy]] });
    }
  }
  return null;
}

function solve(chapter) {
  const board = gameRules.parseChapter(chapter);
  const targets = [...board.pads, ...(board.ghost ? [board.ghost] : [])];
  const visited = new Set();
  function search(echoes, remaining) {
    const game = gameRules.createGame(chapter);
    game.echoes = echoes.map(path => path.map(p => ({...p})));
    if (!remaining.length) {
      const path = shortestPath(game,board.exit,true);
      if (!path) return null;
      for (const [dx,dy] of path) assert.equal(gameRules.move(game,dx,dy).ok,true);
      if (!game.won) return null;
      return game;
    }
    const signature = remaining.map(p => p.x + "," + p.y).join("|") + ":" +
      echoes.map(path => path.length).join(",");
    if (visited.has(signature)) return null;
    visited.add(signature);
    for (let i = 0; i < remaining.length; i++) {
      const target = remaining[i];
      const path = shortestPath(game,target);
      if (!path?.length) continue;
      const recording = gameRules.createGame(chapter);
      recording.echoes = echoes.map(existing => existing.map(p => ({...p})));
      for (const [dx,dy] of path) assert.equal(gameRules.move(recording,dx,dy).ok,true);
      assert.equal(gameRules.record(recording).ok,true);
      const result = search(recording.echoes,remaining.filter((_,j)=>j!==i));
      if (result) return result;
    }
    return null;
  }
  return search([],targets);
}

assert.equal(chapters.length,19);
for (const [index,chapter] of chapters.entries()) {
  const result = solve(chapter);
  assert.ok(result,`Chapter ${index+1} ${chapter.name} needs a valid complete route`);
  assert.ok(result.beat <= chapter.beats);
}

const first = gameRules.createGame(chapters[0]);
assert.equal(gameRules.move(first,1,0).ok,true);
assert.equal(gameRules.undo(first).ok,true);
assert.equal(first.beat,0);
assert.equal(gameRules.record(first).ok,false);
assert.equal(gameRules.move(first,0,0).ok,true);
assert.equal(gameRules.record(first).ok,true);
assert.equal(first.echoes.length,1);
assert.equal(gameRules.erase(first).ok,true);
assert.equal(first.echoes.length,0);

const filled = gameRules.createGame(chapters[0]);
for (let i=0;i<filled.board.beats;i++) assert.equal(gameRules.move(filled,0,0).ok,true);
assert.equal(filled.beat,filled.board.beats);
assert.equal(filled.echoes.length,0,"Full tapes must wait for an explicit Record");
assert.equal(gameRules.undo(filled).ok,true);

const save = gameRules.snapshot(first);
const restored = gameRules.restore(chapters[0],save);
assert.deepEqual(gameRules.snapshot(restored),save);
assert.equal(gameRules.restore(chapters[0],{...save,tape:[{x:-1,y:-1}]}),null);

const scored = solve(chapters[0]);
const value = gameRules.score(scored);
const erased = gameRules.createGame(chapters[0]);
gameRules.move(erased,0,0); gameRules.record(erased); gameRules.erase(erased);
erased.echoes = scored.echoes; erased.tape = scored.tape; erased.beat = scored.beat;
assert.equal(gameRules.score(erased),value,"Erased recordings cannot reduce a score");

console.log("Echo Choir: 19 full chapter routes and rule checks passed");
