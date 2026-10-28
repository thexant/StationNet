"""Wayfinding: destinations, what characters know, routes through the real map, and directions."""
from datetime import datetime
import json
from pathlib import Path
import random
import shutil
import time
import uuid
import zipfile
from io import BytesIO

import pytest
from flask import session

from mud_world_helpers import activate_world, url
from stationnet import create_app, character_creation, mud, mud_conditions, mud_triggers, \
    mud_wayfinding, mud_wilderness, mud_worlds
from stationnet.db import get_db

WORLD = "wayworld"


def _preset(key, name, terrain, links="8"):
    return {"key": key, "name": name, "terrain": terrain, "links": links,
            "descriptions": [f"{name} one.", f"{name} two.", f"{name} three."]}


def _document(world_id=WORLD):
    """A road runs north along x=0 and east along y=0 to Kessa Gate; marsh fills the rest.

    The camp sits at (0, 6), the gate at (10, 0). Kessa Market lies off the grid
    through the gate, and the vault beyond it behind a locked door and a hidden crack.
    """
    return {
        "id": world_id, "name": "Way World", "author": "Test", "start_room": "camp",
        "rooms": [{"id": "camp", "name": "Camp", "description": "A camp.", "x": 0, "y": 6, "z": 0},
                  {"id": "gate", "name": "Kessa Gate", "description": "A gate.", "x": 10, "y": 0, "z": 0},
                  {"id": "market", "name": "Market Square", "description": "Stalls."},
                  {"id": "vault", "name": "Vault", "description": "Dark."}],
        "exits": [{"from": "gate", "to": "market", "name": "archway", "direction": "in"},
                  {"from": "market", "to": "gate", "name": "archway", "direction": "out"},
                  {"from": "market", "to": "vault", "name": "door", "direction": "north", "key_item_id": "brass-key"},
                  {"from": "market", "to": "vault", "name": "crack", "hidden": True, "id": "crack"},
                  {"from": "vault", "to": "market", "name": "door", "direction": "south"}],
        "content": {
            "mud_item_definitions": [
                {"id": "brass-key", "name": "Brass Key", "description": "A key.", "item_type": "key"},
                {"id": "old-map", "name": "Old Map", "description": "A map.", "item_type": "misc"},
                {"id": "guide", "name": "Kessa Guide", "description": "A book.", "item_type": "misc",
                 "properties_json": {"use_message": "You read the guide.", "teaches_destinations": ["market-dest"]}}],
            "mud_world_destinations": [
                {"id": "gate-dest", "room_id": "gate", "name": "Kessa Gate", "aliases": "Kessa, the gate",
                 "category": "town", "public": 1},
                {"id": "market-dest", "room_id": "market", "name": "Kessa Market", "category": "settlement"},
                {"id": "tower", "room_id": "@5,3,0", "name": "Old Watchtower", "discover_on_visit": 1},
                {"id": "vault-dest", "room_id": "vault", "name": "Hidden Vault", "category": "dungeon"},
                {"id": "shrine", "room_id": "@10,6,0", "name": "Shrine", "map_items_json": ["old-map"]},
                {"id": "lodge", "room_id": "@5,6,0", "name": "Hunting Lodge", "public": 1,
                 "conditions_json": [{"type": "level", "min": 5}]}],
        },
        "wilderness": {
            "presets": [_preset("road", "Road", "road"), _preset("marsh", "Marsh", "swamp")],
            "layers": [{"z": 0, "origin": [0, 0], "width": 11, "height": 7, "palette": ["", "road", "marsh"],
                        "rows": ["10*1,0", "1,10*2", "1,10*2", "1,10*2", "1,10*2", "1,10*2", "0,10*2"]}],
        },
    }


@pytest.fixture
def game():
    data = Path(__file__).parent / f"_wayfinding_{uuid.uuid4().hex}"
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
            _place(db, uid, _room(db, "camp"))
        db.commit()
    yield app, users
    app.extensions["broadcast_coordinator"]._closed = True
    app.extensions["broadcast_coordinator"]._timer.cancel()
    mud_wayfinding._ROUTES.clear()
    mud_wayfinding._GRAPHS.clear()
    shutil.rmtree(data)


def _room(db, local):
    return db.execute("SELECT id FROM mud_world_rooms WHERE world_id=? AND id LIKE ?",
                      (WORLD, f"%:room:{local}")).fetchone()["id"]


def _cell(x, y, z=0):
    return mud_wilderness.cell_id(WORLD, x, y, z)


def _destination(db, local):
    return db.execute("SELECT id FROM mud_world_destinations WHERE id LIKE ?", (f"%:{local}",)).fetchone()["id"]


def _item(db, local):
    return db.execute("SELECT id FROM mud_item_definitions WHERE world_id=? AND id LIKE ?",
                      (WORLD, f"%:{local}")).fetchone()["id"]


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


def _names(db, uid):
    return {row["name"]: row["access"] for row in mud._destination_access(db, uid)}


def _character(db, uid):
    return mud._character_row(db, uid)["id"]


def _give(db, uid, local):
    return mud._grant_item(db, uid, _item(db, local), "test")


def _tick(db, steps=1):
    for _ in range(steps):
        due = db.execute("SELECT MIN(next_due) FROM mud_travel").fetchone()[0]
        if due is None:
            return
        mud.process_travel(db, datetime.fromisoformat(due))
        db.execute("UPDATE mud_presence SET last_seen=? WHERE disconnected_at IS NULL", (mud._scheduled_now(),))


def _bodies(db, uid):
    return [row["body"] for row in mud._events_for_user(db, uid)]


# Knowledge -------------------------------------------------------------------

def test_public_destinations_and_unknown_ones_look_alike(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        assert _names(db, one) == {"Kessa Gate": "public"}
        listing = _act(db, one, "/destinations")
        assert "Towns: Kessa Gate" in listing and "Vault" not in listing
        with pytest.raises(mud.ActionError) as hidden:
            _act(db, one, '/directions "Hidden Vault"')
        with pytest.raises(mud.ActionError) as missing:
            _act(db, one, '/directions "Nowhere Keep"')
        assert str(hidden.value).replace("Hidden Vault", "X") == str(missing.value).replace("Nowhere Keep", "X")


def test_conditions_gate_every_way_of_knowing(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        assert "Hunting Lodge" not in _names(db, one)
        db.execute("UPDATE mud_character_progression SET level=5 WHERE character_id=?", (_character(db, one),))
        assert _names(db, one)["Hunting Lodge"] == "public"


def test_maps_give_directions_only_while_carried(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        assert "Shrine" not in _names(db, one)
        _give(db, one, "old-map")
        assert _names(db, one)["Shrine"] == "map"
        assert "(by map)" in _act(db, one, "/destinations")
        db.execute("DELETE FROM mud_item_instances WHERE definition_id=?", (_item(db, "old-map"),))
        assert "Shrine" not in _names(db, one)
        assert not mud_wayfinding.knows(db, _character(db, one), _destination(db, "shrine"))


def test_visiting_dialogue_triggers_and_books_teach_for_good(game):
    app, (one, two) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(5, 3))
        notes = [draft.body for draft in mud._entry_triggers(db, _user(db, one))]
        assert notes == ["You will remember the way to Old Watchtower."]
        assert mud._entry_triggers(db, _user(db, one)) == []
        assert _names(db, one)["Old Watchtower"] == "known"

        vault = _destination(db, "vault-dest")
        context = mud._ConditionContext(db, one)
        assert not mud_conditions.evaluate([{"type": "knows_destination", "destination": vault}], context)
        drafts = mud._settle_outcomes(db, _user(db, one), mud_conditions.validate_outcomes(
            [{"type": "learn_destination", "destination": vault}]))
        assert [draft.body for draft in drafts] == ["You now know the way to Hidden Vault."]
        assert mud_conditions.evaluate([{"type": "knows_destination", "destination": vault}],
                                       mud._ConditionContext(db, one))

        trigger = mud_triggers.validate_trigger({"id": "teach", "event": "manual", "actions": [
            {"type": "learn_destination", "destination": vault}]})
        assert mud._trigger_action(db, _user(db, two), trigger, 0, WORLD)[0].body.endswith("Hidden Vault.")
        forget = {**trigger, "actions": [{"type": "forget_destination", "destination": vault}]}
        mud._trigger_action(db, _user(db, two), forget, 0, WORLD)
        assert "Hidden Vault" not in _names(db, two)

        _give(db, one, "guide")
        text = _act(db, one, '/read "Kessa Guide"')
        assert "You read the guide." in text and "You now know the way to Kessa Market." in text
        assert "You learn nothing new" in _act(db, one, '/read "Kessa Guide"')


def test_dungeon_rules_refuse_destinations():
    with pytest.raises(ValueError):
        mud_conditions.validate_outcomes([{"type": "learn_destination", "destination": "x"}], allow_world=False)
    with pytest.raises(ValueError):
        mud_conditions.validate_conditions([{"type": "knows_destination", "destination": "x"}], allow_quests=False)
    with pytest.raises(ValueError):
        mud_triggers.validate_trigger({"id": "tick", "event": "timer", "source": {"room": "r"}, "interval_seconds": 60,
                                       "actions": [{"type": "learn_destination", "destination": "x"}]})


# Directions ------------------------------------------------------------------

def test_directions_prefer_the_road_and_read_like_advice(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        text = _act(db, one, "/directions kessa")
        lines = text.splitlines()
        assert lines[0] == "Kessa Gate is a fair walk away to the northeast."
        assert lines[1].startswith("Follow the road north")
        assert lines[2:4] == ["Then follow the road east a short way.", "Finally, go east to Kessa Gate."]
        assert "You keep this destination in mind" in text
        assert mud.wayfinding_state(db, one)["destination"] == "Kessa Gate"
        assert mud.wayfinding_state(db, one)["direction"] == "north"
        assert mud._room_state(db, one)["wayfinding"]["destination"] == "Kessa Gate"
        # Checking again tells the same story without re-selecting.
        assert "keep this destination" not in _act(db, one, "/directions")
        assert "stop heading for Kessa Gate" in _act(db, one, "/directions clear")
        assert mud.wayfinding_state(db, one) is None


def test_names_resolve_exactly_then_by_prefix_or_ask(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        mud_wayfinding.learn(db, _character(db, one), _destination(db, "market-dest"), "test")
        assert _act(db, one, "/directions the gate").startswith("Kessa Gate")
        assert _act(db, one, "/directions kessa").startswith("Kessa Gate")  # exact alias wins
        assert _act(db, one, "/directions kessa m").startswith("Kessa Market")
        with pytest.raises(mud.ActionError, match="Which place do you mean: Kessa Gate, Kessa Market"):
            _act(db, one, "/directions kes")


def test_routes_respect_locks_hidden_exits_and_keys(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        mud_wayfinding.learn(db, _character(db, one), _destination(db, "vault-dest"), "test")
        _place(db, one, _room(db, "market"))
        assert "cannot make out a way" in _act(db, one, "/directions vault")
        _give(db, one, "brass-key")
        assert "Take the door north to Vault, where you will find Hidden Vault." in _act(db, one, "/directions vault")
        # A trigger can reveal the hidden crack for one player; it is then a usable route.
        db.execute("DELETE FROM mud_item_instances WHERE definition_id=?", (_item(db, "brass-key"),))
        crack = db.execute("SELECT id FROM mud_world_exits WHERE local_id='crack'").fetchone()["id"]
        mud._store_entity_state(db, WORLD, "exit", crack, "open", _character(db, one))
        assert "Take the crack to Vault" in _act(db, one, "/directions vault")


def test_world_edits_change_the_route(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        _act(db, one, "/directions kessa")
        before = mud_wayfinding.revision(db)
        db.execute("UPDATE mud_world_rooms SET grid_link=0 WHERE id=?", (_cell(0, 3),))
        assert mud_wayfinding.revision(db) > before
        text = _act(db, one, "/directions")
        assert text.startswith("Your way has changed since you last asked.")
        assert "marsh" in text


def test_unreachable_and_far_destinations_give_only_a_bearing(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        for (x, y) in ((9, 0), (9, 1), (10, 1)):
            db.execute("UPDATE mud_world_rooms SET grid_link=0 WHERE id=?", (_cell(x, y),))
        assert _act(db, one, "/directions kessa").startswith(
            "Kessa Gate lies to the northeast, but you can see no way there from here.")
        route = mud_wayfinding.find_route(db, WORLD, _room(db, "camp"), _cell(10, 6), lambda item: True, budget=3)
        assert route.status == "far"


def test_dungeon_rooms_and_arrival(game, monkeypatch):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _room(db, "gate"))
        assert _act(db, one, "/directions kessa gate") == "You are at Kessa Gate."
        monkeypatch.setattr(mud, "_current_room", lambda database, user_id: {"id": "run:room", "instance_id": "run"})
        with pytest.raises(mud.ActionError, match="bearings"):
            _act(db, one, "/directions kessa")


def test_moving_by_hand_reports_new_headings_only(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        _act(db, one, "/directions kessa")
        quiet = [draft.body for draft in mud._go_action(db, _user(db, one), {"arguments": ["north"]})]
        assert not any(body.startswith("Toward") for body in quiet)
        for _ in range(3):
            mud._go_action(db, _user(db, one), {"arguments": ["north"]})
        # Reaching (0, 1), where the road bends east, announces the turn once.
        turned = [draft.body for draft in mud._go_action(db, _user(db, one), {"arguments": ["north"]})]
        assert "Toward Kessa Gate: Go northeast, then follow the road east a short way." in turned, turned
        onward = [draft.body for draft in mud._go_action(db, _user(db, one), {"arguments": ["northeast"]})]
        assert "Toward Kessa Gate: Follow the road east a short way." in onward
        quiet = [draft.body for draft in mud._go_action(db, _user(db, one), {"arguments": ["east"]})]
        assert not any(body.startswith("Toward") for body in quiet)


# Travel ----------------------------------------------------------------------

def test_travel_stops_where_the_way_turns_and_on_arrival(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(0, 5))
        _act(db, one, "/directions kessa")
        mud._travel_action(db, _user(db, one), {"arguments": ["north"]})
        _tick(db, 6)
        assert mud.travel_state(db, one) is None
        room = mud._current_room(db, one)
        assert room["x"] == 0 and room["y"] in (0, 1)
        bodies = _bodies(db, one)
        assert any("your way toward Kessa Gate turns east" in body for body in bodies), bodies
        # The road is joined by cutting the corner, so the heading starts with that step.
        assert bodies[-1].startswith("Toward Kessa Gate: Go northeast, then follow the road east")
        assert mud.wayfinding_state(db, one)["direction"] == "northeast"
        mud._travel_action(db, _user(db, one), {"arguments": ["northeast"]})
        _tick(db, 2)
        assert mud._current_room(db, one)["id"] == _cell(1, 0)
        assert _bodies(db, one)[-1].startswith("Toward Kessa Gate: Follow the road east")
        mud._travel_action(db, _user(db, one), {"arguments": ["east"]})
        _tick(db, 12)
        assert mud._current_room(db, one)["id"] == _room(db, "gate")
        assert "You have reached Kessa Gate." in _bodies(db, one)
        assert mud.wayfinding_state(db, one) is None


def test_stopping_travel_shows_the_heading(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        _place(db, one, _cell(0, 5))
        _act(db, one, "/directions kessa")
        mud._travel_action(db, _user(db, one), {"arguments": ["north"]})
        text = _act(db, one, "/stop")
        assert "You stop travelling" in text and "Toward Kessa Gate:" in text


# Scale -----------------------------------------------------------------------

def test_routes_across_a_large_wilderness_stay_fast(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        preset = db.execute("SELECT id FROM mud_world_room_presets WHERE world_id=? AND local_key='marsh'",
                            (WORLD,)).fetchone()["id"]
        mud_wilderness.fill(db, WORLD, mud_wilderness.parse_shape(
            {"shape": "rect", "z": 0, "x1": 100, "y1": 100, "x2": 399, "y2": 399}), preset)
        started = time.perf_counter()
        route = mud_wayfinding.find_route(db, WORLD, _cell(100, 100), _cell(399, 380), lambda item: True)
        elapsed = time.perf_counter() - started
        assert route.status == "ok" and len(route.steps) == 299
        assert elapsed < 3, elapsed
        lines = mud_wayfinding.describe(route.graph, db, route.start, route.steps, "Far Point")
        assert lines[0] == "Far Point is a great journey away to the southeast."
        assert len(lines) <= mud_wayfinding.MAX_LEGS + 2


# Packages and the builder ----------------------------------------------------

def test_package_round_trip_keeps_destinations_and_references(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        payload = mud_worlds.export(db, WORLD)
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            content = json.loads(archive.read("content.json"))
        destinations = {row["name"]: row for row in content["mud_world_destinations"]}
        assert destinations["Old Watchtower"]["room_id"] == "@5,3,0"
        assert destinations["Kessa Gate"]["public"] == 1 and destinations["Kessa Gate"]["aliases"] == "Kessa, the gate"
        items = {row["name"]: row for row in content["mud_item_definitions"]}
        assert destinations["Shrine"]["map_items_json"] == [items["Old Map"]["id"]]
        assert items["Kessa Guide"]["properties_json"]["teaches_destinations"] == [destinations["Kessa Market"]["id"]]
        assert destinations["Hunting Lodge"]["conditions_json"] == [{"type": "level", "min": 5}]
        document = json.loads(json.dumps(content))
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(files["manifest.json"])
        manifest["id"] = "wayworld-copy"
        files["manifest.json"] = json.dumps(manifest).encode()
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        mud_worlds.install(db, buffer.getvalue())
        copied = {row["name"]: row for row in mud_wayfinding.world_destinations(db, "wayworld-copy")}
        assert set(copied) == set(destinations) and document
        guide = db.execute("SELECT properties_json FROM mud_item_definitions WHERE world_id='wayworld-copy' "
                           "AND name='Kessa Guide'").fetchone()
        assert json.loads(guide["properties_json"])["teaches_destinations"] == [copied["Kessa Market"]["id"]]


def test_packages_reject_bad_destinations():
    for change in ({"name": "X"}, {"category": "castle"}, {"map_items_json": ["missing"]}, {"public": 2}):
        document = _document()
        document["content"]["mud_world_destinations"][0].update(change)
        with pytest.raises(mud_worlds.WorldError):
            mud_worlds.read_package(mud_worlds.package_bytes(document))
    document = _document()
    document["content"]["mud_item_definitions"][2]["properties_json"]["teaches_destinations"] = ["nowhere"]
    with pytest.raises(mud_worlds.WorldError, match="teaches_destinations"):
        mud_worlds.read_package(mud_worlds.package_bytes(document))


def _client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user_id
        flask_session["csrf_token"] = "test-token"
    return client


def test_builder_creates_edits_and_removes_destinations(game):
    app, (one, _two) = game
    client = _client(app, one)
    with app.app_context():
        db = get_db()
        camp, cell = _room(db, "camp"), _cell(3, 3)
        old_map = _item(db, "old-map")
    response = client.post(url("/builder/destinations"), data={
        "csrf_token": "test-token", "room_id": cell, "name": "Reed Pool", "aliases": "pool, reeds",
        "category": "landmark", "discover_on_visit": "1", "map_items": [old_map],
        "return_to": "wilderness", "return_cell": "3,3,0"})
    assert response.status_code == 302 and "cell=3" in response.headers["Location"]
    duplicate = client.post(url("/builder/destinations"), data={
        "csrf_token": "test-token", "room_id": camp, "name": "reed pool"}, follow_redirects=True)
    assert b"already has that name" in duplicate.data
    with app.app_context():
        db = get_db()
        row = db.execute("SELECT * FROM mud_world_destinations WHERE name='Reed Pool'").fetchone()
        assert row["room_id"] == cell and row["discover_on_visit"] == 1 and json.loads(row["map_items_json"]) == [old_map]
    page = client.get(url("/builder"))
    assert b"Reed Pool" in page.data and b"Wayfinding destination" in page.data
    assert b"Wayfinding destination" in client.get(url("/builder/wilderness?cell=3,3,0")).data
    client.post(url(f"/builder/destinations/{row['id']}"), data={
        "csrf_token": "test-token", "name": "Reed Pool", "public": "1",
        "conditions_json": '[{"type":"level","min":2}]'})
    with app.app_context():
        db = get_db()
        row = db.execute("SELECT * FROM mud_world_destinations WHERE id=?", (row["id"],)).fetchone()
        assert row["public"] == 1 and row["discover_on_visit"] == 0 and row["conditions_json"] != "[]"
        guide = _item(db, "guide")
    response = client.post(url(f"/builder/items/{guide}"), data={
        "csrf_token": "test-token", "name": "Kessa Guide", "description": "A book.", "item_type": "misc",
        "teaches_destinations": [row["id"]]})
    assert response.status_code == 302
    with app.app_context():
        properties = json.loads(get_db().execute("SELECT properties_json FROM mud_item_definitions WHERE id=?",
                                                 (guide,)).fetchone()[0])
        assert properties["teaches_destinations"] == [row["id"]]
    client.post(url(f"/builder/destinations/{row['id']}/delete"), data={"csrf_token": "test-token",
                                                                       "confirmation": "delete"})
    with app.app_context():
        assert get_db().execute("SELECT 1 FROM mud_world_destinations WHERE id=?", (row["id"],)).fetchone() is None
