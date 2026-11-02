(() => {
  const zone = document.querySelector('[data-expires-timezone]');
  if (zone && !zone.value) zone.value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const toggle = document.querySelector('[data-poll-toggle]');
  const fields = document.querySelector('[data-poll-fields]');
  if (!toggle || !fields) return;
  const showFields = () => { fields.hidden = !toggle.checked; };
  toggle.addEventListener('change', showFields);
  showFields();
  const options = [...fields.querySelectorAll('[data-poll-option]')];
  const add = fields.querySelector('[data-poll-add]');
  let visible = Math.max(2, options.reduce((last, option, index) => option.querySelector('input').value ? index + 1 : last, 0));
  const showOptions = () => {
    options.forEach((option, index) => { option.hidden = index >= visible; });
    add.hidden = visible >= options.length;
  };
  add.addEventListener('click', () => { visible = Math.min(options.length, visible + 2); showOptions(); });
  showOptions();
})();
