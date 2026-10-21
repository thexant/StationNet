"use strict";

// Public StationNet application API. This file runs only inside the sandboxed
// iframe; applications communicate with the host through this small protocol.
(() => {
  const PROTOCOL = "stationnet-app-v1";
  const actions = new Map();
  const cleanups = [];
  let nextAction = 1;
  let messagesSent = 0;
  let stopped = false;

  function send(type, payload = {}) {
    if (stopped || ++messagesSent > 2000) return;
    postMessage({ protocol: PROTOCOL, type, ...payload });
  }

  function fail(error) {
    const message = error instanceof Error ? error.message : String(error);
    send("error", { message: message.slice(0, 500) });
  }

  function action(handler) {
    if (typeof handler !== "function") return null;
    const id = `action-${nextAction++}`;
    actions.set(id, handler);
    return id;
  }

  function options(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  const api = Object.freeze({
    text(content, settings) {
      const opts = options(settings);
      return { type: "text", text: String(content), tone: opts.tone, emphasis: Boolean(opts.emphasis) };
    },
    title(content) { return { type: "title", text: String(content) }; },
    heading(content, level = 2) { return { type: "heading", text: String(content), level }; },
    button(label, onClick, settings) {
      const opts = options(settings);
      return { type: "button", label: String(label), action: action(onClick), variant: opts.variant };
    },
    menu(label, items, onSelect) {
      return {
        type: "menu", label: String(label || "Choose an option"), action: action(onSelect),
        items: (Array.isArray(items) ? items : []).map((item) => {
          if (item && typeof item === "object") return { label: String(item.label), value: String(item.value) };
          return { label: String(item), value: String(item) };
        })
      };
    },
    input(name, label, settings) {
      const opts = options(settings);
      return { type: "input", name: String(name), label: String(label), placeholder: String(opts.placeholder || ""), value: String(opts.value || ""), maxLength: opts.maxLength };
    },
    numberInput(name, label, settings) {
      const opts = options(settings);
      return { type: "number", name: String(name), label: String(label), value: opts.value, min: opts.min, max: opts.max, step: opts.step };
    },
    list(items, settings) {
      return { type: "list", items: (Array.isArray(items) ? items : []).map(String), ordered: Boolean(options(settings).ordered) };
    },
    table(headers, rows) {
      return { type: "table", headers: (Array.isArray(headers) ? headers : []).map(String), rows: (Array.isArray(rows) ? rows : []).map((row) => (Array.isArray(row) ? row : []).map(String)) };
    },
    preformatted(content) { return { type: "pre", text: String(content) }; },
    grid(rows) { return { type: "grid", rows: (Array.isArray(rows) ? rows : []).map(String) }; },
    render(...components) {
      const value = components.length === 1 && Array.isArray(components[0]) ? components[0] : components;
      send("render", { mode: "replace", components: value });
    },
    append(...components) {
      const value = components.length === 1 && Array.isArray(components[0]) ? components[0] : components;
      send("render", { mode: "append", components: value });
    },
    onKey(handler) {
      const id = action(handler);
      send("keyboard", { action: id, enabled: true });
      return () => { actions.delete(id); send("keyboard", { action: id, enabled: false }); };
    },
    onCleanup(handler) { if (typeof handler === "function") cleanups.push(handler); },
    start(handler) {
      if (typeof handler !== "function") throw new TypeError("StationNet.start expects a function");
      Promise.resolve().then(() => handler(api)).catch(fail);
    },
    terminate() { shutdown(); }
  });

  async function dispatch(message) {
    if (!message || message.protocol !== PROTOCOL) return;
    if (message.type === "terminate") return shutdown();
    if (message.type !== "interaction") return;
    const handler = actions.get(message.action);
    if (!handler) return;
    try {
      await handler(Object.freeze({
        value: message.value,
        values: Object.freeze(message.values || {}),
        key: message.key,
        code: message.code
      }));
    } catch (error) { fail(error); }
  }

  function shutdown() {
    if (stopped) return;
    cleanups.splice(0).forEach((handler) => { try { handler(); } catch (_) {} });
    actions.clear();
    send("terminated");
    stopped = true;
  }

  Object.defineProperty(self, "StationNet", { value: api, writable: false, configurable: false });
  addEventListener("message", (event) => dispatch(event.data));
  addEventListener("error", (event) => fail(event.error || event.message));
  addEventListener("unhandledrejection", (event) => fail(event.reason));
  send("initialized");
})();
