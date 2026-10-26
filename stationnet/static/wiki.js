(() => {
  const screen = document.querySelector('[data-wiki]');
  if (!screen) return;
  const collection = screen.querySelector('[data-wiki-collection]');
  if (collection) collection.addEventListener('change', () => {
    if (collection.value) location.assign(collection.value);
  });
  screen.querySelector('[data-wiki-back]')?.addEventListener('click', () => history.back());
  screen.querySelector('[data-wiki-forward]')?.addEventListener('click', () => history.forward());

  const mount = screen.querySelector('[data-wiki-reading]');
  if (!mount) return;
  const rootPrefix = mount.dataset.kiwixRoot;
  const contentPrefix = mount.dataset.contentPrefix;
  const readPrefix = mount.dataset.readPrefix;
  const assetSuffix = /\.(?:avif|bmp|gif|ico|jpe?g|png|svg|webp|apng|pdf|epub|mp3|mp4|ogg|ogv|webm|woff2?|ttf|otf|zip)(?:$|[?#])/i;
  const shadow = mount.attachShadow({mode: 'open'});
  const status = mount.querySelector('[data-wiki-loading]');
  shadow.append(status);

  function resolved(value, base) {
    try {
      const url = new URL(value, base);
      if (!['http:', 'https:'].includes(url.protocol)) return null;
      return url;
    } catch (_) { return null; }
  }

  function localAddress(value, base) {
    const url = resolved(value, base);
    return url && url.origin === location.origin ? url : null;
  }

  function asReader(url) {
    if (url.pathname === rootPrefix + 'search') {
      const query = new URLSearchParams({
        book: url.searchParams.get('books.name') || url.searchParams.get('content') || '',
        q: url.searchParams.get('pattern') || ''
      });
      if (url.searchParams.has('start')) query.set('start', url.searchParams.get('start'));
      return '/wiki/search?' + query.toString();
    }
    const prefix = url.pathname.startsWith(contentPrefix) ? contentPrefix : rootPrefix;
    if (!url.pathname.startsWith(prefix) ||
        (prefix === rootPrefix && /^(?:skin|catalog|viewer|search|suggest|raw)\//.test(url.pathname.slice(prefix.length)))) return url.href;
    if (assetSuffix.test(url.pathname)) return url.href;
    return readPrefix + url.pathname.slice(prefix.length) + url.search + url.hash;
  }

  function rewriteCssUrls(css, base) {
    return css.replace(/url\(\s*(['"]?)([^)'"\s]+)\1\s*\)/gi, (match, quote, value) => {
      const url = localAddress(value, base);
      return url ? `url("${url.href}")` : match;
    }).replace(/@import\s+(['"])([^'"]+)\1/gi, (match, quote, value) => {
      const url = localAddress(value, base);
      return url ? `@import "${url.href}"` : match;
    });
  }

  function prepareTree(root, base) {
    root.querySelectorAll('script, iframe, frame, object, embed, base, meta[http-equiv="refresh"]').forEach(node => node.remove());
    for (const node of [root, ...root.querySelectorAll('*')]) {
      for (const attr of [...node.attributes]) {
        const key = attr.name.toLowerCase();
        if (key.startsWith('on') || key === 'srcdoc') { node.removeAttribute(attr.name); continue; }
        if (key === 'style') {
          node.setAttribute('style', rewriteCssUrls(attr.value, base));
          continue;
        }
        if (['href', 'xlink:href', 'src', 'poster', 'action', 'data'].includes(key)) {
          if (attr.value.startsWith('#') && (key === 'href' || key === 'xlink:href')) continue;
          if (key === 'href' && node.localName === 'a' && /^(?:mailto:|tel:)/i.test(attr.value)) continue;
          if (key === 'src' && /^(?:data:image\/|data:audio\/|data:video\/)/i.test(attr.value)) continue;
          const url = resolved(attr.value, base);
          if (!url) { node.removeAttribute(attr.name); continue; }
          if ((url.origin !== location.origin && key !== 'href') ||
              (url.origin === location.origin && key !== 'href' && key !== 'action' &&
               !url.pathname.startsWith(rootPrefix)) ||
              (node.localName === 'link' && !url.pathname.startsWith(rootPrefix))) {
            node.removeAttribute(attr.name); continue;
          }
          const isArticleLink = node.localName === 'a' && !node.hasAttribute('download');
          node.setAttribute(attr.name, key === 'href' && url.origin === location.origin && isArticleLink ? asReader(url) : url.href);
          if (key === 'href' && url.origin !== location.origin) {
            node.setAttribute('rel', 'noopener noreferrer');
          }
        }
        if (key === 'srcset') {
          if (/^data:image\//i.test(attr.value.trim())) continue;
          const parts = attr.value.split(',').map(part => {
            const pieces = part.trim().split(/\s+/);
            const url = localAddress(pieces[0], base);
            return url && url.pathname.startsWith(rootPrefix) ? [url.href, ...pieces.slice(1)].join(' ') : '';
          }).filter(Boolean);
          if (parts.length) node.setAttribute('srcset', parts.join(', '));
          else node.removeAttribute('srcset');
        }
      }
    }
    root.querySelectorAll('style').forEach(node => {
      node.textContent = rewriteCssUrls(node.textContent, base);
    });
  }

  function interactiveKind(doc) {
    if (!doc.querySelector('script')) return false;
    if (doc.body?.classList.contains('pure-skin-gutenberg') &&
        doc.querySelector('#books_table, .cover-content, .lcc_shelves')) return 'gutenberg';
    // Production maps2zim bundles use fingerprinted asset names, so neither
    // their module URL nor the initial fallback text necessarily says MapLibre
    // or WebGL. Detect the app's stable boot/loading/error structure as well.
    if (doc.querySelector('script[type="module"]') && doc.querySelector('#map') &&
        doc.querySelector('#loading') && doc.querySelector('#error')) return 'map';
    const text = `${doc.title || ''} ${doc.body?.textContent || ''}`;
    if (/\b(?:webgl|maplibre|mapbox|openlayers|leaflet)\b/i.test(text) ||
        /requires?\s+(?:javascript|webgl)|(?:javascript|webgl)\s+(?:is\s+)?required/i.test(text)) return 'map';
    return [...doc.scripts].some(script =>
      /(?:maplibre|mapbox|openlayers|leaflet|ol(?:\.min)?\.js)(?:[/?#.]|$)/i.test(script.src || '')) ? 'map' : null;
  }

  function themeGutenberg(frame) {
    let frameDoc;
    try { frameDoc = frame.contentDocument; } catch (_) { return; }
    if (!frameDoc?.head || !frameDoc.documentElement) return;
    const stationTheme = getComputedStyle(document.documentElement);
    for (const name of ['black', 'green', 'green-bright', 'green-dim', 'cyan',
                        'magenta', 'amber', 'grey', 'white', 'theme-rgb', 'accent-rgb', 'mono']) {
      frameDoc.documentElement.style.setProperty(`--${name}`, stationTheme.getPropertyValue(`--${name}`));
    }
    const theme = frameDoc.createElement('link');
    theme.rel = 'stylesheet';
    theme.href = mount.dataset.gutenbergCss;
    frameDoc.head.append(theme);
  }

  function openInteractive(doc, sourceUrl, kind) {
    const source = new URL(sourceUrl);
    if (kind === 'gutenberg') {
      source.pathname = mount.dataset.interactivePrefix + source.pathname.slice(rootPrefix.length);
    } else {
      source.searchParams.set('stationnet-interactive', '1');
    }
    const theme = document.createElement('link');
    theme.rel = 'stylesheet';
    theme.href = mount.dataset.themeCss;
    shadow.append(theme);
    const frame = document.createElement('iframe');
    frame.className = 'wiki-interactive-frame';
    frame.title = doc.title || (kind === 'gutenberg' ? 'Project Gutenberg Library' : 'Interactive offline map');
    frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms allow-pointer-lock allow-downloads');
    frame.setAttribute('referrerpolicy', 'same-origin');
    frame.src = source.href;
    frame.addEventListener('load', () => {
      if (kind === 'gutenberg') themeGutenberg(frame);
      status.remove();
    });
    status.textContent = kind === 'gutenberg' ? 'OPENING BOOK CATALOG…' : 'STARTING INTERACTIVE MAP…';
    shadow.append(frame);
  }

  function showHash() {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); }
    catch (_) { return; }
    if (!id) return;
    const target = shadow.getElementById(id) || [...shadow.querySelectorAll('[name]')].find(node => node.getAttribute('name') === id);
    target?.scrollIntoView();
  }

  shadow.addEventListener('click', event => {
    const link = event.target.closest?.('a[href]');
    if (!link || !shadow.contains(link)) return;
    const href = link.getAttribute('href');
    if (!href) return;
    const url = resolved(href, location.href);
    if (!url || url.origin !== location.origin) return;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) {
      event.preventDefault();
      history.pushState(null, '', url.href);
      showHash();
    }
  });
  shadow.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const action = localAddress(form.action, location.href);
    event.preventDefault();
    if (form.method.toLowerCase() !== 'get' || !action || !action.pathname.startsWith(rootPrefix)) return;
    for (const [key, value] of new FormData(form)) {
      if (typeof value === 'string') action.searchParams.append(key, value);
    }
    location.assign(asReader(action));
  });
  window.addEventListener('popstate', showHash);

  async function load() {
    try {
      const response = await fetch(mount.dataset.source, {credentials: 'same-origin'});
      if (!response.ok) throw new Error(`Page unavailable (${response.status})`);
      const type = response.headers.get('content-type') || '';
      if (!/html|xhtml/i.test(type)) {
        status.textContent = 'This item opens as a file.';
        const link = document.createElement('a');
        link.href = response.url;
        link.textContent = 'OPEN FILE →';
        shadow.append(link);
        return;
      }
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const kind = interactiveKind(doc);
      if (kind) {
        openInteractive(doc, response.url, kind);
        document.title = `${doc.title || 'Reference Library'} :: StationNet`;
        return;
      }
      let base = response.url;
      const baseTag = doc.querySelector('base[href]');
      if (baseTag) {
        const candidate = localAddress(baseTag.getAttribute('href'), base);
        if (candidate && candidate.pathname.startsWith(rootPrefix)) base = candidate.href;
      }
      const container = document.createElement('div');
      container.className = 'wiki-document';
      const articlePath = new URL(response.url).pathname;
      const archive = articlePath.startsWith(contentPrefix)
        ? articlePath.slice(contentPrefix.length).split('/')[0] : '';
      if (/^ifixit(?:[_-]|$)/i.test(archive)) container.classList.add('wiki-document--ifixit');
      for (const sheet of doc.querySelectorAll('head link[rel~="stylesheet"]')) {
        const url = localAddress(sheet.getAttribute('href'), base);
        if (!url || !url.pathname.startsWith(rootPrefix)) continue;
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = url.href;
        if (sheet.hasAttribute('media')) link.media = sheet.media;
        shadow.append(link);
      }
      for (const style of doc.querySelectorAll('head style')) {
        const copy = document.createElement('style');
        copy.textContent = rewriteCssUrls(style.textContent, base);
        shadow.append(copy);
      }
      const body = doc.body;
      prepareTree(body, base);
      container.append(body);
      shadow.append(container);
      const theme = document.createElement('link');
      theme.rel = 'stylesheet';
      theme.href = mount.dataset.themeCss;
      shadow.append(theme);
      document.title = `${doc.title || body.querySelector('h1')?.textContent || 'Reference Library'} :: StationNet`;
      status.remove();
      requestAnimationFrame(showHash);
    } catch (error) {
      status.textContent = error.message || 'This page could not be opened.';
    }
  }
  load();
})();
