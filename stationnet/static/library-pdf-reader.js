(() => {
  const root = document.querySelector('[data-pdf-reader]');
  if (!root) return;
  const frame = root.querySelector('[data-pdf-frame]');
  const pageInput = root.querySelector('[data-pdf-page]');
  const status = root.querySelector('[data-pdf-status]');
  const zoomLabel = root.querySelector('[data-pdf-zoom-label]');
  const pages = Number(root.dataset.pages);
  const loggedIn = root.dataset.loggedIn === '1';
  const levels = [50, 75, 100, 125, 150, 200];
  let zoom = 'page-width';
  let page = Number(root.dataset.page);
  let saveTimer;

  async function post(url, body) {
    const response = await fetch(url, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf},
      body: JSON.stringify(body)});
    if (!response.ok) throw new Error('Could not save to this station.');
    return response.json();
  }
  function save() {
    if (!loggedIn) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        await post(root.dataset.progressUrl, {chapter: page - 1, fraction: 0, text_size: 100});
        status.textContent = 'Reading position saved.';
      } catch (error) { status.textContent = error.message; }
    }, 400);
  }
  function show() {
    pageInput.value = page;
    root.querySelector('[data-pdf-previous]').disabled = page <= 1;
    root.querySelector('[data-pdf-next]').disabled = page >= pages;
    zoomLabel.textContent = zoom === 'page-width' ? 'FIT WIDTH' : `${zoom}%`;
    frame.src = `${root.dataset.pdfUrl}#page=${page}&zoom=${zoom}&toolbar=0&navpanes=0`;
    const url = new URL(root.dataset.readUrl, location.href);
    url.searchParams.set('page', String(page));
    history.replaceState(null, '', url);
  }
  function go(value) {
    const next = Number(value);
    if (!Number.isInteger(next) || next < 1 || next > pages) {
      pageInput.value = page;
      return;
    }
    if (next === page) return;
    page = next;
    show();
    save();
  }
  root.querySelector('[data-pdf-previous]').addEventListener('click', () => go(page - 1));
  root.querySelector('[data-pdf-next]').addEventListener('click', () => go(page + 1));
  pageInput.addEventListener('change', () => go(pageInput.value));
  function changeZoom(step) {
    if (zoom === 'page-width') {
      zoom = step < 0 ? 75 : 100;
      show();
      return;
    }
    const index = levels.indexOf(zoom);
    zoom = levels[Math.max(0, Math.min(levels.length - 1, index + step))];
    show();
  }
  root.querySelector('[data-pdf-zoom-out]').addEventListener('click', () => changeZoom(-1));
  root.querySelector('[data-pdf-zoom-in]').addEventListener('click', () => changeZoom(1));
  root.querySelector('[data-pdf-fit]').addEventListener('click', () => { zoom = 'page-width'; show(); });
  document.addEventListener('keydown', event => {
    if (event.altKey || event.ctrlKey || event.metaKey || ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
    if (event.key === 'ArrowLeft') go(page - 1);
    if (event.key === 'ArrowRight') go(page + 1);
  });
  if (loggedIn) {
    const list = root.querySelector('[data-pdf-bookmarks]');
    root.querySelector('[data-pdf-bookmark]').addEventListener('click', async () => {
      try {
        const label = `Page ${page}`;
        const mark = await post(root.dataset.bookmarkUrl, {chapter: page - 1, fraction: 0, label});
        list.querySelector('[data-bookmark-empty]')?.remove();
        const row = document.createElement('div'); row.className = 'library-bookmark'; row.dataset.bookmarkId = mark.id;
        const jump = document.createElement('button'); jump.type = 'button'; jump.className = 'text-button';
        jump.dataset.jumpPage = page; jump.textContent = label;
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'text-button';
        remove.dataset.removeBookmark = mark.id; remove.setAttribute('aria-label', 'Remove bookmark'); remove.textContent = '×';
        row.append(jump, remove); list.prepend(row); status.textContent = 'Bookmark added.';
      } catch (error) { status.textContent = error.message; }
    });
    list.addEventListener('click', async event => {
      const jump = event.target.closest('[data-jump-page]');
      if (jump) { go(jump.dataset.jumpPage); return; }
      const remove = event.target.closest('[data-remove-bookmark]');
      if (!remove) return;
      try {
        await post(`${root.dataset.bookmarkUrl}/${remove.dataset.removeBookmark}/delete`, {});
        remove.closest('.library-bookmark').remove(); status.textContent = 'Bookmark removed.';
      } catch (error) { status.textContent = error.message; }
    });
    window.addEventListener('pagehide', () => {
      fetch(root.dataset.progressUrl, {method: 'POST', credentials: 'same-origin', keepalive: true,
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': root.dataset.csrf},
        body: JSON.stringify({chapter: page - 1, fraction: 0, text_size: 100})});
    });
  }
  show();
})();
