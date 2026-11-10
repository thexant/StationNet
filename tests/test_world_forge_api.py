"""World Forge end to end: drafts, publishing, live-world actions, and who may do what."""
import io
import json
from pathlib import Path
import shutil
import uuid
import zipfile

import pytest

from stationnet import create_app, character_creation, mud, mud_scope, mud_worlds
from stationnet.db import get_db
from stationnet.world_forge.storage import Store

TOKEN = "forge-token"
HEADERS = {"X-CSRF-Token": TOKEN}


@pytest.fixture
def app():
    data = Path(__file__).parent / f"_forge_{uuid.uuid4().hex}"
    data.mkdir()
    application = create_app({"TESTING": True, "DATA_DIR": str(data)})
    with application.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        for name, admin in (("sysop", 1), ("builder", 0), ("visitor", 0)):
            db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,?)",
                       (name, name.upper()[:3] + "-1", "unused", admin))
        builder = db.execute("SELECT id FROM users WHERE username='builder'").fetchone()[0]
        db.execute("INSERT INTO mud_builder_permissions(user_id,granted_by) VALUES(?,1)", (builder,))
        db.commit()
    yield application
    application.extensions["broadcast_coordinator"]._closed = True
    application.extensions["broadcast_coordinator"]._timer.cancel()
    shutil.rmtree(data, ignore_errors=True)


def _client(app, username):
    client = app.test_client()
    with app.app_context():
        user_id = get_db().execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()[0]
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = TOKEN
    return client


class Forge:
    def __init__(self, client):
        self.client = client

    def post(self, path, data=None, status=200):
        response = self.client.post(f"/mud/forge/api{path}", json=data or {}, headers=HEADERS)
        assert response.status_code == status, response.get_data(as_text=True)
        return response.get_json()

    def get(self, path, status=200):
        response = self.client.get(f"/mud/forge/api{path}")
        assert response.status_code == status, response.get_data(as_text=True)
        return response.get_json()

    def tx(self, draft, operation, args=None):
        result = self.post(f"/d/{draft['id']}/transaction",
                           {"revision": draft["revision"], "operation": operation, "args": args or {}})
        draft.update(result)
        return result

    def publish(self, draft, **options):
        preview = self.post(f"/d/{draft['id']}/publish/preview")
        assert not preview["blocked"], preview
        result = self.post(f"/d/{draft['id']}/publish", {"revision": draft["revision"], "hash": preview["hash"],
                                                          **options})
        draft.update(result["draft"])
        return preview, result


def test_only_builders_reach_world_forge(app):
    assert app.test_client().get("/mud/forge/").status_code == 302
    assert _client(app, "visitor").get("/mud/forge/").status_code == 403
    assert _client(app, "visitor").get("/mud/forge/api/worlds").status_code == 403
    page = _client(app, "builder").get("/mud/forge/")
    assert page.status_code == 200 and b'id="world-forge"' in page.data
    # The old form-based builder is gone; World Forge replaces it.
    for old in ("/mud/builder", "/mud/w/stationnet/builder", "/mud/w/stationnet/builder/wilderness"):
        assert _client(app, "builder").get(old).status_code == 404
    # Mutations need the CSRF token, like every other StationNet form.
    no_token = _client(app, "builder").post("/mud/forge/api/worlds/new", json={"id": "x-world"})
    assert no_token.status_code == 400
    # Builder access is managed by SYSOPs only.
    assert _client(app, "builder").get("/mud/forge/api/builders").status_code == 302
    sysop = Forge(_client(app, "sysop"))
    visitor = next(user for user in sysop.get("/builders") if user["username"] == "visitor")
    assert not visitor["builder"]
    sysop.post(f"/builders/{visitor['id']}", {"enabled": True})
    assert _client(app, "visitor").get("/mud/forge/").status_code == 200


def test_new_world_is_authored_validated_and_installed_hidden(app):
    forge = Forge(_client(app, "builder"))
    draft = forge.post("/worlds/new", {"id": "api-world", "name": "API World", "author": "Tester"})
    assert draft["kind"] == "new" and draft["revision"] == 0
    forge.post("/worlds/new", {"id": "stationnet", "name": "Home", "author": "Me"}, status=400)
    forge.tx(draft, "create_record", {"collection": "rooms", "record": {
        "id": "market", "name": "Market", "description": "A market.", "x": 1, "y": 0, "z": 0}})
    forge.tx(draft, "connect", {"from": "arrival", "to": "market", "direction": "east"})
    forge.tx(draft, "create_record", {"collection": "mud_npc_definitions", "record": {
        "id": "keeper", "name": "Keeper", "description": "A keeper."}})
    forge.tx(draft, "create_record", {"collection": "mud_npc_instances", "record": {
        "id": "placed-keeper", "definition_id": "keeper", "room_id": "market"}})
    forge.tx(draft, "create_record", {"collection": "mud_quests", "record": {
        "id": "visit", "name": "Visit market", "description": "Find the market."}})
    stale = forge.post(f"/d/{draft['id']}/transaction", {"revision": 0, "operation": "undo"}, status=409)
    assert "changed" in stale["detail"]
    issues = forge.post(f"/d/{draft['id']}/validate")
    assert not [issue for issue in issues if issue["severity"] == "error"], issues
    map_data = forge.get(f"/d/{draft['id']}/map?x=0&y=0&width=3&height=2")
    assert len(map_data["cells"]) == 2 and map_data["cells"][1]["markers"]
    assert forge.get(f"/d/{draft['id']}/route?start=arrival&end=market")["path"] == ["arrival", "market"]
    assert forge.client.get(f"/mud/forge/api/d/{draft['id']}/export").status_code == 200

    preview, result = forge.publish(draft)
    assert preview["mode"] == "install" and result["summary"]["installed"]
    assert draft["kind"] == "existing" and draft["id"] == "w:api-world"
    with app.app_context():
        world = get_db().execute("SELECT enabled,imported FROM mud_world_packages WHERE id='api-world'").fetchone()
        assert world["enabled"] == 0 and world["imported"] == 1
    listed = forge.get("/worlds")
    assert any(world["key"] == "api-world" and world["draft"] for world in listed["worlds"])
    assert not listed["drafts"]


def test_drafts_survive_a_restart_and_keep_backups(app):
    forge = Forge(_client(app, "builder"))
    draft = forge.post("/worlds/new", {"id": "keep-world", "name": "Keep", "author": "Tester"})
    forge.tx(draft, "set_field", {"collection": "rooms", "index": 0, "path": ["name"], "value": "Kept room"})
    forge.post(f"/d/{draft['id']}/checkpoint", {"revision": draft["revision"]})
    forge.tx(draft, "set_field", {"collection": "rooms", "index": 0, "path": ["name"], "value": "Later room"})
    # A new process sees the saved draft (undo history is in memory only).
    app.extensions["world_forge"] = Store(app.extensions["world_forge"].root)
    reopened = forge.get(f"/d/{draft['id']}")
    assert reopened["files"]["rooms.json"][0]["name"] == "Later room" and not reopened["canUndo"]
    backups = forge.get(f"/d/{draft['id']}/backups")
    assert backups[0]["reason"] == "checkpoint"
    restored = forge.post(f"/d/{draft['id']}/restore", {"revision": reopened["revision"], "backup": backups[0]["id"]})
    assert restored["files"]["rooms.json"][0]["name"] == "Kept room" and restored["canUndo"]
    forge.post(f"/d/{draft['id']}/restore", {"revision": restored["revision"], "backup": "../../escape"}, status=400)
    # Draft IDs are fixed; duplicating gives a new world.
    bad = forge.post(f"/d/{draft['id']}/transaction", {"revision": restored["revision"], "operation": "set_field",
                                                        "args": {"collection": "manifest", "path": ["id"],
                                                                 "value": "other"}}, status=400)
    assert "duplicate" in bad["detail"].lower()
    copy = forge.post(f"/d/{draft['id']}/duplicate", {"world_id": "keep-copy"})
    assert copy["id"] == "n:keep-copy"
    forge.post(f"/d/{copy['id']}/discard", {"revision": copy["revision"]})
    assert [entry["id"] for entry in forge.get("/worlds")["drafts"]] == ["keep-world"]


def test_zip_import_and_preset_and_dungeon_tools(app):
    forge = Forge(_client(app, "builder"))
    payload = mud_worlds.example_bytes()
    response = forge.client.post("/mud/forge/api/worlds/import?id=imported-isles", data=payload,
                                 headers={**HEADERS, "Content-Type": "application/zip"})
    assert response.status_code == 200, response.get_data(as_text=True)
    draft = response.get_json()
    assert draft["files"]["manifest.json"]["id"] == "imported-isles"
    catalog = forge.get("/catalog")
    preset = catalog["presets"][0]["key"]
    forge.tx(draft, "add_bundled_preset", {"key": preset})
    exported = forge.client.get(f"/mud/forge/api/d/{draft['id']}/presets/export?key={preset}")
    presets = json.loads(exported.get_data())["presets"]
    assert presets[0]["key"] == preset
    presets[0]["name"] = "Imported copy"
    forge.tx(draft, "import_presets", {"presets": {"presets": presets}, "conflict": "overwrite"})
    assert any(row.get("name") == "Imported copy" for row in draft["files"]["wilderness.json"]["presets"])
    theme = catalog["themes"][0]["key"]
    forge.tx(draft, "new_dungeon_from_base", {"base": f"theme:{theme}", "name": "Sample Depths"})
    template = draft["files"]["dungeons.json"]["templates"][-1]
    preview = forge.post(f"/d/{draft['id']}/dungeon-preview", {"template": template["key"], "seed": "abc"})
    assert preview["seed"] == "abc" and preview["text"]
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM mud_world_packages WHERE id LIKE 'forge-preview-%'"
                                ).fetchone()[0] == 0


def _player(db, room_id):
    uid = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('wren','WRN-1','x')").lastrowid
    character_creation.complete(db, uid, {
        "name": "Wren", "race": "human", "class": "warrior",
        "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
        "skills": ["lore", "guard"]})
    mud._set_player_location(db, uid, "traditional", "world_room", room_id)
    return uid


def test_home_world_is_edited_and_published_with_light_and_full_upgrades(app):
    with app.app_context(), mud_scope.use(mud_scope.HOME):
        db = get_db()
        start = mud._start_room(db, mud_scope.HOME)["id"]
        uid = _player(db, start)
        db.commit()
    forge = Forge(_client(app, "builder"))
    draft = forge.post("/open", {"world": mud_scope.HOME})
    assert draft["id"] == "w:stationnet" and draft["files"]["manifest.json"]["id"] == "stationnet"
    # A text-only edit is published in place.
    forge.tx(draft, "set_field", {"collection": "rooms", "index": 0, "path": ["description"],
                                  "value": "A freshly painted room."})
    preview, result = forge.publish(draft)
    assert preview["mode"] == "light" and preview["first_home_publish"]
    assert result["summary"]["light"]
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT description FROM mud_world_rooms WHERE id=?", (start,)).fetchone()[0] == \
            "A freshly painted room."
    # A new room is structural and runs the full upgrade; the player keeps their place.
    forge.tx(draft, "create_record", {"collection": "rooms", "record": {
        "id": "garden", "name": "Garden", "description": "Green and quiet."}})
    forge.tx(draft, "connect", {"from": draft["files"]["rooms.json"][0]["id"], "to": "garden",
                                "direction": "north"})
    preview, result = forge.publish(draft)
    assert preview["mode"] == "full" and preview["changes"]["rooms"]["added"] == ["garden"]
    assert result["summary"]["moved_to_start"] == 0
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM mud_world_rooms WHERE world_id=? AND name='Garden'",
                          (mud_scope.HOME,)).fetchone()[0] == 1
        located = db.execute("SELECT r.name FROM mud_player_locations l JOIN mud_world_rooms r "
                             "ON r.id=l.location_key WHERE l.user_id=?", (uid,)).fetchone()
        assert located["name"] == "Starting Room"
        # Characters in the world hear that it changed, even if they were away when it did.
        assert db.execute("SELECT COUNT(*) FROM mud_event_recipients WHERE user_id=?", (uid,)).fetchone()[0] >= 1
    history = forge.get(f"/d/{draft['id']}/history")
    assert [entry["light"] for entry in history] == [False, True]


def test_sysop_only_settings_and_stale_drafts(app):
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, mud_worlds.example_bytes(), enable=True)
        db.commit()
        world = db.execute("SELECT id FROM mud_world_packages WHERE imported=1").fetchone()["id"]
    builder = Forge(_client(app, "builder"))
    draft = builder.post("/open", {"world": world})
    rooms = draft["files"]["rooms.json"]
    builder.tx(draft, "set_field", {"collection": "manifest", "path": ["start_room"], "value": rooms[-1]["id"]})
    preview = builder.post(f"/d/{draft['id']}/publish/preview")
    assert preview["requires_admin"] == ["start_room"] and preview["blocked"]
    builder.post(f"/d/{draft['id']}/publish", {"revision": draft["revision"], "hash": preview["hash"]}, status=400)
    sysop = Forge(_client(app, "sysop"))
    sysop_draft = sysop.get(f"/d/{draft['id']}")
    assert sysop_draft["revision"] == draft["revision"]
    # Another change reaches the published world first: the draft is now stale.
    with app.app_context():
        db = get_db()
        document, _ = mud_worlds.export_document(db, world)
        document["name"] = "Renamed elsewhere"
        payload = mud_worlds.package_bytes(document)
        mud_worlds.upgrade(db, payload, mud_worlds.package_hash(payload))
    preview = sysop.post(f"/d/{draft['id']}/publish/preview")
    assert preview["stale"] and not preview["requires_admin"] == ["progression"]
    refused = sysop.post(f"/d/{draft['id']}/publish", {"revision": sysop_draft["revision"], "hash": preview["hash"]},
                         status=400)
    assert "changed since" in refused["detail"]
    sysop.post(f"/d/{draft['id']}/publish", {"revision": sysop_draft["revision"], "hash": preview["hash"],
                                              "accept_stale": True})
    with app.app_context():
        start = get_db().execute("SELECT r.name FROM mud_world_packages p JOIN mud_world_rooms r "
                                 "ON r.id=p.start_room_id WHERE p.id=?", (world,)).fetchone()["name"]
        assert start == rooms[-1]["name"]


def test_live_world_actions(app):
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, mud_worlds.example_bytes(), enable=True)
        db.commit()
        world = db.execute("SELECT id FROM mud_world_packages WHERE imported=1").fetchone()["id"]
    forge = Forge(_client(app, "builder"))
    summary = forge.get(f"/w/{world}/live")
    assert summary["characters"] == 0 and summary["holdings"] == []
    forge.post(f"/w/{world}/live/scripts/reset", {"confirm": "nope"}, status=400)
    assert "reset" in forge.post(f"/w/{world}/live/scripts/reset", {"confirm": "reset"})["message"]
    forge.post(f"/w/{world}/live/holdings/holding:missing/release", {"confirm": "release"}, status=400)
    assert forge.client.get(f"/mud/forge/api/w/{world}/published/export").status_code == 200
    assert forge.client.get(f"/mud/forge/api/w/{world}/published/check").status_code == 302
    assert Forge(_client(app, "sysop")).get(f"/w/{world}/published/check") == []
