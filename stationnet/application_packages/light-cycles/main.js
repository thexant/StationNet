// LIGHT CYCLES - TRON-style arena for StationNet.
// v1.2: responsive touch steering, on-screen gamepad, dark electronic chase score.
// Trails live in an indexed framebuffer (1 cell = 4px); bikes/HUD are canvas commands.
StationNet.start(async (ui) => {
  const W = 160, H = 100, CS = 4, TOP = 22, CW = W * CS, CH = H * CS + TOP;
  const COLORS = ["#00e5ff", "#ff8a00", "#ff2a2a", "#ffee00", "#ff3df2",
                  "#3dff6a", "#a46bff", "#e8e8ff", "#ff7fa8"];
  const DIRS = [[0, -1], [1, 0], [0, 1], [-1, 0]]; // up right down left
  const KEYS = { KeyW: 0, ArrowUp: 0, KeyD: 1, ArrowRight: 1, KeyS: 2, ArrowDown: 2, KeyA: 3, ArrowLeft: 3 };
  const LEVELS = {
    Easy:   { speed: 24, mult: 1, look: 4 },
    Medium: { speed: 28, mult: 2, look: 8 },
    Hard:   { speed: 32, mult: 3, look: 14 },
    Insane: { speed: 36, mult: 5, look: 20 },
  };
  const cfg = { opp: 3, level: "Medium", music: true };
  try {
    const s = await ui.storage.get("cfg");
    if (s && typeof s === "object") {
      if (s.opp >= 1 && s.opp <= 8 && LEVELS[s.level]) { cfg.opp = s.opp; cfg.level = s.level; }
      if (typeof s.music === "boolean") cfg.music = s.music;
    }
  } catch (e) { /* defaults */ }

  // ---------- sound ----------
  const beep = (f, ms, w = "square", v = 0.08) => ui.sound.playTone(f, ms, { waveform: w, volume: v });
  // Original 30-second dark electronic pursuit loop. Local and fully offline.
  // Sample voices allow music to be stopped without silencing crash/turn sounds.
  let musicSample = null, musicVoice = null;
  try { musicSample = await ui.sound.load("assets/grid_pursuit.wav"); }
  catch (e) { /* The game remains playable if this asset cannot be decoded. */ }
  function stopMusic() {
    if (musicVoice !== null) {
      try { ui.sound.stopSample(musicVoice); } catch (e) { /* already ended */ }
      musicVoice = null;
    }
  }
  function playMusic() {
    if (!cfg.music || !musicSample || musicVoice !== null || paused || mode === "menu") return;
    try { musicVoice = ui.sound.playSample(musicSample, { loop: true, volume: 0.34 }); }
    catch (e) { musicVoice = null; }
  }
  function toggleMusic() {
    cfg.music = !cfg.music;
    save();
    if (cfg.music) playMusic(); else stopMusic();
    if (mode === "menu") ui.update("menu-music", musicButton());
    else ui.update("game-help", gameHelp());
  }
  function setPaused(value) {
    if (mode !== "count" && mode !== "play") return;
    paused = value;
    if (paused) stopMusic(); else playMusic();
  }

  // ---------- state ----------
  const grid = new Uint8Array(W * H);
  const fb = ui.graphics.framebuffer("arena", W, H, { format: "indexed" });
  fb.palette.set([0, 0, 0, 0], 0);
  COLORS.forEach((c, i) => fb.palette.set(
    [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16), 255], (i + 1) * 4));
  const frameCmd = ui.graphics.frame(fb, 0, TOP, CW, CH - TOP);

  const gridSegs = [];
  for (let x = 0; x <= CW; x += 32) gridSegs.push([x, TOP, x, CH]);
  for (let y = TOP; y <= CH; y += 32) gridSegs.push([0, y, CW, y]);
  const borderSegs = [[1, TOP + 1, CW - 1, TOP + 1], [CW - 1, TOP + 1, CW - 1, CH - 1],
                      [CW - 1, CH - 1, 1, CH - 1], [1, CH - 1, 1, TOP + 1]];

  let mode = "menu", bikes = [], fx = [], acc = 0, cd = 0, lastCd = 0;
  let result = null, endTimer = 0, score = 0, paused = false, dirty = true;
  const touchPointers = new Map(), directedPointers = new Set();
  const padActions = [
    { label: "Pause", code: "KeyP" },
    { label: "Next", code: "Space" },
    { label: "Menu", code: "KeyM" },
    { label: "Music", code: "KeyN" },
  ];
  const arenaOptions = { id: "field", label: "Light cycle arena: swipe to steer", touchLayout: "gamepad", onPointer: arenaPointer };
  const directionNames = { up: 0, right: 1, down: 2, left: 3 };

  function steer(nd) {
    const p = bikes[0];
    if ((mode !== "count" && mode !== "play") || !p || !p.alive || paused || nd === undefined) return false;
    const last = p.q.length ? p.q[p.q.length - 1] : p.d;
    // Keep the original buffered turns and no-instant-reverse rule intact.
    if (nd === last || nd === (last + 2) % 4 || p.q.length >= 3) return false;
    p.q.push(nd);
    beep(700, 20, "square", 0.025);
    return true;
  }
  function arenaPointer(event) {
    const id = event.pointerId;
    if (event.phase === "start") {
      // Direct movement detects a finger swipe before its end/gesture callback.
      if (event.pointerType === "touch") touchPointers.set(id, { x: event.x, y: event.y });
      directedPointers.delete(id);
      return;
    }
    if (event.phase === "move" && event.pointerType === "touch") {
      const origin = touchPointers.get(id);
      if (!origin || directedPointers.has(id)) return;
      const dx = event.x - origin.x, dy = event.y - origin.y;
      // Logical coordinates: 24 pixels gives a deliberate mobile swipe.
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 24) return;
      directedPointers.add(id);
      steer(Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 1 : 3) : (dy > 0 ? 2 : 0));
      return;
    }
    if (event.phase === "end" || event.phase === "cancel") {
      touchPointers.delete(id);
      // Retain direction flag through a possible subsequent swipe gesture.
      return;
    }
    if (event.phase !== "gesture" || event.gesture !== "swipe" || directedPointers.has(id)) return;
    const direction = String(event.direction || "").toLowerCase().replace(/^arrow/, "");
    let nd = directionNames[direction];
    if (nd === undefined && Number.isFinite(event.dx) && Number.isFinite(event.dy)) {
      if (Math.max(Math.abs(event.dx), Math.abs(event.dy)) < 14) return;
      nd = Math.abs(event.dx) > Math.abs(event.dy) ? (event.dx > 0 ? 1 : 3) : (event.dy > 0 ? 2 : 0);
    }
    if (nd !== undefined) { directedPointers.add(id); steer(nd); }
  }

  // ---------- menu ----------
  const cfgText = () => ui.text(`Opponents: ${cfg.opp}    Difficulty: ${cfg.level}`, { id: "cfg" });
  const save = () => { try { Promise.resolve(ui.storage.set("cfg", cfg)).catch(() => {}); } catch (e) { /* ignore */ } };
  const musicButton = () => ui.button(`MUSIC: ${cfg.music ? "ON" : "OFF"}`, toggleMusic, { id: "menu-music" });
  const gameHelp = () => ui.text(`Swipe / D-pad or WASD / arrows to steer  |  P pause  |  M menu  |  Space next  |  N music (${cfg.music ? "ON" : "OFF"})`, { id: "game-help" });
  function showMenu() {
    mode = "menu"; stopMusic(); ui.sound.stop();
    ui.render(
      ui.title("LIGHT CYCLES"),
      ui.text("Steer using WASD, arrow keys, swipes on the arena, or the touch D-pad. Avoid walls and trails. Last rider standing wins."),
      ui.menu("Opponents (1-8)", ["1", "2", "3", "4", "5", "6", "7", "8"], ({ value }) => {
        cfg.opp = Number(value); ui.update("cfg", cfgText()); save();
      }),
      ui.menu("Difficulty", Object.keys(LEVELS), ({ value }) => {
        cfg.level = value; ui.update("cfg", cfgText()); save();
      }),
      cfgText(),
      musicButton(),
      ui.button("START (Enter / tap)", startGame)
    );
  }
  function startGame() {
    score = 0;
    ui.render(
      ui.canvas(CW, CH, [], { ...arenaOptions, controls: padActions }),
      gameHelp()
    );
    newRound();
  }

  // ---------- round ----------
  function mark(b) { const k = b.y * W + b.x; grid[k] = b.id + 1; fb.pixels[k] = b.id + 1; dirty = true; }
  function newRound() {
    grid.fill(0); fb.clear(0); fx = []; acc = 0; endTimer = 0; paused = false; result = null;
    touchPointers.clear(); directedPointers.clear();
    const n = cfg.opp + 1; bikes = [];
    for (let i = 0; i < n; i++) {
      const a = Math.PI / 2 + (2 * Math.PI * i) / n;
      const x = Math.round(W / 2 + Math.cos(a) * W * 0.38), y = Math.round(H / 2 + Math.sin(a) * H * 0.36);
      const dx = W / 2 - x, dy = H / 2 - y;
      const d = Math.abs(dx) / W > Math.abs(dy) / H ? (dx > 0 ? 1 : 3) : (dy > 0 ? 2 : 0);
      const b = { id: i, x, y, d, q: [], alive: true, ai: i > 0 };
      bikes.push(b); mark(b);
    }
    mode = "count"; cd = 3; lastCd = 4;
    playMusic();
  }

  function kill(b) {
    b.alive = false;
    fx.push({ x: (b.x + 0.5) * CS, y: (b.y + 0.5) * CS + TOP, t: 0, c: COLORS[b.id] });
    if (b.ai) beep(170 + Math.random() * 60, 140, "sawtooth", 0.05);
    else ui.sound.sequence([440, 330, 247, 165, 110], { duration: 110, waveform: "sawtooth", volume: 0.12 });
  }

  function conclude() {
    if (mode === "over") return;
    mode = "over";
    const L = LEVELS[cfg.level];
    score += bikes.slice(1).filter((b) => !b.alive).length * 10 * L.mult + (result === "win" ? 100 * L.mult : 0);
    if (result === "win") ui.sound.sequence([523, 659, 784, 1047], { duration: 130, waveform: "triangle", volume: 0.1 });
    if (score > 0) { try { Promise.resolve(ui.scores.submit(score)).catch(() => {}); } catch (e) { /* ignore */ } }
  }

  function tick() {
    const live = bikes.filter((b) => b.alive);
    for (const b of live) {
      if (b.ai) aiSteer(b); else if (b.q.length) b.d = b.q.shift();
    }
    const hits = new Map();
    for (const b of live) {
      b.nx = b.x + DIRS[b.d][0]; b.ny = b.y + DIRS[b.d][1];
      b.nk = b.nx < 0 || b.ny < 0 || b.nx >= W || b.ny >= H ? -1 : b.ny * W + b.nx;
      if (b.nk >= 0) hits.set(b.nk, (hits.get(b.nk) || 0) + 1);
    }
    const dead = live.filter((b) => b.nk < 0 || grid[b.nk] !== 0 || hits.get(b.nk) > 1);
    for (const b of live) if (!dead.includes(b)) { b.x = b.nx; b.y = b.ny; mark(b); }
    dead.forEach(kill);
    if (!result) {
      const alive = bikes.filter((b) => b.alive).length;
      if (alive === 0) result = "draw";
      else if (!bikes[0].alive) { result = "lose"; endTimer = 1.8; }
      else if (alive === 1) result = "win";
      if (result === "win" || result === "draw") conclude();
    }
  }

  // ---------- AI ----------
  const free = (x, y) => x >= 0 && y >= 0 && x < W && y < H && grid[y * W + x] === 0;
  function ray(x, y, d, max) {
    let n = 0; const [dx, dy] = DIRS[d];
    while (n < max && free(x + dx * (n + 1), y + dy * (n + 1))) n++;
    return n;
  }
  const stamp = new Uint32Array(W * H), queue = new Int32Array(W * H); let st = 0;
  function area(x, y, cap) {
    if (!free(x, y)) return 0;
    st++; let h = 0, t = 0; queue[t++] = y * W + x; stamp[y * W + x] = st;
    while (h < t && t < cap) {
      const k = queue[h++], cx = k % W, cy = (k / W) | 0;
      for (let i = 0; i < 4; i++) {
        const nx = cx + DIRS[i][0], ny = cy + DIRS[i][1];
        if (free(nx, ny) && stamp[ny * W + nx] !== st) { stamp[ny * W + nx] = st; queue[t++] = ny * W + nx; }
      }
    }
    return t;
  }
  function aiSteer(b) {
    const lv = cfg.level, look = LEVELS[lv].look;
    const straight = ray(b.x, b.y, b.d, look);
    const opts = [b.d, (b.d + 3) % 4, (b.d + 1) % 4].filter((d) => free(b.x + DIRS[d][0], b.y + DIRS[d][1]));
    if (!opts.length) return;
    if (lv === "Easy") {
      if (straight >= look) { if (Math.random() > 0.02) return; }
      else if (Math.random() < 0.15) return; // blunder: reacts too late
      b.d = opts[(Math.random() * opts.length) | 0]; return;
    }
    if (lv === "Medium" && straight >= look && Math.random() > 0.01) return;
    let foe = null, fd = 1e9;
    for (const o of bikes) {
      if (o === b || !o.alive) continue;
      const m = Math.abs(o.x - b.x) + Math.abs(o.y - b.y);
      if (m < fd) { fd = m; foe = o; }
    }
    let best = b.d, bestS = -1e9;
    for (const d of opts) {
      const nx = b.x + DIRS[d][0], ny = b.y + DIRS[d][1];
      let s = Math.min(ray(nx, ny, d, look), look) + (d === b.d ? 2 : 0) + Math.random() * 1.5;
      let ar = 0;
      if (lv === "Hard" || lv === "Insane") {
        ar = area(nx, ny, lv === "Insane" ? 800 : 350);
        s += ar * (lv === "Insane" ? 0.08 : 0.05);
        for (const o of bikes) {
          if (o === b || !o.alive) continue;
          if (o.x + DIRS[o.d][0] === nx && o.y + DIRS[o.d][1] === ny) s -= lv === "Insane" ? 60 : 30;
        }
      }
      if (lv === "Insane" && foe && fd < 50 && ar > 150) {
        s += (fd - (Math.abs(foe.x - nx) + Math.abs(foe.y - ny))) * 0.8; // hunt the nearest rider
      }
      if (s > bestS) { bestS = s; best = d; }
    }
    b.d = best;
  }

  // ---------- input ----------
  ui.onKey(({ code, phase, repeat }) => {
    if (phase !== "down" || repeat) return;
    if (code === "KeyN") { toggleMusic(); return; }
    if (mode === "menu") { if (code === "Enter" || code === "Space") startGame(); return; }
    if (code === "KeyM") { showMenu(); return; }
    if (mode === "over") { if (code === "Space" || code === "Enter") newRound(); return; }
    if (code === "KeyP" || code === "Enter") { setPaused(!paused); return; }
    steer(KEYS[code]);
  });
  ui.onVisibility(({ value }) => { if (value === "hidden") setPaused(true); });
  ui.onCleanup(() => stopMusic());

  // ---------- drawing ----------
  const hexA = (a) => Math.max(0, Math.min(255, Math.round(a))).toString(16).padStart(2, "0");
  const txt = (x, y, text, size, color, align = "center") => ({ type: "text", x, y, text, size, color, align });
  function draw() {
    if (dirty) { fb.present(); dirty = false; }
    const cmds = [
      { type: "rect", x: 0, y: 0, width: CW, height: CH, color: "#02050d" },
      { type: "lines", segments: gridSegs, color: "#0a2a44", width: 1 },
      frameCmd,
    ];
    for (const b of bikes) {
      if (!b.alive) continue;
      const cx = (b.x + 0.5) * CS, cy = (b.y + 0.5) * CS + TOP, [dx, dy] = DIRS[b.d], px = -dy, py = dx;
      cmds.push({ type: "circle", x: cx, y: cy, radius: 10, color: COLORS[b.id] + "30" });
      cmds.push({ type: "polygon", fill: COLORS[b.id], stroke: "#ffffff", width: 1, points: [
        [cx + dx * 8, cy + dy * 8], [cx - dx * 5 + px * 4, cy - dy * 5 + py * 4], [cx - dx * 5 - px * 4, cy - dy * 5 - py * 4]] });
    }
    for (const f of fx) {
      const p = f.t / 0.7;
      cmds.push({ type: "circle", x: f.x, y: f.y, radius: 3 + p * 42, color: f.c + hexA(150 * (1 - p)) });
      cmds.push({ type: "circle", x: f.x, y: f.y, radius: p * 18, color: "#ffffff" + hexA(200 * (1 - p)) });
    }
    cmds.push({ type: "lines", segments: borderSegs, color: "#2fd8ff", width: 3 });
    cmds.push({ type: "rect", x: 0, y: 0, width: CW, height: TOP, color: "#02050d" });
    cmds.push(txt(8, 11, `SCORE ${score}`, 13, "#00e5ff", "left"));
    cmds.push(txt(CW / 2, 11, `RIVALS ${bikes.slice(1).filter((b) => b.alive).length}/${cfg.opp}`, 13, "#ff8a00"));
    cmds.push(txt(CW - 8, 11, cfg.level.toUpperCase(), 13, "#e8e8ff", "right"));
    const mid = TOP + (CH - TOP) / 2;
    if (paused) cmds.push(txt(CW / 2, mid, "PAUSED", 40, "#ffffff"));
    else if (mode === "count") cmds.push(txt(CW / 2, mid, String(Math.ceil(cd)), 64, "#ffffff"));
    else if (result) {
      const m = { win: ["VICTORY", "#3dff6a"], lose: ["DEREZZED", "#ff2a2a"], draw: ["MUTUAL DEREZ", "#ffee00"] }[result];
      cmds.push(txt(CW / 2, mid - 10, m[0], 44, m[1]));
      if (mode === "over") cmds.push(txt(CW / 2, mid + 30, "NEXT / SPACE: again     MENU / M: exit", 16, "#ffffff"));
    }
    // Preserve touch buttons when redrawing; only the canvas bitmap changes.
    ui.update("field", ui.canvas(CW, CH, cmds, arenaOptions));
  }

  ui.animate(({ delta }) => {
    if (mode === "menu") return;
    const dt = Math.min(delta, 0.05);
    if (!paused) {
      if (mode === "count") {
        cd -= dt;
        const c = Math.ceil(cd);
        if (c !== lastCd && c > 0) { beep(440, 120, "square", 0.07); lastCd = c; }
        if (cd <= 0) { mode = "play"; beep(880, 300, "square", 0.09); }
      } else if (mode === "play") {
        acc += dt;
        const step = 1 / LEVELS[cfg.level].speed;
        for (let n = 0; acc >= step && n < 4 && mode === "play"; n++) { acc -= step; tick(); }
        if (result === "lose" && mode === "play" && (endTimer -= dt) <= 0) conclude();
      }
      for (const f of fx) f.t += dt;
      fx = fx.filter((f) => f.t < 0.7);
    }
    draw();
  });

  showMenu();
});
