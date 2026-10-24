(() => {
  const manager = document.querySelector('[data-program-manager]');
  if (!manager) return;
  const rows = [...manager.querySelectorAll('[data-program-row]')];
  const details = [...manager.querySelectorAll('[data-program-detail]')];
  const search = manager.querySelector('[data-program-search]');
  const filters = [...manager.querySelectorAll('[data-program-filter]')];
  const noResults = manager.querySelector('[data-program-no-results]');
  const detailPanel = manager.querySelector('[data-program-details]');
  let category = 'all';
  let selected = manager.querySelector('[data-program-row] a[aria-current="true"]')?.closest('[data-program-row]')?.dataset.slug;

  function select(slug, scroll = false) {
    const row = rows.find(item => item.dataset.slug === slug && !item.hidden);
    if (!row) return;
    selected = slug;
    for (const item of rows) {
      const link = item.querySelector('a');
      if (item === row) link.setAttribute('aria-current', 'true');
      else link.removeAttribute('aria-current');
    }
    for (const item of details) item.hidden = item.dataset.programDetail !== slug;
    if (scroll && matchMedia('(max-width: 700px)').matches) {
      detailPanel.scrollIntoView({block: 'start', behavior: 'smooth'});
    }
  }

  function filter() {
    const query = search.value.trim().toLocaleLowerCase();
    for (const row of rows) {
      row.hidden = (category !== 'all' && row.dataset.category !== category)
        || !row.dataset.name.includes(query);
    }
    const visible = rows.filter(row => !row.hidden);
    noResults.hidden = visible.length > 0 || rows.length === 0;
    if (!visible.some(row => row.dataset.slug === selected)) {
      if (visible.length) select(visible[0].dataset.slug);
      else {
        selected = null;
        for (const item of details) item.hidden = true;
      }
    }
  }

  manager.addEventListener('click', event => {
    const link = event.target.closest('[data-select-program]');
    if (!link || !manager.contains(link) || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const row = rows.find(item => item.dataset.slug === link.dataset.selectProgram);
    if (!row) return;
    // Shortcuts can select a program even if the current filter hides it.
    if (row.hidden) {
      search.value = '';
      category = 'all';
      for (const button of filters) button.setAttribute('aria-pressed', String(button.dataset.programFilter === category));
      filter();
    }
    event.preventDefault();
    select(link.dataset.selectProgram, true);
  });
  manager.querySelector('[data-program-list]').addEventListener('keydown', event => {
    if (event.key === 'Enter' && event.target.matches('[data-select-program]')) {
      event.preventDefault();
      window.location.assign(event.target.href);
      return;
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
    const visible = rows.filter(row => !row.hidden);
    const current = visible.findIndex(row => row.contains(document.activeElement));
    if (current < 0 || !visible.length) return;
    event.preventDefault();
    const next = visible[(current + (event.key === 'ArrowDown' ? 1 : -1) + visible.length) % visible.length];
    next.querySelector('a').focus();
    select(next.dataset.slug);
  });
  search.addEventListener('input', filter);
  for (const button of filters) button.addEventListener('click', () => {
    category = button.dataset.programFilter;
    for (const item of filters) item.setAttribute('aria-pressed', String(item === button));
    filter();
  });
})();
