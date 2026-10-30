"""Authored worlds can place procedural dungeons behind entrances in their own rooms."""
from io import BytesIO
import json
from pathlib import Path
import shutil
import uuid
import zipfile

import pytest

from stationnet import mud_scope
from stationnet import create_app, dungeons, mud, mud_world_dungeons, mud_worlds
from stationnet.db import get_db


START = "stationnet:start"
CAVE = "world:room:cave-mouth"


def _base_template():
    path = Path(dungeons.__file__).with_name("dungeon_templates") / "generic-fantasy-dungeon.json"
    template = json.loads(path.read_text(encoding="utf-8"))
    return {key: value for key, value in template.items() if key not in ("id", "version")}


@pytest.fixture
def world():
    data_dir = Path(__file__).parent / f"_mud_world_dungeon_{uuid.uuid4().hex}"
    data_dir.mkdir()
    try:
        app = create_app({"TESTING": True, "DATA_DIR": str(data_dir)})
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled,"
                       "mud_procedural_enabled) VALUES('Test','Test','TST',1,1,1)")
            users = []
            for name, callsign, admin in (("sysop", "SYS", 1), ("ana", "ANA", 0),
                                          ("bo", "BOO", 0), ("cy", "CYY", 0)):
                user_id = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                                     "VALUES(?,?,'unused',?)", (name, callsign, admin)).lastrowid
                db.execute("UPDATE mud_characters SET creation_state=\'complete\' WHERE id=?", (mud._character_id(db, user_id),))
                users.append(user_id)
            db.execute("INSERT INTO mud_world_rooms(id,name,description,world_id) "
                       "VALUES(?,'Cave Mouth','Cold air rises.','stationnet')", (CAVE,))
            db.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) "
                       "VALUES('world:exit:to-cave',?,?,'cave','north')", (START, CAVE))
            template_id = mud_world_dungeons.save_template(db, "stationnet", "old-mine", "Old Mine",
                                                           _base_template())
            db.execute(
                "INSERT INTO mud_world_dungeon_entrances(id,world_id,room_id,return_room_id,template_id,"
                "name,difficulty,size,max_party) VALUES('world:dungeon-entrance:mine','stationnet',?,?,?,"
                "'old mine',1,8,2)", (CAVE, START, template_id),
            )
            for user_id in users:
                mud._set_player_location(db, user_id, "traditional", "world_room", CAVE)
            db.commit()
        yield app, users, template_id
    finally:
        coordinator = app.extensions["broadcast_coordinator"] if "app" in locals() else None
        if coordinator is not None:
            coordinator._closed = True
            coordinator._timer.cancel()
        shutil.rmtree(data_dir)


def _user(db, user_id):
    return db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def _enter(db, user_id, name="old mine"):
    events = mud._enter_dungeon_action(db, _user(db, user_id), {"arguments": [name]})
    run = mud._active_expedition(db, user_id)
    assert run is not None
    return run, events


def _clear_exit(db, run_id):
    exit_room = db.execute("SELECT room_key FROM mud_dungeon_rooms WHERE instance_id=? AND role='exit'",
                           (run_id,)).fetchone()["room_key"]
    db.execute("INSERT OR IGNORE INTO mud_dungeon_content_state(content_id,status) "
               "SELECT id,'defeated' FROM mud_dungeon_content WHERE instance_id=? AND content_type='enemy'",
               (run_id,))
    return exit_room


def _client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    return client


# Generation --------------------------------------------------------------

def test_world_template_validation_and_deterministic_layouts():
    template = {**_base_template(), "id": "w-test-mine", "version": "1"}
    assert mud_world_dungeons.template_errors(_base_template()) == []
    assert any("origins" in error for error in mud_world_dungeons.template_errors(
        {**_base_template(), "origins": {"races": [{"key": "elf"}]}}))
    assert mud_world_dungeons.template_errors({"name": "Broken"})
    for layout in sorted(dungeons.SUPPORTED_LAYOUTS):
        first = dungeons.generate_manifest(template, "seed-a", size=12, layout=layout, difficulty=2)
        assert dungeons.generate_manifest(template, "seed-a", size=12, layout=layout, difficulty=2) == first
        dungeons.validate_manifest(first)
        assert first["layout"] == layout and len(first["rooms"]) == 12
        roles = {room["role"] for room in first["rooms"]}
        assert {"entrance", "exit", "boss"} <= roles
    designs = {json.dumps(dungeons.generate_manifest(template, seed)["exits"]) for seed in ("a", "b", "c", "d")}
    assert len(designs) > 1


def test_each_entry_generates_a_separate_instance(world):
    app, (_sysop, ana, bo, _cy), template_id = world
    with app.app_context():
        db = get_db()
        first, events = _enter(db, ana)
        second, _ = _enter(db, bo)
        assert first["id"] != second["id"] and first["seed"] != second["seed"]
        assert "Party code" in events[0].body
        assert first["world_entrance_id"] == "world:dungeon-entrance:mine"
        assert first["return_room_id"] == START and first["party_limit"] == 2
        template = db.execute("SELECT theme_id FROM mud_world_dungeon_templates WHERE id=?",
                              (template_id,)).fetchone()
        assert first["theme_id"] == template["theme_id"] and first["theme_version"] == "1"
        assert mud._current_room(db, ana)["instance_id"] == first["id"]
        # Each explorer sees only their own run.
        assert mud._room_occupants(db, mud._current_room(db, ana)["id"], ana, first["id"]) == []


# Transitions and progression ----------------------------------------------

def test_authored_world_dungeon_exit_indicator_is_local_to_exit_room(world):
    app, (_sysop, ana, _bo, _cy), _template_id = world
    with app.app_context():
        db = get_db()
        run, _ = _enter(db, ana)
        user = _user(db, ana)
        entrance = mud._room_state(db, ana)
        assert entrance["mode"] == "traditional"
        assert entrance["is_dungeon_exit"] is False
        assert "/complete" not in entrance["instructions"]
        assert "[EXIT]" not in mud._look_action(db, user, {})[0].body

        exit_room = db.execute("SELECT room_key FROM mud_dungeon_rooms WHERE instance_id=? AND role='exit'",
                               (run["id"],)).fetchone()[0]
        mud._set_player_location(db, ana, "traditional", "dungeon_instance", exit_room, run["id"])
        mud._record_expedition_visit(db, run["id"], ana, exit_room)
        room = mud._room_state(db, ana)
        assert room["mode"] == "traditional"
        assert room["is_dungeon_exit"] is True
        assert "use /complete to finish" in room["instructions"]
        look = mud._look_action(db, user, {})[0].body
        assert look.startswith(f"{room['name']} [EXIT]\n")
        assert "use /complete to finish" in look
        db.commit()

    page = _client(app, ana).get("/mud/w/stationnet")
    assert page.status_code == 200
    assert b'data-mud-room-name>' in page.data and b' [EXIT]</span>' in page.data
    assert b'data-mud-scene-name>' in page.data and b' [EXIT]</h2>' in page.data


def test_completion_returns_to_configured_room_on_the_world_track(world):
    app, (_sysop, ana, _bo, _cy), _template_id = world
    with app.app_context():
        db = get_db()
        look = mud._look_action(db, _user(db, ana), {})[0].body
        assert "ENTER old mine" in look
        assert any(entry.get("dungeon") for entry in mud._room_state(db, ana)["exits"])
        assert "/enter old mine" in mud._examine_dungeon_entrance(db, _user(db, ana),
                                                                  mud._current_room(db, ana), "old mine")
        before = mud.character_state(db, ana, "traditional")["experience"]
        run, _ = _enter(db, ana)
        assert mud._current_mode(db, ana) == "traditional"
        loot = mud._grant_item(db, ana, "stationnet:treasure:relic")
        possession = db.execute("SELECT possession_mode,acquired_run_id FROM mud_item_instances WHERE id=?",
                                (loot,)).fetchone()
        assert tuple(possession) == ("traditional", run["id"])
        with pytest.raises(mud.ActionError):
            mud.actions.from_command("/world")
        exit_room = _clear_exit(db, run["id"])
        mud._set_player_location(db, ana, "traditional", "dungeon_instance", exit_room, run["id"])
        mud._record_expedition_visit(db, run["id"], ana, exit_room)
        mud._complete_expedition_action(db, _user(db, ana), {})
        assert mud._current_room(db, ana)["id"] == START
        assert mud.character_state(db, ana, "traditional")["experience"] > before
        procedural = db.execute("SELECT experience FROM mud_character_progression WHERE character_id=? "
                                "AND track_key='procedural'", (f"stationnet:character:{ana}",)).fetchone()
        assert procedural is None or procedural[0] == 0
        assert db.execute("SELECT acquired_run_id FROM mud_item_instances WHERE id=?", (loot,)).fetchone()[0] is None
        # Completing twice cannot pay twice.
        with pytest.raises(mud.ActionError):
            mud._complete_expedition_action(db, _user(db, ana), {})
        assert db.execute("SELECT status FROM mud_procedural_instances WHERE id=?",
                          (run["id"],)).fetchone()[0] == "completed"


def test_abandon_and_death_lose_run_loot_and_return_to_world(world):
    app, (_sysop, ana, _bo, _cy), _template_id = world
    with app.app_context():
        db = get_db()
        kept = mud._grant_item(db, ana, "stationnet:shop:blade")
        run, _ = _enter(db, ana)
        lost = mud._grant_item(db, ana, "stationnet:treasure:gem")
        mud._abandon_expedition_action(db, _user(db, ana), {})
        assert mud._current_room(db, ana)["id"] == START
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (lost,)).fetchone() is None
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (kept,)).fetchone() is not None

        mud._set_player_location(db, ana, "traditional", "world_room", CAVE)
        run, _ = _enter(db, ana)
        doomed = mud._grant_item(db, ana, "stationnet:treasure:gem")
        experience = mud.character_state(db, ana, "traditional")["experience"]
        events = mud._defeat_player(db, _user(db, ana), {"scope_type": "instance", "scope_key": run["id"]},
                                    {"name": "Cave troll"})
        assert f"/continue to return to {mud._start_room(db)['name']}" in events[0].body
        assert mud._current_room(db, ana)["id"] == "stationnet:death-summary"
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (doomed,)).fetchone() is None
        assert mud.character_state(db, ana, "traditional")["experience"] == experience
        mud._continue_action(db, _user(db, ana), {})
        assert mud._current_room(db, ana)["id"] == START
        assert mud._active_expedition(db, ana) is None


def test_entrance_conditions_and_disabled_entrances_block_entry(world):
    app, (_sysop, ana, _bo, _cy), _template_id = world
    with app.app_context():
        db = get_db()
        db.execute("UPDATE mud_world_dungeon_entrances SET conditions_json=?,conditions_message=?",
                   (json.dumps([{"type": "level", "min": 40}]), "Only veterans may descend."))
        with pytest.raises(mud.ActionError, match="Only veterans"):
            _enter(db, ana)
        db.execute("UPDATE mud_world_dungeon_entrances SET conditions_json='[]',enabled=0")
        with pytest.raises(mud.ActionError):
            _enter(db, ana)
        assert mud._active_expedition(db, ana) is None


# Multiplayer --------------------------------------------------------------

def test_party_shares_one_instance_and_respects_the_limit(world):
    app, (_sysop, ana, bo, cy), _template_id = world
    with app.app_context():
        db = get_db()
        run, _ = _enter(db, ana)
        mud._join_expedition_action(db, _user(db, bo), {"arguments": [run["join_code"]]})
        assert mud._active_expedition(db, bo)["id"] == run["id"]
        with pytest.raises(mud.ActionError, match="full"):
            mud._join_expedition_action(db, _user(db, cy), {"arguments": [run["join_code"]]})
        # Portal and world runs keep their own doors: the dungeon world cannot join a world run.
        with mud_scope.use(mud_scope.PROCEDURAL):
            _dungeon_character(db, cy)
            with pytest.raises(mud.ActionError):
                mud._join_expedition_action(db, _user(db, cy), {"arguments": [run["join_code"]]})
        assert {row[0] for row in db.execute(
            "SELECT instance_id FROM mud_player_locations WHERE user_id IN (?,?)", (ana, bo))} == {run["id"]}
        # A shared floor item can be claimed only once.
        exit_room = _clear_exit(db, run["id"])
        item = db.execute("SELECT c.id,c.room_key FROM mud_dungeon_content c WHERE c.instance_id=? "
                          "AND c.content_type='item' LIMIT 1", (run["id"],)).fetchone()
        if item is not None:
            for user_id in (ana, bo):
                mud._set_player_location(db, user_id, "traditional", "dungeon_instance", item["room_key"], run["id"])
            mud._get_action(db, _user(db, ana), {"item_id": item["id"]})
            with pytest.raises(mud.ActionError):
                mud._get_action(db, _user(db, bo), {"item_id": item["id"]})
        for user_id in (ana, bo):
            mud._set_player_location(db, user_id, "traditional", "dungeon_instance", exit_room, run["id"])
        mud._complete_expedition_action(db, _user(db, ana), {})
        assert mud._current_room(db, ana)["id"] == START and mud._current_room(db, bo)["id"] == START
        assert {row[0] for row in db.execute(
            "SELECT status FROM mud_expedition_participants WHERE instance_id=?", (run["id"],))} == {"completed"}


def test_portal_codes_cannot_be_joined_from_world_entrances(world):
    app, (_sysop, ana, bo, _cy), _template_id = world
    with app.app_context():
        db = get_db()
        with mud_scope.use(mud_scope.PROCEDURAL):
            _dungeon_character(db, ana)
            mud._expedition_action(db, _user(db, ana), {"theme_id": "generic-fantasy-dungeon",
                                                        "difficulty": 1, "size": 8})
            code = mud._active_expedition(db, ana)["join_code"]
            run_id = mud._active_expedition(db, ana)["id"]
        with pytest.raises(mud.ActionError, match="Dungeon Portals"):
            mud._join_expedition_action(db, _user(db, bo), {"arguments": [code]})
        assert mud._world_run(db, run_id) is None
        # The authored-world character has no part in the dungeon world's run.
        assert mud._active_expedition(db, ana) is None


# Persistence ---------------------------------------------------------------

def test_runs_survive_disconnect_restart_and_displacement(world):
    app, (_sysop, ana, _bo, _cy), _template_id = world
    with app.app_context():
        db = get_db()
        run, _ = _enter(db, ana)
        room_key = mud._current_room(db, ana)["id"]
        db.execute("INSERT INTO mud_presence(world_key,user_id,connection_id,client_id,location_type,location_key,"
                   "instance_id,last_seen,disconnected_at) VALUES(mud_world(),?,'c','client','dungeon_instance',?,?,"
                   "'2000-01-01T00:00:00+00:00','2000-01-01T00:00:00+00:00')", (ana, room_key, run["id"]))
        mud._prune_presence(db)
        db.commit()
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT 1 FROM mud_presence WHERE user_id=?", (ana,)).fetchone() is None
        assert mud._current_room(db, ana)["instance_id"] == run["id"]
        # Disabling the world leaves the run waiting; a displaced explorer can /resume it.
        mud_worlds.set_enabled(db, "stationnet", False)
        assert mud._active_expedition(db, ana)["id"] == run["id"]
        mud_worlds.set_enabled(db, "stationnet", True)
        mud._set_player_location(db, ana, "traditional", "world_room", START)
        mud._resume_expedition_action(db, _user(db, ana), {})
        assert mud._current_room(db, ana)["id"] == room_key
        assert mud._current_room(db, ana)["instance_id"] == run["id"]


def test_other_worlds_leave_runs_alone_and_removal_ends_them(world):
    app, (sysop, ana, _bo, _cy), _template_id = world
    with app.app_context():
        db = get_db()
        run, _ = _enter(db, ana)
        loot = mud._grant_item(db, ana, "stationnet:treasure:gem")
        db.commit()
    package = mud_worlds.package_bytes({
        "id": "harbor", "name": "Harbor", "author": "Builder", "start_room": "dock",
        "rooms": [{"id": "dock", "name": "Dock", "description": "Water."}], "exits": []})
    with app.app_context():
        db = get_db()
        # Another world opening beside this one changes nothing here.
        mud_worlds.install(db, package, enable=True)
        assert db.execute("SELECT status FROM mud_procedural_instances WHERE id=?", (run["id"],)).fetchone()[0] == "active"
        # Only removing the run's own world ends it, losing the run loot.
        mud.end_world_runs(db, "stationnet", "This world was removed from the station.")
        assert db.execute("SELECT status,terminal_reason FROM mud_procedural_instances WHERE id=?",
                          (run["id"],)).fetchone()[:] == ("abandoned", "world_removed")
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (loot,)).fetchone() is None
        assert mud._current_room(db, ana)["id"] == START


def _dungeon_character(db, user_id):
    """The account's finished character in the procedural dungeon world, standing in the portals."""
    db.execute("UPDATE mud_characters SET creation_state='complete' WHERE id=?", (mud._character_id(db, user_id),))
    mud._set_player_location(db, user_id, "procedural", "portal", mud.PORTAL_ROOM_ID)


# Builder and packages ----------------------------------------------------

def test_builder_routes_create_preview_and_guard_templates(world):
    app, (sysop, ana, _bo, _cy), template_id = world
    client = _client(app, sysop)
    created = client.post("/mud/w/stationnet/builder/dungeons", data={
        "csrf_token": "test-token", "local_key": "ice-caves", "name": "Ice Caves",
        "base": "theme:frozen-frontier", "size_minimum": "6", "size_default": "8", "size_maximum": "12",
        "difficulty_minimum": "1", "difficulty_default": "2", "difficulty_maximum": "5",
        "layout_linear": "1", "layout_maze": "3"}, follow_redirects=True)
    assert created.status_code == 200 and b"Dungeon template created" in created.data
    invalid = client.post("/mud/w/stationnet/builder/dungeons", data={
        "csrf_token": "test-token", "local_key": "broken", "template_json": '{"name": "Broken"}'},
        follow_redirects=True)
    assert b"Dungeon template is invalid" in invalid.data
    with app.app_context():
        db = get_db()
        row = db.execute("SELECT * FROM mud_world_dungeon_templates WHERE local_key='ice-caves'").fetchone()
        stored = json.loads(row["template_json"])
        assert stored["generation"]["size"] == {"minimum": 6, "default": 8, "maximum": 12}
        assert [entry["type"] for entry in stored["generation"]["layouts"]] == ["linear", "maze"]
    entrance = client.post("/mud/w/stationnet/builder/entrances", data={
        "csrf_token": "test-token", "room_id": START, "return_room_id": CAVE, "template_id": row["id"],
        "name": "ice crack", "size": "10", "layout": "maze", "max_party": "3", "enabled": "1"},
        follow_redirects=True)
    assert b"Dungeon entrance ice crack created" in entrance.data
    out_of_range = client.post("/mud/w/stationnet/builder/entrances", data={
        "csrf_token": "test-token", "room_id": START, "template_id": row["id"], "name": "deep crack",
        "size": "99", "enabled": "1"}, follow_redirects=True)
    assert b"Room count must be blank" in out_of_range.data
    page = client.get(f"/mud/w/stationnet/builder?preview={row['id']}&seed=fixed")
    assert page.status_code == 200 and b"seed=fixed" in page.data and b"ice crack" in page.data
    blocked = client.post(f"/mud/w/stationnet/builder/dungeons/{row['id']}/delete",
                          data={"csrf_token": "test-token", "confirmation": "delete"}, follow_redirects=True)
    assert b"Remove or reassign the entrances" in blocked.data
    # Editing bumps the revision; a running instance keeps the version it was built from.
    with app.app_context():
        db = get_db()
        run, _ = _enter(db, ana)
        db.commit()
    template_json = json.dumps({**stored, "description": "Now colder."})
    client.post(f"/mud/w/stationnet/builder/dungeons/{row['id']}", data={
        "csrf_token": "test-token", "local_key": "ice-caves", "name": "Ice Caves",
        "template_json": template_json}, follow_redirects=True)
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT revision FROM mud_world_dungeon_templates WHERE id=?",
                          (row["id"],)).fetchone()[0] == 2
        assert mud._active_expedition(db, ana)["theme_version"] == "1"
    assert _client(app, ana).post("/mud/w/stationnet/builder/dungeons", data={"csrf_token": "test-token"}).status_code == 403


def test_world_export_and_import_round_trip_dungeons(world):
    app, (_sysop, _ana, _bo, _cy), template_id = world
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO mud_item_definitions(id,name,description,item_type,world_id) "
                   "VALUES('world:item-definition:lantern','Miner lantern','Still warm.','misc','stationnet')")
        template = json.loads(db.execute("SELECT template_json FROM mud_world_dungeon_templates WHERE id=?",
                                         (template_id,)).fetchone()[0])
        template["pools"]["items"].append({"id": "lanterns", "chance": 1, "count": {"minimum": 1, "maximum": 1},
                                           "choices": [{"definition_id": "world:item-definition:lantern"}]})
        mud_world_dungeons.save_template(db, "stationnet", "old-mine", "Old Mine", template, template_id)
        second = mud_world_dungeons.save_template(db, "stationnet", "crypt", "Crypt", _base_template())
        db.execute("INSERT INTO mud_world_dungeon_entrances(id,world_id,room_id,template_id,name,"
                   "conditions_json) VALUES('world:dungeon-entrance:crypt','stationnet',?,?,'crypt door',?)",
                   (START, second, json.dumps([{"type": "level", "min": 2}])))
        db.commit()
        payload = mud_worlds.export(db, "stationnet")
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        exported = json.loads(archive.read("dungeons.json"))
    assert {entry["key"] for entry in exported["templates"]} == {"old-mine", "crypt"}
    mine = next(entry for entry in exported["templates"] if entry["key"] == "old-mine")
    lantern_choice = mine["template"]["pools"]["items"][-1]["choices"][0]["definition_id"]
    assert lantern_choice.startswith("item-")
    assert {entry["name"] for entry in exported["entrances"]} == {"old mine", "crypt door"}

    other = Path(__file__).parent / f"_mud_world_dungeon_{uuid.uuid4().hex}"
    other.mkdir()
    try:
        fresh = create_app({"TESTING": True, "DATA_DIR": str(other)})
        with fresh.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                       "VALUES('Fresh','Fresh','FRS',1,1)")
            db.commit()
            for _ in range(2):
                document = mud_worlds.read_package(payload)
                document_id = f"copy-{uuid.uuid4().hex[:6]}"
                manifest_payload = _renamed(payload, document_id)
                mud_worlds.install(db, manifest_payload)
            rows = db.execute("SELECT * FROM mud_world_dungeon_templates WHERE world_id LIKE 'copy-%'").fetchall()
            assert len(rows) == 4 and len({row["theme_id"] for row in rows}) == 4
            imported = next(row for row in rows if row["local_key"] == "old-mine")
            choice = json.loads(imported["template_json"])["pools"]["items"][-1]["choices"][0]["definition_id"]
            assert choice.startswith(f"world:{imported['world_id']}:") and db.execute(
                "SELECT 1 FROM mud_item_definitions WHERE id=?", (choice,)).fetchone()
            entrance = db.execute("SELECT * FROM mud_world_dungeon_entrances WHERE world_id=? AND name='crypt door'",
                                  (imported["world_id"],)).fetchone()
            assert json.loads(entrance["conditions_json"]) == [{"type": "level", "min": 2}]
            assert document["dungeons"]["entrances"]
        coordinator = fresh.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()
    finally:
        shutil.rmtree(other)


def _renamed(payload, world_id, **dungeon_overrides):
    """Repackage a world under another ID, optionally replacing dungeons.json."""
    source = zipfile.ZipFile(BytesIO(payload))
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name in source.namelist():
            data = source.read(name)
            if name == "manifest.json":
                data = json.dumps({**json.loads(data), "id": world_id}).encode()
            if name == "dungeons.json" and dungeon_overrides:
                data = json.dumps({**json.loads(data), **dungeon_overrides}).encode()
            archive.writestr(name, data)
    return stream.getvalue()


def test_packages_reject_bad_dungeon_references(world):
    app, _users, _template_id = world
    base = {"id": "vault", "name": "Vault", "author": "Builder", "start_room": "hall",
            "rooms": [{"id": "hall", "name": "Hall", "description": "Echoes."}], "exits": []}
    with app.app_context():
        db = get_db()
        good = mud_worlds.package_bytes({**base, "dungeons": {
            "templates": [{"key": "vault", "template": _base_template()}],
            "entrances": [{"room": "hall", "template": "vault", "name": "trapdoor"}]}})
        assert mud_worlds.read_package(good)["dungeons"]["entrances"][0]["return_room"] == "hall"
        for dungeon_doc, message in (
            ({"templates": [{"key": "vault", "template": _base_template()}],
              "entrances": [{"room": "missing", "template": "vault", "name": "trapdoor"}]}, "rooms"),
            ({"templates": [{"key": "vault", "template": _base_template()}],
              "entrances": [{"room": "hall", "template": "nope", "name": "trapdoor"}]}, "template"),
            ({"templates": [{"key": "vault", "template": {"name": "Broken"}}], "entrances": []}, "vault"),
            ({"templates": [{"key": "vault", "template": {**_base_template(), "pools": {
                **_base_template()["pools"], "items": _base_template()["pools"]["items"] + [
                    {"id": "ghost", "choices": [{"definition_id": "does-not-exist"}]}]}}}],
              "entrances": []}, "does not exist"),
        ):
            with pytest.raises(mud_worlds.WorldError, match=message):
                mud_worlds.install(db, mud_worlds.package_bytes({**base, "dungeons": dungeon_doc}))
        assert db.execute("SELECT 1 FROM mud_world_packages WHERE id='vault'").fetchone() is None
        # Packages without dungeons.json still install unchanged.
        mud_worlds.install(db, mud_worlds.package_bytes(base))
        assert db.execute("SELECT COUNT(*) FROM mud_world_dungeon_templates WHERE world_id='vault'").fetchone()[0] == 0


@pytest.mark.mud_world(mud_scope.PROCEDURAL)
def test_migration_preserves_existing_portal_instances(world):
    app, (_sysop, ana, _bo, _cy), _template_id = world
    with app.app_context():
        db = get_db()
        mud._set_player_location(db, ana, "procedural", "portal", mud.PORTAL_ROOM_ID)
        mud._expedition_action(db, _user(db, ana), {"theme_id": "generic-fantasy-dungeon",
                                                    "difficulty": 1, "size": 8})
        run_id = mud._active_expedition(db, ana)["id"]
        db.commit()
        from stationnet.db import init_db
        init_db()
        db.commit()
        run = db.execute("SELECT * FROM mud_procedural_instances WHERE id=?", (run_id,)).fetchone()
        assert run["status"] == "active" and run["world_entrance_id"] is None
        assert mud._current_room(db, ana)["instance_id"] == run_id
