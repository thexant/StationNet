"""What a world may reference from the station itself, for pickers and validation."""
from __future__ import annotations

from .. import character_creation, dungeons, mud_abilities, mud_effects, mud_skills, mud_wilderness


def _name(row, key):
    if not isinstance(row, dict):
        return key
    definition = row.get("definition") if isinstance(row.get("definition"), dict) else {}
    return row.get("name") or row.get("label") or definition.get("name") or key


def _entries(values):
    return sorted(({"key": key, "name": name} for key, name in values), key=lambda row: row["name"].casefold())


def build(database):
    origins = character_creation.catalog(database, world="")
    skills = mud_skills.catalog(database, world="")
    effects = mud_effects.catalog(database)
    abilities = mud_abilities.catalog(database)
    try:
        themes = [(key, template.get("name", key)) for key, (_path, template) in dungeons.discover_templates().items()]
    except dungeons.TemplateError:
        themes = []
    shared_npcs = database.execute("SELECT id,name FROM mud_npc_definitions WHERE id LIKE 'stationnet:%' "
                                   "ORDER BY name COLLATE NOCASE").fetchall()
    shared_items = database.execute("SELECT id,name FROM mud_item_definitions WHERE id LIKE 'stationnet:%' "
                                    "ORDER BY name COLLATE NOCASE").fetchall()
    return {
        "races": _entries((row["key"], _name(row, row["key"])) for row in origins.get("races", [])),
        "classes": _entries((row["key"], _name(row, row["key"])) for row in origins.get("classes", [])),
        "skills": _entries((row["key"], _name(row, row["key"])) for row in skills),
        "effects": _entries((key, _name(value, key)) for key, value in effects.items()),
        "abilities": _entries((key, _name(value, key)) for key, value in abilities.items()),
        "npcs": _entries((row["id"], row["name"]) for row in shared_npcs),
        "items": _entries((row["id"], row["name"]) for row in shared_items),
        "presets": _entries((key, preset.get("name", key)) for key, preset in mud_wilderness.bundled_presets().items()),
        "themes": _entries(themes),
    }


def known(catalog):
    """Reference target -> keys the station already provides."""
    keys = lambda name: {row["key"] for row in catalog.get(name, [])}
    return {"races": keys("races"), "classes": keys("classes"), "skills": keys("skills"),
            "effects": keys("effects"), "abilities": keys("abilities"),
            "mud_npc_definitions": keys("npcs"), "mud_item_definitions": keys("items"),
            "npcs": keys("npcs"), "items": keys("items")}
