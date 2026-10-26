export const blankAgenda = () => ({ version: 1, events: [], tasks: [], contacts: [], settings: { notifications: false } });

export function dayKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export function parseDay(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return null;
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day ? date : null;
}

export function addDays(value, count) {
  const date = parseDay(value);
  if (!date) return value;
  date.setDate(date.getDate() + count);
  return dayKey(date);
}

export function daysBetween(a, b) {
  const start = parseDay(a), end = parseDay(b);
  return start && end ? Math.round((Date.UTC(end.getFullYear(), end.getMonth(), end.getDate()) - Date.UTC(start.getFullYear(), start.getMonth(), start.getDate())) / 86400000) : 0;
}

export function occurrenceStarts(event, from, to) {
  const start = event.startDate, rule = event.repeat || { frequency: 'none' };
  if (!parseDay(start) || start > to) return [];
  const until = rule.until && parseDay(rule.until) ? rule.until : '9999-12-31';
  const last = to < until ? to : until;
  if (rule.frequency === 'none') return start >= from && start <= to ? [start] : [];
  const interval = Math.max(1, Math.min(365, Number(rule.interval) || 1));
  const result = [];
  if (rule.frequency === 'weekly') {
    const weekdays = Array.isArray(rule.weekdays) && rule.weekdays.length ? rule.weekdays.map(Number) : [parseDay(start).getDay()];
    for (let date = from < start ? start : from; parseDay(date) && date <= last; date = addDays(date, 1)) {
      const weeks = Math.floor((daysBetween(start, date) + parseDay(start).getDay()) / 7);
      if (weeks % interval === 0 && weekdays.includes(parseDay(date).getDay())) result.push(date);
    }
  } else if (rule.frequency === 'daily') {
    const first = Math.max(0, Math.ceil(daysBetween(start, from) / interval));
    for (let n = first; n < first + 50000; n++) {
      const date = addDays(start, n * interval);
      if (!parseDay(date) || date > last) break;
      result.push(date);
    }
  } else if (rule.frequency === 'monthly' || rule.frequency === 'yearly') {
    const original = parseDay(start);
    const step = rule.frequency === 'monthly' ? 1 : 12;
    const monthsToFrom = (parseDay(from).getFullYear() - original.getFullYear()) * 12 + parseDay(from).getMonth() - original.getMonth();
    for (let n = Math.max(0, Math.floor(monthsToFrom / (interval * step)) - 1); n < 120000; n++) {
      const monthIndex = original.getMonth() + n * interval * step;
      const candidate = new Date(original.getFullYear(), monthIndex, original.getDate());
      if (candidate.getFullYear() > 9999) break;
      if (candidate.getDate() !== original.getDate()) continue;
      const date = dayKey(candidate);
      if (date > last) break;
      if (date >= from && date >= start) result.push(date);
    }
  }
  return result;
}

export function occurrences(data, from, to) {
  const items = [];
  for (const event of data.events || []) {
    const span = Math.max(0, daysBetween(event.startDate, event.endDate || event.startDate));
    for (const startDate of occurrenceStarts(event, addDays(from, -span), to)) {
      items.push({ kind: 'event', id: event.id, title: event.title, startDate,
        endDate: addDays(startDate, span), startTime: event.allDay ? '' : event.startTime || '',
        endTime: event.allDay ? '' : event.endTime || '', allDay: !!event.allDay,
        onMonth: event.showOnMonth !== false, source: event });
    }
  }
  for (const contact of data.contacts || []) {
    for (const [field, label] of [['birthday', 'Birthday'], ['anniversary', 'Anniversary']]) {
      if (!parseDay(contact[field])) continue;
      const original = parseDay(contact[field]);
      for (let year = parseDay(from).getFullYear(); year <= parseDay(to).getFullYear(); year++) {
        const date = new Date(year, original.getMonth(), original.getDate());
        if (date.getMonth() !== original.getMonth()) continue;
        const key = dayKey(date);
        if (key >= from && key <= to && key >= contact[field]) items.push({ kind: 'contact', id: contact.id, title: `${contact.name} · ${label}`, startDate: key, endDate: key, allDay: true, onMonth: true, source: contact });
      }
    }
  }
  for (const task of data.tasks || []) {
    if (parseDay(task.dueDate) && task.dueDate >= from && task.dueDate <= to)
      items.push({ kind: 'task', id: task.id, title: task.title, startDate: task.dueDate,
        endDate: task.dueDate, startTime: task.dueTime || '', allDay: !task.dueTime,
        onMonth: true, source: task });
  }
  return items.sort((a, b) => a.startDate.localeCompare(b.startDate) || Number(b.allDay) - Number(a.allDay) || (a.startTime || '').localeCompare(b.startTime || '') || a.title.localeCompare(b.title));
}

export function onDay(item, date) { return item.startDate <= date && item.endDate >= date; }

export function searchable(data, query) {
  const term = query.trim().toLocaleLowerCase();
  if (!term) return [];
  return [
    ...(data.events || []).map(item => ({ kind: 'event', item, text: [item.title, item.notes, item.location].join(' ') })),
    ...(data.tasks || []).map(item => ({ kind: 'task', item, text: [item.title, item.notes, ...(item.subtasks || []).map(s => s.title)].join(' ') })),
    ...(data.contacts || []).map(item => ({ kind: 'contact', item, text: [item.name, item.email, item.phone, item.notes].join(' ') })),
  ].filter(result => result.text.toLocaleLowerCase().includes(term));
}
