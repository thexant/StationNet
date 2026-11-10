// Sysop Settings: category routing, inline save feedback, unsaved-change
// protection, and settings search. Without this script every category is
// shown stacked and the sidebar links work as ordinary anchors.
(() => {
  "use strict";
  const shell = document.querySelector("[data-settings-shell]");
  if (!shell) return;
  shell.classList.add("settings-js");

  const panes = [...shell.querySelectorAll("[data-settings-pane]")];
  const paneLinks = [...shell.querySelectorAll("[data-nav-pane]")];
  const itemLinks = [...shell.querySelectorAll("[data-nav-item]")];
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const scrollBehavior = reduceMotion ? "auto" : "smooth";

  // ---- Category routing -------------------------------------------------
  const elementForHash = hash => {
    if (!hash || hash.length < 2) return null;
    let id;
    try { id = decodeURIComponent(hash.slice(1)); } catch { return null; }
    const element = document.getElementById(id);
    return element && shell.contains(element) ? element : null;
  };

  const showPane = pane => {
    for (const candidate of panes) candidate.hidden = candidate !== pane;
    for (const link of paneLinks) {
      const active = link.dataset.navPane === pane.id;
      link.parentElement.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    }
  };

  const markItem = id => {
    for (const link of itemLinks) {
      if (link.dataset.navItem === id) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    }
  };

  const openAncestors = element => {
    for (let node = element; node && node !== shell; node = node.parentElement) {
      if (node.tagName === "DETAILS") node.open = true;
    }
  };

  const highlight = card => {
    card.classList.remove("is-target");
    void card.offsetWidth; // restart the highlight animation
    card.classList.add("is-target");
  };

  // Returns the card a hash points into, if any.
  const route = ({ initial = false, focus = false } = {}) => {
    const target = elementForHash(location.hash);
    const pane = target ? target.closest("[data-settings-pane]") : null;
    if (!pane) {
      showPane(panes[0]);
      markItem(null);
      return null;
    }
    showPane(pane);
    if (target === pane) {
      markItem(null);
      if (!initial) shell.scrollIntoView({ block: "start", behavior: scrollBehavior });
      if (focus) pane.querySelector(".settings-pane-header h2")?.focus({ preventScroll: true });
      return null;
    }
    openAncestors(target);
    const card = target.closest("[data-settings-card]");
    markItem(card ? card.id : null);
    // Let the pane render before scrolling to the target inside it.
    requestAnimationFrame(() => {
      target.scrollIntoView({ block: "start", behavior: initial ? "auto" : scrollBehavior });
      if (card) highlight(card);
      if (focus && card) card.querySelector(".settings-card-title")?.focus({ preventScroll: true });
    });
    return card;
  };

  let navigatingByClick = false;
  shell.addEventListener("click", event => {
    const link = event.target.closest('a[href^="#"]');
    if (!link || !shell.contains(link)) return;
    navigatingByClick = true;
    // Re-route even when the hash is unchanged (no hashchange fires).
    if (link.getAttribute("href") === location.hash) {
      event.preventDefault();
      route({ focus: true });
      navigatingByClick = false;
    }
  });
  window.addEventListener("hashchange", () => {
    route({ focus: navigatingByClick });
    navigatingByClick = false;
  });

  // ---- Inline save feedback ---------------------------------------------
  // Save routes redirect back to the card they changed. Show the result there.
  const initialCard = route({ initial: true });
  if (initialCard) {
    const slot = initialCard.querySelector("[data-card-status]");
    const flashes = document.querySelectorAll(".flash-stack > .flash");
    if (slot && flashes.length) {
      for (const flash of flashes) slot.append(flash);
      slot.hidden = false;
    }
  }

  // ---- Unsaved changes --------------------------------------------------
  const valueFields = form => [...form.elements].filter(field =>
    field.name && !field.disabled && field.tagName !== "BUTTON" && field.tagName !== "FIELDSET"
    && !["submit", "reset", "button", "image"].includes(field.type));
  const editable = field => field.type !== "hidden";
  const tracked = [...shell.querySelectorAll("form")].filter(form =>
    !form.hasAttribute("data-settings-untracked") && valueFields(form).some(editable));

  const snapshot = form => valueFields(form).map(field => {
    if (field.type === "file") return `${field.name}=${[...field.files].map(file => `${file.name}:${file.size}`).join("|")}`;
    if (field.type === "checkbox" || field.type === "radio") return `${field.name}=${field.value}:${field.checked}`;
    if (field.tagName === "SELECT" && field.multiple) return `${field.name}=${[...field.selectedOptions].map(option => option.value).join("|")}`;
    return `${field.name}=${field.value}`;
  }).join("\n");

  const baseline = new Map(tracked.map(form => [form, snapshot(form)]));
  const dirty = new Set();

  const formLabel = form => {
    const card = form.closest("[data-settings-card]");
    const title = card?.querySelector(".settings-card-title")?.textContent.trim() || "Settings";
    const pane = form.closest("[data-settings-pane]");
    const paneTitle = pane?.querySelector(".settings-pane-header h2")?.textContent.trim();
    return paneTitle ? `${paneTitle} › ${title}` : title;
  };

  const refreshNav = () => {
    for (const link of paneLinks) {
      const pane = document.getElementById(link.dataset.navPane);
      link.classList.toggle("has-unsaved", [...dirty].some(form => pane?.contains(form)));
    }
    for (const link of itemLinks) {
      const card = document.getElementById(link.dataset.navItem);
      link.classList.toggle("has-unsaved", [...dirty].some(form => card?.contains(form)));
    }
  };

  const check = form => {
    if (!baseline.has(form)) return;
    const changed = snapshot(form) !== baseline.get(form);
    if (changed) dirty.add(form); else dirty.delete(form);
    form.classList.toggle("is-dirty", changed);
    refreshNav();
  };

  const owningForm = target => target?.form || target?.closest?.("form");
  shell.addEventListener("input", event => check(owningForm(event.target)));
  shell.addEventListener("change", event => check(owningForm(event.target)));
  // Buttons such as ADD TAGLINE, ↑/↓, DELETE, and RESTORE BANNER DEFAULT
  // change a form without an input event; re-check after their handlers.
  shell.addEventListener("click", event => {
    const form = owningForm(event.target);
    if (form && event.target.closest("button")) setTimeout(() => check(form));
  });
  shell.addEventListener("reset", event => setTimeout(() => check(event.target)));

  let submitting = false;
  // Capture phase: runs before each form's own submit handlers, so a cancel
  // here also stops the double-submit guard and any other confirmation.
  document.addEventListener("submit", event => {
    const form = event.target;
    if (!shell.contains(form)) return;
    const others = [...dirty].filter(candidate => candidate !== form);
    if (!others.length) return;
    const names = [...new Set(others.map(formLabel))];
    const message = `You have unsaved changes in:\n\n• ${names.join("\n• ")}\n\nSubmitting this form reloads the page and discards them. Continue?`;
    if (!window.confirm(message)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener("submit", event => {
    if (event.defaultPrevented || !shell.contains(event.target)) return;
    submitting = true;
    // Downloads (such as EXPORT WORLD ZIP) keep the page open.
    setTimeout(() => { submitting = false; }, 4000);
  });
  window.addEventListener("pageshow", () => { submitting = false; });
  window.addEventListener("beforeunload", event => {
    if (submitting || !dirty.size) return;
    event.preventDefault();
    event.returnValue = "";
  });

  // ---- Settings search --------------------------------------------------
  const normalize = text => text.toLowerCase().replace(/\s+/g, " ").trim();
  const ownText = element => [...element.childNodes]
    .filter(node => node.nodeType === Node.TEXT_NODE || (node.nodeType === Node.ELEMENT_NODE && ["SMALL", "SPAN", "STRONG", "EM", "CODE", "A"].includes(node.tagName)))
    .map(node => node.textContent).join(" ");
  const index = [...shell.querySelectorAll("[data-settings-card]")].map(card => {
    const title = card.querySelector(".settings-card-title")?.textContent.trim() || "";
    const pane = card.closest("[data-settings-pane]");
    const paneTitle = pane?.querySelector(".settings-pane-header h2")?.textContent.trim() || "";
    const parts = [title, card.querySelector(".settings-card-desc")?.textContent || ""];
    for (const element of card.querySelectorAll("label, legend, h4, summary, button, .settings-note, .divider, p")) {
      parts.push(element.tagName === "LABEL" ? ownText(element) : element.textContent);
    }
    const text = normalize(parts.join(" "));
    // The compact copy lets "timezone" find "time zone" and "username" find "user name".
    return { id: card.id, title, paneTitle, titleText: normalize(`${title} ${paneTitle}`), text, compact: text.replace(/ /g, "") };
  });

  for (const input of shell.querySelectorAll("[data-settings-search]")) {
    const results = document.getElementById(input.getAttribute("aria-controls"));
    if (!results) continue;
    const clear = () => { input.value = ""; results.hidden = true; results.replaceChildren(); };
    const render = () => {
      const terms = normalize(input.value).split(" ").filter(Boolean);
      results.replaceChildren();
      if (!terms.length) { results.hidden = true; return; }
      const matches = index
        .map(entry => ({ entry, score: terms.every(term => entry.text.includes(term) || entry.compact.includes(term) || entry.titleText.includes(term))
          ? terms.filter(term => entry.titleText.includes(term)).length + 1 : 0 }))
        .filter(match => match.score)
        .sort((a, b) => b.score - a.score)
        .slice(0, 8);
      if (!matches.length) {
        const empty = document.createElement("li");
        empty.className = "settings-search-empty";
        empty.textContent = "No matching settings.";
        results.append(empty);
      }
      for (const { entry } of matches) {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = `#${entry.id}`;
        const where = document.createElement("small");
        where.textContent = entry.paneTitle;
        link.append(document.createTextNode(entry.title), where);
        link.addEventListener("click", () => setTimeout(clear));
        item.append(link);
        results.append(item);
      }
      results.hidden = false;
    };
    input.addEventListener("input", render);
    input.addEventListener("keydown", event => {
      if (event.key === "Escape") { clear(); return; }
      if (event.key === "Enter") {
        const first = results.querySelector("a");
        if (first) { event.preventDefault(); first.click(); }
      } else if (event.key === "ArrowDown") {
        const first = results.querySelector("a");
        if (first) { event.preventDefault(); first.focus(); }
      }
    });
    results.addEventListener("keydown", event => {
      const links = [...results.querySelectorAll("a")];
      const position = links.indexOf(document.activeElement);
      if (event.key === "ArrowDown" && position < links.length - 1) { event.preventDefault(); links[position + 1].focus(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); (position > 0 ? links[position - 1] : input).focus(); }
      else if (event.key === "Escape") { clear(); input.focus(); }
    });
  }
})();
