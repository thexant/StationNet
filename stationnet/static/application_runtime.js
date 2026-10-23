"use strict";

// Public StationNet application API. This file runs only inside the sandboxed
// iframe; applications communicate with the host through this small protocol.
(() => {
  const PROTOCOL = "stationnet-app-v1";
  const actions = new Map();
  const cleanups = [];
  const storageRequests = new Map();
  const scoreRequests = new Map();
  const capabilityRequests = new Map();
  let nextAction = 1;
  let nextRequest = 1;
  let messagesSent = 0;
  let stopped = false;

  function send(type, payload = {}) {
    if (stopped || ++messagesSent > 1000000) return;
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

  function capability(operation, payload) {
    const request = `capability-${nextRequest++}`;
    return new Promise((resolve, reject) => {
      capabilityRequests.set(request, { resolve, reject });
      send("capability", { request, operation, ...payload });
    });
  }

  function scores(operation, score) {
    const request = `score-${nextRequest++}`;
    return new Promise((resolve, reject) => {
      scoreRequests.set(request, { resolve, reject });
      send("scores", { request, operation, score });
    });
  }

  function sound(tones, settings) {
    const opts = options(settings);
    send("sound", {
      operation: "play", tones: Array.isArray(tones) ? tones : [tones],
      duration: opts.duration, delay: opts.delay, waveform: opts.waveform,
      volume: opts.volume, attack: opts.attack, release: opts.release
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
      return identified({ type: "button", label: String(label), action: action(onClick), pressAction: action(opts.onPress), releaseAction: action(opts.onRelease), variant: opts.variant, disabled: Boolean(opts.disabled) }, opts);
    },
    menu(label, items, onSelect, settings) {
      const opts = options(settings);
      return identified({
        type: "menu", label: String(label || "Choose an option"), action: action(onSelect), pressAction: action(opts.onPress), releaseAction: action(opts.onRelease),
        layout: opts.layout === "dpad" ? "dpad" : undefined,
        items: (Array.isArray(items) ? items : []).map((item) => {
          if (item && typeof item === "object") return { label: String(item.label), value: String(item.value), disabled: Boolean(item.disabled) };
          return { label: String(item), value: String(item) };
        })
      }, opts);
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
    cellGrid(rows, settings) {
      const opts = options(settings);
      return identified({
        type: "cell-grid", action: action(opts.onCell), cursor: opts.cursor,
        brush: opts.brush, label: String(opts.label || "Character art canvas"),
        rows: Array.isArray(rows) ? rows : []
      }, opts);
    },
    canvas(width, height, commands, settings) {
      const opts = options(settings);
      return identified({
        type: "canvas", width, height, commands: Array.isArray(commands) ? commands : [],
        action: action(opts.onPointer), hover: Boolean(opts.hover), label: String(opts.label || "Game playfield"),
        controls: Array.isArray(opts.controls) ? opts.controls.slice(0, 4).map((control) => ({
          label: String(control && control.label || "Action"), action: action(control && control.onClick), pressAction: action(control && control.onPress), releaseAction: action(control && control.onRelease),
          variant: control && control.variant === "secondary" ? "secondary" : undefined
        })) : undefined
      }, opts);
    },
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
    onVisibility(handler) {
      const id = action(handler);
      send("lifecycle", { action: id, enabled: true });
      return () => { actions.delete(id); send("lifecycle", { action: id, enabled: false }); };
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
    scores: Object.freeze({
      submit(score) { return scores("submit", score); },
      leaderboard() { return scores("list"); }
    }),
    sound: Object.freeze({
      // Play one synthesized oscillator. Frequency is in hertz; duration and
      // envelope values are milliseconds. No audio files are accepted.
      playTone(frequency, duration = 120, settings) {
        if (duration && typeof duration === "object") { settings = duration; duration = 120; }
        const opts = options(settings);
        sound({ frequency, duration }, opts);
      },
      // Play tones together. Entries may be frequencies or tone objects, so
      // equal delays make a chord and different delays make a pattern.
      play(tones, settings) { sound(tones, settings); },
      // Schedule tones one after another. Give an entry a delay to position it
      // explicitly instead (which also permits chords within a sequence).
      sequence(tones, settings) {
        const opts = options(settings);
        const defaultDuration = Number(opts.duration) || 120;
        let cursor = Number(opts.delay) || 0;
        const events = (Array.isArray(tones) ? tones : []).map((tone) => {
          const event = tone && typeof tone === "object" ? { ...tone } : { frequency: tone };
          const duration = Number(event.duration == null ? defaultDuration : event.duration) || defaultDuration;
          if (event.delay == null) { event.delay = cursor; cursor += Math.max(0, duration); }
          return event;
        });
        sound(events, opts);
      },
      stop() { send("sound", { operation: "stop" }); }
    }),
    clipboard: Object.freeze({
      writeText(value) { return capability("copy-text", { text: String(value) }); }
    }),
    download: Object.freeze({
      text(filename, value) { return capability("download-text", { filename: String(filename), text: String(value) }); },
      cellImage(filename, rows, settings) {
        const opts = options(settings);
        return capability("download-cell-image", {
          filename: String(filename), rows: Array.isArray(rows) ? rows : [],
          cellWidth: opts.cellWidth, cellHeight: opts.cellHeight, fontSize: opts.fontSize
        });
      }
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
    if (message.type === "scores-result") {
      const pending = scoreRequests.get(message.request);
      if (!pending) return;
      scoreRequests.delete(message.request);
      message.ok ? pending.resolve(message.value) : pending.reject(new Error(message.error || "Score request failed"));
      return;
    }
    if (message.type === "capability-result") {
      const pending = capabilityRequests.get(message.request);
      if (!pending) return;
      capabilityRequests.delete(message.request);
      message.ok ? pending.resolve(message.value) : pending.reject(new Error(message.error || "Capability request failed"));
      return;
    }
    if (message.type !== "interaction") return;
    const handler = actions.get(message.action);
    if (!handler) return;
    try {
      await handler(Object.freeze({
        value: message.value,
        values: Object.freeze(message.values || {}),
        key: message.key, code: message.code,
        ctrlKey: Boolean(message.ctrlKey), altKey: Boolean(message.altKey),
        shiftKey: Boolean(message.shiftKey), metaKey: Boolean(message.metaKey),
        x: message.x, y: message.y, drag: Boolean(message.drag), phase: message.phase, repeat: Boolean(message.repeat)
      }));
    } catch (error) { fail(error); }
  }

  function shutdown() {
    if (stopped) return;
    cleanups.splice(0).forEach((handler) => { try { handler(); } catch (_) {} });
    storageRequests.forEach(({ reject }) => reject(new Error("Application terminated")));
    storageRequests.clear();
    scoreRequests.forEach(({ reject }) => reject(new Error("Application terminated")));
    scoreRequests.clear();
    capabilityRequests.forEach(({ reject }) => reject(new Error("Application terminated")));
    capabilityRequests.clear();
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
