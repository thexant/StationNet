"""Production ASGI video uploads use bounded disk storage and release it."""
import asyncio
import io
from pathlib import Path
from threading import Event, get_ident
from types import SimpleNamespace

from flask import request
import pytest

from stationnet import create_app, videos, video_upload_transport as transport
from stationnet.db import get_db
from stationnet.multiplayer_socket import create_asgi_app

MB = 1024 * 1024
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4


@pytest.fixture
def staged(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,videos_enabled,videos_max_upload_mb) "
                   "VALUES('Test','Test','TST-1',1,132)")
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                           "VALUES('admin','ADM-1','unused',1)").lastrowid
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = admin
        session["csrf_token"] = "transport-test"
    cookie = client.get_cookie("session").value
    monkeypatch.setattr(videos.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    files, threads = [], []
    temporary_file = transport.tempfile.TemporaryFile

    class TrackedFile:
        def __init__(self):
            threads.append(get_ident())
            self.raw = temporary_file(mode="w+b", dir=tmp_path)
            files.append(self.raw)

        def write(self, data):
            threads.append(get_ident())
            return self.raw.write(data)

        def seek(self, offset, *args):
            threads.append(get_ident())
            return self.raw.seek(offset, *args)

        def close(self):
            threads.append(get_ident())
            self.raw.close()

        def __getattr__(self, name):
            return getattr(self.raw, name)

    monkeypatch.setattr(transport.tempfile, "TemporaryFile", lambda **kwargs: TrackedFile())
    return app, cookie, files, threads


def scope(cookie="", length=None, path="/videos/station-upload"):
    headers = [(b"content-type", b"multipart/form-data; boundary=video-test")]
    if cookie:
        headers.append((b"cookie", f"session={cookie}".encode()))
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    return {"type": "http", "asgi": {"version": "3.0"}, "method": "POST", "scheme": "http",
            "path": path, "root_path": "", "raw_path": path.encode(), "query_string": b"",
            "headers": headers, "http_version": "1.1", "server": ("localhost", 8080),
            "client": ("127.0.0.1", 12345)}


async def call(asgi, request_scope, events):
    messages = []
    iterator = iter(events)

    async def receive():
        return next(iterator)

    async def send(message):
        messages.append(message)

    await asgi(request_scope, receive, send)
    return messages


def test_real_multipart_upload_above_old_request_ceiling(staged):
    app, cookie, files, threads = staged
    size = 130 * MB
    prefix = (b"--video-test\r\nContent-Disposition: form-data; name=\"csrf_token\"\r\n\r\ntransport-test\r\n"
              b"--video-test\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nLarge video\r\n"
              b"--video-test\r\nContent-Disposition: form-data; name=\"file\"; filename=\"large.mp4\"\r\n"
              b"Content-Type: video/mp4\r\n\r\n")
    suffix = b"\r\n--video-test--\r\n"

    @app.before_request
    def check_staging():
        if request.path == "/videos/station-upload":
            assert not isinstance(request.environ["wsgi.input"], io.BytesIO)
            assert request.max_content_length == 128 * MB  # blueprint sets it next

    def events():
        yield {"type": "http.request", "body": prefix + MP4, "more_body": True}
        remaining = size - len(MP4)
        chunk = b"x" * MB
        while remaining:
            count = min(remaining, MB)
            yield {"type": "http.request", "body": chunk[:count], "more_body": True}
            remaining -= count
        yield {"type": "http.request", "body": suffix, "more_body": False}

    event_thread = get_ident()
    messages = asyncio.run(call(create_asgi_app(app), scope(cookie, len(prefix) + size + len(suffix)), events()))
    assert messages[0]["status"] == 302
    assert files and all(file.closed for file in files)
    assert all(thread != event_thread for thread in threads)
    with app.app_context():
        row = get_db().execute("SELECT size_bytes,stored_name FROM videos").fetchone()
        assert row["size_bytes"] == size
        assert (Path(app.config["DATA_DIR"]) / "videos/station" / row["stored_name"]).stat().st_size == size


@pytest.mark.parametrize("declared,events,expected", [
    (134 * MB, [], 413),
    (-1, [], 400),
    ("invalid", [], 400),
    (2, [{"type": "http.request", "body": b"abc"}], 400),
    (4, [{"type": "http.request", "body": b"abc"}], 400),
    (None, [{"type": "http.disconnect"}], None),
    (None, [{"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.disconnect"}], None),
])
def test_rejected_or_disconnected_requests_close_staging(staged, declared, events, expected):
    app, cookie, files, threads = staged
    messages = asyncio.run(call(create_asgi_app(app), scope(cookie, declared), events))
    if expected is None:
        assert not messages
    else:
        assert messages[0]["status"] == expected
    assert all(file.closed for file in files)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 0


@pytest.mark.parametrize("declared", [None, 2 * MB])
def test_actual_received_length_is_bounded(staged, declared):
    app, cookie, files, threads = staged
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET videos_max_upload_mb=1")
        db.commit()
    events = [{"type": "http.request", "body": b"x" * MB, "more_body": True},
              {"type": "http.request", "body": b"x" * (MB + 1), "more_body": False}]
    messages = asyncio.run(call(create_asgi_app(app), scope(cookie, declared), events))
    assert messages[0]["status"] == 413
    assert files and all(file.closed for file in files)


def test_missing_length_and_mounted_paths_work(staged):
    app, cookie, files, threads = staged
    app.view_functions["videos.station_upload"] = lambda: str(len(request.get_data()))
    request_scope = scope(cookie, path="/station/videos/station-upload")
    request_scope["root_path"] = "/station"
    messages = asyncio.run(call(create_asgi_app(app), request_scope,
                               [{"type": "http.request", "body": b"abc", "more_body": False}]))
    assert messages[0]["status"] == 200
    assert messages[1]["body"] == b"3"
    assert files and all(file.closed for file in files)


def test_asgi_limit_tracks_settings_above_four_gib_without_restart(staged):
    app, cookie, files, threads = staged
    asgi = create_asgi_app(app)
    messages = asyncio.run(call(asgi, scope(cookie, 4196 * MB), []))
    assert messages[0]["status"] == 413
    assert b"Maximum video file size: 132 MB" in messages[1]["body"]
    assert not files
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET videos_max_upload_mb=4196")
        db.commit()
    messages = asyncio.run(call(asgi, scope(cookie, 4196 * MB), [{"type": "http.disconnect"}]))
    assert not messages
    assert files and all(file.closed for file in files)


def test_cancellation_during_receive_closes_staging(staged):
    app, cookie, files, threads = staged

    async def exercise():
        waiting = asyncio.Event()
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": b"abc", "more_body": True}
            waiting.set()
            await asyncio.Event().wait()

        async def send(message):
            pytest.fail("A cancelled upload must not send a response")

        task = asyncio.create_task(create_asgi_app(app)(scope(cookie), receive, send))
        await asyncio.wait_for(waiting.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert files and all(file.closed for file in files)


def test_disk_error_closes_staging(staged, monkeypatch):
    app, cookie, files, threads = staged
    original = transport.tempfile.TemporaryFile

    def broken_file(**kwargs):
        file = original(**kwargs)
        file.write = lambda data: (_ for _ in ()).throw(OSError("disk full"))
        return file

    monkeypatch.setattr(transport.tempfile, "TemporaryFile", broken_file)
    messages = asyncio.run(call(create_asgi_app(app), scope(cookie),
                               [{"type": "http.request", "body": b"abc", "more_body": False}]))
    assert messages[0]["status"] == 507
    assert files and all(file.closed for file in files)


def test_cancellation_waits_for_pending_disk_write(staged, monkeypatch):
    app, cookie, files, threads = staged
    original = transport.tempfile.TemporaryFile
    entered, release = Event(), Event()

    def slow_file(**kwargs):
        file = original(**kwargs)
        write = file.write

        def slow_write(data):
            entered.set()
            assert release.wait(3)
            assert not file.raw.closed
            return write(data)

        file.write = slow_write
        return file

    monkeypatch.setattr(transport.tempfile, "TemporaryFile", slow_file)

    async def exercise():
        task = asyncio.create_task(call(create_asgi_app(app), scope(cookie),
                                   [{"type": "http.request", "body": b"abc", "more_body": False}]))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # Repeated shutdown cancellation must also leave the write safe.
            assert not files[0].closed
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()

    asyncio.run(exercise())
    assert files and all(file.closed for file in files)


def test_application_failure_closes_staging(staged):
    app, cookie, files, threads = staged

    def fail():
        raise RuntimeError("upload failure")

    app.view_functions["videos.station_upload"] = fail
    with pytest.raises(RuntimeError, match="upload failure"):
        asyncio.run(call(create_asgi_app(app), scope(cookie, 0),
                         [{"type": "http.request", "body": b"", "more_body": False}]))
    assert files and all(file.closed for file in files)


def test_unrelated_posts_keep_original_transport_and_limit(staged):
    app, cookie, files, threads = staged

    @app.post("/test-body")
    def test_body():
        assert isinstance(request.environ["wsgi.input"], io.BytesIO)
        assert request.max_content_length == 128 * MB
        return "ok"

    messages = asyncio.run(call(create_asgi_app(app), scope(cookie, 0, path="/test-body"),
                               [{"type": "http.request", "body": b"", "more_body": False}]))
    assert messages[0]["status"] == 200
    assert not files
