"""Sysop-managed skills: import, availability, safe removal, and world/dungeon bundling."""
import gc
from io import BytesIO
import json
from pathlib import Path
import shutil
import uuid

import pytest

from stationnet import mud_scope
from stationnet import create_app, character_creation, dungeons, mud, mud_skills, mud_worlds
from stationnet.db import get_db

SKILL = {"key": "spear-fighting", "name": "Spear Fighting", "group": "weapon",
         "description": "Spears and polearms.",
         "effects": [{"type": "melee_damage", "per_rank": 1, "every": 2, "weapon_category": "spear"}],
         "proficiencies": {"weapons": ["spear"]}, "practice": {"triggers": ["melee_hit"]}}


@pytest.fixture
def station():
    path = Path(__file__).parent / f"_mud_skill_management_{uuid.uuid4().hex}"
    path.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                       "VALUES('Test','Test','TST',1,1)")
            admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                               "VALUES('admin','ADM','unused',1)").lastrowid
            player = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                "VALUES('player','PLY','unused')").lastrowid
            db.commit()
        yield app, path, admin, player
    finally:
        coordinator = app.extensions.get("broadcast_coordinator")
        if coordinator:
            coordinator._closed = True
            coordinator._timer.cancel()
        gc.collect()  # release SQLite handles held by finished requests before deleting the data folder
        shutil.rmtree(path)


def _client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    return client


def _upload(client, route, field, document):
    return client.post(route, data={"csrf_token": "test-token",
                                    field: (BytesIO(json.dumps(document).encode()), "skills.json")},
                       follow_redirects=True)


def _installed(app):
    with app.app_context():
        return {entry["key"]: entry for entry in mud_skills.catalog(get_db(), include_removed=True)}


def test_import_availability_and_minimum_skills(station):
    app, _path, admin, player = station
    client = _client(app, player)
    assert client.post("/sysop/settings/mud-skills/import", data={"csrf_token": "test-token"}).status_code == 302
    assert "spear-fighting" not in _installed(app)
    client = _client(app, admin)
    settings = client.get("/sysop/settings").data
    assert b'id="mud-skills"' in settings and b"RACES, CLASSES &amp; SKILLS" in settings
    selection = client.get("/sysop/settings/mud-worlds/stationnet/availability").data
    assert b"SELECT BUILT-IN" in selection and b"Lockpicking" in selection
    for route in ("/sysop/settings/mud-skills/template", "/sysop/settings/mud-skills/pack/template"):
        response = client.get(route)
        assert response.status_code == 200
        document = json.loads(response.data)
        response.close()
    with app.app_context():
        # The downloadable pack template must itself import cleanly.
        assert len(mud_skills.validate_skill(document["skills"][2], get_db(), extra_skills={"evocation"})
                   ["requirements"]["skills"]) == 1
    bad = _upload(client, "/sysop/settings/mud-skills/import", "skill_file",
                  {**SKILL, "effects": [{"type": "teleport"}]})
    assert b"Import failed" in bad.data and "spear-fighting" not in _installed(app)
    assert b"Imported skill Spear Fighting" in _upload(
        client, "/sysop/settings/mud-skills/import", "skill_file", SKILL).data
    assert _installed(app)["spear-fighting"]["enabled"]
    duplicate = _upload(client, "/sysop/settings/mud-skills/import", "skill_file", {**SKILL, "key": "spears"})
    assert b"already installed" in duplicate.data
    origins = {"races": ["human"], "classes": ["warrior"]}
    refused = client.post("/sysop/settings/mud-worlds/stationnet/availability",
                          data={"csrf_token": "test-token", "skills": ["arms"], **origins})
    assert b"Offer at least 2 starting skills" in refused.data
    assert _installed(app)["lore"]["enabled"]
    saved = client.post("/sysop/settings/mud-worlds/stationnet/availability",
                        data={"csrf_token": "test-token", "skills": ["arms", "guard", "spear-fighting"], **origins},
                        follow_redirects=True)
    assert b"Updated races, classes, and skills for Station World" in saved.data
    with app.app_context():
        db = get_db()
        assert set(mud_skills.definitions(db)) == {"arms", "guard", "spear-fighting"}
        assert {item["key"] for item in character_creation.options(db)["skills"]} == {
            "arms", "guard", "spear-fighting"}
        # Another world's selection is its own.
        with mud_scope.use(mud_scope.PROCEDURAL):
            assert "lore" in mud_skills.definitions(db)


def test_pack_import_is_atomic_and_supports_internal_requirements(station):
    app, _path, admin, _player = station
    client = _client(app, admin)
    pack = {"skills": [SKILL, {"key": "phalanx", "name": "Phalanx", "description": "Spear wall.",
                               "effects": [{"type": "defense"}],
                               "requirements": {"skills": {"spear-fighting": 3}}}]}
    broken = json.loads(json.dumps(pack))
    broken["skills"][1]["practice"] = {"triggers": ["sleeping"]}
    failed = _upload(client, "/sysop/settings/mud-skills/pack/import", "skill_pack", broken)
    assert b"Invalid skill at skills[2]" in failed.data
    assert not {"spear-fighting", "phalanx"} & set(_installed(app))
    duplicated = {"skills": [SKILL, {**SKILL, "name": "Other"}]}
    assert b"Duplicate skill key" in _upload(
        client, "/sysop/settings/mud-skills/pack/import", "skill_pack", duplicated).data
    imported = _upload(client, "/sysop/settings/mud-skills/pack/import", "skill_pack", pack)
    assert b"Imported 2 skills" in imported.data
    assert {"spear-fighting", "phalanx"} <= set(_installed(app))


def test_removal_respects_dependencies_and_keeps_learned_ranks(station):
    app, _path, admin, player = station
    client = _client(app, admin)
    _upload(client, "/sysop/settings/mud-skills/import", "skill_file", SKILL)
    _upload(client, "/sysop/settings/mud-skills/import", "skill_file",
            {**SKILL, "key": "tidecraft", "name": "Tidecraft", "group": "exploration",
             "effects": [{"type": "search", "per_rank": 3}], "proficiencies": {},
             "practice": {"triggers": ["search"]}})
    with app.app_context():
        db = get_db()
        character_creation.import_origin(db, "classes", json.dumps({
            "key": "hoplite", "name": "Hoplite", "description": "Spear soldier.", "traits": "Drilled",
            "skill": "guard", "skills": {"spear-fighting": 1}}).encode())
        character_creation.complete(db, player, {
            "name": "Tide Walker", "race": "human", "class": "warrior",
            "attributes": dict.fromkeys(character_creation.ATTRIBUTES, 11), "skills": ["tidecraft", "lore"]})
        db.commit()
    settings = client.get("/sysop/settings").data
    assert b"used by class Hoplite" in settings and b'data-remove-holders="1"' in settings
    blocked = client.post("/sysop/settings/mud-skills/remove", data={
        "csrf_token": "test-token", "skills": ["spear-fighting"], "confirm_remove": "on"}, follow_redirects=True)
    assert b"still used by class Hoplite" in blocked.data and _installed(app)["spear-fighting"]["installed"]
    unconfirmed = client.post("/sysop/settings/mud-skills/remove", data={
        "csrf_token": "test-token", "skills": ["tidecraft"]}, follow_redirects=True)
    assert b"confirm removal" in unconfirmed.data
    builtin = client.post("/sysop/settings/mud-skills/remove", data={
        "csrf_token": "test-token", "skills": ["arms"], "confirm_remove": "on"}, follow_redirects=True)
    assert b"Select only installed imported skills" in builtin.data
    removed = client.post("/sysop/settings/mud-skills/remove", data={
        "csrf_token": "test-token", "skills": ["tidecraft"], "confirm_remove": "on"}, follow_redirects=True)
    assert b"Removed 1 imported skills: Tidecraft" in removed.data
    entry = _installed(app)["tidecraft"]
    assert (entry["installed"], entry["enabled"]) == (False, False)  # key kept for the character
    with app.app_context():
        db = get_db()
        state = mud.character_state(db, player)
        tide = next(skill for skill in state["skills"] if skill["skill_key"] == "tidecraft")
        assert tide["active"] is False and tide["name"] == "Tidecraft"
        assert mud._character_effects(db, state["id"])["search"] == 0
        with pytest.raises(ValueError, match="already exists"):
            mud_skills.validate_skill({**SKILL, "key": "tidecraft", "name": "New Tide"}, db)
        db.execute("DELETE FROM mud_origin_definitions WHERE key='hoplite'")
        db.commit()
    free = client.post("/sysop/settings/mud-skills/remove", data={
        "csrf_token": "test-token", "skills": ["spear-fighting"], "confirm_remove": "on"}, follow_redirects=True)
    assert b"Removed 1 imported skills" in free.data and "spear-fighting" not in _installed(app)


def _world(skills=True):
    document = {
        "id": "dojo", "name": "Dojo World", "author": "Builder", "start_room": "hall",
        "rooms": [{"id": "hall", "name": "Hall", "description": "A training hall."},
                  {"id": "vault", "name": "Vault", "description": "A locked vault."}],
        "exits": [{"from": "hall", "to": "vault", "name": "vault door", "direction": "north",
                   "pick_difficulty": 35}],
        "content": {
            "mud_item_definitions": [{"id": "spear", "name": "Ash Spear", "description": "A long spear.",
                                      "item_type": "equipment", "equipment_slot": "main_hand",
                                      "properties_json": {"attack_bonus": 3, "category": "spear"}}],
            "mud_npc_definitions": [{"id": "master", "name": "Spear Master", "description": "A teacher."}],
            "mud_npc_instances": [{"id": "master-here", "definition_id": "master", "room_id": "hall"}],
            "mud_npc_trainers": [{"id": "lesson", "npc_definition_id": "master", "skill_key": "spears",
                                  "price": 12, "max_rank": 4},
                                 {"id": "stealth-lesson", "npc_definition_id": "master",
                                  "skill_key": "stealth", "price": 5, "max_rank": 3}],
            "mud_room_discoveries": [{"id": "cache", "room_id": "hall", "clue": "A hidden rack.",
                                      "difficulty": 25}],
        },
        "origins": {"classes": [{"key": "lancer", "name": "Lancer", "description": "Spear fighter.",
                                 "traits": "Reach", "skill": "spears", "skills": {"spears": 1}}]},
    }
    if skills:
        document["skills"] = [{**SKILL, "key": "spears", "name": "Spear Forms"},
                              {"key": "drill", "name": "Drill", "description": "Formation drill.",
                               "effects": [{"type": "defense"}], "practice": {"triggers": ["defend"]},
                               "requirements": {"skills": {"spears": 2}, "classes": ["lancer"]}}]
    return document


def test_world_packages_bundle_remap_export_and_remove_skills(station):
    app, path, admin, _player = station
    client = _client(app, admin)
    without = mud_worlds.package_bytes(_world(skills=False))
    rejected = client.post("/sysop/settings/mud-worlds/import", data={
        "csrf_token": "test-token", "world_file": (BytesIO(without), "dojo.zip")}, follow_redirects=True)
    assert b"World import failed" in rejected.data
    payload = mud_worlds.package_bytes(_world())
    spears = mud_worlds.bundled_key("dojo", "skills", "spears")
    drill = mud_worlds.bundled_key("dojo", "skills", "drill")
    lancer = mud_worlds.bundled_key("dojo", "classes", "lancer")
    preview = client.post("/sysop/settings/mud-worlds/preview", data={
        "csrf_token": "test-token", "world_file": (BytesIO(payload), "dojo.zip")})
    assert [item["key"] for item in preview.json["origins"]["skills"]] == [spears, drill]
    imported = client.post("/sysop/settings/mud-worlds/import", data={
        "csrf_token": "test-token", "world_file": (BytesIO(payload), "dojo.zip"), "origin_mode": "combine"},
        follow_redirects=True)
    assert b"Imported world Dojo World" in imported.data
    with app.app_context(), mud_scope.use("dojo"):
        db = get_db()
        definitions = mud_skills.definitions(db)
        assert {spears, drill, "arms"} <= set(definitions)  # combine keeps defaults and adds bundled skills
        with mud_scope.use(mud_scope.HOME):
            # Other worlds keep their own selection.
            assert spears not in mud_skills.definitions(db)
        assert definitions[drill]["requirements"] == {**definitions[drill]["requirements"],
                                                      "skills": {spears: 2}, "classes": [lancer]}
        lancer_class = next(entry["definition"] for entry in character_creation.catalog(db)["classes"]
                            if entry["key"] == lancer)
        assert lancer_class["skill"] == spears and lancer_class["skills"] == {spears: 1}
        trainers = {row["skill_key"]: row["price"] for row in db.execute("SELECT * FROM mud_npc_trainers")}
        assert trainers == {spears: 12, "stealth": 5}
        assert db.execute("SELECT pick_difficulty FROM mud_world_exits WHERE name='vault door'").fetchone()[0] == 35
        assert db.execute("SELECT difficulty FROM mud_room_discoveries WHERE clue='A hidden rack.'").fetchone()[0] == 25
        exported = mud_worlds.read_package(mud_worlds.export(db, "dojo"))
        assert [skill["key"] for skill in exported["skills"]] == ["drill", "spears"]
        assert exported["skills"][0]["requirements"]["skills"] == {"spears": 2}
        assert exported["skills"][0]["requirements"]["classes"] == ["lancer"]
        assert exported["origins"]["classes"][0]["skill"] == "spears"
        assert {row["skill_key"] for row in exported["content"]["mud_npc_trainers"]} == {"spears", "stealth"}
        assert exported["exits"][0]["pick_difficulty"] == 35
        assert exported["content"]["mud_room_discoveries"][0]["difficulty"] == 25
        # Exporting only the class still carries the skills it depends on.
        partial = mud_worlds.read_package(mud_worlds.export(db, "dojo", origin_keys={("classes", lancer)},
                                                           skill_keys=set()))
        assert {skill["key"] for skill in partial["skills"]} == {"spears"}
    settings = client.get("/sysop/settings").data
    assert b"Spear Forms" in settings and b"TO BUNDLE" in settings
    removed = client.post("/sysop/settings/mud-worlds/remove", data={
        "csrf_token": "test-token", "world_id": "dojo", "remove_origins": "on", "confirm_remove": "on"},
        follow_redirects=True)
    assert b"World removal started" in removed.data
    import time
    finished = []
    for _ in range(200):
        finished = client.get("/sysop/settings/mud-worlds/remove-status").get_json()["finished"]
        if finished:
            break
        time.sleep(0.05)
    assert finished and "Custom world removed" in finished[0] and "2 skills" in finished[0]
    installed = _installed(app)
    assert spears not in installed and drill not in installed
    assert not (path / "mud_worlds" / "dojo.zip").exists()


def test_dungeon_themes_bundle_skills_for_their_origins(station):
    app, path, admin, _player = station
    client = _client(app, admin)
    template = json.loads((Path(dungeons.__file__).with_name("dungeon_templates")
                           / "generic-fantasy-dungeon.json").read_text(encoding="utf-8"))
    template.update(id="spear-caves", name="Spear Caves", author="Builder",
                    skills=[{**SKILL, "key": "spears", "name": "Cave Spears"}],
                    origins={"classes": [{"key": "delver", "name": "Delver", "description": "Cave fighter.",
                                          "traits": "Grit", "skill": "spears"}]})
    response = client.post("/sysop/settings/dungeons/import", data={
        "csrf_token": "test-token",
        "dungeon_file": (BytesIO(json.dumps(template).encode()), "spear-caves.json")}, follow_redirects=True)
    assert b"Imported dungeon theme Spear Caves" in response.data
    key = dungeons.bundled_origin_key("spear-caves", "skills", "spears")
    with app.app_context():
        db = get_db()
        assert dungeons.theme_origin_keys(db, "spear-caves")["skills"] == {key}
        entry = next(entry for entry in mud_skills.catalog(db) if entry["key"] == key)
        assert entry["imported"] and not entry["enabled"]  # bundled skills start inactive
        delver = next(entry["definition"] for entry in character_creation.catalog(db)["classes"]
                      if entry["definition"]["name"] == "Delver")
        assert delver["skill"] == key
    removed = client.post("/sysop/settings/dungeons/remove", data={
        "csrf_token": "test-token", "theme_id": "spear-caves", "remove_origins": "on",
        "confirm_remove": "on"}, follow_redirects=True)
    assert b"1 skills" in removed.data and key not in _installed(app)


def test_world_builder_saves_optional_npc_accuracy_and_evasion(station):
    app, _path, admin, _player = station
    client = _client(app, admin)
    combat = {"csrf_token": "test-token", "description": "Fights.", "properties_json": "{}",
              "behaviors_json": "{}", "is_hostile": "1", "maximum_health": "30", "combat_attack": "6",
              "combat_defense": "2", "experience_reward": "10", "respawn_seconds": "60"}
    client.post("/mud/w/stationnet/builder/npcs", data={**combat, "name": "Duelist", "combat_accuracy": "15",
                                          "combat_evasion": ""})
    rejected = client.post("/mud/w/stationnet/builder/npcs", data={**combat, "name": "Ghost", "combat_evasion": "150"},
                           follow_redirects=True)
    assert b"Accuracy and evasion must be whole numbers" in rejected.data
    with app.app_context():
        db = get_db()
        npc_id = db.execute("SELECT id FROM mud_npc_definitions WHERE name='Duelist'").fetchone()[0]
        profile = lambda: tuple(db.execute("SELECT accuracy,evasion FROM mud_npc_combat_profiles "
                                           "WHERE npc_definition_id=?", (npc_id,)).fetchone())
        assert profile() == (15, None)
        assert db.execute("SELECT 1 FROM mud_npc_definitions WHERE name='Ghost'").fetchone() is None
    assert b'name="combat_accuracy" min="0" max="100" value="15"' in client.get("/mud/w/stationnet/builder").data
    client.post(f"/mud/w/stationnet/builder/npcs/{npc_id}", data={**combat, "name": "Duelist", "combat_accuracy": "",
                                                    "combat_evasion": "9"})
    with app.app_context():
        assert tuple(get_db().execute("SELECT accuracy,evasion FROM mud_npc_combat_profiles "
                                      "WHERE npc_definition_id=?", (npc_id,)).fetchone()) == (None, 9)


def test_world_builder_edits_categories_trainers_locks_and_discoveries(station):
    app, _path, admin, _player = station
    client = _client(app, admin)
    page = client.get("/mud/w/stationnet/builder").data
    assert b"Trainer lessons" not in page and b"skills-reference-heading" in page
    assert b"Lockpicking" in page and b'list="builder-item-categories"' in page
    created = client.post("/mud/w/stationnet/builder/items", data={
        "csrf_token": "test-token", "name": "Ash Spear", "description": "A long spear.",
        "item_type": "equipment", "equipment_slot": "main_hand", "attack_bonus": "4", "category": "Spear"},
        follow_redirects=True)
    assert created.status_code == 200
    bad = client.post("/mud/w/stationnet/builder/items", data={
        "csrf_token": "test-token", "name": "Odd Rock", "description": "A rock.", "item_type": "misc",
        "category": "rock"}, follow_redirects=True)
    assert b"Categories apply to equipment" in bad.data
    client.post("/mud/w/stationnet/builder/npcs", data={"csrf_token": "test-token", "name": "Old Thief",
                                          "description": "Knows locks.", "properties_json": "{}",
                                          "behaviors_json": "{}"})
    with app.app_context():
        db = get_db()
        spear = db.execute("SELECT properties_json FROM mud_item_definitions WHERE name='Ash Spear'").fetchone()
        assert json.loads(spear[0])["category"] == "spear"
        npc_id = db.execute("SELECT id FROM mud_npc_definitions WHERE name='Old Thief'").fetchone()[0]
        start = mud._start_room(db)["id"]
        db.execute("INSERT INTO mud_world_rooms(id,name,description) VALUES('test:den','Den','A den')")
        db.commit()
    rejected = client.post(f"/mud/w/stationnet/builder/npcs/{npc_id}/trainers", data={
        "csrf_token": "test-token", "skill_key": "no-such-skill", "price": "5", "max_rank": "3"},
        follow_redirects=True)
    assert b"Choose an enabled skill" in rejected.data
    saved = client.post(f"/mud/w/stationnet/builder/npcs/{npc_id}/trainers", data={
        "csrf_token": "test-token", "skill_key": "lockpicking", "price": "15", "max_rank": "6"},
        follow_redirects=True)
    assert b"Trainer lesson saved" in saved.data and b"Lockpicking - 15 coins - up to rank 6" in saved.data
    client.post("/mud/w/stationnet/builder/exits", data={"csrf_token": "test-token", "from_room_id": start,
                                            "to_room_id": "test:den", "name": "den door",
                                            "direction": "down", "pick_difficulty": "45"})
    client.post(f"/mud/w/stationnet/builder/rooms/{start}/discoveries", data={
        "csrf_token": "test-token", "clue": "Scratches on the wall.", "difficulty": "30"})
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT pick_difficulty FROM mud_world_exits WHERE name='den door'").fetchone()[0] == 45
        assert db.execute("SELECT difficulty FROM mud_room_discoveries").fetchone()[0] == 30
        lesson = db.execute("SELECT id FROM mud_npc_trainers").fetchone()[0]
    page = client.get("/mud/w/stationnet/builder").data
    assert b"difficulty 30" in page and b'value="45"' in page
    removed = client.post(f"/mud/w/stationnet/builder/trainers/{lesson}/delete",
                          data={"csrf_token": "test-token", "confirmation": "delete"}, follow_redirects=True)
    assert b"Trainer lesson removed" in removed.data
