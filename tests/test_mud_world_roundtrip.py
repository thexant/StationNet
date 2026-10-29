"""An exported world imports into a clean station and plays: its own races, classes, abilities,
effects, progression, merchants, quests, events, and dungeons all work there."""
from io import BytesIO
import json
from pathlib import Path
import shutil
import uuid
import zipfile

import pytest

from mud_world_helpers import enter_world
from stationnet import (create_app, character_creation, dungeons, mud, mud_effects, mud_progression,
                        mud_world_dungeons, mud_worlds)
from stationnet.db import get_db


WORLD = "lantern-isles"
ATTRIBUTES = {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11}


@pytest.fixture
def stations():
    """Factory for independent stations, each with its own empty data directory."""
    made = []

    def make():
        data = Path(__file__).parent / f"_roundtrip_{uuid.uuid4().hex}"
        data.mkdir()
        app = create_app({"TESTING": True, "DATA_DIR": str(data)})
        with app.app_context():
            get_db().execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                             "VALUES('Test','Test','TST',1,1)")
            get_db().commit()
        made.append((app, data))
        return app
    yield make
    for app, data in made:
        app.extensions["broadcast_coordinator"]._closed = True
        app.extensions["broadcast_coordinator"]._timer.cancel()
        shutil.rmtree(data, ignore_errors=True)


def _files(payload):
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        return {name: json.loads(archive.read(name)) for name in archive.namelist()}


def _exported_example(make):
    """Import the shipped example on one station and export it again."""
    app = make()
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, mud_worlds.example_bytes(), enable=True)
        return mud_worlds.export(db, WORLD)


def _global(db, table, name):
    return db.execute(f"SELECT id FROM {table} WHERE world_id=? AND name=?", (WORLD, name)).fetchone()["id"]


def _room(db, name):
    return db.execute("SELECT id FROM mud_world_rooms WHERE world_id=? AND name=?", (WORLD, name)).fetchone()["id"]


def _player(db, name="Wren"):
    uid = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES(?,?,'x')",
                     (name.lower(), name[:3].upper())).lastrowid
    character_creation.complete(db, uid, {
        "name": name, "race": mud_worlds.bundled_key(WORLD, "races", "islander"),
        "class": mud_worlds.bundled_key(WORLD, "classes", "lamplighter"),
        "attributes": ATTRIBUTES, "skills": ["lore", "guard"]})
    mud._set_player_location(db, uid, "traditional", "world_room", _room(db, "Lantern Harbor"))
    return uid, db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _move(db, uid, room_name):
    mud._set_player_location(db, uid, "traditional", "world_room", _room(db, room_name))


def test_exported_world_plays_on_a_clean_station(stations):
    payload = _exported_example(stations)
    files = _files(payload)
    assert files["manifest.json"]["format_version"] == mud_worlds.PACKAGE_FORMAT
    assert {effect["key"] for effect in files["effects.json"]["effects"]} == {
        "searing-light", "tide-blessing", "brine-chill"}
    assert files["origins.json"]["classes"][0]["spells"] == ["lantern-flare", "tide-prayer", "ward"]

    app = stations()
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, payload, enable=True)
        enter_world(db, WORLD)
        offered = character_creation.world_availability(db, WORLD)
        assert offered["races"] == {mud_worlds.bundled_key(WORLD, "races", "islander")}
        uid, user = _player(db)
        character = mud._character_row(db, uid)

        # Progression tuning: starting coins, experience rate, and the level cap.
        assert mud._wallet(db, character["id"]) == 50
        award = mud.award_experience(db, uid, 100)
        assert award["amount"] >= 150
        mud.award_experience(db, uid, 10_000_000)
        state = mud.character_state(db, uid)
        assert state["level"] == 20 and state["max_level"] == 20 and state["at_max_level"]

        # The bundled class casts its bundled ability, which applies the bundled effect by name.
        _move(db, uid, "Wind Cliffs")
        listing = mud._spells_action(db, user, {})[0].body
        assert "Lantern Flare (flare): 6 glow" in listing
        drafts = mud._cast_action(db, user, {"arguments": ["flare", "Storm Gull"]})
        assert any("Searing Light" in draft.body for draft in drafts)
        gull = db.execute("SELECT id FROM mud_npc_instances WHERE id LIKE ?",
                          (f"world:{WORLD}:%:gull-at-cliffs",)).fetchone()["id"]
        encounter = db.execute("SELECT id FROM mud_combat_encounters WHERE npc_instance_id=?", (gull,)).fetchone()
        assert {row["effect_key"] for row in mud_effects.list_effects(db, "encounter", encounter["id"])} == {
            mud_worlds.bundled_key(WORLD, "effects", "searing-light")}

        # A quest given in dialogue, finished by combat and loot, turned in for coins and standing.
        _move(db, uid, "Lantern Harbor")
        quest = _global(db, "mud_quests", "Sea Glass")
        mud._accept_quest(db, user, quest)
        _move(db, uid, "Wind Cliffs")
        for _ in range(30):
            row = db.execute("SELECT status FROM mud_combat_encounters WHERE id=?", (encounter["id"],)).fetchone()
            if row["status"] == "defeated":
                break
            mud._attack_action(db, user, {"arguments": ["Storm Gull"]})
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE owner_character_id=? AND definition_id=?",
                          (character["id"], _global(db, "mud_item_definitions", "Sea Glass"))).fetchone()
        _move(db, uid, "Lantern Harbor")
        coins = mud._wallet(db, character["id"])
        mud._turn_in_quest(db, user, quest, _global(db, "mud_npc_definitions", "Old Keeper"))
        assert mud._wallet(db, character["id"]) == coins + 25
        assert db.execute("SELECT reputation FROM mud_character_reputation WHERE character_id=?",
                          (character["id"],)).fetchone()["reputation"] == 50

        # The merchant sells the world's items, including a weapon that applies a bundled effect.
        _move(db, uid, "Night Market")
        coins = mud._wallet(db, character["id"])
        mud._shop_action(db, user, {"arguments": ["buy", "Flare", "Oil"]})
        assert mud._wallet(db, character["id"]) < coins
        hook = db.execute("SELECT properties_json FROM mud_item_definitions WHERE world_id=? AND name='Searing Hook'",
                          (WORLD,)).fetchone()
        assert json.loads(hook["properties_json"])["on_hit_effects"][0]["key"] == \
            mud_worlds.bundled_key(WORLD, "effects", "searing-light")

        # An event applies a bundled regen effect and sets a flag.
        _move(db, uid, "Tide Shrine")
        mud._entry_triggers(db, user)
        assert any(row["effect_key"] == mud_worlds.bundled_key(WORLD, "effects", "tide-blessing")
                   for row in mud_effects.list_effects(db, "character", character["id"]))

        # The world dungeon generates with remapped effects, tuned scaling, and a scaled boss.
        row = mud_world_dungeons.world_templates(db, WORLD)[0]
        template = mud_world_dungeons.effective_template(row)
        manifest = dungeons.generate_manifest(template, "seed-1", database=db)
        enemies = [entry["snapshot"] for room in manifest["rooms"] for entry in room["content"]
                   if entry["type"] == "enemy"]
        crab = next(snapshot for snapshot in enemies if snapshot["name"] == "Lantern Crab")
        assert json.loads(crab["properties_json"])["on_hit_effects"][0]["key"] in mud_effects.catalog(db)
        king = next(snapshot for snapshot in enemies if snapshot["name"] == "Tide King")
        assert king["boss"] is True and king["maximum_health"] >= 60
        db.commit()


def test_export_is_stable_and_keeps_imported_ids(stations):
    app = stations()
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, mud_worlds.example_bytes())
        first = _files(mud_worlds.export(db, WORLD))
        second = _files(mud_worlds.export(db, WORLD))
    assert first == second
    assert {room["id"] for room in first["rooms.json"]} == {"harbor", "market", "shrine", "cliffs"}
    assert {row["id"] for row in first["content.json"]["mud_quests"]} == {"glass-hunt"}
    assert first["manifest.json"]["progression"] == {"level_cap": 20, "experience_percent": 150,
                                                     "starting_coins": 50}


def test_legacy_format_one_package_installs_with_warnings(stations):
    payload = mud_worlds.package_bytes({
        "id": "old-pier", "name": "Old Pier", "author": "Builder", "start_room": "pier",
        "rooms": [{"id": "pier", "name": "Pier", "description": "Boards creak."}], "exits": []})
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    assert "format_version" not in manifest
    manifest["legacy_note"] = "ignored by older stations"
    files["manifest.json"] = json.dumps(manifest).encode()
    rebuilt = BytesIO()
    with zipfile.ZipFile(rebuilt, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    app = stations()
    with app.app_context():
        db = get_db()
        document, report = mud_worlds.validate_install(db, rebuilt.getvalue())
        assert document["format_version"] == 1
        assert [problem["severity"] for problem in report.problems] == ["warning"]
        mud_worlds.install(db, rebuilt.getvalue())
        assert db.execute("SELECT format_version FROM mud_world_packages WHERE id='old-pier'").fetchone()[0] == 1


def _rebuilt_example(**changes):
    files = _files(mud_worlds.example_bytes())
    for name, edit in changes.items():
        edit(files[name])
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, json.dumps(data))
    return stream.getvalue()


def test_format_two_reports_every_problem_with_its_category(stations):
    def manifest(data):
        data["max_level"] = 99
        data["progression"] = {"level_cap": 80}

    def content(data):
        data["mud_item_definitions"][0]["properties_json"]["on_use_effects"] = [{"key": "no-such-effect"}]
        data["mud_item_definitions"][1]["sparkle"] = True

    def origins(data):
        data["classes"][0]["spells"].append("missing-ability")

    def effects(data):
        data["effects"].append({"key": "poison", "name": "Poison Two", "kind": "damage"})
        data["effects"].append({"key": "glow", "name": "Glow", "kind": "light"})

    app = stations()
    with app.app_context():
        with pytest.raises(mud_worlds.WorldError) as raised:
            mud_worlds.read_package(_rebuilt_example(**{"manifest.json": manifest, "content.json": content,
                                                        "origins.json": origins, "effects.json": effects}),
                                    get_db())
    problems = raised.value.report.errors
    found = {(problem["file"], problem["category"]) for problem in problems}
    assert ("manifest.json", "engine_reserved") in found
    assert ("origins.json", "missing_reference") in found
    assert ("effects.json", "engine_reserved") in found
    assert ("content.json", "unsupported") in found
    assert any(problem["path"].endswith("on_use_effects[0]") for problem in problems)
    assert any("level_cap" in problem["message"] for problem in problems)
    assert len(problems) >= 6


def test_newer_format_names_the_station_version(stations):
    app = stations()
    with app.app_context():
        with pytest.raises(mud_worlds.WorldError, match="needs a newer StationNet"):
            mud_worlds.read_package(_rebuilt_example(**{"manifest.json": lambda data: data.update(format_version=9)}))


def test_shared_definitions_reuse_conflict_and_survive_removal(stations):
    app = stations()
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, mud_worlds.example_bytes())
        flare = mud_worlds.bundled_key(WORLD, "abilities", "lantern-flare")
        light = mud_worlds.bundled_key(WORLD, "effects", "searing-light")
        assert db.execute("SELECT 1 FROM mud_world_abilities WHERE world_id=? AND ability_key=?",
                          (WORLD, flare)).fetchone()
        # Effect still burning on someone: removal keeps the definition for them.
        db.execute("INSERT INTO mud_active_effects(target_type,target_id,effect_key,potency,remaining_turns,source) "
                   "VALUES('character','someone',?,1,3,'test')", (light,))
        db.commit()
        result = mud_worlds.remove(db, WORLD, remove_origins=True)
        assert flare in result["magic"]["abilities"]
        assert db.execute("SELECT installed FROM mud_effect_definitions WHERE key=?", (light,)).fetchone()[0] == 0
        assert mud_effects.display_name(db, light) == "Searing Light"
        # Reinstalling the same package reinstates the retained effect unchanged.
        mud_worlds.install(db, mud_worlds.example_bytes())
        assert db.execute("SELECT installed FROM mud_effect_definitions WHERE key=?", (light,)).fetchone()[0] == 1


def test_progression_bounds_and_procedural_defaults(stations):
    with pytest.raises(mud_progression.ProgressionError, match="engine-owned"):
        mud_progression.validate({"max_level": 60})
    with pytest.raises(mud_progression.ProgressionError, match="from 1 to 50"):
        mud_progression.validate({"level_cap": 51})
    app = stations()
    with app.app_context():
        assert mud_progression.for_world(get_db(), "procedural") == mud_progression.DEFAULTS


def test_preview_lists_problems_and_import_route_installs(stations):
    app = stations()
    with app.app_context():
        db = get_db()
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                           "VALUES('sysop','SYS','x',1)").lastrowid
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = admin
        session["csrf_token"] = "t"
    broken = _rebuilt_example(**{"origins.json": lambda data: data["classes"][0]["spells"].append("nope")})
    preview = client.post("/sysop/settings/mud-worlds/preview", data={
        "csrf_token": "t", "world_file": (BytesIO(broken), "world.zip")})
    assert preview.status_code == 400
    assert any(problem["path"].endswith("spells[3]") for problem in preview.json["problems"])
    good = client.post("/sysop/settings/mud-worlds/preview", data={
        "csrf_token": "t", "world_file": (BytesIO(mud_worlds.example_bytes()), "world.zip")})
    assert good.status_code == 200 and good.json["upgrade"] is None
    assert set(good.json["effects"]) == {"Searing Light", "Tide Blessing", "Brine Chill"}
    client.post("/sysop/settings/mud-worlds/import", data={
        "csrf_token": "t", "world_file": (BytesIO(mud_worlds.example_bytes()), "world.zip")})
    with app.app_context():
        assert get_db().execute("SELECT 1 FROM mud_world_packages WHERE id=?", (WORLD,)).fetchone()
    again = client.post("/sysop/settings/mud-worlds/preview", data={
        "csrf_token": "t", "world_file": (BytesIO(mud_worlds.example_bytes()), "world.zip")})
    assert again.json["upgrade"]["id"] == WORLD and again.json["upgrade"]["changes"] == {}


def test_builder_definitions_travel_with_the_world(stations):
    app = stations()
    with app.app_context():
        db = get_db()
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                           "VALUES('sysop','SYS','x',1)").lastrowid
        db.commit()
        mud_worlds.install(db, mud_worlds.template_bytes())
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = admin
        session["csrf_token"] = "t"
    for kind, definition in (
            ("effects", {"key": "static", "name": "Static", "kind": "accuracy", "sign": -1}),
            ("abilities", {"key": "zap", "name": "Zap", "target": "enemy", "cost": 3, "damage": 4,
                           "effects": [{"key": "static", "potency": 2, "duration": 2}]}),
            ("classes", {"key": "tinkerer", "name": "Tinkerer", "description": "Makers of sparks.",
                         "traits": "Sparks", "skill": "guard", "spells": ["zap"]})):
        response = client.post("/mud/w/my-world/builder/definitions", data={
            "csrf_token": "t", "kind": kind, "definition_json": json.dumps(definition)}, follow_redirects=True)
        assert b"Saved" in response.data, response.data[-2000:]
    page = client.get("/mud/w/my-world/builder").data
    assert b"definitions-heading" in page and b"tinkerer" in page
    with app.app_context():
        db = get_db()
        assert mud_worlds.bundled_key("my-world", "classes", "tinkerer") in \
            character_creation.world_availability(db, "my-world")["classes"]
        files = _files(mud_worlds.export(db, "my-world"))
    assert files["origins.json"]["classes"][0]["spells"] == ["zap"]
    assert files["abilities.json"]["abilities"][0]["effects"][0]["key"] == "static"

    other = stations()
    with other.app_context():
        db = get_db()
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, json.dumps(data))
        payload = stream.getvalue()
        mud_worlds.install(db, payload)
        tinkerer = mud.character_creation.catalog(db, world="")["classes"]
        granted = next(entry for entry in tinkerer if entry["definition"]["name"] == "Tinkerer")
        zap = granted["definition"]["spells"][0]
        assert mud.mud_abilities.catalog(db)[zap]["effects"][0]["key"] == mud_worlds.bundled_key(
            "my-world", "effects", "static")
        # Import then export again changes nothing.
        assert _files(mud_worlds.export(db, "my-world")) == files


def test_builder_item_edit_keeps_properties_the_form_does_not_show(stations):
    app = stations()
    with app.app_context():
        db = get_db()
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                           "VALUES('sysop','SYS','x',1)").lastrowid
        db.commit()
        mud_worlds.install(db, mud_worlds.example_bytes())
        glass = _global(db, "mud_item_definitions", "Sea Glass")
        db.execute("UPDATE mud_item_definitions SET properties_json=? WHERE id=?",
                   (json.dumps({"value": 2, "fixed": False, "state_descriptions": {"lit": "It glows."}}), glass))
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = admin
        session["csrf_token"] = "t"
    response = client.post(f"/mud/w/{WORLD}/builder/items/{glass}", data={
        "csrf_token": "t", "name": "Sea Glass", "description": "Polished glass.", "item_type": "misc",
        "value": "4"}, follow_redirects=True)
    assert b"Item definition updated" in response.data
    with app.app_context():
        properties = json.loads(get_db().execute("SELECT properties_json FROM mud_item_definitions WHERE id=?",
                                                 (glass,)).fetchone()[0])
    assert properties == {"value": 4, "fixed": False, "state_descriptions": {"lit": "It glows."}}
