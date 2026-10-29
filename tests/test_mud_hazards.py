"""Protective gear, cures, lingering room hazards, and effects that work outside combat."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import uuid

import pytest

from mud_world_helpers import activate_world, url
from stationnet import create_app, character_creation, mud, mud_effects, mud_triggers, mud_worlds
from stationnet.db import get_db


def _document(**changes):
    document = {
        "id": "hazworld", "name": "Hazard World", "author": "Test", "start_room": "platform",
        "format_version": 2,
        "effects": [{"key": "radiation", "name": "Radiation", "kind": "damage", "resist_as": "poison",
                     "outside_combat": True}],
        "rooms": [{"id": room, "name": room.title(), "description": f"The {room}."}
                  for room in ("platform", "tunnel", "shower")],
        "exits": [{"from": "platform", "to": "tunnel", "name": "tunnel", "direction": "north"},
                  {"from": "tunnel", "to": "platform", "name": "platform", "direction": "south"},
                  {"from": "platform", "to": "shower", "name": "shower", "direction": "east"},
                  {"from": "shower", "to": "platform", "name": "platform", "direction": "west"}],
        "content": {"mud_item_definitions": [
            {"id": "mask", "name": "Gas Mask", "description": "A rubber mask.", "item_type": "equipment",
             "equipment_slot": "head", "properties_json": {"resistances": {"radiation": 60}}},
            {"id": "suit", "name": "Hazmat Suit", "description": "A heavy suit.", "item_type": "equipment",
             "equipment_slot": "body", "properties_json": {"category": "hazmat", "resistances": {"poison": 40}}},
            {"id": "pills", "name": "Anti-Rad Pills", "description": "Iodine.", "item_type": "consumable",
             "properties_json": {"cures": [{"effect": "radiation", "potency": 3, "turns": 2}]}},
            {"id": "antidote", "name": "Antidote", "description": "Cures toxins.", "item_type": "consumable",
             "properties_json": {"cures": [{"category": "poison"}]}}]},
        "events": [
            {"id": "fallout", "event": "linger", "source": {"room": "tunnel"}, "interval_seconds": 60,
             "actions": [{"type": "apply_effect", "effect": "radiation", "potency": 10, "duration": 5}]},
            {"id": "decon", "event": "enter_room", "source": {"room": "shower"},
             "actions": [{"type": "cure_effect", "effect": "radiation"}]}],
    }
    document.update(changes)
    return document


@pytest.fixture
def game():
    data = Path(__file__).parent / f"_hazards_{uuid.uuid4().hex}"
    data.mkdir()
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,mud_enabled,mud_traditional_enabled) "
                   "VALUES('Test','Test','TST',1,1)")
        users = []
        for name in ("one", "two"):
            uid = db.execute("INSERT INTO users(username,callsign,password_hash,connection_id,is_admin) "
                             "VALUES(?,?,?,?,?)", (name, name.upper(), "unused", f"connection-{name}",
                                                   int(name == "one"))).lastrowid
            character_creation.complete(db, uid, {
                "name": name.title(), "race": "human", "class": "warrior",
                "attributes": {"strength": 11, "agility": 11, "intellect": 11, "vitality": 11},
                "skills": ["lore", "guard"]})
            users.append(uid)
        db.commit()
        mud_worlds.install(db, mud_worlds.package_bytes(_document()))
        activate_world(db, "hazworld")
        for uid in users:
            _move(db, uid, "platform")
        db.commit()
    yield app, users
    app.extensions["broadcast_coordinator"]._closed = True
    app.extensions["broadcast_coordinator"]._timer.cancel()
    shutil.rmtree(data)


def _local(db, table, local):
    kind = "room" if table == "mud_world_rooms" else table
    return db.execute(f"SELECT id FROM {table} WHERE id LIKE ?", (f"world:hazworld:%:{kind}:{local}",)).fetchone()["id"]


def _move(db, uid, room):
    room_id = _local(db, "mud_world_rooms", room)
    mud._set_player_location(db, uid, "traditional", "world_room", room_id)
    db.execute("DELETE FROM mud_presence WHERE user_id=?", (uid,))
    db.execute("INSERT INTO mud_presence(world_key,user_id,connection_id,client_id,room_id,location_type,location_key,"
               "last_seen) VALUES(mud_world(),?,?,?,?,?,?,?)",
               (uid, f"connection-{uid}", f"client-{uid}", room_id, "world_room", room_id, mud._scheduled_now()))


def _user(db, uid):
    return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def _give(db, uid, item):
    instance = f"test:{item}:{uuid.uuid4().hex}"
    db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id) VALUES(?,?,?)",
               (instance, _local(db, "mud_item_definitions", item), mud._character_row(db, uid)["id"]))
    return instance


def _radiation(db):
    return next(key for key, entry in mud_effects.catalog(db).items() if entry["name"] == "Radiation")


def _effect(db, uid, key):
    row = db.execute("SELECT potency,remaining_turns FROM mud_active_effects WHERE target_type='character' "
                     "AND target_id=? AND effect_key=?", (mud._character_row(db, uid)["id"], key)).fetchone()
    return (row["potency"], row["remaining_turns"]) if row else None


def _pulse(db, seconds=90):
    mud.sync_trigger_schedule(db)
    mud.sync_effect_pulse(db)
    mud.process_scheduled_events(db, datetime.now(timezone.utc) + timedelta(seconds=seconds))


def _expose(db, uid, potency=10):
    trigger = {"id": "exposure", "name": "Exposure",
               "actions": [{"type": "apply_effect", "effect": _radiation(db), "potency": potency, "duration": 5}]}
    return mud._trigger_action(db, _user(db, uid), trigger, 0, "hazworld")


# Equipment resistances ---------------------------------------------------------

def test_equipped_gear_resists_by_effect_key_and_category(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        radiation = _radiation(db)
        _give(db, one, "mask")
        mud._equip_action(db, _user(db, one), {"arguments": ["Gas Mask"]})
        _expose(db, one)
        assert _effect(db, one, radiation) == (4, 2)
        # The mask names radiation alone, so other poisons get through untouched.
        mud.mud_effects.apply(db, "character", mud._character_row(db, one)["id"],
                              mud._resist_effect(db, mud._character_row(db, one)["id"],
                                                 {"key": "poison", "potency": 10, "duration": 5}))
        assert _effect(db, one, "poison") == (10, 5)
        assert "Radiation 60%" in mud._character_action(db, _user(db, one), {})[0].body


def test_untrained_gear_stacks_to_immunity_or_near_it(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        radiation = _radiation(db)
        for item, name in (("mask", "Gas Mask"), ("suit", "Hazmat Suit")):
            _give(db, one, item)
            mud._equip_action(db, _user(db, one), {"arguments": [name]})
        # 60 from the mask plus three quarters of the untrained suit's 40.
        assert mud._character_resistances(db, mud._character_row(db, one)["id"])["poison"] == 30
        assert mud_effects.resistance_for(db, radiation, mud._character_resistances(
            db, mud._character_row(db, one)["id"])) == 90
        _expose(db, one)
        assert _effect(db, one, radiation) == (1, 1)
        mud._unequip_action(db, _user(db, one), {"arguments": ["Gas Mask"]})
        mud._unequip_action(db, _user(db, one), {"arguments": ["Hazmat Suit"]})
        _expose(db, one, potency=20)
        assert _effect(db, one, radiation) == (20, 5)


def test_full_resistance_shrugs_the_effect_off(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        db.execute("UPDATE mud_item_definitions SET properties_json=? WHERE id=?",
                   ('{"resistances":{"poison":100}}', _local(db, "mud_item_definitions", "suit")))
        _give(db, one, "suit")
        mud._equip_action(db, _user(db, one), {"arguments": ["Hazmat Suit"]})
        assert "shrug off" in _expose(db, one)[0].body
        assert _effect(db, one, _radiation(db)) is None


# Cures -----------------------------------------------------------------------

def test_consumable_cures_weaken_shorten_and_remove(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        radiation = _radiation(db)
        _expose(db, one)
        pills = _give(db, one, "pills")
        body = mud._use_action(db, _user(db, one), {"arguments": ["Anti-Rad Pills"]})[0].body
        assert "easing Radiation" in body and _effect(db, one, radiation) == (7, 3)
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (pills,)).fetchone() is None
        mud.mud_effects.apply(db, "character", mud._character_row(db, one)["id"],
                              {"key": "poison", "potency": 2, "duration": 3})
        _give(db, one, "antidote")
        body = mud._use_action(db, _user(db, one), {"arguments": ["Antidote"]})[0].body
        # A category cure removes every effect that resists as that category.
        assert "curing" in body and _effect(db, one, radiation) is None and _effect(db, one, "poison") is None


def test_cure_with_nothing_to_treat_is_not_used_up(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        pills = _give(db, one, "pills")
        with pytest.raises(mud.ActionError, match="nothing that Anti-Rad Pills would treat"):
            mud._use_action(db, _user(db, one), {"arguments": ["Anti-Rad Pills"]})
        assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (pills,)).fetchone()
        assert "Treats: Radiation" in mud._examine_item(db, _user(db, one), mud._current_room(db, one),
                                                        "Anti-Rad Pills")


def test_cure_effect_trigger_decontaminates_on_entry(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        _expose(db, one)
        drafts = mud._go_action(db, _user(db, one), {"arguments": ["east"]})
        assert any("Radiation fades" in draft.body for draft in drafts)
        assert _effect(db, one, _radiation(db)) is None


# Lingering hazards and the outside-combat pulse -------------------------------

def test_linger_trigger_reaches_each_player_still_in_the_room(game):
    app, (one, two) = game
    with app.app_context():
        db = get_db()
        radiation = _radiation(db)
        _move(db, one, "tunnel")
        _give(db, two, "mask")
        mud._equip_action(db, _user(db, two), {"arguments": ["Gas Mask"]})
        _move(db, two, "tunnel")
        _pulse(db)
        assert _effect(db, one, radiation)[0] == 10
        assert _effect(db, two, radiation)[0] == 4
        _move(db, two, "platform")
        mud_effects.clear(db, "character", mud._character_row(db, two)["id"])
        _pulse(db, 200)
        assert _effect(db, two, radiation) is None and _effect(db, one, radiation) is not None


def test_outside_combat_effects_tick_but_never_kill(game):
    app, (one, _two) = game
    with app.app_context():
        db = get_db()
        character = mud._character_row(db, one)
        _expose(db, one, potency=1000)
        mud_effects.apply(db, "character", character["id"], {"key": "poison", "potency": 3, "duration": 3})
        mud.sync_effect_pulse(db)
        mud.process_scheduled_events(db, datetime.now(timezone.utc) + timedelta(seconds=40))
        assert mud._character_row(db, one)["current_health"] == 1
        assert _effect(db, one, _radiation(db)) == (1000, 4)
        # Built-in poison is not marked outside_combat, so it waits for combat as before.
        assert _effect(db, one, "poison") == (3, 3)
        assert any("damage from Radiation" in row["body"] for row in mud._events_for_user(db, one))


def test_no_pulse_is_scheduled_without_outside_combat_effects(game):
    app, _users = game
    with app.app_context():
        db = get_db()
        db.execute("UPDATE mud_effect_definitions SET definition_json=replace(definition_json,"
                   "',\"outside_combat\":true','')")
        mud.sync_effect_pulse(db)
        assert db.execute("SELECT 1 FROM mud_scheduled_events WHERE event_kind='effect_pulse'").fetchone() is None


# World Builder -----------------------------------------------------------------

def test_builder_saves_gear_resistances_cures_and_effect_triggers(game):
    app, (one, _two) = game
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = one
        session["csrf_token"] = "test-token"
    with app.app_context():
        db = get_db()
        radiation, tunnel = _radiation(db), _local(db, "mud_world_rooms", "tunnel")
    item = {"csrf_token": "test-token", "name": "Filter Mask", "description": "A mask.", "item_type": "equipment",
            "equipment_slot": "head", "resistances": json.dumps({radiation: 80}), "cures": "[]"}
    assert b"Item definition Filter Mask created." in client.post(url("/builder/items"), data=item,
                                                                    follow_redirects=True).data
    bad = client.post(url("/builder/items"), data={**item, "name": "Bad Mask", "resistances": '{"nope": 5}'},
                      follow_redirects=True)
    assert b"Invalid cures or resistances" in bad.data
    form = {"csrf_token": "test-token", "name": "Leak", "event": "linger", "source": f"room|{tunnel}",
            "scope": "character", "enabled": "1", "interval_seconds": "45",
            "actions": json.dumps([{"type": "apply_effect", "effect": radiation, "potency": 2},
                                   {"type": "cure_effect", "category": "burning"}])}
    assert b"Trigger Leak created." in client.post(url("/builder/triggers"), data=form, follow_redirects=True).data
    with app.app_context():
        db = get_db()
        properties = json.loads(db.execute("SELECT properties_json FROM mud_item_definitions WHERE name='Filter Mask'")
                                .fetchone()[0])
        assert properties["resistances"] == {radiation: 80}
        trigger = json.loads(db.execute("SELECT definition_json FROM mud_world_triggers WHERE local_id='leak'")
                             .fetchone()[0])
        assert trigger["interval_seconds"] == 45


def test_export_bundles_effects_named_only_by_resistances_and_cures(game):
    app, _users = game
    with app.app_context():
        db = get_db()
        # With the triggers gone, only the gear's resistances and the pills' cure name radiation.
        db.execute("DELETE FROM mud_world_triggers WHERE world_id='hazworld'")
        document, _report = mud_worlds.export_document(db, "hazworld")
        assert [effect["key"] for effect in document["effects"]] == ["radiation"]
        assert document["effects"][0]["outside_combat"] is True
        items = {row["id"]: row["properties_json"] for row in document["content"]["mud_item_definitions"]}
        assert items["mask"]["resistances"] == {"radiation": 60}
        assert items["pills"]["cures"][0]["effect"] == "radiation"


# Validation --------------------------------------------------------------------

@pytest.mark.parametrize("trigger,message", [
    ({"event": "linger", "source": {"room": "r"}, "actions": [{"type": "damage", "amount": 1}]},
     "interval_seconds must be 30"),
    ({"event": "timer", "source": {"room": "r"}, "interval_seconds": 60,
      "actions": [{"type": "cure_effect", "effect": "poison"}]}, "timers have no player"),
    ({"event": "enter_room", "source": {"room": "r"},
      "actions": [{"type": "cure_effect", "effect": "poison", "category": "poison"}]}, "exactly one of effect"),
    ({"event": "enter_room", "source": {"room": "r"},
      "actions": [{"type": "cure_effect", "category": "radiation"}]}, "category must be one of"),
])
def test_invalid_hazard_triggers_are_rejected(trigger, message):
    with pytest.raises(ValueError, match=message):
        mud_triggers.validate_trigger({"id": "t", **trigger})


def test_linger_and_cure_triggers_validate():
    trigger = mud_triggers.validate_trigger({
        "id": "t", "event": "linger", "source": {"room": "r"}, "interval_seconds": 30,
        "conditions": [{"type": "level", "min": 2}],
        "actions": [{"type": "cure_effect", "category": "poison", "turns": 2}]})
    assert trigger["scope"] == "character" and trigger["actions"][0] == {
        "type": "cure_effect", "category": "poison", "turns": 2}


@pytest.mark.parametrize("properties,path", [
    ({"resistances": {"radioactive": 50}}, "resistances"),
    ({"resistances": {"poison": 150}}, "resistances"),
    ({"cures": [{"effect": "nothing"}]}, "cures"),
    ({"cures": [{"category": "poison", "turns": 0}]}, "cures"),
])
def test_world_packages_reject_bad_gear_and_cures(properties, path):
    app = create_app({"TESTING": True, "DATA_DIR": str(Path(__file__).parent / f"_hazards_{uuid.uuid4().hex}")})
    try:
        with app.app_context():
            document = _document()
            document["content"]["mud_item_definitions"][0]["properties_json"] = properties
            with pytest.raises(mud_worlds.WorldError) as raised:
                mud_worlds.read_package(mud_worlds.package_bytes(document), get_db())
            assert any(problem["path"].endswith(path) for problem in raised.value.report.errors)
    finally:
        app.extensions["broadcast_coordinator"]._closed = True
        app.extensions["broadcast_coordinator"]._timer.cancel()
        shutil.rmtree(app.config["DATA_DIR"], ignore_errors=True)


def test_effect_definition_accepts_only_boolean_outside_combat():
    assert mud_effects.normalize_effect({"key": "rot", "name": "Rot", "kind": "damage", "outside_combat": True})[
        "outside_combat"] is True
    assert "outside_combat" not in mud_effects.normalize_effect(
        {"key": "rot", "name": "Rot", "kind": "damage", "outside_combat": False})
    with pytest.raises(ValueError, match="outside_combat"):
        mud_effects.normalize_effect({"key": "rot", "name": "Rot", "kind": "damage", "outside_combat": 1})
