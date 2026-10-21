"use strict";

StationNet.start(async (ui) => {
  const MIN_WIDTH = 4, MIN_HEIGHT = 4, MAX_WIDTH = 60, MAX_HEIGHT = 30, HISTORY_LIMIT = 40;
  const COLORS = ["Black", "Red", "Green", "Yellow", "Blue", "Magenta", "Cyan", "White", "Bright Black", "Bright Red", "Bright Green", "Bright Yellow", "Bright Blue", "Bright Magenta", "Bright Cyan", "Bright White"];
  const CHARACTERS = ["#", "@", "*", "+", "-", "=", "|", "/", "\\", ".", ":", "_", "~", "^", "░", "▒", "▓", "█", "│", "─", "┌", "┐", "└", "┘", "├", "┤", "┬", "┴", "┼", " "];
  let width = 32, height = 16, cells = blankCells(width, height);
  let cursor = { x: 0, y: 0 }, selectedChar = "#", foreground = 7, background = 0, tool = "pencil";
  let undoStack = [], redoStack = [], lineStart = null, dirty = false, projectName = "Untitled", selectedProjectId = "";
  let projects = [], confirmation = "", status = "Ready. Click or drag on the canvas; arrow keys move the cursor.";
  let canvasComponent;

  function blankCell() { return { char: " ", fg: 7, bg: 0 }; }
  function blankCells(w, h) { return Array.from({ length: h }, () => Array.from({ length: w }, blankCell)); }
  function cloneCells(source = cells) { return source.map((row) => row.map((cell) => ({ char: cell.char, fg: cell.fg, bg: cell.bg }))); }
  function snapshot() { return { width, height, cells: cloneCells(), cursor: { ...cursor } }; }
  function restore(state) { width = state.width; height = state.height; cells = cloneCells(state.cells); cursor = { ...state.cursor }; lineStart = null; dirty = true; }
  function remember() { undoStack.push(snapshot()); if (undoStack.length > HISTORY_LIMIT) undoStack.shift(); redoStack = []; }
  function clamp(value, low, high) { return Math.max(low, Math.min(high, Number(value) || low)); }
  function oneCharacter(value) { return Array.from(String(value || " "))[0] || " "; }
  function currentCell() { return tool === "erase" ? blankCell() : { char: selectedChar, fg: foreground, bg: background }; }
  function cellRows() { return cells.map((row) => row.map((cell) => ({ char: cell.char, fg: cell.fg, bg: cell.bg }))); }
  function setStatus(message) { status = message; ui.update("studio-status", ui.text(status, { id: "studio-status", tone: "accent" })); }
  function updateInfo() {
    const pending = lineStart ? ` / LINE START ${lineStart.x + 1},${lineStart.y + 1}` : "";
    ui.update("canvas-info", ui.text(`${width}x${height} / CURSOR ${cursor.x + 1},${cursor.y + 1} / TOOL ${tool.toUpperCase()} / CHAR ${selectedChar === " " ? "SPACE" : selectedChar}${pending}${dirty ? " / UNSAVED" : ""}`, { id: "canvas-info", tone: "muted" }));
  }
  function refreshCanvas(message) {
    canvasComponent.rows = cellRows(); canvasComponent.cursor = { ...cursor };
    canvasComponent.brush = tool === "pencil" || tool === "erase" ? currentCell() : null;
    ui.update("art-canvas", canvasComponent); updateInfo();
    if (message) setStatus(message);
  }
  function confirm(action, message) {
    if (confirmation !== action) { confirmation = action; setStatus(`${message} Select the same action again to confirm.`); return false; }
    confirmation = ""; return true;
  }
  function cancelConfirmation() { confirmation = ""; }

  function paintAt(x, y, drag, deferRefresh = false) {
    x = Math.trunc(x); y = Math.trunc(y);
    if (x < 0 || y < 0 || x >= width || y >= height) return;
    cursor = { x, y }; cancelConfirmation();
    if (tool === "line") {
      if (drag) return;
      if (!lineStart) { lineStart = { x, y }; refreshCanvas("Line start selected. Choose the end cell."); return; }
      remember(); drawLine(lineStart.x, lineStart.y, x, y, currentCell()); lineStart = null; dirty = true; refreshCanvas("Line drawn."); return;
    }
    if (tool === "fill") {
      if (drag) return;
      const replacement = currentCell(), target = cells[y][x];
      if (sameCell(target, replacement)) { refreshCanvas(); return; }
      remember(); floodFill(x, y, target, replacement); dirty = true; refreshCanvas("Area filled."); return;
    }
    if (!drag) remember();
    const replacement = currentCell();
    if (!sameCell(cells[y][x], replacement)) { cells[y][x] = replacement; dirty = true; }
    if (!deferRefresh) refreshCanvas();
  }
  function sameCell(a, b) { return a.char === b.char && a.fg === b.fg && a.bg === b.bg; }
  function drawLine(x0, y0, x1, y1, value) {
    let dx = Math.abs(x1 - x0), sx = x0 < x1 ? 1 : -1, dy = -Math.abs(y1 - y0), sy = y0 < y1 ? 1 : -1, error = dx + dy;
    while (true) {
      cells[y0][x0] = { ...value };
      if (x0 === x1 && y0 === y1) break;
      const twice = 2 * error;
      if (twice >= dy) { error += dy; x0 += sx; }
      if (twice <= dx) { error += dx; y0 += sy; }
    }
  }
  function floodFill(startX, startY, target, replacement) {
    const queue = [[startX, startY]], seen = new Set();
    while (queue.length) {
      const [x, y] = queue.pop(), key = `${x}:${y}`;
      if (x < 0 || y < 0 || x >= width || y >= height || seen.has(key) || !sameCell(cells[y][x], target)) continue;
      seen.add(key); cells[y][x] = { ...replacement };
      queue.push([x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]);
    }
  }
  function onCell(event) {
    if (event.phase === "end") { refreshCanvas(); return; }
    paintAt(event.x, event.y, event.drag, event.phase === "start" || event.phase === "move");
  }

  function undo() {
    if (!undoStack.length) return setStatus("Nothing to undo.");
    redoStack.push(snapshot()); restore(undoStack.pop()); refreshCanvas("Undid the last edit.");
  }
  function redo() {
    if (!redoStack.length) return setStatus("Nothing to redo.");
    undoStack.push(snapshot()); restore(redoStack.pop()); refreshCanvas("Redid the last edit.");
  }
  function clearCanvas() {
    if (!confirm("clear", "Clear the entire canvas?")) return;
    remember(); cells = blankCells(width, height); cursor = { x: 0, y: 0 }; dirty = true; refreshCanvas("Canvas cleared.");
  }
  function resizeCanvas(event) {
    const nextWidth = Math.trunc(clamp(event.values.canvasWidth, MIN_WIDTH, MAX_WIDTH));
    const nextHeight = Math.trunc(clamp(event.values.canvasHeight, MIN_HEIGHT, MAX_HEIGHT));
    if (nextWidth === width && nextHeight === height) return setStatus("Canvas dimensions are unchanged.");
    remember(); const resized = blankCells(nextWidth, nextHeight);
    for (let y = 0; y < Math.min(height, nextHeight); y += 1) for (let x = 0; x < Math.min(width, nextWidth); x += 1) resized[y][x] = { ...cells[y][x] };
    width = nextWidth; height = nextHeight; cells = resized; cursor = { x: Math.min(cursor.x, width - 1), y: Math.min(cursor.y, height - 1) }; dirty = true;
    renderMain(`Canvas resized to ${width}x${height}. Content outside the new bounds was clipped.`);
  }
  function newProject() {
    if (dirty && !confirm("new", "Start a new project and discard unsaved canvas changes?")) return;
    width = 32; height = 16; cells = blankCells(width, height); cursor = { x: 0, y: 0 }; projectName = "Untitled"; selectedProjectId = "";
    undoStack = []; redoStack = []; lineStart = null; dirty = false; confirmation = ""; renderMain("New project started. Saved projects were not changed.");
  }

  function projectPayload() {
    return { format: 1, name: projectName, width, height, chars: cells.map((row) => row.map((cell) => cell.char).join("")), fg: cells.map((row) => row.map((cell) => cell.fg)), bg: cells.map((row) => row.map((cell) => cell.bg)) };
  }
  function projectId(name) {
    const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 45) || "project";
    let id = base, suffix = 2;
    while (projects.some((item) => item.id === id && item.name.toLowerCase() !== name.toLowerCase())) id = `${base}-${suffix++}`;
    return id;
  }
  async function saveProject(event) {
    const name = String(event.values.projectName || projectName).trim().slice(0, 60);
    if (!name) return setStatus("Enter a project name before saving.");
    projectName = name;
    const existing = projects.find((item) => item.name.toLowerCase() === name.toLowerCase());
    const id = existing ? existing.id : projectId(name);
    try {
      await ui.storage.set(`project:${id}`, projectPayload());
      const entry = { id, name, updated: new Date().toISOString() };
      projects = [entry, ...projects.filter((item) => item.id !== id)];
      await ui.storage.set("projects", projects); selectedProjectId = id; dirty = false; renderMain(`Project “${name}” saved.`);
    } catch (error) { setStatus(`Save failed: ${error.message}`); }
  }
  function validProject(data) {
    return data && data.format === 1 && Number.isInteger(data.width) && Number.isInteger(data.height) && data.width >= MIN_WIDTH && data.width <= MAX_WIDTH && data.height >= MIN_HEIGHT && data.height <= MAX_HEIGHT && Array.isArray(data.chars) && Array.isArray(data.fg) && Array.isArray(data.bg);
  }
  async function loadProject() {
    if (!selectedProjectId) return setStatus("Choose a saved project first.");
    if (dirty && !confirm("load", "Load the selected project and discard unsaved canvas changes?")) return;
    try {
      const data = await ui.storage.get(`project:${selectedProjectId}`);
      if (!validProject(data)) throw new Error("Saved project data is missing or invalid");
      width = data.width; height = data.height; cells = blankCells(width, height);
      for (let y = 0; y < height; y += 1) {
        const characters = Array.from(String(data.chars[y] || ""));
        for (let x = 0; x < width; x += 1) cells[y][x] = { char: oneCharacter(characters[x]), fg: Math.trunc(clamp(data.fg[y] && data.fg[y][x], 0, 15)), bg: Math.trunc(clamp(data.bg[y] && data.bg[y][x], 0, 15)) };
      }
      const entry = projects.find((item) => item.id === selectedProjectId); projectName = data.name || (entry && entry.name) || "Untitled";
      cursor = { x: 0, y: 0 }; undoStack = []; redoStack = []; lineStart = null; dirty = false; confirmation = ""; renderMain(`Project “${projectName}” loaded.`);
    } catch (error) { setStatus(`Load failed: ${error.message}`); }
  }
  async function deleteProject() {
    if (!selectedProjectId) return setStatus("Choose a saved project first.");
    const entry = projects.find((item) => item.id === selectedProjectId);
    if (!confirm("delete", `Delete saved project “${entry ? entry.name : selectedProjectId}”?`)) return;
    try {
      await ui.storage.delete(`project:${selectedProjectId}`); projects = projects.filter((item) => item.id !== selectedProjectId);
      await ui.storage.set("projects", projects); selectedProjectId = projects[0] ? projects[0].id : ""; renderMain("Saved project deleted. The open canvas was not changed.");
    } catch (error) { setStatus(`Delete failed: ${error.message}`); }
  }

  function expandTabs(line) {
    let output = "";
    for (const character of Array.from(line)) output += character === "\t" ? " ".repeat(4 - output.length % 4) : character;
    return output;
  }
  function importText(event) {
    const raw = String(event.values.importText || "").replace(/\r\n?/g, "\n");
    if (!raw) return setStatus("Paste plain-text artwork before importing.");
    if (dirty && !confirm("import", "Import text and replace the unsaved canvas?")) return;
    const lines = raw.split("\n").map(expandTabs), clippedWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, ...lines.map((line) => Array.from(line).length)));
    const clippedHeight = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, lines.length));
    remember(); width = clippedWidth; height = clippedHeight; cells = blankCells(width, height);
    for (let y = 0; y < Math.min(lines.length, height); y += 1) Array.from(lines[y]).slice(0, width).forEach((character, x) => { cells[y][x] = { char: character, fg: foreground, bg: background }; });
    cursor = { x: 0, y: 0 }; dirty = true; confirmation = "";
    const clipped = lines.length > MAX_HEIGHT || lines.some((line) => Array.from(line).length > MAX_WIDTH);
    renderMain(clipped ? `Artwork imported and clipped to the ${MAX_WIDTH}x${MAX_HEIGHT} limit.` : `Artwork imported at ${width}x${height}.`);
  }
  function plainText() { return cells.map((row) => row.map((cell) => cell.char).join("")).join("\n"); }
  function ansiCode(cell) { return `\u001b[${cell.fg < 8 ? 30 + cell.fg : 90 + cell.fg - 8};${cell.bg < 8 ? 40 + cell.bg : 100 + cell.bg - 8}m`; }
  function ansiText() {
    return cells.map((row) => {
      let output = "", previous = "";
      row.forEach((cell) => { const code = ansiCode(cell); if (code !== previous) { output += code; previous = code; } output += cell.char; });
      return `${output}\u001b[0m`;
    }).join("\n");
  }
  function showExport(kind) {
    const value = kind === "ansi" ? ansiText() : plainText();
    ui.update("export-output", ui.textarea("exportOutput", kind === "ansi" ? "ANSI export" : "Plain-text export", { id: "export-output", value, maxLength: 120000 }));
    setStatus(`${kind === "ansi" ? "ANSI" : "Plain-text"} export prepared.`);
  }
  async function copyExport(event) {
    try { await ui.clipboard.writeText(String(event.values.exportOutput || "")); setStatus("Export copied to the clipboard."); }
    catch (error) { setStatus(`Copy failed: ${error.message}`); }
  }
  async function downloadExport(kind) {
    const name = (projectName || "artwork").replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-|-$/g, "") || "artwork";
    try { await ui.download.text(`${name}.${kind === "ansi" ? "ans" : "txt"}`, kind === "ansi" ? ansiText() : plainText()); setStatus("Export download started."); }
    catch (error) { setStatus(`Download failed: ${error.message}`); }
  }
  async function downloadImage() {
    const name = (projectName || "artwork").replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-|-$/g, "") || "artwork";
    try { await ui.download.cellImage(`${name}.png`, cellRows(), { cellWidth: 20, cellHeight: 24, fontSize: 18 }); setStatus("PNG image download started."); }
    catch (error) { setStatus(`Image download failed: ${error.message}`); }
  }

  function chooseTool(event) { tool = event.value; lineStart = null; cancelConfirmation(); refreshCanvas(`${tool[0].toUpperCase() + tool.slice(1)} tool selected.`); }
  function chooseCharacter(event) { selectedChar = event.value; tool = "pencil"; lineStart = null; refreshCanvas(`Drawing character set to ${selectedChar === " " ? "SPACE" : selectedChar}.`); }
  function customCharacter(event) {
    selectedChar = oneCharacter(event.values.customChar); tool = "pencil"; lineStart = null; refreshCanvas(`Drawing character set to ${selectedChar === " " ? "SPACE" : selectedChar}.`);
  }
  function onKey(event) {
    const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (moves[event.key]) { cursor.x = clamp(cursor.x + moves[event.key][0], 0, width - 1); cursor.y = clamp(cursor.y + moves[event.key][1], 0, height - 1); return refreshCanvas(); }
    if (event.key === "Delete" || event.key === "Backspace") { const previous = tool; tool = "erase"; paintAt(cursor.x, cursor.y, false); tool = previous; return; }
    if (event.key === " " || event.key === "Enter") return paintAt(cursor.x, cursor.y, false);
    if (event.key.toLowerCase() === "u") return undo();
    if (event.key.toLowerCase() === "r") return redo();
    if (Array.from(event.key).length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey) { selectedChar = event.key; tool = "pencil"; paintAt(cursor.x, cursor.y, false); }
  }

  function renderMain(message) {
    status = message || status; confirmation = "";
    canvasComponent = ui.cellGrid(cellRows(), { id: "art-canvas", cursor: { ...cursor }, brush: tool === "pencil" || tool === "erase" ? currentCell() : null, onCell, label: "ASCII and ANSI drawing canvas" });
    const savedItems = projects.length ? projects.map((item) => ({ label: item.name, value: item.id })) : [{ label: "No saved projects", value: "" }];
    ui.render(
      ui.title("ASCII/ANSI Art Studio"),
      ui.text(status, { id: "studio-status", tone: "accent" }),
      ui.menu("TOOLS", ["pencil", "erase", "line", "fill"].map((value) => ({ label: value.toUpperCase(), value })), chooseTool),
      ui.menu("CHARACTER PALETTE", CHARACTERS.map((value) => ({ label: value === " " ? "SP" : value, value })), chooseCharacter),
      ui.input("customChar", "Custom character", { maxLength: 2, placeholder: "Type one character" }),
      ui.button("Use Custom Character", customCharacter, { variant: "secondary" }),
      ui.select("foreground", "Foreground color", COLORS.map((label, value) => ({ label, value: String(value) })), { value: String(foreground), onChange: (event) => { foreground = Number(event.value); refreshCanvas(); } }),
      ui.select("background", "Background color", COLORS.map((label, value) => ({ label, value: String(value) })), { value: String(background), onChange: (event) => { background = Number(event.value); refreshCanvas(); } }),
      ui.text("", { id: "canvas-info", tone: "muted" }), canvasComponent,
      ui.menu("EDIT", [{ label: "UNDO (U)", value: "undo" }, { label: "REDO (R)", value: "redo" }, { label: "CLEAR", value: "clear" }], (event) => event.value === "undo" ? undo() : event.value === "redo" ? redo() : clearCanvas()),
      ui.heading("Canvas", 3),
      ui.numberInput("canvasWidth", "Width (4–60)", { value: width, min: MIN_WIDTH, max: MAX_WIDTH }),
      ui.numberInput("canvasHeight", "Height (4–30)", { value: height, min: MIN_HEIGHT, max: MAX_HEIGHT }),
      ui.button("Resize Canvas", resizeCanvas), ui.button("New Project", newProject, { variant: "secondary" }),
      ui.heading("Projects", 3),
      ui.input("projectName", "Project name", { value: projectName, maxLength: 60 }), ui.button("Save / Update", saveProject),
      ui.select("savedProject", "Saved projects", savedItems, { value: selectedProjectId, onChange: (event) => { selectedProjectId = event.value; cancelConfirmation(); } }),
      ui.menu("PROJECT ACTIONS", [{ label: "LOAD", value: "load" }, { label: "DELETE", value: "delete" }], (event) => event.value === "load" ? loadProject() : deleteProject()),
      ui.heading("Import plain text", 3), ui.textarea("importText", "Paste artwork", { maxLength: 120000, placeholder: "Paste plain ASCII or Unicode art here" }), ui.button("Import and Replace Canvas", importText),
      ui.heading("Export", 3),
      ui.menu("FORMAT", [{ label: "PREPARE TEXT", value: "text" }, { label: "PREPARE ANSI", value: "ansi" }], (event) => showExport(event.value)),
      ui.textarea("exportOutput", "Export output", { id: "export-output", value: plainText(), maxLength: 120000 }),
      ui.menu("EXPORT ACTIONS", [{ label: "COPY", value: "copy" }, { label: "DOWNLOAD .TXT", value: "text" }, { label: "DOWNLOAD .ANS", value: "ansi" }, { label: "DOWNLOAD .PNG", value: "png" }], (event) => event.value === "copy" ? copyExport(event) : event.value === "png" ? downloadImage() : downloadExport(event.value)),
      ui.text("Keyboard: arrows move; Space/Enter draws; type a character to draw it; Delete erases; U/R undo and redo.", { tone: "muted" })
    );
    updateInfo();
  }

  ui.onKey(onKey);
  try { const saved = await ui.storage.get("projects"); projects = Array.isArray(saved) ? saved.filter((item) => item && typeof item.id === "string" && typeof item.name === "string").slice(0, 90) : []; selectedProjectId = projects[0] ? projects[0].id : ""; }
  catch (_) { status = "Storage is temporarily unavailable; editing and export still work."; }
  renderMain(status);
});
