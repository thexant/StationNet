"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function main() {
  const source = fs.readFileSync(path.join(__dirname, "../stationnet/application_packages/light-cycles/engine.js"), "utf8");
  const { createRound, queueDirection, step, chooseComputerDirection } = await import(`data:text/javascript,${encodeURIComponent(source)}`);
  const fresh = (count = 2) => {
    const round = createRound(count, "compact");
    round.occupied.fill(0);
    return round;
  };
  const place = (round, id, x, y, direction) => {
    const rider = round.riders[id];
    Object.assign(rider, { x, y, direction, queued: direction, alive: true });
    round.occupied[y * round.width + x] = id + 1;
  };

  {
    const round = fresh(); place(round, 0, 0, 5, 2); place(round, 1, 10, 5, 0);
    const result = step(round);
    assert.deepEqual(result.eliminated, [0]);
    assert.equal(result.winner, 1);
  }
  {
    const round = fresh(); place(round, 0, 4, 5, 0); place(round, 1, 10, 5, 0);
    round.occupied[5 * round.width + 5] = 2;
    assert.deepEqual(step(round).eliminated, [0]);
  }
  {
    const round = fresh(); place(round, 0, 4, 5, 0); place(round, 1, 6, 5, 2);
    const result = step(round);
    assert.deepEqual(result.eliminated, [0, 1]);
    assert.equal(result.winner, null);
    assert.equal(round.occupied[5 * round.width + 5], 0);
  }
  {
    const round = fresh(); place(round, 0, 4, 5, 0); place(round, 1, 5, 5, 2);
    assert.deepEqual(step(round).eliminated, [0, 1]); // swapping occupied heads is a crash
  }
  {
    const round = fresh(); place(round, 0, 4, 5, 0); place(round, 1, 10, 5, 0);
    assert.equal(queueDirection(round, 0, 2), false);
    assert.equal(queueDirection(round, 0, 3), true);
    step(round);
    assert.equal(round.riders[0].y, 4);
  }
  {
    const round = fresh(1); place(round, 0, 0, 5, 2);
    const result = step(round);
    assert.equal(result.over, true);
    assert.equal(result.winner, null);
  }
  {
    const round = fresh(); place(round, 0, 1, 5, 2); place(round, 1, 10, 5, 0);
    round.ticks = 189; // the first closing wall becomes solid at tick 190
    assert.deepEqual(step(round).eliminated, [0]);
  }
  {
    const round = fresh(); place(round, 0, 4, 5, 0); place(round, 1, 10, 5, 0);
    round.occupied[5 * round.width + 5] = 2;
    const choice = chooseComputerDirection(round, 0, () => .5);
    assert.ok(choice === 1 || choice === 3);
  }

  let seed = 124331;
  const random = () => { seed = (1664525 * seed + 1013904223) >>> 0; return seed / 4294967296; };
  const wins = [0, 0, 0, 0];
  let shortest = Infinity, longest = 0, draws = 0;
  for (let match = 0; match < 120; match += 1) {
    const round = createRound(4, ["compact", "standard", "wide"][match % 3]);
    while (!round.over && round.ticks < 5000) {
      for (let id = 0; id < 4; id += 1) queueDirection(round, id, chooseComputerDirection(round, id, random));
      step(round);
    }
    assert.ok(round.over, "CPU round terminates");
    shortest = Math.min(shortest, round.ticks); longest = Math.max(longest, round.ticks);
    if (round.winner === null) draws += 1; else wins[round.winner] += 1;
  }
  assert.ok(shortest >= 8, `round too short: ${shortest}`);
  assert.ok(longest <= 500, `round too long: ${longest}`);
  assert.ok(wins.every((winsForRider) => winsForRider >= 8), `spawn imbalance: ${wins}`);
  assert.ok(draws < 60, `too many draws: ${draws}`);

  // Exercise the packaged entrypoint against the same shape of UI methods the host exposes.
  const surface = new Map(), buffers = new Set();
  let rendered = [];
  let startup, animate, onKey;
  const component = (type) => (...args) => ({ type, args, id: args.at(-1)?.id });
  const ui = {
    modules: { load: async () => ({ createRound, queueDirection, step, chooseComputerDirection,
      safeInset: (ticks) => Math.max(0, Math.floor((ticks - 180) / 10)),
      ARENAS: { compact: [40, 26], standard: [56, 36], wide: [72, 46] } }) },
    storage: { get: async () => null, set: async () => true },
    scores: { leaderboard: async () => ({ leaderboard: [] }), submit: async () => ({}) },
    sound: { playTone() {}, play() {}, sequence() {} },
    graphics: {
      framebuffer(id, width, height) {
        assert.ok(!buffers.has(id), `duplicate framebuffer ${id}`); buffers.add(id);
        return { pixels: new Uint8Array(width * height), palette: new Uint8Array(1024), present() {} };
      },
      frame() { return { type: "frame" }; }
    },
    render(...items) { rendered = items; surface.clear(); items.forEach((item) => { if (item.id) surface.set(item.id, item); }); },
    update(id, item) { assert.ok(surface.has(id), `missing component ${id}`); surface.set(id, item); },
    onKey(handler) { onKey = handler; }, onVisibility() {}, animate(handler) { animate = handler; },
    title: component("title"), text: component("text"), select: component("select"),
    button: component("button"), menu: component("menu"), canvas: component("canvas"),
    heading: component("heading"), table: component("table")
  };
  const script = fs.readFileSync(path.join(__dirname, "../stationnet/application_packages/light-cycles/main.js"), "utf8");
  vm.runInNewContext(script, { StationNet: { start(callback) { startup = callback(ui); } }, Uint8Array, Math, Promise });
  await startup;
  assert.equal(surface.get("summary").args[0].includes("4 riders"), true);
  surface.get("human-setting").args.at(-1).onChange({ value: "2" });
  assert.equal(surface.get("summary").args[0].includes("2 local"), true);
  const startButton = rendered.find((item) => item.type === "button");
  startButton.args[1]();
  assert.equal(surface.get("arena").type, "canvas");
  surface.get("action").args[1]();
  onKey({ code: "ArrowUp", phase: "down", repeat: false });
  for (let i = 0; i < 30; i += 1) animate({ delta: .15 });
  assert.ok(surface.get("arena").args[2].length < 600);
  assert.ok(buffers.has("light-cycles-standard"));
  console.log(`Light Cycles: collisions passed; 120 CPU rounds, wins ${wins.join("/")}, draws ${draws}, length ${shortest}-${longest} ticks.`);
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
