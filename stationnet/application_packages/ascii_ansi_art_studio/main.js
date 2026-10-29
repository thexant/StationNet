"use strict";

StationNet.start(async (ui) => {
  const art = await ui.modules.load("art.js");
  const COLORS = ["Black", "Red", "Green", "Yellow", "Blue", "Magenta", "Cyan", "White", "Bright Black", "Bright Red", "Bright Green", "Bright Yellow", "Bright Blue", "Bright Magenta", "Bright Cyan", "Bright White"];
  const CHARACTERS = ["#", "@", "*", "+", "-", "=", "|", "/", "\\", ".", ":", "_", "~", "^", "░", "▒", "▓", "█", "│", "─", "┌", "┐", "└", "┘", "├", "┤", "┬", "┴", "┼", " "];
  const TOOLS = ["pencil", "erase", "line", "rectangle", "fill", "eyedropper", "text"];
  const HISTORY_LIMIT = 40, PROJECT_LIMIT = 80;
  let width = 32, height = 16, cells = art.blankCells(width, height);
  let cursor = { x: 0, y: 0 }, selectedChar = "#", foreground = 7, background = 0, tool = "pencil";
  let undoStack = [], redoStack = [], shapeStart = null, stroke = null;
  let dirty = false, projectName = "Untitled", openProjectId = "", selectedProjectId = "";
  let projects = [], activeTab = 0, importFormat = "text", exportFormat = "text";
  let stampText = "", customCharInput = "";
  let status = "Ready. Choose a tool and draw on the grid.", canvasComponent;
  let draftTimer = null, storageQueue = Promise.resolve(), editGeneration = 0;

  const queueStorage = work => {
    storageQueue = storageQueue.catch(() => {}).then(work);
    return storageQueue;
  };
  const snapshot = () => ({ width, height, cells: art.cloneCells(cells), cursor: { ...cursor } });
  function restore(state) {
    width = state.width; height = state.height; cells = art.cloneCells(state.cells);
    cursor = { ...state.cursor }; shapeStart = null; stroke = null;
  }
  function setStatus(message) {
    status = message;
    ui.update("studio-status", ui.statusBar(status, { id: "studio-status" }));
  }
  function updateInfo() {
    const pending = shapeStart ? ` / ${tool.toUpperCase()} START ${shapeStart.x + 1},${shapeStart.y + 1}` : "";
    ui.update("canvas-info", ui.text(`${width}×${height} / CURSOR ${cursor.x + 1},${cursor.y + 1} / ${tool.toUpperCase()} / ${selectedChar === " " ? "SPACE" : selectedChar} / FG ${COLORS[foreground]} / BG ${COLORS[background]}${pending}${dirty ? " / UNSAVED" : ""}`, { id: "canvas-info", tone: "muted" }));
  }
  function currentCell() { return tool === "erase" ? art.blankCell() : { char: selectedChar, fg: foreground, bg: background }; }
  function refreshCanvas(message) {
    canvasComponent.rows = art.cloneCells(cells); canvasComponent.cursor = { ...cursor };
    canvasComponent.brush = ["pencil", "erase"].includes(tool) ? currentCell() : null;
    ui.update("art-canvas", canvasComponent); updateInfo();
    if (message) setStatus(message);
    if (activeTab === 1) ui.update("plain-preview", ui.preformatted(art.plainText(cells), { id: "plain-preview" }));
  }
  function record(before, changed, message) {
    if (!changed) { if (message) setStatus(message); return false; }
    undoStack.push(before); if (undoStack.length > HISTORY_LIMIT) undoStack.shift();
    redoStack = []; dirty = true; editGeneration += 1; scheduleDraft();
    if (message) setStatus(message);
    return true;
  }
  function scheduleDraft() {
    if (draftTimer) draftTimer();
    draftTimer = ui.timeout(async () => {
      draftTimer = null;
      const draft = { payload: art.projectPayload(projectName, cells), openProjectId };
      try { await queueStorage(() => ui.storage.set("draft", draft)); }
      catch (error) { setStatus(`Draft could not be saved: ${error.message}`); }
    }, 500);
  }
  async function clearDraft() {
    if (draftTimer) { draftTimer(); draftTimer = null; }
    try { await queueStorage(() => ui.storage.delete("draft")); }
    catch (error) { setStatus(`Draft could not be cleared: ${error.message}`); }
  }
  async function ask(title, message, affirmative = "Continue") {
    return await ui.dialog(title, [ui.text(message)], [
      { label: affirmative, value: "yes" }, { label: "Cancel", value: "no" }
    ]) === "yes";
  }
  function replaceArtwork(next, message, name = "Untitled", markDirty = true) {
    width = next.width; height = next.height; cells = art.cloneCells(next.cells);
    cursor = { x: 0, y: 0 }; shapeStart = null; stroke = null;
    projectName = name; openProjectId = ""; dirty = markDirty; undoStack = []; redoStack = [];
    if (markDirty) { editGeneration += 1; scheduleDraft(); }
    renderMain(message);
  }
  function paint(x, y, value) {
    if (x < 0 || y < 0 || x >= width || y >= height || art.sameCell(cells[y][x], value)) return false;
    cells[y][x] = { ...value }; return true;
  }
  function pencilAt(x, y, phase) {
    if (phase === "start") stroke = { before: snapshot(), changed: false, last: { x, y } };
    if (!stroke) return;
    let x0 = stroke.last.x, y0 = stroke.last.y;
    const dx = Math.abs(x - x0), sx = x0 < x ? 1 : -1;
    const dy = -Math.abs(y - y0), sy = y0 < y ? 1 : -1;
    let error = dx + dy;
    while (true) {
      stroke.changed = paint(x0, y0, currentCell()) || stroke.changed;
      if (x0 === x && y0 === y) break;
      const twice = 2 * error;
      if (twice >= dy) { error += dy; x0 += sx; }
      if (twice <= dx) { error += dx; y0 += sy; }
    }
    stroke.last = { x, y };
  }
  function finishStroke(cancelled = false) {
    if (stroke) {
      if (cancelled) restore(stroke.before);
      else record(stroke.before, stroke.changed);
      stroke = null;
    }
    refreshCanvas();
  }
  function placeText(x, y, text) {
    const before = snapshot(); let changed = false, column = x, row = y;
    for (const char of Array.from(text.replace(/\r\n?/g, "\n"))) {
      if (char === "\n") { column = x; row += 1; continue; }
      if (row >= height) break;
      if (column < width && char !== "\u001b" && !/[\u0000-\u001f\u007f-\u009f]/u.test(char))
        changed = paint(column, row, { char, fg: foreground, bg: background }) || changed;
      column += 1;
    }
    record(before, changed, changed ? "Text placed." : "Text did not change the canvas."); refreshCanvas();
  }
  function onCell(event) {
    if (event.phase === "end" || event.phase === "cancel") { finishStroke(event.phase === "cancel"); return; }
    const x = Math.trunc(event.x), y = Math.trunc(event.y);
    if (x < 0 || y < 0 || x >= width || y >= height) return;
    cursor = { x, y }; updateInfo();
    if (tool === "pencil" || tool === "erase") { pencilAt(x, y, event.phase); return; }
    if (event.phase !== "start") return;
    if (tool === "eyedropper") {
      const cell = cells[y][x]; selectedChar = cell.char; foreground = cell.fg; background = cell.bg;
      tool = "pencil"; refreshBrush(event.values); setStatus("Character and colors sampled."); return;
    }
    if (tool === "text") {
      const text = String(event.values.stampText || stampText);
      if (!text) { setStatus("Enter text in the Text tool field first."); return; }
      placeText(x, y, text); return;
    }
    if (tool === "fill") {
      const before = snapshot();
      record(before, art.floodFill(cells, x, y, currentCell()), "Area filled.");
      return;
    }
    if (!shapeStart) { shapeStart = { x, y }; updateInfo(); setStatus(`Choose the ${tool} end cell, or press Escape.`); return; }
    const before = snapshot();
    const changed = tool === "line"
      ? art.drawLine(cells, shapeStart.x, shapeStart.y, x, y, currentCell())
      : art.drawRectangle(cells, shapeStart.x, shapeStart.y, x, y, currentCell());
    shapeStart = null; record(before, changed, `${tool === "line" ? "Line" : "Rectangle"} drawn.`);
  }
  function undo() {
    if (!undoStack.length) return setStatus("Nothing to undo.");
    redoStack.push(snapshot()); restore(undoStack.pop()); dirty = true; editGeneration += 1; scheduleDraft();
    refreshCanvas("Undid the last edit.");
  }
  function redo() {
    if (!redoStack.length) return setStatus("Nothing to redo.");
    undoStack.push(snapshot()); restore(redoStack.pop()); dirty = true; editGeneration += 1; scheduleDraft();
    refreshCanvas("Redid the last edit.");
  }
  async function clearCanvas() {
    if (!await ask("Clear canvas?", "This removes all characters and colors from the open canvas. You can undo it.", "Clear")) return;
    const before = snapshot(), changed = cells.some(row => row.some(cell => !art.sameCell(cell, art.blankCell())));
    if (changed) { cells = art.blankCells(width, height); record(before, true); refreshCanvas("Canvas cleared."); }
    else setStatus("Canvas is already blank.");
  }
  async function resizeCanvas(event) {
    const nextWidth = Number(event.values.canvasWidth), nextHeight = Number(event.values.canvasHeight);
    if (!Number.isInteger(nextWidth) || !Number.isInteger(nextHeight)
        || nextWidth < art.MIN_WIDTH || nextWidth > art.MAX_WIDTH
        || nextHeight < art.MIN_HEIGHT || nextHeight > art.MAX_HEIGHT)
      return setStatus(`Enter dimensions from ${art.MIN_WIDTH}×${art.MIN_HEIGHT} to ${art.MAX_WIDTH}×${art.MAX_HEIGHT}.`);
    if (nextWidth === width && nextHeight === height) return setStatus("Canvas dimensions are unchanged.");
    const clipping = cells.some((row, y) => row.some((cell, x) =>
      (x >= nextWidth || y >= nextHeight) && !art.sameCell(cell, art.blankCell())));
    if (clipping && !await ask("Clip artwork?", "Occupied cells outside the new canvas will be removed. You can undo the resize.", "Resize")) return;
    const before = snapshot(), next = art.blankCells(nextWidth, nextHeight);
    for (let y = 0; y < Math.min(height, nextHeight); y += 1)
      for (let x = 0; x < Math.min(width, nextWidth); x += 1) next[y][x] = { ...cells[y][x] };
    cells = next; width = nextWidth; height = nextHeight;
    cursor = { x: Math.min(cursor.x, width - 1), y: Math.min(cursor.y, height - 1) };
    record(before, true); renderMain(`Canvas resized to ${width}×${height}${clipping ? "; content was clipped" : ""}.`);
  }
  async function newProject() {
    if (dirty && !await ask("Start a new project?", "Unsaved canvas changes will be replaced. Your saved projects remain available.", "New project")) return;
    await clearDraft();
    replaceArtwork({ width: 32, height: 16, cells: art.blankCells(32, 16) }, "New project started.", "Untitled", false);
  }
  function uniqueId(name) {
    const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "project";
    let id = base, index = 2;
    while (projects.some(item => item.id === id)) id = `${base}-${index++}`;
    return id;
  }
  function nameFrom(event, key = "projectName") { return String(event?.values?.[key] ?? projectName).trim().slice(0, 60); }
  async function saveProject(event, asNew = false) {
    const name = nameFrom(event);
    if (!name) return setStatus("Enter a project name before saving.");
    if (projects.some(item => item.name.toLowerCase() === name.toLowerCase() && (asNew || item.id !== openProjectId)))
      return setStatus("That name already belongs to a saved project. Choose a different name.");
    if ((asNew || !openProjectId) && projects.length >= PROJECT_LIMIT)
      return setStatus(`The ${PROJECT_LIMIT}-project limit is reached. Delete a saved project first.`);
    const id = asNew || !openProjectId ? uniqueId(name) : openProjectId;
    const generation = editGeneration, payload = art.projectPayload(name, cells);
    const entry = { id, name, updated: new Date().toISOString() };
    const nextProjects = [entry, ...projects.filter(item => item.id !== id)];
    try {
      await queueStorage(async () => {
        await ui.storage.set(`project:${id}`, payload);
        await ui.storage.set("projects", nextProjects);
      });
      projects = nextProjects; projectName = name; openProjectId = id; selectedProjectId = id;
      if (generation === editGeneration) { dirty = false; await clearDraft(); }
      else scheduleDraft();
      renderMain(`Project “${name}” ${asNew ? "saved as a new copy" : "saved"}.`);
    } catch (error) { setStatus(`Save failed: ${error.message}. The canvas is still open.`); }
  }
  async function loadProject() {
    if (!selectedProjectId) return setStatus("Choose a saved project first.");
    if (dirty && !await ask("Load project?", "Unsaved canvas changes will be replaced.", "Load")) return;
    try {
      const data = art.validateProject(await ui.storage.get(`project:${selectedProjectId}`));
      await clearDraft();
      width = data.width; height = data.height; cells = data.cells;
      projectName = data.name || projects.find(item => item.id === selectedProjectId)?.name || "Untitled";
      openProjectId = selectedProjectId; cursor = { x: 0, y: 0 }; shapeStart = null; stroke = null;
      undoStack = []; redoStack = []; dirty = false; renderMain(`Project “${projectName}” loaded.`);
    } catch (error) { setStatus(`Load failed: ${error.message}. The canvas is unchanged.`); }
  }
  async function renameProject(event) {
    if (!selectedProjectId) return setStatus("Choose a saved project first.");
    const name = nameFrom(event, "renameName");
    if (!name) return setStatus("Enter a new project name.");
    if (projects.some(item => item.id !== selectedProjectId && item.name.toLowerCase() === name.toLowerCase()))
      return setStatus("Another saved project already has that name.");
    const entry = projects.find(item => item.id === selectedProjectId);
    if (!entry || entry.name === name) return setStatus("Project name is unchanged.");
    if (!await ask("Rename project?", `Rename “${entry.name}” to “${name}”?`, "Rename")) return;
    try {
      const payload = await ui.storage.get(`project:${selectedProjectId}`);
      art.validateProject(payload);
      const nextProjects = projects.map(item => item.id === selectedProjectId ? { ...item, name, updated: new Date().toISOString() } : item);
      await queueStorage(async () => {
        await ui.storage.set(`project:${selectedProjectId}`, { ...payload, name });
        await ui.storage.set("projects", nextProjects);
      });
      projects = nextProjects;
      if (openProjectId === selectedProjectId) projectName = name;
      renderMain(`Project renamed to “${name}”.`);
    } catch (error) { setStatus(`Rename failed: ${error.message}`); }
  }
  async function deleteProject() {
    const entry = projects.find(item => item.id === selectedProjectId);
    if (!entry) return setStatus("Choose a saved project first.");
    if (!await ask("Delete saved project?", `Permanently delete “${entry.name}” from your library? The open canvas stays available.`, "Delete")) return;
    try {
      const nextProjects = projects.filter(item => item.id !== entry.id);
      await queueStorage(async () => {
        await ui.storage.delete(`project:${entry.id}`);
        await ui.storage.set("projects", nextProjects);
      });
      projects = nextProjects; selectedProjectId = projects[0]?.id || "";
      if (openProjectId === entry.id) { openProjectId = ""; dirty = true; editGeneration += 1; scheduleDraft(); }
      renderMain(`Saved project “${entry.name}” deleted. The canvas remains open.`);
    } catch (error) { setStatus(`Delete failed: ${error.message}`); }
  }
  async function importArtwork(raw, kind, source) {
    let parsed;
    try { parsed = art.parseArt(raw, kind, foreground, background); }
    catch (error) { return setStatus(`Import failed: ${error.message}. The canvas is unchanged.`); }
    if (dirty && !await ask("Replace canvas?", `Import ${source} and replace unsaved canvas changes?`, "Import")) return;
    await clearDraft();
    replaceArtwork(parsed, parsed.clipped
      ? `Imported ${source}; artwork was clipped to ${art.MAX_WIDTH}×${art.MAX_HEIGHT}.`
      : `Imported ${source} at ${parsed.width}×${parsed.height}.`);
  }
  async function importPaste(event) {
    const raw = String(event.values.importText || "");
    if (!raw) return setStatus("Paste artwork before importing.");
    await importArtwork(raw, importFormat, importFormat === "ansi" ? "ANSI text" : "plain text");
  }
  async function openArtworkFile() {
    try {
      const file = await ui.files.open({ accept: ".txt,.ans,text/plain" });
      const text = new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(file.bytes)).replace(/^\ufeff/, "");
      await importArtwork(text, /\.ans$/i.test(file.name) ? "ansi" : importFormat, file.name);
    } catch (error) { setStatus(`File import failed: ${error.message}. Use a UTF-8 .txt or .ans file.`); }
  }
  async function openProjectFile() {
    try {
      const file = await ui.files.open({ accept: ".json,application/json" });
      const decoded = new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(file.bytes)).replace(/^\ufeff/, "");
      const data = art.validateProject(JSON.parse(decoded));
      if (dirty && !await ask("Open project file?", "Unsaved canvas changes will be replaced.", "Open")) return;
      await clearDraft();
      replaceArtwork(data, `Project file “${file.name}” opened. Save it to add it to your library.`, data.name || "Imported");
    } catch (error) { setStatus(`Project file could not be opened: ${error.message}`); }
  }
  const exportName = extension => `${(projectName || "artwork").replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-|-$/g, "") || "artwork"}.${extension}`;
  const exportText = () => exportFormat === "ansi" ? art.ansiText(cells) : art.plainText(cells);
  async function copyExport() {
    try { await ui.clipboard.writeText(exportText()); setStatus(`${exportFormat.toUpperCase()} artwork copied from the current canvas.`); }
    catch (error) { setStatus(`Copy failed: ${error.message}`); }
  }
  async function downloadExport(kind) {
    try {
      if (kind === "png") await ui.download.cellImage(exportName("png"), art.cloneCells(cells), { cellWidth: 20, cellHeight: 24, fontSize: 18 });
      else if (kind === "project") await ui.download.text(exportName("json"), JSON.stringify(art.projectPayload(projectName, cells), null, 2));
      else await ui.download.text(exportName(exportFormat === "ansi" ? "ans" : "txt"), exportText());
      setStatus("Download started.");
    } catch (error) { setStatus(`Download failed: ${error.message}`); }
  }
  function brushPanel() {
    const palette = (which, selected) => ui.cellGrid([COLORS.map((_, index) => ({ char: which === "fg" ? "A" : " ", fg: which === "fg" ? index : 7, bg: which === "bg" ? index : 0 }))], {
      cursor: { x: selected, y: 0 }, label: `${which === "fg" ? "Foreground" : "Background"} color palette`,
      onCell: event => {
        if (event.phase !== "start") return;
        if (which === "fg") foreground = event.x; else background = event.x;
        refreshBrush(event.values); setStatus(`${which === "fg" ? "Foreground" : "Background"}: ${COLORS[event.x]}.`);
      }
    });
    const choices = CHARACTERS.includes(selectedChar) ? CHARACTERS : [selectedChar, ...CHARACTERS];
    return ui.panel("Brush", [
      ui.row([
        ui.select("character", "Character", choices.map(char => ({ label: char === " " ? "Space" : char, value: char })), {
          value: selectedChar, onChange: event => { selectedChar = event.value; tool = "pencil"; shapeStart = null; refreshBrush(event.values); }
        }),
        ui.input("customChar", "Custom character", { value: customCharInput, maxLength: 2, placeholder: "One character" }),
        ui.button("Use character", event => {
          const character = art.oneCharacter(event.values.customChar);
          if (/[\u0000-\u001f\u007f-\u009f]/u.test(character)) return setStatus("Choose a printable character.");
          selectedChar = character; tool = "pencil"; shapeStart = null;
          refreshBrush(event.values); setStatus(`Drawing character: ${selectedChar === " " ? "Space" : selectedChar}.`);
        })
      ]),
      ui.text(`Foreground: ${COLORS[foreground]}`), palette("fg", foreground),
      ui.text(`Background: ${COLORS[background]}`), palette("bg", background),
      ui.textarea("stampText", "Text tool content", { value: stampText, maxLength: 4000, placeholder: "Type text, then click its start cell" })
    ], { id: "brush-panel" });
  }
  function refreshBrush(values) {
    if (values) { stampText = String(values.stampText || ""); customCharInput = String(values.customChar || ""); }
    ui.update("tools", toolControl()); ui.update("brush-panel", brushPanel());
    canvasComponent.brush = ["pencil", "erase"].includes(tool) ? currentCell() : null;
    updateInfo();
  }
  function toolControl() {
    return ui.select("tool", "Drawing tool", TOOLS.map(value => ({ label: value.toUpperCase(), value })), {
      id: "tools", value: tool, onChange: event => {
        tool = event.value; shapeStart = null; refreshBrush(event.values); refreshCanvas(`${tool.toUpperCase()} selected.`);
      }
    });
  }
  function projectPanel() {
    const selected = projects.find(item => item.id === selectedProjectId);
    return ui.panel("Saved projects", [
      ui.text(`${projects.length}/${PROJECT_LIMIT} saved projects. Library selection does not change the open canvas.`, { tone: "muted" }),
      ui.select("savedProject", "Library", projects.length ? projects.map(item => ({ label: item.name, value: item.id })) : [{ label: "No saved projects", value: "" }], {
        value: selectedProjectId, onChange: event => { selectedProjectId = event.value; updatePanel(); }
      }),
      ui.row([ui.button("Load selected", loadProject), ui.button("Delete selected", deleteProject, { variant: "secondary" })]),
      ui.row([ui.input("renameName", "New name for selected project", { value: selected?.name || "", maxLength: 60 }), ui.button("Rename selected", renameProject)])
    ], { id: "studio-panel" });
  }
  function importExportPanel() {
    return ui.panel("Import and export", [
      ui.text("Import UTF-8 plain text or common 16-color ANSI SGR art. Unsupported ANSI control sequences leave the canvas untouched.", { tone: "muted" }),
      ui.select("importFormat", "Paste format", [{ label: "Plain text", value: "text" }, { label: "ANSI", value: "ansi" }], {
        value: importFormat, onChange: event => { importFormat = event.value; }
      }),
      ui.textarea("importText", "Paste artwork", { maxLength: 120000, placeholder: "Paste text or ANSI data" }),
      ui.row([ui.button("Import pasted artwork", importPaste), ui.button("Open .txt or .ans", openArtworkFile), ui.button("Open project JSON", openProjectFile)]),
      ui.select("exportFormat", "Copy and text download format", [{ label: "Plain text (.txt)", value: "text" }, { label: "ANSI (.ans)", value: "ansi" }], {
        value: exportFormat, onChange: event => { exportFormat = event.value; setStatus(`${exportFormat.toUpperCase()} export selected.`); }
      }),
      ui.row([ui.button("Copy current canvas", copyExport), ui.button("Download selected text", () => downloadExport("text")), ui.button("Download PNG", () => downloadExport("png")), ui.button("Download project JSON", () => downloadExport("project"))]),
      ui.heading("Plain-text preview", 3), ui.preformatted(art.plainText(cells), { id: "plain-preview" })
    ], { id: "studio-panel" });
  }
  function helpPanel() {
    return ui.panel("Studio help", [
      ui.text("Click or drag to draw. Line and rectangle use two clicks. Text places the content from the Brush panel at the clicked cell. Eyedropper samples a cell's character and colors."),
      ui.text("Arrow keys move the cursor. Typing draws and advances. Enter moves to the next row; Space paints a colored space; Delete or Backspace erases. Ctrl/Cmd+Z undoes; Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y redoes. Ctrl/Cmd+S saves. Escape cancels a line or rectangle."),
      ui.text("Projects are private to your account. A working draft is saved automatically; reopening the studio offers to restore it. Project JSON files can be transferred to another station.")
    ], { id: "studio-panel" });
  }
  function updatePanel() { ui.update("studio-panel", activeTab === 0 ? projectPanel() : activeTab === 1 ? importExportPanel() : helpPanel()); }
  async function menuAction(event) {
    if (event.value === "new") return newProject();
    if (event.value === "save") return saveProject(event);
    if (event.value === "save-as") return saveProject(event, true);
    if (event.value === "undo") return undo();
    if (event.value === "redo") return redo();
    if (event.value === "clear") return clearCanvas();
    if (event.value === "copy") return copyExport();
    if (event.value === "png") return downloadExport("png");
  }
  function renderMain(message) {
    if (message) status = message;
    canvasComponent = ui.cellGrid(art.cloneCells(cells), {
      id: "art-canvas", cursor: { ...cursor }, brush: ["pencil", "erase"].includes(tool) ? currentCell() : null,
      onCell, label: "ASCII and ANSI drawing canvas"
    });
    ui.render(
      ui.title("ASCII/ANSI Art Studio"),
      ui.menuBar([
        { label: "File", items: [{ label: "New", value: "new" }, { label: "Save", value: "save" }, { label: "Save As", value: "save-as" }] },
        { label: "Edit", items: [{ label: "Undo", value: "undo" }, { label: "Redo", value: "redo" }, { label: "Clear canvas", value: "clear" }] },
        { label: "Export", items: [{ label: "Copy selected format", value: "copy" }, { label: "Download PNG", value: "png" }] }
      ], menuAction),
      ui.statusBar(status, { id: "studio-status" }),
      toolControl(),
      ui.text("", { id: "canvas-info", tone: "muted" }), canvasComponent,
      brushPanel(),
      ui.row([ui.button("Undo", undo, { variant: "secondary" }), ui.button("Redo", redo, { variant: "secondary" }),
        ui.button("Clear", clearCanvas, { variant: "secondary" })]),
      ui.row([ui.input("projectName", "Open project name", { value: projectName, maxLength: 60 }),
        ui.button("Save", saveProject), ui.button("Save As", event => saveProject(event, true), { variant: "secondary" })]),
      ui.row([ui.numberInput("canvasWidth", `Width (${art.MIN_WIDTH}–${art.MAX_WIDTH})`, { value: width, min: art.MIN_WIDTH, max: art.MAX_WIDTH }),
        ui.numberInput("canvasHeight", `Height (${art.MIN_HEIGHT}–${art.MAX_HEIGHT})`, { value: height, min: art.MIN_HEIGHT, max: art.MAX_HEIGHT }),
        ui.button("Resize", resizeCanvas)]),
      ui.tabs(["Projects", "Import / Export", "Help"], event => { activeTab = Number(event.value); updatePanel(); }, { id: "studio-tabs", selected: activeTab }),
      activeTab === 0 ? projectPanel() : activeTab === 1 ? importExportPanel() : helpPanel()
    );
    updateInfo();
  }
  function drawKey(char) {
    const before = snapshot(), changed = paint(cursor.x, cursor.y, { char, fg: foreground, bg: background });
    record(before, changed);
    if (cursor.x + 1 < width) cursor.x += 1;
    else if (cursor.y + 1 < height) { cursor.x = 0; cursor.y += 1; }
    refreshCanvas();
  }
  function onKey(event) {
    if (event.phase !== "down") return;
    if (event.ctrlKey || event.metaKey) {
      if (event.code === "KeyZ") return event.shiftKey ? redo() : undo();
      if (event.code === "KeyY") return redo();
      if (event.code === "KeyS") return saveProject(event);
      return;
    }
    if (event.altKey) return;
    if (event.key === "Escape") {
      if (shapeStart) { shapeStart = null; updateInfo(); setStatus("Shape cancelled."); }
      return;
    }
    const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (moves[event.key]) {
      cursor.x = Math.max(0, Math.min(width - 1, cursor.x + moves[event.key][0]));
      cursor.y = Math.max(0, Math.min(height - 1, cursor.y + moves[event.key][1]));
      return refreshCanvas();
    }
    if (event.key === "Delete" || event.key === "Backspace") {
      const before = snapshot(); record(before, paint(cursor.x, cursor.y, art.blankCell())); return refreshCanvas();
    }
    if (event.key === "Enter") { cursor.x = 0; cursor.y = Math.min(height - 1, cursor.y + 1); return refreshCanvas(); }
    if (event.key === " " || (Array.from(event.key).length === 1 && !/[\u0000-\u001f\u007f-\u009f]/u.test(event.key))) drawKey(event.key);
  }
  ui.onKey(onKey);
  try {
    const saved = await ui.storage.get("projects");
    projects = Array.isArray(saved) ? saved.filter(item => item && typeof item.id === "string" && typeof item.name === "string").slice(0, PROJECT_LIMIT) : [];
    selectedProjectId = projects[0]?.id || "";
  } catch (error) { status = `Project storage is unavailable: ${error.message}. Editing and export still work.`; }
  renderMain();
  try {
    const savedDraft = await ui.storage.get("draft");
    if (savedDraft) {
      const draft = art.validateProject(savedDraft.payload);
      const choice = await ui.dialog("Restore working draft?", [ui.text(`Restore the unsaved “${draft.name || "Untitled"}” canvas from your last session?`)], [
        { label: "Restore", value: "restore" }, { label: "Discard", value: "discard" }
      ]);
      if (choice === "restore") {
        width = draft.width; height = draft.height; cells = draft.cells; projectName = draft.name || "Untitled";
        openProjectId = projects.some(item => item.id === savedDraft.openProjectId) ? savedDraft.openProjectId : "";
        selectedProjectId = openProjectId || selectedProjectId; dirty = true; renderMain("Working draft restored.");
      } else if (choice === "discard") await clearDraft();
    }
  } catch (error) { setStatus(`Working draft could not be restored: ${error.message}`); }
});
