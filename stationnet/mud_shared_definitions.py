"""One conflict policy for races, classes, skills, abilities, and effects that packages bundle.

Bundled definitions get keys derived from their package (``w-…`` for worlds,
``d-…`` for dungeon themes), and link rows record which packages own them:

* nothing installed under the key: insert it;
* an identical definition: reuse it (and reinstate it if it was retained);
* a different definition only this package (or nobody) owns: replace it, with a warning;
* a different definition another package also owns: refuse, so neither breaks.
"""
import json

KINDS = ("effects", "abilities", "skills", "races", "classes")
DEFINITION_TABLES = {"effects": "mud_effect_definitions", "abilities": "mud_ability_definitions",
                     "skills": "mud_skill_definitions", "races": "mud_origin_definitions",
                     "classes": "mud_origin_definitions"}
# owner type -> kind -> (link table, owner column, key column)
LINKS = {
    "world": {"effects": ("mud_world_effects", "world_id", "effect_key"),
              "abilities": ("mud_world_abilities", "world_id", "ability_key"),
              "skills": ("mud_world_skills", "world_id", "skill_key"),
              "races": ("mud_world_origins", "world_id", "origin_key"),
              "classes": ("mud_world_origins", "world_id", "origin_key")},
    "theme": {"effects": ("mud_dungeon_effects", "theme_id", "effect_key"),
              "abilities": ("mud_dungeon_abilities", "theme_id", "ability_key"),
              "skills": ("mud_dungeon_skills", "theme_id", "skill_key"),
              "races": ("mud_dungeon_origins", "theme_id", "origin_key"),
              "classes": ("mud_dungeon_origins", "theme_id", "origin_key")},
}
NOUNS = {"effects": "effect", "abilities": "ability", "skills": "skill", "races": "race", "classes": "class"}


class DefinitionConflict(ValueError):
    pass


def decode(payload, limit, label):
    if not payload or len(payload) > limit:
        raise ValueError(f"Choose a JSON file no larger than {limit // 1024} KB.")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"The {label} must contain valid UTF-8 JSON.") from error


def _origin(kind):
    return kind in ("races", "classes")


def _where(kind):
    return ("kind=? AND key=?", (kind,)) if _origin(kind) else ("key=?", ())


def existing(database, kind, key):
    clause, prefix = _where(kind)
    return database.execute(f"SELECT definition_json,installed FROM {DEFINITION_TABLES[kind]} WHERE {clause}",
                            (*prefix, key)).fetchone()


def owners(database, kind, key):
    """Every (owner type, owner id) linked to a definition."""
    result = set()
    for owner_type, links in LINKS.items():
        table, owner_column, key_column = links[kind]
        extra = " AND kind=?" if _origin(kind) else ""
        for row in database.execute(f"SELECT {owner_column} FROM {table} WHERE {key_column}=?{extra}",
                                    (key, kind) if extra else (key,)):
            result.add((owner_type, row[0]))
    return result


def link(database, kind, key, owner, local_key):
    owner_type, owner_id = owner
    table, owner_column, key_column = LINKS[owner_type][kind]
    if _origin(kind):
        database.execute(f"INSERT OR IGNORE INTO {table}({owner_column},kind,{key_column},local_key) "
                         "VALUES(?,?,?,?)", (owner_id, kind, key, local_key))
    else:
        database.execute(f"INSERT OR IGNORE INTO {table}({owner_column},{key_column},local_key) VALUES(?,?,?)",
                         (owner_id, key, local_key))


def install_bundled(database, kind, key, definition, owner, local_key, notes=None):
    """Install or reuse one bundled definition under the shared policy; returns what happened."""
    table = DEFINITION_TABLES[kind]
    encoded = json.dumps(definition, separators=(",", ":"))
    row = existing(database, kind, key)
    clause, prefix = _where(kind)
    enabled = ",enabled=1" if kind in ("skills", "races", "classes") else ""
    if row is None:
        if _origin(kind):
            database.execute(f"INSERT INTO {table}(kind,key,definition_json,imported,installed,enabled) "
                             "VALUES(?,?,?,1,1,1)", (kind, key, encoded))
        elif enabled:
            database.execute(f"INSERT INTO {table}(key,definition_json,imported,installed,enabled) "
                             "VALUES(?,?,1,1,1)", (key, encoded))
        else:
            database.execute(f"INSERT INTO {table}(key,definition_json,imported,installed) VALUES(?,?,1,1)",
                             (key, encoded))
        action = "inserted"
    elif json.loads(row["definition_json"]) == definition:
        action = "reused" if row["installed"] else "reinstated"
        if not row["installed"]:
            database.execute(f"UPDATE {table} SET installed=1{enabled} WHERE {clause}", (*prefix, key))
    else:
        others = owners(database, kind, key) - {owner}
        if row["installed"] and others:
            raise DefinitionConflict(
                f"Bundled {NOUNS[kind]} {local_key} conflicts with an installed definition another package "
                "also uses; give it a different key in this package.")
        database.execute(f"UPDATE {table} SET definition_json=?,installed=1{enabled} WHERE {clause}",
                         (encoded, *prefix, key))
        action = "replaced"
        if notes is not None:
            notes.append(f"Bundled {NOUNS[kind]} {local_key} replaced the version installed earlier.")
    link(database, kind, key, owner, local_key)
    return action


def owned_keys(database, owner, kind):
    """Installed key -> package-local key for one owner and kind."""
    owner_type, owner_id = owner
    table, owner_column, key_column = LINKS[owner_type][kind]
    extra = " AND kind=?" if _origin(kind) else ""
    return {row[0]: row[1] for row in database.execute(
        f"SELECT {key_column},local_key FROM {table} WHERE {owner_column}=?{extra}",
        (owner_id, kind) if extra else (owner_id,))}


def unshared(database, owner, kind):
    """Keys this owner links that no other package links."""
    return {key for key in owned_keys(database, owner, kind) if not owners(database, kind, key) - {owner}}


def remove_unused_abilities_and_effects(database, owner):
    """Drop an owner's abilities and effects nothing else uses; used ones are kept and reported."""
    from . import mud_abilities, mud_effects
    kept = []
    abilities = unshared(database, owner, "abilities")
    effects = unshared(database, owner, "effects")
    owner_type, owner_id = owner
    for kind in ("abilities", "effects"):
        table, owner_column, _key_column = LINKS[owner_type][kind]
        database.execute(f"DELETE FROM {table} WHERE {owner_column}=?", (owner_id,))
    removable = {key for key in abilities if not mud_abilities.dependents(database, key)}
    kept += sorted(abilities - removable)
    if removable:
        mud_abilities.remove(database, removable)
    for key in sorted(effects):
        if mud_effects.dependents(database, key):
            kept.append(key)
            continue
        mud_effects.remove_effects(database, {key}, force=True)
    return {"abilities": sorted(removable), "effects": sorted(effects - set(kept)), "kept": kept}
