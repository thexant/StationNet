"""Authored professions, repeatable payouts, and portable work definitions."""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import uuid

import pytest

from stationnet import character_creation, create_app, mud, mud_worlds
from stationnet.db import get_db


@pytest.fixture
def game():
    data = Path(__file__).parent / f"_work_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        uid = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                         "VALUES('worker','WRK','unused',1)").lastrowid
        character_creation.complete(db, uid, {
            "name": "Worker", "race": "human", "class": "warrior",
            "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
            "skills": ["lore", "guard"]})
        db.commit()
    yield app, uid
    app.extensions["broadcast_coordinator"]._closed = True
    app.extensions["broadcast_coordinator"]._timer.cancel()
    shutil.rmtree(data)


def _document():
    activities = [
        {"id": "sweep", "profession_id": "porter", "name": "Sweep floor",
         "description": "Sweep the workshop.", "kind": "immediate", "room_id": "square",
         "practice_skill_key": "lore", "cooldown_seconds": 60},
    ]
    for goal, target in (("visit_room", "woods"), ("talk_npc", "helper"),
                         ("defeat_npc", "rat"), ("possess_item", "ore"), ("deliver_item", "ore")):
        activities.append({"id": goal.replace("_", "-"), "profession_id": "porter",
                           "name": goal.replace("_", " ").title(), "description": f"Do {goal}.",
                           "kind": "assignment", "provider_npc_definition_id": "keeper",
                           "objective_type": goal, "objective_target_id": target,
                           "required_count": 1, "cooldown_seconds": 60})
    return {
        "id": "workland", "name": "Workland", "author": "Test", "start_room": "square",
        "rooms": [{"id": "square", "name": "Town Square", "description": "A busy square."},
                  {"id": "woods", "name": "Near Woods", "description": "A path through trees."}],
        "exits": [],
        "content": {
            "mud_item_definitions": [{"id": "ore", "name": "Ore", "description": "A useful chunk.",
                                      "item_type": "misc"}],
            "mud_npc_definitions": [{"id": key, "name": key.title(), "description": key.title()}
                                    for key in ("keeper", "helper", "rat")],
            "mud_npc_instances": [{"id": key + "-here", "definition_id": key, "room_id": "square"}
                                  for key in ("keeper", "helper")],
            "mud_professions": [{"id": "porter", "name": "Porter", "description": "Useful town work.",
                                 "recruiter_npc_definition_id": "keeper"}],
            "mud_work_activities": activities,
            "mud_work_rewards": [{"id": activity["id"] + "-pay", "activity_id": activity["id"],
                                  "reward_type": "coins", "amount": 10} for activity in activities],
        },
    }


def _setup(app, uid):
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, mud_worlds.package_bytes(_document()))
        mud_worlds.activate(db, "workland")
        db.commit()
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return user


def _activity(db, name):
    return db.execute("SELECT a.*,p.name AS profession_name FROM mud_work_activities a "
                      "JOIN mud_professions p ON p.id=a.profession_id WHERE a.name=?", (name,)).fetchone()


def _call(db, user, *args):
    return mud._work_action(db, user, {"arguments": list(args)})


def test_immediate_work_join_cooldown_and_skill(game):
    app, uid = game
    _setup(app, uid)
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        cid = f"stationnet:character:{uid}"
        with pytest.raises(mud.ActionError, match="Join Porter"):
            _call(db, user, "do", "Sweep floor")
        mud._profession_action(db, user, {"arguments": ["join", "Porter"]})
        before = mud._wallet(db, cid)
        assert "10 coins" in _call(db, user, "do", "Sweep floor")[0].body
        assert mud._wallet(db, cid) == before + 10
        with pytest.raises(mud.ActionError, match="cooling down"):
            _call(db, user, "do", "Sweep floor")
        assert db.execute("SELECT experience FROM mud_character_skills WHERE character_id=? AND skill_key='lore'",
                          (cid,)).fetchone()[0] > 0
        mud._profession_action(db, user, {"arguments": ["leave", "Porter"]})
        mud._profession_action(db, user, {"arguments": ["join", "Porter"]})
        with pytest.raises(mud.ActionError, match="cooling down"):
            _call(db, user, "do", "Sweep floor")


@pytest.mark.parametrize("goal,target", [
    ("visit_room", "woods"), ("talk_npc", "helper"), ("defeat_npc", "rat")])
def test_assignment_event_progress_and_turnin(game, goal, target):
    app, uid = game
    _setup(app, uid)
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        mud._profession_action(db, user, {"arguments": ["join", "Porter"]})
        activity = _activity(db, goal.replace("_", " ").title())
        _call(db, user, "accept", activity["name"])
        with pytest.raises(mud.ActionError, match="not finished"):
            _call(db, user, "turnin", activity["name"])
        ids = {"woods": db.execute("SELECT id FROM mud_world_rooms WHERE name='Near Woods'").fetchone()[0],
               "helper": db.execute("SELECT id FROM mud_npc_definitions WHERE name='Helper'").fetchone()[0],
               "rat": db.execute("SELECT id FROM mud_npc_definitions WHERE name='Rat'").fetchone()[0]}
        if goal == "talk_npc":
            mud._talk_action(db, user, {"arguments": ["Helper"]})
        else:
            mud._set_objective_progress(db, user, goal, ids[target])
        assert "10 coins" in _call(db, user, "turnin", activity["name"])[0].body
        with pytest.raises(mud.ActionError, match="Accept this job first"):
            _call(db, user, "turnin", activity["name"])
        with pytest.raises(mud.ActionError, match="cooling down"):
            _call(db, user, "accept", activity["name"])


def test_item_goals_consume_and_abandon_resets_progress(game):
    app, uid = game
    _setup(app, uid)
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        cid = f"stationnet:character:{uid}"
        ore = db.execute("SELECT id FROM mud_item_definitions WHERE name='Ore'").fetchone()[0]
        mud._profession_action(db, user, {"arguments": ["join", "Porter"]})
        _call(db, user, "accept", "Possess Item")
        mud._grant_item(db, uid, ore)
        mud._set_objective_progress(db, user, "possess_item", ore)
        assert "10 coins" in _call(db, user, "turnin", "Possess Item")[0].body
        assert db.execute("SELECT COUNT(*) FROM mud_item_instances WHERE owner_character_id=? AND definition_id=?",
                          (cid, ore)).fetchone()[0] == 0
        _call(db, user, "accept", "Visit Room")
        woods = db.execute("SELECT id FROM mud_world_rooms WHERE name='Near Woods'").fetchone()[0]
        mud._set_objective_progress(db, user, "visit_room", woods)
        _call(db, user, "abandon", "Visit Room")
        _call(db, user, "accept", "Visit Room")
        with pytest.raises(mud.ActionError, match="not finished"):
            _call(db, user, "turnin", "Visit Room")
        _call(db, user, "accept", "Deliver Item")
        mud._grant_item(db, uid, ore)
        _call(db, user, "deliver", "Deliver Item")
        assert "10 coins" in _call(db, user, "turnin", "Deliver Item")[0].body
        assert db.execute("SELECT COUNT(*) FROM mud_item_instances WHERE owner_character_id=? AND definition_id=?",
                          (cid, ore)).fetchone()[0] == 0


def test_package_roundtrip_and_invalid_refs(game):
    app, uid = game
    original = _document()
    assert mud_worlds.read_package(mud_worlds.package_bytes(original))["content"]["mud_professions"]
    broken = copy.deepcopy(original)
    broken["content"]["mud_work_activities"][0]["room_id"] = "missing"
    with pytest.raises(mud_worlds.WorldError, match="room"):
        mud_worlds.read_package(mud_worlds.package_bytes(broken))
    _setup(app, uid)
    with app.app_context():
        db = get_db()
        payload = mud_worlds.export(db, "workland")
        exported = mud_worlds.read_package(payload)["content"]
        assert len(exported["mud_professions"]) == 1
        assert len(exported["mud_work_activities"]) == 6
        assert len(exported["mud_work_rewards"]) == 6
        mud_worlds.activate(db, "stationnet")
        mud_worlds.remove(db, "workland")
        mud_worlds.install(db, payload)
        assert db.execute("SELECT COUNT(*) FROM mud_work_activities a JOIN mud_professions p "
                          "ON p.id=a.profession_id WHERE p.world_id='workland'").fetchone()[0] == 6


def test_builder_and_action_request_deduplication(game):
    app, uid = game
    _setup(app, uid)
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = uid
        session["csrf_token"] = "test-token"
    page = client.get("/mud/builder")
    assert page.status_code == 200 and b"Professions and work" in page.data
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        mud._profession_action(db, user, {"arguments": ["join", "Porter"]})
        db.commit()
        cid = f"stationnet:character:{uid}"
        before = mud._wallet(db, cid)
    data = {"csrf_token": "test-token", "command": "/work do Sweep floor", "request_id": "work-payment-one"}
    first = client.post("/mud/actions", data=data)
    assert first.status_code == 200
    repeat = client.post("/mud/actions", data=data)
    assert repeat.status_code == 200
    blocked = client.post("/mud/actions", data={**data, "request_id": "work-payment-two"})
    assert blocked.status_code == 400 or blocked.status_code == 409
    with app.app_context():
        db = get_db()
        assert mud._wallet(db, cid) == before + 10
        assert db.execute("SELECT completions FROM mud_character_work WHERE activity_id=? AND character_id=?",
                          (_activity(db, "Sweep floor")["id"], cid)).fetchone()[0] == 1


def test_builder_creates_work_and_campaign_restores_progress(game):
    app, uid = game
    _setup(app, uid)
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = uid
        session["csrf_token"] = "test-token"
    with app.app_context():
        db = get_db()
        keeper = db.execute("SELECT id FROM mud_npc_definitions WHERE name='Keeper'").fetchone()[0]
        square = db.execute("SELECT id FROM mud_world_rooms WHERE name='Town Square'").fetchone()[0]
    result = client.post("/mud/builder/professions", data={"csrf_token": "test-token", "name": "Courier",
        "description": "Carry local messages.", "recruiter_npc_definition_id": keeper,
        "conditions_json": "[]"})
    assert result.status_code == 302
    with app.app_context():
        db = get_db()
        profession = db.execute("SELECT id FROM mud_professions WHERE name='Courier'").fetchone()[0]
    result = client.post(f"/mud/builder/professions/{profession}/activities", data={
        "csrf_token": "test-token", "name": "Sort letters", "description": "Sort the post.",
        "kind": "immediate", "room_id": square, "provider_npc_definition_id": "",
        "conditions_json": "[]", "tool_definition_id": "", "practice_skill_key": "",
        "cooldown_seconds": "60", "objective_type": "", "objective_target_id": "",
        "required_count": "1", "reward_type": "coins", "reward_target_id": "", "reward_amount": "3"})
    assert result.status_code == 302
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT 1 FROM mud_work_activities WHERE name='Sort letters'").fetchone()
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        mud._profession_action(db, user, {"arguments": ["join", "Porter"]})
        _call(db, user, "accept", "Visit Room")
        db.commit()
        other = _document()
        other["id"] = "otherwork"
        mud_worlds.install(db, mud_worlds.package_bytes(other))
        mud_worlds.activate(db, "otherwork", campaign="new")
        archive = db.execute("SELECT id FROM mud_campaign_archives WHERE world_id='workland' "
                             "ORDER BY id DESC LIMIT 1").fetchone()[0]
        mud_worlds.activate(db, "workland", campaign="restore", archive_id=archive)
        cid = f"stationnet:character:{uid}"
        assert db.execute("SELECT 1 FROM mud_character_professions WHERE character_id=? AND profession_id IN "
                          "(SELECT id FROM mud_professions WHERE name='Porter' AND world_id='workland')",
                          (cid,)).fetchone()
        assert db.execute("SELECT status FROM mud_character_work WHERE character_id=? AND activity_id=?",
                          (cid, _activity(db, "Visit Room")["id"])).fetchone()[0] == "active"
        mud_worlds.activate(db, "otherwork")
        mud_worlds.remove(db, "workland")


def test_simultaneous_payment_requests_only_pay_once(game):
    app, uid = game
    _setup(app, uid)
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        mud._profession_action(db, user, {"arguments": ["join", "Porter"]})
        db.commit()
        cid = f"stationnet:character:{uid}"
        before = mud._wallet(db, cid)

    def submit(number):
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = uid
            session["csrf_token"] = "test-token"
        return client.post("/mud/actions", data={"csrf_token": "test-token",
            "command": "/work do Sweep floor", "request_id": f"simultaneous-work-{number}"}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(submit, (1, 2)))
    assert sorted(statuses) == [200, 400] or sorted(statuses) == [200, 409]
    with app.app_context():
        db = get_db()
        assert mud._wallet(db, cid) == before + 10


def test_work_only_skill_exports_and_unlearned_skill_skips_practice(game):
    app, uid = game
    document = _document()
    document["skills"] = [{"key": "portering", "name": "Portering", "description": "Useful carrying work.",
                            "group": "exploration", "starting": False,
                            "practice": {"triggers": ["work"]}}]
    document["content"]["mud_work_activities"][0]["practice_skill_key"] = "portering"
    with app.app_context():
        db = get_db()
        mud_worlds.install(db, mud_worlds.package_bytes(document))
        exported = mud_worlds.read_package(mud_worlds.export(db, "workland"))
        assert exported["skills"][0]["key"] == "portering"
        assert exported["content"]["mud_work_activities"][0]["practice_skill_key"] == "portering"
        mud_worlds.activate(db, "workland")
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        mud._profession_action(db, user, {"arguments": ["join", "Porter"]})
        assert "10 coins" in _call(db, user, "do", "Sweep floor")[0].body
        cid = f"stationnet:character:{uid}"
        assert db.execute("SELECT 1 FROM mud_character_skills WHERE character_id=? AND skill_key IN "
                          "(SELECT skill_key FROM mud_world_skills WHERE world_id='workland' AND local_key='portering')",
                          (cid,)).fetchone() is None
        db.commit()


def test_requirements_and_all_reward_types(game):
    app, uid = game
    _setup(app, uid)
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        cid = f"stationnet:character:{uid}"
        profession = db.execute("SELECT id FROM mud_professions WHERE name='Porter'").fetchone()[0]
        activity = _activity(db, "Sweep floor")
        ore = db.execute("SELECT id FROM mud_item_definitions WHERE name='Ore'").fetchone()[0]
        db.execute("UPDATE mud_professions SET conditions_json=? WHERE id=?",
                   ('[{"type":"level","min":5}]', profession))
        with pytest.raises(mud.ActionError, match="requirements"):
            mud._profession_action(db, user, {"arguments": ["join", "Porter"]})
        db.execute("UPDATE mud_professions SET conditions_json='[]' WHERE id=?", (profession,))
        mud._profession_action(db, user, {"arguments": ["join", "Porter"]})
        db.execute("UPDATE mud_work_activities SET tool_definition_id=? WHERE id=?", (ore, activity["id"]))
        with pytest.raises(mud.ActionError, match="required tool"):
            _call(db, user, "do", "Sweep floor")
        mud._grant_item(db, uid, ore)
        db.execute("INSERT INTO mud_work_rewards(id,activity_id,reward_type,target_id,amount) VALUES(?,?,?,?,?)",
                   ("ore-bonus", activity["id"], "item", ore, 2))
        db.execute("INSERT INTO mud_work_rewards(id,activity_id,reward_type,target_id,amount) VALUES(?,?,?,?,?)",
                   ("xp-bonus", activity["id"], "experience", None, 20))
        before_xp = db.execute("SELECT SUM(experience) FROM mud_character_progression WHERE character_id=?",
                               (cid,)).fetchone()[0]
        assert "10 coins" in _call(db, user, "do", "Sweep floor")[0].body
        assert db.execute("SELECT COUNT(*) FROM mud_item_instances WHERE owner_character_id=? AND definition_id=?",
                          (cid, ore)).fetchone()[0] == 3
        assert db.execute("SELECT SUM(experience) FROM mud_character_progression WHERE character_id=?",
                          (cid,)).fetchone()[0] > before_xp


def test_character_deletion_cascades_work_state(game):
    app, uid = game
    _setup(app, uid)
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        cid = f"stationnet:character:{uid}"
        mud._profession_action(db, user, {"arguments": ["join", "Porter"]})
        _call(db, user, "do", "Sweep floor")
        db.execute("DELETE FROM users WHERE id=?", (uid,))
        assert db.execute("SELECT 1 FROM mud_character_professions WHERE character_id=?", (cid,)).fetchone() is None
        assert db.execute("SELECT 1 FROM mud_character_work WHERE character_id=?", (cid,)).fetchone() is None


def test_npc_provided_immediate_work_and_disabled_skill(game):
    app, uid = game
    _setup(app, uid)
    with app.app_context():
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        cid = f"stationnet:character:{uid}"
        activity = _activity(db, "Sweep floor")
        keeper = db.execute("SELECT id FROM mud_npc_definitions WHERE name='Keeper'").fetchone()[0]
        db.execute("UPDATE mud_work_activities SET room_id=NULL,provider_npc_definition_id=? WHERE id=?",
                   (keeper, activity["id"]))
        db.execute("INSERT INTO mud_skill_definitions(key,enabled) VALUES('lore',0) "
                   "ON CONFLICT(key) DO UPDATE SET enabled=0")
        before = db.execute("SELECT experience FROM mud_character_skills WHERE character_id=? AND skill_key='lore'",
                            (cid,)).fetchone()[0]
        mud._profession_action(db, user, {"arguments": ["join", "Porter"]})
        assert "10 coins" in _call(db, user, "do", "Sweep floor")[0].body
        assert db.execute("SELECT experience FROM mud_character_skills WHERE character_id=? AND skill_key='lore'",
                          (cid,)).fetchone()[0] == before
        db.execute("UPDATE mud_character_work SET available_at='2000-01-01T00:00:00+00:00' "
                   "WHERE character_id=? AND activity_id=?", (cid, activity["id"]))
        db.execute("DELETE FROM mud_npc_instances WHERE definition_id=?", (keeper,))
        with pytest.raises(mud.ActionError, match="NPC is not available"):
            _call(db, user, "do", "Sweep floor")


def test_builder_keeps_room_used_by_job_goal(game):
    app, uid = game
    _setup(app, uid)
    with app.app_context():
        db = get_db()
        woods = db.execute("SELECT id,name FROM mud_world_rooms WHERE name='Near Woods'").fetchone()
        room_id, name = woods["id"], woods["name"]
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = uid
        session["csrf_token"] = "test-token"
    response = client.post(f"/mud/builder/rooms/{room_id}/delete",
                           data={"csrf_token": "test-token", "confirmation": name},
                           follow_redirects=True)
    assert response.status_code == 200
    assert b"work activities using this room" in response.data
    with app.app_context():
        assert get_db().execute("SELECT 1 FROM mud_world_rooms WHERE id=?", (room_id,)).fetchone()
