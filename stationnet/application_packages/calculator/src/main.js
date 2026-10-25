// StationNet Calculator - entrypoint.
//
// The whole calculator is drawn on one ui.canvas (beveled keys, LCD display) and
// pointer events are hit-tested against the key rectangles. Keyboard and touch both
// funnel into runAction(), which drives the same model (src/model.js).

StationNet.start(async (ui) => {
  const E = await ui.modules.load("src/engine.js");
  const M = await ui.modules.load("src/model.js");

  let saved = null;
  try { saved = await ui.storage.get("calculator"); } catch (_) { saved = null; }
  const calc = M.createCalculator(E, saved);

  // ------------------------------------------------------------ geometry ---

  const W = 360, PAD = 8, GAP = 4, COLS = 5, BH = 42;
  const BW = Math.floor((W - 2 * PAD - (COLS - 1) * GAP) / COLS);
  const X0 = PAD + Math.floor((W - 2 * PAD - (COLS * BW + (COLS - 1) * GAP)) / 2);
  const TITLE_H = 24, MENU_H = 40, DISP_H = 100;
  const HIST_ROWS = 4, HEAD_H = 40, ROW_H = 34;
  const DISP_Y = TITLE_H + MENU_H + 6;
  const KEYS_Y = DISP_Y + DISP_H + 8;
  const EXPR_ZONE = 184; // history row: left part edits the expression, right part reuses the result

  // -------------------------------------------------------------- colours ---
  // StationNet green phosphor on near-black, with amber for problems.

  const C = {
    frame: "#04160a", edge: "#19a95b", title: "#00ed45", titleInk: "#010401", menu: "#062812",
    lcd: "#031a0b", lcdGlow: "#0a4a22", ink: "#00ed45", inkDim: "#2e9e5a", inkOff: "#1f6b3b",
    amber: "#ffb000", rule: "#0d4a24",
  };
  const STYLE = {
    num: { face: "#0c3f1e", ink: "#9dffbf" },
    fn: { face: "#062a14", ink: "#3fdc7a" },
    op: { face: "#115d2b", ink: "#00ed45" },
    eq: { face: "#00ed45", ink: "#010401" },
    clr: { face: "#0c3f1e", ink: "#ffb000" },
    mem: { face: "#041f0e", ink: "#27b862" },
    mod: { face: "#062a14", ink: "#3fdc7a" },
  };
  const DISABLED_INK = "#1c5a33";

  function shade(hex, f) {
    const n = parseInt(hex.slice(1), 16);
    const ch = (v) => Math.max(0, Math.min(255, Math.round(v * f))).toString(16).padStart(2, "0");
    return "#" + ch((n >> 16) & 255) + ch((n >> 8) & 255) + ch(n & 255);
  }

  // ----------------------------------------------------------------- keys ---

  const key = (label, action, style, alt) => ({ label, action, style, alt });
  const MEM_ROW = [key("MC", "mc", "mem"), key("MR", "mr", "mem"), key("M+", "mplus", "mem"),
    key("M−", "mminus", "mem"), key("MS", "ms", "mem")];
  const STD_FN = [key("(", "(", "fn"), key(")", ")", "fn"), key("%", "%", "fn"),
    key("√", "sqrt", "fn"), key("x²", "sq", "fn")];
  const SCI_ROWS = [
    [key("2nd", "inv", "mod"), key("DEG", "angle", "mod"), key("(", "(", "fn"), key(")", ")", "fn"), key("%", "%", "fn")],
    [key("sin", "sin", "fn", "asin"), key("cos", "cos", "fn", "acos"), key("tan", "tan", "fn", "atan"),
      key("log", "log", "fn", "10^x"), key("ln", "ln", "fn", "e^x")],
    [key("x²", "sq", "fn", "x³"), key("√", "sqrt", "fn", "∛"), key("x^y", "^", "fn"),
      key("y√x", "root", "fn"), key("n!", "!", "fn")],
    [key("π", "pi", "fn"), key("e", "e", "fn"), key("1/x", "recip", "fn"), key("|x|", "abs", "fn"), key("EXP", "exp", "fn")],
  ];
  const MAIN_ROWS = [
    [key("7", "7", "num"), key("8", "8", "num"), key("9", "9", "num"), key("÷", "/", "op"), key("BACK", "back", "clr")],
    [key("4", "4", "num"), key("5", "5", "num"), key("6", "6", "num"), key("×", "*", "op"), key("CE", "ce", "clr")],
    [key("1", "1", "num"), key("2", "2", "num"), key("3", "3", "num"), key("−", "-", "op"), key("C", "c", "clr")],
    [key("0", "0", "num"), key(".", ".", "num"), key("±", "neg", "fn"), key("+", "+", "op"), key("=", "=", "eq")],
  ];

  const layouts = {};
  function layoutFor(mode) {
    if (layouts[mode]) return layouts[mode];
    const rows = [MEM_ROW, ...(mode === "scientific" ? SCI_ROWS : [STD_FN]), ...MAIN_ROWS];
    const keys = [];
    rows.forEach((row, r) => row.forEach((spec, c) => keys.push({
      ...spec, id: spec.action, x: X0 + c * (BW + GAP), y: KEYS_Y + r * (BH + GAP), w: BW, h: BH,
    })));
    const keysBottom = KEYS_Y + rows.length * (BH + GAP) - GAP;
    const histTop = keysBottom + 8;
    const histH = HEAD_H + HIST_ROWS * ROW_H;
    return (layouts[mode] = { mode, keys, histTop, histH, height: histTop + histH + PAD });
  }

  // ------------------------------------------------------------ UI state ---

  let regions = [];        // hit-test rectangles from the last draw
  let pressedId = null;    // key currently held down (pointer or keyboard flash)
  let down = null;         // region where the current pointer press began
  let lastHitId = null;
  let histOffset = 0;
  let lastHistLen = calc.state.history.length;
  let toast = "";
  let toastTimer = null;
  let flashTimer = null;
  let lastSaved = JSON.stringify(calc.snapshot());

  // ------------------------------------------------------------- drawing ---

  const CHAR_W = 0.62; // monospace glyph width as a fraction of font size (conservative)
  function fit(text, maxW, maxSize, minSize, keepEnd) {
    let size = maxSize;
    while (size > minSize && text.length * size * CHAR_W > maxW) size--;
    const cap = Math.max(2, Math.floor(maxW / (size * CHAR_W)));
    let out = text;
    if (out.length > cap) out = keepEnd ? "…" + out.slice(out.length - (cap - 1)) : out.slice(0, cap - 1) + "…";
    return { text: out, size };
  }

  function render() {
    const v = calc.view();
    const L = layoutFor(v.mode);
    const cmds = [];
    const regs = [];
    const rect = (x, y, w, h, color, extra) =>
      cmds.push(Object.assign({ type: "rect", x, y, width: w, height: h, color }, extra));
    const text = (x, y, t, size, color, align, extra) =>
      cmds.push(Object.assign({ type: "text", x, y, text: t, size, color, align: align || "left" }, extra));
    const bevel = (x, y, w, h, face, pressed, extra) => {
      const hi = shade(face, 2.6), lo = shade(face, 0.3);
      rect(x, y, w, h, pressed ? hi : lo);
      rect(x, y, w - 2, h - 2, pressed ? lo : hi);
      rect(x + 2, y + 2, w - 4, h - 4, face, extra);
    };

    // Window frame and classic striped title bar.
    rect(0, 0, W, L.height, C.frame);
    rect(0, 0, W, TITLE_H, C.title);
    cmds.push({ type: "lines", color: C.titleInk, width: 1,
      segments: [6, 10, 16].map((y) => [8, y, W - 8, y]) });
    rect(W / 2 - 56, 3, 112, TITLE_H - 6, C.title);
    text(W / 2, TITLE_H / 2, "Calculator", 14, C.titleInk, "center", { role: "title" });
    cmds.push({ type: "lines", color: C.edge, width: 2,
      segments: [[1, TITLE_H, 1, L.height - 1], [W - 1, TITLE_H, W - 1, L.height - 1],
        [1, L.height - 1, W - 1, L.height - 1]] });

    // Menu bar: mode tabs and Copy.
    rect(2, TITLE_H, W - 4, MENU_H, C.menu);
    const tab = (id, label, x, w) => {
      const active = id === "mode:" + v.mode;
      const held = pressedId === id;
      const y = TITLE_H + 4;
      if (active) rect(x, y, w, 32, C.title);
      else bevel(x, y, w, 32, "#08301a", held);
      text(x + w / 2, y + 16 + (held ? 1 : 0), label, 15, active ? C.titleInk : C.ink, "center", { role: "tab", key: id });
      regs.push({ id, kind: "menu", action: id, x, y, w, h: 32 });
    };
    tab("mode:standard", "Standard", PAD, 92);
    tab("mode:scientific", "Scientific", PAD + 96, 104);
    {
      const x = W - PAD - 64, y = TITLE_H + 4, held = pressedId === "copy";
      bevel(x, y, 64, 32, "#08301a", held);
      text(x + 32, y + 16 + (held ? 1 : 0), "Copy", 15, C.ink, "center", { role: "tab", key: "copy" });
      regs.push({ id: "copy", kind: "menu", action: "copy", x, y, w: 64, h: 32 });
    }

    // LCD display, drawn inset.
    const dx = PAD, dw = W - 2 * PAD, right = dx + dw - 10;
    bevel(dx, DISP_Y, dw, DISP_H, C.lcd, true);
    if (v.angle) {
      text(dx + 12, DISP_Y + 14, "2nd", 12, v.inv ? C.ink : C.inkOff, "left", { role: "inv" });
      text(dx + 50, DISP_Y + 14, v.angle, 12, C.inkDim, "left", { role: "angle" });
    }
    if (v.memory) {
      const m = fit("M " + v.memoryText, 190, 12, 10, true);
      text(right, DISP_Y + 14, m.text, m.size, C.inkDim, "right", { role: "memory" });
    }
    const sec = toast ? { text: toast, tone: "dim" } : { text: v.secondary, tone: v.secondaryTone };
    if (sec.text) {
      const f = fit(sec.text, dw - 24, 14, 11, true);
      text(right, DISP_Y + 38, f.text, f.size, sec.tone === "error" ? C.amber : C.inkDim, "right", { role: "secondary" });
    }
    {
      const f = fit(v.primary, dw - 24, v.tone === "error" ? 22 : 36, 16, true);
      const color = v.tone === "error" ? C.amber : C.ink;
      if (v.tone !== "error") text(right + 1, DISP_Y + 75, f.text, f.size, C.lcdGlow, "right");
      text(right, DISP_Y + 74, f.text, f.size, color, "right", { role: "primary" });
    }

    // Keypad.
    for (const k of L.keys) {
      let label = k.label, st = STYLE[k.style];
      let face = st.face, ink = st.ink;
      if (k.alt && v.inv) label = k.alt;
      if (k.action === "angle" && v.angle) label = v.angle;
      if (k.action === "inv" && v.inv) { face = STYLE.eq.face; ink = STYLE.eq.ink; }
      if ((k.action === "mc" || k.action === "mr") && !v.memory) ink = DISABLED_INK;
      const held = pressedId === k.id;
      bevel(k.x, k.y, k.w, k.h, face, held, { key: k.id });
      const size = label.length > 3 ? 14 : label.length > 1 ? 16 : 20;
      text(k.x + k.w / 2, k.y + k.h / 2 + (held ? 1 : 0), label, size, ink, "center", { role: "key", key: k.id });
      regs.push({ id: k.id, kind: "key", action: k.action, x: k.x, y: k.y, w: k.w, h: k.h });
    }

    // History panel.
    const hx = PAD, hw = W - 2 * PAD, total = v.historyCount;
    const maxOffset = Math.max(0, Math.floor((total - 1) / HIST_ROWS) * HIST_ROWS);
    if (histOffset > maxOffset) histOffset = maxOffset;
    bevel(hx, L.histTop, hw, L.histH, C.lcd, true);
    const title = total === 0 ? "History"
      : `History ${histOffset + 1}–${Math.min(histOffset + HIST_ROWS, total)} of ${total}`;
    text(hx + 12, L.histTop + HEAD_H / 2, title, 13, C.inkDim, "left", { role: "history-title" });
    const hbtn = (id, label, x, w, enabled) => {
      const held = pressedId === id && enabled;
      const y = L.histTop + 4;
      bevel(x, y, w, 32, "#08301a", held);
      text(x + w / 2, y + 16 + (held ? 1 : 0), label, 13, enabled ? C.ink : DISABLED_INK, "center", { role: "hist-button", key: id });
      if (enabled) regs.push({ id, kind: "hist", action: id, x, y, w, h: 32 });
    };
    const clearX = hx + hw - 6 - 52, olderX = clearX - 4 - 56, newerX = olderX - 4 - 56;
    hbtn("hist:newer", "Newer", newerX, 56, histOffset > 0);
    hbtn("hist:older", "Older", olderX, 56, histOffset + HIST_ROWS < total);
    hbtn("hist:clear", "Clear", clearX, 52, total > 0);
    const rows = calc.historyRows(histOffset, HIST_ROWS);
    if (!rows.length) {
      text(hx + hw / 2, L.histTop + HEAD_H + ROW_H / 2, "No calculations yet", 13, C.inkOff, "center", { role: "history-empty" });
    }
    rows.forEach((row, slot) => {
      const y = L.histTop + HEAD_H + slot * ROW_H;
      cmds.push({ type: "lines", color: C.rule, width: 1, segments: [[hx + 4, y, hx + hw - 4, y]] });
      const exprId = "hist:expr:" + row.index, useId = "hist:use:" + row.index;
      if (pressedId === exprId) rect(hx + 3, y + 1, EXPR_ZONE - 4, ROW_H - 1, "#0a3a1c");
      if (pressedId === useId) rect(hx + EXPR_ZONE + 1, y + 1, hw - EXPR_ZONE - 4, ROW_H - 1, "#0a3a1c");
      const e = fit(row.expr, EXPR_ZONE - 20, 13, 11, false);
      text(hx + 12, y + ROW_H / 2, e.text, e.size, C.inkDim, "left", { role: "history-expr", key: exprId });
      const r = fit("= " + row.result.replace(/^-/, "−"), hw - EXPR_ZONE - 20, 15, 11, false);
      text(hx + hw - 10, y + ROW_H / 2, r.text, r.size, C.ink, "right", { role: "history-result", key: useId });
      regs.push({ id: exprId, kind: "hist", action: exprId, x: hx, y, w: EXPR_ZONE, h: ROW_H });
      regs.push({ id: useId, kind: "hist", action: useId, x: hx + EXPR_ZONE, y, w: hw - EXPR_ZONE, h: ROW_H });
    });
    if (rows.length) {
      cmds.push({ type: "lines", color: C.rule, width: 1,
        segments: [[hx + EXPR_ZONE, L.histTop + HEAD_H + 4, hx + EXPR_ZONE, L.histTop + HEAD_H + rows.length * ROW_H - 4]] });
    }

    regions = regs;
    const label = `Calculator, ${v.mode} mode. Display: ${v.primary}.` + (v.secondary ? ` ${v.secondary}.` : "");
    return ui.canvas(W, L.height, cmds, { id: "calc", label, onPointer });
  }

  const HELP = [
    "Type on the keyboard or tap the keys. Enter or = calculates, Backspace deletes, Delete clears the entry, Esc clears everything.",
    "Scientific mode: s, c, t = sin, cos, tan; l = log; n = ln; p = π; e = e; r = √; q = x²; y = y√x; i = 2nd; d = DEG/RAD/GRAD; m = switch mode. Shift+E = EXP.",
    "Memory: Shift+M store, Shift+R recall, Shift+L clear, Shift+P add, Shift+Q subtract. In History, tap an expression to edit it or its result to reuse it.",
  ];

  function renderAll() {
    ui.render(render(), ...HELP.map((t, i) => ui.text(t, { id: "help" + i })));
  }
  function draw() {
    ui.update("calc", render());
  }

  // ------------------------------------------------------------- actions ---

  function persist() {
    const json = JSON.stringify(calc.snapshot());
    if (json === lastSaved) return;
    lastSaved = json;
    try {
      Promise.resolve(ui.storage.set("calculator", JSON.parse(json))).catch(() => {});
    } catch (_) { /* storage unavailable: the calculator still works */ }
  }

  function showToast(msg) {
    toast = msg;
    if (toastTimer) toastTimer();
    toastTimer = ui.timeout(() => { toast = ""; toastTimer = null; draw(); }, 1600);
    draw();
  }

  async function copyValue() {
    const v = calc.view();
    const value = calc.currentValue();
    if (v.tone === "error" || value === undefined) { showToast("Nothing to copy"); return; }
    try {
      await ui.clipboard.writeText(E.format(value));
      showToast("Copied " + E.format(value));
    } catch (_) {
      showToast("Copy was blocked");
    }
  }

  /** Run one calculator or interface action. Returns true if it changed anything. */
  function runAction(action) {
    toast = "";
    let changedMode = false;
    if (action === "copy") { copyValue(); return true; }
    if (action.startsWith("hist:")) {
      const parts = action.split(":");
      if (parts[1] === "newer") histOffset = Math.max(0, histOffset - HIST_ROWS);
      else if (parts[1] === "older") histOffset += HIST_ROWS;
      else if (parts[1] === "clear") { calc.clearHistory(); histOffset = 0; }
      else if (parts[1] === "expr") calc.recallExpression(Number(parts[2]));
      else if (parts[1] === "use") calc.useResult(Number(parts[2]));
      else return false;
    } else {
      const before = calc.state.mode;
      if (!calc.press(action)) return false;
      changedMode = calc.state.mode !== before;
    }
    if (calc.state.history.length > lastHistLen) histOffset = 0; // show the newest entry
    lastHistLen = calc.state.history.length;
    persist();
    if (changedMode) renderAll(); else draw();
    return true;
  }

  // ---------------------------------------------------------- touch/mouse ---

  function hitTest(x, y) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    for (const r of regions) {
      if (x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h) return r;
    }
    return null;
  }

  // A key fires when the pointer is released on the same key it was pressed on,
  // so a finger that slides away (to scroll, say) cancels the press.
  function onPointer(ev) {
    const hit = hitTest(ev.x, ev.y);
    if (ev.phase === "start") {
      down = hit;
      lastHitId = hit ? hit.id : null;
      pressedId = hit ? hit.id : null;
      if (hit) draw();
    } else if (ev.phase === "move") {
      if (!down) return;
      const id = hit ? hit.id : lastHitId;
      lastHitId = hit ? hit.id : null;
      const next = hit && hit.id === down.id ? down.id : null;
      if (next !== pressedId) { pressedId = next; draw(); }
    } else if (ev.phase === "end") {
      if (!down) return;
      const target = down;
      const endedOn = Number.isFinite(ev.x) && Number.isFinite(ev.y) ? (hit ? hit.id : null) : lastHitId;
      down = null;
      pressedId = null;
      lastHitId = null;
      if (endedOn === target.id && runAction(target.action)) return;
      draw();
    }
  }

  // -------------------------------------------------------------- keyboard ---

  const PLAIN = {
    s: "sin", c: "cos", t: "tan", l: "log", n: "ln", p: "pi", e: "e", r: "sqrt", q: "sq",
    y: "root", i: "inv", d: "angle", m: "mode", x: "*",
  };
  const WITH_SHIFT = { m: "ms", r: "mr", l: "mc", p: "mplus", q: "mminus", e: "exp" };
  const SYMBOLS = {
    ".": ".", ",": ".", "+": "+", "-": "-", "*": "*", "/": "/", "^": "^", "(": "(", ")": ")",
    "!": "!", "%": "%", "=": "=", Enter: "=", Backspace: "back", Delete: "ce", Escape: "c",
  };

  function keyToAction(ev) {
    const k = ev.key;
    if (typeof k !== "string") return null;
    if (/^[0-9]$/.test(k)) return k;
    if (Object.prototype.hasOwnProperty.call(SYMBOLS, k)) return SYMBOLS[k];
    if (/^[a-zA-Z]$/.test(k)) {
      const l = k.toLowerCase();
      const map = ev.shiftKey ? WITH_SHIFT : PLAIN;
      return Object.prototype.hasOwnProperty.call(map, l) ? map[l] : null;
    }
    return null;
  }

  ui.onKey((ev) => {
    if (ev.phase !== "down") return;
    if (ev.ctrlKey || ev.altKey || ev.metaKey) return; // leave browser shortcuts alone
    if (ev.repeat && ev.key !== "Backspace") return;
    const action = keyToAction(ev);
    if (!action) return;
    const mode = calc.state.mode;
    if (!runAction(action)) return;
    if (mode === calc.state.mode) { // brief key-press flash on the matching key
      pressedId = action;
      draw();
      if (flashTimer) flashTimer();
      flashTimer = ui.timeout(() => { pressedId = null; flashTimer = null; draw(); }, 110);
    }
  });

  ui.onCleanup(() => {
    if (toastTimer) toastTimer();
    if (flashTimer) flashTimer();
  });

  renderAll();
});
