"""Station header tagline storage and per-page selection."""
import json
import re
import secrets

from flask import session


EFFECTS = frozenset({
    "none", "blink", "rainbow", "glow", "typewriter", "marquee", "scramble",
    "crt-flicker", "glitch", "scanline", "ansi-steps", "reverse-video",
    "block-reveal", "wave", "slide", "shimmer", "phosphor", "jitter",
    "spacing-pulse", "terminal-scroll", "neon-buzz",
})
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
MAX_TAGLINES = 32


def configured(station):
    """Return the saved list, or the legacy tagline for a newly set up station."""
    if station is None:
        return []
    raw = station["header_taglines_json"]
    if raw is None:
        return [{"text": station["brand_tagline"], "color": "", "effect": "none"}]
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    entries = []
    for item in values[:MAX_TAGLINES]:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        text = item["text"]
        color = item.get("color", "")
        effect = item.get("effect", "none")
        if (not 1 <= len(text) <= 160 or not isinstance(color, str)
                or (color and not HEX_COLOR.fullmatch(color))
                or not isinstance(effect, str) or effect not in EFFECTS):
            continue
        entries.append({"text": text, "color": color, "effect": effect})
    return entries


def parse_form(form):
    texts = form.getlist("tagline_text")
    color_modes = form.getlist("tagline_color_mode")
    color_values = form.getlist("tagline_color_value")
    effects = form.getlist("tagline_effect")
    if len({len(texts), len(color_modes), len(color_values), len(effects)}) != 1:
        raise ValueError("Tagline fields did not match. Reload and try again.")
    if len(texts) > MAX_TAGLINES:
        raise ValueError(f"A station can have up to {MAX_TAGLINES} header taglines.")
    entries = []
    for text, mode, value, effect in zip(texts, color_modes, color_values, effects):
        text = text.strip()
        if mode not in {"inherit", "custom"}:
            raise ValueError("Select a valid tagline color mode.")
        color = value.strip() if mode == "custom" else ""
        if not 1 <= len(text) <= 160:
            raise ValueError("Each header tagline must be between 1 and 160 characters.")
        if mode == "custom" and not HEX_COLOR.fullmatch(color):
            raise ValueError("Tagline colors must be six-digit hexadecimal colors.")
        if effect not in EFFECTS:
            raise ValueError("Select a valid tagline animation.")
        entries.append({"text": text, "color": color.lower(), "effect": effect})
    return entries


def pick_for_page(station):
    """Choose once when the shared page template renders, avoiding the last text."""
    entries = configured(station)
    if not entries:
        return None
    previous = session.get("last_header_tagline")
    alternatives = [entry for entry in entries if entry.get("text") != previous]
    chosen = secrets.choice(alternatives or entries)
    session["last_header_tagline"] = chosen.get("text")
    return chosen
