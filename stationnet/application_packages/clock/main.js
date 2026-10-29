"use strict";

StationNet.start(async (ui) => {
  const KEY = "clock-state";
  const MAX_ALARMS = 24, MAX_TIMERS = 24, MAX_LAPS = 100, MAX_ZONES = 12;
  const MAX_TIMER_MS = 24 * 60 * 60 * 1000, GRACE_MS = 60 * 1000;
  const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const TABS = [["Clock", "clock"], ["Alarms", "alarms"], ["Timers", "timers"],
    ["Stopwatch", "stopwatch"], ["World", "world"], ["Settings", "settings"]];
  const ZONES = [
    ["UTC", "UTC"], ["Edmonton", "America/Edmonton"], ["Vancouver", "America/Vancouver"],
    ["Los Angeles", "America/Los_Angeles"], ["Denver", "America/Denver"],
    ["Chicago", "America/Chicago"], ["New York", "America/New_York"],
    ["São Paulo", "America/Sao_Paulo"], ["London", "Europe/London"],
    ["Paris", "Europe/Paris"], ["Cairo", "Africa/Cairo"],
    ["Johannesburg", "Africa/Johannesburg"], ["Dubai", "Asia/Dubai"],
    ["Mumbai", "Asia/Kolkata"], ["Singapore", "Asia/Singapore"],
    ["Hong Kong", "Asia/Hong_Kong"], ["Tokyo", "Asia/Tokyo"],
    ["Seoul", "Asia/Seoul"], ["Sydney", "Australia/Sydney"],
    ["Auckland", "Pacific/Auckland"]
  ];
  const DEFAULT_ZONES = ["UTC", "America/New_York", "Europe/London", "Asia/Tokyo"];
  const pad = (n) => String(n).padStart(2, "0");
  const clean = (value, max) => String(value == null ? "" : value).trim().slice(0, max);
  const finite = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const validTime = (value) => /^([01]\d|2[0-3]):[0-5]\d$/.test(value || "");
  const zoneKnown = (zone) => ZONES.some((item) => item[1] === zone);
  const makeId = (prefix) => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
  const dateKey = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const timeKey = (date) => `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const currentTime = () => timeKey(new Date());

  function normalize(raw) {
    const value = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    const used = new Set();
    const uniqueId = (candidate, prefix) => {
      let result = /^[A-Za-z0-9_.:-]{1,80}$/.test(candidate || "") ? candidate : makeId(prefix);
      while (used.has(result)) result = makeId(prefix);
      used.add(result);
      return result;
    };
    const alarms = (Array.isArray(value.alarms) ? value.alarms : []).filter((item) =>
      item && typeof item === "object" && validTime(item.time)).slice(0, MAX_ALARMS).map((item) => ({
      id: uniqueId(item.id, "alarm"), time: item.time, label: clean(item.label, 50) || "Alarm",
      repeat: [...new Set(clean(item.repeat, 7).replace(/[^0-6]/g, ""))].sort().join(""),
      enabled: item.enabled !== false,
      snoozeUntil: Number.isFinite(item.snoozeUntil) && item.snoozeUntil > 0 ? item.snoozeUntil : null,
      lastFired: /^\d{4}-\d{2}-\d{2}$/.test(item.lastFired || "") ? item.lastFired : null
    }));
    const timers = (Array.isArray(value.timers) ? value.timers : []).filter((item) =>
      item && typeof item === "object" && Number.isFinite(Number(item.remainingMs)) &&
      Number(item.remainingMs) >= 0).slice(0, MAX_TIMERS).map((item) => {
      const remainingMs = Math.min(MAX_TIMER_MS, Math.max(0, finite(item.remainingMs)));
      const durationMs = Math.min(MAX_TIMER_MS, Math.max(1000, finite(item.durationMs, remainingMs) || remainingMs));
      const endAt = finite(item.endAt);
      const running = item.running === true && endAt > 0;
      return { id: uniqueId(item.id, "timer"), label: clean(item.label, 50) || "Timer",
        durationMs, remainingMs, endAt: running ? endAt : null, running };
    });
    const oldStopwatch = value.stopwatch && typeof value.stopwatch === "object" ? value.stopwatch : {};
    const validLaps = (Array.isArray(oldStopwatch.laps) ? oldStopwatch.laps : []).filter((n) =>
      Number.isFinite(n) && n >= 0).slice(0, MAX_LAPS);
    const laps = validLaps.reduce((kept, n) => {
      if (!kept.length || n <= kept[kept.length - 1]) kept.push(n);
      return kept;
    }, []);
    const stopwatch = {
      elapsed: Math.max(0, finite(oldStopwatch.elapsed)),
      startedAt: Number.isFinite(oldStopwatch.startedAt) && oldStopwatch.startedAt > 0 ? oldStopwatch.startedAt : null,
      laps
    };
    const worldZones = [...new Set((Array.isArray(value.worldZones) ? value.worldZones : DEFAULT_ZONES)
      .filter(zoneKnown))].slice(0, MAX_ZONES);
    return { format24: value.format24 === true, analog: value.analog !== false,
      soundOn: value.soundOn !== false,
      soundVolume: Math.round(Math.max(0, Math.min(100, finite(value.soundVolume, 70)))),
      alarms, timers, stopwatch, worldZones };
  }

  let loadFailed = false, saveFailed = false, notice = "", state;
  try { state = normalize(await ui.storage.get(KEY)); }
  catch (_) { loadFailed = true; state = normalize(null); }
  let writeTail = Promise.resolve();
  function persist() {
    if (loadFailed) { announce("Saved data could not be loaded. Reopen Clock to retry; editing is unavailable."); return Promise.resolve(false); }
    const snapshot = JSON.parse(JSON.stringify(state));
    const pending = writeTail.catch(() => {}).then(() => ui.storage.set(KEY, snapshot));
    writeTail = pending;
    return pending.then(() => {
      if (writeTail === pending && saveFailed) { saveFailed = false; announce("Changes saved."); render(); }
      return true;
    }).catch(() => {
      saveFailed = true;
      announce("Could not save changes. They are available only until you close Clock. Use Retry saving.");
      render();
      return false;
    });
  }
  function editable() {
    if (!loadFailed) return true;
    announce("Saved data could not be loaded. Reopen Clock to retry; editing is unavailable.");
    return false;
  }
  function announce(message) {
    notice = message;
    ui.update("clock-status", ui.statusBar(statusText(), { id: "clock-status" }));
  }
  function statusText() {
    return loadFailed ? "Saved data could not be loaded. Reopen Clock to retry; editing is unavailable." :
      saveFailed ? "Could not save changes. They are available only until you close Clock. Use Retry saving." :
      notice || "Clock must remain open for alarms and timers to sound.";
  }
  async function changed() { await persist(); render(); }

  function timeText(date, zone) {
    const options = { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: !state.format24 };
    if (zone) options.timeZone = zone;
    return new Intl.DateTimeFormat(undefined, options).format(date);
  }
  function dateText(date, zone) {
    const options = { weekday: "long", year: "numeric", month: "long", day: "numeric" };
    if (zone) options.timeZone = zone;
    return new Intl.DateTimeFormat(undefined, options).format(date);
  }
  function durationText(ms, tenths = false) {
    const whole = Math.max(0, Math.floor(finite(ms)));
    return `${pad(Math.floor(whole / 3600000))}:${pad(Math.floor(whole / 60000) % 60)}:${pad(Math.floor(whole / 1000) % 60)}` +
      (tenths ? `.${Math.floor(whole / 100) % 10}` : "");
  }
  const timerMs = (timer, at = Date.now()) => timer.running ? Math.max(0, timer.endAt - at) : timer.remainingMs;
  const stopwatchMs = (at = Date.now()) => state.stopwatch.elapsed +
    (state.stopwatch.startedAt ? Math.max(0, at - state.stopwatch.startedAt) : 0);
  const zoneName = (zone) => ZONES.find((item) => item[1] === zone)?.[0] || zone;
  const repeatLabel = (repeat) => repeat === "" ? "Once" : repeat === "0123456" ? "Daily" :
    repeat === "12345" ? "Weekdays" : repeat === "06" ? "Weekends" :
    [...repeat].map((day) => DAYS[Number(day)].slice(0, 3)).join(", ");
  const repeatMode = (repeat) => repeat === "" ? "once" : repeat === "0123456" ? "daily" :
    repeat === "12345" ? "weekdays" : repeat === "06" ? "weekends" : "custom";

  function nextOccurrence(alarm, from = Date.now()) {
    if (!alarm.enabled) return null;
    if (alarm.snoozeUntil && alarm.snoozeUntil > from) return new Date(alarm.snoozeUntil);
    const [hour, minute] = alarm.time.split(":").map(Number);
    const today = new Date(from);
    for (let offset = 0; offset <= 7; offset++) {
      const candidate = new Date(today.getFullYear(), today.getMonth(), today.getDate() + offset, hour, minute);
      if (candidate.getHours() !== hour || candidate.getMinutes() !== minute || candidate.getTime() <= from) continue;
      if (alarm.repeat && !alarm.repeat.includes(String(candidate.getDay()))) continue;
      if (offset === 0 && alarm.lastFired === dateKey(candidate)) continue;
      return candidate;
    }
    return null;
  }

  let tab = "clock", alarmDraft = null, comparison = null;
  let ringing = [], lastChimeAt = 0;
  let lastCheckAt = Math.floor(Date.now() / 60000) * 60000 - 1;
  let lastSecond = -1, lastTenth = -1;
  const alertItem = (entry) => (entry.kind === "alarm" ? state.alarms : state.timers)
    .find((item) => item.id === entry.id);
  function soundAlert() {
    if (!state.soundOn || !state.soundVolume) return;
    ui.sound.sequence([880, 660, 880, 990, 880, 660, 880, 990], {
      duration: 145, waveform: "square", volume: 0.17 * state.soundVolume / 100,
      attack: 8, release: 30
    });
  }
  function ring(kind, item, at) {
    if (ringing.some((entry) => entry.id === item.id)) return false;
    ringing.push({ kind, id: item.id, startedAt: at });
    if (at - lastChimeAt > 1500) { soundAlert(); lastChimeAt = at; }
    return true;
  }
  function checkDue(at) {
    let changedState = false, newAlert = false;
    const start = Math.max(lastCheckAt + 1, at - GRACE_MS);
    const firstMinute = Math.ceil(start / 60000) * 60000;
    for (const alarm of state.alarms) {
      if (!alarm.enabled) continue;
      if (alarm.snoozeUntil) {
        if (alarm.snoozeUntil > at) continue;
        if (at - alarm.snoozeUntil <= GRACE_MS) newAlert = ring("alarm", alarm, at) || newAlert;
        alarm.snoozeUntil = null;
        if (!alarm.repeat) alarm.enabled = false;
        changedState = true;
        continue;
      }
      for (let minute = firstMinute; minute <= at; minute += 60000) {
        const date = new Date(minute);
        if (alarm.time !== timeKey(date) || alarm.repeat && !alarm.repeat.includes(String(date.getDay())) ||
            alarm.lastFired === dateKey(date)) continue;
        alarm.lastFired = dateKey(date);
        if (!alarm.repeat) alarm.enabled = false;
        newAlert = ring("alarm", alarm, at) || newAlert;
        changedState = true;
        break;
      }
    }
    for (const timer of state.timers) {
      if (!timer.running || timer.endAt > at) continue;
      timer.running = false; timer.remainingMs = 0; changedState = true;
      if (at - timer.endAt <= GRACE_MS) newAlert = ring("timer", timer, at) || newAlert;
    }
    lastCheckAt = at;
    if (changedState) persist();
    return newAlert || changedState;
  }
  function repeatAlert(at) {
    if (ringing.some((entry) => at - entry.startedAt < 60000) && at - lastChimeAt >= 10000) {
      soundAlert(); lastChimeAt = at;
    }
  }
  async function alertAction(id, action) {
    const entry = ringing.find((item) => item.id === id);
    if (!entry || !editable()) return;
    const alarm = entry.kind === "alarm" ? state.alarms.find((item) => item.id === id) : null;
    if (action === "snooze" && alarm) {
      alarm.snoozeUntil = Date.now() + 5 * 60000;
      alarm.enabled = true;
    }
    ringing = ringing.filter((item) => item.id !== id);
    if (!ringing.length) ui.sound.stop();
    if (action === "snooze") await persist();
    render();
  }
  function alertsView() {
    if (!ringing.length) return [];
    if (ringing.length > 8) {
      const actions = ringing.flatMap((entry) => {
        const item = alertItem(entry), name = `${item?.label || "Alert"}${entry.kind === "alarm" ? ` at ${item?.time}` : ""}`;
        return [...(entry.kind === "alarm" ? [{ label: `Snooze ${name} 5 min`, value: `snooze|${entry.id}` }] : []),
          { label: `Dismiss ${name}`, value: `dismiss|${entry.id}` }];
      });
      return [ui.panel(`Active alerts (${ringing.length})`, [ui.menu("Alert actions", actions,
        ({ value }) => { const [action, id] = value.split("|"); alertAction(id, action); })], { id: "active-alerts" })];
    }
    return [ui.panel(`Active alerts (${ringing.length})`, ringing.map((entry) => {
      const item = alertItem(entry);
      return ui.menu(`${entry.kind === "alarm" ? "Alarm" : "Timer"}: ${item?.label || "Alert"}`,
        [...(entry.kind === "alarm" ? [{ label: "Snooze 5 min", value: "snooze" }] : []),
          { label: "Dismiss", value: "dismiss" }],
        ({ value }) => alertAction(entry.id, value));
    }), { id: "active-alerts" })];
  }

  function analogCommands(date) {
    const commands = [{ type: "circle", x: 150, y: 150, radius: 124, color: "#06140b" },
      { type: "circle", x: 150, y: 150, radius: 119, color: "#010401" }];
    for (let n = 0; n < 60; n++) {
      const angle = n * Math.PI / 30 - Math.PI / 2, outer = 112, inner = outer - (n % 5 ? 5 : 13);
      commands.push({ type: "line", x1: 150 + Math.cos(angle) * inner, y1: 150 + Math.sin(angle) * inner,
        x2: 150 + Math.cos(angle) * outer, y2: 150 + Math.sin(angle) * outer,
        color: n % 5 ? "#22713b" : "#00ed45", width: n % 5 ? 1 : 3 });
    }
    for (const [angle, length, width, color] of [
      [(date.getHours() % 12 + date.getMinutes() / 60) * Math.PI / 6, 62, 5, "#ffffff"],
      [date.getMinutes() * Math.PI / 30, 91, 3, "#00ed45"],
      [date.getSeconds() * Math.PI / 30, 104, 1, "#ff5555"]]) {
      const a = angle - Math.PI / 2;
      commands.push({ type: "line", x1: 150, y1: 150, x2: 150 + Math.cos(a) * length,
        y2: 150 + Math.sin(a) * length, color, width });
    }
    commands.push({ type: "circle", x: 150, y: 150, radius: 6, color: "#ff5555" });
    return commands;
  }
  function nextAlarmText() {
    const upcoming = state.alarms.map((alarm) => [alarm, nextOccurrence(alarm)]).filter((item) => item[1])
      .sort((a, b) => a[1] - b[1])[0];
    return upcoming ? `${upcoming[0].label} · ${dateText(upcoming[1])}, ${timeText(upcoming[1])}` : "No upcoming alarms";
  }
  function nearestTimerText() {
    const timer = state.timers.filter((item) => item.running).sort((a, b) => a.endAt - b.endAt)[0];
    return timer ? `${timer.label} · ${durationText(timerMs(timer))} remaining` : "No running timers";
  }
  function clockView() {
    const date = new Date();
    return [ui.panel("Local time", [
      ui.text(timeText(date), { id: "digital", emphasis: true }),
      ui.text(dateText(date), { id: "date", tone: "muted" }),
      ...(state.analog ? [ui.canvas(300, 300, analogCommands(date), { id: "analog", label: "Analog clock" })] : [])
    ]), ui.panel("Coming up", [
      ui.text(nextAlarmText(), { id: "next-alarm" }),
      ui.text(nearestTimerText(), { id: "nearest-timer" })
    ]), ui.text("Clock must remain open for alarms and timers to sound. Browser audio may need a click or tap first.", { tone: "muted" })];
  }
  function updateClock(date) {
    ui.update("digital", ui.text(timeText(date), { id: "digital", emphasis: true }));
    ui.update("date", ui.text(dateText(date), { id: "date", tone: "muted" }));
    if (state.analog) ui.update("analog", ui.canvas(300, 300, analogCommands(date), { id: "analog", label: "Analog clock" }));
    ui.update("next-alarm", ui.text(nextAlarmText(), { id: "next-alarm" }));
    ui.update("nearest-timer", ui.text(nearestTimerText(), { id: "nearest-timer" }));
  }

  function alarmForm() {
    const draft = alarmDraft;
    if (!draft) return ui.button("New alarm", () => { alarmDraft = {
      id: null, time: currentTime(), label: "", mode: "once", days: "" }; render(); }, { disabled: loadFailed });
    return ui.panel(draft.id ? "Edit alarm" : "New alarm", [
      ui.timeInput("alarmTime", "Time", { value: draft.time }),
      ui.input("alarmLabel", "Name", { value: draft.label, placeholder: "Morning alarm", maxLength: 50 }),
      ui.select("alarmMode", "Repeat", [
        { label: "Once", value: "once" }, { label: "Daily", value: "daily" },
        { label: "Weekdays", value: "weekdays" }, { label: "Weekends", value: "weekends" },
        { label: "Custom days", value: "custom" }
      ], { value: draft.mode, onChange: ({ values }) => {
        alarmDraft = { ...draft, time: values.alarmTime, label: values.alarmLabel,
          mode: values.alarmMode, days: draft.mode === "custom" ? DAYS.map((_, i) => values[`day${i}`] ? i : "").join("") : draft.days };
        render();
      } }),
      ...(draft.mode === "custom" ? [ui.row(DAYS.map((day, i) =>
        ui.checkbox(`day${i}`, day, { checked: draft.days.includes(String(i)) })))] : []),
      ui.menu("Alarm form", [{ label: "Save alarm", value: "save" }, { label: "Cancel", value: "cancel" }],
        ({ value, values }) => value === "save" ? saveAlarm(values) : (alarmDraft = null, render()))
    ]);
  }
  async function saveAlarm(values) {
    if (!editable()) return;
    const time = clean(values.alarmTime, 5), label = clean(values.alarmLabel, 50) || "Alarm";
    const mode = values.alarmMode;
    if (!["once", "daily", "weekdays", "weekends", "custom"].includes(mode))
      return announce("Choose a repeat schedule.");
    const repeat = { once: "", daily: "0123456", weekdays: "12345", weekends: "06" }[mode] ??
      DAYS.map((_, i) => values[`day${i}`] ? i : "").join("");
    if (!validTime(time)) return announce("Choose a valid alarm time.");
    if (mode === "custom" && !repeat) return announce("Choose at least one repeat day.");
    if (!alarmDraft.id && state.alarms.length >= MAX_ALARMS) return announce("Alarm limit reached (24). Delete one to add another.");
    const old = state.alarms.find((item) => item.id === alarmDraft.id);
    const replacement = { id: old?.id || makeId("alarm"), time, label, repeat, enabled: true,
      snoozeUntil: null, lastFired: null };
    state.alarms = old ? state.alarms.map((item) => item.id === old.id ? replacement : item) :
      [...state.alarms, replacement];
    alarmDraft = null; await changed();
  }
  async function alarmAction(id, action) {
    if (!editable()) return;
    const alarm = state.alarms.find((item) => item.id === id);
    if (!alarm) return;
    if (action === "edit") { alarmDraft = { id, time: alarm.time, label: alarm.label,
      mode: repeatMode(alarm.repeat), days: alarm.repeat }; render(); return; }
    if (action === "delete") {
      const choice = await ui.dialog("Delete alarm?", [ui.text(alarm.label)],
        [{ label: "Delete", value: "delete" }, { label: "Cancel", value: "cancel" }]);
      if (choice !== "delete") return;
      state.alarms = state.alarms.filter((item) => item.id !== id);
    } else {
      alarm.enabled = !alarm.enabled; alarm.snoozeUntil = null;
      if (alarm.enabled) alarm.lastFired = null;
    }
    const hadAlert = ringing.some((entry) => entry.id === id);
    ringing = ringing.filter((entry) => entry.id !== id);
    if (hadAlert && !ringing.length) ui.sound.stop();
    await changed();
  }
  function alarmsView() {
    const ordered = [...state.alarms].sort((a, b) => a.time.localeCompare(b.time) || a.label.localeCompare(b.label));
    return [ui.heading("Alarms", 2), ui.text(`${state.alarms.length}/${MAX_ALARMS} saved. Times use your device's local time zone.`, { tone: "muted" }),
      alarmForm(), ...ordered.map((alarm) => {
        const next = nextOccurrence(alarm);
        return ui.panel(`${alarm.time} · ${alarm.label}`, [
          ui.text(`${alarm.enabled ? "On" : "Off"} · ${repeatLabel(alarm.repeat)} · ${next ? `Next: ${dateText(next)} ${timeText(next)}` : "No upcoming ring"}`),
          ui.menu("Alarm actions", [
            { label: alarm.enabled ? "Turn off" : "Turn on", value: "toggle" },
            { label: "Edit", value: "edit" }, { label: "Delete", value: "delete" }
          ], ({ value }) => alarmAction(alarm.id, value))
        ]);
      })];
  }

  function timerDisplay(timer) {
    return `${timer.running ? "Running" : timer.remainingMs ? "Paused" : "Done"} · ${durationText(timerMs(timer))}`;
  }
  function timersView() {
    return [ui.heading("Timers", 2), ui.text(`${state.timers.length}/${MAX_TIMERS} saved. A finished timer stays here until you restart or delete it.`, { tone: "muted" }),
      ui.panel("Quick start", [ui.menu("Presets", [
        { label: "1 minute", value: "1", disabled: loadFailed || state.timers.length >= MAX_TIMERS },
        { label: "5 minutes", value: "5", disabled: loadFailed || state.timers.length >= MAX_TIMERS },
        { label: "25 minutes", value: "25", disabled: loadFailed || state.timers.length >= MAX_TIMERS }
      ], ({ value }) => addTimer(Number(value) * 60000, `${value} minute timer`))]),
      ui.panel("Custom timer", [
        ui.row([ui.numberInput("timerHours", "Hours", { min: 0, max: 24, step: 1 }),
          ui.numberInput("timerMinutes", "Minutes", { min: 0, max: 59, step: 1 }),
          ui.numberInput("timerSeconds", "Seconds", { min: 0, max: 59, step: 1 })]),
        ui.input("timerLabel", "Name", { placeholder: "Tea", maxLength: 50 }),
        ui.button("Start timer", ({ values }) => {
          const parts = [values.timerHours, values.timerMinutes, values.timerSeconds].map((value) =>
            value === "" || value == null ? 0 : Number(value));
          if (parts.some((value, i) => !Number.isInteger(value) || value < 0 || value > (i ? 59 : 24)))
            return announce("Enter whole hours (0–24), minutes (0–59), and seconds (0–59).");
          addTimer((parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000, values.timerLabel);
        }, { disabled: loadFailed || state.timers.length >= MAX_TIMERS })
      ]),
      ...state.timers.map((timer) => ui.panel(timer.label, [
        ui.text(timerDisplay(timer), { id: `timer-${timer.id}`, emphasis: true }),
        ui.menu("Timer actions", [
          ...(timer.running ? [{ label: "Pause", value: "pause" }] :
            timer.remainingMs ? [{ label: "Resume", value: "resume" }] : []),
          { label: "Reset", value: "reset" }, { label: "Restart", value: "restart" },
          { label: "Delete", value: "delete" }
        ], ({ value }) => timerAction(timer.id, value))
      ]))];
  }
  async function addTimer(ms, label) {
    if (!editable()) return;
    if (!Number.isInteger(ms) || ms < 1000 || ms > MAX_TIMER_MS) return announce("Choose a duration from 1 second to 24 hours.");
    if (state.timers.length >= MAX_TIMERS) return announce("Timer limit reached (24). Delete one to add another.");
    state.timers.push({ id: makeId("timer"), label: clean(label, 50) || "Timer",
      durationMs: ms, remainingMs: ms, endAt: Date.now() + ms, running: true });
    await changed();
  }
  async function timerAction(id, action) {
    if (!editable()) return;
    const timer = state.timers.find((item) => item.id === id);
    if (!timer) return;
    if (action === "delete") state.timers = state.timers.filter((item) => item.id !== id);
    else if (action === "pause" && timer.running) { timer.remainingMs = timerMs(timer); timer.running = false; timer.endAt = null; }
    else if (action === "resume" && !timer.running && timer.remainingMs > 0) {
      timer.endAt = Date.now() + timer.remainingMs; timer.running = true;
    } else if (action === "reset") { timer.running = false; timer.endAt = null; timer.remainingMs = timer.durationMs; }
    else if (action === "restart") { timer.running = true; timer.remainingMs = timer.durationMs; timer.endAt = Date.now() + timer.durationMs; }
    const hadAlert = ringing.some((entry) => entry.id === id);
    ringing = ringing.filter((entry) => entry.id !== id);
    if (hadAlert && !ringing.length) ui.sound.stop();
    await changed();
  }
  function updateTimers() {
    for (const timer of state.timers) ui.update(`timer-${timer.id}`,
      ui.text(timerDisplay(timer), { id: `timer-${timer.id}`, emphasis: true }));
  }

  function lapRows() {
    return state.stopwatch.laps.map((total, index, laps) =>
      [String(laps.length - index), durationText(total - (laps[index + 1] || 0), true), durationText(total, true)]);
  }
  function lapStats() {
    const splits = state.stopwatch.laps.map((total, index, laps) => total - (laps[index + 1] || 0)).filter((n) => n >= 0);
    if (!splits.length) return "Record a lap to see split statistics.";
    return `Fastest split ${durationText(Math.min(...splits), true)} · Average split ${durationText(splits.reduce((a, b) => a + b, 0) / splits.length, true)}`;
  }
  function stopwatchView() {
    const running = Boolean(state.stopwatch.startedAt);
    return [ui.heading("Stopwatch", 2),
      ui.text(durationText(stopwatchMs(), true), { id: "stopwatch-time", emphasis: true }),
      ui.row([ui.button(running ? "Pause" : "Start", () => stopwatchAction("toggle"), { disabled: loadFailed }),
        ui.button("Lap", () => stopwatchAction("lap"), { disabled: loadFailed || !running }),
        ui.button("Reset", () => stopwatchAction("reset"), { disabled: loadFailed || !running && !state.stopwatch.elapsed && !state.stopwatch.laps.length })]),
      ui.heading(`Laps (${state.stopwatch.laps.length}/${MAX_LAPS})`, 3),
      ui.text(lapStats()),
      ui.table(["Lap", "Split", "Total"], lapRows().length ? lapRows() : [["—", "—", "No laps yet"]])];
  }
  async function stopwatchAction(action) {
    if (!editable()) return;
    const sw = state.stopwatch;
    if (action === "toggle") {
      if (sw.startedAt) { sw.elapsed = stopwatchMs(); sw.startedAt = null; }
      else sw.startedAt = Date.now();
    } else if (action === "lap") {
      if (!sw.startedAt) return;
      if (sw.laps.length >= MAX_LAPS) return announce("Lap limit reached (100). Reset to start a new set.");
      sw.laps.unshift(stopwatchMs());
    } else if (action === "reset") {
      if (sw.elapsed || sw.startedAt || sw.laps.length) {
        const choice = await ui.dialog("Reset stopwatch?", [ui.text("Elapsed time and all laps will be cleared.")],
          [{ label: "Reset", value: "reset" }, { label: "Cancel", value: "cancel" }]);
        if (choice !== "reset") return;
      }
      state.stopwatch = { elapsed: 0, startedAt: null, laps: [] };
    }
    await changed();
  }

  function zoneDay(zone, date) {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: zone,
      year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
    const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return Date.UTC(Number(value.year), Number(value.month) - 1, Number(value.day));
  }
  function worldRows(date) {
    const localDay = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
    return [["Local", timeText(date), dateText(date), "Today"], ...state.worldZones.map((zone) => {
      const delta = Math.round((zoneDay(zone, date) - localDay) / 86400000);
      return [zoneName(zone), timeText(date, zone), dateText(date, zone),
        delta === 0 ? "Same day" : delta > 0 ? `+${delta} day` : `${delta} day`];
    })];
  }
  function worldView() {
    const date = new Date();
    const selected = state.worldZones.length;
    return [ui.heading("World clock", 2), ui.text(`${selected}/${MAX_ZONES} places selected. Times update while Clock is open.`, { tone: "muted" }),
      ui.table(["Place", "Time", "Date", "Local day"], worldRows(date), { id: "world-table" }),
      ...(selected < MAX_ZONES ? [ui.row([
        ui.select("worldZone", "Add a place", ZONES.filter((item) => !state.worldZones.includes(item[1]))
          .map(([label, value]) => ({ label, value }))),
        ui.button("Add place", ({ values }) => addZone(values.worldZone), { disabled: loadFailed })
      ])] : []),
      ...state.worldZones.map((zone) => ui.button(`Remove ${zoneName(zone)}`, () => removeZone(zone), { disabled: loadFailed, variant: "secondary" })),
      ui.panel("Compare a local date and time", [
        ui.text("Choose a time on your device. During a repeated fall-back hour, the first occurrence is used.", { tone: "muted" }),
        ui.row([ui.dateInput("compareDate", "Date", { value: comparison?.date || dateKey(date) }),
          ui.timeInput("compareTime", "Time", { value: comparison?.time || timeKey(date) })]),
        ui.button("Compare", ({ values }) => compareWorld(values)),
        ...(comparison ? [ui.text(comparison.message, { tone: comparison.error ? "warning" : "muted" }),
          ...(comparison.at ? [ui.table(["Place", "Time", "Date", "Local day"], worldRows(new Date(comparison.at)))] : [])] : [])
      ])];
  }
  async function addZone(zone) {
    if (!editable()) return;
    if (!zoneKnown(zone) || state.worldZones.includes(zone)) return announce("Choose a place that is not already shown.");
    if (state.worldZones.length >= MAX_ZONES) return announce("World clock limit reached (12 places).");
    state.worldZones.push(zone); await changed();
  }
  async function removeZone(zone) {
    if (!editable()) return;
    state.worldZones = state.worldZones.filter((item) => item !== zone); await changed();
  }
  function compareWorld(values) {
    const date = clean(values.compareDate, 10), time = clean(values.compareTime, 5);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !validTime(time)) {
      comparison = { message: "Choose a valid local date and time.", error: true, date, time }; render(); return;
    }
    const [year, month, day] = date.split("-").map(Number), [hour, minute] = time.split(":").map(Number);
    const candidate = new Date(year, month - 1, day, hour, minute);
    if (candidate.getFullYear() !== year || candidate.getMonth() !== month - 1 || candidate.getDate() !== day ||
        candidate.getHours() !== hour || candidate.getMinutes() !== minute) {
      comparison = { message: "That local date or time does not exist, possibly because clocks move forward. Choose another time.", error: true, date, time };
    } else comparison = { message: `Comparing ${date} at ${time} local time.`, at: candidate.getTime(), date, time };
    render();
  }

  function settingsView() {
    return [ui.heading("Settings", 2),
      ui.panel("Display", [
        ui.menu("Time format", [{ label: "12-hour", value: "12" }, { label: "24-hour", value: "24" }],
          async ({ value }) => { if (!editable() || state.format24 === (value === "24")) return;
            state.format24 = value === "24"; await changed(); }),
        ui.text(`Current format: ${state.format24 ? "24-hour" : "12-hour"}`),
        ui.checkbox("analogSetting", "Show analog clock", { checked: state.analog,
          onChange: async ({ value }) => { if (!editable()) return; state.analog = Boolean(value); await changed(); } })
      ]),
      ui.panel("Alert sound", [
        ui.checkbox("soundSetting", "Play alert sounds", { checked: state.soundOn,
          onChange: async ({ value }) => { if (!editable()) return; state.soundOn = Boolean(value); if (!value) ui.sound.stop(); await changed(); } }),
        ui.slider("soundVolume", "Volume", { min: 0, max: 100, step: 5, value: state.soundVolume,
          onChange: async ({ value }) => { if (!editable()) return; state.soundVolume = value; await changed(); } }),
        ui.button("Test sound", () => soundAlert())
      ]),
      ui.text("Preferences, alarms, timers, and laps are saved to your StationNet account. Sounds require Clock to remain open.", { tone: "muted" })];
  }

  function render() {
    const views = { clock: clockView, alarms: alarmsView, timers: timersView,
      stopwatch: stopwatchView, world: worldView, settings: settingsView };
    ui.render(ui.title("Clock"), ui.tabs(TABS.map((item) => item[0]), ({ value }) => {
      tab = TABS[Number(value)]?.[1] || "clock"; notice = ""; render();
    }, { id: "clock-tabs", selected: TABS.findIndex((item) => item[1] === tab) }),
    ui.statusBar(statusText(), { id: "clock-status" }),
    ...(saveFailed ? [ui.button("Retry saving", async () => { await persist(); render(); })] : []),
    ...alertsView(), ...views[tab]());
  }
  function tick(force = false) {
    const at = Date.now();
    const second = Math.floor(at / 1000);
    if (force || second !== lastSecond) {
      lastSecond = second;
      const mustRender = checkDue(at);
      if (mustRender) render();
      else {
        if (tab === "clock") updateClock(new Date(at));
        if (tab === "timers") updateTimers();
        if (tab === "world") ui.update("world-table",
          ui.table(["Place", "Time", "Date", "Local day"], worldRows(new Date(at)), { id: "world-table" }));
      }
      repeatAlert(at);
    }
    const tenth = Math.floor(at / 100);
    if (tab === "stopwatch" && state.stopwatch.startedAt && (force || tenth !== lastTenth)) {
      lastTenth = tenth;
      ui.update("stopwatch-time", ui.text(durationText(stopwatchMs(at), true), { id: "stopwatch-time", emphasis: true }));
    }
  }
  ui.interval(tick, 100);
  ui.onVisibility(({ value }) => { if (value === "visible") tick(true); });
  ui.onCleanup(() => ui.sound.stop());
  render();
  tick(true);
});
