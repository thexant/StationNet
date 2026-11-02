/* StationNet TV management: channels, programming library, uploads and schedules. */
(() => {
  const root = document.querySelector("[data-tv-admin]");
  if (!root) return;
  const $ = selector => root.querySelector(selector);
  const $$ = selector => [...root.querySelectorAll(selector)];
  const API = root.dataset.api;
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* storage unavailable */ } },
    remove(key) { try { localStorage.removeItem(key); } catch { /* storage unavailable */ } },
  };
  const WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
  let state = {channels: [], media: [], settings: {}, formats: [], chunk_bytes: 8 * 1024 * 1024};
  let day = null;

  /* Helpers ------------------------------------------------------------ */

  async function api(path, {method = "GET", body} = {}) {
    const options = {method, credentials: "same-origin", headers: {"X-CSRF-Token": root.dataset.csrf}};
    if (body instanceof FormData) options.body = body;
    else if (body !== undefined) {
      options.body = JSON.stringify(body);
      options.headers["Content-Type"] = "application/json";
    }
    let response;
    try {
      response = await fetch(`${API}/${path}`, options);
    } catch {
      return {ok: false, status: 0, data: {error: "Network error."}};
    }
    const data = await response.json().catch(() => ({error: `HTTP ${response.status}`}));
    return {ok: response.ok, status: response.status, data};
  }

  function status(message, error = false) {
    const node = $("[data-status]");
    node.textContent = message ? `SYS:// ${message}` : "";
    node.classList.toggle("is-error", error);
  }

  function element(tag, props = {}, ...children) {
    const node = Object.assign(document.createElement(tag), props);
    children.flat().forEach(child => node.append(child instanceof Node ? child : document.createTextNode(String(child ?? ""))));
    return node;
  }

  function button(label, onClick, className = "secondary") {
    const node = element("button", {type: "button", className, textContent: label});
    node.addEventListener("click", onClick);
    return node;
  }

  function duration(seconds) {
    if (seconds == null) return "?";
    const total = Math.round(seconds);
    const hours = Math.floor(total / 3600), minutes = Math.floor(total % 3600 / 60), secs = total % 60;
    return hours ? `${hours}h ${String(minutes).padStart(2, "0")}m` : `${minutes}m ${String(secs).padStart(2, "0")}s`;
  }

  function bytes(value) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = value, unit = 0;
    while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
    return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  }

  const zone = () => state.settings.timezone || "UTC";
  const zoneTime = ms => new Intl.DateTimeFormat(undefined, {timeZone: zone(), hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).format(new Date(ms));
  function zoneToday() {
    const parts = new Intl.DateTimeFormat("en-CA", {timeZone: zone(), year: "numeric", month: "2-digit", day: "2-digit"}).format(new Date());
    return parts;
  }
  const toMinutes = hhmm => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
  function clockFromMinutes(minutes) {
    const dayOffset = Math.floor(minutes / 1440);
    const within = ((Math.round(minutes) % 1440) + 1440) % 1440;
    const text = `${String(Math.floor(within / 60)).padStart(2, "0")}:${String(within % 60).padStart(2, "0")}`;
    return dayOffset > 0 ? `${text} +${dayOffset}d` : text;
  }
  const mediaById = id => state.media.find(item => item.id === id);
  const supported = name => state.formats.some(ext => name.toLowerCase().endsWith(ext));

  function closeOn(dialog) {
    dialog.querySelectorAll("[data-close]").forEach(node => node.addEventListener("click", () => dialog.close()));
  }

  /* Tabs --------------------------------------------------------------- */

  function showTab(name) {
    $$("[data-tab]").forEach(tab => {
      if (tab.dataset.tab === name) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    });
    $$("[data-panel]").forEach(panel => { panel.hidden = panel.dataset.panel !== name; });
    store.set("stationnet.tv.admin.tab", name);
  }
  $$("[data-tab]").forEach(tab => tab.addEventListener("click", () => showTab(tab.dataset.tab)));

  /* State -------------------------------------------------------------- */

  async function refresh() {
    const result = await api("state");
    if (!result.ok) { status(result.data.error || "Could not load TV data.", true); return; }
    state = result.data;
    $("[data-zone]").textContent = zone();
    $("[data-formats]").textContent = state.formats.join(" ");
    $("[data-import-path]").textContent = state.import_path;
    renderChannels();
    renderLibrary();
    renderScheduleChannels();
    measureDurations();
    await loadDay();
  }

  /* Channels ----------------------------------------------------------- */

  function renderChannels() {
    const body = $("[data-channels]");
    body.replaceChildren();
    if (!state.channels.length) {
      body.append(element("tr", {}, element("td", {colSpan: 6, className: "muted", textContent: "No channels yet. Create one to start scheduling."})));
    }
    state.channels.forEach((channel, position) => {
      const order = element("td", {}, button("↑", () => move(position, -1)), " ", button("↓", () => move(position, 1)));
      const name = element("td", {});
      if (channel.logo_admin_url) name.append(element("img", {className: "tv-logo-thumb", src: channel.logo_admin_url, alt: ""}), " ");
      name.append(element("strong", {textContent: channel.name}));
      if (channel.description) name.append(element("br"), element("small", {className: "muted", textContent: channel.description}));
      const gaps = channel.gap_mode === "filler" ? `Filler (${channel.filler.length} clips)` : (channel.offair_media_id ? "Custom off-air" : "Test card");
      body.append(element("tr", {},
        order, element("td", {textContent: channel.number}), name,
        element("td", {textContent: channel.enabled ? "ON AIR" : "HIDDEN"}), element("td", {textContent: gaps}),
        element("td", {className: "tv-actions"},
          button("SCHEDULE", () => { $("[data-schedule-channel]").value = channel.id; showTab("schedule"); loadDay(); }),
          button("EDIT", () => openChannel(channel)),
          button("DELETE", () => deleteChannel(channel), "danger"))));
    });
  }

  async function move(position, delta) {
    const ids = state.channels.map(channel => channel.id);
    const target = position + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[position], ids[target]] = [ids[target], ids[position]];
    const result = await api("channels/reorder", {method: "POST", body: {ids}});
    if (!result.ok) status(result.data.error, true);
    await refresh();
  }

  async function deleteChannel(channel) {
    if (!confirm(`Delete channel ${channel.number} ${channel.name} and its whole schedule? Library programmes are kept.`)) return;
    const result = await api(`channels/${channel.id}/delete`, {method: "POST", body: {}});
    status(result.ok ? `Channel ${channel.number} deleted.` : result.data.error, !result.ok);
    await refresh();
  }

  const channelDialog = $("[data-channel-dialog]");
  const channelForm = $("[data-channel-form]");
  closeOn(channelDialog);
  let editingChannel = null;
  let filler = [];

  function mediaOptions(select, {blank = "", filter = () => true, preferKinds = []} = {}) {
    select.replaceChildren();
    if (blank) select.append(element("option", {value: "", textContent: blank}));
    const items = state.media.filter(filter).sort((a, b) =>
      (preferKinds.includes(b.kind) - preferKinds.includes(a.kind)) || a.title.localeCompare(b.title));
    items.forEach(item => select.append(element("option", {value: item.id,
      textContent: `${item.title} · ${duration(item.duration_s)}${item.kind !== "program" ? ` · ${item.kind.toUpperCase()}` : ""}`})));
  }

  function renderFiller() {
    const list = $("[data-filler-list]");
    list.replaceChildren();
    filler.forEach((id, position) => {
      const item = mediaById(id);
      list.append(element("li", {}, element("span", {textContent: item ? `${item.title} (${duration(item.duration_s)})` : `#${id}`}),
        button("✕", () => { filler.splice(position, 1); renderFiller(); })));
    });
    if (!filler.length) list.append(element("li", {className: "muted", textContent: "No filler clips."}));
  }

  function openChannel(channel = null) {
    editingChannel = channel;
    channelForm.reset();
    $("[data-channel-dialog-title]").textContent = channel ? `Edit channel ${channel.number}` : "New channel";
    const nextNumber = Math.max(0, ...state.channels.map(entry => entry.number)) + 1;
    channelForm.number.value = channel ? channel.number : nextNumber;
    channelForm.name.value = channel ? channel.name : "";
    channelForm.description.value = channel ? channel.description : "";
    channelForm.enabled.checked = channel ? Boolean(channel.enabled) : true;
    channelForm.gap_mode.value = channel ? channel.gap_mode : "offair";
    mediaOptions(channelForm.offair_media_id, {blank: "Theme test card", filter: item => item.duration_s, preferKinds: ["offair"]});
    channelForm.offair_media_id.value = channel && channel.offair_media_id ? channel.offair_media_id : "";
    mediaOptions($("[data-filler-select]"), {filter: item => item.duration_s, preferKinds: ["ident", "bumper"]});
    filler = channel ? [...channel.filler] : [];
    renderFiller();
    const preview = $("[data-logo-preview]");
    preview.hidden = !(channel && channel.logo_admin_url);
    if (channel && channel.logo_admin_url) preview.src = channel.logo_admin_url;
    $("[data-logo-remove]").closest("label").hidden = !(channel && channel.has_logo);
    channelDialog.showModal();
  }

  $("[data-add-channel]").addEventListener("click", () => openChannel());
  $("[data-filler-add]").addEventListener("click", () => {
    const value = Number($("[data-filler-select]").value);
    if (value) { filler.push(value); renderFiller(); }
  });
  channelForm.addEventListener("submit", async event => {
    event.preventDefault();
    const body = {number: Number(channelForm.number.value), name: channelForm.name.value, description: channelForm.description.value,
      enabled: channelForm.enabled.checked, gap_mode: channelForm.gap_mode.value,
      offair_media_id: channelForm.offair_media_id.value ? Number(channelForm.offair_media_id.value) : null, filler};
    const result = await api(editingChannel ? `channels/${editingChannel.id}` : "channels", {method: "POST", body});
    if (!result.ok) { alert(result.data.error); return; }
    const id = editingChannel ? editingChannel.id : result.data.id;
    const logo = $("[data-logo-input]").files[0];
    if (logo) {
      const form = new FormData();
      form.append("logo", logo);
      const upload = await api(`channels/${id}/logo`, {method: "POST", body: form});
      if (!upload.ok) alert(upload.data.error);
    } else if ($("[data-logo-remove]").checked) {
      await api(`channels/${id}/logo/delete`, {method: "POST", body: {}});
    }
    channelDialog.close();
    status(`Channel ${body.number} saved.`);
    if (!editingChannel) $("[data-schedule-channel]").dataset.pending = id;
    await refresh();
  });

  /* Library ------------------------------------------------------------ */

  function folders() {
    return [...new Set(state.media.map(item => item.folder).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  }

  function renderLibrary() {
    const folderSelect = $("[data-library-folder]");
    const chosen = folderSelect.value;
    folderSelect.replaceChildren(element("option", {value: "", textContent: "All folders"}),
      ...folders().map(name => element("option", {value: name, textContent: name})));
    folderSelect.value = folders().includes(chosen) ? chosen : "";
    $("[data-folder-list]").replaceChildren(...folders().map(name => element("option", {value: name})));
    const search = $("[data-library-search]").value.trim().toLowerCase();
    const kind = $("[data-library-kind]").value;
    const body = $("[data-library]");
    body.replaceChildren();
    const rows = state.media.filter(item => (!kind || item.kind === kind) && (!folderSelect.value || item.folder === folderSelect.value) &&
      (!search || `${item.title} ${item.folder} ${item.original_name}`.toLowerCase().includes(search)));
    if (!rows.length) {
      body.append(element("tr", {}, element("td", {colSpan: 8, className: "muted",
        textContent: state.media.length ? "Nothing matches." : "The library is empty. Upload videos or scan the import folder."})));
    }
    rows.forEach(item => {
      const title = element("td", {}, element("strong", {textContent: item.title}));
      if (item.missing) title.append(" ", element("span", {className: "tv-badge is-error", textContent: "MISSING FILE"}));
      if (item.duration_s == null) title.append(" ", element("span", {className: "tv-badge", textContent: "MEASURING"}));
      body.append(element("tr", {},
        title, element("td", {textContent: item.folder || "—"}), element("td", {textContent: item.kind.toUpperCase()}),
        element("td", {textContent: duration(item.duration_s)}), element("td", {textContent: bytes(item.size_bytes)}),
        element("td", {textContent: item.source === "import" ? "IMPORTED" : "UPLOADED"}),
        element("td", {textContent: item.usage ? `${item.usage} block${item.usage === 1 ? "" : "s"}` : "—"}),
        element("td", {className: "tv-actions"}, button("EDIT", () => openMedia(item)), button("DELETE", () => deleteMedia(item), "danger"))));
    });
  }
  ["[data-library-search]", "[data-library-folder]", "[data-library-kind]"].forEach(selector =>
    $(selector).addEventListener("input", renderLibrary));

  async function deleteMedia(item) {
    const note = item.source === "import" ? "The original file in the import folder is kept." : "The uploaded file will be deleted.";
    if (!confirm(`Remove “${item.title}” from the library? ${note}`)) return;
    const result = await api(`media/${item.id}/delete`, {method: "POST", body: {}});
    if (!result.ok) {
      alert(result.data.error + (result.data.usage ? `\n\n${result.data.usage.join("\n")}` : ""));
      return;
    }
    status(`“${item.title}” removed.`);
    await refresh();
  }

  $("[data-recheck]").addEventListener("click", async () => {
    const result = await api("media/recheck", {method: "POST", body: {}});
    status(result.ok ? `Files rechecked: ${result.data.missing} missing.` : result.data.error, !result.ok);
    await refresh();
  });

  const mediaDialog = $("[data-media-dialog]");
  const mediaForm = $("[data-media-form]");
  closeOn(mediaDialog);
  mediaDialog.addEventListener("close", () => { const preview = $("[data-media-preview]"); preview.pause(); preview.removeAttribute("src"); preview.load(); });
  let editingMedia = null;
  function openMedia(item) {
    editingMedia = item;
    mediaForm.title.value = item.title;
    mediaForm.description.value = item.description;
    mediaForm.folder.value = item.folder;
    mediaForm.kind.value = item.kind;
    $("[data-media-facts]").textContent = [item.original_name, item.width ? `${item.width}×${item.height}` : "",
      duration(item.duration_s), bytes(item.size_bytes), item.rel_path].filter(Boolean).join(" · ");
    $("[data-media-preview]").src = item.url;
    mediaDialog.showModal();
  }
  mediaForm.addEventListener("submit", async event => {
    event.preventDefault();
    const result = await api(`media/${editingMedia.id}`, {method: "POST", body: {title: mediaForm.title.value,
      description: mediaForm.description.value, folder: mediaForm.folder.value, kind: mediaForm.kind.value}});
    if (!result.ok) { alert(result.data.error); return; }
    mediaDialog.close();
    await refresh();
  });

  /* Browser-side duration measurement for stations without ffprobe. */
  let measuring = false;
  async function measureDurations() {
    if (measuring) return;
    measuring = true;
    const probe = $("[data-probe]");
    for (const item of state.media.filter(entry => entry.duration_s == null && !entry.missing)) {
      const result = await new Promise(resolve => {
        const timer = setTimeout(() => finish(null), 20000);
        function finish(value) {
          clearTimeout(timer);
          probe.onloadedmetadata = probe.onerror = null;
          resolve(value);
        }
        probe.onloadedmetadata = () => finish({duration: probe.duration, width: probe.videoWidth, height: probe.videoHeight});
        probe.onerror = () => finish(null);
        probe.src = item.url;
      });
      if (result && Number.isFinite(result.duration) && result.duration >= 1) {
        await api(`media/${item.id}/probe`, {method: "POST", body: result});
        item.duration_s = result.duration;
      }
    }
    probe.removeAttribute("src");
    probe.load();
    measuring = false;
    renderLibrary();
  }

  /* Uploads ------------------------------------------------------------ */

  const queue = [];
  let uploading = false;
  let queueBytes = 0, queueDone = 0;

  function queueEntry(file, relpath) {
    const label = element("span", {className: "tv-queue-name", textContent: relpath});
    const state_ = element("span", {className: "tv-queue-state", textContent: "WAITING"});
    const bar = element("i");
    const node = element("li", {}, label, state_, element("div", {className: "tv-progress"}, bar));
    $("[data-queue]").prepend(node);
    return {file, relpath, node, setState(text, kind = "") { state_.textContent = text; node.dataset.state = kind; },
      progress(fraction) { bar.style.width = `${Math.round(fraction * 100)}%`; }};
  }

  function updateQueueSummary() {
    $("[data-queue-summary]").hidden = !queueBytes;
    $("[data-queue-progress]").style.width = `${queueBytes ? Math.round(queueDone / queueBytes * 100) : 0}%`;
    $("[data-queue-text]").textContent = `${bytes(queueDone)} of ${bytes(queueBytes)} · ${queue.length} file(s) waiting`;
  }

  function addFiles(entries) {
    for (const {file, relpath} of entries) {
      const entry = queueEntry(file, relpath);
      if (!supported(file.name)) { entry.setState("SKIPPED – UNSUPPORTED FORMAT", "skip"); continue; }
      queue.push(entry);
      queueBytes += file.size;
    }
    updateQueueSummary();
    runQueue();
  }

  function putChunk(id, offset, blob, onProgress) {
    return new Promise(resolve => {
      const request = new XMLHttpRequest();
      request.open("PUT", `${API}/uploads/${id}?offset=${offset}`);
      request.setRequestHeader("X-CSRF-Token", root.dataset.csrf);
      request.setRequestHeader("Content-Type", "application/octet-stream");
      request.upload.onprogress = event => onProgress(event.loaded);
      request.onload = () => {
        let data;
        try { data = JSON.parse(request.responseText); } catch { data = {error: `HTTP ${request.status}`}; }
        resolve({ok: request.status < 300, status: request.status, data});
      };
      request.onerror = request.ontimeout = () => resolve({ok: false, status: 0, data: {error: "Network error."}});
      request.send(blob);
    });
  }

  async function uploadOne(entry) {
    const {file, relpath} = entry;
    const key = `stationnet.tv.upload:${relpath}|${file.size}|${file.lastModified}`;
    let id = store.get(key), offset = 0;
    if (id) {
      const resumed = await api(`uploads/${id}`);
      if (resumed.ok) offset = resumed.data.offset; else { store.remove(key); id = null; }
    }
    if (!id) {
      const created = await api("uploads", {method: "POST", body: {name: file.name, size: file.size, relpath}});
      if (!created.ok) return {status: "failed", message: created.data.error};
      if (created.data.status !== "uploading") return created.data;
      id = created.data.upload_id;
      store.set(key, id);
    }
    const chunk = state.chunk_bytes;
    let failures = 0;
    while (offset < file.size) {
      const base = offset;
      entry.progress(base / file.size);
      const result = await putChunk(id, base, file.slice(base, base + chunk), loaded => {
        entry.progress((base + loaded) / file.size);
      });
      if (result.status === 409 && Number.isFinite(result.data.offset)) { offset = result.data.offset; continue; }
      if (result.status === 404) { store.remove(key); return {status: "failed", message: "Upload session expired; add the file again."}; }
      if (!result.ok) {
        failures += 1;
        if (failures > 5) return {status: "failed", message: `${result.data.error} Add the file again to resume.`};
        entry.setState(`RETRYING (${failures})…`);
        await new Promise(resolve => setTimeout(resolve, 1000 * 2 ** failures));
        continue;
      }
      failures = 0;
      queueDone += Math.min(file.size, result.data.offset ?? file.size) - base;
      updateQueueSummary();
      if (result.data.status === "uploading") { offset = result.data.offset; entry.setState(`${Math.round(offset / file.size * 100)}%`); continue; }
      store.remove(key);
      return result.data;
    }
    store.remove(key);
    return {status: "failed", message: "Upload ended unexpectedly."};
  }

  async function runQueue() {
    if (uploading) return;
    uploading = true;
    let added = 0;
    while (queue.length) {
      const entry = queue.shift();
      entry.setState("UPLOADING");
      let result;
      try { result = await uploadOne(entry); } catch (error) { result = {status: "failed", message: String(error)}; }
      const labels = {ok: ["ADDED", "ok"], duplicate: ["DUPLICATE – SKIPPED", "skip"], unsupported: ["UNSUPPORTED – SKIPPED", "skip"]};
      const [text, kind] = labels[result.status] || ["FAILED", "error"];
      entry.setState(result.message && result.status !== "ok" ? `${text}: ${result.message}` : text, kind);
      entry.progress(result.status === "ok" ? 1 : 0);
      if (result.status === "ok") added += 1;
      updateQueueSummary();
    }
    uploading = false;
    queueBytes = queueDone = 0;
    $("[data-queue-summary]").hidden = true;
    if (added) { status(`${added} programme(s) added to the library.`); await refresh(); }
  }

  $("[data-file-input]").addEventListener("change", event => {
    addFiles([...event.target.files].map(file => ({file, relpath: file.name})));
    event.target.value = "";
  });
  $("[data-folder-input]").addEventListener("change", event => {
    addFiles([...event.target.files].map(file => ({file, relpath: file.webkitRelativePath || file.name})));
    event.target.value = "";
  });

  async function walk(entry, found) {
    if (entry.isFile) {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject)).catch(() => null);
      if (file) found.push({file, relpath: entry.fullPath.replace(/^\//, "")});
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      for (;;) {
        const batch = await new Promise(resolve => reader.readEntries(resolve, () => resolve([])));
        if (!batch.length) break;
        for (const child of batch) await walk(child, found);
      }
    }
  }

  const dropzone = $("[data-dropzone]");
  dropzone.addEventListener("dragover", event => { event.preventDefault(); dropzone.classList.add("is-over"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("is-over"));
  dropzone.addEventListener("drop", async event => {
    event.preventDefault();
    dropzone.classList.remove("is-over");
    const entries = [...event.dataTransfer.items].map(item => item.webkitGetAsEntry && item.webkitGetAsEntry()).filter(Boolean);
    const found = [];
    if (entries.length) for (const entry of entries) await walk(entry, found);
    else [...event.dataTransfer.files].forEach(file => found.push({file, relpath: file.name}));
    addFiles(found);
  });

  /* Server-folder import ----------------------------------------------- */

  $("[data-import-scan]").addEventListener("click", async event => {
    const trigger = event.currentTarget;
    trigger.disabled = true;
    const log = $("[data-import-log]");
    log.replaceChildren();
    $("[data-import-summary]").hidden = false;
    const counts = {ok: 0, known: 0, duplicate: 0, unsupported: 0, skipped: 0, failed: 0};
    let cursor = 0, total = 0, done = false;
    while (!done) {
      const result = await api("import/scan", {method: "POST", body: {cursor}});
      if (!result.ok) { status(result.data.error || "Import failed.", true); break; }
      ({cursor, total, done} = result.data);
      for (const item of result.data.results) {
        counts[item.status] = (counts[item.status] || 0) + 1;
        if (item.status !== "known") {
          const line = element("li", {},
            element("span", {className: "tv-queue-name", textContent: item.path}),
            element("span", {className: "tv-queue-state", textContent: item.status.toUpperCase() + (item.message ? `: ${item.message}` : "")}));
          line.dataset.state = item.status === "ok" ? "ok" : item.status === "failed" ? "error" : "skip";
          log.append(line);
        }
      }
      $("[data-import-progress]").style.width = `${total ? Math.round(Math.min(cursor, total) / total * 100) : 100}%`;
      $("[data-import-text]").textContent = `Scanned ${Math.min(cursor, total)} of ${total} file(s)`;
    }
    $("[data-import-text]").textContent += ` · ${counts.ok} added, ${counts.known} already in library, ${counts.duplicate} duplicate, ` +
      `${counts.unsupported} unsupported, ${counts.skipped} skipped, ${counts.failed} failed`;
    trigger.disabled = false;
    await refresh();
  });

  /* Schedule ----------------------------------------------------------- */

  const channelSelect = $("[data-schedule-channel]");
  const dateInput = $("[data-schedule-date]");

  function renderScheduleChannels() {
    const chosen = channelSelect.dataset.pending || channelSelect.value || store.get("stationnet.tv.admin.channel");
    delete channelSelect.dataset.pending;
    channelSelect.replaceChildren(...state.channels.map(channel =>
      element("option", {value: channel.id, textContent: `${channel.number} ${channel.name}`})));
    if (state.channels.some(channel => String(channel.id) === String(chosen))) channelSelect.value = chosen;
    if (!dateInput.value) dateInput.value = zoneToday();
  }

  async function loadDay() {
    const body = $("[data-day-airings]");
    const blocks = $("[data-blocks]");
    if (!channelSelect.value) {
      day = null;
      body.replaceChildren(element("tr", {}, element("td", {colSpan: 4, className: "muted", textContent: "Create a channel first."})));
      blocks.replaceChildren();
      $("[data-conflicts]").replaceChildren();
      return;
    }
    store.set("stationnet.tv.admin.channel", channelSelect.value);
    const result = await api(`schedule?channel=${channelSelect.value}&date=${dateInput.value}`);
    if (!result.ok) { status(result.data.error, true); return; }
    day = result.data;
    body.replaceChildren(...day.airings.map(airing => {
      const start = Date.parse(airing.start), end = Date.parse(airing.end);
      const row = element("tr", {className: `is-${airing.kind}`},
        element("td", {textContent: `${zoneTime(start)}–${zoneTime(end)}`}),
        element("td", {textContent: airing.kind === "offair" ? "OFF AIR" : airing.kind === "filler" ? `Filler: ${airing.title}` :
          airing.kind === "standby" ? `${airing.title} (FILE MISSING)` : airing.title}),
        element("td", {textContent: duration((end - start) / 1000)}),
        element("td", {textContent: airing.block || "—"}));
      return row;
    }));
    $("[data-conflicts]").replaceChildren(...day.conflicts.map(conflict => element("p", {className: "flash warning",
      textContent: `OVERLAP ${zoneTime(Date.parse(conflict.start))}–${zoneTime(Date.parse(conflict.end))}: “${conflict.block}” overrides “${conflict.preempted}”.`})));
    blocks.replaceChildren(...day.blocks.map(block => {
      const airs = block.kind === "once" ? block.start_date :
        WEEKDAYS.filter((_, position) => block.weekdays & (1 << position)).join(" ") +
        (block.valid_from || block.valid_until ? ` (${block.valid_from || "…"} → ${block.valid_until || "…"})` : "");
      const runs = block.loop_until ? `repeats until ${block.loop_until}` :
        `${duration(block.total_duration)} → ${clockFromMinutes(toMinutes(block.start_time) + block.total_duration / 60)}`;
      const row = element("tr", {className: block.airs_on_day ? "is-today" : ""},
        element("td", {}, element("strong", {textContent: block.title}), block.enabled ? "" : element("span", {className: "tv-badge", textContent: "DISABLED"})),
        element("td", {textContent: airs}), element("td", {textContent: block.start_time}), element("td", {textContent: runs}),
        element("td", {textContent: block.items.length}),
        element("td", {className: "tv-actions"}, button("EDIT", () => openBlock(block)), button("DELETE", () => deleteBlock(block), "danger")));
      return row;
    }));
    if (!day.blocks.length) blocks.append(element("tr", {}, element("td", {colSpan: 6, className: "muted", textContent: "No blocks yet. Add one to put programmes on air."})));
  }

  channelSelect.addEventListener("change", loadDay);
  dateInput.addEventListener("change", loadDay);
  $$("[data-day-step]").forEach(node => node.addEventListener("click", () => {
    const value = new Date(`${dateInput.value}T12:00:00Z`);
    value.setUTCDate(value.getUTCDate() + Number(node.dataset.dayStep));
    dateInput.value = value.toISOString().slice(0, 10);
    loadDay();
  }));
  $("[data-day-today]").addEventListener("click", () => { dateInput.value = zoneToday(); loadDay(); });

  async function deleteBlock(block) {
    if (!confirm(`Delete the block “${block.title}”?`)) return;
    const result = await api(`blocks/${block.id}/delete`, {method: "POST", body: {}});
    status(result.ok ? "Block deleted." : result.data.error, !result.ok);
    if (blockDialog.open) blockDialog.close();
    await refresh();
  }

  /* Block editor */
  const blockDialog = $("[data-block-dialog]");
  const blockForm = $("[data-block-form]");
  closeOn(blockDialog);
  let editingBlock = null;
  let items = [];

  function syncWhen() {
    const kind = blockForm.querySelector("[name=kind]:checked").value;
    blockForm.querySelectorAll("[data-when]").forEach(node => { node.hidden = node.dataset.when !== kind; });
    blockForm.loop_until.disabled = !blockForm.loop.checked;
    renderItems();
  }
  blockForm.querySelectorAll("[name=kind], [name=loop]").forEach(node => node.addEventListener("change", syncWhen));
  blockForm.start_time.addEventListener("input", renderItems);
  blockForm.loop_until.addEventListener("input", renderItems);

  function renderItems() {
    const body = $("[data-items]");
    body.replaceChildren();
    const start = blockForm.start_time.value ? toMinutes(blockForm.start_time.value) : null;
    let elapsed = 0;
    items.forEach((item, position) => {
      const media = mediaById(item.media_id);
      const length = media ? media.duration_s || 0 : 0;
      const when = start == null ? "—" : `${clockFromMinutes(start + elapsed / 60)}–${clockFromMinutes(start + (elapsed + length) / 60)}`;
      elapsed += length;
      const title = element("input", {value: item.title_override || "", placeholder: media ? media.title : "(missing)", maxLength: 150,
        ariaLabel: "Title shown in the guide"});
      title.addEventListener("input", () => { item.title_override = title.value; });
      body.append(element("tr", {},
        element("td", {className: "tv-airs", textContent: when}),
        element("td", {}, title),
        element("td", {textContent: duration(media ? media.duration_s : null)}),
        element("td", {className: "tv-actions"},
          button("↑", () => { if (position) { [items[position - 1], items[position]] = [items[position], items[position - 1]]; renderItems(); } }),
          button("↓", () => { if (position < items.length - 1) { [items[position + 1], items[position]] = [items[position], items[position + 1]]; renderItems(); } }),
          button("✕", () => { items.splice(position, 1); renderItems(); }))));
    });
    if (!items.length) body.append(element("tr", {}, element("td", {colSpan: 4, className: "muted", textContent: "Add programmes from the library below."})));
    const total = $("[data-block-total]");
    if (start == null || !items.length) { total.textContent = ""; return; }
    const finish = blockForm.loop.checked && blockForm.loop_until.value
      ? `repeats until ${blockForm.loop_until.value}${toMinutes(blockForm.loop_until.value) <= start ? " (next day)" : ""}`
      : `ends ${clockFromMinutes(start + elapsed / 60)}`;
    total.textContent = `Running time ${duration(elapsed)} · starts ${clockFromMinutes(start)} · ${finish}`;
    total.classList.toggle("is-error", !blockForm.loop.checked && elapsed > 86400);
  }

  function renderPicker() {
    const search = $("[data-item-search]").value.trim().toLowerCase();
    const select = $("[data-item-select]");
    mediaOptions(select, {filter: item => item.duration_s && (!search || `${item.title} ${item.folder}`.toLowerCase().includes(search))});
    const folderSelect = $("[data-item-folder]");
    folderSelect.replaceChildren(...folders().map(name => element("option", {value: name, textContent: name})));
    folderSelect.disabled = !folders().length;
  }
  $("[data-item-search]").addEventListener("input", renderPicker);
  $("[data-item-add]").addEventListener("click", () => {
    const value = Number($("[data-item-select]").value);
    if (value) { items.push({media_id: value, title_override: ""}); renderItems(); }
  });
  $("[data-item-add-folder]").addEventListener("click", () => {
    const folder = $("[data-item-folder]").value;
    state.media.filter(item => item.folder === folder && item.duration_s && item.kind === "program")
      .sort((a, b) => a.original_name.localeCompare(b.original_name, undefined, {numeric: true}))
      .forEach(item => items.push({media_id: item.id, title_override: ""}));
    renderItems();
  });

  $("[data-start-after]").addEventListener("click", () => {
    if (!day) return;
    let latest = null;
    for (const block of day.blocks) {
      if (!block.airs_on_day || (editingBlock && block.id === editingBlock.id)) continue;
      const begin = toMinutes(block.start_time);
      let end = block.loop_until ? toMinutes(block.loop_until) : begin + Math.ceil(block.total_duration / 60);
      if (block.loop_until && end <= begin) end += 1440;
      if (latest === null || end > latest) latest = end;
    }
    if (latest === null) { status("No other block airs on this day; choose a start time."); return; }
    if (latest >= 1440) status("The previous block runs past midnight; the start time wraps to the next day.");
    blockForm.start_time.value = clockFromMinutes(latest % 1440).slice(0, 5);
    renderItems();
  });

  function openBlock(block = null) {
    if (!channelSelect.value) { status("Create a channel first.", true); return; }
    editingBlock = block;
    blockForm.reset();
    $("[data-block-dialog-title]").textContent = block ? "Edit programming block" : "New programming block";
    blockForm.title.value = block ? block.title : "";
    blockForm.description.value = block ? block.description : "";
    blockForm.enabled.checked = block ? Boolean(block.enabled) : true;
    blockForm.querySelector(`[name=kind][value=${block ? block.kind : "once"}]`).checked = true;
    blockForm.start_date.value = block && block.start_date ? block.start_date : dateInput.value;
    const weekday = (new Date(`${dateInput.value}T12:00:00Z`).getUTCDay() + 6) % 7;
    blockForm.querySelectorAll("[name=weekday]").forEach(node => {
      node.checked = block ? Boolean(block.weekdays & (1 << Number(node.value))) : Number(node.value) === weekday;
    });
    blockForm.valid_from.value = block && block.valid_from ? block.valid_from : "";
    blockForm.valid_until.value = block && block.valid_until ? block.valid_until : "";
    blockForm.start_time.value = block ? block.start_time : "";
    blockForm.loop.checked = Boolean(block && block.loop_until);
    blockForm.loop_until.value = block && block.loop_until ? block.loop_until : "";
    items = block ? block.items.map(item => ({media_id: item.media_id, title_override: item.title_override || ""})) : [];
    $("[data-block-delete]").hidden = !block;
    $("[data-item-search]").value = "";
    renderPicker();
    syncWhen();
    blockDialog.showModal();
  }

  $("[data-add-block]").addEventListener("click", () => openBlock());
  $("[data-block-delete]").addEventListener("click", () => editingBlock && deleteBlock(editingBlock));
  blockForm.addEventListener("submit", async event => {
    event.preventDefault();
    const kind = blockForm.querySelector("[name=kind]:checked").value;
    const weekdays = [...blockForm.querySelectorAll("[name=weekday]:checked")].reduce((mask, node) => mask | (1 << Number(node.value)), 0);
    const body = {channel_id: Number(channelSelect.value), title: blockForm.title.value, description: blockForm.description.value,
      enabled: blockForm.enabled.checked, kind, start_date: blockForm.start_date.value, weekdays,
      valid_from: blockForm.valid_from.value, valid_until: blockForm.valid_until.value, start_time: blockForm.start_time.value,
      loop_until: blockForm.loop.checked ? blockForm.loop_until.value : null, items};
    const result = await api(editingBlock ? `blocks/${editingBlock.id}` : "blocks", {method: "POST", body});
    if (!result.ok) { alert(result.data.error); return; }
    blockDialog.close();
    status("Block saved.");
    await refresh();
  });

  /* Copy day */
  const copyDialog = $("[data-copy-dialog]");
  const copyForm = $("[data-copy-form]");
  closeOn(copyDialog);
  $("[data-copy-day]").addEventListener("click", () => {
    if (!channelSelect.value) return;
    const next = new Date(`${dateInput.value}T12:00:00Z`);
    next.setUTCDate(next.getUTCDate() + 1);
    copyForm.from.value = next.toISOString().slice(0, 10);
    next.setUTCDate(next.getUTCDate() + 6);
    copyForm.to.value = next.toISOString().slice(0, 10);
    copyDialog.showModal();
  });
  copyForm.addEventListener("submit", async event => {
    event.preventDefault();
    const dates = [];
    const cursor = new Date(`${copyForm.from.value}T12:00:00Z`), last = new Date(`${copyForm.to.value}T12:00:00Z`);
    while (cursor <= last && dates.length < 62) { dates.push(cursor.toISOString().slice(0, 10)); cursor.setUTCDate(cursor.getUTCDate() + 1); }
    const result = await api("blocks/copy-day", {method: "POST", body: {channel_id: Number(channelSelect.value), from_date: dateInput.value, to_dates: dates}});
    if (!result.ok) { alert(result.data.error); return; }
    copyDialog.close();
    status(`${result.data.copied} block(s) copied.`);
    await refresh();
  });

  showTab(store.get("stationnet.tv.admin.tab") || "schedule");
  refresh();
})();
