"""Map layers: separate maps per z, joined only by authored up and down exits."""
from io import BytesIO
import json
from pathlib import Path
import shutil
import uuid
import zipfile

import pytest

from mud_world_helpers import activate_world, url
from stationnet import create_app, character_creation, mud, mud_wayfinding, mud_wilderness, mud_worlds
from stationnet.db import get_db

WORLD = "layerworld"


def _preset(key, name, glyph):
    return {"key": key, "name": name, "glyph": glyph, "terrain": "road",
            "descriptions": [f"{name} one.", f"{name} two.", f"{name} three."]}


def _document(world_id=WORLD):
    """A city on layer 0 with a ladder down to the sewers (-1) and a lift up to an office (5).

    The plaza is at (0, 0, 0), the ladder shaft east of it and the lift lobby
    south of it. A hidden grate in the north-west street drops into the sewers too.
    """
    return {
        "id": world_id, "name": "Layer World", "author": "Test", "start_room": "plaza",
        "layers": [{"z": 0, "name": "City"}, {"z": -1, "name": "Sewers"}, {"z": 5, "name": "Office Tower"}],
        "rooms": [{"id": "plaza", "name": "Plaza", "description": "A plaza.", "x": 0, "y": 0, "z": 0},
                  {"id": "shaft", "name": "Ladder Shaft", "description": "A ladder.", "x": 1, "y": 0, "z": 0},
                  {"id": "lobby", "name": "Lift Lobby", "description": "A lift.", "x": 0, "y": 1, "z": 0},
                  {"id": "junction", "name": "Sewer Junction", "description": "Damp.", "x": 1, "y": 0, "z": -1},
                  {"id": "office", "name": "Office Lobby", "description": "Carpet.", "x": 0, "y": 0, "z": 5}],
        "exits": [{"from": "shaft", "to": "junction", "name": "ladder", "direction": "down"},
                  {"from": "junction", "to": "shaft", "name": "ladder", "direction": "up"},
                  {"from": "lobby", "to": "office", "name": "elevator", "direction": "up"},
                  {"from": "office", "to": "lobby", "name": "elevator", "direction": "down"},
                  {"from": "@-1,-1,0", "to": "@-1,-1,-1", "name": "grate", "direction": "down",
                   "hidden": True, "id": "grate"}],
        "content": {"mud_world_destinations": [
            {"id": "outflow", "room_id": "@-1,1,-1", "name": "Sewer Outflow", "category": "landmark", "public": 1},
            {"id": "tower", "room_id": "office", "name": "Office Tower", "category": "landmark", "public": 1}]},
        "wilderness": {
            "presets": [_preset("street", "Street", "="), _preset("tunnel", "Tunnel", "~"),
                        _preset("floor", "Office Floor", "_")],
            "layers": [
                {"z": 0, "origin": [-1, -1], "width": 3, "height": 3, "palette": ["", "street"],
                 "rows": ["3*1", "1,2*0", "1,0,1"]},
                {"z": -1, "origin": [-1, -1], "width": 3, "height": 3, "palette": ["", "tunnel"],
                 "rows": ["3*1", "2*1,0", "3*1"]},
                {"z": 5, "origin": [0, 0], "width": 2, "height": 1, "palette": ["", "floor"], "rows": ["0,1"]},
            ],
        },
    }


@pytest.fixture
def game():
    data = Path(__file__).parent / f"_layers_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
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
        mud_worlds.install(db, mud_worlds.package_bytes(_document()))
        activate_world(db, WORLD)
        for uid in users:
            _place(db, uid, _room(db, "plaza"))
        db.commit()
    yield app, users
    app.extensions["broadcast_coordinator"]._closed = True
    app.extensions["broadcast_coordinator"]._timer.cancel()
    mud_wayfinding._ROUTES.clear()
    mud_wayfinding._GRAPHS.clear()
    shutil.rmtree(data)


def _room(db, local, world=WORLD):
    return db.execute("SELECT id FROM mud_world_rooms WHERE world_id=? AND id LIKE ?",
                      (world, f"%:room:{local}")).fetchone()["id"]


def _cell(x, y, z=0):
    return mud_wilderness.cell_id(WORLD, x, y, z)


def _place(db, uid, room_id):
    mud._set_player_location(db, uid, "traditional", "world_room", room_id)
    db.execute("DELETE FROM mud_presence WHERE user_id=?", (uid,))
    db.execute("INSERT INTO mud_presence(world_key,user_id,connection_id,client_id,room_id,location_type,location_key,last_seen) "
               "VALUES(mud_world(),?,?,?,?,?,?,?)", (uid, f"connection-{uid}", f"client-{uid}-1", room_id, "world_room", room_id,
                                         mud._scheduled_now()))


def _user(db, uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _act(db, uid, text):
    definition, parameters = mud.actions.from_command(text)
    return "\n".join(draft.body for draft in definition.handler(db, _user(db, uid), parameters))


def _go(db, uid, direction):
    mud._go_action(db, _user(db, uid), {"arguments": [direction]})
    room_id = mud._current_room(db, uid)["id"]
    _place(db, uid, room_id)
    return room_id


def _directions(db, room_id):
    return {row["direction"]: row["to_room_id"] for row in mud._room_exits(db, room_id)}


def _client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    return client


# Movement ------------------------------------------------------------------

def test_layers_meet_only_through_authored_up_and_down_exits(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        # The street above a tunnel square has no automatic way down.
        assert set(_directions(db, _cell(-1, 0))) == {"north", "south", "east"}
        assert "down" not in _directions(db, _cell(-1, 1))
        assert _go(db, one, "east") == _room(db, "shaft")
        assert _go(db, one, "down") == _room(db, "junction")
        assert set(_directions(db, _room(db, "junction"))) == {"up", "north", "south", "west"}
        assert _go(db, one, "west") == _cell(0, 0, -1)
        assert _go(db, one, "east") == _room(db, "junction")
        assert _go(db, one, "up") == _room(db, "shaft")
        # Layers need not be neighbours: the lift joins layer 0 to layer 5.
        _place(db, one, _room(db, "lobby"))
        assert _go(db, one, "up") == _room(db, "office")
        assert set(_directions(db, _room(db, "office"))) == {"down", "east"}


def test_map_shows_only_the_current_layer_and_its_ways_up_and_down(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        lines = _act(db, one, "/map").splitlines()
        assert lines[0] == "Map around Plaza (0, 0, City, layer 0); north is up."
        grid, legend = lines[1:-1], lines[-1]
        # Centred at column 7, row 4: the shaft leads down, the lobby up; the grate is still hidden.
        assert grid[4][7] == "@" and grid[4][8] == ">" and grid[5][7] == "<" and grid[3][6] == "="
        assert "> way down" in legend and "< way up" in legend and "= Street" in legend
        assert "~" not in "".join(grid)
        grate = db.execute("SELECT id FROM mud_world_exits WHERE local_id='grate'").fetchone()["id"]
        mud._store_entity_state(db, WORLD, "exit", grate, "open", mud._character_row(db, one)["id"])
        assert _act(db, one, "/map").splitlines()[4][6] == ">"

        _place(db, one, _room(db, "junction"))
        lines = _act(db, one, "/map").splitlines()
        assert lines[0] == "Map around Sewer Junction (1, 0, Sewers, layer -1); north is up."
        assert "=" not in "".join(lines[1:-1]) and "~ Tunnel" in lines[-1] and "< way up" in lines[-1]


def test_directions_stop_at_a_way_up_or_down(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        lines = _act(db, one, "/directions outflow").splitlines()
        assert lines[:4] == ["Sewer Outflow is a short walk away.", "Go east to Ladder Shaft.",
                             "Then take the ladder down to Sewer Junction.",
                             "Once you have gone down, ask for directions again."]
        assert not any("Tunnel" in line or "tunnel" in line for line in lines)
        _go(db, one, "east")
        _go(db, one, "down")
        text = _act(db, one, "/directions")
        assert "ask for directions again" not in text and "to reach Sewer Outflow" in text
        # A way up or down as the last step needs no second asking.
        _place(db, one, _room(db, "lobby"))
        text = _act(db, one, "/directions tower")
        assert "Take the elevator up to Office Lobby, where you will find Office Tower." in text
        assert "ask for directions again" not in text


# Packages ------------------------------------------------------------------

def test_packages_refuse_layer_crossings_that_are_not_up_or_down():
    for direction in ("east", "in", None):
        document = _document()
        document["exits"].append({"from": "plaza", "to": "junction", "name": "chute", "direction": direction})
        with pytest.raises(mud_worlds.WorldError, match="up or down exit"):
            mud_worlds.read_package(mud_worlds.package_bytes(document))
    document = _document()
    document["exits"].append({"from": "@1,1,0", "to": "@1,1,-1", "name": "chute", "direction": "east"})
    with pytest.raises(mud_worlds.WorldError, match="up or down exit"):
        mud_worlds.read_package(mud_worlds.package_bytes(document))
    for layers in ([{"z": 0, "name": "A"}], [{"z": 0, "name": "City"}, {"z": 0, "name": "Town"}],
                   [{"z": "0", "name": "City"}], {"z": 0}):
        document = _document()
        document["layers"] = layers
        with pytest.raises(mud_worlds.WorldError):
            mud_worlds.read_package(mud_worlds.package_bytes(document))
    with pytest.raises(mud_wilderness.WildernessError, match="glyph"):
        mud_wilderness.validate_preset(_preset("odd", "Odd", ">"))


def test_export_and_reimport_keep_layers_and_their_connections(game):
    app, _ = game
    with app.app_context():
        db = get_db()
        payload = mud_worlds.export(db, WORLD, strict=True)
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        assert manifest["layers"] == [{"z": -1, "name": "Sewers"}, {"z": 0, "name": "City"},
                                      {"z": 5, "name": "Office Tower"}]
        document = mud_worlds.read_package(payload)
        vertical = {(item["from"], item["to"], item["direction"]) for item in document["exits"]
                    if item["direction"] in ("up", "down")}
        assert ("shaft", "junction", "down") in vertical and ("@-1,-1,0", "@-1,-1,-1", "down") in vertical
        assert ("lobby", "office", "up") in vertical

        mud_worlds.install(db, mud_worlds.package_bytes({**document, "id": "layercopy"}))
        assert mud_wilderness.layer_names(db, "layercopy") == {-1: "Sewers", 0: "City", 5: "Office Tower"}
        down = db.execute("SELECT to_room_id FROM mud_world_exits WHERE from_room_id=? AND direction='down'",
                          (_room(db, "shaft", "layercopy"),)).fetchone()
        assert down["to_room_id"] == _room(db, "junction", "layercopy")


# Builder -------------------------------------------------------------------

def test_builder_names_layers_and_refuses_crossing_exits(game):
    app, (admin, _two) = game
    client = _client(app, admin)
    response = client.post(url("/builder/wilderness/layers"), data={"csrf_token": "test-token", "z": "-1",
                                                                    "name": "Deep Sewers"})
    assert response.status_code == 302
    page = client.get(url("/builder/wilderness?z=-1")).get_data(as_text=True)
    assert 'value="Deep Sewers"' in page and "Deep Sewers (-1)" in page
    with app.app_context():
        db = get_db()
        assert mud_wilderness.layer_names(db, WORLD)[-1] == "Deep Sewers"
        assert mud_wilderness.map_window(db, WORLD, 0, 0, -1, 5, 5)["name"] == "Deep Sewers"
        plaza = _room(db, "plaza")
    client.post(url("/builder/wilderness/layers"), data={"csrf_token": "test-token", "z": "5", "name": ""})

    exit_form = {"csrf_token": "test-token", "from_room_id": plaza, "to_cell": "1,0,-1", "name": "chute",
                 "direction": "east"}
    client.post(url("/builder/exits"), data=exit_form)
    client.post(url("/builder/exits"), data={**exit_form, "direction": ""})
    with app.app_context():
        db = get_db()
        assert 5 not in mud_wilderness.layer_names(db, WORLD)
        assert db.execute("SELECT COUNT(*) FROM mud_world_exits WHERE from_room_id=?", (plaza,)).fetchone()[0] == 0
    client.post(url("/builder/exits"), data={**exit_form, "direction": "down"})
    with app.app_context():
        db = get_db()
        row = db.execute("SELECT to_room_id,direction FROM mud_world_exits WHERE from_room_id=?", (plaza,)).fetchone()
        assert (row["to_room_id"], row["direction"]) == (_room(db, "junction"), "down")
        # Moving a room may not turn one of its exits into a sideways layer crossing.
        db.execute("UPDATE mud_world_exits SET direction='north' WHERE from_room_id=?", (plaza,))
        assert mud_wilderness.crossing_exit(db, plaza)["name"] == "chute"
        assert mud_wilderness.crossing_exit(db, _room(db, "junction"))["name"] == "chute"
        assert mud_wilderness.crossing_exit(db, _room(db, "shaft")) is None
