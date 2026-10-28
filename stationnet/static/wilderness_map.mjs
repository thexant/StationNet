// Pure helpers for the wilderness builder map. The server validates and applies every change.
export const MAX_FILL_CELLS = 250000;

const TERRAIN_COLORS = {
  grassland: "#6f9a3a", forest: "#2f6b2f", "dense-forest": "#1d4a24", hills: "#8a7a3e",
  mountains: "#7c7c86", desert: "#c8a85a", swamp: "#4a5f3a", tundra: "#a8b8c0",
  water: "#2f5f9a", road: "#9a7a5a", custom: "#6a5a8a",
};

export function terrainColor(terrain) {
  return TERRAIN_COLORS[terrain] || TERRAIN_COLORS.custom;
}

export function parseCell(text) {
  const match = /^\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*$/.exec(String(text || ""));
  return match ? {x: Number(match[1]), y: Number(match[2]), z: Number(match[3])} : null;
}

export function cellKey(x, y) {
  return `${x},${y}`;
}

export function normalizeRect(a, b) {
  return {x1: Math.min(a.x, b.x), y1: Math.min(a.y, b.y), x2: Math.max(a.x, b.x), y2: Math.max(a.y, b.y)};
}

// Same membership test as mud_wilderness.shape_cells, so previews match what the server fills.
export function insideShape(kind, rect, x, y) {
  if (x < rect.x1 || x > rect.x2 || y < rect.y1 || y > rect.y2) return false;
  if (kind !== "ellipse") return true;
  const cx = (rect.x1 + rect.x2) / 2, cy = (rect.y1 + rect.y2) / 2;
  const rx = (rect.x2 - rect.x1) / 2 + 0.5, ry = (rect.y2 - rect.y1) / 2 + 0.5;
  return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1;
}

export function shapeCount(kind, rect, brush = new Set()) {
  if (kind === "cells") return brush.size;
  if (kind === "rect") return (rect.x2 - rect.x1 + 1) * (rect.y2 - rect.y1 + 1);
  let count = 0;
  for (let y = rect.y1; y <= rect.y2; y += 1) {
    for (let x = rect.x1; x <= rect.x2; x += 1) if (insideShape(kind, rect, x, y)) count += 1;
  }
  return count;
}

export function brushCells(brush) {
  return [...brush].map(key => key.split(",").map(Number));
}

export function toggleBrush(brush, x, y, adding) {
  const key = cellKey(x, y);
  if (adding) brush.add(key); else brush.delete(key);
  return brush;
}

// Map a pointer position on the canvas to world coordinates.
export function cellAt(view, offsetX, offsetY, size) {
  const column = Math.floor(offsetX / size), row = Math.floor(offsetY / size);
  if (column < 0 || row < 0 || column >= view.width || row >= view.height) return null;
  return {x: view.origin[0] + column, y: view.origin[1] + row};
}

export function legendEntries(view) {
  const used = new Set(view.grid.flat());
  return view.palette
    .map((entry, index) => ({...entry, index}))
    .filter(entry => entry.index > 0 && used.has(entry.index));
}

export function clampView(value, maximum) {
  return Math.max(5, Math.min(maximum, value | 0));
}
