"""Character compatibility and reversible MUD campaign snapshots."""
import json
import sqlite3

from . import character_creation


PERSISTENT_TABLES = (
    "mud_character_progression", "mud_character_skills", "mud_character_wallets",
    "mud_player_quests", "mud_player_objectives", "mud_npc_player_states",
    "mud_dungeon_npc_player_states",
)
USER_TABLES = ("mud_player_locations", "mud_world_locations", "mud_player_stealth")
OPTIONAL_TABLES = (
    "mud_expedition_participants", "mud_combat_participants", "mud_conversation_sessions",
    "mud_dungeon_conversation_sessions",
)


def incompatible(database, user_id):
    row = database.execute("SELECT race_key,class_key,creation_state FROM mud_characters WHERE user_id=?",
                           (user_id,)).fetchone()
    if row is None or row["creation_state"] != "complete":
        return ()
    enabled = character_creation.catalog(database)
    return tuple(kind for kind, column in (("races", "race_key"), ("classes", "class_key"))
                 if row[column] is not None and
                 row[column] not in {entry["key"] for entry in enabled[kind] if entry["enabled"]})


def replace_origins(database, user_id, race_key, class_key):
    """Change unavailable origins while retaining earned skills and progression."""
    row = database.execute("SELECT * FROM mud_characters WHERE user_id=?", (user_id,)).fetchone()
    if row is None or row["creation_state"] != "complete":
        raise ValueError("Create your character first.")
    missing = incompatible(database, user_id)
    if not missing:
        raise ValueError("This character is already compatible.")
    catalog = character_creation.catalog(database, include_removed=True)
    old = {kind: next((e["definition"] for e in catalog[kind] if e["key"] == row[column]), None)
           for kind, column in (("races", "race_key"), ("classes", "class_key"))}
    keys = {"races": race_key, "classes": class_key}
    new = {}
    for kind, column in (("races", "race_key"), ("classes", "class_key")):
        if kind not in missing and keys[kind] != row[column]:
            raise ValueError("Compatible origins cannot be changed here.")
        entry = next((e for e in catalog[kind] if e["key"] == keys[kind] and e["enabled"]), None)
        if entry is None:
            raise ValueError(f"Choose an available {character_creation.KIND_NAMES[kind]}.")
        new[kind] = entry["definition"]
    old_mod = {attribute: sum((definition or {}).get("attributes", {}).get(attribute, 0)
                              for definition in old.values()) for attribute in character_creation.ATTRIBUTES}
    new_mod = {attribute: sum(definition.get("attributes", {}).get(attribute, 0)
                              for definition in new.values()) for attribute in character_creation.ATTRIBUTES}
    tracks = database.execute("SELECT * FROM mud_character_progression WHERE character_id=?", (row["id"],)).fetchall()
    try:
        allocated = json.loads(row["base_attributes_json"]) if row["base_attributes_json"] else None
    except (TypeError, json.JSONDecodeError):
        allocated = None
    if not isinstance(allocated, dict) or any(
        type(allocated.get(key)) is not int for key in character_creation.ATTRIBUTES
    ):
        allocated = None
    from .mud import _trained_attributes, resource_delta

    def revised(track):
        # Trained points sit on top of the allocation and survive origin changes.
        trained = _trained_attributes(track["trained_attributes_json"])
        return {key: (allocated[key] + trained.get(key, 0) if allocated else track[key] - old_mod[key])
                + new_mod[key] for key in character_creation.ATTRIBUTES}

    for track in tracks:
        attributes = revised(track)
        if any(not 1 <= value <= 30 for value in attributes.values()):
            raise ValueError("These choices would put an attribute outside the supported range.")
        health_delta, mana_delta = resource_delta(track["level"], track, attributes)
        health_max = max(1, track["maximum_health"] + health_delta)
        mana_max = max(1, track["maximum_mana"] + mana_delta)
        database.execute(
            "UPDATE mud_character_progression SET strength=?,agility=?,intellect=?,vitality=?,"
            "maximum_health=?,current_health=?,maximum_mana=?,current_mana=?,"
            "updated_at=CURRENT_TIMESTAMP WHERE character_id=? AND track_key=?",
            (*[attributes[key] for key in character_creation.ATTRIBUTES], health_max,
             min(health_max, max(0, track["current_health"] + health_delta)), mana_max,
             min(mana_max, max(0, track["current_mana"] + mana_delta)), row["id"], track["track_key"]),
        )
    # Spells derive from the current class. Retain earned skill ranks and all items;
    # unequip gear whose requirements the revised attributes no longer meet.
    from .mud import _item_requirements, _json_properties
    equipment = database.execute(
        "SELECT e.item_instance_id,d.properties_json,e.loadout_key "
        "FROM mud_character_equipment e JOIN mud_item_instances i ON i.id=e.item_instance_id "
        "JOIN mud_item_definitions d ON d.id=i.definition_id WHERE e.character_id=?", (row["id"],)).fetchall()
    for item in equipment:
        track = next((track for track in tracks if track["track_key"] == item["loadout_key"]), None)
        if track is None:
            continue
        attributes = revised(track)
        try:
            requirements = _item_requirements(_json_properties(item["properties_json"]))
        except ValueError:
            requirements = {}
        if any(attributes[key] < value for key, value in requirements.items()):
            database.execute("DELETE FROM mud_character_equipment WHERE character_id=? AND loadout_key=? "
                             "AND item_instance_id=?", (row["id"], item["loadout_key"], item["item_instance_id"]))
    if allocated is None and tracks:
        trained = _trained_attributes(tracks[0]["trained_attributes_json"])
        allocated = {key: tracks[0][key] - old_mod[key] - trained.get(key, 0)
                     for key in character_creation.ATTRIBUTES}
    database.execute("UPDATE mud_characters SET race_key=?,class_key=?,base_attributes_json=?,"
                     "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (race_key, class_key, json.dumps(allocated) if allocated else None, row["id"]))


def _rows(database, table, field, value):
    return [dict(row) for row in database.execute(f"SELECT * FROM {table} WHERE {field}=?", (value,))]


def _owned_items(database, character_id):
    return [dict(row) for row in database.execute(
        "WITH RECURSIVE owned(id,depth) AS ("
        "SELECT id,0 FROM mud_item_instances WHERE owner_character_id=? UNION ALL "
        "SELECT i.id,owned.depth+1 FROM mud_item_instances i JOIN owned ON i.container_id=owned.id) "
        "SELECT i.*,owned.depth FROM owned JOIN mud_item_instances i ON i.id=owned.id ORDER BY owned.depth",
        (character_id,))]


def _insert(database, table, row):
    columns = list(row)
    database.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                     [row[column] for column in columns])


def _insert_if_references_exist(database, table, row):
    # A removed world or expired dungeon may have retired the referenced scene.
    # The original row remains in the immutable archive for a later restoration.
    try:
        _insert(database, table, row)
    except sqlite3.IntegrityError as error:
        if "FOREIGN KEY constraint failed" not in str(error):
            raise


def _clear_character(database, user_id, character_id):
    for table in ("mud_presence", "mud_player_locations", "mud_world_locations", "mud_player_stealth",
                  "mud_death_summaries"):
        database.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
    database.execute("DELETE FROM mud_active_effects WHERE target_type='character' AND target_id=?", (character_id,))
    for item in reversed(_owned_items(database, character_id)):
        database.execute("DELETE FROM mud_item_instances WHERE id=?", (item["id"],))
    database.execute("DELETE FROM mud_characters WHERE id=?", (character_id,))


def archive_campaign(database, world_id, world_name):
    archive_id = database.execute("INSERT INTO mud_campaign_archives(world_id,world_name) VALUES(?,?)",
                                  (world_id, world_name)).lastrowid
    for character in database.execute("SELECT * FROM mud_characters WHERE creation_state!='pending'").fetchall():
        cid, user_id = character["id"], character["user_id"]
        snapshot = {"character": dict(character),
                    "items": _owned_items(database, cid),
                    "equipment": _rows(database, "mud_character_equipment", "character_id", cid),
                    "storage": _rows(database, "mud_character_storage", "character_id", cid),
                    "effects": [dict(row) for row in database.execute(
                        "SELECT * FROM mud_active_effects WHERE target_type='character' AND target_id=?", (cid,))],
                    "tables": {table: _rows(database, table, "character_id", cid)
                               for table in PERSISTENT_TABLES},
                    "users": {table: _rows(database, table, "user_id", user_id)
                              for table in USER_TABLES},
                    "optional": {table: _rows(database, table, "character_id", cid)
                                 for table in OPTIONAL_TABLES},
                    "expedition_visits": [dict(visit) for visit in database.execute(
                        "SELECT v.* FROM mud_expedition_room_visits v "
                        "JOIN mud_expedition_participants p ON p.instance_id=v.instance_id "
                        "AND p.user_id=v.user_id WHERE p.character_id=?", (cid,))],
                    "death_summary": _rows(database, "mud_death_summaries", "user_id", user_id)}
        database.execute("INSERT INTO mud_campaign_characters(archive_id,user_id,snapshot_json) VALUES(?,?,?)",
                         (archive_id, user_id, json.dumps(snapshot, separators=(",", ":"))))
        _clear_character(database, user_id, cid)
        database.execute(
            "INSERT INTO mud_event_views(user_id,cleared_through_id) "
            "VALUES(?,(SELECT COALESCE(MAX(id),0) FROM mud_events)) "
            "ON CONFLICT(user_id) DO UPDATE SET cleared_through_id=excluded.cleared_through_id",
            (user_id,))
        database.execute("INSERT INTO mud_characters(id,user_id,display_name,creation_state) VALUES(?,?,?,'pending')",
                         (cid, user_id, character["display_name"]))
        for track in ("shared", "traditional", "procedural"):
            database.execute("INSERT INTO mud_character_progression(character_id,track_key) VALUES(?,?)", (cid, track))
    return archive_id


def restore_campaign(database, archive_id):
    rows = database.execute("SELECT user_id,snapshot_json FROM mud_campaign_characters WHERE archive_id=?",
                            (archive_id,)).fetchall()
    for record in rows:
        snapshot = json.loads(record["snapshot_json"])
        cid = snapshot["character"]["id"]
        _clear_character(database, record["user_id"], cid)
        _insert(database, "mud_characters", snapshot["character"])
        for table in PERSISTENT_TABLES:
            for row in snapshot["tables"][table]:
                _insert(database, table, row)
        for item in snapshot["items"]:
            item.pop("depth", None)
            _insert(database, "mud_item_instances", item)
        for row in snapshot["equipment"]:
            _insert(database, "mud_character_equipment", row)
        for row in snapshot["storage"]:
            _insert(database, "mud_character_storage", row)
        for row in snapshot["effects"]:
            _insert(database, "mud_active_effects", row)
        for table in OPTIONAL_TABLES:
            for row in snapshot.get("optional", {}).get(table, []):
                _insert_if_references_exist(database, table, row)
        for row in snapshot.get("expedition_visits", []):
            _insert_if_references_exist(database, "mud_expedition_room_visits", row)
        for row in snapshot.get("death_summary", []):
            _insert_if_references_exist(database, "mud_death_summaries", row)
        for table in USER_TABLES:
            for row in snapshot["users"][table]:
                _insert_if_references_exist(database, table, row)
    return len(rows)
