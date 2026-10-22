"use strict";

// Trusted host renderer. It accepts a deliberately small data vocabulary and
// creates every node itself; application strings are assigned with textContent.
(() => {
  const frame = document.querySelector("[data-app-runtime]");
  const surface = document.querySelector("[data-app-surface]");
  const status = document.querySelector("[data-app-status]");
  if (!frame || !surface || !status) return;

  const PROTOCOL = "stationnet-app-v1";
  const LIMITS = { components: 100, text: 8000, items: 100, columns: 80, rows: 100, canvasCommands: 600, capabilityText: 200000 };
  const keyboardActions = new Set();
  const lifecycleActions = new Set();
  let eventWindow = [];
  let totalEvents = 0;
  let closed = false;
  const startupTimer = setTimeout(() => {
    status.textContent = "PROGRAM STATUS / UNAVAILABLE";
    surface.textContent = "The application runtime did not respond.";
    terminate();
  }, 10000);

  const text = (value, limit = LIMITS.text) => String(value == null ? "" : value).slice(0, limit);
  const number = (value, fallback, min, max) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback;
  };

  function collectValues() {
    const result = Object.create(null);
    surface.querySelectorAll("[data-app-input]").forEach((input) => {
      if (input.type === "checkbox") result[input.name] = input.checked;
      else result[input.name] = input.type === "number" && input.value !== "" ? Number(input.value) : input.value;
    });
    return result;
  }

  function interact(action, extra = {}) {
    if (!action || closed || !frame.contentWindow) return;
    frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "interaction", action, values: collectValues(), ...extra }, "*");
  }

  const canvasColor = (value, fallback = "#00ed45") => {
    const color = String(value || "");
    return /^(#[0-9a-f]{3,8}|rgb\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\))$/i.test(color) ? color : fallback;
  };

  function drawCanvas(canvas, component) {
    const width = Math.trunc(number(component.width, 640, 80, 1200));
    const height = Math.trunc(number(component.height, 400, 80, 800));
    canvas.width = width; canvas.height = height;
    canvas.dataset.logicalWidth = String(width); canvas.dataset.logicalHeight = String(height);
    if (component.action) canvas.dataset.canvasAction = text(component.action, 80);
    if (component.hover != null) canvas.dataset.canvasHover = component.hover ? "1" : "0";
    canvas.setAttribute("aria-label", text(component.label, 200));
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, width, height); context.fillStyle = "#010401"; context.fillRect(0, 0, width, height);
    (Array.isArray(component.commands) ? component.commands : []).slice(0, LIMITS.canvasCommands).forEach((command) => {
      if (!command || typeof command !== "object") return;
      const x = number(command.x, 0, -width, width * 2), y = number(command.y, 0, -height, height * 2);
      if (command.type === "rect") {
        const w = number(command.width, 1, 0, width * 2), h = number(command.height, 1, 0, height * 2);
        context.fillStyle = canvasColor(command.color); context.fillRect(x, y, w, h);
      } else if (command.type === "circle") {
        context.beginPath(); context.arc(x, y, number(command.radius, 2, 0, Math.max(width, height)), 0, Math.PI * 2);
        context.fillStyle = canvasColor(command.color); context.fill();
      } else if (command.type === "text") {
        context.fillStyle = canvasColor(command.color, "#ffffff");
        context.font = `${Math.trunc(number(command.size, 18, 8, 72))}px monospace`;
        context.textAlign = ["left", "center", "right"].includes(command.align) ? command.align : "left";
        context.textBaseline = "middle"; context.fillText(text(command.text, 200), x, y);
      }
    });
  }

  function drawCanvasControls(wrapper, controls) {
    const previous = wrapper.querySelector(".app-canvas-controls");
    if (previous) previous.remove();
    if (!Array.isArray(controls) || !controls.length) return;
    const overlay = document.createElement("div"); overlay.className = "app-canvas-controls";
    controls.slice(0, 4).forEach((control) => {
      if (!control || typeof control !== "object") return;
      const button = document.createElement("button"); button.type = "button";
      button.textContent = text(control.label, 120);
      if (control.variant === "secondary") button.classList.add("secondary");
      button.addEventListener("click", () => interact(text(control.action, 80)));
      overlay.append(button);
    });
    if (overlay.childElementCount) wrapper.append(overlay);
  }

  function renderComponent(component) {
    if (!component || typeof component !== "object") return null;
    let node;
    switch (component.type) {
      case "title":
        node = document.createElement("h2"); node.className = "app-title"; node.textContent = text(component.text); break;
      case "heading":
        node = document.createElement(number(component.level, 3, 2, 4) === 2 ? "h2" : "h3");
        node.className = "app-heading"; node.textContent = text(component.text); break;
      case "text":
        node = document.createElement("p"); node.className = "app-text";
        if (["muted", "warning", "accent"].includes(component.tone)) node.classList.add(`app-${component.tone}`);
        if (component.emphasis) node.classList.add("app-emphasis");
        node.textContent = text(component.text); break;
      case "button":
        node = document.createElement("button"); node.type = "button"; node.textContent = text(component.label, 120);
        if (component.variant === "secondary") node.classList.add("secondary");
        node.disabled = Boolean(component.disabled);
        node.addEventListener("click", () => interact(text(component.action, 80))); break;
      case "menu": {
        node = document.createElement("fieldset"); node.className = "app-menu";
        if (component.layout === "dpad") node.classList.add("app-dpad");
        const legend = document.createElement("legend"); legend.textContent = text(component.label, 200); node.append(legend);
        (Array.isArray(component.items) ? component.items : []).slice(0, LIMITS.items).forEach((item) => {
          if (!item || typeof item !== "object") return;
          const button = document.createElement("button"); button.type = "button"; button.textContent = text(item.label, 120);
          button.disabled = Boolean(item.disabled);
          button.addEventListener("click", () => interact(text(component.action, 80), { value: text(item.value, 500) }));
          node.append(button);
        }); break;
      }
      case "input": case "number": {
        node = document.createElement("label"); node.textContent = text(component.label, 200);
        const input = document.createElement("input"); input.dataset.appInput = "";
        input.name = text(component.name, 80).replace(/[^a-zA-Z0-9_-]/g, "_");
        input.type = component.type === "number" ? "number" : "text";
        input.value = text(component.value, 500); input.placeholder = text(component.placeholder, 200);
        if (component.type === "input") input.maxLength = number(component.maxLength, 500, 1, 500);
        if (component.type === "number") {
          if (Number.isFinite(Number(component.min))) input.min = String(component.min);
          if (Number.isFinite(Number(component.max))) input.max = String(component.max);
          input.step = String(number(component.step, 1, 0.000001, 1000000));
        }
        if (component.submitAction) input.addEventListener("keydown", (event) => {
          if (event.key === "Enter") { event.preventDefault(); interact(text(component.submitAction, 80)); }
        });
        node.append(input); break;
      }
      case "textarea": {
        node = document.createElement("label"); node.textContent = text(component.label, 200);
        const input = document.createElement("textarea"); input.dataset.appInput = "";
        input.name = text(component.name, 80).replace(/[^a-zA-Z0-9_-]/g, "_");
        input.value = text(component.value, LIMITS.capabilityText); input.placeholder = text(component.placeholder, 200);
        input.maxLength = number(component.maxLength, 8000, 1, LIMITS.capabilityText); node.append(input); break;
      }
      case "select": {
        node = document.createElement("label"); node.textContent = text(component.label, 200);
        const input = document.createElement("select"); input.dataset.appInput = "";
        input.name = text(component.name, 80).replace(/[^a-zA-Z0-9_-]/g, "_");
        (Array.isArray(component.items) ? component.items : []).slice(0, LIMITS.items).forEach((item) => {
          const option = document.createElement("option"); option.value = text(item && item.value, 500);
          option.textContent = text(item && item.label, 200); input.append(option);
        });
        input.value = text(component.value, 500);
        if (component.action) input.addEventListener("change", () => interact(text(component.action, 80), { value: input.value }));
        node.append(input); break;
      }
      case "checkbox": {
        node = document.createElement("label"); node.className = "app-checkbox";
        const input = document.createElement("input"); input.type = "checkbox"; input.dataset.appInput = "";
        input.name = text(component.name, 80).replace(/[^a-zA-Z0-9_-]/g, "_"); input.checked = Boolean(component.checked);
        const caption = document.createElement("span"); caption.textContent = text(component.label, 200);
        if (component.action) input.addEventListener("change", () => interact(text(component.action, 80), { value: input.checked }));
        node.append(input, caption); break;
      }
      case "list": {
        node = document.createElement(component.ordered ? "ol" : "ul"); node.className = "app-list";
        (Array.isArray(component.items) ? component.items : []).slice(0, LIMITS.items).forEach((item) => {
          const li = document.createElement("li"); li.textContent = text(item, 1000); node.append(li);
        }); break;
      }
      case "table": {
        node = document.createElement("div"); node.className = "app-table-wrap";
        const table = document.createElement("table"); const headers = Array.isArray(component.headers) ? component.headers.slice(0, LIMITS.columns) : [];
        if (headers.length) { const tr = table.createTHead().insertRow(); headers.forEach((value) => { const th = document.createElement("th"); th.textContent = text(value, 300); tr.append(th); }); }
        const body = table.createTBody();
        (Array.isArray(component.rows) ? component.rows : []).slice(0, LIMITS.rows).forEach((row) => {
          const tr = body.insertRow(); (Array.isArray(row) ? row : []).slice(0, LIMITS.columns).forEach((value) => { const td = tr.insertCell(); td.textContent = text(value, 1000); });
        });
        node.append(table); break;
      }
      case "pre": case "grid":
        node = document.createElement("pre"); node.className = component.type === "grid" ? "app-grid" : "app-pre";
        node.textContent = component.type === "grid" ? (Array.isArray(component.rows) ? component.rows : []).slice(0, LIMITS.rows).map((row) => text(row, 300)).join("\n") : text(component.text); break;
      case "cell-grid": {
        node = document.createElement("div"); node.className = "app-cell-grid";
        node.setAttribute("role", "grid"); node.setAttribute("aria-label", text(component.label, 200));
        const rows = (Array.isArray(component.rows) ? component.rows : []).slice(0, 50);
        const width = Math.min(LIMITS.columns, rows.reduce((largest, row) => Math.max(largest, Array.isArray(row) ? row.length : 0), 0));
        node.style.setProperty("--app-grid-columns", String(Math.max(1, width)));
        let drawing = false, lastCell = null;
        const cursor = component.cursor && typeof component.cursor === "object" ? component.cursor : {};
        const brush = component.brush && typeof component.brush === "object" ? {
          char: Array.from(String(component.brush.char == null ? " " : component.brush.char))[0] || " ",
          fg: Math.trunc(number(component.brush.fg, 7, 0, 15)), bg: Math.trunc(number(component.brush.bg, 0, 0, 15))
        } : null;
        const preview = (cell) => {
          if (!brush) return;
          for (let index = 0; index < 16; index += 1) cell.classList.remove(`app-fg-${index}`, `app-bg-${index}`);
          cell.classList.add(`app-fg-${brush.fg}`, `app-bg-${brush.bg}`); cell.textContent = brush.char;
        };
        const activate = (cell, phase) => {
          if (phase !== "end") { lastCell = cell; preview(cell); }
          interact(text(component.action, 80), {
          value: `${cell.dataset.x},${cell.dataset.y}`,
          x: Number(cell.dataset.x), y: Number(cell.dataset.y), drag: phase === "move", phase
          });
        };
        const finish = () => { if (!drawing || !lastCell) return; drawing = false; activate(lastCell, "end"); };
        rows.forEach((row, y) => (Array.isArray(row) ? row : []).slice(0, width).forEach((cell, x) => {
          const value = cell && typeof cell === "object" ? cell : {};
          const button = document.createElement("button"); button.type = "button"; button.className = "app-cell";
          const character = Array.from(String(value.char == null ? " " : value.char))[0] || " ";
          const foreground = Math.trunc(number(value.fg, 7, 0, 15)); const background = Math.trunc(number(value.bg, 0, 0, 15));
          button.classList.add(`app-fg-${foreground}`, `app-bg-${background}`);
          if (Number(cursor.x) === x && Number(cursor.y) === y) button.classList.add("app-cell-cursor");
          button.dataset.x = String(x); button.dataset.y = String(y); button.textContent = character;
          button.setAttribute("role", "gridcell"); button.setAttribute("aria-label", `Column ${x + 1}, row ${y + 1}, ${character === " " ? "blank" : character}`);
          button.addEventListener("pointerdown", (event) => { if (event.button !== 0) return; event.preventDefault(); drawing = true; button.focus(); activate(button, "start"); });
          button.addEventListener("pointerenter", () => { if (drawing) activate(button, "move"); });
          button.addEventListener("click", (event) => { if (event.detail === 0) { activate(button, "start"); activate(button, "end"); } });
          node.append(button);
        }));
        node.addEventListener("pointerup", finish);
        node.addEventListener("pointercancel", finish);
        node.addEventListener("pointerleave", finish);
        break;
      }
      case "canvas": {
        node = document.createElement("div"); node.className = "app-canvas-wrap";
        const canvas = document.createElement("canvas"); canvas.className = "app-canvas"; canvas.tabIndex = 0;
        canvas.setAttribute("role", "img"); drawCanvas(canvas, component); node.append(canvas);
        const point = (event, phase) => {
          const bounds = canvas.getBoundingClientRect();
          const x = (event.clientX - bounds.left) * Number(canvas.dataset.logicalWidth) / Math.max(1, bounds.width);
          const y = (event.clientY - bounds.top) * Number(canvas.dataset.logicalHeight) / Math.max(1, bounds.height);
          interact(canvas.dataset.canvasAction, { x, y, phase });
        };
        canvas.addEventListener("pointerdown", (event) => { event.preventDefault(); canvas.setPointerCapture(event.pointerId); point(event, "start"); });
        canvas.addEventListener("pointermove", (event) => { if (event.buttons || event.pointerType === "touch" || canvas.dataset.canvasHover === "1") point(event, "move"); });
        canvas.addEventListener("pointerup", (event) => point(event, "end"));
        drawCanvasControls(node, component.controls);
        break;
      }
      default: return null;
    }
    if (component.id != null) node.dataset.appComponent = text(component.id, 80).replace(/[^a-zA-Z0-9_.:-]/g, "_");
    return node;
  }

  function render(message) {
    const components = Array.isArray(message.components) ? message.components.slice(0, LIMITS.components) : [];
    try {
      if (JSON.stringify(components).length > 250000) throw new Error("content limit exceeded");
    } catch (_) {
      status.textContent = "PROGRAM STATUS / REQUEST REJECTED";
      return;
    }
    const targetId = text(message.target, 80).replace(/[^a-zA-Z0-9_.:-]/g, "_");
    const targetComponent = () => Array.from(surface.querySelectorAll("[data-app-component]"))
      .find((node) => node.dataset.appComponent === targetId);
    if (message.mode === "remove") {
      const target = targetComponent();
      if (target) target.remove();
      return;
    }
    if (message.mode === "update") {
      const target = targetComponent();
      if (target && target.classList.contains("app-canvas-wrap") && components[0] && components[0].type === "canvas") {
        drawCanvas(target.querySelector("canvas"), components[0]);
        // A canvas update that only redraws its commands must leave an existing
        // contextual overlay in place. The runtime serializes omitted options as
        // undefined, so checking only for the property removed START as soon as
        // Breakout sent its first hover update.
        if (components[0].controls !== undefined) drawCanvasControls(target, components[0].controls);
        return;
      }
      const replacement = renderComponent(components[0]);
      if (target && replacement) target.replaceWith(replacement);
      return;
    }
    const fragment = document.createDocumentFragment();
    components.forEach((component) => { const node = renderComponent(component); if (node) fragment.append(node); });
    if (message.mode !== "append") surface.replaceChildren();
    surface.append(fragment); status.textContent = "PROGRAM STATUS / RUNNING";
  }

  function allowedEvent() {
    const now = Date.now(); eventWindow = eventWindow.filter((time) => now - time < 1000);
    totalEvents += 1;
    if (totalEvents > 1000000) { terminate(); return false; }
    if (eventWindow.length >= 90) return false;
    eventWindow.push(now); return true;
  }

  async function storage(message) {
    const requestId = text(message.request, 80);
    try {
      const response = await fetch(surface.dataset.appStorageUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": surface.dataset.appCsrf },
        body: JSON.stringify({ operation: message.operation, key: message.key, value: message.value, scope: message.scope })
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || `Storage request failed (${response.status})`);
      if (frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "storage-result", request: requestId, ok: true, value: result.value }, "*");
    } catch (error) {
      if (frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "storage-result", request: requestId, ok: false, error: text(error.message || error, 500) }, "*");
    }
  }

  async function scores(message) {
    const requestId = text(message.request, 80);
    try {
      const submitting = message.operation === "submit";
      const response = await fetch(surface.dataset.appScoresUrl, {
        method: submitting ? "POST" : "GET", credentials: "same-origin",
        headers: submitting ? { "Content-Type": "application/json", "X-CSRF-Token": surface.dataset.appCsrf } : {},
        body: submitting ? JSON.stringify({ score: message.score }) : undefined
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || `Score request failed (${response.status})`);
      if (frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "scores-result", request: requestId, ok: true, value: result }, "*");
    } catch (error) {
      if (frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "scores-result", request: requestId, ok: false, error: text(error.message || error, 500) }, "*");
    }
  }

  function capabilityResult(request, ok, value, error) {
    if (frame.contentWindow) frame.contentWindow.postMessage({
      protocol: PROTOCOL, type: "capability-result", request: text(request, 80), ok,
      value, error: error ? text(error.message || error, 500) : undefined
    }, "*");
  }

  async function capability(message) {
    const request = message.request;
    const value = text(message.text, LIMITS.capabilityText);
    try {
      if (message.operation === "copy-text") {
        if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(value);
        else {
          const input = document.createElement("textarea"); input.value = value; input.style.position = "fixed"; input.style.opacity = "0";
          document.body.append(input); input.select();
          if (!document.execCommand("copy")) throw new Error("Copy is unavailable in this browser");
          input.remove();
        }
        capabilityResult(request, true, true); return;
      }
      if (message.operation === "download-text") {
        const requested = text(message.filename, 120).replace(/[^a-zA-Z0-9._-]/g, "-");
        const filename = requested && !requested.startsWith(".") ? requested : "stationnet-export.txt";
        const url = URL.createObjectURL(new Blob([value], { type: "text/plain;charset=utf-8" }));
        const link = document.createElement("a"); link.href = url; link.download = filename; link.hidden = true;
        document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        capabilityResult(request, true, true); return;
      }
      if (message.operation === "download-cell-image") {
        const rows = (Array.isArray(message.rows) ? message.rows : []).slice(0, 50);
        const columns = Math.min(LIMITS.columns, rows.reduce((largest, row) => Math.max(largest, Array.isArray(row) ? row.length : 0), 0));
        if (!rows.length || !columns) throw new Error("Image grid is empty");
        const cellWidth = Math.trunc(number(message.cellWidth, 20, 8, 32));
        const cellHeight = Math.trunc(number(message.cellHeight, 24, 8, 40));
        const fontSize = Math.trunc(number(message.fontSize, 18, 6, cellHeight));
        const ansi = ["#000000", "#aa0000", "#00aa00", "#aa5500", "#0000aa", "#aa00aa", "#00aaaa", "#aaaaaa", "#555555", "#ff5555", "#55ff55", "#ffff55", "#5555ff", "#ff55ff", "#55ffff", "#ffffff"];
        const canvas = document.createElement("canvas"); canvas.width = columns * cellWidth; canvas.height = rows.length * cellHeight;
        const context = canvas.getContext("2d"); context.textAlign = "center"; context.textBaseline = "middle"; context.font = `${fontSize}px monospace`;
        rows.forEach((row, y) => Array.from({ length: columns }, (_, x) => Array.isArray(row) ? row[x] : null).forEach((raw, x) => {
          const cell = raw && typeof raw === "object" ? raw : {};
          const fg = Math.trunc(number(cell.fg, 7, 0, 15)), bg = Math.trunc(number(cell.bg, 0, 0, 15));
          const character = Array.from(String(cell.char == null ? " " : cell.char))[0] || " ";
          context.fillStyle = ansi[bg]; context.fillRect(x * cellWidth, y * cellHeight, cellWidth, cellHeight);
          context.fillStyle = ansi[fg]; context.fillText(character, x * cellWidth + cellWidth / 2, y * cellHeight + cellHeight / 2);
        }));
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
        if (!blob) throw new Error("Image encoding failed");
        const requested = text(message.filename, 120).replace(/[^a-zA-Z0-9._-]/g, "-");
        const filename = requested && !requested.startsWith(".") ? requested : "stationnet-art.png";
        const url = URL.createObjectURL(blob); const link = document.createElement("a");
        link.href = url; link.download = filename; link.hidden = true; document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000); capabilityResult(request, true, true); return;
      }
      throw new Error("Unsupported capability request");
    } catch (error) { capabilityResult(request, false, false, error); }
  }

  addEventListener("message", (event) => {
    if (event.source !== frame.contentWindow || !event.data || event.data.protocol !== PROTOCOL || !allowedEvent()) return;
    const message = event.data;
    if (message.type === "initialized") { clearTimeout(startupTimer); status.textContent = "PROGRAM STATUS / INITIALIZED"; }
    else if (message.type === "ready") status.textContent = "PROGRAM STATUS / RUNNING";
    else if (message.type === "render") render(message);
    else if (message.type === "storage") storage(message);
    else if (message.type === "scores") scores(message);
    else if (message.type === "capability") capability(message);
    else if (message.type === "keyboard") {
      const id = text(message.action, 80); message.enabled ? keyboardActions.add(id) : keyboardActions.delete(id);
      if (message.enabled) surface.tabIndex = 0;
    } else if (message.type === "lifecycle") {
      const id = text(message.action, 80); message.enabled ? lifecycleActions.add(id) : lifecycleActions.delete(id);
      if (message.enabled) interact(id, { value: document.hidden || !document.hasFocus() ? "hidden" : "visible" });
    } else if (message.type === "error") {
      status.textContent = "PROGRAM STATUS / ERROR";
      const error = document.createElement("p"); error.className = "app-error"; error.textContent = `PROGRAM ERROR: ${text(message.message, 500)}`; surface.replaceChildren(error);
    } else if (message.type === "terminated") status.textContent = "PROGRAM STATUS / TERMINATED";
  });

  // Contextual controls are replaced after activation, which can move focus
  // back to the document body. Listen at the window so game keys continue to
  // work without requiring the player to refocus the application surface.
  addEventListener("keydown", (event) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
    if (event.target.tagName === "BUTTON" && ["Enter", " "].includes(event.key)) return;
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", " ", "Backspace", "Delete"].includes(event.key)) event.preventDefault();
    keyboardActions.forEach((action) => interact(action, {
      key: text(event.key, 40), code: text(event.code, 40),
      ctrlKey: event.ctrlKey, altKey: event.altKey, shiftKey: event.shiftKey, metaKey: event.metaKey
    }));
  });

  const reportVisibility = () => lifecycleActions.forEach((action) => interact(action, {
    value: document.hidden || !document.hasFocus() ? "hidden" : "visible"
  }));
  document.addEventListener("visibilitychange", reportVisibility);
  addEventListener("blur", reportVisibility);
  addEventListener("focus", reportVisibility);

  function terminate() {
    if (closed) return; closed = true;
    clearTimeout(startupTimer);
    if (frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "terminate" }, "*");
    frame.remove(); keyboardActions.clear(); lifecycleActions.clear();
  }
  addEventListener("pagehide", terminate, { once: true });
})();
