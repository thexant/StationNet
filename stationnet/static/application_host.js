"use strict";

// Trusted host renderer. It accepts a deliberately small data vocabulary and
// creates every node itself; application strings are assigned with textContent.
(() => {
  const frame = document.querySelector("[data-app-runtime]");
  const surface = document.querySelector("[data-app-surface]");
  const status = document.querySelector("[data-app-status]");
  const display = document.querySelector(".application-display[data-app-platforms]");
  const compatibility = document.querySelector("[data-app-compatibility]");
  const launchWarning = document.querySelector("[data-app-launch-warning]");
  const immersiveButton = document.querySelector("[data-app-immersive]");
  const immersiveExit = document.querySelector("[data-app-immersive-exit]");
  if (!frame || !surface || !status) return;

  const PROTOCOL = "stationnet-app-v1";
  // ASCII 32-126. 8x8: dhepper/font8x8 (public domain); 8x16: pcface Modern DOS (CC0).
  const BUILTIN_FONT_ROWS = Object.freeze({
    vga8x8: [
      "0000000000000000",
      "183c3c1818001800",
      "3636000000000000",
      "36367f367f363600",
      "0c3e031e301f0c00",
      "006333180c666300",
      "1c361c6e3b336e00",
      "0606030000000000",
      "180c0606060c1800",
      "060c1818180c0600",
      "00663cff3c660000",
      "000c0c3f0c0c0000",
      "00000000000c0c06",
      "0000003f00000000",
      "00000000000c0c00",
      "6030180c06030100",
      "3e63737b6f673e00",
      "0c0e0c0c0c0c3f00",
      "1e33301c06333f00",
      "1e33301c30331e00",
      "383c36337f307800",
      "3f031f3030331e00",
      "1c06031f33331e00",
      "3f3330180c0c0c00",
      "1e33331e33331e00",
      "1e33333e30180e00",
      "000c0c00000c0c00",
      "000c0c00000c0c06",
      "180c0603060c1800",
      "00003f00003f0000",
      "060c1830180c0600",
      "1e3330180c000c00",
      "3e637b7b7b031e00",
      "0c1e33333f333300",
      "3f66663e66663f00",
      "3c66030303663c00",
      "1f36666666361f00",
      "7f46161e16467f00",
      "7f46161e16060f00",
      "3c66030373667c00",
      "3333333f33333300",
      "1e0c0c0c0c0c1e00",
      "7830303033331e00",
      "6766361e36666700",
      "0f06060646667f00",
      "63777f7f6b636300",
      "63676f7b73636300",
      "1c36636363361c00",
      "3f66663e06060f00",
      "1e3333333b1e3800",
      "3f66663e36666700",
      "1e33070e38331e00",
      "3f2d0c0c0c0c1e00",
      "3333333333333f00",
      "33333333331e0c00",
      "6363636b7f776300",
      "6363361c1c366300",
      "3333331e0c0c1e00",
      "7f6331184c667f00",
      "1e06060606061e00",
      "03060c1830604000",
      "1e18181818181e00",
      "081c366300000000",
      "00000000000000ff",
      "0c0c180000000000",
      "00001e303e336e00",
      "0706063e66663b00",
      "00001e3303331e00",
      "3830303e33336e00",
      "00001e333f031e00",
      "1c36060f06060f00",
      "00006e33333e301f",
      "0706366e66666700",
      "0c000e0c0c0c1e00",
      "300030303033331e",
      "070666361e366700",
      "0e0c0c0c0c0c1e00",
      "0000337f7f6b6300",
      "00001f3333333300",
      "00001e3333331e00",
      "00003b66663e060f",
      "00006e33333e3078",
      "00003b6e66060f00",
      "00003e031e301f00",
      "080c3e0c0c2c1800",
      "0000333333336e00",
      "00003333331e0c00",
      "0000636b7f7f3600",
      "000063361c366300",
      "00003333333e301f",
      "00003f190c263f00",
      "380c0c070c0c3800",
      "1818180018181800",
      "070c0c380c0c0700",
      "6e3b000000000000"
    ],
    vga8x16: [
      "00000000000000000000000000000000",
      "0000183c3c3c18181800181800000000",
      "00666622220000000000000000000000",
      "000000006c6cfe6c6cfe6c6c00000000",
      "0010107cd6d670381cd6d67c10100000",
      "00006092966c18306cd2920c00000000",
      "0000386c6c383076dccccc7600000000",
      "00181808100000000000000000000000",
      "00000c18303030303030180c00000000",
      "000030180c0c0c0c0c0c183000000000",
      "0000000000663cff3c66000000000000",
      "000000000018187e1818000000000000",
      "00000000000000000000303010200000",
      "00000000000000fe0000000000000000",
      "00000000000000000000303000000000",
      "00000002060c183060c0800000000000",
      "00007cc6c6cedef6e6c6c67c00000000",
      "00001838781818181818187e00000000",
      "00007cc6060c183060c0c6fe00000000",
      "00007cc606063c060606c67c00000000",
      "00000c1c3c6cccccfe0c0c1e00000000",
      "0000fec0c0c0fc060606c67c00000000",
      "00007cc6c0c0fcc6c6c6c67c00000000",
      "0000fec606060c183030303000000000",
      "00007cc6c6c67cc6c6c6c67c00000000",
      "00007cc6c6c6c67e0606c67c00000000",
      "00000000181800000018180000000000",
      "00000000181800000018180810000000",
      "000000060c18306030180c0600000000",
      "00000000007e00007e00000000000000",
      "0000006030180c060c18306000000000",
      "00007cc6c6060c181800181800000000",
      "0000003c429da5a5adb6403c00000000",
      "0000386cc6c6c6fec6c6c6c600000000",
      "0000fc6666667c66666666fc00000000",
      "00007cc6c6c0c0c0c0c6c67c00000000",
      "0000fc6666666666666666fc00000000",
      "0000fe6662687878686266fe00000000",
      "0000fe6662687878686060f000000000",
      "00007cc6c6c0c0cec6c6c67e00000000",
      "0000c6c6c6c6fec6c6c6c6c600000000",
      "00003c18181818181818183c00000000",
      "00001e0c0c0c0c0c0ccccc7800000000",
      "0000e666666c78786c6666e600000000",
      "0000f06060606060606266fe00000000",
      "000082c6eefefed6c6c6c6c600000000",
      "000086c6e6f6fedecec6c6c600000000",
      "00007cc6c6c6c6c6c6c6c67c00000000",
      "0000fc666666667c606060f000000000",
      "00007cc6c6c6c6c6c6d6de7c06000000",
      "0000fc666666667c6c6666e600000000",
      "00007cc6c660380c06c6c67c00000000",
      "00007e7e5a1818181818183c00000000",
      "0000c6c6c6c6c6c6c6c6c67c00000000",
      "0000c6c6c6c6c6c6c66c381000000000",
      "0000c6c6c6c6c6d6feeec68200000000",
      "0000c6c66c7c38387c6cc6c600000000",
      "0000666666663c181818183c00000000",
      "0000fec6860c183060c2c6fe00000000",
      "00003c30303030303030303c00000000",
      "00000080c06030180c06020000000000",
      "00003c0c0c0c0c0c0c0c0c3c00000000",
      "000010386cc600000000000000000000",
      "00000000000000000000000000ff0000",
      "00181810080000000000000000000000",
      "0000000000780c7ccccccc7600000000",
      "0000e060607c66666666667c00000000",
      "00000000007cc6c0c0c0c67c00000000",
      "00001c0c0c7ccccccccccc7600000000",
      "00000000007cc6c6fec0c67c00000000",
      "00001c36307c30303030307800000000",
      "000000000076cccccccccc7c0ccc7800",
      "0000e060606c7666666666e600000000",
      "00001818003818181818183c00000000",
      "00000c0c001c0c0c0c0c0c0ccccc7800",
      "0000e06060666c78786c66e600000000",
      "00003818181818181818183c00000000",
      "0000000000ecfed6d6d6d6c600000000",
      "0000000000dc66666666666600000000",
      "00000000007cc6c6c6c6c67c00000000",
      "0000000000dc66666666667c6060f000",
      "00000000007ccccccccccc7c0c0c1e00",
      "0000000000de7660606060f000000000",
      "00000000007cc660380cc67c00000000",
      "0000103030fc30303030341800000000",
      "0000000000cccccccccccc7600000000",
      "0000000000c6c6c6c66c381000000000",
      "0000000000c6d6d6d6d6fe6c00000000",
      "0000000000c6c66c386cc6c600000000",
      "0000000000c6c6c6c6c6c67e060cf800",
      "0000000000fe8c183060c2fe00000000",
      "00000e18181870181818180e00000000",
      "00001818181800001818181800000000",
      "0000701818180e181818187000000000",
      "000076dc000000000000000000000000"
    ]
  });
  const LIMITS = {
    components: 100, text: 8000, items: 100, columns: 80, rows: 100,
    canvasCommands: 600, canvasSprites: 2000, canvasGlyphs: 2000,
    canvasSegments: 10000, canvasVertices: 20000,
    canvasPolygons: 256, polygonVertices: 4096, capabilityText: 200000
  };
  const SOUND_LIMITS = { tones: 32, active: 24, frequency: [20, 20000], duration: [10, 10000], delay: [0, 30000], volume: [.001, .25], envelope: [0, 1000] };
  const keyboardActions = new Set();
  const lifecycleActions = new Set();
  let eventWindow = [];
  let totalEvents = 0;
  let closed = false;
  let audioContext = null;
  let midiEngine = null;
  let midiLoading = null;
  let startupTimer = null;
  let immersive = false;
  let immersiveFocus = null;
  let immersiveScroll = [0, 0];
  let pointerLockRequest = null;
  const multiplayerConnection = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
  let realtimeSocket = null;
  let realtimeSession = null;
  const activeTones = new Set();
  const images = new Map();
  const builtinFontSheets = new Map();
  const decodedImages = new Map();
  const tileMaps = new Map();
  const framebuffers = new Map();
  let framebufferPixels = 0;
  const canvasScenes = new WeakMap();
  let imageBytes = 0;
  let imagePixels = 0;

  const parseMetadata = (name) => {
    try { const value = JSON.parse(display?.dataset[name] || "[]"); return Array.isArray(value) ? value : []; }
    catch (_) { return []; }
  };
  const detectedInputs = () => {
    const media = (query) => typeof matchMedia === "function" && matchMedia(query).matches;
    const touch = navigator.maxTouchPoints > 0 || "ontouchstart" in window;
    const finePointer = media("(any-pointer: fine)");
    const pointer = finePointer || media("(any-pointer: coarse)");
    const mobile = navigator.userAgentData?.mobile ?? /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent);
    return {
      keyboard: finePointer || !mobile, pointer, "fine-pointer": finePointer, touch,
      "pointer-lock": "pointerLockElement" in document && typeof surface.requestPointerLock === "function"
    };
  };
  const environmentCompatibility = () => {
    const inputs = detectedInputs();
    const required = parseMetadata("appInputs");
    const platforms = parseMetadata("appPlatforms");
    const missing = required.filter((name) => !inputs[name]);
    const platformMatch = !platforms.length
      || (platforms.includes("touch") && inputs.touch)
      || (platforms.includes("desktop") && (inputs.keyboard || inputs["fine-pointer"]));
    return { inputs, missing, platformMatch, supported: !missing.length && platformMatch };
  };
  const startRuntime = () => {
    if (closed || frame.dataset.started === "1") return;
    frame.dataset.started = "1";
    frame.src = frame.dataset.runtimeSrc;
    startupTimer = setTimeout(() => {
      status.textContent = "PROGRAM STATUS / UNAVAILABLE";
      surface.textContent = "The application runtime did not respond.";
      terminate();
    }, 10000);
  };

  const text = (value, limit = LIMITS.text) => String(value == null ? "" : value).slice(0, limit);
  const number = (value, fallback, min, max) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback;
  };

  function audio() {
    if (audioContext) return audioContext;
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return null;
    audioContext = new Audio();
    return audioContext;
  }

  // Browsers require audio to be enabled directly from a player gesture. The
  // app itself runs in a worker, so the trusted host performs that one-time
  // unlock before forwarding an interaction to the application.
  function unlockAudio() {
    const context = audio();
    if (context && context.state === "suspended") context.resume().catch(() => {});
  }

  function stopSounds() {
    activeTones.forEach((tone) => { try { tone.stop(); } catch (_) {} });
    activeTones.clear();
  }

  async function composerMidi() {
    if (!midiLoading) midiLoading = import("./midi/engine.js?v=composer-2").then(async ({ MidiPlaybackEngine }) => {
      const context = audio();
      if (!context) throw new Error("Audio is unavailable in this browser.");
      midiEngine = new MidiPlaybackEngine(context, context.destination, "/static/midi", () => {},
        { preferScriptProcessor: true });
      await midiEngine.initialize();
      return midiEngine;
    }).catch(error => { midiLoading = null; throw error; });
    return midiLoading;
  }

  function sound(message) {
    if (message.operation === "stop") { stopSounds(); return; }
    if (message.operation !== "play") return;
    const context = audio();
    if (!context) return;
    const tones = Array.isArray(message.tones) ? message.tones.slice(0, SOUND_LIMITS.tones) : [];
    const waveforms = new Set(["sine", "square", "sawtooth", "triangle"]);
    tones.forEach((raw) => {
      if (activeTones.size >= SOUND_LIMITS.active) return;
      const tone = raw && typeof raw === "object" ? raw : { frequency: raw };
      const frequency = number(tone.frequency, NaN, ...SOUND_LIMITS.frequency);
      if (!Number.isFinite(frequency)) return;
      const duration = number(tone.duration, number(message.duration, 120, ...SOUND_LIMITS.duration), ...SOUND_LIMITS.duration);
      const delay = number(tone.delay, number(message.delay, 0, ...SOUND_LIMITS.delay), ...SOUND_LIMITS.delay);
      const volume = number(tone.volume, number(message.volume, .08, ...SOUND_LIMITS.volume), ...SOUND_LIMITS.volume);
      const attack = Math.min(duration / 2, number(tone.attack, number(message.attack, 5, ...SOUND_LIMITS.envelope), ...SOUND_LIMITS.envelope));
      const release = Math.min(duration / 2, number(tone.release, number(message.release, 20, ...SOUND_LIMITS.envelope), ...SOUND_LIMITS.envelope));
      const waveform = waveforms.has(tone.waveform) ? tone.waveform : waveforms.has(message.waveform) ? message.waveform : "sine";
      const oscillator = context.createOscillator(), gain = context.createGain();
      const start = context.currentTime + delay / 1000, end = start + duration / 1000;
      oscillator.type = waveform; oscillator.frequency.setValueAtTime(frequency, start);
      gain.gain.setValueAtTime(0, start);
      if (attack) gain.gain.linearRampToValueAtTime(volume, start + attack / 1000);
      else gain.gain.setValueAtTime(volume, start);
      gain.gain.setValueAtTime(volume, Math.max(start, end - release / 1000));
      gain.gain.linearRampToValueAtTime(0, end);
      oscillator.connect(gain).connect(context.destination);
      activeTones.add(oscillator);
      oscillator.addEventListener("ended", () => { activeTones.delete(oscillator); oscillator.disconnect(); gain.disconnect(); }, { once: true });
      oscillator.start(start); oscillator.stop(end + .01);
    });
  }

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

  // Click is retained for ordinary UI. Games can additionally opt into the
  // beginning and end of a press, including keyboard activation.
  function bindPressable(node, actions, extra = {}) {
    const click = text(actions.click, 80), press = text(actions.press, 80), release = text(actions.release, 80);
    if (click) node.addEventListener("click", () => interact(click, extra));
    if (!press && !release) return;

    let pointerId = null;
    const finishPointer = (event) => {
      if (pointerId == null || (event.pointerId != null && event.pointerId !== pointerId)) return;
      pointerId = null;
      if (release) interact(release, { ...extra, phase: "end" });
    };
    node.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || pointerId != null) return;
      pointerId = event.pointerId;
      node.setPointerCapture?.(pointerId);
      if (press) interact(press, { ...extra, phase: "start" });
    });
    node.addEventListener("pointerup", finishPointer);
    node.addEventListener("pointercancel", finishPointer);
    node.addEventListener("lostpointercapture", finishPointer);

    const keys = new Set();
    node.addEventListener("keydown", (event) => {
      if (!['Enter', ' '].includes(event.key) || event.repeat || keys.has(event.code)) return;
      keys.add(event.code);
      if (press) interact(press, { ...extra, phase: "start" });
    });
    node.addEventListener("keyup", (event) => {
      if (!keys.delete(event.code)) return;
      if (release) interact(release, { ...extra, phase: "end" });
    });
    node.addEventListener("blur", () => {
      if (!keys.size) return;
      keys.clear();
      if (release) interact(release, { ...extra, phase: "end" });
    });
  }

  const canvasColor = (value, fallback = "#00ed45") => {
    const color = String(value || "");
    if (/^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(color)) return color;
    const rgb = color.match(/^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$/i);
    return rgb && rgb.slice(1).every((channel) => Number(channel) <= 255) ? color : fallback;
  };

  const canvasCoordinate = (value) => {
    return typeof value === "number" && Number.isFinite(value)
      ? Math.min(1000000, Math.max(-1000000, value)) : null;
  };

  const canvasPoint = (value) => {
    if (!Array.isArray(value) || value.length < 2) return null;
    const x = canvasCoordinate(value[0]), y = canvasCoordinate(value[1]);
    return x == null || y == null ? null : [x, y];
  };

  const lineWidth = (command) => number(command.width, 1, .25, 64);

  function redrawScenes() {
    surface.querySelectorAll(".app-canvas").forEach((canvas) => {
      const scene = canvasScenes.get(canvas);
      if (scene) drawCanvas(canvas, scene);
    });
  }

  function registerImage(message) {
    const path = text(message.path, 200);
    const mime = /\.png$/i.test(path) ? "image/png" : /\.jpe?g$/i.test(path) ? "image/jpeg" :
      /\.gif$/i.test(path) ? "image/gif" : /\.webp$/i.test(path) ? "image/webp" : null;
    if (!mime || images.has(path) || images.size + decodedImages.size >= 32 || typeof message.data !== "string" ||
        message.data.length > 1400000 || imageBytes + message.data.length > 6000000) return;
    imageBytes += message.data.length;
    images.set(path, null);
    try {
      const binary = atob(message.data);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      const blob = new Blob([bytes], { type: mime });
      const decode = typeof createImageBitmap === "function" ? createImageBitmap(blob) : new Promise((resolve, reject) => {
        const url = URL.createObjectURL(blob);
        const picture = new Image();
        picture.onload = () => { URL.revokeObjectURL(url); resolve(picture); };
        picture.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Invalid image")); };
        picture.src = url;
      });
      decode.then((bitmap) => {
        if (closed) { bitmap.close?.(); return; }
        const pixels = bitmap.width * bitmap.height;
        if (bitmap.width > 2048 || bitmap.height > 2048 || pixels > 4194304 || imagePixels + pixels > 8388608) {
          bitmap.close?.(); return;
        }
        imagePixels += pixels;
        images.set(path, bitmap);
        redrawScenes();
      }).catch(() => {});
    } catch (_) { /* malformed image data is ignored */ }
  }

  function decodeImageResult(request, path, ok, value, error) {
    if (!frame.contentWindow || closed) return;
    const response = { protocol: PROTOCOL, type: "graphics-decode-image-result", request, path, ok,
      ...(value || {}), error: error ? text(error.message || error, 200) : undefined };
    frame.contentWindow.postMessage(response, "*", value ? [value.pixels.buffer] : []);
  }

  async function decodeBundledImage(message) {
    const request = message.request, path = message.path;
    if (typeof request !== "string" || !/^image-[1-9]\d{0,9}$/.test(request) ||
        typeof path !== "string" || !/^[^\\/][^\\]*\.png$/i.test(path) || path.length > 200 ||
        path.split("/").some(part => !part || part === "." || part === "..") ||
        typeof message.data !== "string" || message.data.length > 1400000 ||
        decodedImages.has(path) || images.size + decodedImages.size >= 32 ||
        imageBytes + message.data.length > 6000000) {
      decodeImageResult(request, path, false, null, "Image decode limit exceeded or invalid request"); return;
    }
    decodedImages.set(path, true);
    imageBytes += message.data.length;
    try {
      const binary = atob(message.data);
      if (binary.length < 24 || !binary.startsWith("\x89PNG\r\n\x1a\n") ||
          binary.slice(12, 16) !== "IHDR") throw new Error("Invalid PNG");
      const dimension = (offset) => (binary.charCodeAt(offset) * 16777216 +
        binary.charCodeAt(offset + 1) * 65536 + binary.charCodeAt(offset + 2) * 256 + binary.charCodeAt(offset + 3));
      const width = dimension(16), height = dimension(20), count = width * height;
      if (!width || !height || width > 2048 || height > 2048 || count > 4194304 ||
          imagePixels + count > 8388608) throw new Error("Image pixel budget exceeded");
      const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
      const blob = new Blob([bytes], { type: "image/png" });
      const bitmap = await (typeof createImageBitmap === "function" ? createImageBitmap(blob) :
        new Promise((resolve, reject) => {
          const url = URL.createObjectURL(blob);
          const picture = new Image();
          picture.onload = () => { URL.revokeObjectURL(url); resolve(picture); };
          picture.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Invalid PNG")); };
          picture.src = url;
        }));
      try {
        if (bitmap.width !== width || bitmap.height !== height || imagePixels + count > 8388608)
          throw new Error("Image pixel budget exceeded");
        const canvas = document.createElement("canvas"); canvas.width = width; canvas.height = height;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        if (!context) throw new Error("Image decoding is unavailable");
        context.drawImage(bitmap, 0, 0);
        const pixels = new Uint8Array(context.getImageData(0, 0, width, height).data);
        imagePixels += count;
        decodeImageResult(request, path, true, { width, height, pixels });
      } finally { bitmap.close?.(); }
    } catch (error) {
      decodedImages.delete(path);
      decodeImageResult(request, path, false, null, error);
    }
  }

  function registerMap(message) {
    const id = text(message.id, 80);
    if (!/^[A-Za-z0-9_.:-]{1,80}$/.test(id) || (!tileMaps.has(id) && tileMaps.size >= 16) ||
        !Array.isArray(message.tiles) || !message.tiles.length || message.tiles.length > 262144 ||
        !Number.isInteger(message.columns) || message.columns < 1 || message.tiles.length % message.columns) return;
    const tileWidth = Math.trunc(number(message.tileWidth, 0, 1, 256));
    const tileHeight = Math.trunc(number(message.tileHeight, 0, 1, 256));
    const sheetColumns = Math.trunc(number(message.sheetColumns, 0, 1, 256));
    if (!tileWidth || !tileHeight || !sheetColumns || !images.has(message.image) ||
        !message.tiles.every((tile) => Number.isInteger(tile) && tile >= -1 && tile <= 65535)) return;
    tileMaps.set(id, { tiles: Int32Array.from(message.tiles), columns: message.columns,
      rows: message.tiles.length / message.columns, image: message.image, tileWidth, tileHeight, sheetColumns });
    redrawScenes();
  }

  function registerFrame(message) {
    const id = text(message.id, 80);
    const width = message.width, height = message.height, format = message.format;
    if (!/^[A-Za-z0-9_.:-]{1,80}$/.test(id) || !Number.isInteger(width) || !Number.isInteger(height) ||
        width < 1 || height < 1 || width > 640 || height > 400 || width * height > 256000 ||
        (format !== "rgba" && format !== "indexed")) return;
    let frame = framebuffers.get(id);
    if (frame && (frame.width !== width || frame.height !== height || frame.format !== format)) return;
    if (!frame && (framebuffers.size >= 4 || framebufferPixels + width * height > 512000)) return;
    const length = width * height * (format === "rgba" ? 4 : 1);
    const pixels = message.pixels;
    const palette = message.palette;
    if (!(palette === null || (palette instanceof Uint8Array && palette.length === 1024)) ||
        (format === "indexed" && !(palette instanceof Uint8Array)) ||
        !(pixels === null || (pixels instanceof Uint8Array && pixels.length === length)) ||
        (!frame && pixels === null) || (format === "rgba" && pixels === null)) return;
    if (!frame) {
      const canvas = document.createElement("canvas"); canvas.width = width; canvas.height = height;
      frame = { width, height, format, canvas, pixels: null, palette: null,
        rgba: format === "indexed" ? new Uint8ClampedArray(width * height * 4) : null };
      framebuffers.set(id, frame); framebufferPixels += width * height;
    }
    if (pixels) frame.pixels = pixels;
    if (palette) frame.palette = palette;
    const rgba = format === "rgba" ? new Uint8ClampedArray(frame.pixels.buffer) : frame.rgba;
    if (format === "indexed") {
      for (let i = 0; i < frame.pixels.length; i++) {
        const color = frame.pixels[i] * 4, offset = i * 4;
        rgba[offset] = frame.palette[color]; rgba[offset + 1] = frame.palette[color + 1];
        rgba[offset + 2] = frame.palette[color + 2]; rgba[offset + 3] = frame.palette[color + 3];
      }
    }
    frame.canvas.getContext("2d").putImageData(new ImageData(rgba, width, height), 0, 0);
  }

  function drawImage(context, command, bitmap, x, y, sw, sh, sx, sy, dw, dh) {
    if (!bitmap || sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0 || sx < 0 || sy < 0 ||
        sx + sw > bitmap.width || sy + sh > bitmap.height) return;
    context.save();
    context.globalAlpha = number(command.opacity, 1, 0, 1);
    context.imageSmoothingEnabled = false;
    context.translate(x + (command.flipX ? dw : 0), y + (command.flipY ? dh : 0));
    context.scale(command.flipX ? -1 : 1, command.flipY ? -1 : 1);
    context.drawImage(bitmap, sx, sy, sw, sh, 0, 0, dw, dh);
    context.restore();
  }

  function drawSprite(context, entry, width, height) {
    if (!entry || typeof entry !== "object" || !entry.sheet || typeof entry.sheet !== "object") return;
    const sheet = entry.sheet;
    if (typeof sheet.image !== "string" || !Number.isInteger(sheet.frameWidth) ||
        !Number.isInteger(sheet.frameHeight) || !Number.isInteger(sheet.columns) ||
        sheet.frameWidth < 1 || sheet.frameWidth > 2048 ||
        sheet.frameHeight < 1 || sheet.frameHeight > 2048 ||
        sheet.columns < 1 || sheet.columns > 2048 ||
        !Number.isInteger(entry.frame) || entry.frame < 0 || entry.frame > 65535 ||
        typeof entry.x !== "number" || !Number.isFinite(entry.x) ||
        typeof entry.y !== "number" || !Number.isFinite(entry.y) ||
        (entry.width != null && (typeof entry.width !== "number" || !Number.isFinite(entry.width))) ||
        (entry.height != null && (typeof entry.height !== "number" || !Number.isFinite(entry.height))) ||
        (entry.rotation != null && (typeof entry.rotation !== "number" || !Number.isFinite(entry.rotation))) ||
        (entry.scale != null && (typeof entry.scale !== "number" || !Number.isFinite(entry.scale))) ||
        (entry.opacity != null && (typeof entry.opacity !== "number" || !Number.isFinite(entry.opacity))) ||
        (entry.flipX != null && typeof entry.flipX !== "boolean") ||
        (entry.flipY != null && typeof entry.flipY !== "boolean")) return;
    const bitmap = images.get(sheet.image);
    if (!bitmap) return;
    const sx = (entry.frame % sheet.columns) * sheet.frameWidth;
    const sy = Math.floor(entry.frame / sheet.columns) * sheet.frameHeight;
    if (sx + sheet.frameWidth > bitmap.width || sy + sheet.frameHeight > bitmap.height) return;
    const x = number(entry.x, 0, -width, width * 2);
    const y = number(entry.y, 0, -height, height * 2);
    const dw = number(entry.width, sheet.frameWidth, 0, width * 2);
    const dh = number(entry.height, sheet.frameHeight, 0, height * 2);
    const rotation = number(entry.rotation, 0, -Math.PI * 2, Math.PI * 2);
    const scale = number(entry.scale, 1, 0, 8);
    if (!dw || !dh || !scale) return;
    context.save();
    context.globalAlpha = number(entry.opacity, 1, 0, 1);
    context.imageSmoothingEnabled = false;
    context.translate(x + dw / 2, y + dh / 2);
    context.rotate(rotation);
    context.scale((entry.flipX ? -1 : 1) * scale, (entry.flipY ? -1 : 1) * scale);
    context.drawImage(bitmap, sx, sy, sheet.frameWidth, sheet.frameHeight,
      -dw / 2, -dh / 2, dw, dh);
    context.restore();
  }

  function fontSheet(font, color) {
    if (font.sheet !== "vga8x8" && font.sheet !== "vga8x16") return images.get(font.sheet);
    const name = font.sheet;
    if (font.charWidth !== 8 || font.charHeight !== (name === "vga8x8" ? 8 : 16) ||
        font.firstChar !== 32) return null;
    const key = `${name}:${color}`;
    if (builtinFontSheets.has(key)) return builtinFontSheets.get(key);
    const canvas = document.createElement("canvas");
    canvas.width = 128; canvas.height = Math.ceil(BUILTIN_FONT_ROWS[name].length / 16) * font.charHeight;
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.fillStyle = color;
    BUILTIN_FONT_ROWS[name].forEach((glyph, index) => {
      const left = index % 16 * 8, top = Math.floor(index / 16) * font.charHeight;
      for (let row = 0; row < font.charHeight; row++) {
        const bits = parseInt(glyph.slice(row * 2, row * 2 + 2), 16);
        for (let col = 0; col < 8; col++) {
          if (bits & (name === "vga8x8" ? 1 << col : 128 >> col))
            context.fillRect(left + col, top + row, 1, 1);
        }
      }
    });
    if (builtinFontSheets.size >= 32) builtinFontSheets.delete(builtinFontSheets.keys().next().value);
    builtinFontSheets.set(key, canvas);
    return canvas;
  }

  function drawBitmapText(context, command, width, height, available) {
    const font = command.font;
    if (!font || typeof font !== "object" || typeof font.sheet !== "string" ||
        !Number.isInteger(font.charWidth) || font.charWidth < 1 || font.charWidth > 64 ||
        !Number.isInteger(font.charHeight) || font.charHeight < 1 || font.charHeight > 64 ||
        !Number.isInteger(font.firstChar) || font.firstChar < 0 || font.firstChar > 65535 ||
        typeof command.text !== "string" ||
        (command.opacity != null && (typeof command.opacity !== "number" || !Number.isFinite(command.opacity))) ||
        typeof command.x !== "number" || !Number.isFinite(command.x) ||
        typeof command.y !== "number" || !Number.isFinite(command.y)) return 0;
    const builtin = font.sheet === "vga8x8" || font.sheet === "vga8x16";
    const source = fontSheet(font, canvasColor(command.color, "#ffffff"));
    if (!source || source.width % font.charWidth || source.height % font.charHeight) return 0;
    const columns = source.width / font.charWidth;
    const count = columns * source.height / font.charHeight;
    const lines = command.text.slice(0, 200).split(/\r\n|\r|\n/);
    const x = number(command.x, 0, -width, width * 2);
    const y = number(command.y, 0, -height, height * 2);
    let visited = 0;
    context.save(); context.globalAlpha = number(command.opacity, 1, 0, 1);
    context.imageSmoothingEnabled = false;
    lines.forEach((line, row) => {
      Array.from(line).forEach((char, column) => {
        if (visited >= available) return;
        visited++;
        const index = char.codePointAt(0) - font.firstChar;
        if (index < 0 || index >= count || builtin && index >= 95) return;
        context.drawImage(source, index % columns * font.charWidth,
          Math.floor(index / columns) * font.charHeight, font.charWidth, font.charHeight,
          x + column * font.charWidth, y + row * font.charHeight,
          font.charWidth, font.charHeight);
      });
    });
    context.restore();
    return visited;
  }

  function drawTileMap(context, command, width, height) {
    const map = tileMaps.get(command.map), bitmap = map && images.get(map.image);
    if (!map || !bitmap) return;
    const x = number(command.x, 0, -width, width * 2), y = number(command.y, 0, -height, height * 2);
    const viewWidth = number(command.width, width, 0, width * 2);
    const viewHeight = number(command.height, height, 0, height * 2);
    const cameraX = number(command.cameraX, 0, 0, map.columns * map.tileWidth);
    const cameraY = number(command.cameraY, 0, 0, map.rows * map.tileHeight);
    const firstColumn = Math.floor(cameraX / map.tileWidth), firstRow = Math.floor(cameraY / map.tileHeight);
    const lastColumn = Math.min(map.columns, Math.ceil((cameraX + viewWidth) / map.tileWidth));
    const lastRow = Math.min(map.rows, Math.ceil((cameraY + viewHeight) / map.tileHeight));
    context.save(); context.beginPath(); context.rect(x, y, viewWidth, viewHeight); context.clip();
    context.globalAlpha = number(command.opacity, 1, 0, 1);
    context.imageSmoothingEnabled = false;
    let visited = 0;
    for (let row = firstRow; row < lastRow && visited < 4096; row++) {
      for (let column = firstColumn; column < lastColumn && visited < 4096; column++) {
        visited++;
        const tile = map.tiles[row * map.columns + column];
        if (tile < 0) continue;
        const sx = (tile % map.sheetColumns) * map.tileWidth;
        const sy = Math.floor(tile / map.sheetColumns) * map.tileHeight;
        if (sx + map.tileWidth > bitmap.width || sy + map.tileHeight > bitmap.height) continue;
        context.drawImage(bitmap, sx, sy, map.tileWidth, map.tileHeight,
          x + column * map.tileWidth - cameraX, y + row * map.tileHeight - cameraY,
          map.tileWidth, map.tileHeight);
      }
    }
    context.restore();
  }

  function drawCanvas(canvas, component) {
    canvasScenes.set(canvas, component);
    const width = Math.trunc(number(component.width, 640, 80, 1200));
    const height = Math.trunc(number(component.height, 400, 80, 800));
    // Assigning either bitmap dimension clears the context and reallocates its
    // backing store. Animated updates overwhelmingly keep both dimensions, so
    // only resize when an application actually requests it.
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    canvas.style.setProperty("--app-canvas-ratio", String(width / height));
    if (canvas.dataset.logicalWidth !== String(width)) canvas.dataset.logicalWidth = String(width);
    if (canvas.dataset.logicalHeight !== String(height)) canvas.dataset.logicalHeight = String(height);
    if (component.action) canvas.dataset.canvasAction = text(component.action, 80);
    if (component.hover != null) canvas.dataset.canvasHover = component.hover ? "1" : "0";
    canvas.setAttribute("aria-label", text(component.label, 200));
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, width, height); context.fillStyle = "#010401"; context.fillRect(0, 0, width, height);
    // Canvas bitmaps clip already, but an explicit path also keeps that
    // guarantee if drawing is later moved onto a larger shared surface.
    context.save(); context.beginPath(); context.rect(0, 0, width, height); context.clip();
    let segments = 0, vertices = 0, polygons = 0, sprites = 0, glyphs = 0;
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
      } else if (command.type === "line") {
        if (segments >= LIMITS.canvasSegments) return;
        const x1 = canvasCoordinate(command.x1), y1 = canvasCoordinate(command.y1);
        const x2 = canvasCoordinate(command.x2), y2 = canvasCoordinate(command.y2);
        if ([x1, y1, x2, y2].some((value) => value == null)) return;
        context.beginPath(); context.moveTo(x1, y1); context.lineTo(x2, y2);
        context.strokeStyle = canvasColor(command.color); context.lineWidth = lineWidth(command); context.stroke();
        segments += 1;
      } else if (command.type === "lines") {
        if (!Array.isArray(command.segments) || segments >= LIMITS.canvasSegments) return;
        const remaining = Math.min(command.segments.length, LIMITS.canvasSegments - segments);
        let accepted = 0; context.beginPath();
        for (let index = 0; index < remaining; index += 1) {
          const segment = command.segments[index];
          if (!Array.isArray(segment) || segment.length < 4) continue;
          const x1 = canvasCoordinate(segment[0]), y1 = canvasCoordinate(segment[1]);
          const x2 = canvasCoordinate(segment[2]), y2 = canvasCoordinate(segment[3]);
          if ([x1, y1, x2, y2].some((value) => value == null)) continue;
          context.moveTo(x1, y1); context.lineTo(x2, y2); accepted += 1;
        }
        if (!accepted) return;
        context.strokeStyle = canvasColor(command.color); context.lineWidth = lineWidth(command); context.stroke();
        segments += accepted;
      } else if (command.type === "polygon") {
        if (!Array.isArray(command.points) || polygons >= LIMITS.canvasPolygons || vertices >= LIMITS.canvasVertices) return;
        const available = Math.min(command.points.length, LIMITS.polygonVertices, LIMITS.canvasVertices - vertices);
        const points = command.points.slice(0, available).map(canvasPoint);
        if (points.length < 3 || points.some((point) => point == null)) return;
        context.beginPath(); context.moveTo(points[0][0], points[0][1]);
        points.slice(1).forEach((point) => context.lineTo(point[0], point[1])); context.closePath();
        if (command.fill != null) { context.fillStyle = canvasColor(command.fill); context.fill(); }
        if (command.stroke != null) {
          context.strokeStyle = canvasColor(command.stroke); context.lineWidth = lineWidth(command); context.stroke();
        }
        if (command.fill == null && command.stroke == null) { context.fillStyle = canvasColor(command.color); context.fill(); }
        vertices += points.length; polygons += 1;
      } else if (command.type === "image") {
        const bitmap = images.get(command.image);
        if (!bitmap) return;
        const sx = number(command.sx, 0, 0, bitmap.width), sy = number(command.sy, 0, 0, bitmap.height);
        const sw = number(command.sw, bitmap.width - sx, 0, bitmap.width);
        const sh = number(command.sh, bitmap.height - sy, 0, bitmap.height);
        const dw = number(command.width, sw, 0, width * 2), dh = number(command.height, sh, 0, height * 2);
        drawImage(context, command, bitmap, x, y, sw, sh, sx, sy, dw, dh);
      } else if (command.type === "sprites") {
        if (!Array.isArray(command.sprites) || sprites >= LIMITS.canvasSprites) return;
        const count = Math.min(command.sprites.length, LIMITS.canvasSprites - sprites);
        for (let index = 0; index < count; index++) drawSprite(context, command.sprites[index], width, height);
        sprites += count;
      } else if (command.type === "bitmapText") {
        if (glyphs >= LIMITS.canvasGlyphs) return;
        glyphs += drawBitmapText(context, command, width, height, LIMITS.canvasGlyphs - glyphs);
      } else if (command.type === "tilemap") {
        drawTileMap(context, command, width, height);
      } else if (command.type === "framebuffer") {
        const frame = framebuffers.get(command.buffer);
        if (!frame) return;
        const dw = number(command.width, frame.width, 0, width * 2);
        const dh = number(command.height, frame.height, 0, height * 2);
        context.save(); context.globalAlpha = number(command.opacity, 1, 0, 1);
        context.imageSmoothingEnabled = false;
        context.drawImage(frame.canvas, x, y, dw, dh); context.restore();
      }
    });
    context.restore();
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
      bindPressable(button, { click: control.action, press: control.pressAction, release: control.releaseAction });
      overlay.append(button);
    });
    if (overlay.childElementCount) wrapper.append(overlay);
  }

  function renderComponent(component, depth = 0) {
    if (!component || typeof component !== "object" || depth > 1) return null;
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
        bindPressable(node, { click: component.action, press: component.pressAction, release: component.releaseAction }); break;
      case "menu": {
        node = document.createElement("fieldset"); node.className = "app-menu";
        if (component.layout === "dpad") node.classList.add("app-dpad");
        const legend = document.createElement("legend"); legend.textContent = text(component.label, 200); node.append(legend);
        (Array.isArray(component.items) ? component.items : []).slice(0, LIMITS.items).forEach((item) => {
          if (!item || typeof item !== "object") return;
          const button = document.createElement("button"); button.type = "button"; button.textContent = text(item.label, 120);
          button.disabled = Boolean(item.disabled);
          bindPressable(button, { click: component.action, press: component.pressAction, release: component.releaseAction }, { value: text(item.value, 500) });
          node.append(button);
        }); break;
      }
      case "input": case "number": case "date": case "time": {
        node = document.createElement("label"); node.textContent = text(component.label, 200);
        const input = document.createElement("input"); input.dataset.appInput = "";
        input.name = text(component.name, 80).replace(/[^a-zA-Z0-9_-]/g, "_");
        input.type = component.type === "input" ? "text" : component.type;
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
        (Array.isArray(component.items) ? component.items : []).slice(0, 128).forEach((item) => {
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
      case "panel": {
        if (depth) return null;
        node = document.createElement("section"); node.className = "app-panel";
        const heading = document.createElement("h3"); heading.textContent = text(component.label, 200); node.append(heading);
        (Array.isArray(component.components) ? component.components : []).slice(0, LIMITS.components).forEach((child) => {
          const element = renderComponent(child, depth + 1); if (element) node.append(element);
        });
        break;
      }
      case "calendar": {
        node = document.createElement("section"); node.className = "app-calendar";
        node.setAttribute("aria-label", text(component.month, 100));
        const grid = document.createElement("div"); grid.className = "app-calendar-grid";
        ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].forEach((name) => {
          const label = document.createElement("div"); label.className = "app-calendar-weekday"; label.textContent = name; grid.append(label);
        });
        (Array.isArray(component.days) ? component.days : []).slice(0, 42).forEach((day) => {
          if (!day || typeof day !== "object" || !/^\d{4}-\d{2}-\d{2}$/.test(day.date || "")) return;
          const button = document.createElement("button"); button.type = "button"; button.className = "app-calendar-day";
          if (day.outside) button.classList.add("outside");
          if (day.today) button.classList.add("today");
          if (day.selected) { button.classList.add("selected"); button.setAttribute("aria-pressed", "true"); }
          const dayNumber = document.createElement("span"); dayNumber.className = "app-calendar-number"; dayNumber.textContent = text(day.number, 2); button.append(dayNumber);
          const titles = [];
          (Array.isArray(day.events) ? day.events : []).slice(0, 2).forEach((event) => {
            const item = document.createElement("span"); item.className = "app-calendar-event"; item.textContent = text(event, 100);
            titles.push(item.textContent); button.append(item);
          });
          const extra = Math.max(0, Math.floor(number(day.extra, 0, 0, 1000)));
          if (extra) { const more = document.createElement("span"); more.className = "app-calendar-more"; more.textContent = `+${extra} more`; button.append(more); }
          button.setAttribute("aria-label", `${day.date}${titles.length ? ": " + titles.join(", ") : ""}${extra ? `, ${extra} more` : ""}`);
          bindPressable(button, { click: component.action }, { value: day.date }); grid.append(button);
        });
        node.append(grid); break;
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
          interact(canvas.dataset.canvasAction, { x, y, phase, shiftKey: event.shiftKey, ctrlKey: event.ctrlKey, metaKey: event.metaKey });
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
    if (Array.isArray(message.frames)) message.frames.slice(0, 4).forEach(registerFrame);
    const components = Array.isArray(message.components) ? message.components.slice(0, LIMITS.components) : [];
    try {
      const canvases = components.filter(component => component?.type === "canvas");
      const others = components.filter(component => component?.type !== "canvas");
      if (JSON.stringify(canvases).length > 750000 || JSON.stringify(others).length > 250000)
        throw new Error("content limit exceeded");
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

  async function multiplayer(message) {
    const requestId = text(message.request, 80);
    try {
      const response = await fetch(surface.dataset.appMultiplayerUrl, {
        method: "POST", credentials: "same-origin", keepalive: message.operation === "disconnect",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": surface.dataset.appCsrf },
        body: JSON.stringify({ ...message.payload, operation: message.operation, connection_id: multiplayerConnection })
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || `Multiplayer request failed (${response.status})`);
      if ((message.operation === "leave" || message.operation === "disconnect") && realtimeSocket) {
        realtimeSocket.close(); realtimeSocket = null; realtimeSession = null;
      }
      if (requestId && frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "multiplayer-result", request: requestId, ok: true, value: result }, "*");
    } catch (error) {
      if (requestId && frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "multiplayer-result", request: requestId, ok: false, error: text(error.message || error, 500) }, "*");
    }
  }

  function realtime(message) {
    const requestId = text(message.request, 80);
    const reply = (ok, value, error) => {
      if (requestId && frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "realtime-result", request: requestId, ok, value, error }, "*");
    };
    if (message.operation === "disconnect") {
      if (realtimeSocket) realtimeSocket.close();
      realtimeSocket = null; realtimeSession = null; reply(true, { connected: false }); return;
    }
    if (message.operation === "send") {
      if (!realtimeSocket || realtimeSocket.readyState !== WebSocket.OPEN || realtimeSession !== message.session_id) {
        reply(false, null, "Real-time connection is unavailable."); return;
      }
      try {
        const payload = JSON.stringify({ type: message.kind || "event", event: message.event,
          data: message.data, input_id: message.input_id, tick: message.tick, epoch: message.epoch });
        if (new TextEncoder().encode(payload).length > 16384 || realtimeSocket.bufferedAmount > 65536) throw new Error("Real-time buffer limit reached.");
        realtimeSocket.send(payload); reply(true, { queued: true });
      } catch (error) { reply(false, null, text(error.message || error, 500)); }
      return;
    }
    if (message.operation !== "connect" || typeof message.session_id !== "string") { reply(false, null, "Invalid real-time request."); return; }
    if (realtimeSocket) realtimeSocket.close();
    const socketUrl = new URL(surface.dataset.appRealtimeUrl, location.href);
    socketUrl.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    let socket;
    try { socket = new WebSocket(socketUrl); }
    catch (error) { reply(false, null, text(error.message || error, 500)); return; }
    realtimeSocket = socket; realtimeSession = message.session_id;
    let answered = false;
    const failConnection = () => {
      if (!answered) { answered = true; reply(false, null, "Real-time connection failed."); }
      if (realtimeSocket === socket) {
        realtimeSocket = null; realtimeSession = null;
        if (frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "realtime-status", connected: false }, "*");
      }
    };
    socket.onopen = () => socket.send(JSON.stringify({ type: "hello", csrf: surface.dataset.appCsrf,
      session_id: message.session_id, connection_id: multiplayerConnection, max_rate: message.max_rate }));
    socket.onmessage = (event) => {
      if (realtimeSocket !== socket) return;
      try {
        const value = JSON.parse(event.data);
        if (value.type === "ready") {
          answered = true; reply(true, { connected: true, maxRate: value.max_rate, sync: value.sync,
            serverTime: value.server_time });
          frame.contentWindow?.postMessage({ protocol: PROTOCOL, type: "realtime-status", connected: true }, "*");
        } else if (["event", "presence", "host", "input", "state", "sync"].includes(value.type)) {
          frame.contentWindow?.postMessage({ protocol: PROTOCOL, type: "realtime-event", value }, "*");
        }
      } catch (_) { socket.close(); }
    };
    socket.onerror = failConnection;
    socket.onclose = failConnection;
  }

  function capabilityResult(request, ok, value, error) {
    if (frame.contentWindow) frame.contentWindow.postMessage({
      protocol: PROTOCOL, type: "capability-result", request: text(request, 80), ok,
      value, error: error ? text(error.message || error, 500) : undefined
    }, "*");
  }

  const sendHostEvent = (type, payload = {}) => {
    if (frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type, ...payload }, "*");
  };

  function setImmersive(value) {
    const next = Boolean(value);
    if (immersive === next) return;
    if (next) {
      immersiveFocus = document.activeElement;
      immersiveScroll = [scrollX, scrollY];
    }
    immersive = next;
    document.body.classList.toggle("application-immersive", immersive);
    if (immersiveButton) immersiveButton.setAttribute("aria-pressed", String(immersive));
    if (immersiveExit) immersiveExit.hidden = !immersive;
    if (immersive) {
      surface.focus({ preventScroll: true });
    } else {
      scrollTo(...immersiveScroll);
      if (immersiveFocus instanceof HTMLElement && immersiveFocus.isConnected) immersiveFocus.focus({ preventScroll: true });
    }
    sendHostEvent("presentation-event", { mode: immersive ? "immersive" : "standard" });
  }

  function dismissPointerPrompt(error) {
    const prompt = document.querySelector("[data-app-pointer-prompt]");
    if (prompt) prompt.remove();
    if (error && pointerLockRequest) capabilityResult(pointerLockRequest, false, false, error);
    if (error) pointerLockRequest = null;
  }

  function requestPointerLock(message) {
    if (!detectedInputs()["pointer-lock"]) {
      capabilityResult(message.request, false, false, new Error("Pointer lock is unavailable in this browser"));
      return;
    }
    if (document.pointerLockElement === surface) {
      capabilityResult(message.request, true, true); return;
    }
    dismissPointerPrompt(new Error("A newer mouse capture request replaced this one"));
    pointerLockRequest = message.request;
    const prompt = document.createElement("div");
    prompt.className = "application-pointer-prompt"; prompt.dataset.appPointerPrompt = "";
    const copy = document.createElement("p");
    copy.textContent = "This application wants to capture your mouse for relative movement. Press Escape at any time to release it.";
    const capture = document.createElement("button"); capture.type = "button"; capture.textContent = "CAPTURE MOUSE";
    const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "secondary"; cancel.textContent = "CANCEL";
    capture.addEventListener("click", async () => {
      try {
        const result = surface.requestPointerLock(message.unadjustedMovement ? { unadjustedMovement: true } : undefined);
        if (result && typeof result.then === "function") await result;
      } catch (error) {
        dismissPointerPrompt(error);
      }
    });
    cancel.addEventListener("click", () => dismissPointerPrompt(new Error("Mouse capture was cancelled")));
    prompt.append(copy, capture, cancel);
    display.insertBefore(prompt, display.querySelector(".application-viewport"));
    capture.focus();
  }

  document.addEventListener("pointerlockchange", () => {
    const active = document.pointerLockElement === surface;
    if (active) {
      dismissPointerPrompt();
      if (pointerLockRequest) capabilityResult(pointerLockRequest, true, true);
      pointerLockRequest = null;
      surface.focus({ preventScroll: true });
    }
    sendHostEvent("pointer-lock-event", { active });
  });
  document.addEventListener("pointerlockerror", () => dismissPointerPrompt(new Error("The browser denied mouse capture")));
  document.addEventListener("mousemove", (event) => {
    if (document.pointerLockElement !== surface) return;
    sendHostEvent("pointer-lock-move", { movementX: Number(event.movementX) || 0, movementY: Number(event.movementY) || 0 });
  });

  async function capability(message) {
    const request = message.request;
    const value = text(message.text, LIMITS.capabilityText);
    try {
      if (message.operation === "viewport") { capabilityResult(request, true, { width: Math.max(80, Math.floor(surface.clientWidth - 32)), height: window.innerHeight }); return; }
      if (message.operation === "open-binary") {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = text(message.accept, 100);
        input.style.display = "none";
        const prompt = document.createElement("div");
        prompt.className = "application-pointer-prompt";
        const label = document.createElement("span"); label.textContent = "Select a file for this application";
        const choose = document.createElement("button"); choose.type = "button"; choose.textContent = "CHOOSE FILE";
        const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "secondary"; cancel.textContent = "CANCEL";
        prompt.append(label, choose, cancel, input);
        display.insertBefore(prompt, display.querySelector(".application-viewport"));
        const file = await new Promise((resolve) => {
          const finish = value => { prompt.remove(); resolve(value); };
          input.addEventListener("change", () => finish(input.files?.[0] || null), { once: true });
          input.addEventListener("cancel", () => finish(null), { once: true });
          choose.addEventListener("click", () => input.click());
          cancel.addEventListener("click", () => finish(null), { once: true });
        });
        if (!file) throw new Error("No file selected.");
        if (file.size > 2 * 1024 * 1024) throw new Error("File exceeds the 2 MB import limit.");
        capabilityResult(request, true, { name: text(file.name, 120), bytes: Array.from(new Uint8Array(await file.arrayBuffer())) }); return;
      }
      if (message.operation === "download-binary") {
        if (!Array.isArray(message.bytes) || message.bytes.length > 2 * 1024 * 1024 ||
            !message.bytes.every(byte => Number.isInteger(byte) && byte >= 0 && byte <= 255)) throw new Error("Invalid binary download.");
        const filename = text(message.filename, 120).replace(/[^a-zA-Z0-9._-]/g, "-") || "stationnet-download.bin";
        const mime = /^[\w.+-]+\/[\w.+-]+$/.test(message.mime) ? message.mime : "application/octet-stream";
        const url = URL.createObjectURL(new Blob([Uint8Array.from(message.bytes)], { type: mime }));
        const link = document.createElement("a"); link.href = url; link.download = filename; link.hidden = true;
        document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        capabilityResult(request, true, true); return;
      }
      if (message.operation?.startsWith("midi-")) {
        if (message.operation === "midi-stop") { midiEngine?.stop(); capabilityResult(request, true, true); return; }
        if (message.operation === "midi-position") { capabilityResult(request, true, midiEngine?.position || 0); return; }
        if (message.operation === "midi-pause") { midiEngine?.pause(); capabilityResult(request, true, true); return; }
        if (message.operation === "midi-seek") { midiEngine?.seek(number(message.seconds, 0, 0, 86400)); capabilityResult(request, true, true); return; }
        if (message.operation === "midi-load") {
          if (!Array.isArray(message.bytes) || message.bytes.length > 2 * 1024 * 1024 ||
              !message.bytes.every(byte => Number.isInteger(byte) && byte >= 0 && byte <= 255)) throw new Error("Invalid MIDI data.");
          let timeout;
          try {
            await Promise.race([
              composerMidi().then(engine => engine.loadBinary(Uint8Array.from(message.bytes).buffer)),
              new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("MIDI synthesizer did not finish loading.")), 30000); })
            ]);
          } finally { clearTimeout(timeout); }
          capabilityResult(request, true, true); return;
        }
        if (message.operation === "midi-play") { (await composerMidi()).play(); capabilityResult(request, true, true); return; }
      }
      if (message.operation === "pointer-lock-request") {
        requestPointerLock(message); return;
      }
      if (message.operation === "pointer-lock-release") {
        dismissPointerPrompt(new Error("Mouse capture request was released"));
        if (document.pointerLockElement === surface) document.exitPointerLock();
        capabilityResult(request, true, true); return;
      }
      if (message.operation === "presentation-enter-immersive") {
        setImmersive(true); capabilityResult(request, true, "immersive"); return;
      }
      if (message.operation === "presentation-exit-immersive") {
        setImmersive(false); capabilityResult(request, true, "standard"); return;
      }
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
    if (message.type === "initialized") {
      clearTimeout(startupTimer); status.textContent = "PROGRAM STATUS / INITIALIZED";
      reportVisibility();
    }
    else if (message.type === "ready") {
      status.textContent = "PROGRAM STATUS / RUNNING";
      if (display?.dataset.appPresentation === "immersive") setImmersive(true);
    }
    else if (message.type === "render") render(message);
    else if (message.type === "graphics-image") registerImage(message);
    else if (message.type === "graphics-decode-image") decodeBundledImage(message);
    else if (message.type === "graphics-map") registerMap(message);
    else if (message.type === "graphics-frame") registerFrame(message);
    else if (message.type === "storage") storage(message);
    else if (message.type === "scores") scores(message);
    else if (message.type === "multiplayer") multiplayer(message);
    else if (message.type === "realtime") realtime(message);
    else if (message.type === "capability") capability(message);
    else if (message.type === "sound") sound(message);
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
  const pressedKeys = new Map();
  const keyboardEvent = (event, phase) => ({
    key: text(event.key, 40), code: text(event.code, 40), phase, repeat: Boolean(event.repeat),
    ctrlKey: event.ctrlKey, altKey: event.altKey, shiftKey: event.shiftKey, metaKey: event.metaKey
  });
  const preventGameKeyDefault = (event) => {
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", " ", "Backspace", "Delete"].includes(event.key)) event.preventDefault();
    if ((event.ctrlKey || event.metaKey) && ["KeyS", "KeyZ", "KeyY", "KeyC", "KeyV", "KeyD", "KeyA"].includes(event.code)) event.preventDefault();
  };
  const releasePressedKeys = () => {
    pressedKeys.forEach((event) => keyboardActions.forEach((action) => interact(action, { ...event, phase: "up", repeat: false })));
    pressedKeys.clear();
  };
  addEventListener("keydown", (event) => {
    unlockAudio();
    if (event.key === "Escape" && immersive && document.pointerLockElement !== surface) {
      event.preventDefault(); setImmersive(false); return;
    }
    if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
    if (event.target.tagName === "BUTTON" && ["Enter", " "].includes(event.key)) return;
    preventGameKeyDefault(event);
    const payload = keyboardEvent(event, "down");
    if (!event.repeat) pressedKeys.set(payload.code || payload.key, payload);
    keyboardActions.forEach((action) => interact(action, payload));
  });
  addEventListener("keyup", (event) => {
    const identity = text(event.code, 40) || text(event.key, 40);
    if (!pressedKeys.has(identity)) return;
    preventGameKeyDefault(event);
    pressedKeys.delete(identity);
    const payload = keyboardEvent(event, "up");
    keyboardActions.forEach((action) => interact(action, payload));
  });
  addEventListener("pointerdown", unlockAudio, { capture: true });

  const reportVisibility = () => {
    const value = document.hidden || !document.hasFocus() ? "hidden" : "visible";
    if (value === "hidden") {
      releasePressedKeys();
      if (document.pointerLockElement === surface) document.exitPointerLock();
    }
    if (frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "animation-visibility", visible: value === "visible" }, "*");
    lifecycleActions.forEach((action) => interact(action, { value }));
  };
  document.addEventListener("visibilitychange", reportVisibility);
  addEventListener("blur", reportVisibility);
  addEventListener("focus", reportVisibility);

  function terminate() {
    if (realtimeSocket) { realtimeSocket.close(); realtimeSocket = null; realtimeSession = null; }
    if (closed) return; closed = true;
    clearTimeout(startupTimer);
    dismissPointerPrompt(new Error("Application terminated"));
    if (document.pointerLockElement === surface) document.exitPointerLock();
    setImmersive(false);
    if (frame.contentWindow) frame.contentWindow.postMessage({ protocol: PROTOCOL, type: "terminate" }, "*");
    stopSounds();
    midiEngine?.stop();
    images.forEach((bitmap) => bitmap?.close?.()); images.clear(); decodedImages.clear(); tileMaps.clear();
    frame.remove(); keyboardActions.clear(); lifecycleActions.clear(); pressedKeys.clear();
  }
  if (immersiveButton) immersiveButton.addEventListener("click", () => setImmersive(!immersive));
  if (immersiveExit) immersiveExit.addEventListener("click", () => setImmersive(false));

  const compatibilityResult = environmentCompatibility();
  if (compatibility) {
    if (compatibilityResult.supported) {
      compatibility.firstElementChild.textContent = "COMPATIBILITY / SUPPORTED ON THIS DEVICE";
    } else {
      compatibility.classList.add("warning");
      compatibility.firstElementChild.textContent = "COMPATIBILITY / REQUIREMENTS NOT MET";
    }
  }
  if (compatibilityResult.supported) {
    startRuntime();
  } else {
    const reasons = [];
    if (!compatibilityResult.platformMatch) reasons.push("this device does not match the declared desktop/touch platforms");
    if (compatibilityResult.missing.length) reasons.push(`missing input capabilities: ${compatibilityResult.missing.join(", ")}`);
    launchWarning.hidden = false;
    launchWarning.querySelector("[data-app-launch-message]").textContent = `This application may not work correctly: ${reasons.join("; ")}.`;
    launchWarning.querySelector("[data-app-launch-anyway]").addEventListener("click", () => {
      launchWarning.hidden = true; startRuntime();
    }, { once: true });
  }
  addEventListener("pagehide", terminate, { once: true });
})();
