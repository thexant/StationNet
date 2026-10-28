"""Worlds may rename or disable mana without changing how it works."""
from io import BytesIO
import json
from pathlib import Path
import shutil
import uuid
import zipfile

import pytest

from stationnet import mud_scope
from stationnet import create_app, mud, mud_worlds
from stationnet.db import get_db


@pytest.fixture
def app():
    directory = Path(__file__).parent / f"_mud_resource_{uuid.uuid4().hex}"
    directory.mkdir()
    application = create_app({"TESTING": True, "DATA_DIR": str(directory)})
    try:
        yield application
    finally:
        coordinator = application.extensions.get("broadcast_coordinator")
        if coordinator:
            coordinator._closed = True
            coordinator._timer.cancel()
        shutil.rmtree(directory)


def _scholar(db, username="mage", **user_fields):
    columns = ",".join(["username", "callsign", "password_hash", *user_fields])
    values = (username, username[:3].upper(), "unused", *user_fields.values())
    user_id = db.execute(f"INSERT INTO users({columns}) VALUES({','.join('?' * len(values))})",
                         values).lastrowid
    character = mud._character_row(db, user_id)
    db.execute("UPDATE mud_characters SET creation_state='complete',class_key='scholar' WHERE id=?",
               (character["id"],))
    mud._set_player_location(db, user_id, "traditional", "world_room", mud.START_ROOM_ID)
    return db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone(), character["id"]


def _set_resource(db, enabled, name):
    db.execute("UPDATE mud_world_packages SET resource_enabled=?,resource_name=? WHERE id=mud_world()",
               (int(enabled), name))


def _mana(db, character_id):
    return db.execute("SELECT current_mana FROM mud_character_progression WHERE character_id=? "
                      "AND track_key='traditional'",
                      (character_id,)).fetchone()[0]


def _setup(db):
    db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
               "VALUES('Test','Test','TST',1,1)")
    return _scholar(db)


def test_default_world_keeps_mana(app):
    with app.app_context():
        db = get_db()
        user, _character_id = _setup(db)
        state = mud.character_state(db, user["id"])
        assert state["resource"] == {"enabled": True, "name": "Mana", "noun": "mana"}
        assert "Mana: 40/40" in mud._character_action(db, user, {})[0].body
        assert "ember: 8 mana" in mud._spells_action(db, user, {})[0].body


def test_renamed_resource_changes_only_wording(app):
    with app.app_context():
        db = get_db()
        user, character_id = _setup(db)
        _set_resource(db, True, "Energy")
        assert "Energy: 40/40" in mud._character_action(db, user, {})[0].body
        assert "ember: 8 energy" in mud._spells_action(db, user, {})[0].body
        mud._cast_action(db, user, {"arguments": ["ward"]})
        assert _mana(db, character_id) == 34
        rest = mud._rest_action(db, user, {})[0].body
        assert "energy" in rest and "mana" not in rest.casefold()
        db.execute("UPDATE mud_character_progression SET current_mana=1 WHERE character_id=?", (character_id,))
        with pytest.raises(mud.ActionError, match="enough energy"):
            mud._cast_action(db, user, {"arguments": ["ward"]})
        help_text = mud._help_action(db, user, {})[0].body
        assert "mana" not in help_text.casefold() and "energy" in help_text
        assert "energy" in mud._help_action(db, user, {"arguments": ["rest"]})[0].body
        assert "spell power, healing, experience and energy per level" in mud._train_action(db, user, {})[0].body
        _set_resource(db, True, "PSI")
        assert "ember: 8 PSI" in mud._spells_action(db, user, {})[0].body


def test_disabled_resource_hides_it_and_blocks_spells(app):
    with app.app_context():
        db = get_db()
        user, character_id = _setup(db)
        mud.character_state(db, user["id"])  # applies the scholar's origin mana first
        db.execute("UPDATE mud_character_progression SET current_mana=5 WHERE character_id=?", (character_id,))
        _set_resource(db, False, "Mana")
        sheet = mud._character_action(db, user, {})[0].body
        assert "Mana" not in sheet and "Health:" in sheet
        for action, args in ((mud._cast_action, ["ward"]), (mud._spells_action, [])):
            with pytest.raises(mud.ActionError, match="does not use spells"):
                action(db, user, {"arguments": args})
        help_text = mud._help_action(db, user, {})[0].body
        assert "/cast" not in help_text and "/spells" not in help_text and "mana" not in help_text.casefold()
        with pytest.raises(mud.ActionError):
            mud._help_action(db, user, {"arguments": ["cast"]})
        with pytest.raises(mud.ActionError, match="fully rested"):
            mud._rest_action(db, user, {})
        db.execute("UPDATE mud_character_progression SET current_health=50 WHERE character_id=?", (character_id,))
        assert "mana" not in mud._rest_action(db, user, {})[0].body.casefold()
        assert _mana(db, character_id) == 5
        # The procedural dungeon world keeps its usual Mana for its own characters.
        with mud_scope.use(mud_scope.PROCEDURAL):
            state = mud.character_state(db, user["id"])
            assert state["resource"]["enabled"] and state["resource"]["name"] == "Mana"
        assert _mana(db, character_id) == 5


def test_resource_survives_export_and_import(app):
    with app.app_context():
        db = get_db()
        _setup(db)
        plain = mud_worlds.read_package(mud_worlds.template_bytes())
        assert plain["resource"] == mud_worlds.DEFAULT_RESOURCE
        _set_resource(db, False, "Stamina")
        exported = mud_worlds.export(db, "stationnet")
        with zipfile.ZipFile(BytesIO(exported)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        assert manifest["resource"] == {"enabled": False, "name": "Stamina"}
        manifest["id"] = "stamina-world"
        stream = BytesIO()
        with zipfile.ZipFile(BytesIO(exported)) as source, zipfile.ZipFile(stream, "w") as target:
            for name in source.namelist():
                target.writestr(name, json.dumps(manifest) if name == "manifest.json" else source.read(name))
        db.commit()
        mud_worlds.install(db, stream.getvalue())
        row = db.execute("SELECT resource_enabled,resource_name FROM mud_world_packages WHERE id='stamina-world'"
                         ).fetchone()
        assert (row["resource_enabled"], row["resource_name"]) == (0, "Stamina")
        _set_resource(db, True, "Mana")
        with zipfile.ZipFile(BytesIO(mud_worlds.export(db, "stationnet"))) as archive:
            assert "resource" not in json.loads(archive.read("manifest.json"))


@pytest.mark.parametrize("resource", [{"name": ""}, {"name": "Mana2"}, {"name": "x" * 21},
                                      {"enabled": 1}, {"colour": "blue"}, "Energy"])
def test_invalid_resource_is_rejected(resource):
    with pytest.raises(mud_worlds.WorldError):
        mud_worlds.validate_resource(resource)


def test_builder_sets_resource(app):
    with app.app_context():
        db = get_db()
        _user, _character_id = _setup(db)
        builder, _ = _scholar(db, "builder")
        db.execute("INSERT INTO mud_builder_permissions(user_id) VALUES(?)", (builder["id"],))
        outsider, _ = _scholar(db, "outsider")
        db.commit()
    clients = {}
    for name, user in (("builder", builder), ("outsider", outsider)):
        clients[name] = app.test_client()
        with clients[name].session_transaction() as session:
            session["user_id"] = user["id"]
            session["csrf_token"] = "test-token"
    data = {"csrf_token": "test-token", "enabled": "1", "name": "  Soul   Power "}
    assert clients["outsider"].post("/mud/w/stationnet/builder/resource", data=data).status_code == 403
    page = clients["builder"].post("/mud/w/stationnet/builder/resource", data=data, follow_redirects=True)
    assert page.status_code == 200 and b"Soul Power is now" in page.data
    page = clients["builder"].post("/mud/w/stationnet/builder/resource", data={**data, "name": "Bad1"},
                                   follow_redirects=True)
    assert b"Resource name must be" in page.data
    with app.app_context():
        assert mud.resource_settings(get_db()) == {"enabled": True, "name": "Soul Power", "noun": "soul power"}
    clients["builder"].post("/mud/w/stationnet/builder/resource", data={"csrf_token": "test-token", "name": "Soul Power"})
    with app.app_context():
        assert not mud.resource_settings(get_db())["enabled"]
    page = clients["builder"].get("/mud/w/stationnet")
    assert page.status_code == 200
    assert b"data-character-mana-meter hidden" in page.data and b'"cast"' not in page.data
    with app.app_context():
        db = get_db()
        newcomer = db.execute("INSERT INTO users(username,callsign,password_hash) "
                              "VALUES('newcomer','NEW','unused')").lastrowid
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = newcomer
        session["csrf_token"] = "test-token"
    create = client.get("/mud/w/stationnet/create").data.decode()
    assert "(unavailable in this world)" in create
    assert "Raises healing and experience earned." in create and "mana/level" not in create
