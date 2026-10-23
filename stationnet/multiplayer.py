"""Authenticated, station-local multiplayer sessions for sandboxed applications."""
import json
import secrets
import uuid

from .db import get_db


MAX_PLAYERS = 64
MAX_EVENT_BYTES = 32 * 1024
MAX_STATE_BYTES = 256 * 1024
EVENT_HISTORY = 500
DISCONNECT_SECONDS = 30
RECONNECT_GRACE_SECONDS = 300


class MultiplayerError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _json(value, limit, label):
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise MultiplayerError(f"{label} must be JSON-compatible.") from None
    if len(encoded.encode("utf-8")) > limit:
        raise MultiplayerError(f"{label} exceeds the {limit // 1024} KiB limit.", 413)
    return encoded


def _session(database, slug, session_id):
    row = database.execute(
        "SELECT * FROM application_multiplayer_sessions WHERE id=? AND application_slug=? AND status='open'",
        (session_id, slug),
    ).fetchone()
    if row is None:
        raise MultiplayerError("Session was not found.", 404)
    return row


def _member(database, session_id, user_id):
    row = database.execute(
        "SELECT * FROM application_multiplayer_members WHERE session_id=? AND user_id=?",
        (session_id, user_id),
    ).fetchone()
    if row is None:
        raise MultiplayerError("You are not a member of this session.", 403)
    return row


def _player(row, host_id):
    return {
        "id": f"player:{row['user_id']}",
        "username": row["username"],
        "is_host": row["user_id"] == host_id,
        "connected": bool(row["connected"]),
    }


def _emit(database, session_id, sender_id, event_type, payload):
    cursor = database.execute(
        "INSERT INTO application_multiplayer_events(session_id,sender_user_id,event_type,payload_json) VALUES(?,?,?,?)",
        (session_id, sender_id, event_type, _json(payload, MAX_EVENT_BYTES, "Event data")),
    )
    database.execute(
        "DELETE FROM application_multiplayer_events WHERE session_id=? AND id NOT IN "
        "(SELECT id FROM application_multiplayer_events WHERE session_id=? ORDER BY id DESC LIMIT ?)",
        (session_id, session_id, EVENT_HISTORY),
    )
    return cursor.lastrowid


def _touch(database, session_id, user_id, connection_id):
    _member(database, session_id, user_id)
    database.execute(
        "UPDATE application_multiplayer_members SET connected=1,connection_id=?,last_seen=CURRENT_TIMESTAMP "
        "WHERE session_id=? AND user_id=?",
        (connection_id, session_id, user_id),
    )


def _summary(database, row, include_code=False):
    count = database.execute(
        "SELECT COUNT(*) FROM application_multiplayer_members WHERE session_id=?", (row["id"],)
    ).fetchone()[0]
    result = {
        "id": row["id"], "name": row["name"], "visibility": row["visibility"],
        "max_players": row["max_players"], "player_count": count,
        "state_revision": row["state_revision"],
    }
    if include_code and row["visibility"] == "private":
        result["join_code"] = row["join_code"]
    return result


def _require_text(data, key, limit):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise MultiplayerError(f"{key.replace('_', ' ').title()} must be 1-{limit} characters.")
    return value.strip()


def _cleanup(database):
    """Infer disconnects and retire abandoned membership/session records."""
    stale = database.execute(
        "SELECT session_id,user_id FROM application_multiplayer_members "
        "WHERE connected=1 AND last_seen < datetime('now', ?)", (f"-{DISCONNECT_SECONDS} seconds",),
    ).fetchall()
    for item in stale:
        database.execute(
            "UPDATE application_multiplayer_members SET connected=0 WHERE session_id=? AND user_id=?",
            (item["session_id"], item["user_id"]),
        )
        _emit(database, item["session_id"], item["user_id"], "player_disconnected",
              {"player_id": f"player:{item['user_id']}"})
    expired_sessions = database.execute(
        "SELECT DISTINCT session_id FROM application_multiplayer_members "
        "WHERE connected=0 AND last_seen < datetime('now', ?)", (f"-{RECONNECT_GRACE_SECONDS} seconds",),
    ).fetchall()
    for item in expired_sessions:
        session_id = item["session_id"]
        database.execute(
            "DELETE FROM application_multiplayer_members WHERE session_id=? AND connected=0 "
            "AND last_seen < datetime('now', ?)", (session_id, f"-{RECONNECT_GRACE_SECONDS} seconds"),
        )
        current = database.execute(
            "SELECT host_user_id FROM application_multiplayer_sessions WHERE id=? AND status='open'", (session_id,),
        ).fetchone()
        if current is None:
            continue
        members = database.execute(
            "SELECT user_id FROM application_multiplayer_members WHERE session_id=? ORDER BY joined_at,user_id", (session_id,),
        ).fetchall()
        if not members:
            database.execute(
                "UPDATE application_multiplayer_sessions SET status='closed',updated_at=CURRENT_TIMESTAMP WHERE id=?", (session_id,),
            )
        elif not any(member["user_id"] == current["host_user_id"] for member in members):
            new_host = members[0]["user_id"]
            database.execute(
                "UPDATE application_multiplayer_sessions SET host_user_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_host, session_id),
            )
            _emit(database, session_id, None, "host_changed", {"player_id": f"player:{new_host}"})
    database.execute(
        "DELETE FROM application_multiplayer_sessions WHERE status='closed' AND updated_at < datetime('now','-1 day')"
    )


def handle_multiplayer(slug, user, data):
    """Execute one bounded multiplayer operation using the authenticated user."""
    if not isinstance(data, dict):
        raise MultiplayerError("A JSON request body is required.")
    operation = data.get("operation")
    connection_id = data.get("connection_id")
    if not isinstance(connection_id, str) or not 8 <= len(connection_id) <= 80:
        raise MultiplayerError("A valid runtime connection is required.")
    database = get_db()
    user_id = user["id"]
    _cleanup(database)

    if operation == "discover":
        rows = database.execute(
            "SELECT * FROM application_multiplayer_sessions WHERE application_slug=? "
            "AND status='open' AND visibility='public' ORDER BY updated_at DESC LIMIT 50", (slug,),
        ).fetchall()
        result = {"sessions": [_summary(database, row) for row in rows]}
        database.commit()
        return result

    if operation == "create":
        name = _require_text(data, "name", 80)
        maximum = data.get("max_players", 8)
        if type(maximum) is not int or not 2 <= maximum <= MAX_PLAYERS:
            raise MultiplayerError(f"Player limit must be a whole number from 2 to {MAX_PLAYERS}.")
        visibility = data.get("visibility", "public")
        if visibility not in {"public", "private"}:
            raise MultiplayerError("Visibility must be 'public' or 'private'.")
        session_id = uuid.uuid4().hex
        join_code = secrets.token_urlsafe(9) if visibility == "private" else None
        state = _json(data.get("state", {}), MAX_STATE_BYTES, "Session state")
        database.execute(
            "INSERT INTO application_multiplayer_sessions"
            "(id,application_slug,name,visibility,join_code,max_players,host_user_id,state_json) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (session_id, slug, name, visibility, join_code, maximum, user_id, state),
        )
        database.execute(
            "INSERT INTO application_multiplayer_members(session_id,user_id,connection_id) VALUES(?,?,?)",
            (session_id, user_id, connection_id),
        )
        _emit(database, session_id, user_id, "player_joined", {"player_id": f"player:{user_id}"})
        database.commit()
        row = _session(database, slug, session_id)
        return {"session": _summary(database, row, True), "player_id": f"player:{user_id}", "is_host": True}

    session_id = data.get("session_id")
    if not isinstance(session_id, str):
        raise MultiplayerError("A session id is required.")
    row = _session(database, slug, session_id)

    if operation == "join":
        existing = database.execute(
            "SELECT connected,connection_id FROM application_multiplayer_members WHERE session_id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        if not existing:
            if row["visibility"] == "private" and not secrets.compare_digest(str(data.get("join_code", "")), row["join_code"]):
                raise MultiplayerError("The private session code is invalid.", 403)
            count = database.execute(
                "SELECT COUNT(*) FROM application_multiplayer_members WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            if count >= row["max_players"]:
                raise MultiplayerError("Session is full.", 409)
            database.execute(
                "INSERT INTO application_multiplayer_members(session_id,user_id,connection_id) VALUES(?,?,?)",
                (session_id, user_id, connection_id),
            )
            _emit(database, session_id, user_id, "player_joined", {"player_id": f"player:{user_id}"})
        else:
            _touch(database, session_id, user_id, connection_id)
            if not existing["connected"] or existing["connection_id"] != connection_id:
                _emit(database, session_id, user_id, "player_reconnected", {"player_id": f"player:{user_id}"})
        database.commit()
        row = _session(database, slug, session_id)
        return {"session": _summary(database, row, row["host_user_id"] == user_id),
                "player_id": f"player:{user_id}", "is_host": row["host_user_id"] == user_id}

    member = _member(database, session_id, user_id)

    if operation == "leave":
        database.execute("DELETE FROM application_multiplayer_members WHERE session_id=? AND user_id=?", (session_id, user_id))
        _emit(database, session_id, user_id, "player_left", {"player_id": f"player:{user_id}"})
        remaining = database.execute(
            "SELECT user_id FROM application_multiplayer_members WHERE session_id=? ORDER BY joined_at,user_id", (session_id,)
        ).fetchall()
        if not remaining:
            database.execute("UPDATE application_multiplayer_sessions SET status='closed',updated_at=CURRENT_TIMESTAMP WHERE id=?", (session_id,))
        elif row["host_user_id"] == user_id:
            new_host = remaining[0]["user_id"]
            database.execute("UPDATE application_multiplayer_sessions SET host_user_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_host, session_id))
            _emit(database, session_id, None, "host_changed", {"player_id": f"player:{new_host}"})
        database.commit()
        return {"left": True}

    if operation == "disconnect":
        cursor = database.execute(
            "UPDATE application_multiplayer_members SET connected=0,last_seen=CURRENT_TIMESTAMP "
            "WHERE session_id=? AND user_id=? AND connection_id=?",
            (session_id, user_id, connection_id),
        )
        if cursor.rowcount:
            _emit(database, session_id, user_id, "player_disconnected", {"player_id": f"player:{user_id}"})
        database.commit()
        return {"connected": not bool(cursor.rowcount)}

    _touch(database, session_id, user_id, connection_id)

    if operation == "heartbeat":
        database.commit()
        return {"connected": True}

    if operation == "send":
        event_type = _require_text(data, "event", 80)
        if not all(character.isalnum() or character in "._:-" for character in event_type):
            raise MultiplayerError("Event names may use letters, numbers, '.', '_', ':', and '-'.")
        if event_type in {"player_joined", "player_left", "player_disconnected", "player_reconnected", "host_changed", "state_changed"}:
            raise MultiplayerError("That event name is reserved for StationNet lifecycle events.")
        event_id = _emit(database, session_id, user_id, event_type, data.get("data"))
        database.execute("UPDATE application_multiplayer_sessions SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (session_id,))
        database.commit()
        return {"event_id": event_id}

    if operation == "set_state":
        if row["host_user_id"] != user_id:
            raise MultiplayerError("Only the session host may replace shared state.", 403)
        expected = data.get("expected_revision")
        if expected is not None and (type(expected) is not int or expected != row["state_revision"]):
            raise MultiplayerError("Session state changed; refresh before updating.", 409)
        state_json = _json(data.get("state"), MAX_STATE_BYTES, "Session state")
        revision = row["state_revision"] + 1
        database.execute(
            "UPDATE application_multiplayer_sessions SET state_json=?,state_revision=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (state_json, revision, session_id),
        )
        _emit(database, session_id, user_id, "state_changed", {"revision": revision})
        database.commit()
        return {"state": data.get("state"), "revision": revision}

    if operation in {"snapshot", "poll"}:
        after = data.get("after", 0)
        if type(after) is not int or after < 0:
            raise MultiplayerError("Event cursor must be a non-negative whole number.")
        current = _session(database, slug, session_id)
        players = database.execute(
            "SELECT m.user_id,m.connected,u.username FROM application_multiplayer_members m "
            "JOIN users u ON u.id=m.user_id WHERE m.session_id=? ORDER BY m.joined_at,m.user_id", (session_id,),
        ).fetchall()
        events = database.execute(
            "SELECT e.id,e.sender_user_id,e.event_type,e.payload_json,e.created_at,u.username "
            "FROM application_multiplayer_events e LEFT JOIN users u ON u.id=e.sender_user_id "
            "WHERE e.session_id=? AND e.id>? ORDER BY e.id LIMIT 100", (session_id, after),
        ).fetchall()
        database.commit()
        return {
            "session": _summary(database, current, current["host_user_id"] == user_id),
            "state": json.loads(current["state_json"]), "revision": current["state_revision"],
            "players": [_player(item, current["host_user_id"]) for item in players],
            "events": [{"id": item["id"], "event": item["event_type"],
                        "data": json.loads(item["payload_json"]),
                        "player": None if item["sender_user_id"] is None else {
                            "id": f"player:{item['sender_user_id']}", "username": item["username"]},
                        "created_at": item["created_at"]} for item in events],
            "cursor": events[-1]["id"] if events else after,
        }

    raise MultiplayerError("Unknown multiplayer operation.")
