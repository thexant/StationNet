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


def _post_pack(client, document, *, follow_redirects=False):
    return client.post("/sysop/settings/mud-origins/pack/import", data={
        "csrf_token": "test-token",
        "origin_pack": (BytesIO(json.dumps(document).encode()), "origins.json"),
    }, follow_redirects=follow_redirects)


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


def test_pack_import_is_atomic_and_bulk_removal_handles_both_kinds(data_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                       "VALUES('Test','Test','TST',1,1)")
            admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                               "VALUES('admin','ADM','unused',1)").lastrowid
            db.commit()
        client = app.test_client()
        _login(client, admin)
        race = {**_template("races"), "key": "marshfolk", "name": "Marshfolk"}
        invalid_class = {**_template("classes"), "key": "trailwarden",
                         "name": "Trail Warden", "skill": "unknown"}
        rejected = _post_pack(client, {"races": [race], "classes": [invalid_class]},
                              follow_redirects=True)
        assert b"Invalid class at classes[1]" in rejected.data
        with app.app_context():
            assert get_db().execute(
                "SELECT 1 FROM mud_origin_definitions WHERE key='marshfolk'"
            ).fetchone() is None

        archetype = {**_template("classes"), "key": "trailwarden", "name": "Trail Warden"}
        imported = _post_pack(client, {"races": [race], "classes": [archetype]},
                              follow_redirects=True)
        assert b"Imported origin pack: 1 races, 1 classes" in imported.data
        assert b"SELECT DEFAULT" in imported.data and b"SELECT IMPORTED" in imported.data
        assert b"DEFAULT" in imported.data and b"IMPORTED" in imported.data
        assert b"origin-removal-summary" in imported.data
        assert b'data-remove-name="Marshfolk"' in imported.data
        assert b"settings-mud.js" in imported.data
        assert b"<script>" not in imported.data
        assert b"script-src 'self'" in imported.headers["Content-Security-Policy"].encode()
        assert b"unsafe-inline" not in imported.headers["Content-Security-Policy"].encode()
        assert client.get("/static/settings-mud.js").status_code == 200
        with app.app_context():
            available = character_creation.options(get_db())
            assert "marshfolk" in {item["key"] for item in available["races"]}
            assert "trailwarden" in {item["key"] for item in available["classes"]}

        unconfirmed = client.post("/sysop/settings/mud-origins/remove", data={
            "csrf_token": "test-token", "races": ["marshfolk"],
            "classes": ["trailwarden"],
        }, follow_redirects=True)
        assert b"confirm bulk removal" in unconfirmed.data
        with app.app_context():
            assert get_db().execute(
                "SELECT COUNT(*) FROM mud_origin_definitions "
                "WHERE key IN ('marshfolk','trailwarden')"
            ).fetchone()[0] == 2

        removed = client.post("/sysop/settings/mud-origins/remove", data={
            "csrf_token": "test-token", "races": ["marshfolk"],
            "classes": ["trailwarden"], "confirm_remove": "on",
        }, follow_redirects=True)
        assert b"Removed 2 imported choices: Marshfolk, Trail Warden" in removed.data
        with app.app_context():
            assert get_db().execute(
                "SELECT 1 FROM mud_origin_definitions WHERE key IN ('marshfolk','trailwarden')"
            ).fetchone() is None

        race_only = {**_template("races"), "key": "reedfolk", "name": "Reedfolk"}
        class_only = {**_template("classes"), "key": "wayfinder", "name": "Wayfinder"}
        assert b"Imported origin pack: 1 races" in _post_pack(
            client, {"races": [race_only]}, follow_redirects=True
        ).data
        assert b"Imported origin pack: 1 classes" in _post_pack(
            client, {"classes": [class_only]}, follow_redirects=True
        ).data
    finally:
        coordinator = app.extensions.get("broadcast_coordinator")
        if coordinator:
            coordinator._closed = True
            coordinator._timer.cancel()


def test_pack_rejects_duplicate_keys_and_names_without_partial_install(data_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(data_path)})
    try:
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO station(name,description,callsign) VALUES('Test','Test','TST')")
            admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                               "VALUES('admin','ADM','unused',1)").lastrowid
            db.commit()
        client = app.test_client()
        _login(client, admin)
        first = {**_template("races"), "key": "reedfolk", "name": "Reedfolk"}
        duplicate_key = {**first, "name": "Fenfolk"}
        response = _post_pack(client, {"races": [first, duplicate_key]}, follow_redirects=True)
        assert b"Duplicate race key in pack: reedfolk" in response.data
        with app.app_context():
            assert get_db().execute(
                "SELECT 1 FROM mud_origin_definitions WHERE key='reedfolk'"
            ).fetchone() is None

        duplicate_name = {**first, "key": "fenfolk", "name": "Human"}
        response = _post_pack(client, {"races": [duplicate_name]}, follow_redirects=True)
        assert b"A race named Human is already installed" in response.data
        assert client.get("/sysop/settings/mud-origins/pack/template").status_code == 200
    finally:
        coordinator = app.extensions.get("broadcast_coordinator")
        if coordinator:
            coordinator._closed = True
            coordinator._timer.cancel()
