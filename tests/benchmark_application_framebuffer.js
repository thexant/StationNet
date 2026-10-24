"use strict";

// Run with: node tests/benchmark_application_framebuffer.js
// CPU-only benchmark of the worker pixel loop/snapshot and host palette expand.
const { performance } = require("node:perf_hooks");

function measure(width, height, iterations = 120) {
  const indices = new Uint8Array(width * height);
  const palette = new Uint8Array(1024);
  const rgba = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < palette.length; i++) palette[i] = i & 255;
  let snapshot;
  const start = performance.now();
  for (let frame = 0; frame < iterations; frame++) {
    // Procedural textured vertical columns, similar in shape to a raycaster.
    for (let x = 0; x < width; x++) {
      const top = Math.floor(height * (.2 + .12 * Math.sin((x + frame) * .02)));
      const bottom = height - top;
      for (let y = 0; y < height; y++)
        indices[y * width + x] = y < top ? 12 : y >= bottom ? 28 : 32 + ((x * 13 + y * 7) & 63);
    }
    snapshot = indices.slice();
  }
  const workerMs = (performance.now() - start) / iterations;
  const expandStart = performance.now();
  for (let frame = 0; frame < iterations; frame++) {
    for (let i = 0; i < snapshot.length; i++) {
      const source = snapshot[i] * 4, target = i * 4;
      rgba[target] = palette[source]; rgba[target + 1] = palette[source + 1];
      rgba[target + 2] = palette[source + 2]; rgba[target + 3] = palette[source + 3];
    }
  }
  const expandMs = (performance.now() - expandStart) / iterations;
  return { resolution: `${width}x${height}`, workerMs: workerMs.toFixed(2),
    expandMs: expandMs.toFixed(2), transferKiB: snapshot.byteLength / 1024 };
}

measure(320, 200, 120); measure(640, 400, 120); // JIT warm-up
console.table([measure(320, 200, 300), measure(640, 400, 300)]);
