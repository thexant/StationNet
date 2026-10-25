"use strict";

// This trusted relay is the only script that runs in the sandbox document.
// Untrusted application logic runs in a Worker with the document's network CSP.
(() => {
  const protocol = "stationnet-app-v1";
  const decode = (value) => {
    const bytes = Uint8Array.from(atob(value.trim()), (character) => character.charCodeAt(0));
    return new TextDecoder().decode(bytes);
  };
  const runtime = decode(document.getElementById("stationnet-worker-source").textContent);
  const application = decode(document.getElementById("stationnet-application-source").textContent);
  const files = JSON.parse(document.getElementById("stationnet-package-files").textContent);
  const trailer = `\n;postMessage({protocol:${JSON.stringify(protocol)},type:"ready"});`;
  const url = URL.createObjectURL(new Blob([
    "self.__stationnetPackageFiles = ", JSON.stringify(files), ";\n",
    runtime, "\n", application, trailer
  ], { type: "application/javascript" }));
  const worker = new Worker(url);
  let stopped = false;
  let parentVisible = true;
  let visible = !document.hidden;
  let animationRequest = 0;
  const frameInterval = 1000 / 60;
  const animations = new Map();
  const pendingCanvasUpdates = new Map();
  const pendingFrames = new Map();
  const pendingAnimationBatches = [];

  const flushFrames = () => {
    pendingFrames.forEach((message) => {
      const transfer = [];
      if (message.pixels instanceof Uint8Array) transfer.push(message.pixels.buffer);
      if (message.palette instanceof Uint8Array) transfer.push(message.palette.buffer);
      parent.postMessage(message, "*", transfer);
    });
    pendingFrames.clear();
  };

  const flushCanvasUpdates = () => {
    if (!pendingCanvasUpdates.size) { flushFrames(); return; }
    let frames = Array.from(pendingFrames.values());
    pendingCanvasUpdates.forEach((message) => {
      if (frames.length) {
        message.frames = frames;
        const transfer = [];
        frames.forEach((frame) => {
          if (frame.pixels instanceof Uint8Array) transfer.push(frame.pixels.buffer);
          if (frame.palette instanceof Uint8Array) transfer.push(frame.palette.buffer);
        });
        parent.postMessage(message, "*", transfer);
        frames = [];
      } else parent.postMessage(message, "*");
    });
    pendingFrames.clear();
    pendingCanvasUpdates.clear();
  };

  const scheduleAnimations = () => {
    let runnable = false;
    for (const state of animations.values()) {
      if (!state.pending) { runnable = true; break; }
    }
    if (stopped || !visible || animationRequest || (!runnable && !pendingCanvasUpdates.size && !pendingFrames.size)) return;
    animationRequest = requestAnimationFrame(runAnimations);
  };

  const runAnimations = (time) => {
    animationRequest = 0;
    flushCanvasUpdates();
    if (!visible || stopped) return;
    const due = [];
    animations.forEach((state, action) => {
      if (state.pending) return;
      if (state.nextTime != null && time + .1 < state.nextTime) return;
      const delta = state.lastTime == null ? 0 : Math.max(0, (time - state.lastTime) / 1000);
      state.lastTime = time; state.pending = true;
      if (state.nextTime == null) state.nextTime = time;
      do { state.nextTime += frameInterval; } while (state.nextTime <= time);
      due.push({ action, time, delta });
    });
    if (due.length) {
      if (pendingAnimationBatches.length < 16) {
        pendingAnimationBatches.push(due);
        parent.postMessage({ protocol, type: "gamepad-sample" }, "*");
      } else worker.postMessage({ protocol, type: "animation-frame", frames: due, gamepads: [] });
    }
    scheduleAnimations();
  };

  const setVisible = (value) => {
    parentVisible = Boolean(value);
    visible = parentVisible && !document.hidden;
    if (!visible) {
      if (animationRequest) cancelAnimationFrame(animationRequest);
      animationRequest = 0;
      animations.forEach((state) => { state.lastTime = null; state.nextTime = null; });
      return;
    }
    scheduleAnimations();
  };

  worker.addEventListener("message", (event) => {
    if (!event.data || event.data.protocol !== protocol) return;
    if (event.data.type === "gamepad-sample") return;
    if (event.data.type === "animation") {
      const action = event.data.action;
      if (typeof action !== "string" || !/^animation-[1-9]\d{0,9}$/.test(action)) return;
      if (event.data.enabled && (animations.has(action) || animations.size < 16))
        animations.set(action, { pending: false, lastTime: null, nextTime: null });
      else animations.delete(action);
      scheduleAnimations();
      return;
    }
    if (event.data.type === "animation-ready") {
      const state = animations.get(String(event.data.action || ""));
      if (state) state.pending = false;
      scheduleAnimations();
      return;
    }
    if (event.data.type === "graphics-frame") {
      const value = event.data;
      const id = String(value.id || "");
      if (!/^[A-Za-z0-9_.:-]{1,80}$/.test(id) ||
          !Number.isInteger(value.width) || !Number.isInteger(value.height) ||
          value.width < 1 || value.height < 1 || value.width > 640 || value.height > 400 ||
          value.width * value.height > 256000 ||
          (value.format !== "rgba" && value.format !== "indexed") ||
          !(value.pixels === null || value.pixels instanceof Uint8Array &&
            value.pixels.length === value.width * value.height * (value.format === "rgba" ? 4 : 1)) ||
          !(value.palette === null || value.palette instanceof Uint8Array && value.palette.length === 1024) ||
          (!pendingFrames.has(id) && pendingFrames.size >= 4)) return;
      if (value.pixels === null && pendingFrames.has(id))
        value.pixels = pendingFrames.get(id).pixels;
      pendingFrames.set(id, value);
      scheduleAnimations();
      return;
    }
    if (event.data.type === "graphics-decode-image") {
      const value = event.data;
      if (typeof value.path !== "string" || !/\.png$/i.test(value.path) ||
          !Object.prototype.hasOwnProperty.call(files, value.path) || files[value.path] !== value.data ||
          typeof value.request !== "string" || !/^image-[1-9]\d{0,9}$/.test(value.request)) {
        worker.postMessage({ protocol, type: "graphics-decode-image-result", request: value.request,
          path: value.path, ok: false, error: "Invalid bundled PNG request" });
        return;
      }
    }
    if (event.data.type === "sample" && event.data.operation === "load") {
      const value = event.data;
      if (typeof value.path !== "string" || !/\.wav$/i.test(value.path) ||
          !Object.prototype.hasOwnProperty.call(files, value.path) || files[value.path] !== value.data ||
          typeof value.handle !== "string" || !/^sample-[1-9]\d{0,9}$/.test(value.handle)) {
        worker.postMessage({ protocol, type: "sample-load-result", handle: value.handle,
          ok: false, error: "Invalid bundled WAV request" });
        return;
      }
    }
    const canvas = event.data.type === "render" && event.data.mode === "update"
      && event.data.components?.length === 1 && event.data.components[0]?.type === "canvas";
    if (canvas) {
      pendingCanvasUpdates.set(String(event.data.target || ""), event.data);
      scheduleAnimations();
      return;
    }
    flushCanvasUpdates();
    parent.postMessage(event.data, "*");
    if (event.data.type === "terminated") stop();
  });
  worker.addEventListener("error", (event) => {
    parent.postMessage({ protocol, type: "error", message: String(event.message || "Application failed").slice(0, 500) }, "*");
  });
  addEventListener("message", (event) => {
    if (event.source !== parent || !event.data || event.data.protocol !== protocol || stopped) return;
    if (event.data.type === "animation-visibility") {
      setVisible(event.data.visible);
      return;
    }
    if (event.data.type === "gamepad-state") {
      const batch = pendingAnimationBatches.shift();
      if (batch) {
        worker.postMessage({ protocol, type: "animation-frame", frames: batch,
          gamepads: event.data.gamepads });
      }
      return;
    }
    const transfer = event.data.type === "graphics-decode-image-result" && event.data.pixels instanceof Uint8Array
      ? [event.data.pixels.buffer, ...(event.data.palette instanceof Uint8Array ? [event.data.palette.buffer] : [])] : [];
    worker.postMessage(event.data, transfer);
    if (event.data.type === "terminate") setTimeout(stop, 25);
  });

  function stop() {
    if (stopped) return;
    stopped = true;
    if (animationRequest) cancelAnimationFrame(animationRequest);
    animationRequest = 0; animations.clear(); pendingCanvasUpdates.clear(); pendingFrames.clear(); pendingAnimationBatches.length = 0;
    worker.terminate();
    URL.revokeObjectURL(url);
  }
  document.addEventListener("visibilitychange", () => {
    visible = parentVisible && !document.hidden;
    if (visible) scheduleAnimations();
    else {
      if (animationRequest) cancelAnimationFrame(animationRequest);
      animationRequest = 0;
      animations.forEach((state) => { state.lastTime = null; state.nextTime = null; });
    }
  });
  addEventListener("pagehide", stop, { once: true });
})();
