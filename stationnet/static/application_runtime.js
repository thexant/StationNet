"use strict";

// Public StationNet application API. This file runs only inside the sandboxed
// iframe; applications communicate with the host through this small protocol.
(() => {
  const PROTOCOL = "stationnet-app-v1";
  const actions = new Map();
  const animations = new Map();
  const cleanups = [];
  const storageRequests = new Map();
  const multiplayerRequests = new Map();
  const multiplayerHandlers = new Set();
  const realtimeHandlers = new Set();
  const syncStatusHandlers = new Set();
  const synchronizers = new Set();
  const realtimeRequests = new Map();
  let realtimeConnected = false;
  let multiplayerSession = null, multiplayerCursor = 0, multiplayerTimer = null;
  const scoreRequests = new Map();
  const capabilityRequests = new Map();
  const pointerLockChangeHandlers = new Set();
  const pointerLockMoveHandlers = new Set();
  const presentationHandlers = new Set();
  let pointerLockActive = false;
  let presentationMode = "standard";
  const packageFiles = self.__stationnetPackageFiles;
  delete self.__stationnetPackageFiles;
  const moduleCache = new Map();
  const registeredImages = new Set();
  const decodedImages = new Map();
  const framebuffers = new Map();
  let framebufferPixels = 0;
  let nextAction = 1;
  let nextRequest = 1;
  let messagesSent = 0;
  let stopped = false;

  function bundledBytes(path) {
    if (typeof path !== "string" || !Object.prototype.hasOwnProperty.call(packageFiles, path)) {
      throw new Error(`Bundled file not found: ${String(path)}`);
    }
    return Uint8Array.from(atob(packageFiles[path]), (character) => character.charCodeAt(0));
  }

  function bundledText(path) {
    return new TextDecoder("utf-8", { fatal: true }).decode(bundledBytes(path));
  }

  function image(path) {
    if (typeof path !== "string" || !/\.(?:png|jpe?g|gif|webp)$/i.test(path))
      throw new Error("Image must be a bundled PNG, JPEG, GIF, or WebP file");
    if (!registeredImages.has(path)) {
      const bytes = bundledBytes(path);
      registeredImages.add(path);
      send("graphics-image", { path, data: packageFiles[path] });
    }
    return path;
  }

  function decodeImage(path) {
    if (typeof path !== "string" || !/\.png$/i.test(path))
      return Promise.reject(new Error("Decoded image must be a bundled PNG"));
    try { bundledBytes(path); }
    catch (error) { return Promise.reject(error); }
    if (!decodedImages.has(path)) {
      const request = `image-${nextRequest++}`;
      const pending = new Promise((resolve, reject) => {
        decodedImages.set(path, { request, resolve, reject, promise: null });
        send("graphics-decode-image", { request, path, data: packageFiles[path] });
      });
      decodedImages.get(path).promise = pending;
    }
    return decodedImages.get(path).promise.then((value) => ({ width: value.width, height: value.height,
      pixels: value.pixels.slice(), ...(value.palette ? { palette: value.palette.slice() } : {}) }));
  }

  function sprite(source, frame, x, y, settings) {
    const opts = options(settings);
    const sheet = source && typeof source === "object" ? source : null;
    if (!sheet) throw new TypeError("sprite requires a sheet descriptor");
    const index = Math.max(0, Math.trunc(Number(frame) || 0));
    const columns = Math.max(1, Math.trunc(Number(sheet.columns) || 1));
    return { type: "image", image: sheet.image, x, y,
      sx: (index % columns) * sheet.frameWidth, sy: Math.floor(index / columns) * sheet.frameHeight,
      sw: sheet.frameWidth, sh: sheet.frameHeight, width: opts.width ?? sheet.frameWidth,
      height: opts.height ?? sheet.frameHeight, flipX: opts.flipX, flipY: opts.flipY, opacity: opts.opacity };
  }

  const builtinFonts = Object.freeze({
    vga8x8: Object.freeze({ sheet: "vga8x8", charWidth: 8, charHeight: 8, firstChar: 32 }),
    vga8x16: Object.freeze({ sheet: "vga8x16", charWidth: 8, charHeight: 16, firstChar: 32 })
  });

  function bitmapFont(sheet, settings) {
    const opts = options(settings);
    if (typeof sheet !== "string" || !sheet || sheet.length > 200 ||
        !Number.isInteger(opts.charWidth) || opts.charWidth < 1 || opts.charWidth > 64 ||
        !Number.isInteger(opts.charHeight) || opts.charHeight < 1 || opts.charHeight > 64 ||
        (opts.firstChar != null && (!Number.isInteger(opts.firstChar) || opts.firstChar < 0 || opts.firstChar > 65535)))
      throw new RangeError("Bitmap font requires a sheet and 1-64 pixel character dimensions");
    if (Object.prototype.hasOwnProperty.call(builtinFonts, sheet) &&
        (opts.charWidth !== 8 || opts.charHeight !== builtinFonts[sheet].charHeight ||
        (opts.firstChar ?? 32) !== 32))
      throw new RangeError("Built-in font dimensions and firstChar are fixed");
    return Object.freeze({ sheet, charWidth: opts.charWidth, charHeight: opts.charHeight,
      firstChar: opts.firstChar ?? 32 });
  }

  function measureBitmapText(value, font) {
    if (!font || !Number.isInteger(font.charWidth) || font.charWidth < 1 || font.charWidth > 64 ||
        !Number.isInteger(font.charHeight) || font.charHeight < 1 || font.charHeight > 64)
      throw new TypeError("measureText requires a bitmap font descriptor");
    const lines = String(value).slice(0, 200).split(/\r\n|\r|\n/);
    return { width: Math.max(...lines.map(line => Array.from(line).length)) * font.charWidth,
      height: lines.length * font.charHeight };
  }

  function loadModule(path) {
    if (typeof path !== "string" || !path.endsWith(".js")) {
      return Promise.reject(new Error("Module path must name a bundled .js file"));
    }
    if (!moduleCache.has(path)) {
      let source;
      try { source = bundledText(path); }
      catch (error) { return Promise.reject(error); }
      const url = URL.createObjectURL(new Blob([source], { type: "application/javascript" }));
      const pending = import(url).finally(() => URL.revokeObjectURL(url));
      moduleCache.set(path, pending);
    }
    return moduleCache.get(path);
  }

  function send(type, payload = {}, transfer) {
    if (stopped || ++messagesSent > 1000000) return;
    postMessage({ protocol: PROTOCOL, type, ...payload }, transfer || []);
  }

  function framebuffer(id, width, height, settings) {
    const opts = options(settings);
    if (typeof id !== "string" || !/^[A-Za-z0-9_.:-]{1,80}$/.test(id) || framebuffers.has(id))
      throw new Error("Framebuffer id must be unique and contain only letters, digits, _, ., :, or -");
    if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 ||
        width > 640 || height > 400 || width * height > 256000 ||
        framebuffers.size >= 4 || framebufferPixels + width * height > 512000)
      throw new RangeError("Framebuffer limit exceeded (4 buffers, 512000 total pixels, 640x400 maximum)");
    const format = opts.format === "indexed" ? "indexed" : "rgba";
    if (opts.format != null && opts.format !== format) throw new RangeError("Framebuffer format must be rgba or indexed");
    const pixels = new Uint8Array(width * height * (format === "rgba" ? 4 : 1));
    const palette = format === "indexed" ? new Uint8Array(256 * 4) : null;
    if (palette) for (let i = 0; i < 256; i++) palette[i * 4 + 3] = 255;
    const transparentIndex = opts.transparentIndex;
    if (transparentIndex != null && (format !== "indexed" || !Number.isInteger(transparentIndex) ||
        transparentIndex < 0 || transparentIndex > 255))
      throw new RangeError("transparentIndex requires an indexed buffer and a palette index from 0 to 255");
    if (transparentIndex != null) palette[transparentIndex * 4 + 3] = 0;
    const rgbaColor = (value) => {
      if (!value || typeof value !== "object" || value.length < 3 || value.length > 4 ||
          !Array.from(value).every((channel) => Number.isInteger(channel) && channel >= 0 && channel <= 255))
        throw new RangeError("RGBA color must contain three or four bytes");
      return [value[0], value[1], value[2], value[3] ?? 255];
    };
    const coordinate = (value) => Number.isInteger(value) && Math.abs(value) <= 1000000;
    const fillPixels = (value) => {
      if (format === "indexed") {
        if (!Number.isInteger(value) || value < 0 || value > 255) throw new RangeError("Invalid palette index");
        pixels.fill(value);
      } else {
        const color = rgbaColor(value);
        for (let offset = 0; offset < pixels.length; offset += 4) pixels.set(color, offset);
      }
    };
    const sourceInfo = (src) => {
      if (!src || !Number.isInteger(src.width) || !Number.isInteger(src.height) ||
          src.width < 1 || src.height < 1 || src.width > 2048 || src.height > 2048 ||
          src.width * src.height > 4194304 || !(src.pixels instanceof Uint8Array))
        throw new TypeError("blit source must be a framebuffer or decoded image");
      const sourceFormat = src.format === "indexed" ? "indexed" : "rgba";
      if (src.pixels.length !== src.width * src.height * (sourceFormat === "indexed" ? 1 : 4) ||
          (sourceFormat === "indexed" && !(src.palette instanceof Uint8Array && src.palette.length === 1024)))
        throw new TypeError("Invalid blit source pixels or palette");
      if (format === "indexed" && sourceFormat !== "indexed")
        throw new TypeError("Indexed destinations require an indexed source");
      return sourceFormat;
    };
    const buffer = {
      id, width, height, format, pixels, palette, transparentIndex: transparentIndex ?? null,
      clear(value = 0) {
        if (format === "rgba" && value === 0) pixels.fill(0);
        else fillPixels(value);
      },
      pixel(x, y, value) {
        if (!Number.isInteger(x) || !Number.isInteger(y) || x < 0 || y < 0 || x >= width || y >= height) return;
        if (format === "indexed") pixels[y * width + x] = value;
        else {
          const offset = (y * width + x) * 4;
          pixels[offset] = value[0]; pixels[offset + 1] = value[1];
          pixels[offset + 2] = value[2]; pixels[offset + 3] = value[3] ?? 255;
        }
      },
      fillRect(x, y, w, h, value) {
        const left = Math.max(0, Math.trunc(x)), top = Math.max(0, Math.trunc(y));
        const right = Math.min(width, Math.trunc(x + w)), bottom = Math.min(height, Math.trunc(y + h));
        if (![left, top, right, bottom].every(Number.isFinite) || right <= left || bottom <= top) return;
        if (format === "indexed") {
          for (let row = top; row < bottom; row++) pixels.fill(value, row * width + left, row * width + right);
        } else {
          const color = [value[0], value[1], value[2], value[3] ?? 255];
          for (let row = top; row < bottom; row++) for (let col = left; col < right; col++) pixels.set(color, (row * width + col) * 4);
        }
      },
      blit(src, sx, sy, sw, sh, dx, dy, settings) {
        if (![sx, sy, sw, sh, dx, dy].every(coordinate) || sw < 0 || sh < 0)
          throw new RangeError("Blit coordinates must be bounded integers and sizes nonnegative");
        const sourceFormat = sourceInfo(src);
        const opts = options(settings);
        const key = opts.colorKey;
        if (key != null && (sourceFormat === "indexed" ?
            !Number.isInteger(key) || key < 0 || key > 255 :
            !Array.isArray(key) || key.length < 3 || key.length > 4 ||
              !key.every((channel) => Number.isInteger(channel) && channel >= 0 && channel <= 255)))
          throw new RangeError("colorKey must be a palette index or RGB/RGBA byte array");
        // Clip in destination space. Source bounds are checked per sample to
        // keep flipped and partially out-of-bounds rectangles symmetric.
        const left = Math.max(0, -dx), top = Math.max(0, -dy);
        const right = Math.min(sw, width - dx), bottom = Math.min(sh, height - dy);
        if (right <= left || bottom <= top) return;
        const sourcePixels = src.pixels === pixels ? pixels.slice() : src.pixels;
        for (let y = top; y < bottom; y++) {
          const sourceY = sy + (opts.flipY ? sh - 1 - y : y);
          if (sourceY < 0 || sourceY >= src.height) continue;
          for (let x = left; x < right; x++) {
            const sourceX = sx + (opts.flipX ? sw - 1 - x : x);
            if (sourceX < 0 || sourceX >= src.width) continue;
            const sourceOffset = (sourceY * src.width + sourceX) * (sourceFormat === "rgba" ? 4 : 1);
            const destinationOffset = ((dy + y) * width + dx + x) * (format === "rgba" ? 4 : 1);
            if (sourceFormat === "indexed") {
              const index = sourcePixels[sourceOffset];
              if (index === key || index === src.transparentIndex) continue;
              if (format === "indexed") pixels[destinationOffset] = index;
              else pixels.set(src.palette.subarray(index * 4, index * 4 + 4), destinationOffset);
            } else {
              const red = sourcePixels[sourceOffset], green = sourcePixels[sourceOffset + 1];
              const blue = sourcePixels[sourceOffset + 2], alpha = sourcePixels[sourceOffset + 3];
              if (!alpha || key && red === key[0] && green === key[1] && blue === key[2] &&
                  (key.length === 3 || alpha === key[3])) continue;
              pixels.set(sourcePixels.subarray(sourceOffset, sourceOffset + 4), destinationOffset);
            }
          }
        }
      },
      copyFrom(src, dx = 0, dy = 0) {
        sourceInfo(src);
        this.blit(src, 0, 0, src.width, src.height, dx, dy);
      },
      scroll(dx, dy, fillValue = format === "indexed" ? 0 : [0, 0, 0, 0]) {
        if (!coordinate(dx) || !coordinate(dy)) throw new RangeError("Scroll offsets must be bounded integers");
        const old = pixels.slice();
        fillPixels(fillValue);
        const left = Math.max(0, dx), right = Math.min(width, width + dx);
        const top = Math.max(0, dy), bottom = Math.min(height, height + dy);
        if (right <= left || bottom <= top) return;
        const stride = format === "rgba" ? 4 : 1;
        for (let y = top; y < bottom; y++) {
          const from = ((y - dy) * width + left - dx) * stride;
          pixels.set(old.subarray(from, from + (right - left) * stride), (y * width + left) * stride);
        }
      },
      present(settings) {
        const paletteOnly = Boolean(options(settings).paletteOnly);
        if (paletteOnly && !palette) throw new Error("paletteOnly requires an indexed framebuffer");
        const data = paletteOnly ? null : pixels.slice();
        const colors = palette ? palette.slice() : null;
        send("graphics-frame", { id, width, height, format, pixels: data, palette: colors },
          data ? [data.buffer, ...(colors ? [colors.buffer] : [])] : [colors.buffer]);
      }
    };
    framebufferPixels += width * height;
    framebuffers.set(id, buffer);
    return Object.freeze(buffer);
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

  function multiplayer(operation, payload = {}) {
    const request = `multiplayer-${nextRequest++}`;
    return new Promise((resolve, reject) => {
      multiplayerRequests.set(request, { resolve, reject, operation });
      send("multiplayer", { request, operation, payload });
    });
  }

  function realtime(operation, payload = {}) {
    const request = `realtime-${nextRequest++}`;
    return new Promise((resolve, reject) => {
      realtimeRequests.set(request, { resolve, reject });
      send("realtime", { request, operation, ...payload });
    });
  }

  function interpolateState(a, b, blend) {
    if (typeof a === "number" && typeof b === "number" && Number.isFinite(a) && Number.isFinite(b))
      return a + (b - a) * blend;
    if (Array.isArray(a) && Array.isArray(b) && a.length === b.length)
      return a.map((value, index) => interpolateState(value, b[index], blend));
    if (a && b && typeof a === "object" && typeof b === "object" && !Array.isArray(a) && !Array.isArray(b)) {
      const result = { ...b };
      for (const key of Object.keys(b)) if (Object.hasOwn(a, key)) result[key] = interpolateState(a[key], b[key], blend);
      return result;
    }
    return blend < 1 ? a : b;
  }

  function createSynchronization(settings = {}) {
    const tickRate = settings.tickRate ?? 20;
    const delay = settings.interpolationDelay ?? 100;
    if (!Number.isInteger(tickRate) || tickRate < 1 || tickRate > 30) throw new RangeError("tickRate must be 1-30");
    if (!Number.isFinite(delay) || delay < 0 || delay > 1000) throw new RangeError("interpolationDelay must be 0-1000 ms");
    let timer = null, unsubscribe = null, reconnectTimer = null, active = false;
    let epoch = 0, tick = -1, hostId = null;
    let state = null, inputId = 0, localPlayerId = null;
    let inputs = [], samples = [];
    const notify = (name, ...args) => {
      if (typeof settings[name] === "function") Promise.resolve().then(() => settings[name](...args)).catch(fail);
    };
    const receive = (packet) => {
      if (!packet) return;
      if (packet.type === "host" || packet.type === "sync") {
        if (packet.epoch < epoch) return;
        if (packet.epoch !== epoch) { inputs = []; samples = []; tick = -1; }
        epoch = packet.epoch;
        hostId = packet.host_id || packet.player_id;
        if (packet.state !== null && packet.state !== undefined) {
          state = packet.state;
          tick = packet.tick ?? tick;
          samples = [{ at: Date.now(), state }];
          notify("onState", state, packet);
        }
        notify("onHost", hostId, packet);
      } else if (packet.type === "input" && hostId === localPlayerId && packet.epoch === epoch) {
        if (typeof settings.validateInput !== "function" || settings.validateInput(packet.data, packet.player, packet)) {
          if (inputs.length < 256) inputs.push(packet);
          notify("onInput", packet);
        }
      } else if (packet.type === "state" && packet.epoch === epoch && packet.tick > tick) {
        tick = packet.tick; state = packet.data;
        samples.push({ at: Date.now(), state });
        if (samples.length > 2) samples.shift();
        notify("onState", state, packet);
      } else if (packet.type === "presence") notify("onPresence", packet);
    };
    const reconnect = async () => {
      reconnectTimer = null;
      if (!active || !multiplayerSession) return;
      try {
        const connected = await realtime("connect", { session_id: multiplayerSession, max_rate: settings.maxRate ?? 30 });
        if (!active) return;
        receive(connected.sync);
        notify("onReconnect", connected.sync);
      } catch (_) {
        if (active && reconnectTimer === null) reconnectTimer = setTimeout(reconnect, 2000);
      }
    };
    const status = (connected) => {
      if (connected || !active || reconnectTimer !== null) return;
      notify("onDisconnect");
      reconnectTimer = setTimeout(() => { reconnectTimer = null; reconnect(); }, 2000);
    };
    const controller = Object.freeze({
      async start(playerId) {
        if (active) return;
        if (!multiplayerSession) throw new Error("Join a multiplayer session first");
        active = true;
        synchronizers.add(controller);
        localPlayerId = String(playerId);
        unsubscribe = (packet) => receive(packet);
        realtimeHandlers.add(unsubscribe);
        syncStatusHandlers.add(status);
        try {
          const connected = await realtime("connect", { session_id: multiplayerSession, max_rate: settings.maxRate ?? 30 });
          receive(connected.sync);
          timer = setInterval(() => {
            if (!realtimeConnected) return;
            const host = hostId === localPlayerId;
            const pending = host ? inputs.splice(0) : [];
            if (typeof settings.onTick !== "function") return;
            try {
              const next = settings.onTick({ state, inputs: pending, dt: 1 / tickRate,
                tick: tick + 1, isHost: host, epoch });
              if (host && next !== undefined) {
                tick += 1; state = next;
                samples = [{ at: Date.now(), state }];
                realtime("send", { session_id: multiplayerSession, kind: "state", tick, epoch, data: state }).catch(fail);
              }
            } catch (error) { fail(error); }
          }, 1000 / tickRate);
        } catch (error) {
          active = false;
          synchronizers.delete(controller);
          realtimeHandlers.delete(unsubscribe); unsubscribe = null;
          syncStatusHandlers.delete(status);
          throw error;
        }
      },
      input(data) {
        if (timer === null || !realtimeConnected) return Promise.reject(new Error("Synchronization is disconnected"));
        inputId += 1;
        if (hostId === localPlayerId) {
          const packet = { type: "input", input_id: inputId, data,
            player: { id: localPlayerId }, epoch };
          receive(packet);
          return Promise.resolve({ queued: true });
        }
        if (typeof settings.predict === "function") {
          try {
            state = settings.predict(state, data);
            samples = [{ at: Date.now(), state }];
          } catch (error) { fail(error); }
        }
        return realtime("send", { session_id: multiplayerSession, kind: "input", input_id: inputId, data });
      },
      sample() {
        if (samples.length < 2) return state;
        const [a, b] = samples;
        const blend = Math.max(0, Math.min(1, (Date.now() - delay - a.at) / Math.max(1, b.at - a.at)));
        return interpolateState(a.state, b.state, blend);
      },
      get state() { return state; },
      get isHost() { return hostId === localPlayerId; },
      get epoch() { return epoch; },
      resync() { return realtime("send", { session_id: multiplayerSession, kind: "resync" }); },
      stop() {
        active = false;
        if (timer !== null) clearInterval(timer);
        timer = null;
        if (reconnectTimer !== null) clearTimeout(reconnectTimer);
        reconnectTimer = null;
        if (unsubscribe) realtimeHandlers.delete(unsubscribe);
        unsubscribe = null;
        syncStatusHandlers.delete(status);
        synchronizers.delete(controller);
        return realtime("disconnect");
      }
    });
    return controller;
  }

  function stopMultiplayerPolling() {
    if (multiplayerTimer != null) clearInterval(multiplayerTimer);
    multiplayerTimer = null;
  }

  function startMultiplayerPolling() {
    if (multiplayerTimer != null || !multiplayerSession) return;
    multiplayerTimer = setInterval(async () => {
      if (!multiplayerSession || stopped) return;
      try {
        const value = await multiplayer("poll", { session_id: multiplayerSession, after: multiplayerCursor });
        multiplayerCursor = value.cursor;
        for (const event of value.events || []) {
          for (const handler of multiplayerHandlers) Promise.resolve().then(() => handler(Object.freeze(event), value)).catch(fail);
        }
      } catch (_) { /* transient polling failures are retried */ }
    }, 500);
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

  function animate(handler) {
    if (typeof handler !== "function") throw new TypeError("An animation callback is required");
    const id = `animation-${nextAction++}`;
    let active = true, paused = false;
    animations.set(id, handler);
    send("animation", { action: id, enabled: true });

    const cancel = () => {
      if (!active) return;
      active = false; paused = false; animations.delete(id);
      send("animation", { action: id, enabled: false });
    };
    cancel.pause = () => {
      if (!active || paused) return;
      paused = true;
      send("animation", { action: id, enabled: false });
    };
    cancel.resume = () => {
      if (!active || !paused || stopped) return;
      paused = false;
      send("animation", { action: id, enabled: true });
    };
    Object.defineProperty(cancel, "paused", { get: () => paused });
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
    dateInput(name, label, settings) {
      const opts = options(settings);
      return identified({ type: "date", name: String(name), label: String(label), value: String(opts.value || "") }, opts);
    },
    timeInput(name, label, settings) {
      const opts = options(settings);
      return identified({ type: "time", name: String(name), label: String(label), value: String(opts.value || "") }, opts);
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
    panel(label, components, settings) {
      return identified({ type: "panel", label: String(label), components: Array.isArray(components) ? components : [] }, settings);
    },
    calendar(month, days, onSelect, settings) {
      return identified({ type: "calendar", month: String(month), action: action(onSelect),
        days: (Array.isArray(days) ? days : []).slice(0, 42).map((day) => ({
          date: String(day.date || ""), number: Number(day.number) || 0,
          outside: Boolean(day.outside), today: Boolean(day.today), selected: Boolean(day.selected),
          events: (Array.isArray(day.events) ? day.events : []).slice(0, 2).map(String),
          extra: Math.max(0, Number(day.extra) || 0)
        })) }, settings);
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
    graphics: Object.freeze({
      framebuffer,
      frame(id, x, y, width, height, settings) {
        const opts = options(settings);
        return { type: "framebuffer", buffer: id && typeof id === "object" ? id.id : id,
          x, y, width, height, opacity: opts.opacity };
      },
      image(source, x, y, settings) {
        const opts = options(settings);
        return { type: "image", image: source, x, y, sx: opts.sx, sy: opts.sy,
          sw: opts.sw, sh: opts.sh, width: opts.width, height: opts.height,
          flipX: opts.flipX, flipY: opts.flipY, opacity: opts.opacity };
      },
      sheet(source, frameWidth, frameHeight, settings) {
        const opts = options(settings);
        if (!Number.isInteger(frameWidth) || frameWidth < 1 || !Number.isInteger(frameHeight) || frameHeight < 1)
          throw new RangeError("Sheet frame dimensions must be positive integers");
        return Object.freeze({ image: source, frameWidth, frameHeight,
          columns: Math.max(1, Math.trunc(Number(opts.columns) || 1)) });
      },
      sprite,
      sprites(entries) {
        if (!Array.isArray(entries)) throw new TypeError("sprites requires an array");
        if (entries.length > 2000) throw new RangeError("A sprites command may contain at most 2000 entries");
        return { type: "sprites", sprites: entries };
      },
      fonts: builtinFonts,
      bitmapFont,
      bitmapText(content, font, x, y, settings) {
        const opts = options(settings);
        return { type: "bitmapText", text: String(content).slice(0, 200), font, x, y,
          color: opts.color, opacity: opts.opacity };
      },
      measureText: measureBitmapText,
      tileMap(id, tiles, settings) {
        const opts = options(settings);
        if (typeof id !== "string" || !/^[A-Za-z0-9_.:-]{1,80}$/.test(id)) throw new Error("Invalid tile map id");
        if (!Array.isArray(tiles) || !Number.isInteger(opts.columns) || opts.columns < 1 ||
            tiles.length < 1 || tiles.length > 262144 || tiles.length % opts.columns)
          throw new RangeError("Tile map must be a rectangular array of at most 262144 cells");
        send("graphics-map", { id, tiles, columns: opts.columns, image: opts.image,
          tileWidth: opts.tileWidth, tileHeight: opts.tileHeight, sheetColumns: opts.sheetColumns });
        return id;
      },
      tileLayer(id, x, y, width, height, settings) {
        const opts = options(settings);
        return { type: "tilemap", map: id, x, y, width, height,
          cameraX: opts.cameraX || 0, cameraY: opts.cameraY || 0, opacity: opts.opacity };
      }
    }),
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
    animate,
    storage: Object.freeze({
      set(key, value, settings) { return storage("set", String(key), value, settings); },
      get(key, settings) { return storage("get", String(key), undefined, settings); },
      delete(key, settings) { return storage("delete", String(key), undefined, settings); },
      keys(settings) { return storage("keys", undefined, undefined, settings); }
    }),
    assets: Object.freeze({
      bytes(path) { return bundledBytes(path); },
      text(path) { return bundledText(path); },
      json(path) { return JSON.parse(bundledText(path)); },
      image, decodeImage
    }),
    modules: Object.freeze({ load: loadModule }),
    scores: Object.freeze({
      submit(score) { return scores("submit", score); },
      leaderboard() { return scores("list"); }
    }),
    multiplayer: Object.freeze({
      async create(settings = {}) {
        for (const sync of [...synchronizers]) await sync.stop();
        if (multiplayerSession) await realtime("disconnect");
        const value = await multiplayer("create", { name: settings.name, max_players: settings.maxPlayers, visibility: settings.visibility, state: settings.state });
        multiplayerSession = value.session.id; multiplayerCursor = 0; startMultiplayerPolling(); return value;
      },
      discover() { return multiplayer("discover"); },
      async join(sessionId, settings = {}) {
        for (const sync of [...synchronizers]) await sync.stop();
        if (multiplayerSession) await realtime("disconnect");
        const value = await multiplayer("join", { session_id: String(sessionId), join_code: settings.joinCode });
        multiplayerSession = value.session.id; multiplayerCursor = 0; startMultiplayerPolling(); return value;
      },
      async leave() {
        if (!multiplayerSession) return { ok: true, left: false };
        for (const sync of [...synchronizers]) await sync.stop();
        const id = multiplayerSession; multiplayerSession = null; stopMultiplayerPolling();
        await realtime("disconnect");
        return multiplayer("leave", { session_id: id });
      },
      send(event, data) {
        if (!multiplayerSession) return Promise.reject(new Error("Join a multiplayer session first"));
        return multiplayer("send", { session_id: multiplayerSession, event: String(event), data });
      },
      async snapshot() {
        if (!multiplayerSession) return Promise.reject(new Error("Join a multiplayer session first"));
        const value = await multiplayer("snapshot", { session_id: multiplayerSession, after: multiplayerCursor });
        multiplayerCursor = value.cursor; return value;
      },
      setState(state, settings = {}) {
        if (!multiplayerSession) return Promise.reject(new Error("Join a multiplayer session first"));
        return multiplayer("set_state", { session_id: multiplayerSession, state, expected_revision: settings.expectedRevision });
      },
      onEvent(handler) {
        if (typeof handler !== "function") throw new TypeError("A multiplayer event handler is required");
        multiplayerHandlers.add(handler); return () => multiplayerHandlers.delete(handler);
      },
      connectRealtime(settings = {}) {
        if (!multiplayerSession) return Promise.reject(new Error("Join a multiplayer session first"));
        return realtime("connect", { session_id: multiplayerSession, max_rate: settings.maxRate ?? 30 });
      },
      sendRealtime(event, data) {
        if (!multiplayerSession) return Promise.reject(new Error("Join a multiplayer session first"));
        return realtime("send", { session_id: multiplayerSession, event: String(event), data });
      },
      createSync: createSynchronization,
      disconnectRealtime() { return realtime("disconnect"); },
      onRealtimeEvent(handler) {
        if (typeof handler !== "function") throw new TypeError("A real-time event handler is required");
        realtimeHandlers.add(handler); return () => realtimeHandlers.delete(handler);
      }
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
    pointerLock: Object.freeze({
      request(settings) {
        const opts = options(settings);
        return capability("pointer-lock-request", { unadjustedMovement: Boolean(opts.unadjustedMovement) });
      },
      release() { return capability("pointer-lock-release", {}); },
      get active() { return pointerLockActive; },
      onChange(handler) {
        if (typeof handler !== "function") throw new TypeError("A pointer lock change handler is required");
        pointerLockChangeHandlers.add(handler);
        return () => pointerLockChangeHandlers.delete(handler);
      },
      onMove(handler) {
        if (typeof handler !== "function") throw new TypeError("A relative pointer movement handler is required");
        pointerLockMoveHandlers.add(handler);
        return () => pointerLockMoveHandlers.delete(handler);
      }
    }),
    presentation: Object.freeze({
      enterImmersive() { return capability("presentation-enter-immersive", {}); },
      exitImmersive() { return capability("presentation-exit-immersive", {}); },
      get mode() { return presentationMode; },
      onChange(handler) {
        if (typeof handler !== "function") throw new TypeError("A presentation change handler is required");
        presentationHandlers.add(handler);
        return () => presentationHandlers.delete(handler);
      }
    }),
    clipboard: Object.freeze({
      writeText(value) { return capability("copy-text", { text: String(value) }); }
    }),
    download: Object.freeze({
      text(filename, value) { return capability("download-text", { filename: String(filename), text: String(value) }); },
      binary(filename, bytes, mime = "application/octet-stream") {
        return capability("download-binary", { filename: String(filename), bytes: Array.from(bytes), mime: String(mime) });
      },
      cellImage(filename, rows, settings) {
        const opts = options(settings);
        return capability("download-cell-image", {
          filename: String(filename), rows: Array.isArray(rows) ? rows : [],
          cellWidth: opts.cellWidth, cellHeight: opts.cellHeight, fontSize: opts.fontSize
        });
      }
    }),
    files: Object.freeze({
      open(settings) { return capability("open-binary", { accept: String(options(settings).accept || "") }); }
    }),
    display: Object.freeze({ viewport() { return capability("viewport", {}); } }),
    midi: Object.freeze({
      load(bytes) { return capability("midi-load", { bytes: Array.from(bytes) }); },
      play() { return capability("midi-play", {}); },
      pause() { return capability("midi-pause", {}); },
      stop() { return capability("midi-stop", {}); },
      seek(seconds) { return capability("midi-seek", { seconds: Number(seconds) }); },
      position() { return capability("midi-position", {}); }
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
    if (message.type === "pointer-lock-event") {
      pointerLockActive = Boolean(message.active);
      pointerLockChangeHandlers.forEach((handler) => Promise.resolve().then(() => handler(pointerLockActive)).catch(fail));
      return;
    }
    if (message.type === "pointer-lock-move") {
      if (!pointerLockActive) return;
      const movement = Object.freeze({ x: Number(message.movementX) || 0, y: Number(message.movementY) || 0 });
      pointerLockMoveHandlers.forEach((handler) => Promise.resolve().then(() => handler(movement)).catch(fail));
      return;
    }
    if (message.type === "presentation-event") {
      presentationMode = message.mode === "immersive" ? "immersive" : "standard";
      presentationHandlers.forEach((handler) => Promise.resolve().then(() => handler(presentationMode)).catch(fail));
      return;
    }
    if (message.type === "animation-frame") {
      const handler = animations.get(message.action);
      try {
        if (handler) await handler(Object.freeze({
          time: Number(message.time) || 0,
          delta: Math.max(0, Number(message.delta) || 0)
        }));
      } catch (error) { fail(error); }
      // This acknowledgement is runtime flow control, not an application
      // event, and is consumed by the trusted relay rather than the host.
      finally {
        if (!stopped) postMessage({ protocol: PROTOCOL, type: "animation-ready", action: String(message.action || "") });
      }
      return;
    }
    if (message.type === "storage-result") {
      const pending = storageRequests.get(message.request);
      if (!pending) return;
      storageRequests.delete(message.request);
      message.ok ? pending.resolve(message.value) : pending.reject(new Error(message.error || "Storage request failed"));
      return;
    }
    if (message.type === "graphics-decode-image-result") {
      const pending = decodedImages.get(message.path);
      if (!pending || pending.request !== message.request) return;
      if (message.ok && Number.isInteger(message.width) && Number.isInteger(message.height) &&
          message.width > 0 && message.height > 0 && message.width <= 2048 && message.height <= 2048 &&
          message.width * message.height <= 4194304 && message.pixels instanceof Uint8Array &&
          message.pixels.length === message.width * message.height * 4 &&
          (message.palette == null || message.palette instanceof Uint8Array && message.palette.length === 1024)) {
        pending.resolve({ width: message.width, height: message.height,
          pixels: message.pixels, palette: message.palette });
      } else {
        decodedImages.delete(message.path);
        pending.reject(new Error(message.error || "Image decode failed"));
      }
      return;
    }
    if (message.type === "scores-result") {
      const pending = scoreRequests.get(message.request);
      if (!pending) return;
      scoreRequests.delete(message.request);
      message.ok ? pending.resolve(message.value) : pending.reject(new Error(message.error || "Score request failed"));
      return;
    }
    if (message.type === "multiplayer-result") {
      const pending = multiplayerRequests.get(message.request);
      if (!pending) return;
      multiplayerRequests.delete(message.request);
      message.ok ? pending.resolve(message.value) : pending.reject(new Error(message.error || "Multiplayer request failed"));
      return;
    }
    if (message.type === "realtime-result") {
      const pending = realtimeRequests.get(message.request);
      if (!pending) return;
      realtimeRequests.delete(message.request);
      message.ok ? pending.resolve(message.value) : pending.reject(new Error(message.error || "Real-time request failed"));
      return;
    }
    if (message.type === "realtime-status") {
      realtimeConnected = Boolean(message.connected);
      for (const handler of syncStatusHandlers) handler(realtimeConnected);
      return;
    }
    if (message.type === "realtime-event") {
      for (const handler of realtimeHandlers) Promise.resolve().then(() => handler(Object.freeze(message.value))).catch(fail);
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
    for (const sync of [...synchronizers]) sync.stop().catch(() => {});
    cleanups.splice(0).forEach((handler) => { try { handler(); } catch (_) {} });
    stopMultiplayerPolling();
    if (realtimeConnected) send("realtime", { operation: "disconnect" });
    if (multiplayerSession) send("multiplayer", { operation: "disconnect", payload: { session_id: multiplayerSession } });
    storageRequests.forEach(({ reject }) => reject(new Error("Application terminated")));
    decodedImages.forEach(({ reject }) => reject(new Error("Application terminated")));
    storageRequests.clear();
    scoreRequests.forEach(({ reject }) => reject(new Error("Application terminated")));
    scoreRequests.clear();
    multiplayerRequests.forEach(({ reject }) => reject(new Error("Application terminated")));
    multiplayerRequests.clear(); multiplayerHandlers.clear();
    realtimeRequests.forEach(({ reject }) => reject(new Error("Application terminated")));
    realtimeRequests.clear(); realtimeHandlers.clear();
    syncStatusHandlers.clear();
    capabilityRequests.forEach(({ reject }) => reject(new Error("Application terminated")));
    capabilityRequests.clear();
    pointerLockChangeHandlers.clear(); pointerLockMoveHandlers.clear(); presentationHandlers.clear();
    pointerLockActive = false; presentationMode = "standard";
    animations.clear();
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
