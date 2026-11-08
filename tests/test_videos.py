"""Configurable video file limits, storage quotas, and upload compatibility."""
import io
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from flask import g, request
import pytest
from werkzeug.datastructures import FileStorage, MultiDict

from stationnet import create_app, videos
from stationnet.db import _migrate_videos, get_db

MB = 1024 * 1024
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4
TOKEN = "video-test"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,videos_enabled) VALUES('Test','Test','TST-1',1)")
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                           "VALUES('admin','ADM-1','unused',1)").lastrowid
        member = db.execute("INSERT INTO users(username,callsign,password_hash) "
                            "VALUES('member','MEM-1','unused')").lastrowid
        db.commit()
    monkeypatch.setattr(videos.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    return app, app.test_client(), admin, member


def signin(client, user):
    with client.session_transaction() as session:
        session["user_id"] = user
        session["csrf_token"] = TOKEN


def setting(client, **changes):
    data = {"csrf_token": TOKEN, "settings_section": "videos", "videos_enabled": "on",
            "videos_mode": "community", "videos_default_quota_mb": "1024"}
    return client.post("/sysop/settings", data={**data, **changes}, follow_redirects=True)


def upload(client, data, station=False, **changes):
    return client.post("/videos/station-upload" if station else "/videos/upload",
                       data={"csrf_token": TOKEN, "title": "Video", "description": "Test",
                             "file": (io.BytesIO(data), "test.mp4"), **changes},
                       content_type="multipart/form-data", follow_redirects=True)


def test_setting_defaults_validation_and_omitted_field(setup):
    app, client, admin, member = setup
    signin(client, admin)
    assert b'up to 120 MB' in client.get("/sysop/settings").data
    with app.app_context():
        assert get_db().execute("SELECT videos_max_upload_mb FROM station").fetchone()[0] == 120
    for maximum in (1, 4096, 4196, videos.MAX_UPLOAD_MB_LIMIT):
        response = setting(client, videos_max_upload_mb=str(maximum))
        assert b"Videos settings updated." in response.data
        assert f'up to {maximum} MB'.encode() in response.data
        with app.app_context():
            assert get_db().execute("SELECT videos_max_upload_mb FROM station").fetchone()[0] == maximum
    for invalid in ("", "0", "-1", "1.5", "garbage", str(videos.MAX_UPLOAD_MB_LIMIT + 1)):
        response = setting(client, videos_max_upload_mb=invalid, videos_mode="curated",
                           videos_default_quota_mb="2")
        assert b"Maximum video file size must be between 1 and 1048576 MB" in response.data
        with app.app_context():
            row = get_db().execute("SELECT videos_max_upload_mb,videos_mode,videos_default_quota_mb FROM station").fetchone()
            assert tuple(row) == (videos.MAX_UPLOAD_MB_LIMIT, "community", 1024)
    setting(client)
    signin(client, member)
    assert b"up to 1048576 MB" in client.get("/videos/upload").data
    setting(client, videos_max_upload_mb="2")
    with app.app_context():
        assert get_db().execute("SELECT videos_max_upload_mb FROM station").fetchone()[0] == videos.MAX_UPLOAD_MB_LIMIT


def test_video_migration_is_repeatable_and_preserves_settings():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE station(id INTEGER PRIMARY KEY, videos_default_quota_mb INTEGER)")
    db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
    db.execute("INSERT INTO station VALUES(1,4196)")
    _migrate_videos(db)
    assert tuple(db.execute("SELECT videos_max_upload_mb,videos_default_quota_mb FROM station").fetchone()) == (120, 4196)
    db.execute("UPDATE station SET videos_max_upload_mb=4196")
    _migrate_videos(db)
    assert db.execute("SELECT videos_max_upload_mb FROM station").fetchone()[0] == 4196
    db.close()


@pytest.mark.parametrize("station", [False, True])
def test_exact_limit_and_oversize_cleanup(setup, station):
    app, client, admin, member = setup
    signin(client, admin)
    setting(client, videos_max_upload_mb="1")
    signin(client, admin if station else member)
    data = MP4 + b"x" * (MB - len(MP4))
    assert b"Video exceeds the 1 MB limit" in upload(client, data + b"x", station).data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 0
    assert not list(Path(app.config["DATA_DIR"]).glob("videos/**/*.mp4"))
    assert not list(Path(app.config["DATA_DIR"]).glob("videos/**/*.jpg"))
    accepted = upload(client, data, station)
    assert (b"Station video uploaded." if station else b"Video uploaded.") in accepted.data
    with app.app_context():
        row = get_db().execute("SELECT size_bytes,station_post FROM videos").fetchone()
        assert tuple(row) == (MB, int(station))
    assert len(list(Path(app.config["DATA_DIR"]).glob("videos/**/*.mp4"))) == 1


def test_quota_override_station_exemption_and_existing_media(setup):
    app, client, admin, member = setup
    signin(client, admin)
    setting(client, videos_max_upload_mb="2", videos_default_quota_mb="1")
    signin(client, member)
    data = MP4 + b"x" * (MB + 1 - len(MP4))
    assert b"quota exceeded" in upload(client, data).data
    with app.app_context():
        db = get_db()
        db.execute("UPDATE users SET videos_quota_mb=2 WHERE id=?", (member,))
        db.commit()
    assert b"Video uploaded." in upload(client, data).data
    assert b"quota exceeded" in upload(client, MP4 + b"x" * MB).data
    signin(client, admin)
    assert b"Station video uploaded." in upload(client, data, True).data
    setting(client, videos_max_upload_mb="1")
    assert client.get("/videos/1/media").data == data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 2
    assert len(list(Path(app.config["DATA_DIR"]).glob("videos/**/*.mp4"))) == 2


def test_formats_csrf_and_mode_permissions_are_preserved(setup):
    app, client, admin, member = setup
    signin(client, member)
    assert upload(client, MP4, csrf_token="wrong").status_code == 400
    assert b"not a supported video" in upload(client, b"not a video").data
    assert upload(client, MP4, True).status_code == 403
    signin(client, admin)
    setting(client, videos_max_upload_mb="1", videos_mode="curated")
    signin(client, member)
    assert upload(client, MP4).status_code == 404
    signin(client, admin)
    assert b"Station video uploaded." in upload(client, MP4, True).data
    setting(client, videos_enabled="")
    assert upload(client, MP4, True).status_code == 404


def test_request_limits_are_local_to_video_uploads(setup):
    app, client, admin, member = setup

    @app.after_request
    def expose_limit(response):
        response.headers["X-Test-Limit"] = str(request.max_content_length)
        return response

    signin(client, admin)
    setting(client, videos_max_upload_mb="4196")
    for station in (False, True):
        response = client.post("/videos/station-upload" if station else "/videos/upload",
            data={"csrf_token": TOKEN, "title": "Video", "file": (io.BytesIO(MP4), "test.mp4")})
        assert response.status_code == 302
        assert int(response.headers["X-Test-Limit"]) == 4197 * MB
    assert int(client.get("/videos/upload").headers["X-Test-Limit"]) == 128 * MB
    assert int(client.get("/sysop/settings").headers["X-Test-Limit"]) == 128 * MB


@pytest.mark.parametrize("extra", [0, 1])
def test_sizes_above_four_gib_without_large_allocations(setup, monkeypatch, extra):
    app, client, admin, member = setup
    maximum = 4196 * MB

    class GeneratedVideo:
        def __init__(self):
            self.remaining = maximum + extra

        def read(self, count):
            assert 0 < count <= MB
            count = min(count, self.remaining)
            first = self.remaining == maximum + extra
            self.remaining -= count
            return MP4[:count] if first else b"x" * count

        def close(self):
            pass

    class CountingOutput:
        size = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def write(self, data):
            self.size += len(data)

    output = CountingOutput()
    with app.test_request_context("/videos/upload", method="POST"):
        g.station = {"videos_max_upload_mb": 4196}
        g.user = {"id": member}
        # Exercise the real save loop and database accounting with a bounded input
        # stream and a sink that counts bytes instead of writing several GiB.
        request.files = MultiDict({"file": FileStorage(stream=GeneratedVideo(), filename="large.mp4")})
        request.form = MultiDict({"title": "Large video", "description": ""})
        monkeypatch.setattr(Path, "open", lambda *args, **kwargs: output)
        result = videos._save_upload(True)
        if extra:
            assert result == "Video exceeds the 4196 MB limit."
            assert get_db().execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 0
        else:
            assert isinstance(result, int)
            assert output.size == maximum
            assert get_db().execute("SELECT size_bytes FROM videos").fetchone()[0] == maximum
