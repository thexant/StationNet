/* Sleep Calculator for StationNet 1.0.0
   Self-contained utility; no external services or permissions required. */
StationNet.start(async (ui) => {
  const STORAGE_KEY = "sleep-calculator-settings-v1";
  const DEFAULTS = { wakeTime: "07:00", fallAsleep: 15, cycleLength: 90, clock: "12-hour" };
  let settings = { ...DEFAULTS };
  let lastReport = "";

  // Local, 24-hour civil-time calculations. The reference website uses
  // 15 minutes to fall asleep and estimates each sleep cycle as 90 minutes.
  const clamp = (value, min, max, fallback) => {
    const n = Number(value);
    return Number.isFinite(n) ? Math.max(min, Math.min(max, Math.round(n))) : fallback;
  };
  const parseTime = (value) => {
    if (typeof value !== "string" || !/^\d{2}:\d{2}$/.test(value)) return null;
    const [hours, minutes] = value.split(":").map(Number);
    return hours < 24 && minutes < 60 ? hours * 60 + minutes : null;
  };
  const clockTime = (minutes, style) => {
    const whole = ((Math.floor(minutes) % 1440) + 1440) % 1440;
    const hours = Math.floor(whole / 60), mins = whole % 60;
    const pad = (v) => String(v).padStart(2, "0");
    if (style === "24-hour") return `${pad(hours)}:${pad(mins)}`;
    return `${hours % 12 || 12}:${pad(mins)} ${hours < 12 ? "AM" : "PM"}`;
  };
  const duration = (minutes) => {
    const hours = Math.floor(minutes / 60), mins = minutes % 60;
    return hours ? `${hours}h${mins ? ` ${mins}m` : ""}` : `${mins}m`;
  };
  const wakeRelativeDay = (minutes) => {
    const day = Math.floor(minutes / 1440);
    return day < 0 ? "previous day" : day > 0 ? "following day" : "same day";
  };

  // Storage is personal to this StationNet user and this application.
  try {
    const saved = await ui.storage.get(STORAGE_KEY);
    if (saved && typeof saved === "object") {
      settings = {
        wakeTime: parseTime(saved.wakeTime) === null ? DEFAULTS.wakeTime : saved.wakeTime,
        fallAsleep: clamp(saved.fallAsleep, 0, 60, 15),
        cycleLength: clamp(saved.cycleLength, 60, 120, 90),
        clock: saved.clock === "24-hour" ? "24-hour" : "12-hour"
      };
    }
  } catch (_) { /* Calculator still works if storage is unavailable. */ }

  function readSettings(event) {
    const values = event && event.values ? event.values : {};
    const rawTime = values.wakeTime === undefined ? settings.wakeTime : values.wakeTime;
    const time = parseTime(rawTime);
    settings = {
      wakeTime: time === null ? settings.wakeTime : rawTime,
      fallAsleep: clamp(values.fallAsleep ?? settings.fallAsleep, 0, 60, 15),
      cycleLength: clamp(values.cycleLength ?? settings.cycleLength, 60, 120, 90),
      clock: values.clock === "24-hour" ? "24-hour" :
        values.clock === "12-hour" ? "12-hour" : settings.clock
    };
    // Best-effort: no account or server-wide information is stored.
    ui.storage.set(STORAGE_KEY, settings).catch(() => {});
    return { time, rawTime };
  }

  function status(message) {
    ui.update("status", ui.statusBar(message, { id: "status" }));
  }

  async function copyReport() {
    if (!lastReport) return;
    try {
      await ui.clipboard.writeText(lastReport);
      status("Times copied to clipboard.");
    } catch (_) {
      status("Clipboard unavailable; the times remain visible above.");
    }
  }

  function showResults(kind, originMinutes, originLabel, nowDate) {
    const bedtimeMode = kind === "bedtime";
    const heading = bedtimeMode ? "SUGGESTED BEDTIMES" : "SUGGESTED WAKE-UP TIMES";
    const detail = bedtimeMode
      ? `To wake up at ${originLabel}, consider these times to go to bed.`
      : `If you go to bed at ${originLabel}, these are possible wake-up times.`;
    const parts = [ui.heading(heading), ui.text(detail)];
    const report = [`Sleep Calculator — ${heading}`, detail,
      `Fall-asleep allowance: ${settings.fallAsleep} minutes; cycle: ${settings.cycleLength} minutes.`, ""];

    for (let cycles = 6; cycles >= 1; cycles--) {
      const sleepMinutes = cycles * settings.cycleLength;
      const offset = sleepMinutes + settings.fallAsleep;
      const value = bedtimeMode ? originMinutes - offset : originMinutes + offset;
      let resultTime = clockTime(value, settings.clock);
      let dayNote = wakeRelativeDay(value);

      // For "Sleep now", use actual elapsed time so crossing a daylight-saving
      // clock change does not cause an incorrect local wake-up clock reading.
      if (!bedtimeMode && nowDate) {
        const wakeDate = new Date(nowDate.getTime() + offset * 60000);
        resultTime = clockTime(wakeDate.getHours() * 60 + wakeDate.getMinutes(), settings.clock);
        const today = new Date(nowDate.getFullYear(), nowDate.getMonth(), nowDate.getDate());
        const wakeDay = new Date(wakeDate.getFullYear(), wakeDate.getMonth(), wakeDate.getDate());
        const dayCount = Math.round((Date.UTC(wakeDay.getFullYear(), wakeDay.getMonth(), wakeDay.getDate()) -
          Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) / 86400000);
        dayNote = dayCount === 0 ? "today" : dayCount === 1 ? "tomorrow" : `in ${dayCount} days`;
      }

      const recommended = cycles === 5 || cycles === 6;
      const name = `${cycles} ${cycles === 1 ? "CYCLE" : "CYCLES"}${recommended ? " / LONGER SLEEP" : ""}`;
      const extra = `${duration(sleepMinutes)} asleep / ${duration(offset)} in bed / ${dayNote}`;
      parts.push(ui.panel(name, [ui.heading(resultTime), ui.text(extra)]));
      report.push(`${cycles} ${cycles === 1 ? "cycle" : "cycles"}: ${resultTime} (${extra})`);
    }

    parts.push(ui.button("Copy results", copyReport));
    parts.push(ui.text("These are rough planning estimates, not guarantees of sleep stage or alertness. Sleep duration matters more than matching a precise cycle boundary."));
    lastReport = report.join("\n");
    ui.update("results", ui.column(parts, { id: "results" }));
    status(bedtimeMode ? "Bedtime options calculated." : "Wake-up options calculated.");
  }

  function calculateBedtime(event) {
    const { time } = readSettings(event);
    if (time === null) {
      status("Enter a valid wake-up time in HH:MM format.");
      return;
    }
    showResults("bedtime", time, clockTime(time, settings.clock), null);
  }

  function calculateWake(event) {
    readSettings(event);
    const now = new Date();
    const localMinutes = now.getHours() * 60 + now.getMinutes();
    showResults("wake", localMinutes, clockTime(localMinutes, settings.clock), now);
  }

  ui.render(
    ui.title("Sleep Calculator"),
    ui.text("Plan bedtimes or wake-up times using estimated sleep cycles."),
    ui.panel("CALCULATOR", [
      ui.timeInput("wakeTime", "I want to wake up at", { id: "wake-time", value: settings.wakeTime }),
      ui.slider("fallAsleep", "Minutes to fall asleep", {
        id: "fall-asleep", min: 0, max: 60, step: 1, value: settings.fallAsleep
      }),
      ui.slider("cycleLength", "Minutes per sleep cycle", {
        id: "cycle-length", min: 60, max: 120, step: 5, value: settings.cycleLength
      }),
      ui.radio("clock", "Clock format", ["12-hour", "24-hour"], {
        id: "clock-format", value: settings.clock
      }),
      ui.row([
        ui.button("Calculate bedtimes", calculateBedtime),
        ui.button("If I go to bed now...", calculateWake)
      ])
    ]),
    ui.text("Try 5–6 cycles for a longer night's sleep. Defaults: 90 minutes per cycle plus 15 minutes to fall asleep."),
    ui.text("Choose a calculation to see your times.", { id: "results" }),
    ui.statusBar("Ready", { id: "status" })
  );
});
