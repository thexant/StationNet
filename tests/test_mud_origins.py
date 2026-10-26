"""Sysop-managed origins feed character creation and preserve existing saves."""
import json
from io import BytesIO
from pathlib import Path
import shutil
import uuid

import pytest

from stationnet import create_app, character_creation, mud
from stationnet.db import get_db


@pytest.fixture
def data_path():
    path = Path(__file__).parent / f"_mud_origins_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path)


def _login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"


def _post_json(client, kind, document):
    return client.post(f"/sysop/settings/mud-origins/{kind}/import", data={
        "csrf_token": "test-token",
        "origin_file": (BytesIO(json.dumps(document).encode()), f"{kind}.json"),
    })


def _template(kind):
    path = Path(character_creation.__file__).with_name("mud_origin_templates") / f"{character_creation.KIND_NAMES[kind]}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_import_availability_and_existing_characters(data_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
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
        client = app.test_client()
        _login(client, player)
        assert client.post(
            "/sysop/settings/mud-origins/races/import", data={"csrf_token": "test-token"}
        ).status_code == 302
        with app.app_context():
            assert get_db().execute("SELECT 1 FROM mud_origin_definitions").fetchone() is None
        _login(client, admin)
        assert b"Races and Classes" in client.get("/sysop/settings").data
        assert client.get("/sysop/settings/mud-origins/races/template").status_code == 200
        invalid_class = {**_template("classes"), "skill": ["guard"]}
        assert _post_json(client, "classes", invalid_class).status_code == 302
        with app.app_context():
            assert get_db().execute("SELECT 1 FROM mud_origin_definitions").fetchone() is None
        assert _post_json(client, "races", _template("races")).status_code == 302
        assert _post_json(client, "classes", _template("classes")).status_code == 302
        with app.app_context():
            db = get_db()
            assert {item["key"] for item in character_creation.options(db)["races"]} >= {"riverfolk"}
            assert {item["key"] for item in character_creation.options(db)["classes"]} >= {"pathfinder"}
        _login(client, player)
        page = client.get("/mud/create")
        assert b"Riverfolk" in page.data and b"Pathfinder" in page.data
        assert b"Riverfolk (imported)" not in page.data
        assert b"Pathfinder (imported)" not in page.data
        build = {"name": "River Walker", "race": "riverfolk", "class": "pathfinder",
                 "attributes": dict.fromkeys(character_creation.ATTRIBUTES, 11),
                 "skills": ["guard", "lore"]}
        assert client.post("/mud/create", data={"csrf_token": "test-token",
                                                "build": json.dumps(build)}).status_code == 302
        _login(client, admin)
        availability = client.post("/sysop/settings/mud-origins/availability", data={
            "csrf_token": "test-token", "races": ["human"], "classes": ["warrior"]})
        assert availability.status_code == 302
        with app.app_context():
            db = get_db()
            assert [item["key"] for item in character_creation.options(db)["races"]] == ["human"]
            assert [item["key"] for item in character_creation.options(db)["classes"]] == ["warrior"]
            state = mud.character_state(db, player)
            assert state["race"] == "Riverfolk" and state["class"] == "Pathfinder"
            assert mud._character_effects(db, state["id"])["defense"] >= 2
            newcomer = db.execute("INSERT INTO users(username,callsign,password_hash) "
                                  "VALUES('newcomer','NEW','unused')").lastrowid
            db.commit()
        _login(client, newcomer)
        assert b"Riverfolk" not in client.get("/mud/create").data
        rejected = client.post("/mud/create", data={"csrf_token": "test-token",
                                                    "build": json.dumps({**build, "name": "New Walker"})})
        assert rejected.status_code == 200 and b"Choose a valid race" in rejected.data
        _login(client, admin)
        for kind, key in (("races", "riverfolk"), ("classes", "pathfinder")):
            response = client.post(f"/sysop/settings/mud-origins/{kind}/remove", data={
                "csrf_token": "test-token", "key": key, "confirm_remove": "on"})
            assert response.status_code == 302
        with app.app_context():
            db = get_db()
            assert all(entry["key"] != "riverfolk" for entry in character_creation.catalog(db)["races"])
            state = mud.character_state(db, player)
            assert state["race"] == "Riverfolk" and state["class"] == "Pathfinder"
            assert mud._known_spells(db, state["id"]) == ("ward",)
    finally:
        coordinator = app.extensions.get("broadcast_coordinator")
        if coordinator:
            coordinator._closed = True
            coordinator._timer.cancel()


def test_empty_choices_cannot_support_enabled_mud(data_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_procedural_enabled) "
                       "VALUES('Test','Test','TST',1,1)")
            admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                               "VALUES('admin','ADM','unused',1)").lastrowid
            db.commit()
        client = app.test_client()
        _login(client, admin)
        response = client.post("/sysop/settings/mud-origins/availability", data={
            "csrf_token": "test-token", "races": ["human"]})
        assert response.status_code == 302
        with app.app_context():
            assert character_creation.active_counts(get_db())["classes"] > 0
        assert _post_json(client, "races", _template("races")).status_code == 302
        assert _post_json(client, "classes", _template("classes")).status_code == 302
        client.post("/sysop/settings/mud-origins/availability", data={
            "csrf_token": "test-token", "races": ["riverfolk"], "classes": ["pathfinder"]})
        client.post("/sysop/settings/mud-origins/races/remove", data={
            "csrf_token": "test-token", "key": "riverfolk", "confirm_remove": "on"})
        with app.app_context():
            db = get_db()
            assert character_creation.active_counts(db) == {"races": 1, "classes": 1}
            assert any(entry["key"] == "riverfolk" for entry in character_creation.catalog(db)["races"])
        # Choices may be cleared while the entire MUD is off, but enabling it is rejected.
        with app.app_context():
            db = get_db()
            db.execute("UPDATE station SET mud_enabled=0,mud_procedural_enabled=0")
            db.commit()
        client.post("/sysop/settings/mud-origins/availability", data={"csrf_token": "test-token"})
        with app.app_context():
            assert character_creation.active_counts(get_db()) == {"races": 0, "classes": 0}
        client.post("/sysop/settings/mud", data={
            "csrf_token": "test-token", "mud_traditional_enabled": "on"})
        with app.app_context():
            assert get_db().execute("SELECT mud_enabled FROM station").fetchone()[0] == 0
    finally:
        coordinator = app.extensions.get("broadcast_coordinator")
        if coordinator:
            coordinator._closed = True
            coordinator._timer.cancel()
