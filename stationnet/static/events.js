(() => {
  const zone = document.querySelector('[data-event-timezone]');
  if (zone && !zone.value) zone.value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const dateFormat = new Intl.DateTimeFormat(undefined, {dateStyle: 'full', timeStyle: 'short'});
  document.querySelectorAll('time[data-local-time]').forEach(node => {
    const date = new Date(node.dateTime);
    if (!Number.isNaN(date.getTime())) node.textContent = dateFormat.format(date);
  });
  const cards = [...document.querySelectorAll('[data-event-start]')];
  function update() {
    const now = Date.now();
    for (const card of cards) {
      const start = Date.parse(card.dataset.eventStart);
      const end = card.dataset.eventEnd ? Date.parse(card.dataset.eventEnd) : NaN;
      const cancelled = card.dataset.eventCancelled === '1';
      const status = cancelled ? 'Cancelled' : now < start ? 'Upcoming' : Number.isFinite(end) && now < end ? 'In Progress' : 'Ended';
      const label = card.querySelector('[data-event-status]');
      if (label) label.textContent = status;
      const countdown = card.querySelector('[data-event-countdown]');
      if (!countdown) continue;
      if (status !== 'Upcoming') { countdown.textContent = ''; continue; }
      const minutes = Math.max(0, Math.floor((start - now) / 60000));
      const days = Math.floor(minutes / 1440), hours = Math.floor(minutes % 1440 / 60), mins = minutes % 60;
      countdown.textContent = `${days} days, ${hours} hours, ${mins} minutes until ${card.dataset.eventName || card.querySelector('h1,h3')?.textContent.trim() || 'this event'}`;
    }
  }
  update();
  if (cards.length) setInterval(update, 30000);
})();
