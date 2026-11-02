"""Vehicles and transit: boarding, driving, modes and terrain, fuel, routes, transit lines, packages, and the builder."""
import copy
from datetime import datetime, timedelta, timezone

import pytest

from flask import session

from mud_society_support import act, client, installed, place, room, running, set_society, user
from mud_world_helpers import url
from stationnet import mud, mud_transport, mud_wayfinding, mud_wilderness, mud_worlds
from stationnet.db import get_db

WORLD = "harbor"


def _preset(key, name, **fields):
    return {"key": key, "name": name, "descriptions": [f"{name} one.", f"{name} two."], **fields}


def _document():
    """A dock and an isle across a strip of open sea, a street with fuel, and a narrow alley.

    Layer 0, x 1..3: y=-1 meadow, meadow, forest; y=0 open sea; y=1 meadow.
    The dock (0,0) touches the sea; the isle (4,0) is reached by a landing
    that needs a water vehicle.
    """
    return {
        "id": WORLD, "name": "Harbor", "author": "Test", "start_room": "square",
        "rooms": [{"id": "square", "name": "Harbor Dock", "description": "Wet planks.", "x": 0, "y": 0, "z": 0},
                  {"id": "isle", "name": "Isle Landing", "description": "A shingle beach.", "x": 4, "y": 0, "z": 0,
                   "grid_link": False},
                  {"id": "street", "name": "Harbor Street", "description": "Cobbles."},
                  {"id": "alley", "name": "Narrow Alley", "description": "Barely a gap."},
                  {"id": "deck", "name": "Rowboat Deck", "description": "Two benches and oars."},
                  {"id": "seat", "name": "Coach Seat", "description": "Padded leather."},
                  {"id": "cabin", "name": "Ferry Cabin", "description": "Salt-stained windows."}],
        "exits": [{"from": "square", "to": "street", "name": "street", "direction": "west"},
                  {"from": "street", "to": "square", "name": "dock", "direction": "east"},
                  {"from": "street", "to": "alley", "name": "alley", "direction": "north", "allow_vehicles": False},
                  {"from": "alley", "to": "street", "name": "street", "direction": "south"},
                  {"from": "square", "to": "@1,-1,0", "name": "meadow path", "direction": "north"},
                  {"from": "@1,-1,0", "to": "square", "name": "path to dock", "direction": "west"},
                  {"from": "@3,0,0", "to": "isle", "name": "landing", "direction": "east", "requires_mode": "water"},
                  {"from": "isle", "to": "@3,0,0", "name": "open water", "direction": "west",
                   "requires_mode": "water"}],
        "content": {
            "mud_item_definitions": [
                {"id": "oats", "name": "Sack of Oats", "description": "Horse feed.", "item_type": "misc"},
                {"id": "nail", "name": "Iron Nail", "description": "A nail.", "item_type": "misc"}],
            "mud_vehicle_definitions": [
                {"id": "rowboat", "name": "Rowboat", "description": "A small boat.", "modes": ["water"],
                 "terrains": ["water"], "mode_only": True, "interior_room_id": "deck", "fuel_capacity": 4,
                 "return_seconds": 60},
                {"id": "horse", "name": "Horse", "description": "A bay mare.", "terrains": ["grassland"],
                 "speed": 200, "fuel_capacity": 5, "fuel_item_id": "oats", "fuel_per_item": 3},
                {"id": "cart", "name": "Hand Cart", "description": "Two wheels."},
                {"id": "coach", "name": "Coach", "description": "A private coach.", "interior_room_id": "seat",
                 "shareable": True}],
            "mud_vehicle_placements": [{"id": "boat-1", "vehicle_definition_id": "rowboat", "room_id": "square"},
                                       {"id": "cart-1", "vehicle_definition_id": "cart", "room_id": "street"}],
            "mud_fuel_stations": [{"id": "pump", "room_id": "street", "price": 2}],
            "mud_transit_lines": [
                {"id": "ferry", "name": "Coast Ferry", "description": "Twice a day.", "fare": 5, "fare_per_stop": 2,
                 "cabin_room_id": "cabin"},
                {"id": "coach-line", "name": "Night Coach", "description": "After dark.",
                 "conditions_json": [{"type": "time", "from": 20, "to": 6}],
                 "conditions_message": "The night coach runs after dark."}],
            "mud_transit_stops": [
                {"id": "ferry-dock", "line_id": "ferry", "room_id": "square", "sort_order": 0},
                {"id": "ferry-isle", "line_id": "ferry", "room_id": "isle", "sort_order": 1, "travel_seconds": 120},
                {"id": "coach-street", "line_id": "coach-line", "room_id": "street", "sort_order": 0},
                {"id": "coach-dock", "line_id": "coach-line", "room_id": "square", "sort_order": 1,
                 "travel_seconds": 30}],
            "mud_world_destinations": [{"id": "isle-landing", "room_id": "isle", "name": "Isle Landing",
                                        "public": True}],
        },
        "wilderness": {
            "presets": [_preset("meadow", "Meadow", terrain="grassland"),
                        _preset("forest", "Forest", terrain="forest", glyph="t"),
                        _preset("sea", "Open Sea", terrain="water", glyph="~", requires_mode="water")],
            "regions": [],
            "layers": [{"z": 0, "origin": [1, -1], "width": 3, "height": 3,
                        "palette": ["", "meadow", "sea", "forest"], "rows": ["2*1,3", "3*2", "3*1"]}],
            "cells": [],
        },
    }


def _package(document):
    return mud_worlds.package_bytes(document)


def _cell(x, y, z=0):
    return mud_wilderness.cell_id(WORLD, x, y, z)


def _where(db, uid):
    return mud._current_room(db, uid)["id"]


def _vehicle(db, local):
    definition = installed(db, "mud_vehicle_definitions", local, WORLD)
    return db.execute("SELECT * FROM mud_vehicles WHERE definition_id=? ORDER BY created_at", (definition,)).fetchone()


def _coins(db, uid, amount=None):
    cid = mud._character_id(db, uid)
    if amount is not None:
        mud._wallet(db, cid)
        db.execute("UPDATE mud_character_wallets SET coins=? WHERE character_id=?", (amount, cid))
    return mud._wallet(db, cid)


def _grant(db, uid, local):
    return mud._scripted_vehicle(db, user(db, uid), "grant_vehicle",
                                 installed(db, "mud_vehicle_definitions", local, WORLD))


def _drafts(db, uid, text):
    definition, parameters = mud.actions.from_command(text)
    return definition.handler(db, user(db, uid), parameters)


def _bodies(db, uid):
    return [row["body"] for row in mud._events_for_user(db, uid)]


@pytest.fixture
def harbor():
    with running(_document(), users=("ada", "bram", "cara")) as (app, ids):
        with app.app_context():
            db = get_db()
            for uid in ids:
                place(db, uid, room(db, "square", WORLD))
            yield db, ids


# Rules ----------------------------------------------------------------------

def test_passable_rules():
    boat = {"name": "Rowboat", "modes": "water", "terrains": "water", "mode_only": 1}
    horse = {"name": "Horse", "modes": "", "terrains": "grassland", "mode_only": 0}
    assert "travels by water" in mud_transport.passable(None, exit_mode="water")
    assert "travels by water" in mud_transport.passable(None, target_kind="wild", target_mode="water")
    assert mud_transport.passable(None, target_kind="authored") is None
    assert mud_transport.passable(boat, target_kind="wild", target_terrain="water", target_mode="water") is None
    assert mud_transport.passable(boat, exit_mode="water", target_kind="authored") is None
    assert "cannot go that way" in mud_transport.passable(boat, target_kind="authored")
    assert "too narrow" in mud_transport.passable(horse, exit_allows=False)
    assert "cannot cross forest" in mud_transport.passable(horse, target_kind="wild", target_terrain="forest")
    assert "cannot travel by water" in mud_transport.passable(horse, target_kind="wild", target_terrain="water",
                                                              target_mode="water")
    assert mud_transport.travel_interval(6, 200) == 3 and mud_transport.travel_interval(6, 400) == 2
    stops = [{"travel_seconds": 0}, {"travel_seconds": 30}, {"travel_seconds": 45}]
    assert mud_transport.trip(stops, 2, 0) == (75, 2)
    assert mud_transport.fare({"fare": 5, "fare_per_stop": 2}, 2) == 9


def test_validators_normalize_and_refuse():
    clean = mud_transport.vehicle({"name": "Boat", "modes": "Water, rail,water", "terrains": ["water"]})
    assert clean["modes"] == "rail,water" and clean["terrains"] == "water"
    for bad in ({"modes": ["Bad Mode!"]}, {"terrains": ["lava"]}, {"speed": 10}, {"fuel_item_id": "oats"},
                {"mode_only": True}, {"shareable": True}):
        with pytest.raises(ValueError):
            mud_transport.vehicle({"name": "Boat", **bad})
    with pytest.raises(ValueError):
        mud_transport.stop({"travel_seconds": 1})


def test_package_rejects_broken_transport():
    def rejected(change, match):
        document = _document()
        change(document)
        with pytest.raises(mud_worlds.WorldError, match=match):
            mud_worlds.read_package(_package(document))

    rejected(lambda d: d["exits"][2].update(requires_mode="water"), "cannot both")
    rejected(lambda d: d["exits"][0].update(requires_mode="Not A Mode"), "required mode")
    rejected(lambda d: d["exits"].append({"from": "deck", "to": "square", "name": "ladder"}), "cannot have exits")
    rejected(lambda d: d["content"]["mud_transit_lines"][0].update(cabin_room_id="square"), "start room")
    rejected(lambda d: d["content"]["mud_transit_stops"].pop(), "at least two stops")
    rejected(lambda d: d["content"]["mud_vehicle_definitions"][1].update(fuel_item_id="hay"), "fuel item")
    rejected(lambda d: d["content"]["mud_vehicle_definitions"][0].update(modes=["Water!"]), "Modes")
    rejected(lambda d: d["content"]["mud_vehicle_definitions"][3].update(interior_room_id="deck"), "room of its own")
    document = _document()
    document["exits"][0]["requires_mode"] = "air"
    report = mud_worlds.PackageReport()
    mud_worlds.read_package(_package(document), report=report)
    assert any("air mode" in problem["message"] for problem in report.warnings)


# Walking and driving ---------------------------------------------------------

def test_walkers_cannot_cross_water_but_a_boat_can(harbor):
    db, (ada, bram, cara) = harbor
    dock = room(db, "square", WORLD)
    assert "[needs water]" in act(db, ada, "/look")
    assert "Vehicles: Rowboat (public, fuel 4/4) (/board <vehicle>)." in act(db, ada, "/look")
    with pytest.raises(mud.ActionError, match="travels by water"):
        act(db, ada, "/go east")
    text = act(db, ada, "/board rowboat")
    assert "You board the Rowboat. You take the controls." in text and "Rowboat Deck" in text
    interior = _where(db, ada)
    assert interior.startswith("vehicle-room:")
    assert "Aboard the Rowboat - you steer, fuel 4/4." in act(db, ada, "/look")
    with pytest.raises(mud.ActionError, match="cannot go that way"):
        act(db, ada, "/go west")
    drafts = _drafts(db, ada, "/go east")
    text = "\n".join(draft.body for draft in drafts)
    assert "You steer the Rowboat east." in text and "Outside the Rowboat: Open Sea" in text
    boat = _vehicle(db, "rowboat")
    assert boat["room_id"] == _cell(1, 0) and boat["fuel"] == 3
    assert _where(db, ada) == interior
    assert any(draft.body == "The Rowboat leaves east." and bram in draft.recipient_ids for draft in drafts)
    assert "Outside the Rowboat: Open Sea" in act(db, ada, "/look out")
    act(db, ada, "/go east")
    act(db, ada, "/go east")
    act(db, ada, "/go landing")
    assert _vehicle(db, "rowboat")["room_id"] == room(db, "isle", WORLD) and _vehicle(db, "rowboat")["fuel"] == 0
    with pytest.raises(mud.ActionError, match="out of fuel"):
        act(db, ada, "/go west")
    assert "You leave the Rowboat." in act(db, ada, "/disembark")
    assert _where(db, ada) == room(db, "isle", WORLD)
    assert db.execute("SELECT COUNT(*) FROM mud_vehicle_riders").fetchone()[0] == 0
    assert any("The Rowboat arrives." in body for body in _bodies(db, cara)) is False
    assert _where(db, bram) == dock


def test_directions_follow_what_you_ride(harbor):
    db, (ada, _bram, _cara) = harbor
    on_foot = act(db, ada, "/directions Isle Landing")
    assert "no way" in on_foot
    act(db, ada, "/board rowboat")
    afloat = act(db, ada, "/directions Isle Landing")
    assert "no way" not in afloat and "east" in afloat.casefold()
    act(db, ada, "/disembark")
    assert "no way" in act(db, ada, "/directions Isle Landing")


def test_horse_terrain_fuel_and_narrow_ways(harbor):
    db, (ada, bram, _cara) = harbor
    assert "The Horse is yours. It waits at Harbor Dock" in "\n".join(d.body for d in _grant(db, ada, "horse"))
    assert _grant(db, ada, "horse") == []
    assert db.execute("SELECT COUNT(*) FROM mud_vehicles WHERE owner_character_id IS NOT NULL").fetchone()[0] == 1
    with pytest.raises(mud.ActionError, match="belongs to someone else"):
        act(db, bram, "/board horse")
    act(db, ada, "/board horse")
    assert _where(db, ada) == room(db, "square", WORLD)
    assert "You ride the Horse north." in act(db, ada, "/go north")
    assert _where(db, ada) == _cell(1, -1) == _vehicle(db, "horse")["room_id"]
    with pytest.raises(mud.ActionError, match="cannot travel by water"):
        act(db, ada, "/go south")
    act(db, ada, "/go east")
    with pytest.raises(mud.ActionError, match="cannot cross forest"):
        act(db, ada, "/go east")
    act(db, ada, "/go west")
    act(db, ada, "/go west")
    act(db, ada, "/go west")
    assert _where(db, ada) == room(db, "street", WORLD)
    assert _vehicle(db, "horse")["fuel"] == 0
    with pytest.raises(mud.ActionError, match="out of fuel"):
        act(db, ada, "/go dock")
    _coins(db, ada, 3)
    with pytest.raises(mud.ActionError, match="costs 10 coins"):
        act(db, ada, "/refuel")
    assert "You pay 2 coins to refuel the Horse (fuel 1/5)." in act(db, ada, "/refuel 1")
    mud._grant_item(db, ada, installed(db, "mud_item_definitions", "oats", WORLD))
    assert "Sack of Oats to refuel the Horse (fuel 4/5)" in act(db, ada, "/refuel item")
    with pytest.raises(mud.ActionError, match="too narrow"):
        act(db, ada, "/go north")
    assert "[on foot]" in act(db, ada, "/look")
    act(db, ada, "/dismount")
    act(db, ada, "/go north")
    assert _where(db, ada) == room(db, "alley", WORLD)
    assert _vehicle(db, "horse")["room_id"] == room(db, "street", WORLD)


def test_moving_any_other_way_leaves_the_vehicle(harbor):
    db, (ada, _bram, _cara) = harbor
    _grant(db, ada, "horse")
    act(db, ada, "/board horse")
    act(db, ada, "/go street")
    drafts = mud._trigger_teleport(db, user(db, ada), room(db, "square", WORLD))
    assert "You leave the Horse behind." in [draft.body for draft in drafts]
    assert mud._ride(db, ada) is None
    assert _vehicle(db, "horse")["room_id"] == room(db, "street", WORLD)
    act(db, ada, "/go street")
    assert mud._ride(db, ada) is None
    assert "Horse (yours, fuel 4/5)" in act(db, ada, "/look")


def test_travel_aboard_uses_vehicle_speed_and_survives_the_interior(harbor):
    db, (ada, _bram, _cara) = harbor
    _grant(db, ada, "horse")
    act(db, ada, "/board horse")
    act(db, ada, "/go north")
    mud._travel_action(db, user(db, ada), {"arguments": ["east"]})
    due = datetime.fromisoformat(db.execute("SELECT next_due FROM mud_travel").fetchone()[0])
    assert due - datetime.now(timezone.utc) <= timedelta(seconds=3)
    mud.process_travel(db, due)
    assert _where(db, ada) == _cell(2, -1)
    # The forest beyond is no ground for the horse, so the journey ends.
    assert mud.travel_state(db, ada) is None
    act(db, ada, "/go west")
    act(db, ada, "/go west")
    act(db, ada, "/dismount")
    act(db, ada, "/board rowboat")
    act(db, ada, "/go east")
    mud._travel_action(db, user(db, ada), {"arguments": ["east"]})
    for _ in range(3):
        due = db.execute("SELECT next_due FROM mud_travel").fetchone()
        if due is None:
            break
        mud.process_travel(db, datetime.fromisoformat(due[0]))
        db.execute("UPDATE mud_presence SET last_seen=?", (mud._scheduled_now(),))
    assert _vehicle(db, "rowboat")["room_id"] == _cell(3, 0)
    assert _where(db, ada).startswith("vehicle-room:")


def test_public_controls_pass_and_passengers_do_not_steer(harbor):
    db, (ada, bram, cara) = harbor
    act(db, ada, "/board rowboat")
    assert "You board the Rowboat." == act(db, bram, "/board rowboat").splitlines()[0]
    with pytest.raises(mud.ActionError, match="Only the operator"):
        act(db, bram, "/go east")
    act(db, ada, "/go east")
    with pytest.raises(mud.ActionError, match="only water around you"):
        act(db, ada, "/disembark")
    # A passenger stepping off at sea is brought ashore; the boat stays out.
    assert "You are brought ashore at Harbor Dock." in act(db, bram, "/disembark")
    assert _where(db, bram) == room(db, "square", WORLD)
    act(db, ada, "/go west")
    act(db, bram, "/board rowboat")
    act(db, ada, "/disembark")
    assert db.execute("SELECT role FROM mud_vehicle_riders").fetchone()["role"] == "operator"
    assert any("You take the controls of the Rowboat." in body for body in _bodies(db, bram))
    # A carted rider rides alone.
    place(db, ada, room(db, "street", WORLD))
    place(db, cara, room(db, "street", WORLD))
    act(db, ada, "/board Hand Cart")
    with pytest.raises(mud.ActionError, match="already riding"):
        act(db, cara, "/board Hand Cart")


def test_absent_operators_hand_over(harbor):
    db, (ada, bram, _cara) = harbor
    act(db, ada, "/board rowboat")
    act(db, bram, "/board rowboat")
    db.execute("UPDATE mud_presence SET last_seen='2000-01-01T00:00:00+00:00' WHERE user_id=?", (ada,))
    act(db, bram, "/go east")
    assert db.execute("SELECT role FROM mud_vehicle_riders WHERE character_id=?",
                      (mud._character_id(db, bram),)).fetchone()["role"] == "operator"


def test_shared_coach_takes_passengers_only_with_its_owner(harbor):
    db, (ada, bram, _cara) = harbor
    _grant(db, ada, "coach")
    with pytest.raises(mud.ActionError, match="only while its owner is aboard"):
        act(db, bram, "/board coach")
    act(db, ada, "/board coach")
    act(db, bram, "/board coach")
    with pytest.raises(mud.ActionError, match="Only the operator"):
        act(db, bram, "/go street")
    act(db, ada, "/go street")
    act(db, ada, "/disembark")
    assert _where(db, bram) == room(db, "street", WORLD)
    assert mud._ride(db, bram) is None
    act(db, ada, "/board coach")
    mud._grant_item(db, ada, installed(db, "mud_item_definitions", "nail", WORLD))
    with pytest.raises(mud.ActionError, match="Nothing left loose"):
        act(db, ada, "/drop Iron Nail")


def test_revoking_and_deleting_unload_riders(harbor):
    db, (ada, bram, _cara) = harbor
    _grant(db, ada, "coach")
    act(db, ada, "/board coach")
    act(db, bram, "/board coach")
    mud.character_leaving_vehicles(db, mud._character_id(db, ada))
    assert _vehicle(db, "coach") is None
    assert _where(db, bram) == room(db, "square", WORLD) == _where(db, ada)
    assert db.execute("SELECT COUNT(*) FROM mud_world_rooms WHERE kind='vehicle' AND vehicle_id NOT IN "
                      "(SELECT id FROM mud_vehicles)").fetchone()[0] == 0
    _grant(db, ada, "horse")
    assert "no longer yours" in "\n".join(d.body for d in mud._scripted_vehicle(
        db, user(db, ada), "revoke_vehicle", installed(db, "mud_vehicle_definitions", "horse", WORLD)))
    assert _vehicle(db, "horse") is None


def test_conditions_see_vehicles(harbor):
    db, (ada, _bram, _cara) = harbor
    horse = installed(db, "mud_vehicle_definitions", "horse", WORLD)
    context = mud._ConditionContext(db, ada)
    assert not context.has_vehicle(horse) and not context.aboard(None, None)
    _grant(db, ada, "horse")
    act(db, ada, "/board horse")
    context = mud._ConditionContext(db, ada)
    assert context.has_vehicle(horse) and context.aboard(horse, None) and not context.aboard(None, "water")


def test_stranded_and_unattended_vehicles_go_home(harbor):
    db, (ada, _bram, _cara) = harbor
    act(db, ada, "/board rowboat")
    act(db, ada, "/go east")
    db.execute("UPDATE mud_vehicles SET fuel=0")
    assert "towed back to Harbor Dock" in act(db, ada, "/disembark")
    assert _where(db, ada) == room(db, "square", WORLD)
    act(db, ada, "/board rowboat")
    db.execute("UPDATE mud_vehicles SET fuel=4")
    act(db, ada, "/go east")
    act(db, ada, "/go west")
    db.execute("UPDATE mud_vehicles SET room_id=? WHERE id=?", (room(db, "street", WORLD), _vehicle(db, "rowboat")["id"]))
    db.execute("DELETE FROM mud_vehicle_riders")
    mud._settle_operator(db, _vehicle(db, "rowboat")["id"])
    due = datetime.fromisoformat(_vehicle(db, "rowboat")["return_due"])
    mud.process_transport(db, due - timedelta(seconds=5))
    assert _vehicle(db, "rowboat")["room_id"] == room(db, "street", WORLD)
    mud.process_transport(db, due)
    boat = _vehicle(db, "rowboat")
    assert boat["room_id"] == room(db, "square", WORLD) and boat["fuel"] == 4 and boat["return_due"] is None
    # A vehicle whose room is gone waits at its home.
    db.execute("UPDATE mud_vehicles SET room_id=NULL")
    assert mud._vehicle(db, boat["id"])["room_id"] == room(db, "square", WORLD)


def test_switching_transport_off(harbor):
    db, (ada, _bram, _cara) = harbor
    act(db, ada, "/board rowboat")
    set_society(db, transport={"enabled": False})
    assert "board" in mud._hidden_actions(db, ada)
    act(db, ada, "/disembark")
    with pytest.raises(mud.ActionError, match="not running"):
        act(db, ada, "/board rowboat")
    with pytest.raises(mud.ActionError, match="not running"):
        act(db, ada, "/ride Isle Landing")


# Transit --------------------------------------------------------------------

def test_riding_a_line_with_a_cabin(harbor):
    db, (ada, bram, _cara) = harbor
    listing = act(db, ada, "/ride")
    assert "Coast Ferry: Isle Landing (2m, 7 coins)" in listing
    _coins(db, ada, 6)
    with pytest.raises(mud.ActionError, match="costs 7 coins"):
        act(db, ada, "/ride Isle Landing")
    _coins(db, ada, 10)
    text = act(db, ada, "/ride Isle")
    assert "You pay 7 coins and take the Coast Ferry to Isle Landing" in text and "Ferry Cabin" in text
    assert _coins(db, ada) == 3 and _where(db, ada) == room(db, "cabin", WORLD)
    with pytest.raises(mud.ActionError, match="under way"):
        act(db, ada, "/ride cancel")
    due = datetime.fromisoformat(db.execute("SELECT due_at FROM mud_transit_trips").fetchone()[0])
    mud.process_transport(db, due - timedelta(seconds=1))
    assert _where(db, ada) == room(db, "cabin", WORLD)
    mud.process_transport(db, due)
    assert _where(db, ada) == room(db, "isle", WORLD)
    assert any("The Coast Ferry arrives at Isle Landing." in body for body in _bodies(db, ada))
    assert db.execute("SELECT COUNT(*) FROM mud_transit_trips").fetchone()[0] == 0


def test_waiting_without_a_cabin_and_operating_hours(harbor, monkeypatch):
    db, (ada, _bram, _cara) = harbor
    place(db, ada, room(db, "street", WORLD))
    with pytest.raises(mud.ActionError, match="runs after dark"):
        act(db, ada, "/ride Harbor Dock")
    monkeypatch.setattr(mud._ConditionContext, "game_hour", lambda self: 22)
    assert "take the Night Coach to Harbor Dock" in act(db, ada, "/ride Night Coach to Harbor Dock")
    assert "waiting for the Night Coach" in act(db, ada, "/look")
    act(db, ada, "/go north")
    assert db.execute("SELECT COUNT(*) FROM mud_transit_trips").fetchone()[0] == 0
    act(db, ada, "/go south")
    act(db, ada, "/ride Harbor Dock")
    assert "stop waiting" in act(db, ada, "/ride cancel")


def test_replayed_ride_pays_once():
    with running(_document(), users=("ada",)) as (app, (ada,)):
        with app.test_request_context(url("/actions"), method="POST"):
            db = get_db()
            place(db, ada, room(db, "square", WORLD))
            _coins(db, ada, 20)
            db.commit()
            session["connection_id"] = "connection-ada"
            definition, parameters = mud.actions.from_command("/ride Isle Landing")
            request = mud.ActionRequest(definition, parameters, request_id="ride-once-please")
            first = mud.service.execute(request, user(db, ada))
            second = mud.service.execute(request, user(db, ada))
            assert first[1] == second[1]
            assert _coins(db, ada) == 13


def test_stuck_riders_are_freed(harbor):
    db, (ada, _bram, _cara) = harbor
    place(db, ada, room(db, "cabin", WORLD))
    mud.process_transport(db)
    assert _where(db, ada) == room(db, "square", WORLD)


# Packages and builder --------------------------------------------------------

def test_export_and_upgrade_keep_vehicles(harbor):
    db, (ada, bram, _cara) = harbor
    _grant(db, ada, "horse")
    act(db, ada, "/board horse")
    act(db, ada, "/go north")
    act(db, bram, "/board rowboat")
    db.commit()
    exported = mud_worlds.read_package(mud_worlds.export(db, WORLD))
    content = exported["content"]
    rowboat = next(row for row in content["mud_vehicle_definitions"] if row["id"] == "rowboat")
    assert rowboat == {"id": "rowboat", "name": "Rowboat", "description": "A small boat.", "modes": "water",
                       "terrains": "water", "mode_only": 1, "interior_room_id": "deck", "fuel_capacity": 4,
                       "return_seconds": 60}
    assert {row["id"] for row in content["mud_transit_stops"]} == {"ferry-dock", "ferry-isle", "coach-street",
                                                                   "coach-dock"}
    raw_exits = mud_worlds.export_document(db, WORLD)[0]["exits"]
    assert next(item for item in raw_exits if item["name"] == "landing")["requires_mode"] == "water"
    assert next(item for item in raw_exits if item["name"] == "alley")["allow_vehicles"] is False
    street = next(item for item in raw_exits if item["name"] == "street")
    assert "requires_mode" not in street and "allow_vehicles" not in street
    payload = mud_worlds.package_bytes({**_document(), "name": "Harbor Two"})
    mud_worlds.upgrade(db, payload, mud_worlds.package_hash(payload))
    horse = _vehicle(db, "horse")
    assert horse is not None and horse["owner_character_id"] == mud._character_id(db, ada)
    assert horse["room_id"] == _cell(1, -1) and horse["fuel"] == 4
    assert _where(db, bram) == room(db, "square", WORLD)
    assert len(db.execute("SELECT * FROM mud_vehicles WHERE owner_character_id IS NULL").fetchall()) == 2
    plain = copy.deepcopy(_document())
    plain.update(id="plainharbor", name="Plain Harbor")
    for table in [table for table in plain["content"] if "vehicle" in table or "transit" in table or "fuel" in table]:
        del plain["content"][table]
    mud_worlds.install(db, mud_worlds.package_bytes(plain))
    content = mud_worlds.read_package(mud_worlds.export(db, "plainharbor"))["content"]
    assert not [table for table in content if "vehicle" in table or "transit" in table]


def test_builder_manages_vehicles_lines_and_exits():
    with running(_document(), users=("ada", "bram")) as (app, (ada, bram)):
        web = client(app, ada)
        page = web.get(f"/mud/w/{WORLD}/builder")
        assert page.status_code == 200 and b"Vehicles and transit" in page.data and b"Rowboat" in page.data
        with app.app_context():
            db = get_db()
            street, dock, alley = room(db, "street", WORLD), room(db, "square", WORLD), room(db, "alley", WORLD)
            deck, isle = room(db, "deck", WORLD), room(db, "isle", WORLD)
            rowboat = installed(db, "mud_vehicle_definitions", "rowboat", WORLD)
            ferry = installed(db, "mud_transit_lines", "ferry", WORLD)
        base = f"/mud/w/{WORLD}/builder"
        form = {"csrf_token": "test-token", "name": "Pony", "description": "Shaggy.", "modes": "", "speed": "150",
                "fuel_capacity": "0", "fuel_per_move": "1", "fuel_per_item": "10", "return_seconds": "0",
                "conditions_json": "[]", "terrains": ["grassland", "road"]}
        assert web.post(f"{base}/vehicles", data=form).status_code == 302
        # An interior must be a room of its own with no exits.
        web.post(f"{base}/vehicles", data={**form, "name": "Barge", "interior_room_id": street})
        web.post(f"{base}/vehicles", data={**form, "name": "Second Boat", "interior_room_id": deck})
        with app.app_context():
            db = get_db()
            pony = db.execute("SELECT * FROM mud_vehicle_definitions WHERE name='Pony'").fetchone()
            assert pony["terrains"] == "grassland,road" and pony["speed"] == 150 and pony["world_id"] == WORLD
            assert db.execute("SELECT COUNT(*) FROM mud_vehicle_definitions WHERE name IN ('Barge','Second Boat')"
                              ).fetchone()[0] == 0
        assert web.post(f"{base}/vehicle-placements", data={"csrf_token": "test-token",
                                                            "vehicle_definition_id": pony["id"],
                                                            "room_id": street}).status_code == 302
        with app.app_context():
            db = get_db()
            assert db.execute("SELECT room_id,home_room_id FROM mud_vehicles WHERE definition_id=?",
                              (pony["id"],)).fetchone()["room_id"] == street
            exit_id = db.execute("SELECT id FROM mud_world_exits WHERE from_room_id=? AND to_room_id=?",
                                 (street, alley)).fetchone()["id"]
        exit_form = {"csrf_token": "test-token", "from_room_id": street, "to_room_id": alley, "name": "alley",
                     "direction": "north", "conditions_json": "[]", "requires_mode": "rail"}
        assert web.post(f"{base}/exits/{exit_id}", data=exit_form).status_code == 302
        with app.app_context():
            row = get_db().execute("SELECT requires_mode,allow_vehicles FROM mud_world_exits WHERE id=?",
                                   (exit_id,)).fetchone()
            assert (row["requires_mode"], row["allow_vehicles"]) == ("rail", 1)
        web.post(f"{base}/exits/{exit_id}", data={**exit_form, "requires_mode": "", "on_foot": "1"})
        web.post(f"{base}/exits", data={**exit_form, "from_room_id": deck, "to_room_id": street, "name": "ladder",
                                        "direction": "up", "requires_mode": ""})
        with app.app_context():
            db = get_db()
            assert db.execute("SELECT allow_vehicles FROM mud_world_exits WHERE id=?", (exit_id,)).fetchone()[0] == 0
            assert db.execute("SELECT 1 FROM mud_world_exits WHERE from_room_id=?", (deck,)).fetchone() is None
            # A rider on the ferry when its line is removed arrives at once.
            place(db, bram, dock)
            mud._wallet(db, mud._character_id(db, bram))
            db.execute("UPDATE mud_character_wallets SET coins=50 WHERE character_id=?", (mud._character_id(db, bram),))
            act(db, bram, "/ride Isle Landing")
            db.commit()
        line_form = {"csrf_token": "test-token", "name": "Street Tram", "fare": "1", "fare_per_stop": "0",
                     "conditions_json": "[]", "stop_room_id": [street, dock, ""], "stop_seconds": ["60", "20", "60"]}
        assert web.post(f"{base}/transit-lines", data=line_form).status_code == 302
        web.post(f"{base}/transit-lines", data={**line_form, "name": "Lonely Tram", "stop_room_id": [street]})
        web.post(f"{base}/fuel-stations", data={"csrf_token": "test-token", "room_id": dock, "price": "3",
                                                "modes": "water"})
        web.post(f"{base}/transit-lines/{ferry}/delete", data={"csrf_token": "test-token", "confirmation": "delete"})
        web.post(f"{base}/society/transport", data={"csrf_token": "test-token"})
        with app.app_context():
            db = get_db()
            tram = db.execute("SELECT id FROM mud_transit_lines WHERE name='Street Tram'").fetchone()
            assert [row["travel_seconds"] for row in mud._line_stops(db, tram["id"])] == [60, 20]
            assert db.execute("SELECT 1 FROM mud_transit_lines WHERE name='Lonely Tram'").fetchone() is None
            assert db.execute("SELECT price,modes FROM mud_fuel_stations WHERE room_id=?",
                              (dock,)).fetchone()["modes"] == "water"
            assert mud._current_room(db, bram)["id"] == isle
            assert mud.society_settings(db)["transport"]["enabled"] is False
        assert web.post(f"{base}/vehicles/{rowboat}/delete",
                        data={"csrf_token": "test-token", "confirmation": "delete"}).status_code == 302
        with app.app_context():
            assert get_db().execute("SELECT COUNT(*) FROM mud_vehicles WHERE definition_id=?",
                                    (rowboat,)).fetchone()[0] == 0
