"use strict";

StationNet.start(async (ui) => {
  if (!["calendar", "panel", "dateInput", "timeInput"].every((name) => typeof ui[name] === "function")) {
    ui.render(ui.title("Personal Calendar"), ui.text("StationNet loaded an older app runtime. Reload this page to open the calendar.", { tone: "warning" }));
    return;
  }
  const KEY = "calendar-v1";
  const REPEATS = ["none", "daily", "weekly", "monthly", "yearly"];
  const pad = (n) => String(n).padStart(2, "0");
  const dateKey = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const today = () => dateKey(new Date());
  const parseDate = (s) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s || "")) return null;
    const d = new Date(Number(s.slice(0, 4)), Number(s.slice(5, 7)) - 1, Number(s.slice(8, 10)));
    return dateKey(d) === s ? d : null;
  };
  const niceDate = (s) => new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" }).format(parseDate(s));
  const niceMonth = (d) => new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(d);
  const validTime = (s) => /^([01]\d|2[0-3]):[0-5]\d$/.test(s || "");
  const timeLabel = (s) => { const [h, m] = s.split(":").map(Number); return `${h % 12 || 12}:${pad(m)} ${h < 12 ? "AM" : "PM"}`; };
  const clean = (v, max) => String(v == null ? "" : v).trim().slice(0, max);
  let events = [];
  let selected = today();
  let month = new Date(parseDate(selected).getFullYear(), parseDate(selected).getMonth(), 1);
  let editing = null;
  let pendingDelete = null;
  let notice = "";
  let loadError = false;

  function normalize(raw) {
    if (!Array.isArray(raw)) return [];
    return raw.slice(0, 400).filter((e) => e && parseDate(e.date) && clean(e.title, 100)).map((e) => ({
      id: clean(e.id, 80) || `legacy-${Math.random().toString(36).slice(2)}`,
      title: clean(e.title, 100), date: e.date, allDay: Boolean(e.allDay),
      start: validTime(e.start) ? e.start : "09:00", end: validTime(e.end) ? e.end : "10:00",
      notes: clean(e.notes, 3000), repeat: REPEATS.includes(e.repeat) ? e.repeat : "none",
      until: parseDate(e.until) ? e.until : "", skips: Array.isArray(e.skips) ? e.skips.filter(parseDate).slice(0, 400) : []
    }));
  }
  try { events = normalize(await ui.storage.get(KEY)); }
  catch (_) { loadError = true; notice = "Could not load your saved calendar. Reopen the app to try again."; }

  function occurs(e, day) {
    if (day < e.date || (e.until && day > e.until) || e.skips.includes(day)) return false;
    if (day === e.date) return true;
    if (e.repeat === "none") return false;
    if (e.repeat === "daily") return true;
    const a = parseDate(e.date), b = parseDate(day);
    if (e.repeat === "weekly") return Math.round((Date.UTC(b.getFullYear(), b.getMonth(), b.getDate()) - Date.UTC(a.getFullYear(), a.getMonth(), a.getDate())) / 86400000) % 7 === 0;
    if (e.repeat === "monthly") return b.getDate() === a.getDate();
    return b.getMonth() === a.getMonth() && b.getDate() === a.getDate();
  }
  function schedule(day) {
    return events.filter((e) => occurs(e, day)).sort((a, b) =>
      Number(b.allDay) - Number(a.allDay) || a.start.localeCompare(b.start) || a.title.localeCompare(b.title));
  }
  function monthDays() {
    const first = new Date(month.getFullYear(), month.getMonth(), 1 - month.getDay());
    const weeks = Math.ceil((month.getDay() + new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate()) / 7);
    return Array.from({ length: weeks * 7 }, (_, i) => {
      const d = new Date(first.getFullYear(), first.getMonth(), first.getDate() + i);
      const key = dateKey(d), listed = schedule(key);
      return { date: key, number: d.getDate(), outside: d.getMonth() !== month.getMonth(), today: key === today(), selected: key === selected,
        events: listed.slice(0, 2).map((e) => `${e.title} · ${e.allDay ? "all day" : timeLabel(e.start)}`), extra: Math.max(0, listed.length - 2) };
    });
  }
  function selectDate(day) {
    if (!parseDate(day)) return;
    selected = day; const d = parseDate(day); month = new Date(d.getFullYear(), d.getMonth(), 1);
    editing = null; pendingDelete = null; notice = ""; render();
  }
  function moveMonth(delta) {
    month = new Date(month.getFullYear(), month.getMonth() + delta, 1);
    selected = dateKey(new Date(month.getFullYear(), month.getMonth(), Math.min(parseDate(selected).getDate(), new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate())));
    editing = null; pendingDelete = null; render();
  }
  async function commit(next) {
    try { await ui.storage.set(KEY, next); events = next; notice = ""; return true; }
    catch (_) { notice = "Could not save your changes. Please try again."; render(); return false; }
  }
  function form() {
    const e = editing && editing !== "new" ? events.find((item) => item.id === editing) : null;
    return [
      ui.text(e ? (e.repeat === "none" ? "Edit event" : "Editing this series changes every occurrence.") : `New event on ${niceDate(selected)}`, { tone: "muted" }),
      ui.input("eventTitle", "Title", { value: e?.title || "", maxLength: 100, placeholder: "What is planned?" }),
      ui.dateInput("eventDate", "Date", { value: e?.date || selected }),
      ui.checkbox("eventAllDay", "All day", { checked: e?.allDay || false }),
      ui.timeInput("eventStart", "Start time", { value: e?.start || "09:00" }),
      ui.timeInput("eventEnd", "End time", { value: e?.end || "10:00" }),
      ui.select("eventRepeat", "Repeat", REPEATS.map((r) => ({ label: r === "none" ? "Does not repeat" : r[0].toUpperCase() + r.slice(1), value: r })), { value: e?.repeat || "none" }),
      ui.dateInput("eventUntil", "Repeat until (optional)", { value: e?.until || "" }),
      ui.textarea("eventNotes", "Notes", { value: e?.notes || "", maxLength: 3000, placeholder: "Details, location, or anything to remember" }),
      ui.menu("Event actions", [{ label: "Save event", value: "save" }, { label: "Cancel", value: "cancel" }, ...(e ? [{ label: "Delete entire series", value: "delete" }] : [])],
        ({ value, values }) => formAction(value, values))
    ];
  }
  async function formAction(action, values) {
    if (action === "cancel") { editing = null; notice = ""; render(); return; }
    const old = events.find((e) => e.id === editing);
    if (action === "delete") {
      if (old && await commit(events.filter((e) => e.id !== old.id))) { editing = null; render(); }
      return;
    }
    const title = clean(values.eventTitle, 100), date = clean(values.eventDate, 10);
    const allDay = Boolean(values.eventAllDay), start = clean(values.eventStart, 5), end = clean(values.eventEnd, 5);
    const repeat = clean(values.eventRepeat, 20), until = clean(values.eventUntil, 10);
    if (!title) notice = "Enter a title.";
    else if (!parseDate(date)) notice = "Enter a valid date as YYYY-MM-DD.";
    else if (!allDay && (!validTime(start) || !validTime(end) || start >= end)) notice = "Enter valid start and end times; the end must be later.";
    else if (!REPEATS.includes(repeat)) notice = "Choose a repeat option.";
    else if (until && (!parseDate(until) || until < date)) notice = "Repeat until must be a valid date on or after the event date.";
    else if (!old && events.length >= 400) notice = "Your calendar is full (400 events).";
    else {
      const item = { id: old?.id || `event-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, title, date, allDay,
        start: validTime(start) ? start : "09:00", end: validTime(end) ? end : "10:00", notes: clean(values.eventNotes, 3000), repeat, until,
        skips: old?.skips || [] };
      const next = old ? events.map((e) => e.id === old.id ? item : e) : [...events, item];
      if (await commit(next)) { editing = null; selectDate(date); }
      return;
    }
    ui.update("calendar-notice", ui.text(notice, { id: "calendar-notice", tone: "warning" }));
  }
  async function skipOccurrence(id) {
    const next = events.map((e) => e.id === id ? { ...e, skips: [...new Set([...e.skips, selected])] } : e);
    if (await commit(next)) render();
  }
  async function deleteFromAgenda() {
    const id = pendingDelete;
    if (await commit(events.filter((e) => e.id !== id))) { pendingDelete = null; render(); }
  }
  function agenda() {
    if (loadError) return ui.panel("Calendar unavailable", [ui.text("Your saved events could not be loaded. Reopen this app to try again.", { tone: "warning" })], { id: "daily-agenda" });
    if (pendingDelete) {
      const e = events.find((item) => item.id === pendingDelete);
      if (e) return ui.panel("Delete event?", [
        ui.text(`${e.title}${e.repeat === "none" ? "" : " — every occurrence in this series"}`),
        ui.menu("Confirm deletion", [{ label: "Delete", value: "delete" }, { label: "Cancel", value: "cancel" }],
          ({ value }) => { if (value === "delete") deleteFromAgenda(); else { pendingDelete = null; render(); } })
      ], { id: "daily-agenda" });
      pendingDelete = null;
    }
    if (editing) return ui.panel(editing === "new" ? "Create event" : "Edit event", form(), { id: "daily-agenda" });
    const listed = schedule(selected);
    return ui.panel(niceDate(selected), [
      ui.button("+ New event", () => { editing = "new"; render(); }),
      ...(listed.length ? listed.flatMap((e) => [
        ui.menu(e.allDay ? "All day" : `${timeLabel(e.start)} – ${timeLabel(e.end)}`,
          [{ label: e.title, value: "edit" }, ...(e.repeat !== "none" ? [{ label: "Skip this date", value: "skip" }] : []),
            { label: "Delete", value: "delete" }],
          ({ value }) => { if (value === "skip") skipOccurrence(e.id); else if (value === "delete") { pendingDelete = e.id; render(); } else { editing = e.id; render(); } }),
        ...(e.notes ? [ui.text(e.notes.length > 180 ? `${e.notes.slice(0, 180)}…` : e.notes, { tone: "muted" })] : [])
      ]) : [ui.text("Nothing scheduled. Select New event to plan this day.", { tone: "muted" })])
    ], { id: "daily-agenda" });
  }
  function render() {
    ui.render(
      ui.title("Personal Calendar"),
      ui.menu(niceMonth(month), [
        { label: "← Previous", value: "previous" }, { label: "Today", value: "today" }, { label: "Next →", value: "next" }
      ], ({ value }) => { if (value === "previous") moveMonth(-1); else if (value === "next") moveMonth(1); else selectDate(today()); }),
      ui.text(notice, { id: "calendar-notice", tone: "warning" }),
      ui.calendar(niceMonth(month), monthDays(), ({ value }) => selectDate(value)),
      agenda()
    );
  }
  render();
});
