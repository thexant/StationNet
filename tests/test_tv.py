"""TV module: switches, permissions, chunked uploads, imports, media ranges and scheduling."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re

import pytest

from stationnet import create_app, tv_library
from stationnet.db import get_db

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8
TOKEN = "tv-test"


def _setup(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign) VALUES('Test','Test','TST-1')")
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) "
                           "VALUES('admin','ADM-1','unused',1)").lastrowid
        member = db.execute("INSERT INTO users(username,callsign,password_hash) "
                            "VALUES('member','MEM-1','unused')").lastrowid
        db.commit()
    return app, app.test_client(), admin, member


def _signin(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = TOKEN


def _post(client, path, body=None):
    return client.post(path, json=body or {}, headers={"X-CSRF-Token": TOKEN})


def _enable(client):
    return client.post("/sysop/settings", data={"csrf_token": TOKEN, "settings_section": "tv", "tv_enabled": "on",
                                                "tv_max_upload_mb": "100", "tv_timezone": "UTC"})


def _upload(client, name, data, relpath=None):
    created = _post(client, "/sysop/tv/api/uploads", {"name": name, "size": len(data), "relpath": relpath or name}).get_json()
    if created["status"] != "uploading":
        return created
    offset, upload_id = 0, created["upload_id"]
    while True:
        chunk = data[offset:offset + tv_library.CHUNK_BYTES]
        result = client.put(f"/sysop/tv/api/uploads/{upload_id}?offset={offset}", data=chunk,
                            headers={"X-CSRF-Token": TOKEN, "Content-Type": "application/octet-stream"}).get_json()
        if result["status"] != "uploading":
            return result
        offset = result["offset"]


def _media_with_duration(client, app, name, seconds, data=None):
    result = _upload(client, name, data or MP4 + name.encode())
    media_id = result["media"]["id"]
    with app.app_context():
        get_db().execute("UPDATE tv_media SET duration_s=NULL WHERE id=?", (media_id,))
        get_db().commit()
    assert _post(client, f"/sysop/tv/api/media/{media_id}/probe", {"duration": seconds, "width": 640, "height": 480}).status_code == 200
    return media_id


def test_tv_is_disabled_by_default_and_toggled_from_settings(tmp_path):
    app, client, admin, _member = _setup(tmp_path)
    _signin(client, admin)
    assert client.get("/tv").status_code == 404
    settings = client.get("/sysop/settings")
    assert b'id="tv-service"' in settings.data and b'href="/tv"' not in settings.data
    assert _enable(client).status_code == 302
    page = client.get("/tv")
    assert page.status_code == 200 and b"data-tv" in page.data and b'href="/tv"' in page.data
    assert b"TUNE IN TO STATION TELEVISION" in client.get("/terminal").data
    bad = client.post("/sysop/settings", data={"csrf_token": TOKEN, "settings_section": "tv", "tv_enabled": "on",
                                               "tv_max_upload_mb": "100", "tv_timezone": "Mars/Olympus"})
    assert bad.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT tv_timezone FROM station").fetchone()[0] == "UTC"


def test_management_requires_sysop_and_csrf(tmp_path):
    _app, client, admin, member = _setup(tmp_path)
    _signin(client, member)
    _enable(client)
    assert client.get("/sysop/tv/api/state").status_code == 403
    assert _post(client, "/sysop/tv/api/channels", {"number": 1, "name": "X"}).status_code == 403
    assert client.get("/sysop/tv").status_code == 302
    _signin(client, admin)
    assert client.get("/sysop/tv").status_code == 200
    assert client.post("/sysop/tv/api/channels", json={"number": 1, "name": "X"}).status_code == 400
    assert _post(client, "/sysop/tv/api/channels", {"number": 1, "name": "X"}).status_code == 200
    assert _post(client, "/sysop/tv/api/channels", {"number": 1, "name": "Y"}).status_code == 400


def test_chunked_upload_resume_duplicates_and_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(tv_library, "CHUNK_BYTES", 16)
    app, client, admin, _member = _setup(tmp_path)
    _signin(client, admin)
    _enable(client)
    data = MP4 + b"programme-body-bytes" * 3
    created = _post(client, "/sysop/tv/api/uploads", {"name": "pilot.mp4", "size": len(data), "relpath": "Shows/S1/pilot.mp4"}).get_json()
    upload_id = created["upload_id"]
    headers = {"X-CSRF-Token": TOKEN, "Content-Type": "application/octet-stream"}
    assert client.put(f"/sysop/tv/api/uploads/{upload_id}?offset=0", data=data[:16], headers=headers).get_json()["offset"] == 16
    mismatch = client.put(f"/sysop/tv/api/uploads/{upload_id}?offset=0", data=data[:16], headers=headers)
    assert mismatch.status_code == 409 and mismatch.get_json()["offset"] == 16
    assert client.get(f"/sysop/tv/api/uploads/{upload_id}").get_json()["offset"] == 16
    offset = 16
    while True:
        result = client.put(f"/sysop/tv/api/uploads/{upload_id}?offset={offset}", data=data[offset:offset + 16], headers=headers).get_json()
        if result["status"] != "uploading":
            break
        offset = result["offset"]
    assert result["status"] == "ok"
    media = result["media"]
    assert media["folder"] == "Shows/S1" and media["title"] == "pilot" and media["source"] == "upload"
    assert (tmp_path / "tv" / media["rel_path"]).read_bytes() == data
    assert not list((tmp_path / "tv" / "incoming").glob(f"{upload_id}*"))

    assert _upload(client, "pilot.mp4", data)["status"] == "duplicate"
    assert _upload(client, "renamed.mp4", data)["status"] == "duplicate"
    assert _upload(client, "notes.txt", b"hello")["status"] == "unsupported"
    assert _upload(client, "fake.mp4", b"not a video at all")["status"] == "unsupported"
    with app.app_context():
        get_db().execute("UPDATE station SET tv_max_upload_mb=1")
        get_db().commit()
    too_big = _post(client, "/sysop/tv/api/uploads", {"name": "big.mp4", "size": 2 * 1024 * 1024}).get_json()
    assert too_big["status"] == "failed"


def test_server_folder_import(tmp_path):
    app, client, admin, _member = _setup(tmp_path)
    _signin(client, admin)
    root = tmp_path / "tv" / "import"
    (root / "Cartoons" / "Season 1").mkdir(parents=True)
    (root / "Cartoons" / "Season 1" / "ep1.mp4").write_bytes(MP4 + b"ep1")
    (root / "Cartoons" / "Season 1" / "ep1-copy.webm").write_bytes(MP4 + b"ep1")
    (root / "readme.txt").write_text("notes")
    (root / "broken.mp4").write_bytes(b"garbage")
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(MP4 + b"outside")
    linked = False
    try:
        os.symlink(outside, root / "escape.mp4")
        linked = True
    except (OSError, NotImplementedError):
        pass

    results = {}
    cursor, done = 0, False
    while not done:
        data = _post(client, "/sysop/tv/api/import/scan", {"cursor": cursor}).get_json()
        results.update({item["path"]: item["status"] for item in data["results"]})
        cursor, done = data["cursor"], data["done"]
    # Identical content is imported once; the second copy is reported as a duplicate.
    assert sorted([results["Cartoons/Season 1/ep1.mp4"], results["Cartoons/Season 1/ep1-copy.webm"]]) == ["duplicate", "ok"]
    assert results["readme.txt"] == "unsupported"
    assert results["broken.mp4"] == "unsupported"
    if linked:
        assert results["escape.mp4"] == "skipped"
    with app.app_context():
        row = get_db().execute("SELECT * FROM tv_media WHERE source='import'").fetchone()
        assert row["folder"] == "Cartoons/Season 1" and row["rel_path"].startswith("import/Cartoons/Season 1/ep1")
    imported = row["rel_path"].removeprefix("import/")
    again = _post(client, "/sysop/tv/api/import/scan", {"cursor": 0}).get_json()
    assert {item["path"]: item["status"] for item in again["results"]}[imported] == "known"

    assert _post(client, f"/sysop/tv/api/media/{row['id']}/delete").get_json()["file_kept"] is True
    assert (root / imported).is_file()


def test_media_ranges_are_clamped_and_missing_files_stand_by(tmp_path, monkeypatch):
    app, client, admin, _member = _setup(tmp_path)
    _signin(client, admin)
    _enable(client)
    data = MP4 + bytes(range(256)) * 4
    media = _upload(client, "clip.mp4", data)["media"]
    monkeypatch.setattr(tv_library, "CHUNK_BYTES", 100)
    ranged = client.get(f"/tv/media/{media['id']}", headers={"Range": "bytes=10-"})
    assert ranged.status_code == 206
    assert ranged.headers["Content-Range"] == f"bytes 10-109/{len(data)}"
    assert ranged.data == data[10:110]
    tail = client.get(f"/tv/media/{media['id']}", headers={"Range": "bytes=-20"})
    assert tail.data == data[-20:]
    assert client.get(f"/tv/media/{media['id']}", headers={"Range": f"bytes={len(data)}-"}).status_code == 416
    plain = client.get(f"/tv/media/{media['id']}")
    assert plain.status_code == 206 and len(plain.data) == 100
    (tmp_path / "tv" / media["rel_path"]).unlink()
    assert client.get(f"/tv/media/{media['id']}").status_code == 404
    with app.app_context():
        assert get_db().execute("SELECT missing FROM tv_media WHERE id=?", (media["id"],)).fetchone()[0] == 1


def test_schedule_now_playing_guide_and_safe_deletion(tmp_path):
    app, client, admin, member = _setup(tmp_path)
    _signin(client, admin)
    _enable(client)
    show = _media_with_duration(client, app, "show.mp4", 3600)
    short = _media_with_duration(client, app, "short.mp4", 600)
    channel = _post(client, "/sysop/tv/api/channels", {"number": 106, "name": "Station One"}).get_json()["id"]
    start = (datetime.now(timezone.utc) - timedelta(minutes=35)).replace(second=0, microsecond=0)
    block = _post(client, "/sysop/tv/api/blocks", {
        "channel_id": channel, "kind": "once", "start_date": start.date().isoformat(),
        "start_time": start.strftime("%H:%M"), "items": [{"media_id": show}, {"media_id": short}]})
    assert block.status_code == 200, block.get_json()
    block_id = block.get_json()["id"]

    _signin(client, member)
    now = client.get(f"/tv/api/now?channel={channel}").get_json()
    assert now["airing"]["title"] == "show" and now["airing"]["kind"] == "program"
    assert 35 * 60 <= now["airing"]["offset"] < 37 * 60
    assert now["airing"]["media_url"] == f"/tv/media/{show}"
    assert now["next"]["title"] == "short"
    guide = client.get(f"/tv/api/guide?start={now['server_now']}&hours=1.5").get_json()
    titles = [airing["title"] for airing in guide["channels"][0]["airings"]]
    assert "show" in titles and guide["channels"][0]["number"] == 106
    assert guide["channels"][0]["airings"][titles.index("show")]["start"].startswith(start.strftime("%Y-%m-%dT%H:%M"))

    _signin(client, admin)
    day = client.get(f"/sysop/tv/api/schedule?channel={channel}&date={start.date().isoformat()}").get_json()
    assert day["blocks"][0]["airs_on_day"] and day["blocks"][0]["total_duration"] == 4200
    blocked = _post(client, f"/sysop/tv/api/media/{show}/delete")
    assert blocked.status_code == 409 and blocked.get_json()["usage"]
    unmeasured = _upload(client, "unmeasured.mp4", MP4 + b"unmeasured")["media"]["id"]
    with app.app_context():
        get_db().execute("UPDATE tv_media SET duration_s=NULL WHERE id=?", (unmeasured,))
        get_db().commit()
    rejected = _post(client, "/sysop/tv/api/blocks", {"channel_id": channel, "kind": "weekly", "weekdays": 1,
                                                      "start_time": "10:00", "items": [{"media_id": unmeasured}]})
    assert rejected.status_code == 400 and "duration" in rejected.get_json()["error"]
    assert _post(client, f"/sysop/tv/api/blocks/{block_id}/delete").status_code == 200
    media = {item["id"]: item for item in client.get("/sysop/tv/api/state").get_json()["media"]}
    path = tmp_path / "tv" / media[show]["rel_path"]
    assert path.is_file()
    assert _post(client, f"/sysop/tv/api/media/{show}/delete").status_code == 200
    assert not path.exists()

    _signin(client, member)
    offair = client.get(f"/tv/api/now?channel={channel}").get_json()
    assert offair["airing"]["kind"] == "offair" and offair["airing"]["media_url"] is None


def test_tv_css_uses_theme_tokens_not_hardcoded_colours():
    static = Path(__file__).resolve().parents[1] / "stationnet" / "static"
    for name in ("tv.css", "tv-admin.css"):
        css = (static / name).read_text(encoding="utf-8")
        for colour in re.findall(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", css):
            digits = colour if len(colour) == 6 else "".join(c * 2 for c in colour)
            red, green, blue = (digits[i:i + 2] for i in (0, 2, 4))
            assert red == green == blue, f"{name} hard-codes a tinted colour #{colour}; use a theme token"
        assert "var(--green)" in css and "var(--surface-selected)" in css
