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
      if (input.type === "checkbox") result[input.name] = input.checked;
      else result[input.name] = input.type === "number" && input.value !== "" ? Number(input.value) : input.value;
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
        node.disabled = Boolean(component.disabled);
        node.addEventListener("click", () => interact(text(component.action, 80))); break;
      case "menu": {
        node = document.createElement("fieldset"); node.className = "app-menu";
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
        input.value = text(component.value, 8000); input.placeholder = text(component.placeholder, 200);
        input.maxLength = number(component.maxLength, 8000, 1, 8000); node.append(input); break;
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
      default: return null;
    }
    if (component.id != null) node.dataset.appComponent = text(component.id, 80).replace(/[^a-zA-Z0-9_.:-]/g, "_");
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
    if (totalEvents > 10000) { terminate(); return false; }
    if (eventWindow.length >= 40) return false;
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

  addEventListener("message", (event) => {
    if (event.source !== frame.contentWindow || !event.data || event.data.protocol !== PROTOCOL || !allowedEvent()) return;
    const message = event.data;
    if (message.type === "initialized") { clearTimeout(startupTimer); status.textContent = "PROGRAM STATUS / INITIALIZED"; }
    else if (message.type === "ready") status.textContent = "PROGRAM STATUS / RUNNING";
    else if (message.type === "render") render(message);
    else if (message.type === "storage") storage(message);
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
