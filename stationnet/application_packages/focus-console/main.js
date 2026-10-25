StationNet.start((ui) => {
  const PRESETS = {
    classic: { label: "Classic 25/5", f: 25, b: 5 },
    short: { label: "Sprint 15/3", f: 15, b: 3 },
    deep: { label: "Deep 50/10", f: 50, b: 10 }
  };
  const CATS = ["Work", "Study", "Code", "Write", "Read"];
  const KEY = "focus-v1";

  let S = { preset: "classic", cat: "Work", days: {}, total: 0, cycle: 0 };
  let mode = "focus", running = false, endAt = 0, remMs = 0;
  let visible = true, lastSec = -1, longBreak = false;
  let note = "Ready. Press Start or SPACE.";

  const dayKey = (d) => {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  };
  const daysAgo = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return d; };
  const rec = (k) => S.days[k] || { min: 0, n: 0, cat: {} };
  const today = () => rec(dayKey(new Date()));

  const durationMs = () => {
    const p = PRESETS[S.preset];
    if (mode === "focus") return p.f * 60000;
    return (longBreak ? p.b * 3 : p.b) * 60000;
  };

  const save = () => {
    const keys = Object.keys(S.days).sort();
    while (keys.length > 90) delete S.days[keys.shift()];
    ui.storage.set(KEY, S).catch(() => {});
  };

  const streak = () => {
    let n = 0, i = rec(dayKey(new Date())).min > 0 ? 0 : 1;
    for (; i < 90; i++) {
      if (rec(dayKey(daysAgo(i))).min > 0) n++; else break;
    }
    return n;
  };

  // ---------- components ----------
  const timerCanvas = () => {
    const rem = running ? Math.max(0, endAt - Date.now()) : remMs;
    const total = durationMs();
    const lit = Math.floor((1 - rem / total) * 60);
    const on = mode === "focus" ? "#00ed45" : "#55aaff";
    const cmds = [];
    for (let i = 0; i < 60; i++) {
      const a = (i / 60) * Math.PI * 2 - Math.PI / 2;
      cmds.push({
        type: "circle",
        x: 160 + Math.cos(a) * 85,
        y: 112 + Math.sin(a) * 85,
        radius: i % 5 === 0 ? 5 : 3.5,
        color: i < lit ? on : "#12301a"
      });
    }
    const secs = Math.ceil(rem / 1000);
    const mm = String(Math.floor(secs / 60)).padStart(2, "0");
    const ss = String(secs % 60).padStart(2, "0");
    cmds.push({ type: "text", x: 160, y: 80, text: mode === "focus" ? "FOCUS" : (longBreak ? "LONG BREAK" : "BREAK"), size: 14, align: "center", color: on });
    cmds.push({ type: "text", x: 160, y: 112, text: `${mm}:${ss}`, size: 44, align: "center", color: "#ffffff" });
    cmds.push({ type: "text", x: 160, y: 148, text: S.cat.toUpperCase(), size: 12, align: "center", color: "#55ff55" });
    const n = Math.min(today().n, 12);
    for (let i = 0; i < n; i++) {
      cmds.push({ type: "circle", x: 160 - (n - 1) * 8 + i * 16, y: 222, radius: 5, color: "#00ed45" });
    }
    if (n === 0) cmds.push({ type: "text", x: 160, y: 222, text: "no sessions yet today", size: 10, align: "center", color: "#3a6a45" });
    return ui.canvas(320, 240, cmds, { id: "timer", label: "Focus timer" });
  };

  const weekCanvas = () => {
    const vals = [];
    for (let i = 6; i >= 0; i--) {
      const d = daysAgo(i);
      vals.push({ min: rec(dayKey(d)).min, label: "SMTWTFS"[d.getDay()], now: i === 0 });
    }
    const max = Math.max(60, ...vals.map((v) => v.min));
    const cmds = [
      { type: "text", x: 12, y: 14, text: "LAST 7 DAYS (MINUTES)", size: 11, color: "#55ff55" },
      { type: "rect", x: 10, y: 118, width: 300, height: 1, color: "#1d5a2c" }
    ];
    vals.forEach((v, i) => {
      const h = Math.round((v.min / max) * 80);
      const x = 20 + i * 42;
      cmds.push({ type: "rect", x, y: 118 - h, width: 28, height: Math.max(h, 1), color: v.now ? "#00ed45" : "#1f8f3a" });
      if (v.min > 0) cmds.push({ type: "text", x: x + 14, y: 118 - h - 9, text: String(v.min), size: 10, align: "center", color: "#ffffff" });
      cmds.push({ type: "text", x: x + 14, y: 134, text: v.label, size: 10, align: "center", color: v.now ? "#00ed45" : "#3a6a45" });
    });
    return ui.canvas(320, 150, cmds, { id: "week", label: "Weekly focus minutes chart" });
  };

  const controlsMenu = () => ui.menu("Timer", [
    { label: running ? "Pause (SPACE)" : "Start (SPACE)", value: "toggle" },
    { label: "Reset (R)", value: "reset" },
    { label: "Skip (S)", value: "skip" }
  ], ({ value }) => act(value), { id: "controls" });

  const presetMenu = () => ui.menu("Preset", Object.keys(PRESETS).map((k) => ({
    label: (S.preset === k ? "● " : "") + PRESETS[k].label, value: k
  })), ({ value }) => {
    S.preset = value; save();
    if (!running) { remMs = durationMs(); note = `${PRESETS[value].label} selected.`; }
    else note = `${PRESETS[value].label} applies to the next session.`;
    refresh();
  }, { id: "presets" });

  const catMenu = () => ui.menu("What are you working on?", CATS.map((c) => ({
    label: (S.cat === c ? "● " : "") + c, value: c
  })), ({ value }) => { S.cat = value; save(); note = `Logging to ${value}.`; refresh(); }, { id: "cats" });

  const statsText = () => {
    const t = today();
    const lines = [
      `TODAY      ${t.min} min / ${t.n} sessions`,
      `STREAK     ${streak()} day(s)`,
      `LIFETIME   ${S.total} sessions`
    ];
    const cats = Object.keys(t.cat).filter((c) => t.cat[c] > 0);
    if (cats.length) lines.push("", ...cats.map((c) => `  ${c.padEnd(8)} ${t.cat[c]} min`));
    return ui.preformatted(lines.join("\n"), { id: "stats" });
  };

  const boardText = (r) => {
    let body = "Loading...";
    if (r && r.leaderboard) {
      body = r.leaderboard.length
        ? r.leaderboard.map((e, i) => `${String(i + 1).padStart(2)}. ${String(e.username).slice(0, 18).padEnd(18)} ${e.score}`).join("\n")
        : "No entries yet. Finish a focus session!";
    }
    return ui.preformatted("STATION FOCUS LEADERS (sessions)\n" + body, { id: "board" });
  };

  const statusText = () => ui.text(note, { id: "status" });

  // ---------- updates ----------
  const drawTimer = () => ui.update("timer", timerCanvas());
  const refresh = () => {
    ui.update("timer", timerCanvas());
    ui.update("controls", controlsMenu());
    ui.update("presets", presetMenu());
    ui.update("cats", catMenu());
    ui.update("stats", statsText());
    ui.update("week", weekCanvas());
    ui.update("status", statusText());
  };
  const loadBoard = () => {
    ui.scores.leaderboard().then((r) => ui.update("board", boardText(r))).catch(() => {
      ui.update("board", ui.preformatted("STATION FOCUS LEADERS\nLeaderboard unavailable.", { id: "board" }));
    });
  };

  // ---------- logic ----------
  const act = (value) => {
    if (value === "toggle") {
      if (running) {
        remMs = Math.max(0, endAt - Date.now());
        running = false;
        note = "Paused.";
      } else {
        if (remMs <= 0) remMs = durationMs();
        endAt = Date.now() + remMs;
        running = true;
        lastSec = -1;
        note = mode === "focus" ? `Focusing on ${S.cat}. Stay with it.` : "Break time. Step away from the screen.";
        ui.sound.playTone(660, 80, { waveform: "triangle", volume: 0.08 });
      }
    } else if (value === "reset") {
      running = false; remMs = durationMs(); note = "Timer reset.";
    } else if (value === "skip") {
      running = false;
      if (mode === "focus") { mode = "break"; longBreak = false; } else { mode = "focus"; longBreak = false; }
      remMs = durationMs();
      note = mode === "focus" ? "Skipped to focus." : "Skipped to break (nothing logged).";
    }
    refresh();
  };

  const complete = () => {
    running = false;
    if (mode === "focus") {
      const k = dayKey(new Date());
      const r = rec(k);
      const mins = PRESETS[S.preset].f;
      r.min += mins; r.n += 1; r.cat[S.cat] = (r.cat[S.cat] || 0) + mins;
      S.days[k] = r;
      S.total += 1; S.cycle += 1;
      save();
      ui.scores.submit(S.total).then(loadBoard).catch(() => {});
      ui.sound.sequence([523.25, 659.25, 783.99, 1046.5], { duration: 160, waveform: "triangle", volume: 0.1 });
      mode = "break";
      longBreak = S.cycle % 4 === 0;
      note = `Session logged (+${mins} min ${S.cat}). ${longBreak ? "Long break earned!" : "Take a break."} Press Start.`;
    } else {
      ui.sound.sequence([783.99, 659.25], { duration: 180, waveform: "triangle", volume: 0.1 });
      mode = "focus"; longBreak = false;
      note = "Break over. Ready for the next focus session.";
    }
    remMs = durationMs();
    refresh();
  };

  const tick = () => {
    if (!running) return;
    const rem = endAt - Date.now();
    if (rem <= 0) { complete(); return; }
    const sec = Math.ceil(rem / 1000);
    if (sec !== lastSec && visible) { lastSec = sec; drawTimer(); }
  };

  // ---------- boot ----------
  ui.render(ui.title("Focus Console"), ui.text("Loading your data..."));
  ui.storage.get(KEY).then((v) => {
    if (v && typeof v === "object") {
      S = Object.assign(S, v);
      if (!PRESETS[S.preset]) S.preset = "classic";
      if (!CATS.includes(S.cat)) S.cat = "Work";
      if (!S.days || typeof S.days !== "object") S.days = {};
    }
  }).catch(() => {}).then(() => {
    remMs = durationMs();
    ui.render(
      ui.title("Focus Console"),
      timerCanvas(),
      statusText(),
      controlsMenu(),
      presetMenu(),
      catMenu(),
      ui.heading("Your stats"),
      statsText(),
      weekCanvas(),
      ui.heading("Leaderboard"),
      boardText(null)
    );
    loadBoard();
    ui.onKey((e) => {
      if (e.phase !== "down" || e.repeat) return;
      if (e.key === " ") act("toggle");
      else if (e.key === "r" || e.key === "R") act("reset");
      else if (e.key === "s" || e.key === "S") act("skip");
    });
    ui.onVisibility(({ value }) => {
      visible = value === "visible";
      if (visible) { tick(); drawTimer(); }
    });
    ui.interval(tick, 250);
  });
});
