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
  let state = {channels: [], media: [], folder_rules: [], settings: {}, formats: [], chunk_bytes: 8 * 1024 * 1024};
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
    const known = new Set(state.media.map(item => item.id));
    [...selected].forEach(id => { if (!known.has(id)) selected.delete(id); });
    $("[data-zone]").textContent = zone();
    $("[data-formats]").textContent = state.formats.join(" ");
    $("[data-import-path]").textContent = state.import_path;
    renderChannels();
    renderFolders();
    fillContentSelects();
    renderLibrary();
    renderScheduleChannels();
    renderAutoChannels();
    measureDurations();
    await Promise.all([loadDay(), loadAuto()]);
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
  const FILLER_LIMIT = 200;  // matches the server's cap in tv.py
  document.querySelectorAll("[data-filler-add-kind]").forEach(control => control.addEventListener("click", () => {
    const kind = control.dataset.fillerAddKind;
    const matches = state.media.filter(item => item.kind === kind && item.duration_s)
      .sort((a, b) => a.title.localeCompare(b.title));
    if (!matches.length) { alert(`No ${kind}s with a measured duration in the library yet.`); return; }
    const fresh = matches.map(item => item.id).filter(id => !filler.includes(id));
    const room = Math.max(0, FILLER_LIMIT - filler.length);
    filler.push(...fresh.slice(0, room));
    renderFiller();
    if (fresh.length > room) alert(`Filler is limited to ${FILLER_LIMIT} clips; ${fresh.length - room} were not added.`);
  }));
  $("[data-filler-clear]").addEventListener("click", () => { filler = []; renderFiller(); });
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

  const KIND_LABELS = {program: "PROG", ident: "IDENT", bumper: "BUMPER", commercial: "AD", offair: "OFF-AIR"};
  const KIND_NAMES = {program: "Programme", ident: "Station ident", bumper: "Bumper / intermission", commercial: "Commercial", offair: "Off-air loop"};
  const CONTENT_NAMES = {show: "TV show", miniseries: "Miniseries", documentary: "Documentary", movie: "Movie",
    short: "Short film", animation: "Animation", special: "Special", other: "Other"};
  const DEFAULT_GENRES = ["Comedy", "Drama", "Action", "Adventure", "Science fiction", "Fantasy", "Horror", "Mystery",
    "Crime", "Romance", "Western", "War", "History", "Science", "Nature", "Educational", "Music", "News", "Sport", "Kids"];
  const genres = () => [...new Set(state.media.map(item => item.genre).filter(Boolean).concat(DEFAULT_GENRES)
    .map(name => name.trim()))].sort((a, b) => a.localeCompare(b));
  const seriesLabel = item => item.series ? `${item.series}${item.season != null || item.episode != null ?
    ` S${String(item.season ?? 0).padStart(2, "0")}E${String(item.episode ?? 0).padStart(2, "0")}` : ""}` : "";

  function fillContentSelects() {
    $$("[data-content-select]").forEach(select => {
      const value = select.value;
      select.replaceChildren(element("option", {value: "", textContent: "Not set"}),
        ...Object.entries(CONTENT_NAMES).map(([key, label]) => element("option", {value: key, textContent: label})));
      select.value = value;
    });
    const content = $("[data-library-content]"), genre = $("[data-library-genre]");
    const [contentValue, genreValue] = [content.value, genre.value];
    content.replaceChildren(element("option", {value: "", textContent: "Any content"}),
      ...Object.entries(CONTENT_NAMES).map(([key, label]) => element("option", {value: key, textContent: label})),
      element("option", {value: "-", textContent: "Not set"}));
    const used = [...new Set(state.media.map(item => item.genre).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    genre.replaceChildren(element("option", {value: "", textContent: "Any genre"}),
      ...used.map(name => element("option", {value: name, textContent: name})), element("option", {value: "-", textContent: "Not set"}));
    content.value = contentValue;
    genre.value = used.includes(genreValue) || genreValue === "-" ? genreValue : "";
    $("[data-genre-list]").replaceChildren(...genres().map(name => element("option", {value: name})));
    $("[data-series-list]").replaceChildren(...[...new Set(state.media.map(item => item.series).filter(Boolean))].sort()
      .map(name => element("option", {value: name})));
  }
  const inFolder = (item, folder, recursive) => item.folder === folder || (recursive && folder !== "" && item.folder.startsWith(`${folder}/`));

  /* Every folder in use plus its parents, so a whole tree can be selected at once. */
  function folders() {
    const found = new Set();
    state.media.forEach(item => {
      const parts = item.folder ? item.folder.split("/") : [];
      parts.forEach((_, index) => found.add(parts.slice(0, index + 1).join("/")));
    });
    return [...found].sort((a, b) => a.localeCompare(b, undefined, {numeric: true}));
  }

  function renderFolders() {
    const body = $("[data-folders]");
    const names = folders();
    const rows = (state.media.some(item => !item.folder) ? [""] : []).concat(names);
    $("[data-folders-card]").hidden = !state.media.length;
    body.replaceChildren(...rows.map(folder => {
      const recursiveDefault = folder !== "" && names.some(name => name.startsWith(`${folder}/`));
      const members = state.media.filter(item => inFolder(item, folder, true));
      const counts = {};
      members.forEach(item => { counts[item.kind] = (counts[item.kind] || 0) + 1; });
      const rule = state.folder_rules.find(entry => entry.folder === folder);
      const name = element("td", {className: "tv-folder-name"},
        element("span", {style: `padding-left:${folder ? (folder.split("/").length - 1) * 1.1 : 0}rem`,
          textContent: folder ? folder.split("/").pop() : "(no folder)"}));
      if (rule) {
        name.append(" ", element("span", {className: "tv-badge", title: "New files in this folder get this type",
          textContent: `AUTO: ${KIND_LABELS[rule.kind]}${rule.recursive ? " +SUB" : ""}`}),
        button("✕", async () => {
          await api("folders/rule/delete", {method: "POST", body: {folder}});
          status(`New files in “${folder || "(no folder)"}” will be added as programmes.`);
          await refresh();
        }));
      }
      const kind = element("select", {ariaLabel: "Type"}, ...Object.entries(KIND_NAMES).map(([value, label]) =>
        element("option", {value, textContent: label})));
      kind.value = rule ? rule.kind : (Object.entries(counts).sort((a, b) => b[1] - a[1])[0] || ["program"])[0];
      const recursive = element("input", {type: "checkbox", checked: recursiveDefault, disabled: !recursiveDefault});
      const remember = element("input", {type: "checkbox", checked: Boolean(rule)});
      const apply = button("APPLY", async () => {
        const scope = recursive.checked ? "and its sub-folders" : "";
        const total = state.media.filter(item => inFolder(item, folder, recursive.checked)).length;
        if (!confirm(`Set ${total} item(s) in “${folder || "(no folder)"}” ${scope} to ${KIND_NAMES[kind.value]}?`)) return;
        const result = await api("folders/kind", {method: "POST",
          body: {folder, kind: kind.value, recursive: recursive.checked, remember: remember.checked}});
        status(result.ok ? `${result.data.updated} item(s) set to ${KIND_NAMES[kind.value]}.` : result.data.error, !result.ok);
        await refresh();
      });
      const actions = element("td", {className: "tv-actions"},
        button("SELECT", () => {
          members.forEach(item => selected.add(item.id));
          if (folder) {
            $("[data-library-folder]").value = folder;
            $("[data-library-recursive]").checked = true;
          }
          renderLibrary();
        }));
      if (folder) actions.append(button("RENAME", () => renameFolder(folder)));
      return element("tr", {}, name, element("td", {textContent: members.length}),
        element("td", {textContent: Object.entries(counts).map(([key, count]) => `${count} ${KIND_LABELS[key]}`).join(" · ")}),
        element("td", {className: "tv-folder-apply"}, kind,
          element("label", {className: "check", title: "Include sub-folders"}, recursive, " sub-folders"),
          element("label", {className: "check", title: "Give new files in this folder the same type"}, remember, " remember"), apply),
        actions);
    }));
  }

  async function renameFolder(folder) {
    const target = prompt(`Rename or move “${folder}” (and its sub-folders). Use / to nest; leave blank to move items out of any folder.`, folder);
    if (target === null || target.trim() === folder) return;
    const result = await api("folders/rename", {method: "POST", body: {from: folder, to: target}});
    status(result.ok ? `${result.data.updated} item(s) moved.` : result.data.error, !result.ok);
    if (result.ok && $("[data-library-folder]").value === folder) $("[data-library-folder]").value = "";
    await refresh();
  }

  /* Multi-select, filtering and sorting */
  const selected = new Set();
  let lastPicked = null;
  let sort = (() => { const [key, dir] = (store.get("stationnet.tv.admin.sort") || "").split(":"); return key ? {key, dir: Number(dir) || 1} : null; })();

  function visibleMedia() {
    const folder = $("[data-library-folder]").value;
    const recursive = $("[data-library-recursive]").checked;
    const search = $("[data-library-search]").value.trim().toLowerCase();
    const kind = $("[data-library-kind]").value;
    const content = $("[data-library-content]").value;
    const genre = $("[data-library-genre]").value;
    const usage = $("[data-library-usage]").value;
    const usageTest = {used: item => item.usage, unused: item => !item.usage, missing: item => item.missing,
      measuring: item => item.duration_s == null}[usage] || (() => true);
    const matches = (value, wanted) => !wanted || (wanted === "-" ? !value : value === wanted);
    const rows = state.media.filter(item => (!kind || item.kind === kind) && (!folder || inFolder(item, folder, recursive)) &&
      matches(item.content_type, content) && matches(item.genre, genre) && usageTest(item) &&
      (!search || `${item.title} ${item.folder} ${item.original_name} ${item.series}`.toLowerCase().includes(search)));
    if (sort) {
      rows.sort((a, b) => {
        const left = a[sort.key] ?? -1, right = b[sort.key] ?? -1;
        const order = typeof left === "string" ? left.localeCompare(right, undefined, {numeric: true}) : left - right;
        return order * sort.dir || a.title.localeCompare(b.title, undefined, {numeric: true});
      });
    }
    return rows;
  }

  /* Selected items in on-screen order, then any hidden by the current filter. */
  function selectedMedia() {
    const visible = visibleMedia();
    return visible.filter(item => selected.has(item.id))
      .concat(state.media.filter(item => selected.has(item.id) && !visible.includes(item)));
  }

  function renderSelectBar(rows) {
    const chosen = state.media.filter(item => selected.has(item.id));
    $("[data-select-bar]").hidden = !chosen.length;
    const hidden = chosen.length - rows.filter(item => selected.has(item.id)).length;
    $("[data-select-count]").textContent = `${chosen.length} SELECTED · ${duration(chosen.reduce((sum, item) => sum + (item.duration_s || 0), 0))} · ` +
      `${bytes(chosen.reduce((sum, item) => sum + item.size_bytes, 0))}${hidden ? ` (${hidden} not shown)` : ""}`;
    const all = $("[data-select-all]");
    const shown = rows.filter(item => selected.has(item.id)).length;
    all.checked = rows.length > 0 && shown === rows.length;
    all.indeterminate = shown > 0 && shown < rows.length;
  }

  function renderLibrary() {
    const folderSelect = $("[data-library-folder]");
    const chosen = folderSelect.value;
    folderSelect.replaceChildren(element("option", {value: "", textContent: "All folders"}),
      ...folders().map(name => element("option", {value: name, textContent: name})));
    folderSelect.value = folders().includes(chosen) ? chosen : "";
    $("[data-folder-list]").replaceChildren(...folders().map(name => element("option", {value: name})));
    $$("[data-sort]").forEach(header => {
      if (sort && header.dataset.sort === sort.key) header.dataset.dir = sort.dir > 0 ? "asc" : "desc";
      else delete header.dataset.dir;
    });
    const body = $("[data-library]");
    body.replaceChildren();
    const rows = visibleMedia();
    if (!rows.length) {
      body.append(element("tr", {}, element("td", {colSpan: 11, className: "muted",
        textContent: state.media.length ? "Nothing matches." : "The library is empty. Upload videos or scan the import folder."})));
    }
    rows.forEach((item, index) => {
      const check = element("input", {type: "checkbox", checked: selected.has(item.id), ariaLabel: `Select ${item.title}`});
      check.addEventListener("click", event => {
        const range = event.shiftKey && lastPicked !== null ? rows.slice(Math.min(lastPicked, index), Math.max(lastPicked, index) + 1) : [item];
        range.forEach(entry => (check.checked ? selected.add(entry.id) : selected.delete(entry.id)));
        lastPicked = index;
        renderLibrary();
      });
      const title = element("td", {}, element("strong", {textContent: item.title}));
      if (item.missing) title.append(" ", element("span", {className: "tv-badge is-error", textContent: "MISSING FILE"}));
      if (item.duration_s == null) title.append(" ", element("span", {className: "tv-badge", textContent: "MEASURING"}));
      body.append(element("tr", {className: selected.has(item.id) ? "is-selected" : ""},
        element("td", {className: "tv-check"}, check),
        title, element("td", {textContent: item.folder || "—"}),
        element("td", {textContent: item.kind === "program" ? (CONTENT_NAMES[item.content_type] || "PROG").toUpperCase() : KIND_NAMES[item.kind].toUpperCase()}),
        element("td", {textContent: item.genre || "—"}), element("td", {textContent: seriesLabel(item) || "—"}),
        element("td", {textContent: duration(item.duration_s)}), element("td", {textContent: bytes(item.size_bytes)}),
        element("td", {textContent: item.source === "import" ? "IMPORTED" : "UPLOADED"}),
        element("td", {textContent: item.usage ? `${item.usage} block${item.usage === 1 ? "" : "s"}` : "—"}),
        element("td", {className: "tv-actions"}, button("EDIT", () => openMedia(item)), button("DELETE", () => deleteMedia(item), "danger"))));
    });
    renderSelectBar(rows);
  }
  ["[data-library-search]", "[data-library-folder]", "[data-library-kind]", "[data-library-content]", "[data-library-genre]",
    "[data-library-usage]", "[data-library-recursive]"].forEach(selector =>
    $(selector).addEventListener("input", () => { lastPicked = null; renderLibrary(); }));
  $$("[data-sort]").forEach(header => header.addEventListener("click", () => {
    const key = header.dataset.sort;
    sort = sort && sort.key === key ? (sort.dir > 0 ? {key, dir: -1} : null) : {key, dir: 1};
    if (sort) store.set("stationnet.tv.admin.sort", `${sort.key}:${sort.dir}`); else store.remove("stationnet.tv.admin.sort");
    renderLibrary();
  }));
  $("[data-select-all]").addEventListener("click", event => {
    visibleMedia().forEach(item => (event.target.checked ? selected.add(item.id) : selected.delete(item.id)));
    renderLibrary();
  });
  $("[data-select-clear]").addEventListener("click", () => { selected.clear(); renderLibrary(); });

  async function bulkEdit(changes, label) {
    const ids = [...selected];
    const result = await api("media/bulk", {method: "POST", body: {ids, ...changes}});
    status(result.ok ? `${result.data.updated} item(s) ${label}.` : result.data.error, !result.ok);
    await refresh();
  }
  $("[data-bulk-kind-apply]").addEventListener("click", () => {
    const kind = $("[data-bulk-kind]").value;
    bulkEdit({kind}, `set to ${KIND_NAMES[kind]}`);
  });
  $("[data-bulk-folder-apply]").addEventListener("click", () => {
    const folder = $("[data-bulk-folder]").value.trim();
    bulkEdit({folder}, folder ? `moved to “${folder}”` : "moved out of their folders");
    $("[data-bulk-folder]").value = "";
  });
  const descriptionDialog = $("[data-description-dialog]");
  const descriptionForm = $("[data-description-form]");
  closeOn(descriptionDialog);
  $("[data-bulk-description]").addEventListener("click", () => {
    descriptionForm.reset();
    $("[data-description-title]").textContent = `Set description for ${selected.size} item(s)`;
    descriptionDialog.showModal();
  });
  descriptionForm.addEventListener("submit", async event => {
    event.preventDefault();
    descriptionDialog.close();
    await bulkEdit({description: descriptionForm.description.value}, "updated");
  });
  const metaDialog = $("[data-meta-dialog]");
  const metaForm = $("[data-meta-form]");
  closeOn(metaDialog);
  $("[data-bulk-meta]").addEventListener("click", () => {
    metaForm.reset();
    const chosen = state.media.filter(item => selected.has(item.id));
    const common = key => chosen.every(item => item[key] === chosen[0][key]) ? chosen[0][key] ?? "" : "";
    ["content_type", "genre", "series", "season"].forEach(key => { metaForm[key].value = common(key); });
    $("[data-meta-title]").textContent = `Categorise ${chosen.length} item(s)`;
    metaDialog.showModal();
  });
  [["content_type", "set_content_type"], ["genre", "set_genre"], ["series", "set_series"], ["season", "set_season"]].forEach(([field, flag]) =>
    metaForm[field].addEventListener("input", () => { metaForm[flag].checked = true; }));
  metaForm.addEventListener("submit", async event => {
    event.preventDefault();
    const changes = {};
    ["content_type", "genre", "series", "season"].forEach(key => { if (metaForm[`set_${key}`].checked) changes[key] = metaForm[key].value; });
    if (metaForm.autonumber.checked) changes.autonumber = true;
    if (!Object.keys(changes).length) { metaDialog.close(); return; }
    metaDialog.close();
    await bulkEdit(changes, "categorised");
  });
  $("[data-bulk-delete]").addEventListener("click", async () => {
    const chosen = state.media.filter(item => selected.has(item.id));
    const uploads = chosen.filter(item => item.source === "upload").length;
    const scheduled = chosen.filter(item => item.usage).length;
    const notes = [uploads ? `${uploads} uploaded file(s) will be deleted from disk.` : "",
      chosen.length - uploads ? `${chosen.length - uploads} imported original(s) stay in the import folder.` : "",
      scheduled ? `${scheduled} scheduled item(s) will be skipped.` : ""].filter(Boolean).join("\n");
    if (!confirm(`Remove ${chosen.length} item(s) from the library?\n\n${notes}`)) return;
    const result = await api("media/bulk-delete", {method: "POST", body: {ids: chosen.map(item => item.id)}});
    if (!result.ok) { status(result.data.error, true); return; }
    const {deleted, blocked, failed} = result.data;
    status(`${deleted} item(s) removed.${blocked.length ? ` ${blocked.length} still scheduled were kept.` : ""}${failed.length ? ` ${failed.length} file(s) could not be deleted.` : ""}`,
      Boolean(blocked.length || failed.length));
    if (blocked.length) alert(`These are still scheduled and were kept:\n\n${blocked.map(entry => `${entry.title}: ${entry.usage.join(", ")}`).join("\n")}`);
    selected.clear();
    blocked.forEach(entry => selected.add(entry.id));
    await refresh();
  });
  $("[data-bulk-block]").addEventListener("click", () => {
    const chosen = selectedMedia();
    const ready = chosen.filter(item => item.duration_s);
    if (!ready.length) { status("None of the selected items has a known length yet.", true); return; }
    if (ready.length < chosen.length) status(`${chosen.length - ready.length} item(s) without a known length were left out.`);
    showTab("schedule");
    openBlock(null, {items: ready.map(item => ({media_id: item.id, title_override: ""}))});
  });

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
  function syncMediaKind() {
    mediaDialog.querySelectorAll("[data-program-only]").forEach(node => { node.hidden = mediaForm.kind.value !== "program"; });
  }
  mediaForm.kind.addEventListener("change", syncMediaKind);
  function openMedia(item) {
    editingMedia = item;
    mediaForm.title.value = item.title;
    mediaForm.description.value = item.description;
    mediaForm.folder.value = item.folder;
    mediaForm.kind.value = item.kind;
    ["content_type", "genre", "series", "season", "episode"].forEach(key => { mediaForm[key].value = item[key] ?? ""; });
    syncMediaKind();
    $("[data-media-facts]").textContent = [item.original_name, item.width ? `${item.width}×${item.height}` : "",
      duration(item.duration_s), bytes(item.size_bytes), item.rel_path].filter(Boolean).join(" · ");
    $("[data-media-preview]").src = item.url;
    mediaDialog.showModal();
  }
  mediaForm.addEventListener("submit", async event => {
    event.preventDefault();
    const result = await api(`media/${editingMedia.id}`, {method: "POST", body: {title: mediaForm.title.value,
      description: mediaForm.description.value, folder: mediaForm.folder.value, kind: mediaForm.kind.value,
      content_type: mediaForm.content_type.value, genre: mediaForm.genre.value, series: mediaForm.series.value,
      season: mediaForm.season.value, episode: mediaForm.episode.value}});
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
      $("[data-block-select-bar]").hidden = true;
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
    const ids = new Set(day.blocks.map(block => block.id));
    [...selectedBlocks].forEach(id => { if (!ids.has(id)) selectedBlocks.delete(id); });
    const hidden = $("[data-auto-hidden]");
    hidden.hidden = !day.auto_hidden;
    hidden.textContent = `${day.auto_hidden} generated block(s) on other days are not listed. Pick a day to see its generated entries.`;
    renderBlocks();
  }

  const selectedBlocks = new Set();

  function renderBlocks() {
    const blocks = $("[data-blocks]");
    blocks.replaceChildren(...day.blocks.map(block => {
      const airs = block.kind === "once" ? block.start_date :
        WEEKDAYS.filter((_, position) => block.weekdays & (1 << position)).join(" ") +
        (block.valid_from || block.valid_until ? ` (${block.valid_from || "…"} → ${block.valid_until || "…"})` : "");
      const runs = block.loop_until ? `repeats until ${block.loop_until}` :
        `${duration(block.total_duration)} → ${clockFromMinutes(toMinutes(block.start_time) + block.total_duration / 60)}`;
      const check = element("input", {type: "checkbox", checked: selectedBlocks.has(block.id), ariaLabel: `Select ${block.title}`});
      check.addEventListener("change", () => { (check.checked ? selectedBlocks.add(block.id) : selectedBlocks.delete(block.id)); renderBlocks(); });
      const row = element("tr", {className: `${block.airs_on_day ? "is-today" : ""} ${selectedBlocks.has(block.id) ? "is-selected" : ""}`},
        element("td", {className: "tv-check"}, check),
        element("td", {}, element("strong", {textContent: block.title}), block.enabled ? "" : element("span", {className: "tv-badge", textContent: "DISABLED"}),
          block.origin === "auto" ? element("span", {className: "tv-badge is-auto", textContent: "AUTO"}) : "",
          block.locked ? element("span", {className: "tv-badge", textContent: "LOCKED"}) : ""),
        element("td", {textContent: airs}), element("td", {textContent: block.start_time}), element("td", {textContent: runs}),
        element("td", {textContent: block.items.length}),
        element("td", {className: "tv-actions"},
          block.origin === "auto" ? button(block.locked ? "UNLOCK" : "LOCK", async () => {
            const result = await api(`blocks/${block.id}/lock`, {method: "POST", body: {locked: !block.locked}});
            status(result.ok ? `“${block.title}” ${block.locked ? "unlocked" : "locked"}.` : result.data.error, !result.ok);
            await refresh();
          }) : "",
          button("EDIT", () => openBlock(block)),
          button("DUPLICATE", () => openBlock(block, {duplicate: true})), button("DELETE", () => deleteBlock(block), "danger")));
      return row;
    }));
    if (!day.blocks.length) blocks.append(element("tr", {}, element("td", {colSpan: 7, className: "muted", textContent: "No blocks yet. Add one to put programmes on air."})));
    $("[data-block-select-bar]").hidden = !selectedBlocks.size;
    $("[data-block-select-count]").textContent = `${selectedBlocks.size} BLOCK(S) SELECTED`;
    const all = $("[data-block-select-all]");
    all.checked = day.blocks.length > 0 && selectedBlocks.size === day.blocks.length;
    all.indeterminate = selectedBlocks.size > 0 && selectedBlocks.size < day.blocks.length;
  }

  $("[data-block-select-all]").addEventListener("click", event => {
    if (!day) return;
    day.blocks.forEach(block => (event.target.checked ? selectedBlocks.add(block.id) : selectedBlocks.delete(block.id)));
    renderBlocks();
  });
  $("[data-block-select-clear]").addEventListener("click", () => { selectedBlocks.clear(); renderBlocks(); });
  $$("[data-block-bulk]").forEach(node => node.addEventListener("click", async () => {
    const action = node.dataset.blockBulk;
    if (action === "delete" && !confirm(`Delete ${selectedBlocks.size} programming block(s)? This cannot be undone.`)) return;
    const result = await api("blocks/bulk", {method: "POST", body: {ids: [...selectedBlocks], action}});
    const done = {delete: "deleted", enable: "enabled", disable: "disabled", lock: "locked", unlock: "unlocked"}[action];
    status(result.ok ? `${result.data.updated} block(s) ${done}.` : result.data.error, !result.ok);
    if (result.ok && action === "delete") selectedBlocks.clear();
    await refresh();
  }));

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
  blockForm.start_date.addEventListener("input", renderItems);
  blockForm.querySelectorAll("[name=weekday]").forEach(node => node.addEventListener("change", renderItems));

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
    renderOverlap(start, elapsed);
  }

  /* [start, end) in minutes from the viewed day's midnight; loops past midnight end on the next day. */
  function spanMinutes(startTime, loopUntil, seconds) {
    const begin = toMinutes(startTime);
    let end = loopUntil ? toMinutes(loopUntil) : begin + Math.ceil(seconds / 60);
    if (loopUntil && end <= begin) end += 1440;
    return [begin, end];
  }

  /* Warn before saving a block that would cut into another block airing on the viewed day. */
  function renderOverlap(start, elapsed) {
    const warning = $("[data-block-overlap]");
    warning.hidden = true;
    if (!day || start == null || !items.length) return;
    const kind = blockForm.querySelector("[name=kind]:checked").value;
    const weekday = (new Date(`${day.date}T12:00:00Z`).getUTCDay() + 6) % 7;
    const airsToday = kind === "once" ? blockForm.start_date.value === day.date
      : blockForm.querySelector(`[name=weekday][value="${weekday}"]`).checked;
    if (!airsToday) return;
    const loop = blockForm.loop.checked && blockForm.loop_until.value ? blockForm.loop_until.value : null;
    const [begin, end] = spanMinutes(blockForm.start_time.value, loop, elapsed);
    const clashes = day.blocks.filter(block => block.airs_on_day && !(editingBlock && block.id === editingBlock.id)).filter(block => {
      const [otherBegin, otherEnd] = spanMinutes(block.start_time, block.loop_until, block.total_duration);
      return otherBegin < end && otherEnd > begin;
    });
    if (!clashes.length) return;
    warning.hidden = false;
    warning.textContent = `Overlaps ${clashes.map(block => `“${block.title}” (${block.start_time})`).join(", ")} on ${day.date}. ` +
      "One-off blocks override weekly ones, otherwise the later-starting block takes over the overlap.";
  }

  function renderPicker() {
    const search = $("[data-item-search]").value.trim().toLowerCase();
    const select = $("[data-item-select]");
    mediaOptions(select, {filter: item => item.duration_s && (!search || `${item.title} ${item.folder}`.toLowerCase().includes(search))});
    const folderSelect = $("[data-item-folder]");
    folderSelect.replaceChildren(...folders().map(name => element("option", {value: name, textContent: name})));
    folderSelect.disabled = !folders().length;
    mediaOptions($("[data-interleave-select]"), {filter: item => item.duration_s && item.kind !== "program", preferKinds: ["bumper", "ident"]});
  }
  $("[data-item-search]").addEventListener("input", renderPicker);
  $("[data-item-add]").addEventListener("click", () => {
    const value = Number($("[data-item-select]").value);
    if (value) { items.push({media_id: value, title_override: ""}); renderItems(); }
  });
  $("[data-item-add-folder]").addEventListener("click", () => {
    const folder = $("[data-item-folder]").value;
    const kind = $("[data-item-folder-kind]").value;
    const recursive = $("[data-item-folder-recursive]").checked;
    const found = state.media.filter(item => inFolder(item, folder, recursive) && item.duration_s && (!kind || item.kind === kind))
      .sort((a, b) => a.folder.localeCompare(b.folder, undefined, {numeric: true}) ||
        a.original_name.localeCompare(b.original_name, undefined, {numeric: true}));
    found.forEach(item => items.push({media_id: item.id, title_override: ""}));
    if (!found.length) status("No measured items of that type in the folder.");
    renderItems();
  });
  $("[data-interleave]").addEventListener("click", () => {
    const clip = Number($("[data-interleave-select]").value);
    if (!clip) { status("Mark some library items as idents or bumpers first.", true); return; }
    const result = [];
    items.forEach((item, position) => {
      result.push(item);
      const next = items[position + 1];
      if (next && item.media_id !== clip && next.media_id !== clip) result.push({media_id: clip, title_override: ""});
    });
    items = result;
    renderItems();
  });
  $("[data-items-shuffle]").addEventListener("click", () => {
    for (let index = items.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(Math.random() * (index + 1));
      [items[index], items[swap]] = [items[swap], items[index]];
    }
    renderItems();
  });
  $("[data-items-clear]").addEventListener("click", () => {
    if (items.length && confirm(`Remove all ${items.length} programme(s) from this block?`)) { items = []; renderItems(); }
  });

  $("[data-start-after]").addEventListener("click", () => {
    if (!day) return;
    let latest = null;
    for (const block of day.blocks) {
      if (!block.airs_on_day || (editingBlock && block.id === editingBlock.id)) continue;
      const end = spanMinutes(block.start_time, block.loop_until, block.total_duration)[1];
      if (latest === null || end > latest) latest = end;
    }
    if (latest === null) { status("No other block airs on this day; choose a start time."); return; }
    if (latest >= 1440) status("The previous block runs past midnight; the start time wraps to the next day.");
    blockForm.start_time.value = clockFromMinutes(latest % 1440).slice(0, 5);
    renderItems();
  });

  /* With {duplicate: true} the block's settings prefill a new block; {items} preloads programmes. */
  function openBlock(block = null, {duplicate = false, items: preset = null} = {}) {
    if (!channelSelect.value) { status("Create a channel first.", true); return; }
    editingBlock = duplicate ? null : block;
    blockForm.reset();
    $("[data-block-dialog-title]").textContent = duplicate ? "Duplicate programming block" : block ? "Edit programming block" : "New programming block";
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
    items = preset || (block ? block.items.map(item => ({media_id: item.media_id, title_override: item.title_override || ""})) : []);
    $("[data-block-delete]").hidden = !editingBlock;
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

  /* Auto scheduler ----------------------------------------------------- */

  const autoChannel = $("[data-auto-channel]");
  const autoForm = $("[data-auto-form]");
  const autoStart = $("[data-auto-start]");
  const DAY_BITS = WEEKDAYS.map((_, position) => 1 << position);
  const MINUTE_PX = 1.1;
  let auto = null;
  let autoLoaded = null;
  let autoDirty = false;
  let rules = [];
  let themes = {};

  function renderAutoChannels() {
    const chosen = autoChannel.value || store.get("stationnet.tv.admin.autoChannel") || $("[data-schedule-channel]").value;
    autoChannel.replaceChildren(...state.channels.map(channel =>
      element("option", {value: channel.id, textContent: `${channel.number} ${channel.name}`})));
    if (state.channels.some(channel => String(channel.id) === String(chosen))) autoChannel.value = chosen;
  }

  async function loadAuto() {
    if (!autoChannel.value) { auto = null; renderAuto(); return; }
    store.set("stationnet.tv.admin.autoChannel", autoChannel.value);
    const result = await api(`auto/${autoChannel.value}`);
    if (!result.ok) { status(result.data.error, true); return; }
    // Keep unsaved settings when the rest of the page refreshes.
    const keepForm = autoDirty && autoLoaded === autoChannel.value;
    auto = result.data;
    if (!keepForm) {
      autoLoaded = autoChannel.value;
      autoDirty = false;
      rules = auto.config.rules.map(rule => ({...rule, types: [...rule.types], genres: [...rule.genres]}));
      themes = JSON.parse(JSON.stringify(auto.config.themes));
    }
    renderAuto(keepForm);
  }
  autoChannel.addEventListener("change", () => { autoDirty = false; loadAuto(); });
  autoForm.addEventListener("input", () => { autoDirty = true; renderMixShares(); });

  function renderAuto(keepForm = false) {
    $("[data-auto-zone]").textContent = zone();
    const library = $("[data-auto-library]");
    if (!auto) {
      library.replaceChildren(element("p", {className: "muted", textContent: "Create a channel first."}));
      $("[data-auto-draft]").hidden = true;
      return;
    }
    renderReadiness();
    if (!keepForm) {
      ["on_air_from", "on_air_until", "commercials", "reruns", "episode_order", "days"].forEach(key => { autoForm[key].value = auto.config[key]; });
      renderMix();
      renderRules();
      renderThemes();
    }
    if (!autoStart.value || autoStart.value < auto.today) autoStart.value = auto.today;
    renderDraft();
  }

  function renderReadiness() {
    const {types, clips, series, unmeasured} = auto.library;
    const programmes = Object.values(types).reduce((sum, count) => sum + count, 0);
    const lines = [element("p", {}, element("strong", {textContent: `${programmes} programme(s) ready`}),
      programmes ? ` · ${Object.entries(types).map(([key, count]) => `${count} ${CONTENT_NAMES[key] || key}`).join(" · ")}` : "")];
    lines.push(element("p", {textContent: `Breaks: ${clips.commercial || 0} commercial(s) · ${clips.bumper || 0} bumper(s) · ${clips.ident || 0} ident(s)`}));
    if (series.length) {
      lines.push(element("p", {textContent: `Series: ${series.slice(0, 8).map(entry => `${entry.name} (${entry.episodes})`).join(" · ")}` +
        (series.length > 8 ? ` · +${series.length - 8} more` : "")}));
    }
    const hints = [];
    if (!programmes) hints.push("Upload programmes and give them a measured length.");
    if (types.other && types.other === programmes) hints.push("Set a content type on your programmes (CATEGORISE in the library) so the mix and rules can tell them apart.");
    if (!clips.commercial && !clips.bumper && !clips.ident) hints.push("Mark some short clips as commercials, bumpers or idents to fill breaks between programmes.");
    if (unmeasured) hints.push(`${unmeasured} item(s) are still being measured and are not scheduled yet.`);
    lines.push(...hints.map(text => element("p", {className: "flash warning", textContent: text})));
    $("[data-auto-library]").replaceChildren(...lines);
  }

  function renderMix() {
    const holder = $("[data-auto-mix]");
    holder.replaceChildren(...auto.content_types.map(key => {
      const available = auto.library.types[key] || 0;
      const range = element("input", {type: "range", min: 0, max: 100, step: 5, name: `mix_${key}`, value: auto.config.mix[key] ?? 0,
        ariaLabel: `${CONTENT_NAMES[key]} weight`});
      const share = element("output");
      share.dataset.share = key;
      return element("label", {className: `tv-auto-mix-row${available ? "" : " is-empty"}`},
        element("span", {textContent: CONTENT_NAMES[key]}), range, share,
        element("small", {className: "muted", textContent: available ? `${available}` : "none"}));
    }));
    renderMixShares();
  }

  function renderMixShares() {
    if (!auto) return;
    const weights = Object.fromEntries(auto.content_types.map(key =>
      [key, auto.library.types[key] ? Number(autoForm[`mix_${key}`].value) : 0]));
    const total = Object.values(weights).reduce((sum, value) => sum + value, 0);
    $$("[data-share]").forEach(node => {
      node.textContent = total && weights[node.dataset.share] ? `${Math.round(weights[node.dataset.share] / total * 100)}%` : "—";
    });
  }

  function dayChecks(mask, onChange) {
    return element("span", {className: "tv-auto-days"}, ...WEEKDAYS.map((label, position) => {
      const box = element("input", {type: "checkbox", checked: Boolean(mask & DAY_BITS[position]), ariaLabel: label});
      box.addEventListener("change", () => onChange(position, box.checked));
      return element("label", {className: "check", title: label}, box, label[0]);
    }));
  }

  function typeChecks(chosen, onChange) {
    return element("span", {className: "tv-auto-chips"}, ...auto.content_types.map(key => {
      const box = element("input", {type: "checkbox", checked: chosen.includes(key)});
      box.addEventListener("change", () => onChange(key, box.checked));
      return element("label", {className: "check"}, box, CONTENT_NAMES[key]);
    }));
  }

  function renderRules() {
    const body = $("[data-auto-rules]");
    const touched = () => { autoDirty = true; };
    body.replaceChildren(...rules.map((rule, index) => {
      const from = element("input", {type: "time", value: rule.from, step: 60, ariaLabel: "From"});
      const until = element("input", {type: "time", value: rule.until, step: 60, ariaLabel: "Until"});
      from.addEventListener("input", () => { rule.from = from.value; touched(); });
      until.addEventListener("input", () => { rule.until = until.value; touched(); });
      const mode = element("select", {ariaLabel: "Mode"}, element("option", {value: "prefer", textContent: "Prefer"}),
        element("option", {value: "only", textContent: "Only"}));
      mode.value = rule.mode;
      mode.addEventListener("change", () => { rule.mode = mode.value; touched(); });
      const genreInput = element("input", {value: rule.genres.join(", "), placeholder: "e.g. Horror, Comedy", ariaLabel: "Genres"});
      genreInput.addEventListener("input", () => { rule.genres = genreInput.value.split(",").map(text => text.trim()).filter(Boolean); touched(); });
      return element("tr", {},
        element("td", {}, dayChecks(rule.days, (position, on) => { rule.days = on ? rule.days | DAY_BITS[position] : rule.days & ~DAY_BITS[position]; touched(); })),
        element("td", {}, from), element("td", {}, until), element("td", {}, mode),
        element("td", {}, typeChecks(rule.types, (key, on) => {
          rule.types = on ? [...rule.types, key] : rule.types.filter(value => value !== key); touched();
        })),
        element("td", {}, genreInput),
        element("td", {className: "tv-actions"}, button("✕", () => { rules.splice(index, 1); touched(); renderRules(); }, "danger")));
    }));
    if (!rules.length) body.append(element("tr", {}, element("td", {colSpan: 7, className: "muted", textContent: "No rules: any programme may air at any hour."})));
  }
  $("[data-auto-rule-add]").addEventListener("click", () => {
    rules.push({days: 127, from: "20:00", until: "23:00", types: [], genres: [], mode: "prefer"});
    autoDirty = true;
    renderRules();
  });
  $("[data-auto-rule-defaults]").addEventListener("click", () => {
    if (!auto || !confirm("Replace the time-of-day rules with the defaults?")) return;
    rules = auto.defaults.rules.map(rule => ({...rule, types: [...rule.types], genres: [...rule.genres]}));
    autoDirty = true;
    renderRules();
  });

  function renderThemes() {
    $("[data-auto-themes]").replaceChildren(...WEEKDAYS.map((label, position) => {
      const theme = themes[position] || {types: [], genres: []};
      const type = element("select", {ariaLabel: `${label} content`}, element("option", {value: "", textContent: "No theme"}),
        ...auto.content_types.map(key => element("option", {value: key, textContent: CONTENT_NAMES[key]})));
      type.value = theme.types[0] || "";
      const genre = element("input", {value: theme.genres[0] || "", placeholder: "Any genre", ariaLabel: `${label} genre`});
      genre.setAttribute("list", "tv-genre-list");
      const update = () => {
        autoDirty = true;
        if (type.value || genre.value.trim()) themes[position] = {types: type.value ? [type.value] : [], genres: genre.value.trim() ? [genre.value.trim()] : []};
        else delete themes[position];
      };
      type.addEventListener("change", update);
      genre.addEventListener("input", update);
      return element("tr", {}, element("td", {textContent: label}), element("td", {}, type), element("td", {}, genre));
    }));
  }

  function autoConfig() {
    return {on_air_from: autoForm.on_air_from.value, on_air_until: autoForm.on_air_until.value,
      commercials: autoForm.commercials.value, reruns: autoForm.reruns.value,
      episode_order: autoForm.episode_order.value, days: Number(autoForm.days.value),
      mix: Object.fromEntries(auto.content_types.map(key => [key, Number(autoForm[`mix_${key}`].value)])),
      rules: rules.map(rule => ({...rule})), themes};
  }

  async function saveAuto() {
    if (!auto) return false;
    const result = await api(`auto/${autoChannel.value}/config`, {method: "POST", body: autoConfig()});
    if (!result.ok) { status(result.data.error, true); return false; }
    autoDirty = false;
    auto.config = result.data.config;
    return true;
  }
  $("[data-auto-save]").addEventListener("click", async () => { if (await saveAuto()) status("Auto scheduler settings saved."); });

  async function generate() {
    if (!auto || !(await saveAuto())) return;
    status("Generating schedule…");
    const result = await api(`auto/${autoChannel.value}/generate`, {method: "POST",
      body: {start_date: autoStart.value, days: Number(autoForm.days.value)}});
    if (!result.ok) { status(result.data.error, true); return; }
    auto.draft = result.data.draft;
    status(`Preview ready: ${auto.draft.slots.length} entries. Review it, then publish.`);
    renderDraft();
    $("[data-auto-draft]").scrollIntoView({behavior: "smooth", block: "start"});
  }
  $("[data-auto-generate]").addEventListener("click", generate);
  $("[data-auto-regenerate]").addEventListener("click", generate);

  $("[data-auto-publish]").addEventListener("click", async () => {
    const draft = auto && auto.draft;
    if (!draft) return;
    const replacing = draft.replaced ? `\n\n${draft.replaced} earlier generated entr${draft.replaced === 1 ? "y" : "ies"} in this range will be replaced. Manual and locked programming is kept.` : "";
    if (!confirm(`Publish ${draft.slots.length} entries to the channel?${replacing}`)) return;
    const result = await api(`auto/${autoChannel.value}/publish`, {method: "POST", body: {}});
    if (!result.ok) { alert(result.data.error); return; }
    status(`Published ${result.data.published} entries${result.data.replaced ? `, replacing ${result.data.replaced}` : ""}.`);
    await refresh();
  });
  $("[data-auto-discard]").addEventListener("click", async () => {
    if (!confirm("Discard this preview?")) return;
    await api(`auto/${autoChannel.value}/discard`, {method: "POST", body: {}});
    auto.draft = null;
    renderDraft();
  });
  $("[data-auto-clear]").addEventListener("click", async () => {
    if (!auto) return;
    const days = Number(autoForm.days.value) || 7;
    if (!confirm(`Remove unlocked generated programming from ${autoStart.value} for ${days} day(s)? Manual and locked entries stay, and anything already on air is kept.`)) return;
    const result = await api(`auto/${autoChannel.value}/clear`, {method: "POST", body: {start_date: autoStart.value, days}});
    status(result.ok ? `${result.data.removed} generated entr${result.data.removed === 1 ? "y" : "ies"} removed.` : result.data.error, !result.ok);
    await refresh();
  });

  /* Station-zone calendar date and minute of day for an instant. */
  function zoneParts(ms) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {timeZone: zone(), year: "numeric", month: "2-digit",
      day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23"}).formatToParts(new Date(ms)).map(part => [part.type, part.value]));
    return {date: `${parts.year}-${parts.month}-${parts.day}`, minutes: Number(parts.hour) * 60 + Number(parts.minute)};
  }

  /* An entry cut into per-day pieces, so programmes crossing midnight show in both columns. */
  function pieces(entry) {
    const start = zoneParts(Date.parse(entry.start)), last = zoneParts(Date.parse(entry.end) - 1);
    const endMinutes = last.minutes + 1;
    if (start.date === last.date) return [{date: start.date, from: start.minutes, to: endMinutes, entry}];
    return [{date: start.date, from: start.minutes, to: 1440, entry, continues: true}, {date: last.date, from: 0, to: endMinutes, entry, continued: true}];
  }

  function renderDraft() {
    const section = $("[data-auto-draft]");
    const draft = auto && auto.draft;
    section.hidden = !draft;
    if (!draft) return;
    const lastDay = new Date(`${draft.start_date}T12:00:00Z`);
    lastDay.setUTCDate(lastDay.getUTCDate() + draft.days - 1);
    $("[data-auto-draft-meta]").textContent = `${draft.start_date} → ${lastDay.toISOString().slice(0, 10)} · ${draft.slots.length} new entries · ` +
      `${draft.fixed.length} fixed · times in ${draft.timezone}`;
    const warnings = draft.warnings.map(text => element("li", {textContent: text}));
    if (draft.stale) warnings.unshift(element("li", {className: "is-error", textContent: "The library or schedule changed after this preview was generated. Regenerate before publishing."}));
    $("[data-auto-warnings]").replaceChildren(...warnings);
    $("[data-auto-publish]").disabled = draft.stale || !draft.slots.length;

    const entries = draft.slots.map(slot => ({...slot, status: "new"})).concat(draft.fixed.map(entry => ({...entry, fixed: true})));
    const all = entries.flatMap(pieces);
    const days = [];
    for (let offset = 0; offset < draft.days; offset += 1) {
      const value = new Date(`${draft.start_date}T12:00:00Z`);
      value.setUTCDate(value.getUTCDate() + offset);
      days.push(value.toISOString().slice(0, 10));
    }
    const inRange = new Set(days);
    const extra = [...new Set(all.map(piece => piece.date))].filter(value => !inRange.has(value) && value > days[days.length - 1]);
    days.push(...extra.sort());
    const shown = all.filter(piece => days.includes(piece.date));
    const top = shown.length ? Math.floor(Math.min(...shown.map(piece => piece.from)) / 60) * 60 : 0;
    const bottom = shown.length ? Math.ceil(Math.max(...shown.map(piece => piece.to)) / 60) * 60 : 1440;
    const height = `${(bottom - top) * MINUTE_PX}px`;
    const place = (from, to) => `top:${(from - top) * MINUTE_PX}px;height:${Math.max(2, (to - from) * MINUTE_PX)}px`;

    const hours = element("div", {className: "tv-auto-hours", style: `height:${height}`});
    for (let minute = top; minute < bottom; minute += 60) {
      hours.append(element("span", {style: `top:${(minute - top) * MINUTE_PX}px`, textContent: clockFromMinutes(minute)}));
    }
    const dayFormat = new Intl.DateTimeFormat(undefined, {weekday: "short", day: "numeric", month: "short", timeZone: "UTC"});
    const columns = days.map(value => {
      const track = element("div", {className: "tv-auto-track", style: `height:${height}`});
      shown.filter(piece => piece.date === value).forEach(piece => {
        const entry = piece.entry;
        const type = entry.fixed ? "fixed" : entry.interlude ? "break" : entry.content_type || "other";
        const startText = zoneTime(Date.parse(entry.start));
        const label = `${startText} ${entry.title}${entry.rerun ? " (R)" : ""}`;
        const cell = element("button", {type: "button", className: `tv-auto-cell is-${type}${entry.fixed ? ` is-${entry.status}` : ""}`,
          style: place(piece.from, piece.to), title: `${startText}–${zoneTime(Date.parse(entry.end))} ${entry.title}`},
          element("span", {textContent: piece.continued ? `… ${entry.title}` : label}));
        cell.addEventListener("click", () => showDetail(entry));
        track.append(cell);
      });
      return element("div", {className: "tv-auto-day"},
        element("div", {className: "tv-auto-dayhead", textContent: dayFormat.format(new Date(`${value}T12:00:00Z`))}), track);
    });
    const guide = $("[data-auto-guide]");
    guide.style.setProperty("--days", days.length);
    guide.replaceChildren(element("div", {className: "tv-auto-day tv-auto-hourcol"}, element("div", {className: "tv-auto-dayhead"}), hours), ...columns);
    const used = [...new Set(draft.slots.map(slot => slot.interlude ? "break" : slot.content_type || "other"))];
    $("[data-auto-legend]").replaceChildren(...used.map(type => element("span", {className: `tv-auto-cell is-${type}`,
      textContent: type === "break" ? "Station break" : CONTENT_NAMES[type] || type})),
      ...(draft.fixed.length ? [element("span", {className: "tv-auto-cell is-fixed is-manual", textContent: "Manual / locked (kept)"})] : []));
    $("[data-auto-detail]").hidden = true;
  }

  function showDetail(entry) {
    const detail = $("[data-auto-detail]");
    detail.hidden = false;
    const when = `${zoneTime(Date.parse(entry.start))}–${zoneTime(Date.parse(entry.end))}`;
    if (entry.fixed) {
      detail.replaceChildren(element("p", {className: "eyebrow", textContent: entry.status === "locked" ? "LOCKED ENTRY (KEPT)" :
        entry.status === "auto" ? "EARLIER GENERATED ENTRY (ALREADY ON AIR)" : "MANUAL BLOCK (KEPT)"}),
        element("p", {}, element("strong", {textContent: entry.title}), ` · ${when}`),
        element("p", {className: "muted", textContent: "Edit this in the SCHEDULE tab."}));
      return;
    }
    const facts = [entry.interlude ? "Station break" : CONTENT_NAMES[entry.content_type] || entry.content_type, entry.genre,
      entry.rerun ? "Encore (rerun)" : ""].filter(Boolean).join(" · ");
    detail.replaceChildren(element("p", {className: "eyebrow", textContent: "PROPOSED ENTRY"}),
      element("p", {}, element("strong", {textContent: entry.title}), ` · ${when}${facts ? ` · ${facts}` : ""}`),
      element("div", {className: "app-table-wrap"}, element("table", {className: "tv-admin-table"},
        element("thead", {}, element("tr", {}, element("th", {textContent: "ITEM"}), element("th", {textContent: "TYPE"}), element("th", {textContent: "LENGTH"}))),
        element("tbody", {}, ...entry.items.map(item => element("tr", {}, element("td", {textContent: item.title}),
          element("td", {textContent: KIND_NAMES[item.kind] || item.kind}), element("td", {textContent: duration(item.duration)})))))),
      element("p", {className: "muted", textContent: "After publishing, edit, move, replace or lock entries in the SCHEDULE tab. The last break item is cut at the slot end."}));
  }

  showTab(store.get("stationnet.tv.admin.tab") || "schedule");
  refresh();
})();
