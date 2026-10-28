"""Several MUD worlds run at once, each a separate game with its own characters and choices."""
from io import BytesIO
import json
from pathlib import Path
import shutil
import uuid

import pytest
from flask import session

from stationnet import character_creation, create_app, mud, mud_scope, mud_worlds
from stationnet.db import get_db

BUILD = {"race": "human", "class": "warrior",
         "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
         "skills": ["lore", "guard"]}


def _world(world_id, name, npc_line=None):
    document = {"id": world_id, "name": name, "author": "Test", "start_room": "plaza",
                "rooms": [{"id": "plaza", "name": f"{name} Plaza", "description": f"The heart of {name}."}],
                "exits": []}
    if npc_line:
        document["content"] = {
            "mud_npc_definitions": [{"id": "crier", "name": "Crier", "description": "A town crier.",
                                     "behaviors_json": {"ambient": {"interval_seconds": 30, "lines": [npc_line]}}}],
            "mud_npc_instances": [{"id": "crier-here", "definition_id": "crier", "room_id": "plaza"}]}
    return mud_worlds.package_bytes(document)


@pytest.fixture
def station():
    data = Path(__file__).parent / f"_multiworld_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_procedural_enabled) "
                       "VALUES('Test','Test','TST',1,1)")
            users = [db.execute("INSERT INTO users(username,callsign,password_hash,connection_id,is_admin) "
                                "VALUES(?,?,?,?,?)", (name, name.upper()[:3], "unused", f"conn-{name}",
                                                      int(name == "sysop"))).lastrowid
                     for name in ("sysop", "ann", "bea")]
            db.commit()
            mud_worlds.install(db, _world("north", "Northreach", "Fresh fish!"), enable=True)
            mud_worlds.install(db, _world("south", "Southmarch", "Spices for sale!"), enable=True)
        yield app, users
    finally:
        app.extensions["broadcast_coordinator"]._closed = True
        app.extensions["broadcast_coordinator"]._timer.cancel()
        shutil.rmtree(data)


def _client(app, user_id):
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = user_id
        state["csrf_token"] = "t"
        state["connection_id"] = f"conn-{['sysop', 'ann', 'bea'][user_id - 1]}"
    return client


def _create(client, world, name, **build):
    response = client.post(f"/mud/w/{world}/create", data={
        "csrf_token": "t", "build": json.dumps({**BUILD, "name": name, **build})})
    assert response.status_code == 302 and response.location.endswith(f"/mud/w/{world}"), response.data[-400:]


def _act(client, world, command, client_id):
    return client.post(f"/mud/w/{world}/actions", data={"csrf_token": "t", "command": command,
                                                         "client_id": client_id})


def test_world_list_and_independent_characters(station):
    app, (_sysop, ann, _bea) = station
    client = _client(app, ann)
    listing = client.get("/mud").data
    for name in (b"Station World", b"Northreach", b"Southmarch", b"Dungeon Portals"):
        assert name in listing
    assert listing.count(b"CREATE CHARACTER") == 4
    _create(client, "north", "Ann North")
    _create(client, "south", "Ann South", race="dwarf")
    # Names are unique per world, so the same name is fine in another world.
    _create(client, mud_scope.PROCEDURAL, "Ann North")
    with app.app_context():
        db = get_db()
        rows = {row["world_key"]: row for row in db.execute(
            "SELECT * FROM mud_characters WHERE user_id=?", (ann,))}
        assert set(rows) == {"north", "south", mud_scope.PROCEDURAL}
        assert len({row["id"] for row in rows.values()}) == 3
        assert rows["south"]["race_key"] == "dwarf" and rows["north"]["race_key"] == "human"
        with mud_scope.use("north"):
            mud.award_experience(db, ann, 400)
            mud._grant_item(db, ann, "stationnet:shop:tonic")
            db.execute("UPDATE mud_character_wallets SET coins=999 WHERE character_id=?", (rows["north"]["id"],))
            north = mud.character_state(db, ann)
            north_items = [item["name"] for item in mud.inventory_state(db, ann)["inventory"]]
        with mud_scope.use("south"):
            south = mud.character_state(db, ann)
            south_items = [item["name"] for item in mud.inventory_state(db, ann)["inventory"]]
            assert mud._wallet(db, rows["south"]["id"]) != 999
        db.commit()
        assert north["level"] > 1 and south["level"] == 1
        assert "Healing Tonic" in north_items and "Healing Tonic" not in south_items
        assert north["progression_track"] == "traditional"
    listing = client.get("/mud").data
    assert b"Ann North" in listing and b"Ann South" in listing and b"CONTINUE" in listing


def test_two_tabs_in_two_worlds_never_mix(station):
    app, (_sysop, ann, bea) = station
    ann_client, bea_client = _client(app, ann), _client(app, bea)
    for client, name in ((ann_client, "Ann"), (bea_client, "Bea")):
        _create(client, "north", f"{name} North")
        _create(client, "south", f"{name} South")
    # Ann has a tab in each world; Bea plays only Northreach.
    assert ann_client.get("/mud/w/north/updates?client_id=ann-north-tab-1").status_code == 200
    assert ann_client.get("/mud/w/south/updates?client_id=ann-south-tab-1").status_code == 200
    assert bea_client.get("/mud/w/north/updates?client_id=bea-north-tab-1").status_code == 200
    said = _act(ann_client, "north", "/say hello north", "ann-north-tab-1")
    assert said.status_code == 200
    north_feed = bea_client.get("/mud/w/north/updates?client_id=bea-north-tab-1&after=0").get_json()
    assert any("hello north" in event["body"] for event in north_feed["events"])
    south_feed = ann_client.get("/mud/w/south/updates?client_id=ann-south-tab-1&after=0").get_json()
    assert not any("hello north" in event["body"] for event in south_feed["events"])
    who = _act(ann_client, "south", "/who", "ann-south-tab-1").get_json()
    body = "\n".join(event["body"] for event in who["events"])
    assert "Ann South" in body and "Bea" not in body
    # Bea is not in Southmarch, so Ann's Southmarch character cannot reach her there.
    assert _act(ann_client, "south", '/tell "Bea North" psst', "ann-south-tab-1").status_code == 400
    assert _act(ann_client, "north", '/tell "Bea North" psst', "ann-north-tab-1").status_code == 200
    with app.app_context():
        db = get_db()
        rooms = {row["world_key"]: row["location_key"] for row in db.execute(
            "SELECT world_key,location_key FROM mud_player_locations WHERE user_id=?", (ann,))}
        assert rooms["north"].startswith("world:north:") and rooms["south"].startswith("world:south:")


def test_disabled_worlds_hide_from_players_but_stay_editable(station):
    app, (sysop, ann, _bea) = station
    client, admin = _client(app, ann), _client(app, sysop)
    _create(client, "north", "Ann North")
    admin.post("/sysop/settings/mud-worlds/enabled", data={"csrf_token": "t", "world_id": "north", "enabled": "0"})
    assert client.get("/mud/w/north").status_code == 404
    assert client.post("/mud/w/north/actions", data={"csrf_token": "t", "command": "/look"}).status_code == 404
    assert b"Northreach" not in client.get("/mud").data
    assert client.get("/mud/w/north/builder").status_code == 403  # not a builder
    builder = admin.get("/mud/w/north/builder").data
    assert b"Northreach" in builder and b"is disabled" in builder
    assert admin.get(f"/mud/w/{mud_scope.PROCEDURAL}/builder").status_code == 404
    admin.post("/sysop/settings/mud-worlds/enabled", data={"csrf_token": "t", "world_id": "north", "enabled": "1"})
    assert client.get("/mud/w/north").status_code == 200
    with app.app_context():
        assert get_db().execute("SELECT display_name FROM mud_characters WHERE user_id=? AND world_key='north'",
                                (ann,)).fetchone()[0] == "Ann North"


def test_availability_is_per_world_and_deletion_reaches_every_world(station):
    app, (sysop, ann, bea) = station
    admin, client = _client(app, sysop), _client(app, ann)
    template = json.loads((Path(character_creation.__file__).with_name("mud_origin_templates")
                           / "race.json").read_text(encoding="utf-8"))
    imported = admin.post("/sysop/settings/mud-origins/races/import", data={
        "csrf_token": "t", "offer_form": "1", "offer_worlds": ["north", "south"],
        "origin_file": (BytesIO(json.dumps(template).encode()), "race.json")})
    assert imported.status_code == 302
    with app.app_context():
        db = get_db()
        assert "riverfolk" in character_creation.world_availability(db, "north")["races"]
        assert "riverfolk" not in character_creation.world_availability(db, "stationnet")["races"]
    _create(client, "north", "Ann River", race="riverfolk")
    _create(client, "south", "Ann Delta", race="riverfolk")
    with app.app_context():
        skills = sorted(character_creation.default_availability()["skills"])
    # Disabling riverfolk in Northreach leaves Southmarch alone and grandfathers Ann.
    admin.post("/sysop/settings/mud-worlds/north/availability", data={
        "csrf_token": "t", "races": ["human"], "classes": ["warrior"], "skills": skills})
    assert client.get("/mud/w/north").status_code == 200
    bea_client = _client(app, bea)
    assert b"Riverfolk" not in bea_client.get("/mud/w/north/create").data
    assert b"Riverfolk" in bea_client.get("/mud/w/south/create").data
    # Deleting the race removes it from every world; its characters choose a replacement.
    admin.post("/sysop/settings/mud-origins/races/remove", data={
        "csrf_token": "t", "key": "riverfolk", "confirm_remove": "on"})
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM mud_world_availability WHERE definition_key='riverfolk'"
                          ).fetchone()[0] == 0
        assert db.execute("SELECT installed FROM mud_origin_definitions WHERE key='riverfolk'").fetchone()[0] == 0
    assert client.get("/mud/w/south").location.endswith("/mud/w/south/character/compatibility")
    assert client.get("/mud/w/north").location.endswith("/mud/w/north/character/compatibility")
    replaced = client.post("/mud/w/south/character/compatibility", data={
        "csrf_token": "t", "race": "human", "class": "warrior"})
    assert replaced.status_code == 302
    assert client.get("/mud/w/south").status_code == 200
    assert client.get("/mud/w/north").location.endswith("/compatibility")


def test_scheduler_runs_every_enabled_world(station):
    app, (_sysop, ann, _bea) = station
    with app.app_context():
        db = get_db()
        for world in ("north", "south"):
            with mud_scope.use(world):
                character_creation.complete(db, ann, {**BUILD, "name": f"Ann {world.title()}"})
                mud._set_player_location(db, ann, "traditional", "world_room", mud._start_room(db)["id"])
        db.commit()
        mud.sync_npc_schedule(db)
        timers = {row["entity_id"] for row in db.execute(
            "SELECT entity_id FROM mud_scheduled_events WHERE event_kind='ambient'")}
        assert len(timers) == 2
        db.execute("UPDATE mud_scheduled_events SET due_at='2000-01-01T00:00:00+00:00'")
        db.commit()
    with app.test_request_context():
        session["connection_id"] = "conn-ann"
        db = get_db()
        for world in ("north", "south"):
            with mud_scope.use(world):
                mud._register_presence(db, db.execute("SELECT * FROM users WHERE id=?", (ann,)).fetchone(),
                                       f"ann-{world}-tab-1")
        db.commit()
    assert mud.run_scheduled_tick(app) >= 2
    with app.app_context():
        db = get_db()
        events = {row["world_key"]: row["body"] for row in db.execute(
            "SELECT world_key,body FROM mud_events WHERE event_type='npc'")}
        assert "Fresh fish!" in events["north"] and "Spices for sale!" in events["south"]
        mud_worlds.set_enabled(db, "south", False)
        db.commit()
        mud.sync_npc_schedule(db)
        assert len([row for row in db.execute(
            "SELECT 1 FROM mud_scheduled_events WHERE event_kind='ambient'")]) == 1


def test_removed_worlds_keep_characters_for_a_reinstall(station):
    app, (_sysop, ann, _bea) = station
    _create(_client(app, ann), "south", "Ann South")
    with app.app_context():
        db = get_db()
        mud_worlds.set_enabled(db, "south", False)
        db.commit()
        mud_worlds.remove(db, "south")
        assert db.execute("SELECT display_name FROM mud_characters WHERE world_key='south'").fetchone()[0] == "Ann South"
        mud_worlds.install(db, _world("south", "Southmarch"), enable=True)
    client = _client(app, ann)
    assert client.get("/mud/w/south").status_code == 200
    look = _act(client, "south", "/look", "ann-south-tab-1").get_json()
    assert look["character"]["name"] == "Ann South" and look["room"]["name"] == "Southmarch Plaza"
