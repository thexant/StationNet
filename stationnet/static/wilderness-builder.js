import {
  MAX_FILL_CELLS, brushCells, cellAt, clampView, insideShape, legendEntries, normalizeRect, parseCell,
  shapeCount, terrainColor, toggleBrush,
} from "./wilderness_map.mjs";

const root = document.querySelector("[data-wilderness-map]");
const form = document.querySelector("[data-fill-form]");

if (root && form) {
  root.hidden = false;
  const canvas = root.querySelector("[data-map-canvas]");
  const context = canvas.getContext("2d");
  const status = root.querySelector("[data-map-status]");
  const legend = root.querySelector("[data-map-legend]");
  const summary = form.querySelector("[data-shape-summary]");
  const kindField = form.querySelector("[data-shape-kind]");
  const cellsField = form.querySelector("[data-shape-cells]");
  const fields = Object.fromEntries([...form.querySelectorAll("[data-shape-field]")]
    .map(input => [input.dataset.shapeField, input]));
  const maximum = Number(root.dataset.max) || 101;
  const selected = parseCell(root.dataset.selected);
  const state = {
    x: Number(root.dataset.x) || 0, y: Number(root.dataset.y) || 0, z: Number(root.dataset.z) || 0,
    width: Number(root.dataset.width) || 41, height: Number(root.dataset.height) || 25,
    tool: "inspect", corner: null, brush: new Set(), painting: null, view: null, size: 14, request: 0,
  };
  const style = getComputedStyle(document.body);
  const ink = style.getPropertyValue("--white").trim() || "#ddd";
  const empty = style.getPropertyValue("--surface-field").trim() || "#000";

  function rect() {
    const value = name => Number(fields[name].value);
    return normalizeRect({x: value("x1"), y: value("y1")}, {x: value("x2"), y: value("y2")});
  }

  function selection() {
    return {kind: kindField.value, rect: rect(), z: Number(fields.z.value)};
  }

  function updateSummary() {
    const {kind, rect: area} = selection();
    const count = shapeCount(kind, area, state.brush);
    summary.textContent = `${count.toLocaleString()} squares selected` +
      (count > MAX_FILL_CELLS ? ` (more than the ${MAX_FILL_CELLS.toLocaleString()} allowed at once)` : "");
  }

  function inSelection(x, y) {
    const {kind, rect: area, z} = selection();
    if (z !== state.z) return false;
    return kind === "cells" ? state.brush.has(`${x},${y}`) : insideShape(kind, area, x, y);
  }

  function draw() {
    const view = state.view;
    if (!view) return;
    const available = Math.max(200, root.clientWidth - 4);
    state.size = Math.max(4, Math.min(18, Math.floor(available / view.width)));
    const size = state.size;
    canvas.width = view.width * size;
    canvas.height = view.height * size;
    context.font = `${Math.floor(size * 0.8)}px monospace`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    view.grid.forEach((row, rowIndex) => row.forEach((index, column) => {
      const left = column * size, top = rowIndex * size;
      const entry = view.palette[index];
      context.fillStyle = index === 0 ? empty : index === 1 ? "#c9b840" : terrainColor(entry.terrain);
      context.fillRect(left, top, size, size);
      if (index > 0 && size >= 10) {
        context.fillStyle = "#000";
        context.fillText(entry.glyph, left + size / 2, top + size / 2 + 1);
      }
      if (inSelection(view.origin[0] + column, view.origin[1] + rowIndex)) {
        context.fillStyle = "rgba(255,255,255,0.35)";
        context.fillRect(left, top, size, size);
      }
    }));
    const mark = (points, color) => points.forEach(([x, y]) => {
      context.fillStyle = color;
      context.beginPath();
      context.arc((x - view.origin[0] + 0.5) * size, (y - view.origin[1] + 0.5) * size, Math.max(2, size / 5), 0, 7);
      context.fill();
    });
    mark(view.npcs, "#e04040");
    mark(view.players, "#ffffff");
    if (selected && selected.z === state.z) {
      context.strokeStyle = ink;
      context.lineWidth = 2;
      context.strokeRect((selected.x - view.origin[0]) * size + 1, (selected.y - view.origin[1]) * size + 1, size - 2, size - 2);
    }
    legend.replaceChildren(...legendEntries(view).map(entry => {
      const item = document.createElement("span");
      item.textContent = `${entry.glyph} ${entry.name}   `;
      return item;
    }));
  }

  async function load() {
    const ticket = ++state.request;
    const query = new URLSearchParams({x: state.x, y: state.y, z: state.z, w: state.width, h: state.height});
    status.textContent = "Loading map…";
    try {
      const response = await fetch(`${root.dataset.mapUrl}?${query}`, {headers: {Accept: "application/json"}});
      const data = await response.json();
      if (ticket !== state.request) return;
      if (!response.ok) throw new Error(data.error || "The map could not be loaded.");
      state.view = data;
      status.textContent = `Centre ${state.x}, ${state.y}, layer ${state.z}. Hover a square for details.`;
      draw();
    } catch (problem) {
      status.textContent = problem.message;
    }
  }

  function describe(point) {
    const view = state.view;
    const index = view.grid[point.y - view.origin[1]][point.x - view.origin[0]];
    const landmark = view.authored.find(entry => entry.x === point.x && entry.y === point.y);
    const label = landmark ? landmark.name : index ? view.palette[index].name : "empty";
    return `${point.x}, ${point.y}, layer ${state.z}: ${label}`;
  }

  function setRect(a, b) {
    const area = normalizeRect(a, b);
    for (const name of ["x1", "y1", "x2", "y2"]) fields[name].value = area[name];
    fields.z.value = state.z;
    kindField.value = state.tool;
    updateSummary();
    draw();
  }

  function pointFrom(event) {
    const bounds = canvas.getBoundingClientRect();
    const scale = canvas.width / bounds.width;
    return state.view && cellAt(state.view, (event.clientX - bounds.left) * scale,
                                (event.clientY - bounds.top) * scale, state.size);
  }

  canvas.addEventListener("pointermove", event => {
    const point = pointFrom(event);
    if (!point) return;
    status.textContent = describe(point);
    if (state.painting !== null) {
      toggleBrush(state.brush, point.x, point.y, state.painting);
      cellsField.value = JSON.stringify(brushCells(state.brush));
      updateSummary();
      draw();
    }
  });

  canvas.addEventListener("pointerdown", event => {
    const point = pointFrom(event);
    if (!point) return;
    if (state.tool === "inspect") {
      window.location.href = `${root.dataset.pageUrl}?cell=${point.x},${point.y},${state.z}#cell-inspector`;
    } else if (state.tool === "brush") {
      if (kindField.value !== "cells" || Number(fields.z.value) !== state.z) state.brush.clear();
      kindField.value = "cells";
      fields.z.value = state.z;
      state.painting = !state.brush.has(`${point.x},${point.y}`);
      toggleBrush(state.brush, point.x, point.y, state.painting);
      cellsField.value = JSON.stringify(brushCells(state.brush));
      canvas.setPointerCapture(event.pointerId);
      updateSummary();
      draw();
    } else if (!state.corner) {
      state.corner = point;
      status.textContent = `First corner ${point.x}, ${point.y}. Click the opposite corner.`;
    } else {
      setRect(state.corner, point);
      state.corner = null;
    }
  });

  const stopPainting = () => { state.painting = null; };
  canvas.addEventListener("pointerup", stopPainting);
  canvas.addEventListener("pointercancel", stopPainting);

  root.querySelectorAll("input[name=map_tool]").forEach(input => input.addEventListener("change", () => {
    state.tool = input.value;
    state.corner = null;
  }));

  root.querySelectorAll("[data-pan]").forEach(button => button.addEventListener("click", () => {
    const [dx, dy, dz] = button.dataset.pan.split(",").map(Number);
    state.x += dx * Math.max(1, Math.round(state.width / 41));
    state.y += dy * Math.max(1, Math.round(state.height / 25));
    state.z += dz;
    load();
  }));

  root.querySelectorAll("[data-zoom]").forEach(button => button.addEventListener("click", () => {
    const factor = button.dataset.zoom === "1" ? 1.5 : 1 / 1.5;
    state.width = clampView(Math.round(state.width * factor) | 1, maximum);
    state.height = clampView(Math.round(state.height * factor) | 1, maximum);
    load();
  }));

  root.querySelector("[data-clear-selection]").addEventListener("click", () => {
    state.brush.clear();
    cellsField.value = "";
    state.corner = null;
    if (kindField.value === "cells") kindField.value = "rect";
    updateSummary();
    draw();
  });

  const goto = document.querySelector("[data-map-goto]");
  goto?.addEventListener("submit", event => {
    event.preventDefault();
    const data = new FormData(goto);
    state.x = Number(data.get("x")) || 0;
    state.y = Number(data.get("y")) || 0;
    state.z = Number(data.get("z")) || 0;
    load();
  });

  [kindField, ...Object.values(fields)].forEach(input => input.addEventListener("input", () => {
    updateSummary();
    draw();
  }));

  form.addEventListener("submit", event => {
    const clearing = event.submitter?.formAction?.endsWith("/clear");
    const count = shapeCount(kindField.value, rect(), state.brush);
    if (!clearing && count > 10000 &&
        !window.confirm(`Fill ${count.toLocaleString()} squares? Large fills can take a few seconds.`)) {
      event.preventDefault();
    }
    if (clearing && !window.confirm("Remove every wilderness room in the selected area?")) event.preventDefault();
  });

  window.addEventListener("resize", draw);
  updateSummary();
  load();
}
