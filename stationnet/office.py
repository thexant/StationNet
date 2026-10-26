"""Optional Office suite and owner-only encrypted file storage."""
import base64
import binascii
import hmac
import json
import secrets
import sqlite3

from flask import Blueprint, abort, g, jsonify, render_template, request, session

from .auth import login_required
from .db import get_db

bp = Blueprint("office", __name__, url_prefix="/office")
MAX_ENVELOPE_BYTES = 8 * 1024 * 1024
MAX_SHEET_ENVELOPE_BYTES = 32 * 1024 * 1024
MAX_PRESENTATION_ENVELOPE_BYTES = 64 * 1024 * 1024


@bp.before_request
def require_enabled():
    if not g.station or not g.station["office_enabled"]:
        abort(404)


def _csrf():
    token = request.headers.get("X-CSRF-Token", "")
    if not session.get("csrf_token") or not hmac.compare_digest(token, session["csrf_token"]):
        return jsonify(error="Invalid or expired form token."), 400
    return None


def _envelope(max_bytes=MAX_ENVELOPE_BYTES):
    value = (request.get_json(silent=True) or {}).get("envelope")
    if not isinstance(value, dict) or value.get("v") != 1 or set(value) != {"v", "iv", "data"}:
        return None
    iv, data = value.get("iv"), value.get("data")
    if not isinstance(iv, str) or not isinstance(data, str):
        return None
    if len(data) < 24 or len(data) > max_bytes * 4 // 3 + 8:
        return None
    try:
        if len(base64.b64decode(iv, validate=True)) != 12:
            return None
        raw = base64.b64decode(data, validate=True)
        if len(raw) < 16 or len(raw) > max_bytes:
            return None
    except (ValueError, binascii.Error):
        return None
    return json.dumps(value, separators=(",", ":"))


@bp.get("/")
@bp.get("")
@login_required
def index():
    return render_template("office/index.html")


@bp.get("/documents")
@login_required
def documents():
    return render_template("office/documents.html")


@bp.get("/sheets")
@login_required
def sheets():
    return render_template("office/sheets.html")


@bp.get("/presentations")
@login_required
def presentations():
    return render_template("office/presentations.html")


@bp.get("/agenda")
@login_required
def agenda():
    return render_template("office/agenda.html")


@bp.get("/agenda/api")
@login_required
def agenda_get():
    row = get_db().execute(
        "SELECT envelope,revision,created_at,updated_at FROM office_agendas WHERE owner_id=?",
        (g.user["id"],),
    ).fetchone()
    if row is None:
        return jsonify(agenda=None)
    return jsonify(agenda={
        "envelope": json.loads(row["envelope"]), "revision": row["revision"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    })


@bp.post("/agenda/api")
@login_required
def agenda_create():
    failure = _csrf()
    if failure:
        return failure
    envelope = _envelope(MAX_SHEET_ENVELOPE_BYTES)
    if envelope is None:
        return jsonify(error="Invalid encrypted agenda."), 400
    database = get_db()
    try:
        database.execute(
            "INSERT INTO office_agendas(owner_id,envelope) VALUES(?,?)",
            (g.user["id"], envelope),
        )
        database.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="Revision conflict. This agenda was created in another tab or device.", revision=1), 409
    return jsonify(revision=1), 201


@bp.put("/agenda/api")
@login_required
def agenda_update():
    failure = _csrf()
    if failure:
        return failure
    payload = request.get_json(silent=True) or {}
    envelope = _envelope(MAX_SHEET_ENVELOPE_BYTES)
    revision = payload.get("revision")
    if envelope is None or type(revision) is not int or revision < 1:
        return jsonify(error="Invalid encrypted agenda or revision."), 400
    database = get_db()
    cursor = database.execute(
        "UPDATE office_agendas SET envelope=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP "
        "WHERE owner_id=? AND revision=?",
        (envelope, g.user["id"], revision),
    )
    if cursor.rowcount:
        database.commit()
        return jsonify(revision=revision + 1)
    row = database.execute(
        "SELECT revision FROM office_agendas WHERE owner_id=?", (g.user["id"],)
    ).fetchone()
    if row is None:
        return jsonify(error="Agenda no longer exists."), 404
    return jsonify(error="Revision conflict. This agenda changed in another tab or device.",
                   revision=row["revision"]), 409


@bp.post("/documents/api/vault")
@login_required
def vault():
    failure = _csrf()
    if failure:
        return failure
    database = get_db()
    database.execute(
        "INSERT OR IGNORE INTO office_document_vaults(owner_id,salt) VALUES(?,?)",
        (g.user["id"], base64.b64encode(secrets.token_bytes(16)).decode("ascii")),
    )
    database.commit()
    row = database.execute(
        "SELECT salt FROM office_document_vaults WHERE owner_id=?", (g.user["id"],)
    ).fetchone()
    return jsonify(salt=row["salt"])


@bp.get("/documents/api")
@login_required
def document_list():
    rows = get_db().execute(
        "SELECT id,envelope,revision,created_at,updated_at FROM office_documents "
        "WHERE owner_id=? ORDER BY updated_at DESC,id DESC", (g.user["id"],)
    ).fetchall()
    return jsonify(documents=[{
        "id": row["id"], "envelope": json.loads(row["envelope"]),
        "revision": row["revision"], "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    } for row in rows])


@bp.post("/documents/api")
@login_required
def document_create():
    failure = _csrf()
    if failure:
        return failure
    envelope = _envelope()
    if envelope is None:
        return jsonify(error="Invalid encrypted document."), 400
    database = get_db()
    cursor = database.execute(
        "INSERT INTO office_documents(owner_id,envelope) VALUES(?,?)",
        (g.user["id"], envelope),
    )
    database.commit()
    return jsonify(id=cursor.lastrowid, revision=1), 201


@bp.get("/documents/api/<int:document_id>")
@login_required
def document_get(document_id):
    row = get_db().execute(
        "SELECT id,envelope,revision,created_at,updated_at FROM office_documents "
        "WHERE id=? AND owner_id=?", (document_id, g.user["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    return jsonify(id=row["id"], envelope=json.loads(row["envelope"]),
                   revision=row["revision"], created_at=row["created_at"],
                   updated_at=row["updated_at"])


@bp.put("/documents/api/<int:document_id>")
@login_required
def document_update(document_id):
    failure = _csrf()
    if failure:
        return failure
    payload = request.get_json(silent=True) or {}
    envelope = _envelope()
    revision = payload.get("revision")
    if envelope is None or type(revision) is not int or revision < 1:
        return jsonify(error="Invalid encrypted document or revision."), 400
    database = get_db()
    cursor = database.execute(
        "UPDATE office_documents SET envelope=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND owner_id=? AND revision=?",
        (envelope, document_id, g.user["id"], revision),
    )
    if cursor.rowcount:
        database.commit()
        return jsonify(revision=revision + 1)
    row = database.execute(
        "SELECT revision FROM office_documents WHERE id=? AND owner_id=?",
        (document_id, g.user["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    return jsonify(error="Revision conflict. This document changed in another tab or device.",
                   revision=row["revision"]), 409


@bp.delete("/documents/api/<int:document_id>")
@login_required
def document_delete(document_id):
    failure = _csrf()
    if failure:
        return failure
    database = get_db()
    cursor = database.execute(
        "DELETE FROM office_documents WHERE id=? AND owner_id=?",
        (document_id, g.user["id"]),
    )
    database.commit()
    if not cursor.rowcount:
        abort(404)
    return jsonify(ok=True)


@bp.get("/sheets/api")
@login_required
def sheet_list():
    rows = get_db().execute(
        "SELECT id,envelope,revision,created_at,updated_at FROM office_sheets "
        "WHERE owner_id=? ORDER BY updated_at DESC,id DESC", (g.user["id"],)
    ).fetchall()
    return jsonify(sheets=[{
        "id": row["id"], "envelope": json.loads(row["envelope"]),
        "revision": row["revision"], "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    } for row in rows])


@bp.post("/sheets/api")
@login_required
def sheet_create():
    failure = _csrf()
    if failure:
        return failure
    envelope = _envelope(MAX_SHEET_ENVELOPE_BYTES)
    if envelope is None:
        return jsonify(error="Invalid encrypted sheet."), 400
    database = get_db()
    cursor = database.execute(
        "INSERT INTO office_sheets(owner_id,envelope) VALUES(?,?)",
        (g.user["id"], envelope),
    )
    database.commit()
    return jsonify(id=cursor.lastrowid, revision=1), 201


@bp.get("/sheets/api/<int:sheet_id>")
@login_required
def sheet_get(sheet_id):
    row = get_db().execute(
        "SELECT id,envelope,revision,created_at,updated_at FROM office_sheets "
        "WHERE id=? AND owner_id=?", (sheet_id, g.user["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    return jsonify(id=row["id"], envelope=json.loads(row["envelope"]),
                   revision=row["revision"], created_at=row["created_at"],
                   updated_at=row["updated_at"])


@bp.put("/sheets/api/<int:sheet_id>")
@login_required
def sheet_update(sheet_id):
    failure = _csrf()
    if failure:
        return failure
    payload = request.get_json(silent=True) or {}
    envelope = _envelope(MAX_SHEET_ENVELOPE_BYTES)
    revision = payload.get("revision")
    if envelope is None or type(revision) is not int or revision < 1:
        return jsonify(error="Invalid encrypted sheet or revision."), 400
    database = get_db()
    cursor = database.execute(
        "UPDATE office_sheets SET envelope=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND owner_id=? AND revision=?",
        (envelope, sheet_id, g.user["id"], revision),
    )
    if cursor.rowcount:
        database.commit()
        return jsonify(revision=revision + 1)
    row = database.execute(
        "SELECT revision FROM office_sheets WHERE id=? AND owner_id=?",
        (sheet_id, g.user["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    return jsonify(error="Revision conflict. This sheet changed in another tab or device.",
                   revision=row["revision"]), 409


@bp.delete("/sheets/api/<int:sheet_id>")
@login_required
def sheet_delete(sheet_id):
    failure = _csrf()
    if failure:
        return failure
    database = get_db()
    cursor = database.execute(
        "DELETE FROM office_sheets WHERE id=? AND owner_id=?",
        (sheet_id, g.user["id"]),
    )
    database.commit()
    if not cursor.rowcount:
        abort(404)
    return jsonify(ok=True)


@bp.get("/presentations/api")
@login_required
def presentation_list():
    rows = get_db().execute(
        "SELECT id,envelope,revision,created_at,updated_at FROM office_presentations "
        "WHERE owner_id=? ORDER BY updated_at DESC,id DESC", (g.user["id"],)
    ).fetchall()
    return jsonify(presentations=[{
        "id": row["id"], "envelope": json.loads(row["envelope"]),
        "revision": row["revision"], "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    } for row in rows])


@bp.post("/presentations/api")
@login_required
def presentation_create():
    failure = _csrf()
    if failure:
        return failure
    envelope = _envelope(MAX_PRESENTATION_ENVELOPE_BYTES)
    if envelope is None:
        return jsonify(error="Invalid encrypted presentation."), 400
    database = get_db()
    cursor = database.execute(
        "INSERT INTO office_presentations(owner_id,envelope) VALUES(?,?)",
        (g.user["id"], envelope),
    )
    database.commit()
    return jsonify(id=cursor.lastrowid, revision=1), 201


@bp.get("/presentations/api/<int:presentation_id>")
@login_required
def presentation_get(presentation_id):
    row = get_db().execute(
        "SELECT id,envelope,revision,created_at,updated_at FROM office_presentations "
        "WHERE id=? AND owner_id=?", (presentation_id, g.user["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    return jsonify(id=row["id"], envelope=json.loads(row["envelope"]),
                   revision=row["revision"], created_at=row["created_at"],
                   updated_at=row["updated_at"])


@bp.put("/presentations/api/<int:presentation_id>")
@login_required
def presentation_update(presentation_id):
    failure = _csrf()
    if failure:
        return failure
    payload = request.get_json(silent=True) or {}
    envelope = _envelope(MAX_PRESENTATION_ENVELOPE_BYTES)
    revision = payload.get("revision")
    if envelope is None or type(revision) is not int or revision < 1:
        return jsonify(error="Invalid encrypted presentation or revision."), 400
    database = get_db()
    cursor = database.execute(
        "UPDATE office_presentations SET envelope=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND owner_id=? AND revision=?",
        (envelope, presentation_id, g.user["id"], revision),
    )
    if cursor.rowcount:
        database.commit()
        return jsonify(revision=revision + 1)
    row = database.execute(
        "SELECT revision FROM office_presentations WHERE id=? AND owner_id=?",
        (presentation_id, g.user["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    return jsonify(error="Revision conflict. This presentation changed in another tab or device.",
                   revision=row["revision"]), 409


@bp.delete("/presentations/api/<int:presentation_id>")
@login_required
def presentation_delete(presentation_id):
    failure = _csrf()
    if failure:
        return failure
    database = get_db()
    cursor = database.execute(
        "DELETE FROM office_presentations WHERE id=? AND owner_id=?",
        (presentation_id, g.user["id"]),
    )
    database.commit()
    if not cursor.rowcount:
        abort(404)
    return jsonify(ok=True)
