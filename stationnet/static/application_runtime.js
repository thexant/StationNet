"use strict";

// Public StationNet application API. This file runs only inside the sandboxed
// iframe; applications communicate with the host through this small protocol.
(() => {
  const PROTOCOL = "stationnet-app-v1";
  const actions = new Map();
  const cleanups = [];
  const storageRequests = new Map();
  let nextAction = 1;
  let nextRequest = 1;
  let messagesSent = 0;
  let stopped = false;

  function send(type, payload = {}) {
    if (stopped || ++messagesSent > 10000) return;
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

  function identified(component, settings) {
    const id = options(settings).id;
    if (id != null) component.id = String(id);
    return component;
  }

  function storage(operation, key, value, settings) {
    const request = `storage-${nextRequest++}`;
    const scope = options(settings).scope === "shared" ? "shared" : "user";
    return new Promise((resolve, reject) => {
      storageRequests.set(request, { resolve, reject });
      try {
        send("storage", { request, operation, key, value, scope });
      } catch (error) {
        storageRequests.delete(request);
        reject(new Error("Storage values must be JSON-compatible"));
      }
    });
  }

  function scheduled(handler, delay, repeat) {
    if (typeof handler !== "function") throw new TypeError("A timer callback is required");
    const wait = Math.max(0, Math.min(3600000, Number(delay) || 0));
    let active = true;
    const invoke = () => {
      if (!active || stopped) return;
      Promise.resolve().then(handler).catch(fail);
      if (!repeat) active = false;
    };
    const timer = repeat ? setInterval(invoke, Math.max(10, wait)) : setTimeout(invoke, wait);
    const cancel = () => {
      if (!active) return;
      active = false;
      repeat ? clearInterval(timer) : clearTimeout(timer);
    };
    cleanups.push(cancel);
    return cancel;
  }

  const api = Object.freeze({
    text(content, settings) {
      const opts = options(settings);
      return identified({ type: "text", text: String(content), tone: opts.tone, emphasis: Boolean(opts.emphasis) }, opts);
    },
    title(content, settings) { return identified({ type: "title", text: String(content) }, settings); },
    heading(content, level = 2, settings) { return identified({ type: "heading", text: String(content), level }, settings); },
    button(label, onClick, settings) {
      const opts = options(settings);
      return identified({ type: "button", label: String(label), action: action(onClick), variant: opts.variant, disabled: Boolean(opts.disabled) }, opts);
    },
    menu(label, items, onSelect, settings) {
      return identified({
        type: "menu", label: String(label || "Choose an option"), action: action(onSelect),
        items: (Array.isArray(items) ? items : []).map((item) => {
          if (item && typeof item === "object") return { label: String(item.label), value: String(item.value), disabled: Boolean(item.disabled) };
          return { label: String(item), value: String(item) };
        })
      }, settings);
    },
    input(name, label, settings) {
      const opts = options(settings);
      return identified({ type: "input", name: String(name), label: String(label), placeholder: String(opts.placeholder || ""), value: String(opts.value || ""), maxLength: opts.maxLength, submitAction: action(opts.onSubmit) }, opts);
    },
    numberInput(name, label, settings) {
      const opts = options(settings);
      return identified({ type: "number", name: String(name), label: String(label), value: opts.value, min: opts.min, max: opts.max, step: opts.step, submitAction: action(opts.onSubmit) }, opts);
    },
    textarea(name, label, settings) {
      const opts = options(settings);
      return identified({ type: "textarea", name: String(name), label: String(label), placeholder: String(opts.placeholder || ""), value: String(opts.value || ""), maxLength: opts.maxLength }, opts);
    },
    select(name, label, items, settings) {
      const opts = options(settings);
      return identified({ type: "select", name: String(name), label: String(label), value: opts.value, action: action(opts.onChange), items: (Array.isArray(items) ? items : []).map((item) => item && typeof item === "object" ? { label: String(item.label), value: String(item.value) } : { label: String(item), value: String(item) }) }, opts);
    },
    checkbox(name, label, settings) {
      const opts = options(settings);
      return identified({ type: "checkbox", name: String(name), label: String(label), checked: Boolean(opts.checked), action: action(opts.onChange) }, opts);
    },
    list(items, settings) {
      return identified({ type: "list", items: (Array.isArray(items) ? items : []).map(String), ordered: Boolean(options(settings).ordered) }, settings);
    },
    table(headers, rows, settings) {
      return identified({ type: "table", headers: (Array.isArray(headers) ? headers : []).map(String), rows: (Array.isArray(rows) ? rows : []).map((row) => (Array.isArray(row) ? row : []).map(String)) }, settings);
    },
    preformatted(content, settings) { return identified({ type: "pre", text: String(content) }, settings); },
    grid(rows, settings) { return identified({ type: "grid", rows: (Array.isArray(rows) ? rows : []).map(String) }, settings); },
    render(...components) {
      send("render", { mode: "replace", components: components.length === 1 && Array.isArray(components[0]) ? components[0] : components });
    },
    append(...components) {
      const value = components.length === 1 && Array.isArray(components[0]) ? components[0] : components;
      send("render", { mode: "append", components: value });
    },
    update(id, component) { send("render", { mode: "update", target: String(id), components: [component] }); },
    remove(id) { send("render", { mode: "remove", target: String(id), components: [] }); },
    clear() { send("render", { mode: "replace", components: [] }); },
    onKey(handler) {
      const id = action(handler);
      send("keyboard", { action: id, enabled: true });
      return () => { actions.delete(id); send("keyboard", { action: id, enabled: false }); };
    },
    onCleanup(handler) { if (typeof handler === "function") cleanups.push(handler); },
    timeout(handler, delay) { return scheduled(handler, delay, false); },
    interval(handler, delay) { return scheduled(handler, delay, true); },
    storage: Object.freeze({
      set(key, value, settings) { return storage("set", String(key), value, settings); },
      get(key, settings) { return storage("get", String(key), undefined, settings); },
      delete(key, settings) { return storage("delete", String(key), undefined, settings); },
      keys(settings) { return storage("keys", undefined, undefined, settings); }
    }),
    start(handler) {
      if (typeof handler !== "function") throw new TypeError("StationNet.start expects a function");
      Promise.resolve().then(() => handler(api)).catch(fail);
    },
    terminate() { shutdown(); }
  });

  async function dispatch(message) {
    if (!message || message.protocol !== PROTOCOL) return;
    if (message.type === "terminate") return shutdown();
    if (message.type === "storage-result") {
      const pending = storageRequests.get(message.request);
      if (!pending) return;
      storageRequests.delete(message.request);
      message.ok ? pending.resolve(message.value) : pending.reject(new Error(message.error || "Storage request failed"));
      return;
    }
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
    storageRequests.forEach(({ reject }) => reject(new Error("Application terminated")));
    storageRequests.clear();
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
