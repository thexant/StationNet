"""Faction membership: world and faction switches, starting factions, joining, leaving, and expulsion."""
import json
import sqlite3
import zipfile
from io import BytesIO

import pytest

from stationnet import character_creation, mud, mud_compat, mud_conditions, mud_society, mud_triggers, mud_worlds
from stationnet.db import get_db

from mud_society_support import (WORLD, act, client, document, installed, place, room, running, set_society, user)


def _world(enabled=True, **settings):
    doc = document(society={"factions": {"enabled": enabled, **settings}})
    content = doc["content"]
    content["mud_world_factions"] = [
        {"id": "guild", "name": "Smiths Guild", "description": "Hammer and tongs.", "joinable": 1,
         "start_joinable": 1, "join_reputation": 100, "leave_reputation": -50},
        {"id": "rangers", "name": "Rangers", "joinable": 1, "start_joinable": 1,
         "recruiter_npc_definition_id": "baker", "join_cost": 30,
         "join_conditions_json": [{"type": "level", "min": 1}, {"type": "member", "faction": "guild", "negate": True}],
         "join_conditions_message": "Guild folk are not welcome.",
         "start_conditions_json": [{"type": "class", "keys": ["ranger"]}]},
        {"id": "veterans", "name": "Veterans", "joinable": 1, "join_conditions_json": [{"type": "level", "min": 9}]},
        {"id": "council", "name": "Council"},
        {"id": "order", "name": "Hidden Order", "public": 0},
        {"id": "rivals", "name": "Rival Band", "joinable": 1, "expel_below": -20},
    ]
    content["mud_faction_relations"] = [
        {"id": "feud", "faction_id": "guild", "other_faction_id": "rivals", "share_percent": -50}]
    return doc


def _new_user(db, name):
    return db.execute("INSERT INTO users(username,callsign,password_hash,connection_id) VALUES(?,?,?,?)",
                      (name, name.upper(), "unused", f"connection-{name}")).lastrowid


def _build(name, **extra):
    return {"name": name, "race": "human", "class": "warrior",
            "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
            "skills": ["lore", "guard"], **extra}


def _faction(db, local):
    return installed(db, "mud_world_factions", local)


def _member(db, uid, local):
    return mud_society.is_member(db, mud._character_id(db, uid), _faction(db, local))


def _standing(db, uid, local):
    return mud_society.reputation(db, mud._character_id(db, uid), mud_society.faction(db, _faction(db, local)))


def _coins(db, uid, amount):
    character = mud._character_id(db, uid)
    mud._wallet(db, character)
    db.execute("UPDATE mud_character_wallets SET coins=? WHERE character_id=?", (amount, character))


def test_settings_and_faction_validation():
    assert mud_society.validate_factions(None) == mud_society.FACTION_DEFAULTS
    assert mud_society.validate_factions({"enabled": True, "max_memberships": 0, "start_required": True})[
        "start_required"] is True
    for bad in ({"max_memberships": 99}, {"start_required": "yes"}, {"extra": 1}, "on"):
        with pytest.raises(mud_society.SocietyError):
            mud_society.validate_factions(bad)
    base = {"name": "Guild"}
    assert mud_society.validate_faction(base)["joinable"] == 0
    assert mud_society.validate_faction(base)["leavable"] == 1
    for bad in ({"joinable": 1, "public": 0}, {"start_joinable": 1, "public": 0}, {"joinable": 2},
                {"join_cost": -1}, {"rejoin_hours": 9000}, {"join_reputation": 5000}, {"expel_below": 5000},
                {"join_conditions_json": [{"type": "nonsense"}]}, {"join_conditions_message": "x" * 301},
                {"start_joinable": 1, "default_reputation": -700},
                {"start_joinable": 1, "expel_below": 50}):
        with pytest.raises(ValueError):
            mud_society.validate_faction({**base, **bad})
    # Start conditions cannot read what a new character does not have, even inside an any group.
    for conditions in ([{"type": "quest", "quest": "a"}], [{"any": [{"type": "coins", "min": 1}]}],
                       [{"type": "member", "faction": "x"}]):
        with pytest.raises(ValueError):
            mud_society.validate_faction({**base, "start_conditions_json": conditions})
    assert mud_society.validate_faction({**base, "start_conditions_json": [{"type": "race", "keys": ["elf"]}]})


def test_world_without_membership_is_unchanged():
    with running(_world(enabled=False)) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            assert "faction" in mud._hidden_actions(db, one)
            with pytest.raises(mud.ActionError):
                act(db, one, "/faction")
            assert character_creation.starting_factions(db) == ([], False)
            with pytest.raises(ValueError):
                character_creation.complete(db, _new_user(db, "three"), _build("Three", faction=_faction(db, "guild")))
            db.rollback()
            assert all(entry["member"] is False for entry in mud.reputation_state(db, one))
            payload = mud_worlds.export(db, WORLD)
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            assert "factions" not in manifest


def test_plain_factions_export_without_membership_fields():
    doc = document()
    doc["content"]["mud_world_factions"] = [{"id": "guild", "name": "Smiths Guild"}]
    with running(doc) as (app, _ids):
        with app.app_context():
            payload = mud_worlds.export(get_db(), WORLD)
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        row = json.loads(archive.read("content.json"))["mud_world_factions"][0]
    assert not set(row) & set(mud_worlds.FACTION_MEMBERSHIP_DEFAULTS)


def test_starting_faction_rules():
    with running(_world()) as (app, _ids):
        with app.app_context():
            db = get_db()
            set_society(db, factions={"start_required": True})
            offered, required = character_creation.starting_factions(db)
            assert required and [entry["name"] for entry in offered] == ["Rangers", "Smiths Guild"]
            three = _new_user(db, "three")
            with pytest.raises(ValueError, match="Choose a starting faction"):
                character_creation.complete(db, three, _build("Three"))
            db.rollback()
            three = _new_user(db, "three")
            with pytest.raises(ValueError, match="qualifies"):
                character_creation.complete(db, three, _build("Three", faction=_faction(db, "rangers")))
            db.rollback()
            three = _new_user(db, "three")
            with pytest.raises(ValueError, match="qualifies"):
                character_creation.complete(db, three, _build("Three", faction=_faction(db, "council")))
            db.rollback()
            three = _new_user(db, "three")
            character_creation.complete(db, three, _build("Three", faction=_faction(db, "guild")))
            assert _member(db, three, "guild")
            # Membership mechanics apply at once: the bonus, and its share to rivals.
            assert _standing(db, three, "guild") == 100 and _standing(db, three, "rivals") == -50
            # That share breaks nothing here, because Rival Band is not this character's faction.
            assert _member(db, three, "rivals") is False


def test_required_faction_does_not_trap_a_build_with_no_options():
    doc = _world(start_required=True)
    doc["content"]["mud_world_factions"][0]["start_conditions_json"] = [{"type": "race", "keys": ["elf"]}]
    with running(doc) as (app, _ids):
        with app.app_context():
            db = get_db()
            three = _new_user(db, "three")
            character_creation.complete(db, three, _build("Three"))
            assert mud_society.memberships(db, mud._character_id(db, three)) == []


def test_optional_starting_faction_and_attribute_gate():
    doc = _world()
    doc["content"]["mud_world_factions"][0]["start_conditions_json"] = [
        {"type": "attribute", "attribute": "strength", "min": 12}]
    with running(doc) as (app, _ids):
        with app.app_context():
            db = get_db()
            three = _new_user(db, "three")
            character_creation.complete(db, three, _build("Three"))
            assert not _member(db, three, "guild")
            four = _new_user(db, "four")
            weak = _build("Four", faction=_faction(db, "guild"),
                          attributes={"strength": 8, "agility": 12, "intellect": 12, "vitality": 12})
            with pytest.raises(ValueError, match="qualifies"):
                character_creation.complete(db, four, weak)
            db.rollback()
            four = _new_user(db, "four")
            strong = _build("Four", faction=_faction(db, "guild"),
                            attributes={"strength": 14, "agility": 10, "intellect": 10, "vitality": 10})
            character_creation.complete(db, four, strong)
            assert _member(db, four, "guild")


def test_creation_page_offers_the_faction_step():
    doc = _world()
    with running(doc) as (app, _ids):
        with app.app_context():
            db = get_db()
            three = _new_user(db, "three")
            db.commit()
        web = client(app, three)
        page = web.get(f"/mud/w/{WORLD}/create").data
        assert b"data-faction-card" in page and b"Smiths Guild" in page
        assert b"Veterans" not in page.split(b"mud-creator-steps")[-1]
    with running(_world(enabled=False)) as (app, _ids):
        with app.app_context():
            db = get_db()
            three = _new_user(db, "three")
            db.commit()
        assert b"data-faction-card" not in client(app, three).get(f"/mud/w/{WORLD}/create").data


def test_join_and_leave_during_play():
    with running(_world()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            listing = act(db, one, "/faction")
            assert "Smiths Guild (recruiting)" in listing and "Council" not in listing
            assert "Hidden Order" not in listing
            assert "Hammer and tongs." in act(db, one, '/faction info "Smiths Guild"')
            with pytest.raises(mud.ActionError, match="does not accept"):
                act(db, one, "/faction join Council")
            with pytest.raises(mud.ActionError, match="matches"):
                act(db, one, '/faction join "Hidden Order"')
            with pytest.raises(mud.ActionError, match="requirements"):
                act(db, one, "/faction join Veterans")
            assert "You join Smiths Guild." in act(db, one, '/faction join "Smiths Guild"')
            assert _member(db, one, "guild") and _standing(db, one, "guild") == 100
            with pytest.raises(mud.ActionError, match="already belong"):
                act(db, one, '/faction join "Smiths Guild"')
            # The default limit is one membership at a time.
            with pytest.raises(mud.ActionError, match="as many factions"):
                act(db, one, "/faction join Rangers")
            assert any(entry["member"] for entry in mud.reputation_state(db, one))
            assert "You leave Smiths Guild." in act(db, one, '/faction leave "Smiths Guild"')
            assert not _member(db, one, "guild") and _standing(db, one, "guild") == 50
            with pytest.raises(mud.ActionError, match="do not belong"):
                act(db, one, '/faction leave "Smiths Guild"')


def test_recruiter_conditions_message_and_fee():
    with running(_world(max_memberships=0)) as (app, (one, two)):
        with app.app_context():
            db = get_db()
            act(db, one, '/faction join "Smiths Guild"')
            with pytest.raises(mud.ActionError, match="Guild folk"):
                act(db, one, "/faction join Rangers")
            place(db, two, room(db, "forge"))
            with pytest.raises(mud.ActionError, match="not available"):
                act(db, two, "/faction join Rangers")
            place(db, two, room(db, "square"))
            _coins(db, two, 10)
            with pytest.raises(mud.ActionError, match="costs 30"):
                act(db, two, "/faction join Rangers")
            assert not _member(db, two, "rangers")
            _coins(db, two, 40)
            act(db, two, "/faction join Rangers")
            assert _member(db, two, "rangers") and mud._wallet(db, mud._character_id(db, two)) == 10


def test_rejoin_wait_and_members_who_cannot_leave():
    doc = _world()
    doc["content"]["mud_world_factions"][0]["rejoin_hours"] = 24
    doc["content"]["mud_world_factions"][2].update(leavable=0, join_conditions_json=[])
    with running(doc) as (app, (one, two)):
        with app.app_context():
            db = get_db()
            act(db, one, '/faction join "Smiths Guild"')
            act(db, one, '/faction leave "Smiths Guild"')
            with pytest.raises(mud.ActionError, match="not take you back"):
                act(db, one, '/faction join "Smiths Guild"')
            db.execute("UPDATE mud_character_factions SET left_at=datetime('now','-25 hours')")
            act(db, one, '/faction join "Smiths Guild"')
            assert _member(db, one, "guild")
            act(db, two, "/faction join Veterans")
            with pytest.raises(mud.ActionError, match="does not let"):
                act(db, two, "/faction leave Veterans")


def test_wanted_players_cannot_join():
    doc = _world(max_memberships=0)
    doc["society"]["crime"] = {"enabled": True}
    with running(doc) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            mud_society.record_crime(db, mud._character_id(db, one), WORLD, _faction(db, "guild"), "other", 50, 0)
            with pytest.raises(mud.ActionError, match="owe them fines"):
                act(db, one, '/faction join "Smiths Guild"')


def test_hostile_standing_blocks_joining_and_ends_membership():
    doc = _world(max_memberships=0)
    with running(doc) as (app, (one, two)):
        with app.app_context():
            db = get_db()
            mud._change_reputation(db, user(db, two), _faction(db, "rivals"), -700)
            with pytest.raises(mud.ActionError, match="too low"):
                act(db, two, '/faction join "Rival Band"')
            act(db, one, '/faction join "Smiths Guild"')
            drafts = mud._change_reputation(db, user(db, one), _faction(db, "guild"), -800)
            assert not _member(db, one, "guild")
            assert any("casts you out" in draft.body for draft in drafts)
            # The leave penalty applies to expulsion too.
            assert _standing(db, one, "guild") == 100 - 800 - 50


def test_joining_a_faction_can_expel_from_its_rival():
    with running(_world(max_memberships=0)) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            act(db, one, '/faction join "Rival Band"')
            # The guild's join bonus passes -50 to the band, below its expel_below of -20.
            body = act(db, one, '/faction join "Smiths Guild"')
            assert "Rival Band casts you out." in body
            assert _member(db, one, "guild") and not _member(db, one, "rivals")


def test_quest_reward_share_can_expel():
    doc = _world(max_memberships=0)
    content = doc["content"]
    content["mud_quests"] = [{"id": "job", "name": "Job", "description": "Work."}]
    content["mud_quest_objectives"] = [{"id": "go", "quest_id": "job", "objective_type": "visit_room",
                                        "target_id": "forge", "description": "Visit the forge"}]
    content["mud_quest_rewards"] = [{"id": "pay", "quest_id": "job", "reward_type": "reputation",
                                     "target_id": "guild", "amount": 100}]
    with running(doc) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            act(db, one, '/faction join "Rival Band"')
            character = mud._character_id(db, one)
            quest = installed(db, "mud_quests", "job")
            db.execute("INSERT INTO mud_player_quests(character_id,quest_id,status) VALUES(?,?,'active')",
                       (character, quest))
            db.execute("INSERT INTO mud_player_objectives(character_id,objective_id,progress,completed_at) "
                       "VALUES(?,?,1,CURRENT_TIMESTAMP)", (character, installed(db, "mud_quest_objectives", "go")))
            drafts = mud._finish_ready_quests(db, user(db, one))
            assert any("casts you out" in draft.body for draft in drafts)
            assert not _member(db, one, "rivals")


def test_member_condition_follows_the_world_switch():
    with running(_world()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            guild = _faction(db, "guild")
            needs = [{"type": "member", "faction": guild}]
            refuses = [{"type": "member", "faction": guild, "negate": True}]
            context = lambda: mud._ConditionContext(db, one)
            assert not mud_conditions.evaluate(needs, context())
            assert mud_conditions.evaluate(refuses, context())
            act(db, one, '/faction join "Smiths Guild"')
            assert mud_conditions.evaluate(needs, context())
            set_society(db, factions={"enabled": False})
            assert not mud_conditions.evaluate(needs, context())
            assert db.execute("SELECT COUNT(*) FROM mud_character_factions WHERE left_at IS NULL").fetchone()[0] == 1
            set_society(db, factions={"enabled": True})
            assert mud_conditions.evaluate(needs, context())


def test_member_condition_is_refused_in_dungeon_templates():
    with pytest.raises(ValueError):
        mud_conditions.validate_conditions([{"type": "member", "faction": "x"}], allow_quests=False)
    with pytest.raises(ValueError):
        mud_conditions.validate_outcomes([{"type": "join_faction", "faction": "x"}], allow_world=False)


def test_scripted_joins_and_triggers():
    doc = _world()
    doc["events"] = [
        {"id": "oath", "event": "say_phrase", "source": {"room": "square"}, "phrase": "i swear",
         "actions": [{"type": "join_faction", "faction": "order"}]},
        {"id": "oath-off", "event": "say_phrase", "source": {"room": "square"}, "phrase": "i recant",
         "actions": [{"type": "leave_faction", "faction": "order"}]},
        {"id": "welcome", "event": "faction_join", "source": {"faction": "order"},
         "actions": [{"type": "message", "text": "Candles flicker."}]},
        {"id": "farewell", "event": "faction_leave", "source": {"faction": "order"},
         "actions": [{"type": "message", "text": "The candles go out."}]}]
    with running(doc) as (app, (one, two)):
        with app.app_context():
            db = get_db()
            order = _faction(db, "order")
            bodies = [draft.body for draft in mud.fire_triggers(db, user(db, one), "say_phrase", [room(db, "square")],
                                                                 phrase="I swear")]
            assert _member(db, one, "order") and "Candles flicker." in bodies
            # Hidden factions show only to their members.
            assert "Hidden Order" in act(db, one, "/faction")
            assert "Hidden Order" not in act(db, two, "/faction")
            act(db, two, '/faction join "Smiths Guild"')
            # A refused scripted join tells the player and changes nothing.
            bodies = [draft.body for draft in mud.fire_triggers(db, user(db, two), "say_phrase", [room(db, "square")],
                                                                 phrase="I swear")]
            assert not _member(db, two, "order") and any("as many factions" in body for body in bodies)
            bodies = [draft.body for draft in mud.fire_triggers(db, user(db, one), "say_phrase", [room(db, "square")],
                                                                 phrase="I recant")]
            assert not _member(db, one, "order") and "The candles go out." in bodies
            # A dialogue outcome is refused outright instead.
            with pytest.raises(mud.ActionError, match="as many factions"):
                mud._settle_outcomes(db, user(db, two), [{"type": "join_faction", "faction": order}])
            set_society(db, factions={"enabled": False})
            assert mud._scripted_membership(db, user(db, one), "join_faction", order) == []


def test_package_round_trip_keeps_rules_and_recruiter():
    with running(_world()) as (app, _ids):
        with app.app_context():
            db = get_db()
            set_society(db, factions={"start_required": True})
            payload = mud_worlds.export(db, WORLD)
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            manifest = json.loads(files["manifest.json"])
            assert manifest["factions"]["start_required"] is True
            manifest["id"] = "lifeworld-copy"
            files["manifest.json"] = json.dumps(manifest).encode()
            stream = BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            db.commit()
            mud_worlds.install(db, stream.getvalue())
            row = db.execute("SELECT * FROM mud_world_factions WHERE world_id='lifeworld-copy' AND name='Rangers'"
                             ).fetchone()
            assert row["join_cost"] == 30 and row["joinable"] == 1
            recruiter = db.execute("SELECT name,world_id FROM mud_npc_definitions WHERE id=?",
                                   (row["recruiter_npc_definition_id"],)).fetchone()
            assert recruiter["name"] == "Baker" and recruiter["world_id"] == "lifeworld-copy"
            conditions = json.loads(row["join_conditions_json"])
            guild = db.execute("SELECT id FROM mud_world_factions WHERE world_id='lifeworld-copy' AND name='Smiths Guild'"
                               ).fetchone()["id"]
            assert conditions[1]["faction"] == guild


@pytest.mark.parametrize("change", [
    lambda row: row.update(recruiter_npc_definition_id="nobody"),
    lambda row: row.update(start_conditions_json=[{"type": "quest", "quest": "x"}]),
    lambda row: row.update(joinable=1, public=0),
])
def test_bad_packages_are_rejected(change):
    doc = _world()
    change(doc["content"]["mud_world_factions"][1])
    with pytest.raises(mud_worlds.WorldError):
        mud_worlds.read_package(mud_worlds.package_bytes(doc))


def test_upgrade_carries_membership():
    with running(_world()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            act(db, one, '/faction join "Smiths Guild"')
            db.commit()
            changed = _world()
            changed["content"]["mud_world_factions"][0]["description"] = "New words."
            payload = mud_worlds.package_bytes(changed)
            mud_worlds.upgrade(db, payload, mud_worlds.upgrade_preview(db, payload)["expected_hash"])
            db.commit()
            row = db.execute("SELECT f.description FROM mud_character_factions m JOIN mud_world_factions f "
                             "ON f.id=m.faction_id WHERE m.left_at IS NULL").fetchone()
            assert row["description"] == "New words."


def test_removing_a_faction_ends_its_memberships():
    with running(_world()) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            act(db, one, '/faction join "Smiths Guild"')
            db.execute("DELETE FROM mud_world_factions WHERE id=?", (_faction(db, "guild"),))
            assert db.execute("SELECT COUNT(*) FROM mud_character_factions").fetchone()[0] == 0


def test_archives_carry_memberships():
    assert "mud_character_factions" in mud_compat.RESTORED_OPTIONAL_TABLES


def test_migration_is_repeatable():
    with running() as (app, _ids):
        with app.app_context():
            from stationnet.db import _migrate_mud_faction_membership
            db = get_db()
            _migrate_mud_faction_membership(db)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(mud_world_factions)")}
            assert {"joinable", "start_joinable", "expel_below"} <= columns


def test_builder_saves_rules_and_settings():
    with running(_world()) as (app, (one, two)):
        with app.app_context():
            db = get_db()
            guild, baker = _faction(db, "guild"), installed(db, "mud_npc_definitions", "baker")
        web = client(app, one)
        url = f"/mud/w/{WORLD}/builder/factions/{guild}"
        fields = {"csrf_token": "test-token", "name": "Smiths Guild", "public": "1", "membership_fields": "1",
                  "joinable": "1", "leavable": "1", "recruiter_npc_definition_id": baker, "join_cost": "15",
                  "expel_below": "", "join_conditions_json": '[{"type": "level", "min": 3}]'}
        assert web.post(url, data=fields).status_code == 302
        with app.app_context():
            row = mud_society.faction(get_db(), guild)
            assert row["join_cost"] == 15 and row["expel_below"] is None
            assert row["recruiter_npc_definition_id"] == baker and row["start_joinable"] == 0
        web.post(url, data={**fields, "expel_below": "-100"})
        # A form without the joining fields leaves them alone.
        web.post(url, data={"csrf_token": "test-token", "name": "Renamed", "public": "1"})
        with app.app_context():
            row = mud_society.faction(get_db(), guild)
            assert row["name"] == "Renamed" and row["expel_below"] == -100 and row["join_cost"] == 15
        for bad, message in (({"recruiter_npc_definition_id": "nobody"}, b"recruiter"),
                             ({"expel_below": "low"}, b"whole number"),
                             ({"start_conditions_json": '[{"type": "coins", "min": 1}]'}, b"Unsupported"),
                             ({"public": ""}, b"must be public")):
            response = web.post(url, data={**fields, **bad}, follow_redirects=True)
            assert message in response.data
        assert web.post(f"/mud/w/{WORLD}/builder/society/factions",
                        data={"csrf_token": "test-token", "enabled": "1", "max_memberships": "3",
                              "start_required": "1"}).status_code == 302
        with app.app_context():
            settings = mud_society.stored_settings(get_db().execute(
                "SELECT society_json FROM mud_world_packages WHERE id=?", (WORLD,)).fetchone()["society_json"])
            assert settings["factions"] == {"enabled": True, "max_memberships": 3, "start_required": True}
        assert client(app, two).post(url, data=fields).status_code == 403
