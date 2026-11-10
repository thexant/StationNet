"""World Forge: the station's built-in MUD world editor.

Builders edit a server-side draft of a world package (rooms, map, content,
scripts, wilderness) and publish it to the live world. See ``storage`` for
drafts, ``publish`` for going live, and ``live`` for actions on the running world.
"""
from __future__ import annotations

import copy
import io
import re
from collections import deque
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, g, jsonify, render_template, request, send_file

from .. import mud_scope, mud_worlds
from ..auth import admin_required
from ..db import get_db
from ..mud import builder_required
from ..security import validate_csrf
from . import catalog as station_catalog, live, ops, publish as publishing
from .connections import connection_graph
from .schema import HANDBOOK, registry
from .storage import MAX_ZIP, Conflict, Store, archive, key_for, unpack
from .world import WorldError, WorldIndex, position, records, uses, mutate as editor_mutate, validate

bp = Blueprint("world_forge", __name__, url_prefix="/mud/forge")
VERSION = "2.0.0"
FILE_COLLECTIONS = {"manifest.json": "manifest", "rooms.json": "rooms", "exits.json": "exits"}


def store():
    app = current_app._get_current_object()
    found = app.extensions.get("world_forge")
    if found is None:
        found = app.extensions["world_forge"] = Store(Path(app.config["DATA_DIR"]) / "mud_worlds" / "forge")
    return found


def _authored(database):
    return [world for world in mud_worlds.worlds(database, include_disabled=True)
            if world["key"] != mud_scope.PROCEDURAL]


def _installed(database):
    return {world["key"] for world in _authored(database)}


def _json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise WorldError("Expected a JSON object.")
    return data


def _post():
    validate_csrf()
    return _json() if request.mimetype == "application/json" else {}


def _known(database):
    return station_catalog.known(station_catalog.build(database))


def _session(key):
    session = store().get(key)
    store().touch(session, g.user["id"], g.user["username"])
    return session


def _scoped(session):
    return mud_scope.use(session.world_id) if session.kind == "existing" else mud_scope.use(mud_scope.HOME)


@bp.errorhandler(WorldError)
def _world_error(error):
    return jsonify(detail=str(error)), 409 if isinstance(error, Conflict) else 400


@bp.errorhandler(mud_worlds.WorldError)
def _package_error(error):
    report = getattr(error, "report", None)
    return jsonify(detail=str(error), problems=report.as_json() if report else []), 400


@bp.errorhandler(IndexError)
def _index_error(_error):
    return jsonify(detail="Record no longer exists. Refresh the editor."), 409


@bp.errorhandler(KeyError)
def _key_error(error):
    return jsonify(detail=f"Required field or record is missing: {error}"), 400


@bp.errorhandler(OSError)
def _io_error(error):
    return jsonify(detail=f"File operation failed: {error}"), 400


# -- the page -------------------------------------------------------------------------------
@bp.get("/")
@builder_required
def index():
    database = get_db()
    world = request.args.get("world", "")
    if world and world not in _installed(database):
        world = ""
    return render_template("world_forge/index.html", initial_world=world, is_admin=bool(g.user["is_admin"]))


# -- station-wide -----------------------------------------------------------------------------
@bp.get("/api/session")
@builder_required
def api_session():
    return jsonify(user=g.user["username"], admin=bool(g.user["is_admin"]), version=VERSION)


@bp.get("/api/schema")
@builder_required
def api_schema():
    return jsonify(registry())


@bp.get("/api/catalog")
@builder_required
def api_catalog():
    return jsonify(station_catalog.build(get_db()))


@bp.get("/api/handbook")
@builder_required
def api_handbook():
    return Response(HANDBOOK.read_text(encoding="utf-8"), mimetype="text/markdown")


@bp.get("/api/worlds")
@builder_required
def api_worlds():
    database = get_db()
    worlds = []
    installed = _installed(database)
    # A world removed in Settings leaves its draft behind; forget it.
    folder = store().root / "worlds"
    for path in folder.iterdir() if folder.is_dir() else []:
        if path.is_dir() and path.name not in installed:
            store().remove_world(path.name)
    for world in _authored(database):
        worlds.append({**world, "home": world["key"] == mud_scope.HOME,
                       "draftKey": key_for("existing", world["key"]),
                       "draft": store().draft_status(world["key"])})
    return jsonify(worlds=worlds, drafts=store().new_drafts())


@bp.post("/api/worlds/new")
@builder_required
def api_new_world():
    data = _post()
    session = store().create_new(str(data.get("id", "")).strip(), data.get("name", ""), data.get("author", ""),
                                 _installed(get_db()))
    return jsonify(session.payload(viewer=g.user["id"]))


@bp.post("/api/worlds/import")
@builder_required
def api_import_world():
    validate_csrf()
    raw = request.get_data(cache=False)
    if len(raw) > MAX_ZIP:
        raise WorldError("World ZIP exceeds 16 MB.")
    files = unpack(raw)
    wanted = request.args.get("id", "").strip()
    if wanted:
        files["manifest.json"]["id"] = wanted
    session = store().adopt_new(files, _installed(get_db()))
    return jsonify(session.payload(viewer=g.user["id"]))


@bp.post("/api/open")
@builder_required
def api_open():
    """Open (seeding if needed) the shared draft of an installed world."""
    data = _post()
    database = get_db()
    world_id = str(data.get("world", ""))
    if world_id not in _installed(database):
        raise WorldError("That world is not installed.")
    key = key_for("existing", world_id)
    seeded = []
    if not store().exists("existing", world_id):
        with mud_scope.use(world_id):
            files, seeded = publishing.seed_files(database, world_id)
            store().seed(world_id, files, publishing.base_state(database, world_id))
    session = _session(key)
    result = session.payload(viewer=g.user["id"])
    result["seedWarnings"] = seeded
    result["stale"] = publishing.stale(database, session)
    return jsonify(result)


@bp.get("/api/builders")
@admin_required
def api_builders():
    return jsonify(live.builders(get_db()))


@bp.post("/api/builders/<int:user_id>")
@admin_required
def api_set_builder(user_id):
    data = _post()
    return jsonify(message=live.set_builder(get_db(), user_id, bool(data.get("enabled")), g.user["id"]),
                   builders=live.builders(get_db()))


# -- one draft --------------------------------------------------------------------------------
@bp.get("/api/d/<key>")
@builder_required
def api_draft(key):
    session = _session(key)
    result = session.payload(viewer=g.user["id"])
    if session.kind == "existing":
        result["stale"] = publishing.stale(get_db(), session)
    return jsonify(result)


@bp.post("/api/d/<key>/transaction")
@builder_required
def api_transaction(key):
    data = _post()
    _session(key)
    known = _known(get_db()) if data.get("operation") == "import_presets" else None
    result = store().transaction(key, data["revision"], data["operation"], data.get("args") or {},
                                 lambda files, operation, args: ops.mutate(files, operation, args, known))
    return jsonify(result)


@bp.post("/api/d/<key>/checkpoint")
@builder_required
def api_checkpoint(key):
    data = _post()
    _session(key)
    return jsonify(store().checkpoint(key, data["revision"]))


@bp.get("/api/d/<key>/backups")
@builder_required
def api_backups(key):
    _session(key)
    return jsonify(store().backups(key))


@bp.post("/api/d/<key>/restore")
@builder_required
def api_restore(key):
    data = _post()
    _session(key)
    return jsonify(store().restore(key, data["revision"], str(data.get("backup", ""))))


@bp.post("/api/d/<key>/rebase")
@builder_required
def api_rebase(key):
    """Throw the draft away (after a backup) and start again from the published world."""
    data = _post()
    session = _session(key)
    if session.kind != "existing":
        raise WorldError("Only a draft of an installed world can be rebased.")
    database = get_db()
    with mud_scope.use(session.world_id):
        files, warnings = publishing.seed_files(database, session.world_id)
    result = store().replace(key, data["revision"], files, backup_reason="before rebase")
    session.meta.update(publishing.base_state(database, session.world_id))
    session.meta["published_revision"] = session.revision
    store()._write_meta(session)
    result = session.payload(viewer=g.user["id"])
    result["seedWarnings"] = warnings
    return jsonify(result)


@bp.post("/api/d/<key>/discard")
@builder_required
def api_discard(key):
    data = _post()
    _session(key)
    store().discard(key, data["revision"])
    return jsonify(ok=True)


@bp.post("/api/d/<key>/duplicate")
@builder_required
def api_duplicate(key):
    data = _post()
    session = _session(key)
    files = copy.deepcopy(session.files)
    files["manifest.json"]["id"] = str(data.get("world_id", "")).strip()
    if data.get("name"):
        files["manifest.json"]["name"] = str(data["name"]).strip()
    created = store().adopt_new(files, _installed(get_db()))
    return jsonify(created.payload(viewer=g.user["id"]))


def _engine_issues(database, session):
    """Problems only the engine finds (a dry-run install or upgrade), shaped like editor issues."""
    try:
        payload = archive(session.files)
    except WorldError as problem:
        return [{"severity": "error", "category": "invalid", "file": "", "path": "$", "message": str(problem),
                 "collection": "manifest", "index": 0, "field": None}]
    try:
        with _scoped(session):
            if session.kind == "new":
                _document, report = mud_worlds.validate_install(database, payload)
                problems = report.as_json()
            else:
                with publishing._scope(session.world_id):
                    _document, report, _incoming = mud_worlds.validate_upgrade(database, payload)
                problems = report.as_json()
    except mud_worlds.WorldError as problem:
        report = getattr(problem, "report", None)
        problems = report.as_json() if report else [{"severity": "error", "category": "invalid", "file": "",
                                                     "path": "", "message": str(problem)}]
    finally:
        database.rollback()
    issues = []
    for problem in problems:
        collection = FILE_COLLECTIONS.get(problem.get("file"), "manifest")
        found = re.match(r"^\$\[(\d+)\]", problem.get("path") or "")
        issues.append({**problem, "engine": True, "collection": collection,
                       "index": int(found.group(1)) if found and collection != "manifest" else 0, "field": None})
    return issues


@bp.post("/api/d/<key>/validate")
@builder_required
def api_validate(key):
    validate_csrf()
    session = _session(key)
    database = get_db()
    try:
        issues = validate(session.files, _known(database))
    except (TypeError, KeyError, AttributeError, ValueError) as problem:
        issues = [{"severity": "error", "category": "invalid", "file": "manifest.json", "path": "$",
                   "message": f"Malformed data prevents complete validation: {problem}", "collection": "manifest",
                   "index": 0, "field": None}]
    if not any(issue["severity"] == "error" for issue in issues):
        seen = {issue["message"] for issue in issues}
        issues += [issue for issue in _engine_issues(database, session) if issue["message"] not in seen]
    return jsonify(issues)


@bp.get("/api/d/<key>/uses")
@builder_required
def api_uses(key):
    return jsonify(uses(_session(key).files, request.args["collection"], request.args["value"]))


@bp.get("/api/d/<key>/connections")
@builder_required
def api_connections(key):
    session = _session(key)
    with session.lock:
        files, revision = copy.deepcopy(session.files), session.revision
    return jsonify({"revision": revision, **connection_graph(files)})


MARKER_COLLECTIONS = ("mud_npc_instances", "mud_item_instances", "mud_item_spawns", "mud_gathering_nodes",
                      "mud_room_discoveries", "mud_world_destinations", "mud_storage_points",
                      "mud_vehicle_placements", "mud_fuel_stations", "mud_transit_stops", "entrances")


@bp.get("/api/d/<key>/map")
@builder_required
def api_map(key):
    z, x, y = (request.args.get(name, default, type=int) for name, default in (("z", 0), ("x", -10), ("y", -7)))
    width, height = request.args.get("width", 25, type=int), request.args.get("height", 18, type=int)
    if width < 1 or height < 1 or width * height > 18000:
        raise WorldError("Map viewport is too large.")
    files = _session(key).files
    index = WorldIndex(files)
    cells, visible, markers = [], set(), {}
    for collection in MARKER_COLLECTIONS:
        for position_index, row in enumerate(records(files, collection)):
            room_id = row.get("room_id", row.get("room")) if isinstance(row, dict) else None
            if room_id:
                markers.setdefault(room_id, []).append({"collection": collection, "index": position_index})
    for row_y in range(y, y + height):
        for row_x in range(x, x + width):
            cell = index.at(row_x, row_y, z)
            if cell:
                # Long room prose stays out of map responses.
                cells.append({key_: value for key_, value in cell.items()
                              if key_ in ("id", "name", "x", "y", "z", "preset", "region", "terrain", "glyph",
                                          "grid_link", "blocked_dirs")})
                cells[-1]["markers"] = markers.get(cell["id"], [])
                visible.add(cell["id"])
    edges = []
    for room_id in visible:
        for edge in index.links(room_id):
            target = index.get(edge.get("to"))
            edges.append({**edge, "targetPosition": position(target) if target else None})
    return jsonify(cells=cells, edges=edges)


@bp.get("/api/d/<key>/inspect")
@builder_required
def api_inspect(key):
    files = _session(key).files
    index = WorldIndex(files)
    room = index.get(request.args.get("room", ""))
    if not room:
        raise WorldError("Room does not exist.")
    return jsonify(room=room, links=index.links(room["id"]), uses=uses(files, "rooms", room["id"]))


@bp.post("/api/d/<key>/placement-preview")
@builder_required
def api_placement_preview(key):
    data = _post()
    files = _session(key).files
    room = records(files, "rooms")[int(data["index"])]
    index = WorldIndex(files)
    after, _selection = editor_mutate(files, "place_room", data)
    next_index = WorldIndex(after)
    affected = {room["id"]}
    for point in (position(room), tuple(data[name] for name in ("x", "y", "z"))):
        if point:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for candidate in (index, next_index):
                        neighbor = candidate.at(point[0] + dx, point[1] + dy, point[2])
                        if neighbor:
                            affected.add(neighbor["id"])
    before = [edge for room_id in affected for edge in index.links(room_id)]
    after_edges = [edge for room_id in affected for edge in next_index.links(room_id)]

    def label(edge):
        return (f'{edge["from"]}: {edge.get("direction") or edge.get("name")} → {edge["to"]}'
                + (" (automatic)" if edge.get("automatic") else ""))

    old, new = {label(edge) for edge in before}, {label(edge) for edge in after_edges}
    return jsonify({"from": position(room), "to": [data[name] for name in ("x", "y", "z")],
                    "added": sorted(new - old), "removed": sorted(old - new)})


@bp.get("/api/d/<key>/route")
@builder_required
def api_route(key):
    index = WorldIndex(_session(key).files)
    start, end = request.args["start"], request.args["end"]
    queue, parents = deque([start]), {start: None}
    while queue and len(parents) < 20000:
        here = queue.popleft()
        if here == end:
            break
        for edge in index.links(here):
            if edge.get("hidden") or edge.get("key_item_id") or edge.get("conditions") or edge.get("requires_mode"):
                continue
            if edge["to"] not in parents:
                parents[edge["to"]] = here
                queue.append(edge["to"])
    if end not in parents:
        return jsonify(path=[], limited=len(parents) >= 20000)
    path, here = [], end
    while here is not None:
        path.append(here)
        here = parents[here]
    return jsonify(path=path[::-1], limited=False)


@bp.post("/api/d/<key>/dungeon-preview")
@builder_required
def api_dungeon_preview(key):
    """Generate one sample of a draft dungeon template, inside a rolled-back dry-run install."""
    import secrets
    import uuid
    from .. import dungeons, mud_world_dungeons
    data = _post()
    session = _session(key)
    template_key = str(data.get("template", ""))
    seed = str(data.get("seed") or "").strip()[:64] or secrets.token_hex(8)
    files = copy.deepcopy(session.files)
    temporary = f"forge-preview-{uuid.uuid4().hex[:12]}"
    files["manifest.json"]["id"] = temporary
    database = get_db()
    with mud_worlds.dry_run(database, "forge_dungeon_preview"):
        try:
            payload = archive(files)
            document = mud_worlds.read_package(payload, database)
            with mud_scope.use(mud_scope.HOME):
                mud_worlds._install_rows(database, document, payload)
            row = database.execute("SELECT * FROM mud_world_dungeon_templates WHERE world_id=? AND local_key=?",
                                   (temporary, template_key)).fetchone()
            if row is None:
                raise WorldError("Choose one of this draft's dungeon templates.")
            try:
                manifest = dungeons.generate_manifest(mud_world_dungeons.effective_template(row), seed,
                                                      database=database)
                text = dungeons.format_preview(manifest)
            except (dungeons.TemplateError, dungeons.GenerationError) as problem:
                text = f"Preview failed: {problem}"
        except mud_worlds.WorldError as problem:
            text = f"Preview failed: the draft does not install cleanly yet ({problem})."
    return jsonify(template=template_key, seed=seed, text=text)


@bp.get("/api/d/<key>/export")
@builder_required
def api_export_draft(key):
    session = _session(key)
    issues = validate(session.files, _known(get_db()))
    if any(issue["severity"] == "error" for issue in issues):
        raise WorldError("Resolve validation errors before exporting the draft.")
    return send_file(io.BytesIO(archive(session.files)), mimetype="application/zip", as_attachment=True,
                     download_name=f"{session.world_id}-draft.zip")


@bp.get("/api/d/<key>/presets/export")
@builder_required
def api_export_presets(key):
    import json
    session = _session(key)
    wanted = request.args.get("key")
    presets = [row for row in records(session.files, "presets") if isinstance(row, dict)
               and (not wanted or row.get("key") == wanted)]
    if wanted and not presets:
        abort(404)
    payload = json.dumps({"presets": presets}, ensure_ascii=False, indent=2).encode("utf-8")
    name = f"{wanted}-preset.json" if wanted else f"{session.world_id}-room-presets.json"
    return send_file(io.BytesIO(payload), mimetype="application/json", as_attachment=True, download_name=name)


@bp.post("/api/d/<key>/publish/preview")
@builder_required
def api_publish_preview(key):
    validate_csrf()
    session = _session(key)
    with _scoped(session):
        return jsonify(publishing.preview(get_db(), session, is_admin=bool(g.user["is_admin"])))


@bp.post("/api/d/<key>/publish")
@builder_required
def api_publish(key):
    data = _post()
    session = _session(key)
    database = get_db()
    with _scoped(session):
        result = publishing.publish(database, store(), session, revision=data["revision"],
                                    expected_hash=str(data.get("hash", "")), is_admin=bool(g.user["is_admin"]),
                                    reset_world_state=bool(data.get("reset_world_state")),
                                    announce=data.get("announce", True) is not False,
                                    accept_stale=bool(data.get("accept_stale")))
    return jsonify(result)


@bp.get("/api/d/<key>/history")
@builder_required
def api_history(key):
    import json
    session = _session(key)
    rows = get_db().execute("SELECT id AS rowid,from_hash,to_hash,summary_json,upgraded_at FROM mud_world_upgrades "
                            "WHERE world_id=? ORDER BY rowid DESC LIMIT 50", (session.world_id,)).fetchall()
    history = []
    for row in rows:
        try:
            summary = json.loads(row["summary_json"] or "{}")
        except ValueError:
            summary = {}
        history.append({"id": row["rowid"], "at": row["upgraded_at"], "light": bool(summary.get("light")),
                        "moved": summary.get("moved_to_start", 0), "carried": summary.get("carried", 0),
                        "notes": summary.get("notes", [])})
    return jsonify(history)


# -- the published world --------------------------------------------------------------------
def _published(world_id):
    database = get_db()
    if world_id not in _installed(database):
        abort(404)
    return database


@bp.get("/api/w/<world_id>/published/export")
@builder_required
def api_published_export(world_id):
    database = _published(world_id)
    with mud_scope.use(world_id):
        payload = mud_worlds.export(database, world_id, strict=request.args.get("strict") == "1")
    name = world_id if world_id != mud_scope.HOME else "my-world"
    return send_file(io.BytesIO(payload), mimetype="application/zip", as_attachment=True, download_name=f"{name}.zip")


@bp.get("/api/w/<world_id>/published/check")
@admin_required
def api_published_check(world_id):
    database = _published(world_id)
    with mud_scope.use(world_id):
        _document, report = mud_worlds.export_document(database, world_id)
    return jsonify(report.as_json())


@bp.get("/api/w/<world_id>/live")
@builder_required
def api_live(world_id):
    return jsonify(live.summary(_published(world_id), world_id))


def _confirmed(data, word):
    if data.get("confirm") != word:
        raise WorldError(f'Type "{word}" to confirm.')


@bp.post("/api/w/<world_id>/live/holdings/<path:holding_id>/release")
@builder_required
def api_release_holding(world_id, holding_id):
    data = _post()
    _confirmed(data, "release")
    message = live.release_holding(_published(world_id), world_id, holding_id)
    return jsonify(message=message, live=live.summary(get_db(), world_id))


@bp.post("/api/w/<world_id>/live/vehicles/<path:vehicle_id>/home")
@builder_required
def api_vehicle_home(world_id, vehicle_id):
    _post()
    message = live.vehicle_home(_published(world_id), world_id, vehicle_id)
    return jsonify(message=message, live=live.summary(get_db(), world_id))


@bp.post("/api/w/<world_id>/live/vehicles/<path:vehicle_id>/remove")
@builder_required
def api_vehicle_remove(world_id, vehicle_id):
    data = _post()
    _confirmed(data, "delete")
    message = live.vehicle_remove(_published(world_id), world_id, vehicle_id)
    return jsonify(message=message, live=live.summary(get_db(), world_id))


@bp.post("/api/w/<world_id>/live/scripts/reset")
@builder_required
def api_reset_scripts(world_id):
    data = _post()
    _confirmed(data, "reset")
    message = live.reset_scripts(_published(world_id), world_id)
    return jsonify(message=message, live=live.summary(get_db(), world_id))
