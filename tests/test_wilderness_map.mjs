import assert from "node:assert/strict";
import {
  brushCells, cellAt, clampView, insideShape, legendEntries, normalizeRect, parseCell, shapeCount,
  terrainColor, toggleBrush,
} from "../stationnet/static/wilderness_map.mjs";

assert.deepEqual(parseCell(" 3, -4 ,0"), {x: 3, y: -4, z: 0});
assert.equal(parseCell("3,4"), null);
assert.deepEqual(normalizeRect({x: 5, y: -1}, {x: 2, y: 3}), {x1: 2, y1: -1, x2: 5, y2: 3});

const square = {x1: 0, y1: 0, x2: 9, y2: 9};
assert.equal(shapeCount("rect", square), 100);
// Matches mud_wilderness.shape_cells for the same corners (tested server-side with 10..14 x 10..12).
assert.equal(shapeCount("ellipse", {x1: 10, y1: 10, x2: 14, y2: 12}), 11);
assert.equal(insideShape("ellipse", square, 0, 0), false);
assert.equal(insideShape("ellipse", square, 5, 5), true);
assert.equal(insideShape("rect", square, 10, 0), false);

const brush = new Set();
toggleBrush(brush, 1, 2, true);
toggleBrush(brush, 3, 4, true);
toggleBrush(brush, 1, 2, false);
assert.deepEqual(brushCells(brush), [[3, 4]]);
assert.equal(shapeCount("cells", square, brush), 1);

const view = {origin: [-5, 10], width: 3, height: 2, grid: [[0, 2, 2], [1, 0, 0]],
              palette: [{key: "", name: "Nothing"}, {key: "*", name: "Landmark"}, {key: "forest", name: "Forest", glyph: "t"}]};
assert.deepEqual(cellAt(view, 15, 15, 10), {x: -4, y: 11});
assert.equal(cellAt(view, 31, 5, 10), null);
assert.deepEqual(legendEntries(view).map(entry => entry.key), ["*", "forest"]);
assert.equal(clampView(500, 101), 101);
assert.equal(clampView(1, 101), 5);
assert.equal(terrainColor("lava"), terrainColor("custom"));
console.log("wilderness map helpers ok");
