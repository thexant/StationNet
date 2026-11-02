"""Start rules: conditional starting rooms, their priority, authoring, packaging, and fallbacks."""
import json

import pytest

from stationnet import character_creation, mud, mud_compat, mud_conditions, mud_start_rules, mud_worlds
from stationnet.db import get_db

from mud_society_support import WORLD, client, document, installed, room, running


def _rule(rule_id, room_id, conditions, order=0):
    return {"id": rule_id, "room_id": room_id, "spawn_conditions_json": conditions, "sort_order": order}


def _race(*keys):
    return {"type": "race", "keys": list(keys)}


def _class(*keys):
    return {"type": "class", "keys": list(keys)}


def _world(*rules):
    doc = document(society={"factions": {"enabled": True}})
    doc["content"]["mud_world_factions"] = [
        {"id": "guild", "name": "Smiths Guild", "joinable": 1, "start_joinable": 1},
        {"id": "rangers", "name": "Rangers", "joinable": 1, "start_joinable": 1},
        {"id": "council", "name": "Council"}]
    if rules:
        doc["content"]["mud_world_start_rules"] = list(rules)
    return doc


def _user(db, name):
    return db.execute("INSERT INTO users(username,callsign,password_hash,connection_id) VALUES(?,?,?,?)",
                      (name, name.upper(), "unused", f"connection-{name}")).lastrowid


def _build(db, name, race="human", archetype="warrior", faction=None):
    config = character_creation.options(db)
    race_row = next(entry for entry in config["races"] if entry["key"] == race)
    class_row = next(entry for entry in config["classes"] if entry["key"] == archetype)
    allocated = {key: 11 for key in character_creation.ATTRIBUTES}
    final = {key: allocated[key] + race_row.get("attributes", {}).get(key, 0)
             + class_row.get("attributes", {}).get(key, 0) for key in allocated}
    eligible = character_creation.eligible_skills(config, race_row, class_row, final)
    wanted = min(character_creation.starting_skill_count(config, race_row, class_row), len(eligible))
    payload = {"name": name.title(), "race": race, "class": archetype, "attributes": allocated,
               "skills": [entry["key"] for entry in eligible[:wanted]]}
    return {**payload, "faction": faction} if faction else payload


def _create(db, name, race="human", archetype="warrior", faction=None):
    """Complete a new account's character; returns its user ID."""
    uid = _user(db, name)
    character_creation.complete(db, uid, _build(db, name, race, archetype, faction))
    return uid


def _located(db, uid):
    row = db.execute("SELECT location_key FROM mud_player_locations WHERE user_id=? AND world_key=?",
                     (uid, WORLD)).fetchone()
    return row["location_key"] if row else None


def _stored_start(db, uid):
    return db.execute("SELECT start_room_id FROM mud_characters WHERE user_id=? AND world_key=?",
                      (uid, WORLD)).fetchone()[0]


def test_condition_validation():
    good = [_race("elf"), _class("scout"), {"any": [{"type": "member", "faction": "a"},
                                                     {"type": "member", "faction": "b"}]}]
    assert len(mud_conditions.validate_start_rule_conditions(good)) == 3
    for bad in ([], None, "", "not json", [{"type": "level", "min": 1}], [{"type": "tag", "keys": ["x"]}],
                [{**_race("elf"), "negate": True}], [_race("elf"), _race("dwarf")],
                [_class("a"), _class("b"), _race("c"), _race("d")],
                [{"any": [_race("elf"), _class("scout")]}],
                [{"any": [{"type": "member", "faction": "a", "negate": True}]}],
                [{"any": [{"type": "member", "faction": "a"}], "negate": True}],
                [{"type": "member", "faction": "a"}, {"any": [{"type": "member", "faction": "b"}]}]):
        with pytest.raises(ValueError):
            mud_conditions.validate_start_rule_conditions(bad)


def test_conditions_from_form_round_trip():
    conditions = mud_start_rules.conditions_from(["elf", "dwarf"], [], ["a", "b"])
    assert mud_start_rules.choices(mud_conditions.validate_start_rule_conditions(conditions)) == {
        "race": {"elf", "dwarf"}, "member": {"a", "b"}}
    assert mud_start_rules.conditions_from([], [], ["a"]) == [{"type": "member", "faction": "a"}]
    assert mud_start_rules.conditions_from([], [], []) == []
    names = {"race": {"elf": "Elf"}, "class": {}, "member": {"a": "Guild"}}
    assert mud_start_rules.summary(mud_start_rules.conditions_from(["elf"], ["mage"], ["a"]), names) == (
        "Race: Elf AND Class: mage AND Faction: Guild")


def test_world_without_rules_starts_at_the_default_lazily():
    with running(_world()) as (app, _users):
        with app.app_context():
            db = get_db()
            uid = _create(db, "plain")
            assert _located(db, uid) is None and _stored_start(db, uid) is None
            assert mud._current_room(db, uid)["id"] == room(db, "square")


@pytest.mark.parametrize("race,archetype,faction,expected", [
    ("human", "warrior", None, "square"),
    ("elf", "warrior", None, "home"),
    ("human", "scout", None, "forge"),
    ("human", "warrior", "guild", "tavern"),
    ("elf", "scout", None, "forge"),  # Two single rules tie on order 0: Forge sorts before Smith's Home.
])
def test_single_conditions(race, archetype, faction, expected):
    doc = _world(_rule("elves", "home", [_race("elf")]), _rule("rangers", "forge", [_class("scout")]),
                 _rule("guild", "tavern", [{"type": "member", "faction": "guild"}]))
    with running(doc) as (app, _users):
        with app.app_context():
            db = get_db()
            chosen = installed(db, "mud_world_factions", faction) if faction else None
            uid = _create(db, "newcomer", race, archetype, chosen)
            assert mud._current_room(db, uid)["id"] == room(db, expected)


def test_combination_beats_general_rules_and_keys_are_alternatives():
    doc = _world(_rule("elves", "home", [_race("elf")], order=-50),
                 _rule("forest", "forge", [_race("elf", "dwarf"), _class("scout")], order=50),
                 _rule("exact", "tavern", [_race("elf"), _class("scout"),
                                           {"any": [{"type": "member", "faction": "guild"},
                                                    {"type": "member", "faction": "rangers"}]}], order=99))
    with running(doc) as (app, _users):
        with app.app_context():
            db = get_db()
            guild = installed(db, "mud_world_factions", "rangers")
            assert _located(db, _create(db, "aa", "elf", "scout", guild)) == room(db, "tavern")
            assert _located(db, _create(db, "bb", "elf", "scout")) == room(db, "forge")
            assert _located(db, _create(db, "cc", "dwarf", "scout")) == room(db, "forge")
            assert _located(db, _create(db, "dd", "elf", "warrior")) == room(db, "home")
            assert _located(db, _create(db, "ee", "dwarf", "warrior")) is None


def test_order_settles_equally_specific_rules_and_review_warns_only_on_ties():
    tied = _world(_rule("elves", "tavern", [_race("elf")]), _rule("rangers", "home", [_class("scout")]))
    with running(tied) as (app, _users):
        with app.app_context():
            db = get_db()
            # Smith's Home sorts before Tavern, whichever was written first.
            assert _located(db, _create(db, "aa", "elf", "scout")) == room(db, "home")
            names = {"race": {"elf": "Elf"}, "class": {"scout": "Scout"}, "member": {}}
            rules = mud_start_rules.review(db, WORLD, names, set())
            assert not rules[0]["warnings"]
            assert "Ties with the rule for Smith's Home" in rules[1]["warnings"][0]
            db.execute("UPDATE mud_world_start_rules SET sort_order=-1 WHERE room_id=?", (room(db, "tavern"),))
            assert _located(db, _create(db, "bb", "elf", "scout")) == room(db, "tavern")
            assert not any(rule["warnings"] for rule in mud_start_rules.review(db, WORLD, names, set()))


def test_rules_that_cannot_overlap_are_not_ties():
    doc = _world(_rule("elves", "home", [_race("elf")]), _rule("dwarves", "tavern", [_race("dwarf")]))
    with running(doc) as (app, _users):
        with app.app_context():
            db = get_db()
            names = {"race": {"elf": "Elf", "dwarf": "Dwarf"}, "class": {}, "member": {}}
            assert not any(rule["warnings"] for rule in mud_start_rules.review(db, WORLD, names, set()))


def test_faction_rule_uses_the_final_choice_and_ignores_factions_never_offered():
    doc = _world(_rule("guild", "tavern", [{"type": "member", "faction": "guild"}]),
                 _rule("council", "forge", [{"type": "member", "faction": "council"}]))
    with running(doc) as (app, _users):
        with app.app_context():
            db = get_db()
            rangers = installed(db, "mud_world_factions", "rangers")
            assert _located(db, _create(db, "aa", faction=rangers)) is None
            guild = installed(db, "mud_world_factions", "guild")
            assert _located(db, _create(db, "bb", faction=guild)) == room(db, "tavern")
            names = {"race": {}, "class": {}, "member": {guild: "Guild", rangers: "Rangers",
                                                         installed(db, "mud_world_factions", "council"): "Council"}}
            warnings = [text for rule in mud_start_rules.review(db, WORLD, names, {guild, rangers})
                        for text in rule["warnings"]]
            assert warnings == ["Council is not offered at character creation, so no new character can match it."]


def test_invalid_rules_fall_through_to_the_next_match_and_default():
    doc = _world(_rule("specific", "forge", [_race("elf"), _class("scout")]),
                 _rule("general", "home", [_race("elf")]))
    other = document("elsewhere")
    with running(doc) as (app, _users):
        with app.app_context():
            db = get_db()
            mud_worlds.install(db, mud_worlds.package_bytes(other))
            foreign = room(db, "forge", "elsewhere")
            db.execute("UPDATE mud_world_start_rules SET room_id=? WHERE room_id=?", (foreign, room(db, "forge")))
            assert _located(db, _create(db, "aa", "elf", "scout")) == room(db, "home")
            db.execute("UPDATE mud_world_start_rules SET spawn_conditions_json='{broken' WHERE room_id=?",
                       (room(db, "home"),))
            assert _located(db, _create(db, "bb", "elf", "scout")) is None
            names = {"race": {"elf": "Elf"}, "class": {"scout": "Scout"}, "member": {}}
            warnings = " ".join(text for rule in mud_start_rules.review(db, WORLD, names, set())
                                for text in rule["warnings"])
            assert "cannot be read" in warnings and "not in this world" in warnings


def test_world_without_rooms_refuses_creation_with_a_clear_error():
    with running(_world()) as (app, _users):
        with app.app_context():
            db = get_db()
            db.execute("PRAGMA defer_foreign_keys=ON")
            db.execute("DELETE FROM mud_world_rooms WHERE world_id=?", (WORLD,))
            with pytest.raises(ValueError, match="no rooms yet"):
                _create(db, "stranded")
            db.rollback()


def test_rules_never_touch_legacy_or_existing_characters():
    with running(_world(_rule("humans", "forge", [_race("human")]))) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            # Created before a rule mattered to it: placed in the square by the test world, and it stays.
            mud._set_player_location(db, one, "traditional", "world_room", room(db, "tavern"))
            assert mud._current_room(db, one)["id"] == room(db, "tavern")
            # Replacing origins is not a new beginning.
            db.execute("UPDATE mud_characters SET race_key='removed' WHERE user_id=?", (one,))
            mud_compat.replace_origins(db, one, "human", "warrior")
            assert _located(db, one) == room(db, "tavern") and _stored_start(db, one) == room(db, "forge")
            # A legacy character finishing a build stays where it stood.
            uid = _user(db, "old")
            character_creation.ensure_character(db, uid)
            db.execute("UPDATE mud_characters SET creation_state='legacy' WHERE user_id=?", (uid,))
            mud._set_player_location(db, uid, "traditional", "world_room", room(db, "tavern"))
            character_creation.complete(db, uid, _build(db, "oldtimer"))
            assert _located(db, uid) == room(db, "tavern") and _stored_start(db, uid) is None


def test_rule_edits_do_not_move_characters_and_defeat_uses_the_stored_room():
    with running(_world(_rule("elves", "home", [_race("elf")]))) as (app, _users):
        with app.app_context():
            db = get_db()
            uid = _create(db, "wanderer", "elf")
            assert _stored_start(db, uid) == room(db, "home")
            db.execute("DELETE FROM mud_world_start_rules")
            mud._set_player_location(db, uid, "traditional", "world_room", room(db, "tavern"))
            assert mud._current_room(db, uid)["id"] == room(db, "tavern")
            # The stored room is never re-evaluated, and a stale one falls back to the default.
            assert mud._home_room(db, uid)["id"] == room(db, "home")
            db.execute("UPDATE mud_characters SET start_room_id='gone' WHERE user_id=?", (uid,))
            assert mud._home_room(db, uid)["id"] == room(db, "square")


def test_recreating_a_character_evaluates_the_rules_again():
    with running(_world(_rule("elves", "home", [_race("elf")]))) as (app, _users):
        with app.app_context():
            db = get_db()
            uid = _create(db, "reborn", "elf")
            db.execute("DELETE FROM mud_player_locations WHERE user_id=?", (uid,))
            db.execute("DELETE FROM mud_world_locations WHERE user_id=?", (uid,))
            db.execute("DELETE FROM mud_item_instances WHERE owner_character_id=?",
                       (mud._character_id(db, uid),))
            db.execute("DELETE FROM mud_characters WHERE user_id=?", (uid,))
            character_creation.ensure_character(db, uid)
            character_creation.complete(db, uid, _build(db, "reborn", "dwarf"))
            assert _located(db, uid) is None and _stored_start(db, uid) is None


def test_builder_adds_edits_reviews_and_removes_rules():
    with running(_world()) as (app, (admin, _two)):
        with app.app_context():
            db = get_db()
            home, tavern = room(db, "home"), room(db, "tavern")
            guild = installed(db, "mud_world_factions", "guild")
            other = document("elsewhere")
            mud_worlds.install(db, mud_worlds.package_bytes(other))
            foreign = room(db, "forge", "elsewhere")
            db.commit()
        browser = client(app, admin)
        base = f"/mud/w/{WORLD}/builder/start-rules"
        token = {"csrf_token": "test-token"}

        def stored():
            with app.app_context():
                return [dict(row) for row in get_db().execute("SELECT * FROM mud_world_start_rules")]

        for bad in ({"room_id": home}, {"room_id": foreign, "races": "elf"}, {"room_id": home, "races": "unknown"},
                    {"room_id": home, "factions": "nope"}, {"room_id": home, "races": "elf", "sort_order": "99999"}):
            assert browser.post(base, data={**token, **bad}).status_code == 302
        assert stored() == []
        browser.post(base, data={**token, "room_id": home, "races": ["elf", "dwarf"], "classes": "scout",
                                 "factions": guild, "sort_order": "3"})
        rule = stored()[0]
        assert rule["room_id"] == home and rule["sort_order"] == 3
        page = browser.get(f"/mud/w/{WORLD}/builder").get_data(as_text=True)
        assert "Start rules" in page and "Class: Scout" in page and "Faction: Smiths Guild" in page
        browser.post(f"{base}/{rule['id']}", data={**token, "room_id": tavern, "classes": "scout"})
        assert stored()[0]["room_id"] == tavern
        assert json.loads(stored()[0]["spawn_conditions_json"]) == [_class("scout")]
        browser.post(f"{base}/{rule['id']}/delete", data=token)
        assert len(stored()) == 1
        browser.post(f"{base}/{rule['id']}/delete", data={**token, "confirmation": "delete"})
        assert stored() == []


def test_foreign_world_rules_cannot_be_edited():
    with running(_world()) as (app, (admin, _two)):
        with app.app_context():
            db = get_db()
            mud_worlds.install(db, mud_worlds.package_bytes(_elsewhere()))
            db.commit()
            rule = db.execute("SELECT id FROM mud_world_start_rules WHERE world_id='elsewhere'").fetchone()["id"]
            home = room(db, "home")
        response = client(app, admin).post(f"/mud/w/{WORLD}/builder/start-rules/{rule}/delete",
                                           data={"csrf_token": "test-token", "confirmation": "delete"})
        assert response.status_code == 404
        response = client(app, admin).post(f"/mud/w/{WORLD}/builder/start-rules/{rule}",
                                           data={"csrf_token": "test-token", "room_id": home, "races": "elf"})
        assert response.status_code == 404


def _elsewhere():
    doc = document("elsewhere")
    doc["content"]["mud_world_start_rules"] = [_rule("elves", "home", [_race("elf")])]
    return doc


def test_room_with_a_rule_cannot_be_deleted():
    with running(_world(_rule("elves", "home", [_race("elf")]))) as (app, (admin, _two)):
        with app.app_context():
            db = get_db()
            home = room(db, "home")
        response = client(app, admin).post(f"/mud/w/{WORLD}/builder/rooms/{home}/delete", data={
            "csrf_token": "test-token", "confirmation": "Smith's Home"}, follow_redirects=True)
        assert "start rules using this room" in response.get_data(as_text=True).replace("&#39;", "'")
        with app.app_context():
            assert get_db().execute("SELECT 1 FROM mud_world_rooms WHERE id=?", (home,)).fetchone()


@pytest.mark.parametrize("rule,message", [
    (_rule("bad", "nowhere", [_race("elf")]), "room_id must name a room"),
    (_rule("bad", "home", []), "needs a race, class, or faction"),
    (_rule("bad", "home", [{"type": "member", "faction": "unknown"}]), "must name factions"),
    (_rule("bad", "home", [{"type": "level", "min": 2}]), "Unsupported condition"),
    (_rule("bad", "home", [_race("elf")], order=99999), "sort_order"),
])
def test_invalid_packages_are_refused(rule, message):
    with pytest.raises(mud_worlds.WorldError, match=message):
        mud_worlds.read_package(mud_worlds.package_bytes(_world(rule)))


def test_export_and_upgrade_keep_rules_and_character_rooms():
    doc = _world(_rule("guilded-elves", "home", [_race("elf"), {"type": "member", "faction": "guild"}], order=4))
    with running(doc) as (app, _users):
        with app.app_context():
            db = get_db()
            guild = installed(db, "mud_world_factions", "guild")
            kept = _create(db, "traveller", "elf", faction=guild)
            lost = _create(db, "castaway", "elf", faction=guild)
            db.execute("UPDATE mud_characters SET start_room_id=? WHERE user_id=?", (room(db, "tavern"), lost))
            old_room = _stored_start(db, kept)
            payload = mud_worlds.export(db, WORLD)
            exported = mud_worlds.read_package(payload)["content"]["mud_world_start_rules"]
            assert exported[0]["room_id"] == "home" and exported[0]["sort_order"] == 4
            assert {"type": "member", "faction": "guild"} in exported[0]["spawn_conditions_json"]
            # The new version drops the tavern; the rule's room and faction get new IDs.
            changed = json.loads(json.dumps(doc))
            changed["rooms"] = [entry for entry in changed["rooms"] if entry["id"] != "tavern"]
            changed["exits"] = [entry for entry in changed["exits"] if "tavern" not in (entry["from"], entry["to"])]
            del changed["content"]["mud_npc_definitions"][0]["behaviors_json"]
            package = mud_worlds.package_bytes(changed)
            db.commit()
            mud_worlds.upgrade(db, package, mud_worlds.upgrade_preview(db, package)["expected_hash"])
            assert _stored_start(db, kept) == room(db, "home") != old_room
            assert _stored_start(db, lost) is None
            rule = db.execute("SELECT room_id,spawn_conditions_json FROM mud_world_start_rules").fetchall()
            assert len(rule) == 1 and rule[0]["room_id"] == room(db, "home")
            live = db.execute("SELECT id FROM mud_world_factions WHERE world_id=? AND id LIKE '%:guild'",
                              (WORLD,)).fetchone()["id"]
            assert live in rule[0]["spawn_conditions_json"]


def test_plain_world_export_has_no_rule_content():
    with running(_world()) as (app, _users):
        with app.app_context():
            payload = mud_worlds.export(get_db(), WORLD)
            assert "mud_world_start_rules" not in mud_worlds.read_package(payload)["content"]


def test_create_page_places_the_character_and_reports_a_world_without_rooms():
    with running(_world(_rule("elves", "home", [_race("elf")]))) as (app, _users):
        with app.app_context():
            db = get_db()
            db.execute("UPDATE mud_world_packages SET enabled=1 WHERE id=?", (WORLD,))
            uid = _user(db, "webby")
            stranded = _user(db, "stranded")
            build = json.dumps(_build(db, "webby", "elf"))
            db.commit()
        url = f"/mud/w/{WORLD}/create"
        response = client(app, uid).post(url, data={"csrf_token": "test-token", "build": build})
        assert response.status_code == 302 and response.headers["Location"].endswith(f"/mud/w/{WORLD}")
        with app.app_context():
            db = get_db()
            assert _located(db, uid) == room(db, "home")
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("DELETE FROM mud_world_rooms WHERE world_id=?", (WORLD,))
            db.commit()
            db.execute("PRAGMA foreign_keys=ON")
        # A second character still sees the form, with the reason and nothing saved.
        taken = build.replace("Webby", "Stranded")
        response = client(app, stranded).post(url, data={"csrf_token": "test-token", "build": taken})
        assert response.status_code == 200 and "no rooms yet" in response.get_data(as_text=True)
        with app.app_context():
            state = get_db().execute("SELECT creation_state FROM mud_characters WHERE user_id=? AND world_key=?",
                                     (stranded, WORLD)).fetchone()["creation_state"]
            assert state == "pending"


def test_defeat_wakes_a_character_in_its_own_start_room():
    with running(_world(_rule("elves", "home", [_race("elf")]))) as (app, (one, _two)):
        with app.app_context():
            db = get_db()
            uid = _create(db, "fallen", "elf")
            forge = room(db, "forge")
            mud._set_player_location(db, uid, "traditional", "world_room", forge)
            mud._set_player_location(db, one, "traditional", "world_room", forge)
            encounter = {"scope_type": "room", "scope_key": forge}
            for fighter in (uid, one):
                player = db.execute("SELECT * FROM users WHERE id=?", (fighter,)).fetchone()
                drafts = mud._defeat_player(db, player, encounter, {"name": "Bear"})
                assert "You awaken in" in drafts[0].body
            assert mud._current_room(db, uid)["id"] == room(db, "home")
            assert mud._current_room(db, one)["id"] == room(db, "square")


def test_wilderness_clear_keeps_rooms_used_by_rules():
    with running(_world(_rule("elves", "home", [_race("elf")]))) as (app, (admin, _two)):
        with app.app_context():
            db = get_db()
            home = room(db, "home")
            db.execute("UPDATE mud_world_rooms SET kind='wild',x=5,y=5,z=0 WHERE id=?", (home,))
            db.commit()
        response = client(app, admin).post(f"/mud/w/{WORLD}/builder/wilderness/clear", data={
            "csrf_token": "test-token", "shape": "rect", "x1": "4", "y1": "4", "x2": "6", "y2": "6", "z": "0",
            "confirm_clear": "1"}, follow_redirects=True)
        assert "Start rules use rooms in that area" in response.get_data(as_text=True)
        with app.app_context():
            assert get_db().execute("SELECT 1 FROM mud_world_rooms WHERE id=?", (home,)).fetchone()
