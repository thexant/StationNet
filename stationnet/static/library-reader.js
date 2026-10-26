(() => {
  const root = document.querySelector('[data-library-reader]');
  if (!root) return;
  const content = root.querySelector('[data-chapter-content]');
  const select = root.querySelector('[data-chapter-select]');
  const status = root.querySelector('[data-reader-status]');
  const position = root.querySelector('[data-chapter-position]');
  const list = root.querySelector('[data-bookmark-list]');
  const loggedIn = root.dataset.loggedIn === '1';
  let chapter = Number(root.dataset.chapter);
  let size = Number(root.dataset.size) || 100;
  let timer;
  let restoring = false;

  function fraction() {
    const top = content.getBoundingClientRect().top + window.scrollY;
    const height = Math.max(1, content.offsetHeight - window.innerHeight + 80);
    return Math.max(0, Math.min(1, (window.scrollY - top) / height));
  }
  function restore(value) {
    restoring = true;
    const top = content.getBoundingClientRect().top + window.scrollY;
    const height = Math.max(1, content.offsetHeight - window.innerHeight + 80);
    window.scrollTo({top: top + height * Math.max(0, Math.min(1, value)), behavior: 'instant'});
    setTimeout(() => { restoring = false; }, 100);
  }
  async function post(url, body) {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json',
      'X-CSRF-Token': root.dataset.csrf}, body: JSON.stringify(body), credentials: 'same-origin'});
    if (!response.ok) throw new Error('Could not save to this station.');
    return response.json();
  }
  async function save() {
    if (!loggedIn) return;
    clearTimeout(timer);
    try {
      await post(root.dataset.progressUrl, {chapter, fraction: fraction(), text_size: size});
      status.textContent = 'Reading position saved.';
    } catch (error) { status.textContent = error.message; }
  }
  function scheduleSave() {
    if (!loggedIn || restoring) return;
    clearTimeout(timer);
    timer = setTimeout(save, 700);
  }
  async function goTo(index, value = 0, fragment = '') {
    if (index < 0 || index >= select.options.length) return;
    if (index !== chapter) {
      await save();
      try {
        const url = new URL(root.dataset.readUrl, location.href);
        url.searchParams.set('chapter', index);
        url.searchParams.set('format', 'json');
        const response = await fetch(url, {credentials: 'same-origin'});
        if (!response.ok) throw new Error('Could not load chapter.');
        const data = await response.json();
        content.innerHTML = data.content;
        chapter = index;
        select.value = String(index);
        position.textContent = `${index + 1} / ${select.options.length}`;
        const address = new URL(root.dataset.readUrl, location.href);
        address.searchParams.set('chapter', index);
        history.replaceState(null, '', address);
      } catch (error) { status.textContent = error.message; return; }
    }
    if (fragment) {
      const target = Array.from(content.querySelectorAll('[id]')).find(node => node.id === fragment);
      (target || content).scrollIntoView();
    } else restore(value);
    scheduleSave();
  }
  select.addEventListener('change', () => goTo(Number(select.value)));
  root.querySelector('[data-previous]').addEventListener('click', () => goTo(chapter - 1));
  root.querySelector('[data-next]').addEventListener('click', () => goTo(chapter + 1));
  root.querySelector('[data-text-smaller]').addEventListener('click', () => {
    size = Math.max(70, size - 10); content.style.fontSize = `${size}%`; scheduleSave();
  });
  root.querySelector('[data-text-larger]').addEventListener('click', () => {
    size = Math.min(180, size + 10); content.style.fontSize = `${size}%`; scheduleSave();
  });
  content.style.fontSize = `${size}%`;
  content.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link) return;
    const url = new URL(link.href, location.href);
    if (url.pathname !== new URL(root.dataset.readUrl, location.href).pathname) return;
    event.preventDefault();
    goTo(Number(url.searchParams.get('chapter') || 0), 0, decodeURIComponent(url.hash.slice(1)));
  });
  if (loggedIn) {
    root.querySelector('[data-add-bookmark]').addEventListener('click', async () => {
      try {
        const label = select.selectedOptions[0].textContent.trim();
        const mark = await post(root.dataset.bookmarkUrl, {chapter, fraction: fraction(), label});
        list.querySelector('[data-bookmark-empty]')?.remove();
        const row = document.createElement('div'); row.className = 'library-bookmark'; row.dataset.bookmarkId = mark.id;
        const jump = document.createElement('button'); jump.type = 'button'; jump.className = 'text-button';
        jump.dataset.jumpChapter = mark.chapter; jump.dataset.jumpFraction = mark.fraction; jump.textContent = mark.label;
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'text-button';
        remove.dataset.removeBookmark = mark.id; remove.setAttribute('aria-label', 'Remove bookmark'); remove.textContent = '×';
        row.append(jump, remove); list.prepend(row); status.textContent = 'Bookmark added.';
      } catch (error) { status.textContent = error.message; }
    });
    list.addEventListener('click', async event => {
      const jump = event.target.closest('[data-jump-chapter]');
      if (jump) { goTo(Number(jump.dataset.jumpChapter), Number(jump.dataset.jumpFraction)); return; }
      const remove = event.target.closest('[data-remove-bookmark]');
      if (!remove) return;
      try {
        await post(`${root.dataset.bookmarkUrl}/${remove.dataset.removeBookmark}/delete`, {});
        remove.closest('.library-bookmark').remove(); status.textContent = 'Bookmark removed.';
      } catch (error) { status.textContent = error.message; }
    });
    window.addEventListener('scroll', scheduleSave, {passive: true});
    window.addEventListener('pagehide', () => {
      // Keep the latest position even when navigation happens before the debounce.
      const body = JSON.stringify({chapter, fraction: fraction(), text_size: size});
      fetch(root.dataset.progressUrl, {method: 'POST', headers: {'Content-Type': 'application/json',
        'X-CSRF-Token': root.dataset.csrf}, body, credentials: 'same-origin', keepalive: true});
    });
  }
  requestAnimationFrame(() => restore(Number(root.dataset.fraction) || 0));
})();
