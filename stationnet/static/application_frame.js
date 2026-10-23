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

  const flushCanvasUpdates = () => {
    pendingCanvasUpdates.forEach((message) => parent.postMessage(message, "*"));
    pendingCanvasUpdates.clear();
  };

  const scheduleAnimations = () => {
    let runnable = false;
    for (const state of animations.values()) {
      if (!state.pending) { runnable = true; break; }
    }
    if (stopped || !visible || animationRequest || (!runnable && !pendingCanvasUpdates.size)) return;
    animationRequest = requestAnimationFrame(runAnimations);
  };

  const runAnimations = (time) => {
    animationRequest = 0;
    flushCanvasUpdates();
    if (!visible || stopped) return;
    animations.forEach((state, action) => {
      if (state.pending) return;
      if (state.nextTime != null && time + .1 < state.nextTime) return;
      const delta = state.lastTime == null ? 0 : Math.max(0, (time - state.lastTime) / 1000);
      state.lastTime = time; state.pending = true;
      if (state.nextTime == null) state.nextTime = time;
      do { state.nextTime += frameInterval; } while (state.nextTime <= time);
      worker.postMessage({ protocol, type: "animation-frame", action, time, delta });
    });
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
    if (event.data.type === "animation") {
      const action = String(event.data.action || "");
      if (event.data.enabled && action) animations.set(action, { pending: false, lastTime: null, nextTime: null });
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
    worker.postMessage(event.data);
    if (event.data.type === "terminate") setTimeout(stop, 25);
  });

  function stop() {
    if (stopped) return;
    stopped = true;
    if (animationRequest) cancelAnimationFrame(animationRequest);
    animationRequest = 0; animations.clear(); pendingCanvasUpdates.clear();
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
