"""Continuous external broadcasts share the radio arbiter, not playlist timers."""
from io import BytesIO

import pytest

from stationnet import create_app
from stationnet.db import get_db
from stationnet.external_radio import _playlist_target, open_audio_stream, validate_stream_url


def test_playlist_resolution_and_private_origin_rejection():
    assert _playlist_target(b"#EXTM3U\n#EXTINF:-1,Origin FM\n/live.mp3\n",
                            "https://example.com/radio/list.m3u", "audio/x-mpegurl") == \
           "https://example.com/live.mp3"
    assert _playlist_target(b"[playlist]\nFile1=https://example.com/live.aac\n",
                            "https://example.com/list.pls", "audio/x-scpls") == \
           "https://example.com/live.aac"
    assert _playlist_target(b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=123\n/stream.m3u8\n",
                            "https://example.com/master.m3u8", "application/vnd.apple.mpegurl") == \
           "https://example.com/master.m3u8"
    with pytest.raises(ValueError):
        validate_stream_url("http://127.0.0.1/private")
    with pytest.raises(ValueError):
        validate_stream_url("http://user:secret@example.com/live")


def test_stream_mime_detection_and_rejected_pages(monkeypatch):
    class Headers:
        def __init__(self, kind):
            self.kind = kind

        def get_content_type(self):
            return self.kind

    class Upstream(BytesIO):
        def __init__(self, kind, data):
            super().__init__(data)
            self.headers = Headers(kind)

        def geturl(self):
            return "https://example.com/live"

    class Opener:
        def __init__(self, upstream):
            self.upstream = upstream

        def open(self, *_args, **_kwargs):
            return self.upstream

    monkeypatch.setattr("stationnet.external_radio.inspect_stream", lambda url: {"url": url, "hls": False})
    monkeypatch.setattr("stationnet.external_radio.validate_stream_url", lambda url: url)
    upstream = Upstream("application/octet-stream", b"\xff\xf1\x50\x80aac-data")
    monkeypatch.setattr("stationnet.external_radio._opener", lambda: Opener(upstream))
    response, mime, prefix = open_audio_stream("https://example.com/live")
    assert mime == "audio/aac" and prefix + response.read() == b"\xff\xf1\x50\x80aac-data"
    response.close()
    page = Upstream("text/html", b"<html>offline</html>")
    monkeypatch.setattr("stationnet.external_radio._opener", lambda: Opener(page))
    with pytest.raises(ValueError, match="instead of audio"):
        open_audio_stream("https://example.com/live")
    assert page.closed


def test_external_broadcast_management_and_continuous_playback(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    with app.app_context():
        database = get_db()
        database.execute("INSERT INTO station(id,name,description,callsign) VALUES(1,'Test','','TST-001')")
        database.execute("INSERT INTO users(id,username,callsign,password_hash,is_admin) "
                         "VALUES(1,'sysop','SYS-001','test',1)")
        database.execute("INSERT INTO radio_group_memberships(user_id,group_id) "
                         "SELECT 1,id FROM radio_permission_groups WHERE name='OPERATORS'")
        database.commit()

    monkeypatch.setattr("stationnet.broadcast.validate_stream_url", lambda url, resolve=False: url)
    monkeypatch.setattr("stationnet.broadcast.inspect_stream", lambda url, metadata=False: {
        "url": "https://example.com/live.mp3", "station": "Origin FM",
        "title": "Current song", "artist": "Current artist", "hls": False,
    })
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1, csrf_token="token")
    form = {"csrf_token": "token", "name": "", "message": "Live from afar",
            "frequency": "7.10", "stream_url": "https://example.com/list.m3u"}
    assert client.post("/radio/manage/automated/external", data=form).status_code == 302
    page = client.get("/radio/manage/automated")
    assert b"Origin FM" not in page.data  # Metadata appears when on air.
    assert b"Create external broadcast" in page.data
    assert b"Loop Indefinitely" not in page.data  # No local session is running.
    assert client.post("/radio/manage/automated/external/1/control",
                       data={"csrf_token": "token", "action": "start"}).status_code == 302
    directory = client.get("/radio/manage/status").get_json()["directory"]
    assert directory == [{
        "broadcasting": True,
        "callsign": "STATION AUTOMATION",
        "frequency": "7.10",
        "message": "Live from afar",
        "name": "Origin FM",
        "now_playing": "Current song",
        "user_id": None,
        "username": None,
    }]
    with app.app_context():
        transmission_id = app.extensions["hosted_broadcasts"].public("external:1")["transmission_id"]
    assert client.get(f"/radio/external/{transmission_id}/audio").status_code == 403
    assert client.post("/radio/tune", data={"csrf_token": "token", "frequency": "7.10"}).status_code == 200
    upstream = BytesIO(b"\xff\xfb" + b"radio" * 100)
    monkeypatch.setattr("stationnet.radio.open_audio_stream", lambda url: (upstream, "audio/mpeg", b""))
    relay = client.get(f"/radio/external/{transmission_id}/audio", buffered=False)
    assert relay.status_code == 200 and relay.mimetype == "audio/mpeg"
    assert "no-store" in relay.headers["Cache-Control"]
    assert next(relay.response).startswith(b"\xff\xfb")
    relay.close()
    assert upstream.closed
    assert app.test_client().get(f"/radio/external/{transmission_id}/audio").status_code == 302
    upstream = BytesIO(b"\xff\xfb" + b"radio" * 100)
    relay = client.get(f"/radio/external/{transmission_id}/audio", buffered=False)
    assert next(relay.response).startswith(b"\xff\xfb")
    assert client.post("/radio/tune", data={"csrf_token": "token", "frequency": "7.20"}).status_code == 200
    assert list(relay.response) == []
    relay.close()
    assert upstream.closed
    with app.app_context():
        store = app.extensions["hosted_broadcasts"]
        radio = app.extensions["radio"]
        assert store.public("external:1")["track_artist"] == "Current artist"
        assert "external:1" not in store._sessions
        assert "external:1" not in store._waiting
        assert radio.current(7100)["external_url"] == "https://example.com/live.mp3"
        assert not store.set_indefinite("external:1", True)
        local = store.start(1, "SYS-001", 7200, 1, "Local playlist",
                            [{"entry_id": 1, "file_id": 1, "name": "local.mp3",
                              "title": "Local track", "artist": "Local artist", "duration": 120}],
                            False, 60, permission_user_id=1)
        assert local["indefinite"] is False
        assert store.set_indefinite(1, True)
        assert store.public(1)["indefinite"] is True
        assert radio.current(7200)["audio_file_id"] == 1
        # The playlist clock's expiry path cannot affect the continuous source.
        store.refresh()
        assert radio.current(7100)["external"] is True
        store.stop(1)
    conflict = {**form, "frequency": "7.10"}
    assert client.post("/radio/manage/automated/external", data=conflict).status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM radio_external_broadcasts").fetchone()[0] == 1
    assert client.post("/radio/manage/automated/external/1/control",
                       data={"csrf_token": "token", "action": "stop"}).status_code == 302
    with app.app_context():
        assert app.extensions["radio"].current(7100) is None
    assert client.get(f"/radio/external/{transmission_id}/audio").status_code == 404
