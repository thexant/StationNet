import assert from "node:assert/strict";
import { createRound, queueDirection, step, chooseComputerDirection, safeInset } from "../stationnet/application_packages/light-cycles/engine.js";

const round = createRound(2, "compact");
assert.equal(round.riders[0].path.length, 1, "round starts with a trail origin");
assert.equal(queueDirection(round, 0, 2), false, "reverse turns are rejected");
assert.equal(queueDirection(round, 0, 1), true, "a perpendicular turn is accepted");
step(round);
assert.equal(round.riders[0].direction, 1, "queued turns apply on the next fixed step");
assert.deepEqual(round.riders[0].path.at(-1), [round.riders[0].x, round.riders[0].y], "trail follows every accepted step");

const wall = createRound(1, "compact");
wall.riders[0].x = wall.width - 1;
wall.occupied.fill(0); wall.occupied[wall.riders[0].y * wall.width + wall.riders[0].x] = 1;
const crash = step(wall);
assert.deepEqual(crash.eliminated, [0], "border collision eliminates precisely at the crossed cell");
assert.equal(crash.over, true, "solo round ends on collision");

const cpu = createRound(2, "compact");
const selected = chooseComputerDirection(cpu, 1, () => .5, "elite");
assert.ok([0, 1, 2, 3].includes(selected), "CPU selects a valid direction");
assert.notEqual(selected, (cpu.riders[1].direction + 2) % 4, "CPU never reverses");
assert.equal(safeInset(260), 0); assert.equal(safeInset(274), 1);

console.log("Light Cycles engine checks passed");
