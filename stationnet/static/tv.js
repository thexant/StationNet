/* StationNet TV receiver: tunes channels, follows the server schedule, never seeks freely. */
(() => {
  const root = document.querySelector("[data-tv]");
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  const screen = $("[data-tv-screen]");
  const video = $("[data-tv-video]");
  const channels = JSON.parse(root.dataset.channels || "[]");
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* storage unavailable */ } },
  };
  const timeFormat = new Intl.DateTimeFormat(undefined, {hour: "2-digit", minute: "2-digit"});
  const dayFormat = new Intl.DateTimeFormat(undefined, {weekday: "short", day: "numeric", month: "short"});
  const clock = ms => timeFormat.format(new Date(ms));
  const DRIFT_LIMIT = 2;
  const STALL_LIMIT = 8000;

  let skew = 0;
  let index = 0;
  let current = null;
  let fetchedAt = 0;
  let powered = false;
  let tuneToken = 0;
  let transitionTimer = 0;
  let retryTimer = 0;
  let stallTimer = 0;
  let selfSeek = false;
  let lastCheck = 0;
  let volume = Number.parseFloat(store.get("stationnet.tv.volume") ?? "0.8");
  let muted = store.get("stationnet.tv.muted") === "1";
  if (!Number.isFinite(volume)) volume = 0.8;

  const serverNow = () => Date.now() + skew;
  const channel = () => channels[index];

  function initialIndex() {
    const preferred = [store.get("stationnet.tv.channel"), root.dataset.defaultChannel];
    for (const id of preferred) {
      const found = channels.findIndex(entry => String(entry.id) === String(id));
      if (found >= 0) return found;
    }
    let lowest = 0;
    channels.forEach((entry, position) => { if (entry.number < channels[lowest].number) lowest = position; });
    return lowest;
  }

  /* Screen layers ------------------------------------------------------ */

  const layers = {offair: $("[data-tv-offair]"), standby: $("[data-tv-standby]"), poweroff: $("[data-tv-poweroff]")};
  function showLayer(name) {
    Object.entries(layers).forEach(([key, node]) => { node.hidden = key !== name; });
  }

  const staticCanvas = $("[data-tv-static]");
  let staticFrame = 0;
  function showStatic(duration = 350) {
    const context = staticCanvas.getContext("2d");
    const style = getComputedStyle(document.documentElement);
    const tint = (style.getPropertyValue("--theme-rgb").trim() || "0,237,69").split(",").map(Number);
    const image = context.createImageData(staticCanvas.width, staticCanvas.height);
    const until = performance.now() + duration;
    cancelAnimationFrame(staticFrame);
    staticCanvas.hidden = false;
    const draw = () => {
      for (let offset = 0; offset < image.data.length; offset += 4) {
        const level = Math.random();
        image.data[offset] = tint[0] * level * .5 + 255 * level * .5;
        image.data[offset + 1] = tint[1] * level * .5 + 255 * level * .5;
        image.data[offset + 2] = tint[2] * level * .5 + 255 * level * .5;
        image.data[offset + 3] = 255;
      }
      context.putImageData(image, 0, 0);
      if (performance.now() < until) staticFrame = requestAnimationFrame(draw);
      else staticCanvas.hidden = true;
    };
    draw();
  }

  /* On-screen display -------------------------------------------------- */

  const osd = {
    number: $("[data-tv-osd-number]"), banner: $("[data-tv-osd]"), hint: $("[data-tv-hint]"),
    volume: $("[data-tv-osd-volume]"), logo: $("[data-tv-osd-logo]"), channel: $("[data-tv-osd-channel]"),
    clock: $("[data-tv-osd-clock]"), title: $("[data-tv-osd-title]"), time: $("[data-tv-osd-time]"),
    progress: $("[data-tv-osd-progress]"), description: $("[data-tv-osd-description]"), next: $("[data-tv-osd-next]"),
    exit: $("[data-tv-exit-fullscreen]"),
  };
  const hideTimers = new Map();
  function flash(node, duration) {
    node.hidden = false;
    clearTimeout(hideTimers.get(node));
    if (duration) hideTimers.set(node, setTimeout(() => { node.hidden = true; }, duration));
  }

  function airingLabel(airing) {
    if (!airing) return "";
    return airing.kind === "offair" ? "OFF AIR" : airing.title;
  }

  function renderBanner(detailed) {
    const entry = channel();
    if (!entry) return;
    osd.channel.textContent = `${entry.number}  ${entry.name}`;
    osd.logo.hidden = !entry.logo_url;
    if (entry.logo_url) osd.logo.src = entry.logo_url;
    osd.clock.textContent = clock(serverNow());
    osd.exit.hidden = !document.fullscreenElement;
    const airing = current && current.airing;
    if (!airing) return;
    const start = Date.parse(airing.start), end = Date.parse(airing.end);
    osd.title.textContent = airingLabel(airing);
    osd.time.textContent = `${clock(start)} – ${clock(end)}`;
    const progress = Math.min(1, Math.max(0, (serverNow() - start) / Math.max(1, end - start)));
    osd.progress.style.width = `${(progress * 100).toFixed(1)}%`;
    osd.description.textContent = airing.description || "";
    osd.description.hidden = !detailed || !airing.description;
    const following = current.next;
    osd.next.textContent = following ? `${clock(Date.parse(following.start))}  ${airingLabel(following)}` : "—";
  }

  function showBanner(detailed = false) {
    renderBanner(detailed);
    flash(osd.banner, detailed ? 8000 : 4500);
  }

  function showNumber(text, duration = 3000) {
    osd.number.textContent = text;
    flash(osd.number, duration);
  }

  function showHint(text, duration = 0) {
    osd.hint.textContent = text;
    flash(osd.hint, duration);
  }

  /* Receiver panel ----------------------------------------------------- */

  const panel = {number: $("[data-tv-number]"), name: $("[data-tv-name]"), clock: $("[data-tv-clock]"), line: $("[data-tv-nowline]")};
  function updatePanel() {
    panel.clock.textContent = clock(serverNow());
    const entry = channel();
    if (!powered || !entry) {
      panel.number.textContent = "---";
      panel.name.textContent = powered ? "NO SIGNAL" : "STANDBY";
      panel.line.textContent = "";
      return;
    }
    panel.number.textContent = String(entry.number).padStart(3, "0");
    panel.name.textContent = entry.name;
    const airing = current && current.airing;
    if (airing) {
      const following = current.next;
      panel.line.textContent = `NOW ${airingLabel(airing)}` +
        (following ? `  ·  ${clock(Date.parse(following.start))} ${airingLabel(following)}` : "");
    }
  }

  /* Playback ----------------------------------------------------------- */

  function position() {
    const airing = current.airing;
    let seconds = airing.offset + (serverNow() - fetchedAt) / 1000;
    if (video.loop && Number.isFinite(video.duration) && video.duration > 0) seconds %= video.duration;
    return Math.max(0, seconds);
  }

  function seekTo(seconds) {
    selfSeek = true;
    try { video.currentTime = seconds; } catch { selfSeek = false; }
  }

  function applyVolume() {
    video.volume = volume;
    video.muted = muted;
    $("[data-tv-mute]").setAttribute("aria-pressed", String(muted));
  }

  function stopVideo() {
    video.pause();
    video.removeAttribute("src");
    video.load();
  }

  async function startPlayback(token) {
    applyVolume();
    try {
      await video.play();
      if (!muted) osd.hint.hidden = true;
    } catch (error) {
      if (token !== tuneToken) return;
      if (error && error.name === "NotAllowedError" && !video.muted) {
        video.muted = true;
        try {
          await video.play();
          showHint("SOUND OFF – PRESS M OR CLICK THE SCREEN");
          return;
        } catch { /* fall through to power card */ }
      }
      if (error && error.name === "NotAllowedError") powerOff();
    }
  }

  function waitForMetadata(token) {
    return new Promise(resolve => {
      if (video.readyState >= 1) { resolve(true); return; }
      const done = ok => {
        video.removeEventListener("loadedmetadata", onLoad);
        video.removeEventListener("error", onError);
        resolve(ok && token === tuneToken);
      };
      const onLoad = () => done(true);
      const onError = () => done(false);
      video.addEventListener("loadedmetadata", onLoad);
      video.addEventListener("error", onError);
    });
  }

  async function play(airing, token) {
    const source = new URL(airing.media_url, location.href).href;
    video.loop = airing.kind === "offair";
    if (video.src !== source) {
      video.src = source;
      if (!await waitForMetadata(token)) {
        if (token === tuneToken) standby();
        return;
      }
    }
    const target = position();
    if (Number.isFinite(video.duration) && target >= video.duration && !video.loop) {
      standby("PROGRAMME ENDING – PLEASE STAND BY");
      return;
    }
    if (Math.abs(video.currentTime - target) > .5) seekTo(target);
    showLayer(null);
    await startPlayback(token);
  }

  function standby(text = "WE ARE EXPERIENCING TECHNICAL DIFFICULTIES") {
    stopVideo();
    $("[data-tv-standby-text]").textContent = text;
    showLayer("standby");
  }

  function offAir(data) {
    stopVideo();
    $("[data-tv-offair-channel]").textContent = `${data.channel.number}  ${data.channel.name}`;
    $("[data-tv-offair-resume]").textContent = data.resumes_at
      ? `PROGRAMMING RESUMES ${dayFormat.format(new Date(Date.parse(data.resumes_at)))} AT ${clock(Date.parse(data.resumes_at))}`
      : "NO PROGRAMMING IS SCHEDULED";
    showLayer("offair");
  }

  async function fetchNow(entry) {
    const sent = Date.now();
    const response = await fetch(`${root.dataset.nowUrl}?channel=${encodeURIComponent(entry.id)}`,
      {cache: "no-store", credentials: "same-origin"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const received = Date.now();
    skew = Date.parse(data.server_now) + (received - sent) / 2 - received;
    return data;
  }

  function sameAiring(a, b) {
    return a && b && a.start === b.start && a.media_url === b.media_url && a.kind === b.kind;
  }

  async function sync({quiet = false} = {}) {
    const entry = channel();
    if (!powered || !entry) return;
    const token = ++tuneToken;
    clearTimeout(transitionTimer);
    clearTimeout(retryTimer);
    let data;
    try {
      data = await fetchNow(entry);
    } catch {
      if (token !== tuneToken) return;
      standby("SIGNAL LOST – RETUNING");
      retryTimer = setTimeout(() => sync(), 5000);
      return;
    }
    if (token !== tuneToken) return;
    const unchanged = quiet && current && sameAiring(current.airing, data.airing) && current.channel.id === data.channel.id;
    current = data;
    fetchedAt = Date.parse(data.server_now);
    lastCheck = Date.now();
    const airing = data.airing;
    const end = Date.parse(airing.end);
    transitionTimer = setTimeout(() => sync(), Math.min(2 ** 31 - 1, Math.max(250, end - serverNow() + 250)));
    updatePanel();
    if (unchanged && airing.media_url && !video.paused) {
      if (Math.abs(video.currentTime - position()) > DRIFT_LIMIT) seekTo(position());
      return;
    }
    if (airing.media_url) await play(airing, token);
    else if (airing.kind === "standby") standby();
    else offAir(data);
    if (!osd.banner.hidden) renderBanner(!osd.description.hidden);
  }

  async function tune(newIndex, {banner = true} = {}) {
    if (!channels.length) return;
    index = (newIndex + channels.length) % channels.length;
    store.set("stationnet.tv.channel", String(channel().id));
    if (!powered) { powerOn(); return; }
    current = null;
    showNumber(String(channel().number));
    updatePanel();
    showStatic();
    await sync();
    if (banner) showBanner();
    if (guide.open) guide.render();
  }

  function powerOn() {
    powered = true;
    root.classList.add("is-on");
    showLayer(null);
    applyVolume();
    tune(index);
  }

  function powerOff() {
    powered = false;
    tuneToken += 1;
    clearTimeout(transitionTimer);
    clearTimeout(retryTimer);
    root.classList.remove("is-on");
    stopVideo();
    osd.banner.hidden = osd.number.hidden = osd.hint.hidden = true;
    showLayer("poweroff");
    updatePanel();
  }

  /* No free seeking, pausing or speed changes. */
  video.addEventListener("seeked", () => { selfSeek = false; });
  video.addEventListener("seeking", () => {
    if (!selfSeek && current && current.airing.media_url) seekTo(position());
  });
  video.addEventListener("ratechange", () => { if (video.playbackRate !== 1) video.playbackRate = 1; });
  video.addEventListener("pause", () => {
    if (powered && video.src && !video.ended && current && current.airing.media_url) {
      setTimeout(() => { if (powered && video.paused && video.src) startPlayback(tuneToken); }, 300);
    }
  });
  video.addEventListener("ended", () => { if (powered && !video.loop) standby("PLEASE STAND BY"); });
  video.addEventListener("error", () => {
    if (!powered || !video.getAttribute("src")) return;
    standby();
    clearTimeout(retryTimer);
    retryTimer = setTimeout(() => sync(), 10000);
  });
  video.addEventListener("waiting", () => {
    clearTimeout(stallTimer);
    stallTimer = setTimeout(() => { if (powered) sync(); }, STALL_LIMIT);
  });
  video.addEventListener("playing", () => clearTimeout(stallTimer));
  video.addEventListener("contextmenu", event => event.preventDefault());

  /* Drift, transitions after sleep, and schedule changes. */
  setInterval(() => {
    updatePanel();
    if (!osd.banner.hidden) renderBanner(!osd.description.hidden);
    if (guide.open) guide.tick();
    if (!powered || !current) return;
    if (serverNow() >= Date.parse(current.airing.end)) { sync(); return; }
    if (Date.now() - lastCheck > 60000) { sync({quiet: true}); return; }
    if (current.airing.media_url && !video.paused && video.readyState >= 2 && !video.loop &&
        Math.abs(video.currentTime - position()) > DRIFT_LIMIT) seekTo(position());
  }, 1000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && powered) sync({quiet: true}); });
  window.addEventListener("online", () => { if (powered) sync(); });
  window.addEventListener("pageshow", event => { if (event.persisted && powered) sync(); });

  /* Volume, fullscreen, direct entry ----------------------------------- */

  function changeVolume(delta) {
    volume = Math.min(1, Math.max(0, Math.round((volume + delta) * 20) / 20));
    muted = false;
    store.set("stationnet.tv.volume", String(volume));
    store.set("stationnet.tv.muted", "0");
    applyVolume();
    showVolume();
  }

  function toggleMute() {
    muted = !muted;
    store.set("stationnet.tv.muted", muted ? "1" : "0");
    applyVolume();
    if (!muted) osd.hint.hidden = true;
    showVolume();
  }

  function showVolume() {
    $("[data-tv-volume-label]").textContent = muted ? "MUTE" : "VOLUME";
    $("[data-tv-volume-bar]").style.setProperty("--level", `${muted ? 0 : Math.round(volume * 100)}%`);
    flash(osd.volume, 2000);
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen?.();
    else screen.requestFullscreen?.().catch(() => {});
  }

  let digits = "";
  let digitTimer = 0;
  const maxDigits = Math.max(1, ...channels.map(entry => String(entry.number).length));
  function enterDigit(digit) {
    digits = (digits + digit).slice(-maxDigits);
    showNumber(digits.padEnd(maxDigits, "_"), 2500);
    clearTimeout(digitTimer);
    const commit = () => {
      const wanted = Number.parseInt(digits, 10);
      digits = "";
      const found = channels.findIndex(entry => entry.number === wanted);
      if (found >= 0) tune(found);
      else showNumber(`${wanted} – NO CHANNEL`, 2000);
    };
    if (digits.length >= maxDigits) commit();
    else digitTimer = setTimeout(commit, 2000);
  }

  /* Electronic programme guide ----------------------------------------- */

  const guide = (() => {
    const node = $("[data-tv-guide]");
    const rowsNode = $("[data-guide-rows]");
    const timesNode = $("[data-guide-times]");
    const infoNode = $("[data-guide-info]");
    const clockNode = $("[data-guide-clock]");
    const SLOT = 30 * 60 * 1000;
    const SLOTS = 3;
    const ROWS = 8;
    const MAX_AHEAD = 7 * 24 * 3600 * 1000;
    const cache = new Map();
    let start = 0;
    let row = 0;
    let top = 0;
    let focus = 0;
    let data = null;
    let loading = 0;

    const floorSlot = ms => Math.floor(ms / SLOT) * SLOT;
    const span = () => SLOT * SLOTS;

    async function load() {
      const key = start;
      const token = ++loading;
      if (!cache.has(key)) {
        try {
          const url = `${root.dataset.guideUrl}?start=${encodeURIComponent(new Date(start).toISOString())}&hours=${SLOTS / 2}`;
          const response = await fetch(url, {cache: "no-store", credentials: "same-origin"});
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          cache.set(key, await response.json());
        } catch {
          if (token === loading) rowsNode.innerHTML = '<p class="tv-guide-empty">GUIDE DATA UNAVAILABLE</p>';
          return;
        }
      }
      if (token !== loading) return;
      data = cache.get(key);
      render();
    }

    function airingsFor(position) {
      const entry = data && data.channels.find(item => item.id === channels[position].id);
      return entry ? entry.airings : [];
    }

    function selectedAiring() {
      const list = airingsFor(row);
      return list.find(airing => Date.parse(airing.start) <= focus && focus < Date.parse(airing.end)) || list[0];
    }

    function render() {
      if (!data) return;
      const windowEnd = start + span();
      const now = serverNow();
      timesNode.replaceChildren();
      const day = document.createElement("span");
      day.className = "tv-guide-day";
      day.textContent = new Date(start).toDateString() === new Date(now).toDateString() ? "Today" : dayFormat.format(new Date(start));
      const timeline = document.createElement("span");
      timeline.className = "tv-guide-timeline";
      for (let slot = 0; slot < SLOTS; slot += 1) {
        const label = document.createElement("span");
        label.style.left = `${(slot / SLOTS) * 100}%`;
        label.textContent = clock(start + slot * SLOT);
        timeline.append(label);
      }
      timesNode.append(day, timeline);

      rowsNode.replaceChildren();
      const selected = selectedAiring();
      channels.slice(top, top + ROWS).forEach((entry, offset) => {
        const position = top + offset;
        const line = document.createElement("div");
        line.className = "tv-guide-row";
        line.setAttribute("role", "row");
        const name = document.createElement("button");
        name.type = "button";
        name.className = "tv-guide-channel" + (position === index && powered ? " is-current" : "");
        name.textContent = `${entry.number} ${entry.name}`;
        name.addEventListener("click", () => choose(position));
        const track = document.createElement("div");
        track.className = "tv-guide-track";
        for (const airing of airingsFor(position)) {
          const airStart = Date.parse(airing.start), airEnd = Date.parse(airing.end);
          const left = Math.max(airStart, start), right = Math.min(airEnd, windowEnd);
          if (right <= left) continue;
          const cell = document.createElement("button");
          cell.type = "button";
          cell.setAttribute("role", "gridcell");
          cell.className = "tv-guide-cell";
          if (airStart <= now && now < airEnd) cell.classList.add("is-now");
          if (airing.kind === "offair") cell.classList.add("is-offair");
          if (position === row && airing === selected) cell.classList.add("is-selected");
          cell.style.left = `${((left - start) / span()) * 100}%`;
          cell.style.width = `${((right - left) / span()) * 100}%`;
          if (airStart < start) cell.append(Object.assign(document.createElement("b"), {className: "tv-guide-more", textContent: "◄"}));
          cell.append(Object.assign(document.createElement("span"), {textContent: airingLabel(airing)}));
          if (airEnd > windowEnd) cell.append(Object.assign(document.createElement("b"), {className: "tv-guide-more", textContent: "►"}));
          cell.title = `${clock(airStart)}–${clock(airEnd)} ${airingLabel(airing)}`;
          cell.addEventListener("click", () => {
            if (position === row && airing === selectedAiring()) choose(position);
            else { row = position; focus = Math.max(airStart, start); render(); }
          });
          track.append(cell);
        }
        if (now >= start && now < windowEnd) {
          const line2 = document.createElement("i");
          line2.className = "tv-guide-nowline";
          line2.style.left = `${((now - start) / span()) * 100}%`;
          track.append(line2);
        }
        line.append(name, track);
        rowsNode.append(line);
      });
      if (!channels.length) rowsNode.innerHTML = '<p class="tv-guide-empty">NO CHANNELS</p>';
      renderInfo(selected);
      tick();
    }

    function renderInfo(airing) {
      infoNode.replaceChildren();
      if (!airing) return;
      const entry = channels[row];
      const title = Object.assign(document.createElement("strong"), {textContent: `${entry.number} ${entry.name} · ${airingLabel(airing)}`});
      const when = Object.assign(document.createElement("span"), {className: "tv-guide-info-time",
        textContent: `${dayFormat.format(new Date(Date.parse(airing.start)))} ${clock(Date.parse(airing.start))}–${clock(Date.parse(airing.end))}`});
      infoNode.append(title, when);
      if (airing.description) infoNode.append(Object.assign(document.createElement("p"), {textContent: airing.description}));
    }

    function tick() {
      clockNode.textContent = `${clock(serverNow())} ${dayFormat.format(new Date(serverNow()))}`;
    }

    function moveWindow(delta) {
      const earliest = floorSlot(serverNow());
      const next = Math.min(Math.max(start + delta, earliest), earliest + MAX_AHEAD);
      if (next === start) return false;
      focus = Math.min(Math.max(focus + (next - start), next), next + span() - 1);
      start = next;
      load();
      return true;
    }

    function moveRow(delta) {
      if (!channels.length) return;
      row = Math.min(channels.length - 1, Math.max(0, row + delta));
      if (row < top) top = row;
      if (row >= top + ROWS) top = row - ROWS + 1;
      render();
    }

    function moveCell(direction) {
      const list = airingsFor(row);
      const selected = selectedAiring();
      const at = list.indexOf(selected);
      const target = list[at + direction];
      if (direction > 0) {
        if (target && Date.parse(target.start) < start + span()) { focus = Date.parse(target.start); render(); }
        else if (moveWindow(SLOT)) focus = Math.max(focus, selected ? Date.parse(selected.end) : focus);
      } else if (target && Date.parse(target.end) > start) {
        focus = Math.max(Date.parse(target.start), start); render();
      } else moveWindow(-SLOT);
    }

    function choose(position) {
      close();
      tune(position);
    }

    function show() {
      node.hidden = false;
      api.open = true;
      start = floorSlot(serverNow());
      focus = serverNow();
      row = Math.max(0, index);
      top = Math.max(0, Math.min(row - Math.floor(ROWS / 2), channels.length - ROWS));
      cache.clear();
      load();
    }

    function close() {
      node.hidden = true;
      api.open = false;
      screen.focus({preventScroll: true});
    }

    function action(name) {
      if (name === "page-up") moveRow(-ROWS);
      else if (name === "page-down") moveRow(ROWS);
      else if (name === "day-forward") moveWindow(24 * 3600 * 1000);
      else if (name === "day-back") moveWindow(-24 * 3600 * 1000);
    }
    node.querySelectorAll("[data-guide-action]").forEach(button => {
      button.addEventListener("click", () => action(button.dataset.guideAction));
    });

    function key(event) {
      const handlers = {
        ArrowUp: () => moveRow(-1), ArrowDown: () => moveRow(1),
        ArrowLeft: () => moveCell(-1), ArrowRight: () => moveCell(1),
        PageUp: () => action("page-up"), PageDown: () => action("page-down"),
        "+": () => action("day-forward"), "=": () => action("day-forward"), "-": () => action("day-back"),
        Enter: () => choose(row), Escape: close, g: close, G: close,
      };
      const handler = handlers[event.key];
      if (!handler) return false;
      handler();
      return true;
    }

    const api = {open: false, show, close, key, render, tick};
    return api;
  })();

  /* Controls ----------------------------------------------------------- */

  $("[data-tv-power-on]").addEventListener("click", powerOn);
  $("[data-tv-power]").addEventListener("click", () => (powered ? powerOff() : powerOn()));
  $("[data-tv-channel-up]").addEventListener("click", () => tune(index + 1));
  $("[data-tv-channel-down]").addEventListener("click", () => tune(index - 1));
  $("[data-tv-volume-up]").addEventListener("click", () => changeVolume(.05));
  $("[data-tv-volume-down]").addEventListener("click", () => changeVolume(-.05));
  $("[data-tv-mute]").addEventListener("click", toggleMute);
  $("[data-tv-guide-toggle]").addEventListener("click", () => (guide.open ? guide.close() : guide.show()));
  $("[data-tv-info]").addEventListener("click", () => { if (powered) showBanner(true); });
  $("[data-tv-fullscreen]").addEventListener("click", toggleFullscreen);
  osd.exit.addEventListener("click", () => document.exitFullscreen?.());
  document.addEventListener("fullscreenchange", () => { osd.exit.hidden = !document.fullscreenElement; });
  root.querySelectorAll("[data-tv-digit]").forEach(button => {
    button.addEventListener("click", () => enterDigit(button.dataset.tvDigit));
  });
  video.addEventListener("click", () => {
    if (video.muted && !muted) { applyVolume(); osd.hint.hidden = true; }
    else if (powered) showBanner();
  });
  screen.addEventListener("dblclick", event => { if (event.target === video) toggleFullscreen(); });

  document.addEventListener("keydown", event => {
    const target = event.target;
    if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    if (target instanceof HTMLElement && (target.isContentEditable || target.matches("input, textarea, select"))) return;
    if (guide.open) {
      if (guide.key(event)) event.preventDefault();
      return;
    }
    const handlers = {
      ArrowUp: () => tune(index + 1), ArrowDown: () => tune(index - 1),
      PageUp: () => tune(index + 1), PageDown: () => tune(index - 1),
      "[": () => changeVolume(-.05), ",": () => changeVolume(-.05),
      "]": () => changeVolume(.05), ".": () => changeVolume(.05),
      m: toggleMute, M: toggleMute, g: guide.show, G: guide.show,
      i: () => powered && showBanner(true), I: () => powered && showBanner(true),
      f: toggleFullscreen, F: toggleFullscreen,
      Escape: () => { osd.banner.hidden = true; },
    };
    if (/^[0-9]$/.test(event.key)) { enterDigit(event.key); event.preventDefault(); return; }
    const handler = handlers[event.key];
    if (!handler) return;
    handler();
    event.preventDefault();
  });

  /* Start-up ----------------------------------------------------------- */

  index = channels.length ? initialIndex() : 0;
  updatePanel();
  if (channels.length) {
    // Try to come straight on; browsers that block autoplay fall back to the power card.
    powerOn();
    if (root.hasAttribute("data-open-guide")) guide.show();
  } else {
    layers.poweroff.hidden = true;
  }
})();
