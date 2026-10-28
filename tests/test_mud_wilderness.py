"""Wilderness: room presets, grid rooms, computed exits, regions, fills, packages and the builder."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import random
import shutil
import time
import uuid
import zipfile

import pytest

from mud_world_helpers import activate_world, url
from stationnet import create_app, character_creation, mud, mud_wilderness, mud_worlds
from stationnet.db import get_db


def _preset(key, name, **fields):
    return {"key": key, "name": name, "descriptions": [f"{name} one.", f"{name} two.", f"{name} three.",
                                                       f"{name} four.", f"{name} five."], **fields}


def _document(world_id="wildworld"):
    return {
        "id": world_id, "name": "Wild World", "author": "Test", "start_room": "town",
        "rooms": [{"id": "town", "name": "Market Town", "description": "A busy town.", "x": 0, "y": 0, "z": 0},
                  {"id": "cave", "name": "Dark Cave", "description": "A damp cave."}],
        "exits": [{"from": "cave", "to": "@2,-1,0", "name": "cave mouth", "direction": "out"},
                  {"from": "@2,-1,0", "to": "cave", "name": "cave", "direction": "in"}],
        "content": {
            "mud_item_definitions": [{"id": "herb", "name": "Wild Herb", "description": "A herb.", "item_type": "misc"}],
            "mud_npc_definitions": [{"id": "wolf", "name": "Grey Wolf", "description": "A wolf."},
                                    {"id": "hermit", "name": "Hermit", "description": "A hermit."}],
            "mud_npc_combat_profiles": [{"npc_definition_id": "wolf", "maximum_health": 10, "attack": 1,
                                         "defense": 0, "experience_reward": 1, "respawn_seconds": 60}],
            "mud_npc_instances": [{"id": "hermit-1", "definition_id": "hermit", "room_id": "@-2,-2,0"}],
        },
        "wilderness": {
            "presets": [
                _preset("forest", "Forest", glyph="t", links="4",
                        encounters={"chance": 0, "max_live": 1, "despawn_seconds": 60,
                                    "creatures": [{"npc": "wolf", "min_danger": 3}]},
                        resources={"respawn_seconds": 600, "max_per_room": 1,
                                   "items": [{"item": "herb", "chance": 100}]}),
                _preset("desert", "Desert", glyph=":", links="8", terrain="desert"),
            ],
            "regions": [{"key": "wolfwood", "name": "Wolf Wood",
                         "overrides": {"danger": 5, "encounter_chance": 100, "name_prefix": "Haunted"}}],
            "layers": [
                {"z": 0, "origin": [-2, -2], "width": 5, "height": 5, "palette": ["", "forest", "desert", "forest@wolfwood"],
                 "rows": ["5*1", "5*1", "2*1,0,2*1", "5*2", "5*3"]},
                {"z": 1, "origin": [1, 1], "width": 1, "height": 1, "palette": ["", "forest"], "rows": ["1"]},
            ],
            "cells": [{"x": -2, "y": -2, "z": 0, "blocked_dirs": "east"}],
        },
    }


def _package(document):
    return mud_worlds.package_bytes({**document, "wilderness": document["wilderness"]})


@pytest.fixture
def game(monkeypatch):
    data = Path(__file__).parent / f"_wilderness_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    monkeypatch.setattr(mud_wilderness, "_RNG", random.Random(7))
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        users = []
        for name, admin in (("one", 1), ("two", 0)):
            uid = db.execute("INSERT INTO users(username,callsign,password_hash,connection_id,is_admin) "
                             "VALUES(?,?,?,?,?)", (name, name.upper(), "unused", f"connection-{name}", admin)).lastrowid
            character_creation.complete(db, uid, {
                "name": name.title(), "race": "human", "class": "warrior",
                "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
                "skills": ["lore", "guard"]})
            users.append(uid)
        db.commit()
        mud_worlds.install(db, _package(_document()))
        activate_world(db, 'wildworld')
        for uid in users:
            _place(db, uid, _town(db))
        db.commit()
    yield app, users
    app.extensions["broadcast_coordinator"]._closed = True
    app.extensions["broadcast_coordinator"]._timer.cancel()
    shutil.rmtree(data)


def _town(db):
    return db.execute("SELECT id FROM mud_world_rooms WHERE world_id='wildworld' AND name='Market Town'").fetchone()["id"]


def _cell(x, y, z=0, world="wildworld"):
    return mud_wilderness.cell_id(world, x, y, z)


def _place(db, uid, room_id):
    mud._set_player_location(db, uid, "traditional", "world_room", room_id)
    db.execute("DELETE FROM mud_presence WHERE user_id=?", (uid,))
    db.execute("INSERT INTO mud_presence(world_key,user_id,connection_id,client_id,room_id,location_type,location_key,last_seen) "
               "VALUES(mud_world(),?,?,?,?,?,?,?)", (uid, f"connection-{uid}", f"client-{uid}-1", room_id, "world_room", room_id,
                                         mud._scheduled_now()))


def _user(db, uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _go(db, uid, direction):
    drafts = mud._go_action(db, _user(db, uid), {"arguments": [direction]})
    room_id = mud._current_room(db, uid)["id"]
    _place(db, uid, room_id)
    return drafts


def _directions(db, room_id):
    return {row["direction"]: row["to_room_id"] for row in mud._room_exits(db, room_id)}


def _client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    return client


# Validation -----------------------------------------------------------------

def test_preset_validation_normalizes_and_refuses_bad_values():
    preset = mud_wilderness.validate_preset(_preset("pine", "Pine Wood", descriptions=["Pines.", {"text": "Firs.", "weight": 3}]))
    assert preset["descriptions"] == [{"text": "Pines.", "weight": 1}, {"text": "Firs.", "weight": 3}]
    assert preset["links"] == "4" and preset["encounters"]["chance"] == 0
    for bad in ({"key": "Bad Key"}, {"glyph": "@"}, {"links": "6"}, {"descriptions": []}, {"terrain": "lava"},
                {"surprise": 1}, {"encounters": {"chance": 101}},
                {"encounters": {"creatures": [{"npc": "wolf", "min_danger": 5, "max_danger": 2}]}}):
        with pytest.raises(mud_wilderness.WildernessError):
            mud_wilderness.validate_preset({**_preset("pine", "Pine Wood"), **bad})
    assert set(mud_wilderness.bundled_presets()) >= {"grassland", "forest", "dense-forest", "mountains", "desert", "swamp"}


def test_package_validation_rejects_bad_wilderness():
    document = _document()
    document["wilderness"]["layers"][0]["palette"].append("jungle")
    with pytest.raises(mud_worlds.WorldError, match="Palette"):
        mud_worlds.read_package(_package(document))
    document = _document()
    document["exits"][0]["to"] = "@9,9,0"
    with pytest.raises(mud_worlds.WorldError, match="connect rooms"):
        mud_worlds.read_package(_package(document))
    document = _document()
    document["rooms"][1].update(x=-2, y=-2, z=0)
    with pytest.raises(mud_worlds.WorldError, match="leave that square empty"):
        mud_worlds.read_package(_package(document))
    document = _document()
    document["wilderness"]["presets"][0]["encounters"]["creatures"][0]["npc"] = "dragon"
    with pytest.raises(mud_worlds.WorldError, match="unknown definitions"):
        mud_worlds.read_package(_package(document))


# Storage and movement -------------------------------------------------------

def test_install_creates_slim_cells_and_no_exit_rows(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM mud_world_rooms WHERE world_id='wildworld' AND kind='wild'").fetchone()[0] == 25
        assert db.execute("SELECT name,description FROM mud_world_rooms WHERE id=?", (_cell(1, 1),)).fetchone()[:] == ("", "")
        assert db.execute("SELECT COUNT(*) FROM mud_world_exits e JOIN mud_world_rooms r ON r.id=e.from_room_id "
                          "WHERE r.world_id='wildworld'").fetchone()[0] == 2
        hermit = db.execute("SELECT room_id FROM mud_npc_instances WHERE definition_id LIKE '%hermit'").fetchone()
        assert hermit["room_id"] == _cell(-2, -2)


def test_grid_links_follow_preset_link_modes(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        # The town is authored and permissive; diagonals reach only the eight-way desert.
        assert _directions(db, _town(db)) == {
            "north": _cell(0, -1), "east": _cell(1, 0), "south": _cell(0, 1), "west": _cell(-1, 0),
            "southeast": _cell(1, 1), "southwest": _cell(-1, 1)}
        assert set(_directions(db, _cell(0, -1))) == {"north", "east", "south", "west"}
        desert = _directions(db, _cell(1, 1))
        assert desert["northwest"] == _town(db) and desert["up"] == _cell(1, 1, 1)
        assert "northeast" not in desert and "southeast" not in desert and desert["east"] == _cell(2, 1)
        assert _directions(db, _cell(1, 1, 1)) == {"down": _cell(1, 1)}


def test_walls_and_stored_exits_override_grid_links(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        assert "east" not in _directions(db, _cell(-2, -2))
        assert "west" not in _directions(db, _cell(-1, -2))
        cave = db.execute("SELECT id FROM mud_world_rooms WHERE name='Dark Cave'").fetchone()["id"]
        assert _directions(db, _cell(2, -1))["in"] == cave
        db.execute("INSERT INTO mud_world_exits(id,from_room_id,to_room_id,name,direction) VALUES('x',?,?,?,?)",
                   (_town(db), cave, "trapdoor", "north"))
        assert _directions(db, _town(db))["north"] == cave


def test_players_walk_the_grid_and_meet(game):
    app, (one, two) = game
    with app.app_context():
        db = get_db()
        _go(db, one, "north")
        room = mud._current_room(db, one)
        assert room["id"] == _cell(0, -1) and room["name"] == "Forest"
        assert room["description"].startswith("Forest ")
        drafts = _go(db, two, "north")
        assert any("One" in draft.body for draft in drafts if draft.event_type == "room")
        _go(db, one, "east")
        _go(db, one, "west")
        assert mud._current_room(db, one)["id"] == _cell(0, -1)
        db.commit()


def test_descriptions_are_stable_per_room_and_vary_between_rooms(game):
    app, (one, _) = game
    with app.app_context():
        db = get_db()
        first = mud._world_room(db, _cell(0, -2))["description"]
        assert all(mud._world_room(db, _cell(0, -2))["description"] == first for _ in range(5))
        texts = {mud._world_room(db, _cell(x, y))["description"] for x in range(-2, 3) for y in (-2, -1)}
        assert len(texts) > 1
        assert mud._world_room(db, _cell(0, 2))["name"] == "Haunted Forest"


def test_region_encounters_respect_danger_and_live_cap(game):
    app, (one, _) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(0, 1))
        drafts = _go(db, one, "south")
        assert any(draft.body == "Grey Wolf appears." for draft in drafts)
        spawned = db.execute("SELECT id FROM mud_npc_instances WHERE spawned_by_trigger=?",
                             (f"wild:{_cell(0, 2)}",)).fetchall()
        assert len(spawned) == 1
        assert db.execute("SELECT 1 FROM mud_scheduled_events WHERE event_kind='npc_despawn' AND entity_id=?",
                          (spawned[0]["id"],)).fetchone()
        assert mud_wilderness.roll_encounter(db, mud._world_room(db, _cell(0, 2))) is None
        # Danger 1 forest never offers the danger-3 wolf, whatever the chance.
        db.execute("UPDATE mud_world_room_presets SET preset_json=json_set(preset_json,'$.encounters.chance',100),"
                   "revision=revision+1 WHERE local_key='forest'")
        assert mud_wilderness.roll_encounter(db, mud._world_room(db, _cell(0, -1))) is None


def test_resources_gather_once_until_they_renew(game):
    app, (one, _) = game
    with app.app_context():
        db = get_db()
        _go(db, one, "north")
        room = _cell(0, -1)
        herbs = lambda: db.execute("SELECT COUNT(*) FROM mud_item_instances WHERE room_id=? "
                                   "AND id LIKE 'world:wild-item:%'", (room,)).fetchone()[0]
        assert herbs() == 1
        _go(db, one, "south")
        _go(db, one, "north")
        assert herbs() == 1
        later = datetime.now(timezone.utc) + timedelta(hours=1)
        db.execute("DELETE FROM mud_item_instances WHERE room_id=?", (room,))
        assert mud_wilderness.roll_resources(db, mud._world_room(db, room), later) == ["Wild Herb"]


def test_map_command_draws_surroundings(game):
    app, (one, two) = game
    with app.app_context():
        db = get_db()
        _go(db, two, "north")
        text = mud._map_action(db, _user(db, one), {})[0].body
        lines = text.splitlines()
        assert lines[0].startswith("Map around Market Town (0, 0)")
        grid = lines[1:-1]
        assert grid[4][7] == "@" and grid[3][7] == "P" and grid[5][7] == ":"
        assert "t Forest" in lines[-1] and ": Desert" in lines[-1]
        cave = db.execute("SELECT id FROM mud_world_rooms WHERE name='Dark Cave'").fetchone()["id"]
        _place(db, one, cave)
        assert mud._map_action(db, _user(db, one), {})[0].body == "No map is available here."


def test_wandering_npcs_use_grid_exits(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        hermit = db.execute("SELECT id,room_id FROM mud_npc_instances WHERE definition_id LIKE '%hermit'").fetchone()
        assert {row["to_room_id"] for row in mud._room_exits(db, hermit["room_id"])} == {_cell(-2, -1)}


# Fill, clear, convert and placement -----------------------------------------

def test_fill_preserves_authored_rooms_and_replace_keeps_contents(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        swamp = mud_wilderness.save_preset(db, "wildworld", _preset("swamp", "Swamp", glyph="~"))
        shape = mud_wilderness.parse_shape({"shape": "rect", "x1": "-3", "y1": "-3", "x2": "3", "y2": "3", "z": "0"})
        result = mud_wilderness.fill(db, "wildworld", shape, swamp)
        # 49 squares: 24 already wild, one the authored town.
        assert result == {"created": 24, "updated": 0, "skipped_authored": 1}
        db.execute("INSERT INTO mud_item_instances(id,definition_id,room_id) VALUES('dropped',"
                   "(SELECT id FROM mud_item_definitions WHERE name='Wild Herb'),?)", (_cell(0, -1),))
        result = mud_wilderness.fill(db, "wildworld", shape, swamp, mode="replace")
        assert result["updated"] == 48 and result["created"] == 0
        assert mud._world_room(db, _cell(0, -1))["name"] == "Swamp"
        assert db.execute("SELECT room_id FROM mud_item_instances WHERE id='dropped'").fetchone()[0] == _cell(0, -1)
        ellipse = mud_wilderness.parse_shape({"shape": "ellipse", "x1": "10", "y1": "10", "x2": "14", "y2": "12", "z": "0"})
        assert mud_wilderness.fill(db, "wildworld", ellipse, swamp)["created"] == len(list(mud_wilderness.shape_cells(ellipse)))
        with pytest.raises(mud_wilderness.WildernessError):
            mud_wilderness.parse_shape({"shape": "rect", "x1": "0", "y1": "0", "x2": "1000", "y2": "1000", "z": "0"})


def test_clear_moves_players_and_spares_authored_rooms(game):
    app, (one, _) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(0, -2))
        shape = mud_wilderness.parse_shape({"shape": "rect", "x1": "-2", "y1": "-2", "x2": "2", "y2": "0", "z": "0"})
        room_ids = mud_wilderness.cells_in(db, "wildworld", shape)
        assert len(room_ids) == 14
        mud._move_room_occupants(db, room_ids, mud._start_room(db), "Moved to {name}.")
        mud_wilderness.delete_cells(db, room_ids)
        assert mud._current_room(db, one)["id"] == mud._start_room(db)["id"]
        assert db.execute("SELECT 1 FROM mud_world_rooms WHERE id=?", (_town(db),)).fetchone()


def test_convert_and_place_authored_rooms(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        before = mud._world_room(db, _cell(-1, -1))
        mud_wilderness.convert_to_authored(db, _cell(-1, -1))
        after = mud._world_room(db, _cell(-1, -1))
        assert after["kind"] == "authored" and after["description"] == before["description"]
        cave = db.execute("SELECT id FROM mud_world_rooms WHERE name='Dark Cave'").fetchone()["id"]
        hermit_room = _cell(-2, -2)
        absorbed = mud_wilderness.place_authored(db, cave, (-2, -2, 0))
        assert absorbed == hermit_room
        assert db.execute("SELECT room_id FROM mud_npc_instances WHERE definition_id LIKE '%hermit'").fetchone()[0] == cave
        assert _directions(db, cave)["south"] == _cell(-2, -1)
        with pytest.raises(mud_wilderness.WildernessError, match="already stands"):
            mud_wilderness.place_authored(db, cave, (0, 0, 0))


def test_large_fill_smoke(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        plains = mud_wilderness.save_preset(db, "wildworld", _preset("plains", "Plains"))
        shape = mud_wilderness.parse_shape({"shape": "rect", "x1": "100", "y1": "100", "x2": "299", "y2": "299", "z": "0"})
        started = time.monotonic()
        assert mud_wilderness.fill(db, "wildworld", shape, plains)["created"] == 40000
        assert time.monotonic() - started < 60
        assert set(_directions(db, _cell(200, 200))) == {"north", "east", "south", "west"}
        assert db.execute("EXPLAIN QUERY PLAN SELECT id FROM mud_world_rooms WHERE world_id=? AND z BETWEEN ? AND ? "
                          "AND y BETWEEN ? AND ? AND x BETWEEN ? AND ?", ("wildworld", -1, 1, 0, 2, 0, 2)
                          ).fetchall()[0]["detail"].find("idx_mud_world_rooms_grid") >= 0


# Packages -------------------------------------------------------------------

def test_export_round_trip_keeps_cells_references_and_presets(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        payload = mud_worlds.export(db, "wildworld")
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            wilderness = json.loads(archive.read("wilderness.json"))
            exits = json.loads(archive.read("exits.json"))
            rooms = json.loads(archive.read("rooms.json"))
            content = json.loads(archive.read("content.json"))
        assert [row["rows"] for row in wilderness["layers"]][0] == ["5*1", "5*1", "2*1,0,2*1", "5*2", "5*3"]
        assert {item["to"] for item in exits} >= {"@2,-1,0"}
        assert len(rooms) == 2 and rooms[[room["name"] for room in rooms].index("Market Town")]["x"] == 0
        assert content["mud_npc_instances"][0]["room_id"] == "@-2,-2,0"
        assert wilderness["cells"] == [{"x": -2, "y": -2, "z": 0, "blocked_dirs": "east"}]
        forest = next(preset for preset in wilderness["presets"] if preset["key"] == "forest")
        assert forest["encounters"]["creatures"][0]["npc"].startswith("npc-")
        assert forest["resources"]["items"][0]["item"].startswith("item-")
        reinstall = BytesIO()
        with zipfile.ZipFile(BytesIO(payload)) as source, zipfile.ZipFile(reinstall, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "manifest.json":
                    data = json.dumps({**json.loads(data), "id": "copyworld"}).encode()
                target.writestr(name, data)
        mud_worlds.install(db, reinstall.getvalue())
        assert db.execute("SELECT COUNT(*) FROM mud_world_rooms WHERE world_id='copyworld' AND kind='wild'").fetchone()[0] == 25
        copied = _cell(0, 2, world="copyworld")
        assert mud._world_room(db, copied)["name"] == "Haunted Forest"
        assert db.execute("SELECT blocked_dirs FROM mud_world_rooms WHERE id=?", (_cell(-2, -2, world="copyworld"),)).fetchone()[0] == "east"


# Builder --------------------------------------------------------------------

def test_builder_preset_region_fill_and_map_routes(game):
    app, (admin, player) = game
    client = _client(app, admin)
    page = client.get(url("/builder/wilderness"))
    assert page.status_code == 200 and b"Room presets" in page.data
    response = client.post(url("/builder/room-presets"), data={"csrf_token": "test-token", "base": "bundled:swamp",
                                                              "key": "bog", "name": "Peat Bog"}, follow_redirects=True)
    assert b"Room preset Peat Bog created" in response.data
    with app.app_context():
        db = get_db()
        bog = db.execute("SELECT id FROM mud_world_room_presets WHERE local_key='bog'").fetchone()["id"]
    response = client.post(url(f"/builder/room-presets/{bog}"), data={
        "csrf_token": "test-token", "key": "bog", "name": "Peat Bog", "glyph": "%", "terrain": "swamp", "links": "4",
        "light": "dim", "exposure": "open", "descriptions": "Peat.\n\n3| Bog water.", "encounter_chance": "5",
        "max_live": "1", "despawn_seconds": "60", "creatures": "Grey Wolf, 2, 1, 10", "respawn_seconds": "600",
        "max_per_room": "1", "resources": "Wild Herb, 50, 1, 2"}, follow_redirects=True)
    assert b"Room preset saved" in response.data
    response = client.post(url("/builder/regions"), data={"csrf_token": "test-token", "key": "fen", "name": "The Fen",
                                                         "danger": "4", "creatures": "Grey Wolf"}, follow_redirects=True)
    assert b"Region saved" in response.data
    response = client.post(url("/builder/wilderness/fill"), data={
        "csrf_token": "test-token", "shape": "rect", "x1": "-10", "y1": "5", "x2": "-6", "y2": "6", "z": "0",
        "preset_id": bog, "mode": "empty"}, follow_redirects=True)
    assert b"10 rooms created" in response.data
    data = client.get(url("/builder/wilderness/map?x=-8&y=5&z=0&w=5&h=3")).get_json()
    assert data["origin"] == [-10, 4] and data["palette"][data["grid"][1][0]]["key"] == "bog"
    inspector = client.get(url("/builder/wilderness?cell=-8,5,0"))
    assert b"Peat" in inspector.data and b"CONVERT TO AUTHORED ROOM" in inspector.data
    response = client.post(url(f"/builder/room-presets/{bog}/delete"), data={"csrf_token": "test-token",
                                                                             "confirmation": "delete"},
                           follow_redirects=True)
    assert b"Reassign them to another preset first" in response.data
    exported = client.get(url("/builder/room-presets/export"))
    assert {preset["key"] for preset in exported.get_json()["presets"]} >= {"forest", "bog"}
    upload = json.dumps({"key": "tundra", "name": "Tundra", "descriptions": ["Ice."],
                         "encounters": {"creatures": [{"npc": "missing:npc"}]}}).encode()
    response = client.post(url("/builder/room-presets/import"), data={
        "csrf_token": "test-token", "drop_unknown": "1", "preset_file": (BytesIO(upload), "tundra.json")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"1 created" in response.data
    response = client.post(url("/builder/wilderness/clear"), data={
        "csrf_token": "test-token", "shape": "rect", "x1": "-10", "y1": "5", "x2": "-6", "y2": "6", "z": "0",
        "confirm_clear": "1"}, follow_redirects=True)
    assert b"Removed 10 wilderness rooms" in response.data
    assert _client(app, player).get(url("/builder/wilderness")).status_code == 403


def test_builder_inspector_forms_return_to_the_inspector(game):
    app, (admin, _) = game
    client = _client(app, admin)
    with app.app_context():
        npc = get_db().execute("SELECT id FROM mud_npc_definitions WHERE name='Grey Wolf'").fetchone()["id"]
    response = client.post(url("/builder/npcs/place"), data={
        "csrf_token": "test-token", "definition_id": npc, "room_id": _cell(1, 0), "return_to": "wilderness",
        "return_cell": "1,0,0"})
    assert response.status_code == 302 and "cell=1,0,0" in response.headers["Location"]
    response = client.post(url(f"/builder/wilderness/rooms/{_cell(1, 0)}/convert"), data={"csrf_token": "test-token"})
    assert response.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT kind FROM mud_world_rooms WHERE id=?", (_cell(1, 0),)).fetchone()[0] == "authored"
    page = client.get(url("/builder"))
    assert page.status_code == 200 and b"grid 1, 0, 0" in page.data


def test_builder_room_form_places_rooms_on_the_grid(game):
    app, (admin, _) = game
    client = _client(app, admin)
    with app.app_context():
        cave = get_db().execute("SELECT id FROM mud_world_rooms WHERE name='Dark Cave'").fetchone()["id"]
    response = client.post(url(f"/builder/rooms/{cave}"), data={
        "csrf_token": "test-token", "name": "Dark Cave", "description": "A damp cave.", "grid_form": "1",
        "x": "2", "y": "-2", "z": "0", "grid_link": "1", "blocked_dirs": ["north"]}, follow_redirects=True)
    assert b"Room Dark Cave updated" in response.data
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT x,y,z,blocked_dirs FROM mud_world_rooms WHERE id=?", (cave,)).fetchone()[:] == (2, -2, 0, "north")
        assert db.execute("SELECT 1 FROM mud_world_rooms WHERE id=?", (_cell(2, -2),)).fetchone() is None
        assert set(_directions(db, cave)) >= {"south", "west"}
