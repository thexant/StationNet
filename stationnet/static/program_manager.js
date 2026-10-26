(() => {
  const manager = document.querySelector('[data-program-manager]');
  if (!manager) return;
  const rows = [...manager.querySelectorAll('[data-program-row]')];
  const details = [...manager.querySelectorAll('[data-program-detail]')];
  const search = manager.querySelector('[data-program-search]');
  const filters = [...manager.querySelectorAll('[data-program-filter]')];
  const noResults = manager.querySelector('[data-program-no-results]');
  const detailPanel = manager.querySelector('[data-program-details]');
  const previous = manager.querySelector('[data-program-previous]');
  const next = manager.querySelector('[data-program-next]');
  const pageStatus = manager.querySelector('[data-program-page-status]');
  const pageSize = 12;
  let category = 'all';
  let selected = manager.querySelector('[data-program-row] a[aria-current="true"]')?.closest('[data-program-row]')?.dataset.slug;
  let page = Math.max(0, Math.floor(rows.findIndex(row => row.dataset.slug === selected) / pageSize));

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

  function matchingRows() {
    const query = search.value.trim().toLocaleLowerCase();
    return rows.filter(row => (category === 'all' || row.dataset.category === category)
      && row.dataset.name.includes(query));
  }

  function renderPage(preferredSlug = selected, scroll = false) {
    const matching = matchingRows();
    const pageCount = Math.ceil(matching.length / pageSize);
    page = Math.min(page, Math.max(0, pageCount - 1));
    const visible = matching.slice(page * pageSize, (page + 1) * pageSize);
    for (const row of rows) row.hidden = !visible.includes(row);
    noResults.hidden = matching.length > 0 || rows.length === 0;
    previous.disabled = page === 0;
    next.disabled = page >= pageCount - 1;
    pageStatus.textContent = pageCount ? `PAGE ${page + 1} / ${pageCount}` : '0 PROGRAMS';
    const active = visible.find(row => row.dataset.slug === preferredSlug) || visible[0];
    if (active) select(active.dataset.slug, scroll);
    else {
      selected = null;
      for (const item of details) item.hidden = true;
    }
  }

  function filter() {
    page = 0;
    renderPage();
  }

  manager.addEventListener('click', event => {
    const link = event.target.closest('[data-select-program]');
    if (!link || !manager.contains(link) || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const row = rows.find(item => item.dataset.slug === link.dataset.selectProgram);
    if (!row) return;
    // Shortcuts can select a program even if the current filter hides it.
    let matching = matchingRows();
    if (!matching.includes(row)) {
      search.value = '';
      category = 'all';
      for (const button of filters) button.setAttribute('aria-pressed', String(button.dataset.programFilter === category));
      matching = matchingRows();
    }
    event.preventDefault();
    page = Math.floor(matching.indexOf(row) / pageSize);
    renderPage(link.dataset.selectProgram, true);
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
  previous.addEventListener('click', () => { page--; renderPage(); });
  next.addEventListener('click', () => { page++; renderPage(); });
  renderPage();
})();
