"""Player housing and storage: properties, private copies, shared ownership, rent, storage, and packages."""
import copy
from datetime import datetime, timedelta, timezone

import pytest

from flask import session

from mud_society_support import act, installed, place, room, running, set_society, user
from mud_world_helpers import url
from stationnet import mud, mud_housing, mud_worlds
from stationnet.db import get_db

WORLD = "hometown"


def _document(**changes):
    """A street with a cottage (one owner set), an apartment block (a copy each), a locker and a stash."""
    document = {
        "id": WORLD, "name": "Hometown", "author": "Test", "start_room": "square",
        "rooms": [{"id": "square", "name": "High Street", "description": "A street."},
                  {"id": "cottage", "name": "Harbor Cottage", "description": "A snug cottage."},
                  {"id": "loft", "name": "Cottage Loft", "description": "Under the roof."},
                  {"id": "office", "name": "Letting Office", "description": "Keys on hooks."},
                  {"id": "flat", "name": "Small Flat", "description": "Bare walls."},
                  {"id": "bedroom", "name": "Flat Bedroom", "description": "A narrow bed."}],
        "exits": [{"from": "square", "to": "cottage", "name": "cottage door", "direction": "north"},
                  {"from": "cottage", "to": "square", "name": "cottage door", "direction": "south"},
                  {"from": "cottage", "to": "loft", "name": "ladder", "direction": "up"},
                  {"from": "loft", "to": "cottage", "name": "ladder", "direction": "down"},
                  {"from": "square", "to": "office", "name": "office door", "direction": "east"},
                  {"from": "office", "to": "square", "name": "office door", "direction": "west"},
                  {"from": "office", "to": "flat", "name": "stairs", "direction": "up"},
                  {"from": "flat", "to": "office", "name": "stairs", "direction": "down"},
                  {"from": "flat", "to": "bedroom", "name": "bedroom door", "direction": "north"},
                  {"from": "bedroom", "to": "flat", "name": "bedroom door", "direction": "south"}],
        "content": {
            "mud_item_definitions": [
                {"id": "nail", "name": "Iron Nail", "description": "A nail.", "item_type": "misc"},
                {"id": "sack", "name": "Burlap Sack", "description": "A sack.", "item_type": "container"},
                {"id": "pistol", "name": "Old Pistol", "description": "A pistol.", "item_type": "equipment",
                 "equipment_slot": "main_hand"}],
            "mud_properties": [
                {"id": "cottage", "name": "Harbor Cottage", "description": "A cottage by the harbor.",
                 "entrance_room_id": "square", "price": 100, "refund_percent": 50, "rent": 31,
                 "rent_period_seconds": 86400, "grace_seconds": 3600, "max_owners": 3},
                {"id": "apartment", "name": "Apartment", "description": "A flat of your own.",
                 "kind": "instanced", "acquisition": "claim", "entrance_room_id": "office", "max_owners": 2}],
            "mud_property_rooms": [{"room_id": "cottage", "property_id": "cottage"},
                                   {"room_id": "loft", "property_id": "cottage"},
                                   {"room_id": "flat", "property_id": "apartment"},
                                   {"room_id": "bedroom", "property_id": "apartment"}],
            "mud_storage_points": [
                {"id": "chest", "room_id": "cottage", "name": "Oak Chest", "scope": "property", "capacity": 3},
                {"id": "closet", "room_id": "flat", "name": "Closet", "scope": "property", "capacity": 5},
                {"id": "locker", "room_id": "office", "name": "Locker", "scope": "personal", "capacity": 2,
                 "item_types": ["misc", "container"]},
                {"id": "stash", "room_id": "square", "name": "Town Stash", "scope": "communal"}],
        },
    }
    document["content"].update(changes)
    return document


def _cid(db, uid):
    return mud._character_id(db, uid)


def _coins(db, uid, amount=None):
    cid = _cid(db, uid)
    if amount is not None:
        mud._wallet(db, cid)
        db.execute("UPDATE mud_character_wallets SET coins=? WHERE character_id=?", (amount, cid))
    return mud._wallet(db, cid)


def _item(db, uid, local):
    return mud._grant_item(db, uid, installed(db, "mud_item_definitions", local, WORLD))


def _carried(db, uid):
    """Carried items from this world's content, leaving out starting gear."""
    return sorted(row["name"] for row in mud._inventory_rows(db, uid) if row["definition_id"].startswith(f"world:{WORLD}:"))


def _where(db, uid):
    return mud._current_room(db, uid)["name"]


def _holding(db, local="cottage"):
    return db.execute("SELECT id FROM mud_property_holdings WHERE property_id=?",
                      (installed(db, "mud_properties", local, WORLD),)).fetchone()


@pytest.fixture
def town():
    with running(_document(), users=("ada", "bram", "cara")) as (app, ids):
        with app.app_context():
            yield get_db(), ids


def test_package_rules_reject_conflicting_housing():
    def problems(document):
        with pytest.raises(mud_worlds.WorldError) as error:
            mud_worlds.read_package(mud_worlds.package_bytes(document))
        return str(error.value)

    doubled = _document()
    doubled["content"]["mud_property_rooms"].append({"room_id": "flat", "property_id": "cottage"})
    assert "more than one property" in problems(doubled)
    started = _document()
    started["start_room"] = "cottage"
    assert "start room cannot be a property room" in problems(started)
    loose = _document()
    loose["content"]["mud_storage_points"].append({"id": "loose", "room_id": "office", "name": "Loose Box",
                                                  "scope": "property"})
    assert "must be in a property room" in problems(loose)
    priced = _document()
    priced["content"]["mud_properties"][1]["price"] = 5
    assert "Only purchased properties have a price" in problems(priced)
    typed = _document()
    typed["content"]["mud_storage_points"][2]["item_types"] = ["gems"]
    assert "item types" in problems(typed)


def test_worlds_without_properties_hide_house(town):
    db, (ada, _bram, _cara) = town
    assert "house" not in mud._hidden_actions(db, ada)
    db.execute("DELETE FROM mud_properties")
    assert "house" in mud._hidden_actions(db, ada)


def test_buying_a_cottage_keeps_others_out(town):
    db, (ada, bram, _cara) = town
    _coins(db, ada, 200)
    look = act(db, bram, "/look")
    assert "Offered here: Harbor Cottage (100 coins, rent 31 coins every 1 day)" in look
    assert "Town Stash (shared)" in look
    with pytest.raises(mud.ActionError, match="costs 131 coins"):
        act(db, bram, "/house buy Harbor Cottage")
    assert "You pay for Harbor Cottage (131 coins). Rent is paid until" in act(db, ada, "/house buy harbor cottage")
    assert _coins(db, ada) == 69
    with pytest.raises(mud.ActionError, match="already has an owner"):
        _coins(db, bram, 500)
        act(db, bram, "/house buy Harbor Cottage")
    assert "Harbor Cottage [private]" in act(db, bram, "/look")
    with pytest.raises(mud.ActionError, match="Harbor Cottage is private"):
        act(db, bram, "/go north")
    act(db, ada, "/go north")
    assert _where(db, ada) == "Harbor Cottage"
    assert "Property: Harbor Cottage - yours (primary owner), rent paid until" in act(db, ada, "/look")
    with pytest.raises(mud.ActionError, match="property's storage"):
        _item(db, ada, "nail")
        act(db, ada, "/drop Iron Nail")
    act(db, ada, "/go up")
    assert _where(db, ada) == "Cottage Loft"
    # Teleports respect privacy too.
    assert mud._trigger_teleport(db, user(db, bram), room(db, "loft", WORLD))[0].body == "Harbor Cottage is private."


def test_each_holder_gets_a_private_apartment(town):
    db, (ada, bram, cara) = town
    for uid in (ada, bram, cara):
        place(db, uid, room(db, "office", WORLD))
    with pytest.raises(mud.ActionError, match="you have no place there"):
        act(db, cara, "/go up")
    act(db, ada, "/house claim Apartment")
    act(db, bram, "/house claim apartment")
    act(db, ada, "/go up")
    act(db, bram, "/go up")
    first, second = mud._current_room(db, ada), mud._current_room(db, bram)
    assert first["name"] == second["name"] == "Small Flat" and first["id"] != second["id"]
    assert first["kind"] == "housing" and first["template_room_id"] == room(db, "flat", WORLD)
    assert "Other players: none" in act(db, ada, "/look")
    act(db, ada, "/go north")
    assert _where(db, ada) == "Flat Bedroom"
    assert mud._current_room(db, ada)["holding_id"] == first["holding_id"]
    act(db, ada, "/go south")
    assert mud._current_room(db, ada)["id"] == first["id"]
    # Template edits show in every copy straight away.
    db.execute("UPDATE mud_world_rooms SET name='Tidy Flat' WHERE id=?", (room(db, "flat", WORLD),))
    assert _where(db, bram) == "Tidy Flat"
    act(db, ada, "/go down")
    assert _where(db, ada) == "Letting Office"
    with pytest.raises(mud.ActionError, match="already have Apartment"):
        act(db, ada, "/house claim Apartment")


def test_co_owners_share_storage_and_rent(town):
    db, (ada, bram, cara) = town
    _coins(db, ada, 500)
    _coins(db, bram, 100)
    act(db, ada, "/house buy Harbor Cottage")
    assert "invites you to share Harbor Cottage. Your share of the rent would be 15 coins" in act(
        db, ada, "/house invite Harbor Cottage Bram")
    with pytest.raises(mud.ActionError, match="already shares or is invited"):
        act(db, ada, "/house invite Harbor Cottage to bram")
    assert "Invitations: Harbor Cottage" in act(db, bram, "/house")
    with pytest.raises(mud.ActionError, match="Harbor Cottage is private"):
        act(db, bram, "/go north")
    assert "You now share Harbor Cottage, paying 15 coins of rent." in act(db, bram, "/house accept")
    assert _coins(db, bram) == 85
    listing = act(db, ada, "/house")
    assert "Harbor Cottage (primary owner) with Bram. Your rent: 16 coins every 1 day" in listing
    with pytest.raises(mud.ActionError, match="Only a primary owner"):
        act(db, bram, "/house invite Harbor Cottage Cara")

    act(db, bram, "/go north")
    _item(db, bram, "nail")
    sack = _item(db, bram, "sack")
    assert act(db, bram, "/storage deposit Iron Nail") == "You put Iron Nail in Oak Chest."
    assert act(db, bram, "/storage deposit Burlap Sack into oak chest") == "You put Burlap Sack in Oak Chest."
    assert _carried(db, bram) == []
    place(db, ada, room(db, "cottage", WORLD))
    assert "Oak Chest (owners only, 2/3): Burlap Sack, Iron Nail" in act(db, ada, "/storage")
    # Withdrawing hands the item to whoever takes it out.
    act(db, ada, "/storage withdraw Burlap Sack")
    assert db.execute("SELECT owner_character_id FROM mud_item_instances WHERE id=?",
                      (sack,)).fetchone()[0] == _cid(db, ada)
    with pytest.raises(mud.ActionError, match="cannot find"):
        act(db, bram, "/storage withdraw Burlap Sack")

    # Removing a co-owner returns what they stored and walks them out.
    assert "You remove Bram from Harbor Cottage." in act(db, ada, "/house remove Harbor Cottage Bram")
    assert _carried(db, bram) == ["Iron Nail"] and _where(db, bram) == "High Street"
    with pytest.raises(mud.ActionError, match="private"):
        act(db, bram, "/go north")


def test_transfer_leave_and_abandon(town):
    db, (ada, bram, _cara) = town
    _coins(db, ada, 500)
    _coins(db, bram, 100)
    act(db, ada, "/house buy Harbor Cottage")
    act(db, ada, "/house invite Harbor Cottage Bram")
    act(db, bram, "/house accept Harbor Cottage")
    act(db, ada, "/house transfer Harbor Cottage Bram")
    assert "Harbor Cottage (primary owner) with Ada" in act(db, bram, "/house")
    act(db, ada, "/house leave Harbor Cottage")
    assert "You hold no property" in act(db, ada, "/house")
    with pytest.raises(mud.ActionError, match="ends it for every owner"):
        act(db, bram, "/house abandon Harbor Cottage")
    before = _coins(db, bram)
    assert act(db, bram, "/house abandon Harbor Cottage confirm").startswith(
        "You abandon Harbor Cottage and are refunded 50 coins.")
    assert _coins(db, bram) == before + 50 and _holding(db) is None


def test_storage_limits_and_item_safety(town):
    db, (ada, bram, _cara) = town
    place(db, ada, room(db, "office", WORLD))
    place(db, bram, room(db, "office", WORLD))
    sack = _item(db, ada, "sack")
    nail = _item(db, ada, "nail")
    db.execute("UPDATE mud_item_instances SET container_id=?,owner_character_id=NULL WHERE id=?", (sack, nail))
    db.execute("UPDATE mud_item_instances SET state_json='{\"rounds\": 7}' WHERE id=?", (sack,))
    _item(db, ada, "pistol")
    with pytest.raises(mud.ActionError, match="only holds misc, container items"):
        act(db, ada, "/storage deposit Old Pistol")
    act(db, ada, "/storage deposit Burlap Sack")
    # The sack and the nail inside it fill the two-item locker.
    _item(db, ada, "nail")
    with pytest.raises(mud.ActionError, match="Locker is full \\(2/2\\)"):
        act(db, ada, "/storage deposit Iron Nail")
    assert "Locker (personal, 0/2): empty" in act(db, bram, "/storage")
    with pytest.raises(mud.ActionError, match="cannot find"):
        act(db, bram, "/storage withdraw Burlap Sack")
    act(db, ada, "/storage withdraw Burlap Sack")
    with pytest.raises(mud.ActionError, match="cannot find"):
        act(db, ada, "/storage withdraw Burlap Sack")
    row = db.execute("SELECT state_json FROM mud_item_instances WHERE id=?", (sack,)).fetchone()
    assert row["state_json"] == '{"rounds": 7}'
    assert db.execute("SELECT container_id FROM mud_item_instances WHERE id=?", (nail,)).fetchone()[0] == sack
    # World storage keeps its point, so portal storage never lists it.
    act(db, ada, "/storage deposit Burlap Sack")
    assert db.execute("SELECT storage_point_id FROM mud_character_storage WHERE item_instance_id=?",
                      (sack,)).fetchone()[0] == installed(db, "mud_storage_points", "locker", WORLD)


def test_communal_stash_and_replayed_requests():
    with running(_document(), users=("ada", "bram")) as (app, (ada, bram)):
        with app.test_request_context(url("/actions"), method="POST"):
            db = get_db()
            _item(db, ada, "nail")
            act(db, ada, "/storage deposit Iron Nail")
            assert "Town Stash (shared, 1/20): Iron Nail" in act(db, bram, "/storage")
            db.commit()
            session["connection_id"] = f"connection-bram"
            definition, parameters = mud.actions.from_command("/storage withdraw Iron Nail")
            request = mud.ActionRequest(definition, parameters, request_id="withdraw-once")
            first = mud.service.execute(request, user(db, bram))
            second = mud.service.execute(request, user(db, bram))
            assert first[1] == second[1]
            assert _carried(db, bram) == ["Iron Nail"] and _carried(db, ada) == []


def test_rent_reminders_and_lapses(town):
    db, (ada, bram, _cara) = town
    _coins(db, ada, 500)
    _coins(db, bram, 100)
    act(db, ada, "/house buy Harbor Cottage")
    act(db, ada, "/house invite Harbor Cottage Bram")
    act(db, bram, "/house accept Harbor Cottage")
    holding = _holding(db)["id"]
    now = datetime.now(timezone.utc)
    with pytest.raises(mud.ActionError, match="at most 4 periods ahead; 1 is already paid"):
        act(db, ada, "/house pay Harbor Cottage 4")
    assert "You pay 48 coins of rent" in act(db, ada, "/house pay Harbor Cottage 3")

    # Bram's share falls due, then lapses past the grace period.
    db.execute("UPDATE mud_property_owners SET paid_until=? WHERE character_id=?",
               (mud_housing.stamp(now - timedelta(minutes=10)), _cid(db, bram)))
    place(db, bram, room(db, "loft", WORLD))
    _item(db, bram, "nail")
    db.execute("INSERT INTO mud_character_storage(item_instance_id,character_id,storage_point_id,holding_id) "
               "SELECT id,?,?,? FROM mud_item_instances WHERE owner_character_id=?",
               (_cid(db, bram), installed(db, "mud_storage_points", "chest", WORLD), holding, _cid(db, bram)))
    mud.process_housing(db, now)
    reminder = db.execute("SELECT body FROM mud_events WHERE audience_key=? ORDER BY id DESC LIMIT 1",
                          (str(bram),)).fetchone()["body"]
    assert reminder.startswith("Rent for Harbor Cottage is due: pay 15 coins")
    set_society(db, housing={"enabled": False})
    mud.process_housing(db, now + timedelta(hours=2))
    assert mud._owner_row(db, holding, _cid(db, bram)) is not None
    set_society(db, housing={"enabled": True})
    mud.process_housing(db, now + timedelta(hours=2))
    assert mud._owner_row(db, holding, _cid(db, bram)) is None
    assert _carried(db, bram) == ["Iron Nail"] and _where(db, bram) == "High Street"
    assert mud._owner_row(db, holding, _cid(db, ada)) is not None

    # The primary owner lapsing ends the holding for everyone.
    db.execute("UPDATE mud_property_owners SET paid_until=? WHERE character_id=?",
               (mud_housing.stamp(now - timedelta(hours=5)), _cid(db, ada)))
    place(db, ada, room(db, "cottage", WORLD))
    mud.process_housing(db, now)
    assert _holding(db) is None and _where(db, ada) == "High Street"


def test_grants_conditions_and_scripted_revoke(town):
    db, (ada, bram, _cara) = town
    cottage = installed(db, "mud_properties", "cottage", WORLD)
    context = mud._ConditionContext(db, ada)
    owns = [{"type": "owns_property", "property": cottage}]
    assert not mud.mud_conditions.evaluate(owns, context)
    events = mud._settle_outcomes(db, user(db, ada), [{"type": "grant_property", "property": cottage}])
    assert events[0].body.startswith("You are given Harbor Cottage.")
    assert _coins(db, ada) == 30
    assert mud.mud_conditions.evaluate(owns, mud._ConditionContext(db, ada))
    assert mud.mud_conditions.evaluate([{"type": "owns_property", "property": cottage, "role": "primary"}],
                                       mud._ConditionContext(db, ada))
    assert not mud.mud_conditions.evaluate(owns, mud._ConditionContext(db, bram))
    mud._scripted_property(db, user(db, ada), "revoke_property", cottage)
    assert _holding(db) is None
    assert mud.mud_conditions.validate_outcomes([{"type": "grant_property", "property": "cottage"}])
    with pytest.raises(ValueError, match="role must be owner or primary"):
        mud.mud_conditions.validate_conditions([{"type": "owns_property", "property": "x", "role": "guest"}])


def test_housing_limits_and_closed_offers(town):
    db, (ada, _bram, _cara) = town
    set_society(db, housing={"max_holdings": 1})
    _coins(db, ada, 500)
    act(db, ada, "/house buy Harbor Cottage")
    place(db, ada, room(db, "office", WORLD))
    with pytest.raises(mud.ActionError, match="at most 1 property"):
        act(db, ada, "/house claim Apartment")
    db.execute("UPDATE mud_properties SET available=0 WHERE id=?", (installed(db, "mud_properties", "apartment", WORLD),))
    with pytest.raises(mud.ActionError, match="not being offered"):
        act(db, ada, "/house claim Apartment")
    set_society(db, housing={"enabled": False})
    apartment = db.execute("SELECT * FROM mud_properties WHERE id=?",
                           (installed(db, "mud_properties", "apartment", WORLD),)).fetchone()
    with pytest.raises(mud.ActionError, match="Housing is closed"):
        mud._acquire_property(db, user(db, ada), apartment, granted=True)


def test_deleting_a_primary_hands_the_property_on(town):
    db, (ada, bram, _cara) = town
    _coins(db, ada, 500)
    _coins(db, bram, 100)
    act(db, ada, "/house buy Harbor Cottage")
    act(db, ada, "/house invite Harbor Cottage Bram")
    act(db, bram, "/house accept Harbor Cottage")
    mud.character_leaving_holdings(db, _cid(db, ada))
    assert "Harbor Cottage (primary owner)" in act(db, bram, "/house")
    mud.character_leaving_holdings(db, _cid(db, bram))
    assert _holding(db) is None


def test_export_and_upgrade_keep_housing(town):
    db, (ada, bram, _cara) = town
    _coins(db, ada, 500)
    act(db, ada, "/house buy Harbor Cottage")
    place(db, bram, room(db, "office", WORLD))
    act(db, bram, "/house claim Apartment")
    act(db, bram, "/go up")
    copy_id = mud._current_room(db, bram)["id"]
    _item(db, bram, "nail")
    act(db, bram, "/storage deposit Iron Nail")
    db.commit()

    exported = mud_worlds.read_package(mud_worlds.export(db, WORLD))
    content = exported["content"]
    assert {row["id"] for row in content["mud_properties"]} == {"cottage", "apartment"}
    apartment = next(row for row in content["mud_properties"] if row["id"] == "apartment")
    assert apartment == {"id": "apartment", "name": "Apartment", "description": "A flat of your own.",
                         "kind": "instanced", "acquisition": "claim", "entrance_room_id": "office",
                         "max_owners": 2}
    assert len(content["mud_property_rooms"]) == 4
    assert next(row for row in content["mud_storage_points"] if row["id"] == "locker")["item_types"] == "misc,container"

    version_two = _document()
    version_two["name"] = "Hometown Two"
    payload = mud_worlds.package_bytes(version_two)
    mud_worlds.upgrade(db, payload, mud_worlds.package_hash(payload))
    assert _holding(db) is not None
    assert mud._current_room(db, bram)["id"] == copy_id
    assert "Closet (owners only, 1/5): Iron Nail" in act(db, bram, "/storage")

    # A version without the cottage ends that holding safely.
    version_three = _document()
    version_three["content"]["mud_properties"] = version_three["content"]["mud_properties"][1:]
    version_three["content"]["mud_property_rooms"] = version_three["content"]["mud_property_rooms"][2:]
    version_three["content"]["mud_storage_points"] = version_three["content"]["mud_storage_points"][1:]
    payload = mud_worlds.package_bytes(version_three)
    mud_worlds.upgrade(db, payload, mud_worlds.package_hash(payload))
    assert db.execute("SELECT COUNT(*) FROM mud_property_holdings").fetchone()[0] == 1
    assert "You hold no property" in act(db, ada, "/house")


def test_plain_worlds_export_no_housing_tables(town):
    db, _ids = town
    plain = copy.deepcopy(_document())
    plain["id"], plain["name"] = "plainville", "Plainville"
    for table in ("mud_properties", "mud_property_rooms", "mud_storage_points"):
        del plain["content"][table]
    mud_worlds.install(db, mud_worlds.package_bytes(plain))
    content = mud_worlds.read_package(mud_worlds.export(db, "plainville"))["content"]
    assert not {"mud_properties", "mud_property_rooms", "mud_storage_points"} & set(content)


def test_builder_manages_properties_and_storage():
    from mud_society_support import client
    with running(_document(), users=("ada", "bram")) as (app, (ada, bram)):
        web = client(app, ada)
        page = web.get(f"/mud/w/{WORLD}/builder")
        assert page.status_code == 200 and b"Housing and storage" in page.data and b"Storage in Small Flat" in page.data
        with app.app_context():
            db = get_db()
            street, office = room(db, "square", WORLD), room(db, "office", WORLD)
            cottage_room, loft = room(db, "cottage", WORLD), room(db, "loft", WORLD)
            cottage = installed(db, "mud_properties", "cottage", WORLD)
            _coins(db, ada, 500)
            act(db, ada, "/house buy Harbor Cottage")
            place(db, ada, room(db, "cottage", WORLD))
            _item(db, ada, "nail")
            act(db, ada, "/storage deposit Iron Nail")
            db.commit()
        form = {"csrf_token": "test-token", "name": "Watch Bunker", "description": "Concrete.", "kind": "unique",
                "acquisition": "claim", "entrance_room_id": street, "room_ids": [office], "max_owners": "1",
                "rent": "0", "conditions_json": "[]", "available": "1"}
        assert web.post(f"/mud/w/{WORLD}/builder/properties", data=form).status_code == 302
        with app.app_context():
            db = get_db()
            bunker = db.execute("SELECT * FROM mud_properties WHERE name='Watch Bunker'").fetchone()
            assert bunker["acquisition"] == "claim" and bunker["world_id"] == WORLD
        # A room can belong to only one property.
        web.post(f"/mud/w/{WORLD}/builder/properties", data={**form, "name": "Second Bunker"})
        web.post(f"/mud/w/{WORLD}/builder/rooms/{office}/storage", data={
            "csrf_token": "test-token", "name": "Ammo Crate", "scope": "property", "capacity": "4",
            "item_types": ["equipment"], "conditions_json": "[]"})
        # Kind cannot change while someone holds the cottage; deleting it hands items back.
        web.post(f"/mud/w/{WORLD}/builder/properties/{cottage}", data={
            **form, "name": "Harbor Cottage", "kind": "instanced", "acquisition": "purchase",
            "room_ids": [cottage_room, loft]})
        with app.app_context():
            db = get_db()
            assert db.execute("SELECT COUNT(*) FROM mud_properties WHERE name='Second Bunker'").fetchone()[0] == 0
            crate = db.execute("SELECT * FROM mud_storage_points WHERE name='Ammo Crate'").fetchone()
            assert crate["item_types"] == "equipment" and crate["scope"] == "property"
            assert db.execute("SELECT kind FROM mud_properties WHERE id=?", (cottage,)).fetchone()[0] == "unique"
        assert web.post(f"/mud/w/{WORLD}/builder/rooms/{cottage_room}/delete",
                        data={"csrf_token": "test-token", "confirmation": "Harbor Cottage"}).status_code == 302
        web.post(f"/mud/w/{WORLD}/builder/properties/{cottage}/delete",
                 data={"csrf_token": "test-token", "confirmation": "delete"})
        with app.app_context():
            db = get_db()
            assert db.execute("SELECT 1 FROM mud_world_rooms WHERE id=?", (cottage_room,)).fetchone()
            assert db.execute("SELECT COUNT(*) FROM mud_properties WHERE id=?", (cottage,)).fetchone()[0] == 0
            assert _carried(db, ada) == ["Iron Nail"] and _where(db, ada) == "High Street"
