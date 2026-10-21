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
  const trailer = `\n;postMessage({protocol:${JSON.stringify(protocol)},type:"ready"});`;
  const url = URL.createObjectURL(new Blob([runtime, "\n", application, trailer], { type: "application/javascript" }));
  const worker = new Worker(url);
  let stopped = false;

  worker.addEventListener("message", (event) => {
    if (!event.data || event.data.protocol !== protocol) return;
    parent.postMessage(event.data, "*");
    if (event.data.type === "terminated") stop();
  });
  worker.addEventListener("error", (event) => {
    parent.postMessage({ protocol, type: "error", message: String(event.message || "Application failed").slice(0, 500) }, "*");
  });
  addEventListener("message", (event) => {
    if (event.source !== parent || !event.data || event.data.protocol !== protocol || stopped) return;
    worker.postMessage(event.data);
    if (event.data.type === "terminate") setTimeout(stop, 25);
  });

  function stop() {
    if (stopped) return;
    stopped = true;
    worker.terminate();
    URL.revokeObjectURL(url);
  }
  addEventListener("pagehide", stop, { once: true });
})();
