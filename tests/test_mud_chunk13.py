import uuid

import pytest

from stationnet import create_app, mud
from stationnet.db import get_db


@pytest.fixture()
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "chunk-13-tests",
        "DATABASE": str(tmp_path / "stationnet.sqlite"),
        "DATA_DIR": str(tmp_path),
        "DUNGEON_THEMES_DIR": str(tmp_path / "dungeon_themes"),
    })


def _station(database, traditional=True, procedural=True):
    database.execute(
        "INSERT INTO station(id,name,description,callsign,mud_enabled,"
        "mud_traditional_enabled,mud_procedural_enabled) VALUES(1,'Test','Test','TEST',?,?,?)",
        (traditional or procedural, traditional, procedural),
    )


def _user(database, name="player"):
    user_id = database.execute(
        "INSERT INTO users(username,callsign,password_hash,connection_id) VALUES(?,?,?,?)",
        (name, name.upper(), "unused", uuid.uuid4().hex),
    ).lastrowid
    return database.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def _policies(database, progression_shared, inventory_shared):
    database.execute(
        "UPDATE mud_crossover_settings SET progression_shared=?,inventory_shared=? WHERE id=1",
        (progression_shared, inventory_shared),
    )


def test_all_mode_combinations_preserve_one_identity_and_saved_world_location(app):
    with app.app_context():
        database = get_db(); _station(database); user = _user(database)
        character_id = mud.character_state(database, user["id"])["id"]
        start = mud._start_room(database)
        mud._set_player_location(database, user["id"], "traditional", "world_room", start["id"])

        for traditional, procedural, expected in (
            (True, False, "traditional"),
            (False, True, "procedural"),
            (True, True, "procedural"),
        ):
            database.execute(
                "UPDATE station SET mud_enabled=1,mud_traditional_enabled=?,mud_procedural_enabled=? WHERE id=1",
                (traditional, procedural),
            )
            if traditional and procedural:
                mud._set_player_location(database, user["id"], "procedural", "portal", mud.PORTAL_ROOM_ID)
            mud.reconcile_mode_locations(database)
            location = database.execute(
                "SELECT mode FROM mud_player_locations WHERE user_id=?", (user["id"],)
            ).fetchone()
            assert location["mode"] == expected
            assert mud._character_row(database, user["id"])["id"] == character_id

        database.execute(
            "UPDATE station SET mud_enabled=0,mud_traditional_enabled=0,mud_procedural_enabled=0 WHERE id=1"
        )
        mud.reconcile_mode_locations(database)
        assert mud._reconcile_player_location(database, user["id"]) is None
        assert mud._character_row(database, user["id"], "shared")["id"] == character_id
        assert database.execute(
            "SELECT room_id FROM mud_world_locations WHERE user_id=?", (user["id"],)
        ).fetchone()["room_id"] == start["id"]


def test_progression_policy_selects_tracks_without_overwriting_any_track(app):
    with app.app_context():
        database = get_db(); _station(database); user = _user(database)
        start = mud._start_room(database)
        mud._set_player_location(database, user["id"], "traditional", "world_room", start["id"])

        mud.award_experience(database, user["id"], 400)
        _policies(database, False, True)
        mud.award_experience(database, user["id"], 100)
        assert mud.character_state(database, user["id"])["progression_track"] == "traditional"
        assert mud.character_state(database, user["id"])["experience"] == 100

        mud._set_player_location(database, user["id"], "procedural", "portal", mud.PORTAL_ROOM_ID)
        mud.award_experience(database, user["id"], 225)
        assert mud.character_state(database, user["id"])["progression_track"] == "procedural"
        assert mud.character_state(database, user["id"])["experience"] == 225

        _policies(database, True, True)
        assert mud.character_state(database, user["id"])["experience"] == 400
        mud.award_experience(database, user["id"], 25)
        _policies(database, False, True)
        assert mud.character_state(database, user["id"])["experience"] == 225
        tracks = {row["track_key"]: row["experience"] for row in database.execute(
            "SELECT track_key,experience FROM mud_character_progression WHERE character_id=?",
            (f"stationnet:character:{user['id']}",),
        )}
        assert tracks == {"shared": 425, "traditional": 100, "procedural": 225}


def test_inventory_provenance_and_loadouts_survive_policy_changes_without_duplicates(app):
    with app.app_context():
        database = get_db(); _station(database); user = _user(database)
        database.execute(
            "INSERT INTO mud_item_definitions(id,name,description,item_type,equipment_slot) "
            "VALUES('blade','Blade','A test blade.','equipment','main_hand')"
        )
        _policies(database, True, False)
        start = mud._start_room(database)
        mud._set_player_location(database, user["id"], "traditional", "world_room", start["id"])
        traditional_item = mud._grant_item(database, user["id"], "blade", "traditional")
        mud._equip_action(database, user, {"item_id": traditional_item})

        mud._set_player_location(database, user["id"], "procedural", "portal", mud.PORTAL_ROOM_ID)
        procedural_item = mud._grant_item(database, user["id"], "blade", "procedural")
        mud._equip_action(database, user, {"item_id": procedural_item})
        assert [row["id"] for row in mud._inventory_rows(database, user["id"], True)] == [procedural_item]

        mud._set_player_location(database, user["id"], "traditional", "world_room", start["id"])
        assert [row["id"] for row in mud._inventory_rows(database, user["id"], True)] == [traditional_item]

        _policies(database, True, True)
        shared = mud.inventory_state(database, user["id"])
        assert {row["id"] for row in shared["inventory"]} == {traditional_item, procedural_item}
        mud._equip_action(database, user, {"item_id": procedural_item})
        assert [row["id"] for row in mud._inventory_rows(database, user["id"], True)] == [procedural_item]

        _policies(database, True, False)
        assert [row["id"] for row in mud._inventory_rows(database, user["id"], True)] == [traditional_item]
        assert database.execute(
            "SELECT COUNT(*) AS amount FROM mud_item_instances WHERE owner_character_id=?",
            (f"stationnet:character:{user['id']}",),
        ).fetchone()["amount"] == 2


def test_disabling_procedural_mode_preserves_active_expedition_for_later_resume(app):
    with app.app_context():
        database = get_db(); _station(database); user = _user(database)
        character = mud._character_row(database, user["id"])
        database.execute(
            "INSERT INTO mud_procedural_instances(id,instance_type,status,join_code) "
            "VALUES('run','random','active','ABC123')"
        )
        database.execute(
            "INSERT INTO mud_dungeon_rooms(instance_id,room_key,ordinal,x,y,role,depth,difficulty,"
            "name,description,room_template_id) VALUES('run','entrance',0,0,0,'entrance',0,1,"
            "'Entrance','Safe entrance.','fixture')"
        )
        database.execute(
            "INSERT INTO mud_expedition_participants(instance_id,user_id,character_id,current_room_key) "
            "VALUES('run',?,?, 'entrance')", (user["id"], character["id"]),
        )
        mud._set_player_location(
            database, user["id"], "procedural", "dungeon_instance", "entrance", "run"
        )

        database.execute(
            "UPDATE station SET mud_procedural_enabled=0,mud_traditional_enabled=1 WHERE id=1"
        )
        mud.reconcile_mode_locations(database)
        assert mud._current_room(database, user["id"])["id"] == mud._start_room(database)["id"]
        assert mud._active_expedition(database, user["id"])["id"] == "run"

        database.execute("UPDATE station SET mud_procedural_enabled=1 WHERE id=1")
        mud._set_player_location(database, user["id"], "procedural", "portal", mud.PORTAL_ROOM_ID)
        mud._resume_expedition_action(database, user, {})
        assert mud._current_room(database, user["id"])["instance_id"] == "run"


def test_sysop_interface_saves_disabled_mode_and_crossover_policies(app):
    with app.app_context():
        database = get_db(); _station(database); user = _user(database, "sysop")
        database.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
        database.commit()
    client = app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = user["id"]
        state["csrf_token"] = "chunk13-token"
    response = client.post("/sysop/settings/mud", data={
        "csrf_token": "chunk13-token",
        "mud_progression_policy": "independent",
        "mud_inventory_policy": "independent",
        "mud_portal_name": "Test Portals",
        "mud_portal_description": "A protected test hub.",
    })
    assert response.status_code == 302
    with app.app_context():
        database = get_db()
        station = database.execute(
            "SELECT mud_enabled,mud_traditional_enabled,mud_procedural_enabled FROM station WHERE id=1"
        ).fetchone()
        crossover = database.execute(
            "SELECT progression_shared,inventory_shared FROM mud_crossover_settings WHERE id=1"
        ).fetchone()
        assert tuple(station) == (0, 0, 0)
        assert tuple(crossover) == (0, 0)

