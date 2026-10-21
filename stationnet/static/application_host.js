"use strict";

// Trusted host renderer. It accepts a deliberately small data vocabulary and
// creates every node itself; application strings are assigned with textContent.
(() => {
  const frame = document.querySelector("[data-app-runtime]");
  const surface = document.querySelector("[data-app-surface]");
  const status = document.querySelector("[data-app-status]");
  if (!frame || !surface || !status) return;

  const PROTOCOL = "stationnet-app-v1";
  const LIMITS = { components: 100, text: 8000, items: 100, columns: 12, rows: 100 };
  const keyboardActions = new Set();
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
      result[input.name] = input.type === "number" && input.value !== "" ? Number(input.value) : input.value;
    });
    return result;
  }

  function interact(action, extra = {}) {
    if (!action || closed || !frame.contentWindow) return;
    frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "interaction", action, values: collectValues(), ...extra }, "*");
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
        node.addEventListener("click", () => interact(text(component.action, 80))); break;
      case "menu": {
        node = document.createElement("fieldset"); node.className = "app-menu";
        const legend = document.createElement("legend"); legend.textContent = text(component.label, 200); node.append(legend);
        (Array.isArray(component.items) ? component.items : []).slice(0, LIMITS.items).forEach((item) => {
          if (!item || typeof item !== "object") return;
          const button = document.createElement("button"); button.type = "button"; button.textContent = text(item.label, 120);
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
        node.append(input); break;
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
      default: return null;
    }
    return node;
  }

  function render(message) {
    const components = Array.isArray(message.components) ? message.components.slice(0, LIMITS.components) : [];
    try {
      if (JSON.stringify(components).length > 100000) throw new Error("content limit exceeded");
    } catch (_) {
      status.textContent = "PROGRAM STATUS / REQUEST REJECTED";
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
    if (totalEvents > 1000) { terminate(); return false; }
    if (eventWindow.length >= 40) return false;
    eventWindow.push(now); return true;
  }

  addEventListener("message", (event) => {
    if (event.source !== frame.contentWindow || !event.data || event.data.protocol !== PROTOCOL || !allowedEvent()) return;
    const message = event.data;
    if (message.type === "initialized") { clearTimeout(startupTimer); status.textContent = "PROGRAM STATUS / INITIALIZED"; }
    else if (message.type === "ready") status.textContent = "PROGRAM STATUS / RUNNING";
    else if (message.type === "render") render(message);
    else if (message.type === "keyboard") {
      const id = text(message.action, 80); message.enabled ? keyboardActions.add(id) : keyboardActions.delete(id);
      if (message.enabled) surface.tabIndex = 0;
    } else if (message.type === "error") {
      status.textContent = "PROGRAM STATUS / ERROR";
      const error = document.createElement("p"); error.className = "app-error"; error.textContent = `PROGRAM ERROR: ${text(message.message, 500)}`; surface.replaceChildren(error);
    } else if (message.type === "terminated") status.textContent = "PROGRAM STATUS / TERMINATED";
  });

  surface.addEventListener("keydown", (event) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
    keyboardActions.forEach((action) => interact(action, { key: text(event.key, 40), code: text(event.code, 40) }));
  });

  function terminate() {
    if (closed) return; closed = true;
    clearTimeout(startupTimer);
    if (frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "terminate" }, "*");
    frame.remove(); keyboardActions.clear();
  }
  addEventListener("pagehide", terminate, { once: true });
})();
