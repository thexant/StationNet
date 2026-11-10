"""Turning a World Forge draft into the live world.

A new world is installed hidden until a SYSOP enables it. An installed world is
updated in place: field-only edits are applied directly to the existing rows (a
"light" publish keeps items players dropped, NPC positions, and dungeon runs),
and anything structural runs the package upgrade, which keeps characters,
quests, reputation, and possessions by package-local ID.
"""
from __future__ import annotations

import contextlib
import json

from .. import mud, mud_progression, mud_scope, mud_worlds
from .storage import archive
from .world import WorldError

ADMIN_FIELDS = ("progression", "start_room")


def _scope(world_id):
    return mud_worlds.home_publish() if world_id == mud_scope.HOME else contextlib.nullcontext()


def base_state(database, world_id):
    """What the published world looked like when a draft was seeded or last published."""
    package = database.execute("SELECT package_hash FROM mud_world_packages WHERE id=?", (world_id,)).fetchone()
    upgrade = database.execute("SELECT MAX(rowid) FROM mud_world_upgrades WHERE world_id=?", (world_id,)).fetchone()
    return {"base_hash": package["package_hash"] if package else None, "base_upgrade": upgrade[0] if upgrade else None}


def seed_files(database, world_id):
    """Draft files for an installed world, from its own export (the home world keeps its ID)."""
    from .storage import unpack
    document, report = mud_worlds.export_document(database, world_id, keep_id=True)
    files = unpack(mud_worlds.package_bytes(document))
    return files, report.as_json()


def _manifest(files):
    return files.get("manifest.json") if isinstance(files.get("manifest.json"), dict) else {}


def admin_changes(database, session):
    """Settings only a SYSOP may change, in a draft that changes them."""
    if session.kind != "existing":
        return []
    try:
        current, _report = mud_worlds.export_document(database, session.world_id, keep_id=True)
    except mud_worlds.WorldError:
        return []
    draft = _manifest(session.files)
    published = {"progression": mud_progression.changed(current.get("progression") or {}),
                 "start_room": current.get("start_room")}
    changed = []
    for name in ADMIN_FIELDS:
        value = draft.get(name)
        if name == "progression":
            try:
                value = mud_progression.changed(mud_progression.validate(value))
            except (ValueError, TypeError):
                pass
        if value != published[name]:
            changed.append(name)
    return changed


def _impact(database, world_id):
    """Live state a full upgrade resets, for the publish dialog."""
    with mud_scope.use(world_id):
        rooms = "(SELECT id FROM mud_world_rooms WHERE world_id=?)"
        return {
            "room_items": database.execute(f"SELECT COUNT(*) FROM mud_item_instances WHERE room_id IN {rooms}",
                                           (world_id,)).fetchone()[0],
            "dungeon_runs": database.execute(
                "SELECT COUNT(*) FROM mud_procedural_instances WHERE world_id=? AND status='active'",
                (world_id,)).fetchone()[0],
            "present": database.execute(
                f"SELECT COUNT(*) FROM mud_player_locations WHERE location_type='world_room' AND location_key IN {rooms}",
                (world_id,)).fetchone()[0],
            "holdings": database.execute("SELECT COUNT(*) FROM mud_property_holdings WHERE world_id=?",
                                         (world_id,)).fetchone()[0],
            "vehicles": database.execute("SELECT COUNT(*) FROM mud_vehicles WHERE world_id=?",
                                         (world_id,)).fetchone()[0],
        }


def stale(database, session):
    if session.kind != "existing":
        return False
    now = base_state(database, session.world_id)
    return (now["base_hash"] != session.meta.get("base_hash")
            or now["base_upgrade"] != session.meta.get("base_upgrade"))


def preview(database, session, *, is_admin):
    with session.lock:
        files, revision = session.files, session.revision
    try:
        payload = archive(files)
    except WorldError as problem:
        return {"ok": False, "error": str(problem), "problems": [], "revision": revision}
    result = {"ok": True, "revision": revision, "hash": mud_worlds.package_hash(payload), "kind": session.kind,
              "world": session.world_id, "name": _manifest(files).get("name", session.world_id)}
    try:
        if session.kind == "new":
            if database.execute("SELECT 1 FROM mud_world_packages WHERE id=?", (session.world_id,)).fetchone():
                raise mud_worlds.WorldError("A world with this ID was installed since this draft was started; "
                                            "duplicate the draft under a new ID.")
            _document, report = mud_worlds.validate_install(database, payload)
            result.update(mode="install", problems=report.as_json())
        else:
            with _scope(session.world_id):
                upgrade = mud_worlds.upgrade_preview(database, payload)
                light = mud_worlds.light_upgrade_plan(database, payload)
            result.update(mode="light" if light is not None else "full", problems=upgrade["problems"],
                          changes=upgrade["changes"], characters=upgrade["characters"],
                          moved_to_start=upgrade["moved_to_start"], enabled=upgrade["enabled"],
                          edits=len(light["edits"]) if light else None,
                          impact=None if light is not None else _impact(database, session.world_id),
                          stale=stale(database, session), home=session.world_id == mud_scope.HOME,
                          first_home_publish=session.world_id == mud_scope.HOME and not database.execute(
                              "SELECT 1 FROM mud_world_upgrades WHERE world_id=?", (mud_scope.HOME,)).fetchone())
            needs_admin = admin_changes(database, session)
            result["requires_admin"] = needs_admin
            result["allowed"] = is_admin or not needs_admin
    except (mud_worlds.WorldError, ValueError, KeyError, TypeError) as problem:
        report = getattr(problem, "report", None)
        result.update(ok=False, error=str(problem), problems=report.as_json() if report else [])
    finally:
        database.rollback()
    result["blocked"] = (not result["ok"] or any(problem.get("severity") == "error"
                                                 for problem in result.get("problems", []))
                         or result.get("allowed") is False)
    return result


def publish(database, store, session, *, revision, expected_hash, is_admin, reset_world_state=False,
            announce=True, accept_stale=False):
    with session.lock:
        store.check_revision(session, revision)
        payload = archive(session.files)
        if mud_worlds.package_hash(payload) != expected_hash:
            raise WorldError("The draft changed since it was previewed; preview it again.")
        if session.kind == "existing":
            if stale(database, session) and not accept_stale:
                raise WorldError("The published world changed since this draft was started (for example by a ZIP "
                                 "upgrade in Settings). Rebase the draft, or confirm publishing anyway.")
            if not is_admin and admin_changes(database, session):
                raise WorldError("Only a SYSOP can publish changes to progression or the starting room.")
        session.publishing = True
        try:
            store.backup(session, "before publish")
            if session.kind == "new":
                mud_worlds.install(database, payload, enable=False)
                summary = {"installed": True, "enabled": False}
            else:
                with _scope(session.world_id):
                    light = None if reset_world_state else mud_worlds.light_upgrade(database, payload, expected_hash)
                    if light is None:
                        _document, summary = mud_worlds.upgrade(database, payload, expected_hash,
                                                                reset_world_state=bool(reset_world_state))
                    else:
                        _document, summary = light
                notices(database, session.world_id, summary, announce=announce,
                        name=_manifest(session.files).get("name", session.world_id))
            store.published(session, revision=session.revision, base=base_state(database, session.world_id))
        finally:
            session.publishing = False
        return {"summary": json.loads(json.dumps(summary, default=sorted)), "draft": session.payload()}


def notices(database, world_id, summary, *, announce, name):
    """Tell the people a publish affected, as the old builder did after each edit."""
    from ..mud import EventDraft, _insert_event
    with mud_scope.use(world_id):
        start = mud._start_room(database, world_id)
        moved = set(summary.get("moved_users") or [])
        for user_id in moved:
            _insert_event(database, EventDraft(
                f"The place you stood is gone; you find yourself at {start['name']}.",
                audience_key=str(user_id), recipient_ids=(user_id,)))
        owners = summary.get("ended_holding_owners") or []
        if owners:
            marks = ",".join("?" * len(owners))
            for row in database.execute(f"SELECT user_id FROM mud_characters WHERE id IN ({marks})", owners):
                _insert_event(database, EventDraft("A world builder's changes ended your property claim; "
                                                   "anything you stored there is back in your pack.",
                                                   audience_key=str(row["user_id"]),
                                                   recipient_ids=(row["user_id"],)))
        if announce:
            present = set(summary.get("present_users") or []) - moved
            for room in summary.get("touched_rooms") or []:
                present |= set(mud._occupant_ids(database, room))
            if summary.get("light"):
                rooms = summary.get("touched_rooms") or []
                present = {user for room in rooms for user in mud._occupant_ids(database, room)}
            for user_id in present:
                _insert_event(database, EventDraft(f"The world shifts around you: {name} was updated.",
                                                   audience_key=str(user_id), recipient_ids=(user_id,)))
        database.commit()
