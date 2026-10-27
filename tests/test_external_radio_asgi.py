"""Continuous radio responses must not occupy the station's request workers."""

import asyncio
from threading import Event, Lock

from flask import Flask, Response

from stationnet.multiplayer_socket import create_asgi_app


def test_audio_streams_do_not_exhaust_settings_workers():
    flask_app = Flask(__name__)
    flask_app.extensions = {}
    release = Event()
    lock = Lock()
    started = 0
    closed = 0

    @flask_app.get("/radio/external/<int:transmission_id>/audio")
    def audio(transmission_id):
        def chunks():
            nonlocal started, closed
            with lock:
                started += 1
            try:
                yield b"audio"
                release.wait(5)
                yield b"more audio"
            finally:
                with lock:
                    closed += 1

        return Response(chunks(), mimetype="audio/mpeg")

    @flask_app.post("/sysop/settings")
    def save_settings():
        return "saved"

    asgi_app = create_asgi_app(flask_app)

    def scope(path, method="GET"):
        return {"type": "http", "asgi": {"version": "3.0"},
                "method": method, "scheme": "http", "path": path,
                "raw_path": path.encode(), "query_string": b"", "headers": [],
                "http_version": "1.1", "server": ("localhost", 8080),
                "client": ("127.0.0.1", 12345)}

    async def exercise():
        streams = []
        try:
            for transmission_id in range(10):
                incoming = asyncio.Queue()
                outgoing = asyncio.Queue()
                await incoming.put({"type": "http.request", "body": b"",
                                    "more_body": False})
                task = asyncio.create_task(asgi_app(
                    scope(f"/radio/external/{transmission_id}/audio"),
                    incoming.get, outgoing.put))
                streams.append((task, incoming, outgoing))
            for _task, _incoming, outgoing in streams:
                assert (await asyncio.wait_for(outgoing.get(), 2))["status"] == 200
                assert (await asyncio.wait_for(outgoing.get(), 2))["body"] == b"audio"
            assert started == 10

            incoming = asyncio.Queue()
            outgoing = asyncio.Queue()
            await incoming.put({"type": "http.request", "body": b"",
                                "more_body": False})
            await asyncio.wait_for(asgi_app(scope("/sysop/settings", "POST"),
                                            incoming.get, outgoing.put), 2)
            assert (await outgoing.get())["status"] == 200
            assert (await outgoing.get())["body"] == b"saved"

            for _task, incoming, _outgoing in streams:
                await incoming.put({"type": "http.disconnect"})
            release.set()
            await asyncio.wait_for(asyncio.gather(*(task for task, _, _ in streams)), 2)
            assert closed == 10
        finally:
            release.set()
            for task, incoming, _outgoing in streams:
                await incoming.put({"type": "http.disconnect"})
            await asyncio.gather(*(task for task, _, _ in streams),
                                 return_exceptions=True)

    asyncio.run(exercise())
