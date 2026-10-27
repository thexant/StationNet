"""ASGI WebSocket relay for transient, station-local application messages."""
import asyncio
import hmac
import json
import re
import time

from uvicorn.middleware.wsgi import WSGIMiddleware

from . import applications, moderation, mud
from .realtime import utc_now
from .db import get_db
from .multiplayer import MAX_EVENT_BYTES, MultiplayerError, _emit, _promote_connected_host

PATH = re.compile(r"^/games-apps/([a-z0-9]+(?:-[a-z0-9]+)*)/realtime$")
MAX_FRAME = 16 * 1024
MAX_QUEUE = 64
MAX_CONNECTIONS = 512
MAX_RATE = 60
MIN_RATE = 1
MAX_SYNC_STATE = 12 * 1024
SYNC_TTL = 300
MAX_SYNC_ROOMS = 512


class RoomRelay:
    def __init__(self):
        self.rooms = {}
        self.sync = {}
        self.loop = None

    def synchronize_host(self, room, host_id):
        now = time.monotonic()
        for key, value in tuple(self.sync.items()):
            if key not in self.rooms and now - value["updated"] > SYNC_TTL:
                self.sync.pop(key, None)
        state = self.sync.get(room)
        if state is None:
            if len(self.sync) >= MAX_SYNC_ROOMS:
                inactive = [(key, value["updated"]) for key, value in self.sync.items()
                            if key not in self.rooms]
                if inactive:
                    self.sync.pop(min(inactive, key=lambda item: item[1])[0], None)
            state = {"host_id": host_id, "epoch": 1, "sequence": 0,
                     "tick": -1, "state": None, "updated": now}
            self.sync[room] = state
        elif state["host_id"] != host_id:
            state.update(host_id=host_id, epoch=state["epoch"] + 1,
                         sequence=0, tick=-1, updated=now)
            self._publish(room, {"type": "host", "player_id": f"player:{host_id}",
                                 "epoch": state["epoch"], "state": state["state"]})
        return state

    def snapshot(self, state):
        return {"type": "sync", "epoch": state["epoch"], "host_id": f"player:{state['host_id']}",
                "tick": state["tick"], "state": state["state"],
                "server_time": int(time.time() * 1000)}

    def publish(self, room, value, exclude=None):
        """May be called by the Flask HTTP worker after a committed operation."""
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self._publish, room, value, exclude)

    def _publish(self, room, value, exclude=None):
        for client in tuple(self.rooms.get(room, ())):
            if client is exclude:
                continue
            self._publish_to(client, value)

    def _publish_to(self, client, value):
        try:
            client["queue"].put_nowait(value)
        except asyncio.QueueFull:
            client["overloaded"] = True

    def publish_host(self, room, host_id):
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.synchronize_host, room, host_id)

    def add(self, room, client):
        self.loop = asyncio.get_running_loop()
        if sum(map(len, self.rooms.values())) >= MAX_CONNECTIONS:
            raise MultiplayerError("Real-time connection limit reached.", 503)
        peers = self.rooms.setdefault(room, [])
        for previous in tuple(peers):
            if previous["user_id"] == client["user_id"]:
                previous["overloaded"] = True
        peers.append(client)

    def remove(self, room, client):
        peers = self.rooms.get(room)
        if peers:
            if client in peers:
                peers.remove(client)
            if not peers:
                self.rooms.pop(room, None)


def create_asgi_app(flask_app):
    relay = RoomRelay()
    flask_app.extensions["multiplayer_relay"] = relay
    http = WSGIMiddleware(flask_app)

    async def mud_timer():
        while True:
            try:
                await asyncio.to_thread(mud.run_scheduled_tick, flask_app)
            except Exception:
                flask_app.logger.exception("MUD scheduled event tick failed")
            await asyncio.sleep(2)

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            timer = None
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    timer = asyncio.create_task(mud_timer())
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    if timer:
                        timer.cancel()
                        try:
                            await timer
                        except asyncio.CancelledError:
                            pass
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        if scope["type"] != "websocket":
            await http(scope, receive, send)
            return
        match = PATH.fullmatch(scope.get("path", ""))
        if not match:
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        host = headers.get(b"host", b"").decode("ascii", "ignore")
        origin = headers.get(b"origin", b"").decode("ascii", "ignore")
        if not host or origin not in {"http://" + host, "https://" + host}:
            await send({"type": "websocket.close", "code": 1008})
            return
        cookie = headers.get(b"cookie", b"").decode("latin1")
        from http.cookies import SimpleCookie
        parsed = SimpleCookie()
        try:
            parsed.load(cookie)
            signed = parsed[flask_app.config.get("SESSION_COOKIE_NAME", "session")].value
            serializer = flask_app.session_interface.get_signing_serializer(flask_app)
            session = serializer.loads(signed, max_age=int(flask_app.permanent_session_lifetime.total_seconds()))
        except Exception:
            # A malformed or expired signed session never reaches application code.
            await send({"type": "websocket.close", "code": 1008})
            return
        try:
            first = await asyncio.wait_for(receive(), timeout=5)
            if first["type"] != "websocket.connect":
                return
            await send({"type": "websocket.accept"})
            auth_frame = await asyncio.wait_for(receive(), timeout=5)
            raw = auth_frame.get("text", "")
            if len(raw.encode("utf-8")) > MAX_FRAME:
                raise MultiplayerError("Frame too large.")
            hello = json.loads(raw)
            if not isinstance(hello, dict) or hello.get("type") != "hello":
                raise MultiplayerError("A real-time hello is required.")
            csrf = hello.get("csrf")
            if (not isinstance(csrf, str) or not session.get("csrf_token")
                    or not hmac.compare_digest(csrf, session["csrf_token"])):
                raise MultiplayerError("Invalid request token.", 403)
            slug = match.group(1)
            room = hello.get("session_id")
            connection = hello.get("connection_id")
            rate = hello.get("max_rate", 30)
            if not isinstance(room, str) or not re.fullmatch(r"[a-f0-9]{32}", room):
                raise MultiplayerError("Invalid session id.")
            if not isinstance(connection, str) or not 8 <= len(connection) <= 80:
                raise MultiplayerError("Invalid runtime connection.")
            if type(rate) is not int or not MIN_RATE <= rate <= MAX_RATE:
                raise MultiplayerError("Update rate must be 1-60 per second.")
            user_id = session.get("user_id")
            with flask_app.app_context():
                database = get_db()
                application = applications.find(slug)
                membership = database.execute(
                    "SELECT m.connection_id,m.connected,u.username,u.connection_id AS account_connection,s.host_user_id FROM application_multiplayer_members m "
                    "JOIN users u ON u.id=m.user_id JOIN application_multiplayer_sessions s ON s.id=m.session_id "
                    "WHERE m.session_id=? AND m.user_id=? AND s.application_slug=? AND s.status='open'",
                    (room, user_id, slug),
                ).fetchone()
                if (not application or not application.enabled or not application.runnable
                        or "multiplayer" not in application.permissions or not membership
                        or membership["connection_id"] != connection
                        or not session.get("connection_id")
                        or membership["account_connection"] != session["connection_id"]
                        or moderation.active_restriction(user_id, ("ban", "suspension"))):
                    raise MultiplayerError("Real-time access denied.", 403)
                username = membership["username"]
                database.execute("UPDATE application_multiplayer_members SET connected=1,last_seen=CURRENT_TIMESTAMP "
                                 "WHERE session_id=? AND user_id=? AND connection_id=?", (room, user_id, connection))
                if not membership["connected"]:
                    _emit(database, room, user_id, "player_reconnected", {"player_id": f"player:{user_id}"})
                database.commit()
            client = {"queue": asyncio.Queue(MAX_QUEUE), "user_id": user_id, "overloaded": False}
            relay.add(room, client)
            sync = relay.synchronize_host(room, membership["host_user_id"])
            await send({"type": "websocket.send", "text": json.dumps({"type": "ready", "max_rate": rate,
                "server_time": int(time.time() * 1000), "sync": relay.snapshot(sync)})})
            relay._publish(room, {"type": "presence", "player_id": f"player:{user_id}", "connected": True})
            last = time.monotonic()
            tokens = float(rate)
            last_check = last
            async def writer():
                while True:
                    item = await client["queue"].get()
                    await send({"type": "websocket.send", "text": json.dumps(item, separators=(",", ":"))})
            writing = asyncio.create_task(writer())
            try:
                while True:
                    if client["overloaded"]:
                        break
                    try:
                        frame = await asyncio.wait_for(receive(), timeout=10)
                    except asyncio.TimeoutError:
                        frame = {"type": "idle"}
                    if frame["type"] == "websocket.disconnect":
                        break
                    now = time.monotonic()
                    if now - last_check >= 10:
                        with flask_app.app_context():
                            database = get_db()
                            valid = database.execute(
                                "SELECT 1 FROM application_multiplayer_members m JOIN application_multiplayer_sessions s "
                                "ON s.id=m.session_id JOIN users u ON u.id=m.user_id "
                                "WHERE m.session_id=? AND m.user_id=? AND m.connection_id=? AND u.connection_id=? "
                                "AND s.application_slug=? AND s.status='open'",
                                (room, user_id, connection, session["connection_id"], slug),
                            ).fetchone()
                            if not valid or moderation.active_restriction(user_id, ("ban", "suspension")):
                                break
                            database.execute("UPDATE application_multiplayer_members SET connected=1,last_seen=CURRENT_TIMESTAMP "
                                             "WHERE session_id=? AND user_id=? AND connection_id=?", (room, user_id, connection))
                            database.execute("UPDATE users SET last_seen=? WHERE id=? AND connection_id=?",
                                             (utc_now(), user_id, session["connection_id"]))
                            database.commit()
                        last_check = now
                    if frame["type"] == "idle":
                        continue
                    with flask_app.app_context():
                        still_member = get_db().execute(
                            "SELECT s.host_user_id FROM application_multiplayer_members m JOIN application_multiplayer_sessions s "
                            "ON s.id=m.session_id JOIN users u ON u.id=m.user_id "
                            "WHERE m.session_id=? AND m.user_id=? AND m.connection_id=? AND u.connection_id=? "
                            "AND s.application_slug=? AND s.status='open'",
                            (room, user_id, connection, session["connection_id"], slug),
                        ).fetchone()
                        if not still_member:
                            break
                    sync = relay.synchronize_host(room, still_member["host_user_id"])
                    raw = frame.get("text")
                    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_FRAME:
                        break
                    try:
                        message = json.loads(raw)
                    except ValueError:
                        break
                    if not isinstance(message, dict) or message.get("type") not in {"event", "input", "state", "resync"}:
                        break
                    kind = message["type"]
                    if kind == "event":
                        name = message.get("event")
                        if (not isinstance(name, str) or not 1 <= len(name) <= 80
                                or not re.fullmatch(r"[A-Za-z0-9._:-]+", name)
                                or name in {"player_joined", "player_left", "player_disconnected", "player_reconnected", "host_changed", "state_changed"}):
                            break
                    try:
                        payload = json.dumps(message.get("data"), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                    except (TypeError, ValueError):
                        break
                    if len(payload.encode("utf-8")) > (MAX_SYNC_STATE if kind == "state" else MAX_EVENT_BYTES):
                        break
                    tokens = min(float(rate), tokens + (now - last) * rate)
                    last = now
                    if tokens < 1:
                        break
                    tokens -= 1
                    stamped = {"server_time": int(time.time() * 1000), "epoch": sync["epoch"]}
                    if kind == "event":
                        sync["sequence"] += 1
                        relay._publish(room, {"type": "event", "event": name, "data": message.get("data"),
                            "sequence": sync["sequence"], **stamped,
                            "player": {"id": f"player:{user_id}", "username": username}}, client)
                    elif kind == "input":
                        number = message.get("input_id")
                        if type(number) is not int or number < 0 or number > 2147483647 or number <= client.get("input_id", -1):
                            break
                        client["input_id"] = number
                        sync["sequence"] += 1
                        packet = {"type": "input", "input_id": number, "sequence": sync["sequence"],
                                  "data": message.get("data"), **stamped,
                                  "player": {"id": f"player:{user_id}", "username": username}}
                        for peer in tuple(relay.rooms.get(room, ())):
                            if peer["user_id"] == sync["host_id"]:
                                relay._publish_to(peer, packet)
                    elif kind == "state":
                        tick = message.get("tick")
                        if (user_id != sync["host_id"] or type(tick) is not int or
                                tick <= sync["tick"] or tick > 2147483647 or message.get("epoch") != sync["epoch"]):
                            break
                        sync.update(tick=tick, state=message.get("data"), updated=now)
                        sync["sequence"] += 1
                        relay._publish(room, {"type": "state", "tick": tick, "sequence": sync["sequence"],
                            "data": sync["state"], **stamped}, client)
                    else:
                        relay._publish_to(client, relay.snapshot(sync))
            finally:
                writing.cancel()
                relay.remove(room, client)
                if not any(peer["user_id"] == user_id for peer in relay.rooms.get(room, ())):
                    with flask_app.app_context():
                        database = get_db()
                        updated = database.execute(
                            "UPDATE application_multiplayer_members SET connected=0,last_seen=CURRENT_TIMESTAMP "
                            "WHERE session_id=? AND user_id=? AND connection_id=?", (room, user_id, connection),
                        )
                        if updated.rowcount:
                            _emit(database, room, user_id, "player_disconnected", {"player_id": f"player:{user_id}"})
                            new_host = _promote_connected_host(database, room)
                        else:
                            new_host = None
                        database.commit()
                    relay._publish(room, {"type": "presence", "player_id": f"player:{user_id}", "connected": False})
                    if new_host is not None:
                        relay.synchronize_host(room, new_host)
        except (MultiplayerError, ValueError, asyncio.TimeoutError, KeyError):
            pass
        await send({"type": "websocket.close", "code": 1008})
    return app
