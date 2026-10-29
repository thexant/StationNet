"""Castable abilities: built-in spells plus installed definitions built from fixed primitives.

An ability costs the world's resource and either strikes one enemy (damage
plus harmful effects) or acts on the caster (healing plus helpful effects).
"""
import json

from . import character_creation, mud_effects


TARGETS = ("enemy", "self")
MAX_EFFECTS = 4
MAX_PER_PACK = 100
MAX_PACK_BYTES = 512 * 1024
BUILT_IN = {
    "ember": {"cost": 8, "damage": 9, "effects": [{"key": "burning", "potency": 2, "duration": 3}]},
    "venom": {"cost": 7, "damage": 5, "effects": [{"key": "poison", "potency": 3, "duration": 3}]},
    "rend": {"cost": 6, "damage": 7, "effects": [{"key": "bleeding", "potency": 2, "duration": 3}]},
    "hex": {"cost": 7, "damage": 2, "effects": [{"key": "weakness", "potency": 2, "duration": 3},
                                                   {"key": "vulnerability", "potency": 2, "duration": 3}]},
    "mend": {"cost": 7, "heal": 20},
    "ward": {"cost": 6, "effects": [{"key": "ward", "potency": 3, "duration": 3}]},
    "might": {"cost": 6, "effects": [{"key": "might", "potency": 3, "duration": 3}]},
}
for _key, _definition in BUILT_IN.items():
    _definition.update(key=_key, name=_key, command=_key, description="",
                       target="enemy" if "damage" in _definition else "self")


def _whole(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be a whole number from {low} to {high}.")
    return value


def normalize_ability(document, known_effects):
    """Validate one ability against fixed primitives; ``known_effects`` maps keys to effect definitions."""
    if not isinstance(document, dict):
        raise ValueError("An ability must be a JSON object.")
    unknown = set(document) - {"key", "name", "description", "command", "target", "cost", "damage", "heal",
                               "effects"}
    if unknown:
        raise ValueError(f"Unsupported ability fields: {', '.join(sorted(unknown))}.")
    key = document.get("key")
    if not isinstance(key, str) or not character_creation.KEY_RE.fullmatch(key):
        raise ValueError("Ability key must be 2-40 lowercase letters, digits, or hyphens, starting with a letter.")
    command = document.get("command", key)
    if not isinstance(command, str) or not character_creation.KEY_RE.fullmatch(command):
        raise ValueError("Ability command must be one lowercase word such as frostbolt.")
    target = document.get("target")
    if target not in TARGETS:
        raise ValueError("Ability target must be enemy or self.")
    definition = {"key": key, "name": character_creation._text(document.get("name"), "Ability name", 40, 2),
                  "command": command, "description": "", "target": target,
                  "cost": _whole(document.get("cost", 0), 0, 100, "Ability cost")}
    if document.get("description"):
        definition["description"] = character_creation._text(document["description"], "Ability description", 500)
    if target == "enemy":
        definition["damage"] = _whole(document.get("damage"), 1, 200, "Enemy ability damage")
        if "heal" in document:
            raise ValueError("Only self abilities can heal.")
    else:
        if "damage" in document:
            raise ValueError("Self abilities cannot deal damage.")
        if "heal" in document:
            definition["heal"] = _whole(document["heal"], 1, 200, "Ability heal")
    effects = document.get("effects", [])
    if not isinstance(effects, list) or len(effects) > MAX_EFFECTS:
        raise ValueError(f"Ability effects must be a list of up to {MAX_EFFECTS} effects.")
    parsed = []
    for spec in effects:
        if not isinstance(spec, dict) or set(spec) - {"key", "potency", "duration"}:
            raise ValueError("Each ability effect has key, potency, and duration.")
        effect = known_effects.get(spec.get("key"))
        if effect is None:
            raise ValueError(f"Ability effect {spec.get('key')} is not installed or bundled.")
        key_, potency, duration = mud_effects.validate(spec, extra_keys={spec["key"]})
        if mud_effects.harmful(effect) != (target == "enemy"):
            raise ValueError(f"Effect {key_} cannot be applied by a {target} ability: enemy abilities apply "
                             "harmful effects and self abilities apply helpful ones.")
        parsed.append({"key": key_, "potency": potency, "duration": duration})
    if parsed:
        definition["effects"] = parsed
    if target == "self" and not parsed and "heal" not in definition:
        raise ValueError("A self ability needs heal or at least one effect.")
    return definition


def catalog(database, *, include_removed=False):
    """Every ability by key: built-ins, then installed definitions."""
    result = {key: {**definition, "imported": False, "installed": True} for key, definition in BUILT_IN.items()}
    if database is None:
        return result
    for row in database.execute("SELECT key,definition_json,installed FROM mud_ability_definitions"):
        if row["key"] in result or (not row["installed"] and not include_removed):
            continue
        result[row["key"]] = {**json.loads(row["definition_json"]), "imported": True,
                              "installed": bool(row["installed"])}
    return result


def names(database):
    return {key: entry["name"] for key, entry in catalog(database, include_removed=True).items()}


def known(database, keys):
    """Ability definitions for a character's keys, in order; removed ones still resolve."""
    entries = catalog(database, include_removed=True)
    return [entries[key] for key in keys if key in entries]


def resolve(database, keys, word):
    """The known ability a player means by its command, name, or key."""
    folded = word.casefold()
    matches = [entry for entry in known(database, keys)
               if folded in (entry["command"], entry["name"].casefold(), entry["key"])]
    if not matches:
        return None
    if len({entry["key"] for entry in matches}) > 1:
        raise ValueError(f"More than one known ability answers to {word}; use its full name.")
    return matches[0]


def store(database, definition):
    database.execute("INSERT INTO mud_ability_definitions(key,definition_json,imported,installed) VALUES(?,?,1,1)",
                     (definition["key"], json.dumps(definition, separators=(",", ":"))))


def import_pack(database, payload):
    """Install one ability, one effect, or an {"effects": [...], "abilities": [...]} pack atomically."""
    from . import mud_shared_definitions
    document = mud_shared_definitions.decode(payload, MAX_PACK_BYTES, "ability or effect file")
    if isinstance(document, dict) and document and set(document) <= {"effects", "abilities"}:
        effect_sources, ability_sources = document.get("effects", []), document.get("abilities", [])
    elif isinstance(document, dict) and "target" in document:
        effect_sources, ability_sources = [], [document]
    elif isinstance(document, dict) and "kind" in document:
        effect_sources, ability_sources = [document], []
    else:
        raise ValueError('Upload one ability, one effect, or a pack with "effects" and/or "abilities" arrays.')
    if (not isinstance(effect_sources, list) or not isinstance(ability_sources, list)
            or not 1 <= len(effect_sources) + len(ability_sources) <= MAX_PER_PACK):
        raise ValueError(f"A pack holds 1 to {MAX_PER_PACK} definitions.")
    effects = mud_effects.catalog(database, include_removed=True)
    abilities = catalog(database, include_removed=True)
    parsed_effects, parsed_abilities = [], []
    for index, source in enumerate(effect_sources, 1):
        try:
            definition = mud_effects.normalize_effect(source)
        except ValueError as error:
            raise ValueError(f"Invalid effect at effects[{index}]: {error}") from error
        if definition["key"] in effects:
            raise ValueError(f"An effect with the key {definition['key']} already exists.")
        effects[definition["key"]] = definition
        parsed_effects.append(definition)
    for index, source in enumerate(ability_sources, 1):
        try:
            definition = normalize_ability(source, effects)
        except ValueError as error:
            raise ValueError(f"Invalid ability at abilities[{index}]: {error}") from error
        if definition["key"] in abilities:
            raise ValueError(f"An ability with the key {definition['key']} already exists.")
        abilities[definition["key"]] = definition
        parsed_abilities.append(definition)
    for definition in parsed_effects:
        mud_effects.store(database, definition)
    for definition in parsed_abilities:
        store(database, definition)
    return parsed_effects + parsed_abilities


def dependents(database, key):
    """Installed races and classes that grant an ability."""
    entries = character_creation.catalog(database, world="")
    return [f"{character_creation.KIND_NAMES[kind]} {entry['definition']['name']}"
            for kind in character_creation.KINDS for entry in entries[kind]
            if key in entry["definition"].get("spells", [])]


def remove(database, keys):
    """Remove imported abilities nothing grants any more."""
    entries = {key: entry for key, entry in catalog(database).items() if entry["imported"]}
    keys = set(keys)
    if not keys or keys - set(entries):
        raise ValueError("Select only installed imported abilities to remove.")
    for key in sorted(keys):
        blocking = dependents(database, key)
        if blocking:
            raise ValueError(f"{entries[key]['name']} is still granted by {', '.join(blocking)}. "
                             "Remove or update those first.")
    # Races and classes kept only for existing characters still grant their abilities.
    retained = {key for kind in character_creation.KINDS
                for entry in character_creation.catalog(database, include_removed=True, world="")[kind]
                if not entry["installed"] for key in entry["definition"].get("spells", [])}
    for key in sorted(keys):
        if key in retained:
            database.execute("UPDATE mud_ability_definitions SET installed=0 WHERE key=?", (key,))
        else:
            database.execute("DELETE FROM mud_ability_definitions WHERE key=?", (key,))
        for table in ("mud_world_abilities", "mud_dungeon_abilities"):
            database.execute(f"DELETE FROM {table} WHERE ability_key=?", (key,))
    return [entries[key] for key in sorted(keys)]


def remap_effects(definition, mapping):
    """Point an ability's effect keys at installed keys (or back to package-local ones)."""
    if not definition.get("effects"):
        return dict(definition)
    return {**definition, "effects": [{**spec, "key": mapping.get(spec["key"], spec["key"])}
                                      for spec in definition["effects"]]}
