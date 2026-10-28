"""Character compatibility with deleted definitions, and conversion of legacy campaign archives."""
import json
import uuid

from . import character_creation, mud_scope


PERSISTENT_TABLES = (
    "mud_character_progression", "mud_character_skills", "mud_character_wallets", "mud_character_banks",
    "mud_player_quests", "mud_player_objectives", "mud_npc_player_states",
    "mud_dungeon_npc_player_states", "mud_player_dialogue_claims",
)
# World-scoped character state an archive carries into its world's new character.
# Live sessions (combat, conversations, expedition membership) are not revived.
RESTORED_OPTIONAL_TABLES = (
    "mud_character_destinations", "mud_npc_relationships", "mud_character_reputation",
    "mud_character_crimes", "mud_pickpocket_marks", "mud_character_professions", "mud_character_work",
)


def incompatible(database, user_id):
    """Origins of the current world's character that were deleted from the station.

    Disabling a race or class for a world only affects new characters; existing
    ones keep playing. Only a deleted definition asks for a replacement.
    """
    row = character_creation.find_character(database, user_id)
    if row is None or row["creation_state"] != "complete":
        return ()
    installed = character_creation.catalog(database, world="")
    return tuple(kind for kind, column in (("races", "race_key"), ("classes", "class_key"))
                 if row[column] is not None and
                 row[column] not in {entry["key"] for entry in installed[kind]})


def replace_origins(database, user_id, race_key, class_key):
    """Change unavailable origins while retaining earned skills and progression."""
    row = character_creation.find_character(database, user_id)
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
    if allocated is None and tracks:
        trained = _trained_attributes(tracks[0]["trained_attributes_json"])
        allocated = {key: tracks[0][key] - old_mod[key] - trained.get(key, 0)
                     for key in character_creation.ATTRIBUTES}
    database.execute("UPDATE mud_characters SET race_key=?,class_key=?,base_attributes_json=?,"
                     "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (race_key, class_key, json.dumps(allocated) if allocated else None, row["id"]))
    # Spells, tags, and proficiencies derive from the current origins. Retain earned skill
    # ranks and all items; unequip gear the revised character no longer qualifies for.
    from .mud import _item_requirement_problems, _json_properties
    facts = character_creation.origin_facts(database, row["id"])
    equipment = database.execute(
        "SELECT e.item_instance_id,d.properties_json,e.loadout_key "
        "FROM mud_character_equipment e JOIN mud_item_instances i ON i.id=e.item_instance_id "
        "JOIN mud_item_definitions d ON d.id=i.definition_id WHERE e.character_id=?", (row["id"],)).fetchall()
    for item in equipment:
        track = next((track for track in tracks if track["track_key"] == item["loadout_key"]), None)
        if track is None:
            continue
        character = {**revised(track), "level": track["level"]}
        if _item_requirement_problems(_json_properties(item["properties_json"]), character, facts):
            database.execute("DELETE FROM mud_character_equipment WHERE character_id=? AND loadout_key=? "
                             "AND item_instance_id=?", (row["id"], item["loadout_key"], item["item_instance_id"]))
    character_creation.sync_origin_resources(database, row["id"])
    # New origins add their skill grants; earned ranks are never lowered.
    from . import mud_skills
    mud_skills.grant(database, row["id"], mud_skills.origin_grants(new["races"], new["classes"]))


def _columns(database, table):
    return {row["name"] for row in database.execute(f"PRAGMA table_info({table})")}


def _references_exist(database, table, row):
    """Archived rows may name rooms, quests, or runs that are gone; keep only rows that still fit."""
    for key in database.execute(f"PRAGMA foreign_key_list({table})").fetchall():
        value = row.get(key["from"])
        target = key["to"] or "rowid"
        if value is not None and database.execute(
                f"SELECT 1 FROM {key['table']} WHERE {target}=?", (value,)).fetchone() is None:
            return False
    return True


def _insert(database, table, row):
    known = _columns(database, table)
    columns = [column for column in row if column in known]
    database.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                     [row[column] for column in columns])


def _insert_if_references_exist(database, table, row):
    if _references_exist(database, table, row):
        _insert(database, table, row)
        return True
    return False


def _restore_snapshot(database, user_id, world, snapshot, policy):
    """Recreate one archived character as the account's character in its world, with fresh item IDs."""
    from .db import normalize_mud_character
    character_id = f"stationnet:character:{user_id}"
    if world != mud_scope.HOME or database.execute(
            "SELECT 1 FROM mud_characters WHERE id=?", (character_id,)).fetchone():
        character_id = f"stationnet:character:{user_id}:{world}"
    _insert(database, "mud_characters", {**snapshot["character"], "id": character_id, "world_key": world,
                                         "user_id": user_id})
    item_ids = {}
    for item in snapshot.get("items", []):
        item = dict(item)
        item.pop("depth", None)
        old = item["id"]
        if item.get("container_id") is not None:
            if item["container_id"] not in item_ids:
                continue
            item["container_id"] = item_ids[item["container_id"]]
        else:
            item["owner_character_id"] = character_id
        if item.get("acquired_run_id") and database.execute(
                "SELECT 1 FROM mud_procedural_instances WHERE id=?", (item["acquired_run_id"],)).fetchone() is None:
            item["acquired_run_id"] = None
        item.update(id=f"item:archive:{uuid.uuid4().hex}", room_id=None, equipped_slot=None,
                    possession_mode="traditional")
        if _insert_if_references_exist(database, "mud_item_instances", item):
            item_ids[old] = item["id"]
    for table in PERSISTENT_TABLES:
        for row in snapshot.get("tables", {}).get(table, []):
            _insert_if_references_exist(database, table, {**row, "character_id": character_id})
    for table, rows in (("mud_character_equipment", snapshot.get("equipment", [])),
                        ("mud_character_storage", snapshot.get("storage", []))):
        for row in rows:
            if row.get("item_instance_id") in item_ids:
                _insert_if_references_exist(database, table, {**row, "character_id": character_id,
                                                              "item_instance_id": item_ids[row["item_instance_id"]]})
    for table in RESTORED_OPTIONAL_TABLES:
        for row in snapshot.get("optional", {}).get(table, []):
            if "world_id" in row and row["world_id"] != world:
                continue
            if table == "mud_character_crimes":
                row = {key: value for key, value in row.items() if key != "id"}
            _insert_if_references_exist(database, table, {**row, "character_id": character_id})
    users = snapshot.get("users", {})
    for row in users.get("mud_world_locations", []):
        _insert_if_references_exist(database, "mud_world_locations", {**row, "world_key": world})
    for row in users.get("mud_player_locations", []):
        if row.get("location_type") == "world_room" and database.execute(
                "SELECT 1 FROM mud_world_rooms WHERE id=? AND world_id=?",
                (row.get("location_key"), world)).fetchone():
            _insert_if_references_exist(database, "mud_player_locations",
                                        {**row, "world_key": world, "mode": "traditional", "instance_id": None})
    normalize_mud_character(database, character_id, "traditional",
                            "shared" if policy["progression_shared"] else "traditional",
                            "shared" if policy["inventory_shared"] else "traditional")
    return character_id


def convert_archives(database, policy):
    """Make the latest archived character per account and installed world that account's character there.

    Archives were snapshots taken when a station switched its one active world.
    Accounts that already have a character in that world keep it. The archive
    rows stay as they are; ``converted_at`` records that they were applied.
    """
    worlds = {row["id"] for row in database.execute("SELECT id FROM mud_world_packages")}
    handled = set()
    restored = []
    for archive in database.execute("SELECT id,world_id FROM mud_campaign_archives WHERE converted_at IS NULL "
                                     "ORDER BY id DESC").fetchall():
        world = archive["world_id"]
        for record in database.execute("SELECT user_id,snapshot_json FROM mud_campaign_characters "
                                       "WHERE archive_id=? ORDER BY user_id", (archive["id"],)).fetchall():
            key = (record["user_id"], world)
            if key in handled or world not in worlds:
                continue
            handled.add(key)
            if database.execute("SELECT 1 FROM mud_characters WHERE user_id=? AND world_key=?", key).fetchone():
                continue
            database.execute("SAVEPOINT mud_archive_character")
            try:
                snapshot = json.loads(record["snapshot_json"])
                restored.append(_restore_snapshot(database, record["user_id"], world, snapshot, policy))
            except Exception:  # noqa: BLE001 - one unreadable snapshot must not block the others
                database.execute("ROLLBACK TO mud_archive_character")
            database.execute("RELEASE mud_archive_character")
        database.execute("UPDATE mud_campaign_archives SET converted_at=CURRENT_TIMESTAMP WHERE id=?",
                         (archive["id"],))
    return restored
