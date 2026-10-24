"""Integration coverage for authenticated ASGI rooms and the legacy API."""
import asyncio
import json

import pytest

from stationnet import create_app
from stationnet.db import get_db
from stationnet.multiplayer import handle_multiplayer
from stationnet.multiplayer_socket import create_asgi_app


@pytest.fixture
def station(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(id,name,description,callsign) VALUES(1,'Test','','TST-001')")
        for number in range(1, 4):
            db.execute("INSERT INTO users(id,username,callsign,password_hash,connection_id) VALUES(?,?,?,'test',?)",
                       (number, f"user{number}", f"USR-00{number}", f"account-{number}"))
        db.commit()
    return app, create_asgi_app(app)


def room(app, user_id, connection, visibility="public"):
    with app.app_context():
        return handle_multiplayer("multiplayer-demo", {"id": user_id}, {
            "operation": "create", "connection_id": connection, "name": "Room",
            "visibility": visibility,
        })["session"]["id"]


def join(app, user_id, connection, session_id, code=None):
    with app.app_context():
        return handle_multiplayer("multiplayer-demo", {"id": user_id}, {
            "operation": "join", "connection_id": connection, "session_id": session_id, "join_code": code,
        })


class Socket:
    def __init__(self, asgi, app, user_id, room_id, connection, token="token", origin="http://station.test"):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        signed = app.session_interface.get_signing_serializer(app).dumps(
            {"user_id": user_id, "csrf_token": "token", "connection_id": f"account-{user_id}"})
        self.scope = {"type": "websocket", "path": "/games-apps/multiplayer-demo/realtime",
                      "headers": [(b"host", b"station.test"), (b"origin", origin.encode()),
                                  (b"cookie", f"session={signed}".encode())]}
        self.asgi = asgi
        self.room_id = room_id
        self.connection = connection
        self.token = token
        self.task = None

    async def start(self, rate=30):
        self.task = asyncio.create_task(self.asgi(self.scope, self.incoming.get, self.outgoing.put))
        await self.incoming.put({"type": "websocket.connect"})
        accepted = await asyncio.wait_for(self.outgoing.get(), 1)
        if accepted["type"] != "websocket.accept":
            return accepted
        await self.incoming.put({"type": "websocket.receive", "text": json.dumps({
            "type": "hello", "csrf": self.token, "session_id": self.room_id,
            "connection_id": self.connection, "max_rate": rate,
        })})
        return await asyncio.wait_for(self.outgoing.get(), 1)

    async def send(self, event, data):
        await self.incoming.put({"type": "websocket.receive", "text": json.dumps({
            "type": "event", "event": event, "data": data,
        })})

    async def packet(self, **value):
        await self.incoming.put({"type": "websocket.receive", "text": json.dumps(value)})

    async def next_type(self, kind):
        while True:
            item = await self.next()
            if item["type"] == "websocket.close":
                raise AssertionError("socket closed while waiting for " + kind)
            value = json.loads(item["text"])
            if value["type"] == kind:
                return value

    async def next(self):
        return await asyncio.wait_for(self.outgoing.get(), 1)

    async def close(self):
        if self.task and not self.task.done():
            await self.incoming.put({"type": "websocket.disconnect"})
        if self.task:
            await asyncio.wait_for(self.task, 1)


def test_realtime_is_room_scoped_and_transient(station):
    app, asgi = station
    first = room(app, 1, "connection-1")
    second = room(app, 3, "connection-3")
    join(app, 2, "connection-2", first)

    async def check():
        a = Socket(asgi, app, 1, first, "connection-1")
        b = Socket(asgi, app, 2, first, "connection-2")
        c = Socket(asgi, app, 3, second, "connection-3")
        try:
            for client in (a, b, c):
                ready = await client.start()
                assert json.loads(ready["text"])["type"] == "ready"
            # Drain presence notifications before checking event delivery.
            for client in (a, b, c):
                while not client.outgoing.empty():
                    client.outgoing.get_nowait()
            await a.send("position", {"x": 4})
            received = json.loads((await b.next())["text"])
            assert received["event"] == "position"
            assert received["player"] == {"id": "player:1", "username": "user1"}
            await asyncio.sleep(.02)
            for client in (a, c):
                assert all(json.loads(item["text"])["type"] != "event"
                           for item in list(client.outgoing._queue))
        finally:
            await asyncio.gather(a.close(), b.close(), c.close())

    asyncio.run(check())
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM application_multiplayer_events WHERE event_type='position'").fetchone()[0] == 0
        snapshot = handle_multiplayer("multiplayer-demo", {"id": 1}, {
            "operation": "snapshot", "connection_id": "connection-1", "session_id": first,
        })
        assert snapshot["session"]["id"] == first
        assert not any(event["event"] == "position" for event in snapshot["events"])


def test_realtime_rejects_invalid_identity_and_rate(station):
    app, asgi = station
    private = room(app, 1, "connection-1", "private")

    async def check():
        cases = [
            Socket(asgi, app, 2, private, "connection-2"),
            Socket(asgi, app, 1, private, "connection-1", token="bad"),
            Socket(asgi, app, 1, private, "connection-1", origin="http://other.test"),
        ]
        try:
            for client in cases:
                initial = await client.start()
                if initial["type"] == "websocket.accept":
                    initial = await client.next()
                assert initial["type"] == "websocket.close"
            rate = Socket(asgi, app, 1, private, "connection-1")
            cases.append(rate)
            assert (await rate.start(61))["type"] == "websocket.close"
            with app.app_context():
                get_db().execute("UPDATE users SET connection_id='new-login' WHERE id=1")
                get_db().commit()
            stale_login = Socket(asgi, app, 1, private, "connection-1")
            cases.append(stale_login)
            assert (await stale_login.start())["type"] == "websocket.close"
        finally:
            await asyncio.gather(*(client.close() for client in cases))

    asyncio.run(check())


def test_realtime_rate_limit_and_departure(station):
    app, asgi = station
    room_id = room(app, 1, "connection-1")
    join(app, 2, "connection-2", room_id)

    async def check():
        sender = Socket(asgi, app, 1, room_id, "connection-1")
        listener = Socket(asgi, app, 2, room_id, "connection-2")
        try:
            assert json.loads((await sender.start(rate=1))["text"])["type"] == "ready"
            assert json.loads((await listener.start())["text"])["type"] == "ready"
            await sender.send("move", {"x": 1})
            await sender.send("move", {"x": 2})
            while True:
                closed = await sender.next()
                if closed["type"] == "websocket.close":
                    break
            assert closed["code"] == 1008
            with app.app_context():
                handle_multiplayer("multiplayer-demo", {"id": 2}, {
                    "operation": "leave", "connection_id": "connection-2", "session_id": room_id,
                })
            await listener.send("move", {"x": 3})
            while True:
                closed = await listener.next()
                if closed["type"] == "websocket.close":
                    break
            assert closed["code"] == 1008
        finally:
            await asyncio.gather(sender.close(), listener.close())

    asyncio.run(check())


def test_legacy_host_migration_and_private_discovery(station):
    app, _ = station
    private = room(app, 1, "connection-1", "private")
    with app.app_context():
        discovered = handle_multiplayer("multiplayer-demo", {"id": 2}, {
            "operation": "discover", "connection_id": "connection-2",
        })
        assert all(item["id"] != private for item in discovered["sessions"])
        code = get_db().execute("SELECT join_code FROM application_multiplayer_sessions WHERE id=?", (private,)).fetchone()[0]
    with pytest.raises(ValueError):
        join(app, 2, "connection-2", private, "wrong")
    join(app, 2, "connection-2", private, code)
    with app.app_context():
        handle_multiplayer("multiplayer-demo", {"id": 1}, {
            "operation": "leave", "connection_id": "connection-1", "session_id": private,
        })
        snapshot = handle_multiplayer("multiplayer-demo", {"id": 2}, {
            "operation": "snapshot", "connection_id": "connection-2", "session_id": private,
        })
        assert snapshot["players"][0]["is_host"]


def test_ordered_input_snapshots_reconnect_and_host_transfer(station):
    app, asgi = station
    room_id = room(app, 1, "connection-1")
    join(app, 2, "connection-2", room_id)

    async def check():
        host = Socket(asgi, app, 1, room_id, "connection-1")
        guest = Socket(asgi, app, 2, room_id, "connection-2")
        try:
            ready = json.loads((await host.start())["text"])
            assert ready["sync"]["host_id"] == "player:1"
            assert json.loads((await guest.start())["text"])["sync"]["epoch"] == 1
            await guest.packet(type="input", input_id=1, data={"dx": 1})
            move = await host.next_type("input")
            assert (move["player"]["id"], move["input_id"], move["sequence"]) == ("player:2", 1, 1)
            assert isinstance(move["server_time"], int)
            await host.packet(type="state", tick=0, epoch=1, data={"x": 1})
            movement = await guest.next_type("state")
            assert (movement["tick"], movement["data"], movement["sequence"]) == (0, {"x": 1}, 2)
            await guest.close()
            guest = Socket(asgi, app, 2, room_id, "connection-2")
            resumed = json.loads((await guest.start())["text"])
            assert resumed["sync"]["state"] == {"x": 1}
            assert resumed["sync"]["tick"] == 0
            await guest.packet(type="resync")
            assert (await guest.next_type("sync"))["state"] == {"x": 1}
            await host.close()
            changed = await guest.next_type("host")
            assert changed["player_id"] == "player:2"
            assert changed["epoch"] == 2
            assert changed["state"] == {"x": 1}
            await guest.packet(type="state", tick=0, epoch=2, data={"x": 2})
            host = Socket(asgi, app, 1, room_id, "connection-1")
            rejoined = json.loads((await host.start())["text"])
            assert rejoined["sync"]["host_id"] == "player:2"
            assert rejoined["sync"]["state"] == {"x": 2}
            await host.packet(type="state", tick=1, epoch=2, data={"x": 99})
            while (await host.next())["type"] != "websocket.close":
                pass
        finally:
            await asyncio.gather(host.close(), guest.close())

    asyncio.run(check())
    with app.app_context():
        database = get_db()
        assert database.execute("SELECT state_revision FROM application_multiplayer_sessions WHERE id=?", (room_id,)).fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM application_multiplayer_events WHERE event_type IN ('input','state')").fetchone()[0] == 0
