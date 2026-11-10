"""Draft operations World Forge adds on top of the ported editor's ``mutate``.

They replace old builder tools that worked from station resources: importing a
room-preset file, copying a bundled preset, and starting a dungeon template
from an installed theme or another template.
"""
from __future__ import annotations

import copy

from .. import dungeons, mud_wilderness
from .world import WorldError, mutate as editor_mutate, records, unique_id

MAX_PRESETS = 200


def _presets(files):
    return records(files, "presets", True)


def import_presets(files, args, known):
    """Add presets from a preset file; ``conflict`` is skip or overwrite, ``drop_unknown`` strips bad refs."""
    entries = args.get("presets")
    if isinstance(entries, dict):
        entries = entries.get("presets", [entries])
    if not isinstance(entries, list) or not entries or len(entries) > MAX_PRESETS:
        raise WorldError(f'Upload one preset object or {{"presets": [...]}} with at most {MAX_PRESETS} entries.')
    overwrite = args.get("conflict") == "overwrite"
    drop_unknown = bool(args.get("drop_unknown"))
    npcs = {row.get("id") for row in records(files, "mud_npc_definitions") if isinstance(row, dict)} | known.get("npcs", set())
    items = {row.get("id") for row in records(files, "mud_item_definitions") if isinstance(row, dict)} | known.get("items", set())
    rows = _presets(files)
    counts = {"created": 0, "updated": 0, "skipped": 0}
    for entry in entries:
        try:
            preset = mud_wilderness.validate_preset(copy.deepcopy(entry))
        except (ValueError, mud_wilderness.WildernessError) as problem:
            raise WorldError(f"Preset {entry.get('key', '?') if isinstance(entry, dict) else '?'}: {problem}") from None
        creatures = preset.get("encounters", {}).get("creatures", [])
        resources = preset.get("resources", {}).get("items", [])
        if drop_unknown:
            creatures[:] = [row for row in creatures if row.get("npc") in npcs or str(row.get("npc")).startswith("stationnet:")]
            resources[:] = [row for row in resources if row.get("item") in items or str(row.get("item")).startswith("stationnet:")]
        position = next((index for index, row in enumerate(rows) if isinstance(row, dict) and row.get("key") == preset["key"]), None)
        if position is not None and not overwrite:
            counts["skipped"] += 1
            continue
        if position is None:
            rows.append(preset)
            counts["created"] += 1
        else:
            rows[position] = preset
            counts["updated"] += 1
    return counts


def add_bundled_preset(files, args):
    bundled = mud_wilderness.bundled_presets()
    key = args.get("key")
    if key not in bundled:
        raise WorldError("Choose one of the station's bundled presets.")
    preset = copy.deepcopy(bundled[key])
    rows = _presets(files)
    if any(isinstance(row, dict) and row.get("key") == key for row in rows):
        preset["key"] = unique_id(files, "presets", key)
    rows.append(preset)
    return {"collection": "presets", "index": len(rows) - 1}


def new_dungeon_from_base(files, args):
    """A dungeon template copied from an installed theme or one of this draft's templates."""
    base = str(args.get("base", ""))
    templates = records(files, "templates", True)
    if base.startswith("theme:"):
        try:
            discovered = dungeons.discover_templates()
        except dungeons.TemplateError as problem:
            raise WorldError(str(problem)) from None
        if base[6:] not in discovered:
            raise WorldError("Choose an installed dungeon theme to start from.")
        template = {key: value for key, value in discovered[base[6:]][1].items()
                    if key not in ("origins", "skills", "author")}
    elif base.startswith("template:"):
        source = next((row for row in templates if isinstance(row, dict) and row.get("key") == base[9:]), None)
        if source is None:
            raise WorldError("Choose one of this world's templates to copy.")
        template = copy.deepcopy(source.get("template", {}))
    else:
        raise WorldError("Choose a theme or template to start from.")
    name = str(args.get("name") or template.get("name") or "New dungeon")[:80]
    key = unique_id(files, "templates", args.get("key") or name)
    templates.append({"key": key, "name": name, "template": template})
    return {"collection": "templates", "index": len(templates) - 1}


def mutate(files, operation, args, known=None):
    """The editor's operations plus World Forge's own; returns (files, selection)."""
    known = known or {}
    if operation == "import_presets":
        files = copy.deepcopy(files)
        import_presets(files, args, known)
        return files, {"collection": "presets", "index": max(0, len(records(files, "presets")) - 1)}
    if operation == "add_bundled_preset":
        files = copy.deepcopy(files)
        return files, add_bundled_preset(files, args)
    if operation == "new_dungeon_from_base":
        files = copy.deepcopy(files)
        return files, new_dungeon_from_base(files, args)
    return editor_mutate(files, operation, args)
