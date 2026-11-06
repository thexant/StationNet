/* Test-only driver using Node's built-in WebSocket and Chromium's test protocol. */
'use strict';
const fs = require('node:fs');
const [websocket, url, requestedWidth, screenshot] = process.argv.slice(2);
const width = Number(requestedWidth);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function main() {
  const socket = new WebSocket(websocket);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, {once: true}); socket.addEventListener('error', reject, {once: true}); });
  let next = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const {resolve, reject, timer} = pending.get(message.id);
    clearTimeout(timer);
    pending.delete(message.id);
    if (message.error) reject(new Error(JSON.stringify(message.error)));
    else resolve(message.result);
  });
  function send(method, params = {}, sessionId) {
    return new Promise((resolve, reject) => {
      const id = ++next;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('Timeout: ' + method)); }, 15000);
      pending.set(id, {resolve, reject, timer});
      socket.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
    });
  }
  let targetId;
  try {
    ({targetId} = await send('Target.createTarget', {url: 'about:blank'}));
    const {sessionId} = await send('Target.attachToTarget', {targetId, flatten: true});
    await send('Page.enable', {}, sessionId);
    await send('Runtime.enable', {}, sessionId);
    await send('Emulation.setDeviceMetricsOverride', {width, height: 1000, deviceScaleFactor: 1, mobile: width < 1100}, sessionId);
    await send('Page.navigate', {url}, sessionId);
    const deadline = Date.now() + 15000;
    let report;
    while (Date.now() < deadline) {
      try {
        const result = await send('Runtime.evaluate', {expression: "document.getElementById('browser-results')?.textContent || ''", returnByValue: true}, sessionId);
        if (result.result.value) { report = JSON.parse(result.result.value); break; }
      } catch (_) { /* Navigation can replace the execution context. */ }
      await delay(80);
    }
    const captured = await send('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false}, sessionId);
    fs.writeFileSync(screenshot, Buffer.from(captured.data, 'base64'));
    if (!report) throw new Error('Browser checks did not finish. See ' + screenshot);
    if (report.width !== width) throw new Error(`Expected ${width}px viewport, got ${report.width}px.`);
    process.stdout.write(JSON.stringify(report));
  } finally {
    if (targetId) await send('Target.closeTarget', {targetId});
    socket.close();
  }
}
main().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
