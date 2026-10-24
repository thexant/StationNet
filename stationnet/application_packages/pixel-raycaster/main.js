"use strict";

StationNet.start((ui) => {
  const W = 320, H = 200;
  const wall = [
    "111111111111", "100000000001", "102000000301", "100000000001",
    "100011100001", "100010000001", "100010000001", "100000000001",
    "100000000001", "111111111111"
  ];
  const frame = ui.graphics.framebuffer("view", W, H, { format: "indexed" });
  const tex = new Uint8Array(3 * 32 * 32);
  for (let type = 0; type < 3; type++) for (let y = 0; y < 32; y++) for (let x = 0; x < 32; x++) {
    const mortar = y % 8 === 0 || (x + (Math.floor(y / 8) % 2) * 8) % 16 === 0;
    tex[type * 1024 + y * 32 + x] = mortar ? 20 : 32 + type * 32 + ((x * 13 + y * 7) & 15);
  }
  for (let i = 0; i < 256; i++) {
    const p = i * 4;
    frame.palette[p] = i < 32 ? i * 2 : i < 64 ? 110 + (i - 32) * 3 : i < 96 ? 35 : 60;
    frame.palette[p + 1] = i < 32 ? i * 2 + 10 : i < 64 ? 72 + (i - 32) * 2 : i < 96 ? 90 + (i - 64) * 3 : 50 + (i - 96) / 4;
    frame.palette[p + 2] = i < 32 ? i * 3 + 25 : i < 64 ? 48 : i < 96 ? 130 : 65;
  }
  let px = 3.5, py = 3.5, angle = .2, elapsed = 0, count = 0, renderMs = 0;
  const keys = new Set();
  const scene = ui.canvas(W, H, [ui.graphics.frame(frame, 0, 0, W, H)], { id: "ray", label: "Procedural raycast scene" });
  ui.render(ui.title("Pixel Raycaster"), ui.text("W/S move; A/D turn. Textures and palette are generated in the worker."),
    scene, ui.text("", { id: "stats" }));
  ui.onKey(({ code, phase }) => { if (phase === "down") keys.add(code); else if (phase === "up") keys.delete(code); });
  function solid(x, y) {
    const row = wall[Math.floor(y)];
    return !row || (row[Math.floor(x)] || "1") !== "0";
  }
  ui.animate(({ delta }) => {
    const dt = Math.min(delta, .05);
    if (keys.has("KeyA") || keys.has("ArrowLeft")) angle -= dt * 1.6;
    if (keys.has("KeyD") || keys.has("ArrowRight")) angle += dt * 1.6;
    const move = (keys.has("KeyW") || keys.has("ArrowUp") ? 1 : 0) -
      (keys.has("KeyS") || keys.has("ArrowDown") ? 1 : 0);
    const nx = px + Math.cos(angle) * move * dt * 2.4;
    const ny = py + Math.sin(angle) * move * dt * 2.4;
    if (!solid(nx, py)) px = nx;
    if (!solid(px, ny)) py = ny;
    const start = performance.now();
    const pixels = frame.pixels;
    for (let x = 0; x < W; x++) {
      const ray = angle + Math.atan((x / W - .5) * 1.4);
      const dx = Math.cos(ray), dy = Math.sin(ray);
      let distance = .02, hitX = px, hitY = py;
      while (distance < 16) {
        hitX = px + dx * distance; hitY = py + dy * distance;
        if (solid(hitX, hitY)) break;
        distance += .025;
      }
      const corrected = Math.max(.1, distance * Math.cos(ray - angle));
      const half = Math.min(H, H / corrected * .42);
      const top = Math.max(0, Math.floor(H / 2 - half));
      const bottom = Math.min(H, Math.ceil(H / 2 + half));
      const tile = Number(wall[Math.floor(hitY)]?.[Math.floor(hitX)] || "1") - 1;
      const fx = hitX - Math.floor(hitX), fy = hitY - Math.floor(hitY);
      const vertical = Math.min(fx, 1 - fx) < Math.min(fy, 1 - fy);
      const tx = Math.min(31, Math.floor((vertical ? fy : fx) * 32));
      for (let y = 0; y < H; y++) {
        const offset = y * W + x;
        if (y < top) pixels[offset] = 8 + Math.floor(y / 18);
        else if (y >= bottom) pixels[offset] = 28 - Math.floor((y - H / 2) / 15);
        else {
          const ty = Math.min(31, Math.floor((y - (H / 2 - half)) * 32 / (half * 2)));
          pixels[offset] = tex[Math.max(0, tile) * 1024 + ty * 32 + tx] - (vertical ? 6 : 0);
        }
      }
    }
    frame.present();
    ui.update("ray", scene);
    renderMs += performance.now() - start; elapsed += dt; count++;
    if (count % 60 === 0) ui.update("stats", ui.text(`Worker render: ${(renderMs / count).toFixed(1)} ms/frame; ${(count / elapsed).toFixed(0)} frames/s (includes transfer request)`));
  });
});
