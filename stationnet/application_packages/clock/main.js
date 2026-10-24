"use strict";

// Clock is intentionally self-contained: it only needs StationNet's bounded
// per-user storage and synthesized sound API, so it works in every deployment.
StationNet.start(async (ui) => {
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const ZONES = [
    ["Local time", "local"], ["UTC", "UTC"], ["Edmonton", "America/Edmonton"],
    ["New York", "America/New_York"], ["Los Angeles", "America/Los_Angeles"],
    ["São Paulo", "America/Sao_Paulo"], ["London", "Europe/London"],
    ["Paris", "Europe/Paris"], ["Cairo", "Africa/Cairo"], ["Johannesburg", "Africa/Johannesburg"],
    ["Dubai", "Asia/Dubai"], ["Mumbai", "Asia/Kolkata"], ["Singapore", "Asia/Singapore"],
    ["Tokyo", "Asia/Tokyo"], ["Sydney", "Australia/Sydney"], ["Auckland", "Pacific/Auckland"]
  ];
  const DEFAULT = {
    format24: false, analog: true, alarms: [], timers: [],
    stopwatch: { elapsed: 0, startedAt: null, laps: [] }, worldZones: ["UTC", "America/New_York", "Europe/London", "Asia/Tokyo"]
  };
  let state = await ui.storage.get("clock-state").catch(() => null);
  state = normalize(state);
  let tab = "clock";
  let ringing = [];
  let lastAlarmMinute = "";
  let savedAt = 0;

  function normalize(value) {
    const next = value && typeof value === "object" ? value : {};
    return {
      format24: Boolean(next.format24), analog: next.analog !== false,
      alarms: Array.isArray(next.alarms) ? next.alarms.filter(validAlarm).slice(0, 24) : [],
      timers: Array.isArray(next.timers) ? next.timers.filter(validTimer).slice(0, 24) : [],
      stopwatch: next.stopwatch && typeof next.stopwatch === "object" ? {
        elapsed: Math.max(0, Number(next.stopwatch.elapsed) || 0),
        startedAt: Number(next.stopwatch.startedAt) || null,
        laps: Array.isArray(next.stopwatch.laps) ? next.stopwatch.laps.slice(0, 100).map((n) => Math.max(0, Number(n) || 0)) : []
      } : { ...DEFAULT.stopwatch, laps: [] },
      worldZones: Array.isArray(next.worldZones) ? next.worldZones.filter(zoneKnown).slice(0, 12) : [...DEFAULT.worldZones]
    };
  }
  function validAlarm(item) { return item && typeof item === "object" && /^\d\d:\d\d$/.test(item.time || ""); }
  function validTimer(item) { return item && typeof item === "object" && Number(item.remainingMs) >= 0; }
  function zoneKnown(zone) { return ZONES.some((entry) => entry[1] === zone); }
  function id(prefix) { return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`; }
  async function save(force = false) {
    // A running clock should not issue four storage writes every second.
    if (!force && Date.now() - savedAt < 3000) return;
    savedAt = Date.now();
    await ui.storage.set("clock-state", state).catch(() => {});
  }
  function pad(value) { return String(value).padStart(2, "0"); }
  function now() { return new Date(); }
  function timeText(date, zone) {
    const options = { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: !state.format24 };
    if (zone && zone !== "local") options.timeZone = zone;
    return new Intl.DateTimeFormat(undefined, options).format(date);
  }
  function dateText(date, zone) {
    const options = { weekday: "long", year: "numeric", month: "long", day: "numeric" };
    if (zone && zone !== "local") options.timeZone = zone;
    return new Intl.DateTimeFormat(undefined, options).format(date);
  }
  function msText(ms, tenths = false) {
    ms = Math.max(0, Math.floor(ms));
    const hours = Math.floor(ms / 3600000), minutes = Math.floor(ms / 60000) % 60, seconds = Math.floor(ms / 1000) % 60;
    return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}${tenths ? `.${Math.floor(ms / 100) % 10}` : ""}`;
  }
  function stopwatchMs() { return state.stopwatch.elapsed + (state.stopwatch.startedAt ? Date.now() - state.stopwatch.startedAt : 0); }
  function timerMs(timer) { return timer.running ? Math.max(0, timer.endAt - Date.now()) : Math.max(0, Number(timer.remainingMs) || 0); }
  function weekday() { return now().getDay(); }
  function alarmMatches(alarm, date) {
    if (!alarm.enabled || alarm.snoozeUntil && alarm.snoozeUntil > Date.now()) return false;
    const current = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    if (alarm.snoozeUntil && alarm.snoozeUntil <= Date.now()) return true;
    return alarm.time === current && (!alarm.repeat || alarm.repeat.includes(String(date.getDay())));
  }
  function soundAlarm() {
    ui.sound.sequence([880, 660, 880, 990, 880, 660, 880, 990], { duration: 145, waveform: "square", volume: .11, attack: 8, release: 30 });
  }
  function ring(alarm) {
    if (ringing.includes(alarm.id)) return;
    ringing.push(alarm.id); soundAlarm();
  }
  function stopRinging(alarmId) { ringing = ringing.filter((item) => item !== alarmId); if (!ringing.length) ui.sound.stop(); }
  function activeAlarm() { return state.alarms.find((alarm) => ringing.includes(alarm.id)) || state.timers.find((timer) => ringing.includes(timer.id)); }
  function tick() {
    const date = now();
    const minute = `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}-${date.getHours()}-${date.getMinutes()}`;
    if (minute !== lastAlarmMinute) {
      lastAlarmMinute = minute;
      state.alarms.forEach((alarm) => {
        if (alarmMatches(alarm, date)) {
          alarm.snoozeUntil = null;
          if (!alarm.repeat) alarm.enabled = false;
          ring(alarm);
        }
      });
    }
    state.timers.forEach((timer) => {
      if (timer.running && timerMs(timer) === 0) { timer.running = false; timer.remainingMs = 0; ring(timer); }
    });
    if (tab === "clock") updateClock();
    if (tab === "timers") updateTimers();
    if (tab === "stopwatch") updateStopwatch();
    if (tab === "world") updateWorld();
    updateAlert(); save();
  }
  function nav() {
    return ui.menu("Clock tools", [
      { label: "CLOCK", value: "clock" }, { label: "ALARMS", value: "alarms" },
      { label: "TIMERS", value: "timers" }, { label: "STOPWATCH", value: "stopwatch" },
      { label: "WORLD", value: "world" }, { label: "SETTINGS", value: "settings" }
    ], ({ value }) => { tab = value; render(); });
  }
  function analogCommands(date) {
    const commands = [], cx = 150, cy = 150, r = 124;
    commands.push({ type: "circle", x: cx, y: cy, radius: r, color: "#06140b" }, { type: "circle", x: cx, y: cy, radius: r - 5, color: "#010401" });
    for (let n = 0; n < 60; n += 1) {
      const a = n * Math.PI / 30 - Math.PI / 2, outer = r - 12, inner = outer - (n % 5 ? 5 : 13);
      commands.push({ type: "line", x1: cx + Math.cos(a) * inner, y1: cy + Math.sin(a) * inner, x2: cx + Math.cos(a) * outer, y2: cy + Math.sin(a) * outer, color: n % 5 ? "#22713b" : "#00ed45", width: n % 5 ? 1 : 3 });
    }
    const hands = [[(date.getHours() % 12 + date.getMinutes() / 60) * Math.PI / 6, 62, 5, "#ffffff"], [date.getMinutes() * Math.PI / 30, 91, 3, "#00ed45"], [date.getSeconds() * Math.PI / 30, 104, 1, "#ff5555"]];
    hands.forEach(([angle, length, width, color]) => {
      const a = angle - Math.PI / 2;
      commands.push({ type: "line", x1: cx, y1: cy, x2: cx + Math.cos(a) * length, y2: cy + Math.sin(a) * length, color, width });
    });
    commands.push({ type: "circle", x: cx, y: cy, radius: 6, color: "#ff5555" });
    return commands;
  }
  function clockView() {
    const date = now();
    const parts = [ui.heading("Local time", 2), ui.text(timeText(date), { id: "digital", emphasis: true }), ui.text(dateText(date), { id: "date", tone: "muted" })];
    if (state.analog) parts.push(ui.canvas(300, 300, analogCommands(date), { id: "analog", label: "Analog clock" }));
    parts.push(ui.text("Alarms and timers sound only while Clock is open.", { tone: "muted" }));
    return parts;
  }
  function updateClock() {
    const date = now();
    ui.update("digital", ui.text(timeText(date), { id: "digital", emphasis: true }));
    ui.update("date", ui.text(dateText(date), { id: "date", tone: "muted" }));
    if (state.analog) ui.update("analog", ui.canvas(300, 300, analogCommands(date), { id: "analog", label: "Analog clock" }));
  }
  function repeatLabel(repeat) { return repeat ? `Repeats: ${[...repeat].map((day) => DAYS[Number(day)]).join(", ")}` : "Once"; }
  function alarmsView() {
    const rows = state.alarms.map((alarm) => [alarm.enabled ? "ON" : "OFF", alarm.time, alarm.label || "Alarm", repeatLabel(alarm.repeat), alarm.snoozeUntil ? "Snoozed" : ""]);
    return [
      ui.heading("Alarms", 2), ui.text("Repeat days use 0–6 (0 = Sunday). For example, 12345 repeats on weekdays.", { tone: "muted" }),
      ui.table(["State", "Time", "Name", "Schedule", ""], rows.length ? rows : [["—", "—", "No alarms saved", "—", "—"]]),
      ui.input("alarmTime", "Time (24-hour HH:MM)", { placeholder: "07:30", maxLength: 5 }),
      ui.input("alarmLabel", "Alarm name", { placeholder: "Morning alarm", maxLength: 50 }),
      ui.input("alarmRepeat", "Repeat days (optional)", { placeholder: "12345", maxLength: 7 }),
      ui.button("Add alarm", addAlarm),
      ...state.alarms.flatMap((alarm) => [ui.menu(`${alarm.time} — ${alarm.label || "Alarm"}`, [
        { label: alarm.enabled ? "TURN OFF" : "TURN ON", value: "toggle" }, { label: "DELETE", value: "delete" }
      ], ({ value }) => alterAlarm(alarm.id, value))])
    ];
  }
  async function addAlarm(event) {
    const values = event.values || {}, time = String(values.alarmTime || "").trim(), label = String(values.alarmLabel || "").trim(), repeat = [...new Set(String(values.alarmRepeat || "").replace(/[^0-6]/g, ""))].join("");
    const match = /^(\d{2}):(\d{2})$/.exec(time);
    if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) return message("Enter an alarm time as HH:MM (for example 07:30).", "warning");
    state.alarms.push({ id: id("alarm"), time, label: label || "Alarm", repeat, enabled: true, snoozeUntil: null }); await save(true); render();
  }
  async function alterAlarm(alarmId, operation) {
    const alarm = state.alarms.find((item) => item.id === alarmId); if (!alarm) return;
    if (operation === "delete") state.alarms = state.alarms.filter((item) => item.id !== alarmId);
    else alarm.enabled = !alarm.enabled;
    stopRinging(alarmId); await save(true); render();
  }
  function timersView() {
    const rows = state.timers.map((timer) => [timer.running ? "RUNNING" : timer.remainingMs ? "PAUSED" : "DONE", msText(timerMs(timer)), timer.label || "Timer"]);
    return [
      ui.heading("Countdown timers", 2), ui.table(["State", "Remaining", "Name"], rows.length ? rows : [["—", "00:00:00", "No timers running"]], { id: "timer-table" }),
      ui.numberInput("timerMinutes", "Minutes", { min: 0, max: 1440, step: 1 }), ui.numberInput("timerSeconds", "Seconds", { min: 0, max: 59, step: 1 }),
      ui.input("timerLabel", "Timer name", { placeholder: "Tea", maxLength: 50 }), ui.button("Start new timer", addTimer),
      ...state.timers.flatMap((timer) => [ui.menu(`${timer.label || "Timer"} — ${msText(timerMs(timer))}`, [
        { label: timer.running ? "PAUSE" : "START", value: "toggle" }, { label: "RESET", value: "reset" }, { label: "DELETE", value: "delete" }
      ], ({ value }) => alterTimer(timer.id, value))])
    ];
  }
  function updateTimers() { ui.update("timer-table", ui.table(["State", "Remaining", "Name"], state.timers.length ? state.timers.map((timer) => [timer.running ? "RUNNING" : timer.remainingMs ? "PAUSED" : "DONE", msText(timerMs(timer)), timer.label || "Timer"]) : [["—", "00:00:00", "No timers running"]], { id: "timer-table" })); }
  async function addTimer(event) {
    const values = event.values || {}, ms = (Math.max(0, Number(values.timerMinutes) || 0) * 60 + Math.max(0, Number(values.timerSeconds) || 0)) * 1000;
    if (!ms) return message("Choose a timer duration greater than zero.", "warning");
    state.timers.push({ id: id("timer"), label: String(values.timerLabel || "").trim() || "Timer", remainingMs: ms, endAt: Date.now() + ms, running: true }); await save(true); render();
  }
  async function alterTimer(timerId, operation) {
    const timer = state.timers.find((item) => item.id === timerId); if (!timer) return;
    if (operation === "delete") state.timers = state.timers.filter((item) => item.id !== timerId);
    else if (operation === "reset") { timer.running = false; timer.remainingMs = 0; }
    else if (timer.running) { timer.remainingMs = timerMs(timer); timer.running = false; }
    else if (timer.remainingMs > 0) { timer.endAt = Date.now() + timer.remainingMs; timer.running = true; }
    stopRinging(timerId); await save(true); render();
  }
  function stopwatchView() {
    const running = Boolean(state.stopwatch.startedAt), laps = state.stopwatch.laps.map((lap, index) => [state.stopwatch.laps.length - index, msText(lap, true)]);
    return [ui.heading("Stopwatch", 2), ui.text(msText(stopwatchMs(), true), { id: "stopwatch-time", emphasis: true }),
      ui.menu("Stopwatch controls", [{ label: running ? "PAUSE" : "START", value: "toggle" }, { label: "LAP", value: "lap" }, { label: "RESET", value: "reset" }], ({ value }) => stopwatchAction(value)),
      ui.heading("Lap times", 3), ui.table(["Lap", "Total"], laps.length ? laps : [["—", "No laps yet"]], { id: "laps" })];
  }
  function updateStopwatch() { ui.update("stopwatch-time", ui.text(msText(stopwatchMs(), true), { id: "stopwatch-time", emphasis: true })); }
  async function stopwatchAction(operation) {
    if (operation === "toggle") {
      if (state.stopwatch.startedAt) { state.stopwatch.elapsed = stopwatchMs(); state.stopwatch.startedAt = null; }
      else state.stopwatch.startedAt = Date.now();
    } else if (operation === "lap" && state.stopwatch.startedAt) state.stopwatch.laps.unshift(stopwatchMs());
    else if (operation === "reset") state.stopwatch = { elapsed: 0, startedAt: null, laps: [] };
    await save(true); render();
  }
  function zoneName(zone) { return ZONES.find((entry) => entry[1] === zone)?.[0] || zone; }
  function worldRows() { const date = now(); return state.worldZones.map((zone) => [zoneName(zone), timeText(date, zone), dateText(date, zone)]); }
  function worldView() {
    return [ui.heading("World clock", 2), ui.table(["Place", "Time", "Date"], worldRows(), { id: "world-table" }),
      ui.select("worldZone", "Add a time zone", ZONES.filter((entry) => entry[1] !== "local").map(([label, value]) => ({ label, value }))), ui.button("Add time zone", addZone),
      ...state.worldZones.filter((zone) => zone !== "local").map((zone) => ui.button(`Remove ${zoneName(zone)}`, () => removeZone(zone)))];
  }
  function updateWorld() { ui.update("world-table", ui.table(["Place", "Time", "Date"], worldRows(), { id: "world-table" })); }
  async function addZone(event) { const zone = event.values?.worldZone; if (zoneKnown(zone) && !state.worldZones.includes(zone)) { state.worldZones.push(zone); await save(true); render(); } }
  async function removeZone(zone) { state.worldZones = state.worldZones.filter((item) => item !== zone); await save(true); render(); }
  function settingsView() { return [ui.heading("Display settings", 2), ui.text(`Time format: ${state.format24 ? "24-hour" : "12-hour"}`), ui.button(`Use ${state.format24 ? "12" : "24"}-hour time`, async () => { state.format24 = !state.format24; await save(true); render(); }), ui.text(`Analog clock: ${state.analog ? "shown" : "hidden"}`), ui.button(state.analog ? "Hide analog clock" : "Show analog clock", async () => { state.analog = !state.analog; await save(true); render(); }), ui.text("Preferences, alarms, timers, laps, and selected time zones are saved to your StationNet account.", { tone: "muted" })]; }
  function message(text, tone) { ui.update("clock-message", ui.text(text, { id: "clock-message", tone })); }
  function updateAlert() {
    const active = activeAlarm();
    ui.update("alarm-alert", active ? ui.text(`${active.label || "Timer"} is ringing.`, { id: "alarm-alert", tone: "warning", emphasis: true }) : ui.text("", { id: "alarm-alert" }));
  }
  async function snooze() {
    const alarm = state.alarms.find((item) => ringing.includes(item.id));
    // Timers have no snooze state; their alert is dismissed by this control.
    if (!alarm) return dismiss();
    alarm.snoozeUntil = Date.now() + 5 * 60000; alarm.enabled = true;
    stopRinging(alarm.id); await save(true); render();
  }
  async function dismiss() { const active = activeAlarm(); if (active) stopRinging(active.id); await save(true); render(); }
  function render() {
    const views = { clock: clockView, alarms: alarmsView, timers: timersView, stopwatch: stopwatchView, world: worldView, settings: settingsView };
    ui.render(ui.title("Clock"), ui.text("StationNet timekeeping console", { tone: "muted" }), nav(), ui.text("", { id: "clock-message" }), ui.text("", { id: "alarm-alert" }), ui.menu("Active alert", [{ label: "SNOOZE 5 MIN", value: "snooze" }, { label: "DISMISS", value: "dismiss" }], ({ value }) => value === "snooze" ? snooze() : dismiss()), ...views[tab]());
    updateAlert();
  }
  ui.interval(tick, 250);
  ui.onCleanup(() => ui.sound.stop());
  render();
});
