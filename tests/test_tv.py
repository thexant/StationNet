"""TV module: switches, permissions, chunked uploads, imports, media ranges and scheduling."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from unittest.mock import Mock

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


def _scan_all(client):
    results, cursor, done = {}, 0, False
    while not done:
        data = _post(client, "/sysop/tv/api/import/scan", {"cursor": cursor}).get_json()
        results.update({item["path"]: item["status"] for item in data["results"]})
        cursor, done = data["cursor"], data["done"]
    return results


def _library(client):
    return {item["id"]: item for item in client.get("/sysop/tv/api/state").get_json()["media"]}


def test_bulk_edit_and_delete(tmp_path):
    app, client, admin, member = _setup(tmp_path)
    _signin(client, admin)
    first = _media_with_duration(client, app, "a.mp4", 60)
    second = _media_with_duration(client, app, "b.mp4", 60)
    scheduled = _media_with_duration(client, app, "c.mp4", 60)
    with app.app_context():
        before = get_db().execute("SELECT version FROM tv_state").fetchone()[0]
    assert _post(client, "/sysop/tv/api/media/bulk", {"ids": [first, second], "kind": "comet"}).status_code == 400
    assert _post(client, "/sysop/tv/api/media/bulk", {"ids": [first]}).status_code == 400
    result = _post(client, "/sysop/tv/api/media/bulk", {"ids": [first, second, first, 9999], "kind": "bumper",
                                                        "folder": "Breaks/../Night"})
    assert result.get_json() == {"updated": 2}
    media = _library(client)
    assert [media[first]["kind"], media[second]["kind"], media[scheduled]["kind"]] == ["bumper", "bumper", "program"]
    assert media[first]["folder"] == "Breaks/Night" and media[first]["description"] == ""
    with app.app_context():
        assert get_db().execute("SELECT version FROM tv_state").fetchone()[0] > before

    channel = _post(client, "/sysop/tv/api/channels", {"number": 1, "name": "One"}).get_json()["id"]
    assert _post(client, "/sysop/tv/api/blocks", {"channel_id": channel, "kind": "weekly", "weekdays": 1,
                                                  "start_time": "10:00", "items": [{"media_id": scheduled}]}).status_code == 200
    path = tmp_path / "tv" / media[first]["rel_path"]
    deleted = _post(client, "/sysop/tv/api/media/bulk-delete", {"ids": [first, second, scheduled]}).get_json()
    assert deleted["deleted"] == 2 and deleted["kept_files"] == 0 and deleted["failed"] == []
    assert [entry["id"] for entry in deleted["blocked"]] == [scheduled] and deleted["blocked"][0]["usage"]
    assert not path.exists() and set(_library(client)) == {scheduled}

    _signin(client, member)
    assert _post(client, "/sysop/tv/api/media/bulk-delete", {"ids": [scheduled]}).status_code == 403
    _signin(client, admin)
    assert client.post("/sysop/tv/api/media/bulk", json={"ids": [scheduled], "kind": "ident"}).status_code == 400


def test_folder_kinds_rules_and_rename(tmp_path):
    app, client, admin, _member = _setup(tmp_path)
    _signin(client, admin)
    root = tmp_path / "tv" / "import"
    (root / "Breaks" / "Night").mkdir(parents=True)
    (root / "Breaks" / "one.mp4").write_bytes(MP4 + b"one")
    (root / "Breaks" / "Night" / "two.mp4").write_bytes(MP4 + b"two")
    (root / "Breakfast").mkdir()
    (root / "Breakfast" / "show.mp4").write_bytes(MP4 + b"show")
    _scan_all(client)
    by_name = lambda: {item["original_name"]: item for item in _library(client).values()}

    assert _post(client, "/sysop/tv/api/folders/kind", {"folder": "Breaks", "kind": "nope"}).status_code == 400
    assert _post(client, "/sysop/tv/api/folders/kind", {"folder": "Breaks", "kind": "ident"}).get_json() == {"updated": 1}
    assert [by_name()[name]["kind"] for name in ("one.mp4", "two.mp4")] == ["ident", "program"]
    assert _post(client, "/sysop/tv/api/folders/kind", {"folder": "Breaks", "kind": "bumper", "recursive": True,
                                                        "remember": True}).get_json() == {"updated": 2}
    media = by_name()
    assert media["one.mp4"]["kind"] == media["two.mp4"]["kind"] == "bumper" and media["show.mp4"]["kind"] == "program"
    state = client.get("/sysop/tv/api/state").get_json()
    assert state["folder_rules"] == [{"folder": "Breaks", "kind": "bumper", "recursive": True}]

    # New files below a remembered folder arrive with its type; a sibling with a shared prefix does not.
    (root / "Breaks" / "Night" / "three.mp4").write_bytes(MP4 + b"three")
    (root / "Breakfast" / "four.mp4").write_bytes(MP4 + b"four")
    _scan_all(client)
    media = by_name()
    assert media["three.mp4"]["kind"] == "bumper" and media["four.mp4"]["kind"] == "program"
    assert _upload(client, "five.mp4", MP4 + b"five", "Breaks/five.mp4")["media"]["kind"] == "bumper"

    assert _post(client, "/sysop/tv/api/folders/rename", {"from": "Breaks", "to": "Breaks/Inner"}).status_code == 400
    assert _post(client, "/sysop/tv/api/folders/rename", {"from": "Breaks", "to": "Interstitials"}).get_json()["updated"] == 4
    media = by_name()
    assert media["two.mp4"]["folder"] == "Interstitials/Night" and media["show.mp4"]["folder"] == "Breakfast"
    assert client.get("/sysop/tv/api/state").get_json()["folder_rules"][0]["folder"] == "Interstitials"
    assert _post(client, "/sysop/tv/api/folders/rule/delete", {"folder": "Interstitials"}).status_code == 200
    assert client.get("/sysop/tv/api/state").get_json()["folder_rules"] == []


def test_bulk_block_actions(tmp_path):
    app, client, admin, _member = _setup(tmp_path)
    _signin(client, admin)
    show = _media_with_duration(client, app, "show.mp4", 600)
    channel = _post(client, "/sysop/tv/api/channels", {"number": 1, "name": "One"}).get_json()["id"]
    blocks = [_post(client, "/sysop/tv/api/blocks", {"channel_id": channel, "kind": "weekly", "weekdays": 127,
                                                     "start_time": f"{hour:02d}:00", "items": [{"media_id": show}]}).get_json()["id"]
              for hour in (8, 9, 10)]
    assert _post(client, "/sysop/tv/api/blocks/bulk", {"ids": blocks, "action": "explode"}).status_code == 400
    assert _post(client, "/sysop/tv/api/blocks/bulk", {"ids": blocks[:2], "action": "disable"}).get_json() == {"updated": 2}
    day = client.get(f"/sysop/tv/api/schedule?channel={channel}&date=2026-01-05").get_json()
    assert {block["id"]: block["enabled"] for block in day["blocks"]} == {blocks[0]: 0, blocks[1]: 0, blocks[2]: 1}
    assert _post(client, "/sysop/tv/api/blocks/bulk", {"ids": blocks[1:], "action": "delete"}).get_json() == {"updated": 2}
    day = client.get(f"/sysop/tv/api/schedule?channel={channel}&date=2026-01-05").get_json()
    assert [block["id"] for block in day["blocks"]] == [blocks[0]]


def test_media_metadata_bulk_autonumber_and_episode_guessing(tmp_path):
    app, client, admin, _member = _setup(tmp_path)
    _signin(client, admin)
    guessed = _upload(client, "Moon.Base.S02E05.Lift.Off.mp4", MP4 + b"ep5")["media"]
    assert (guessed["series"], guessed["season"], guessed["episode"], guessed["content_type"]) == ("Moon Base", 2, 5, "show")
    ids = [_upload(client, f"part {n}.mp4", MP4 + bytes([n]))["media"]["id"] for n in (10, 9, 2)]
    assert _post(client, f"/sysop/tv/api/media/{ids[0]}", {"title": "part 10", "kind": "program",
                                                         "content_type": "nope"}).status_code == 400
    assert _post(client, f"/sysop/tv/api/media/{ids[0]}", {"title": "part 10", "kind": "commercial",
                                                         "genre": " Sci  Fi "}).status_code == 200
    result = _post(client, "/sysop/tv/api/media/bulk", {"ids": ids, "kind": "program", "content_type": "miniseries",
                                                        "series": "Deep Field", "season": 1, "autonumber": True})
    assert result.status_code == 200, result.get_json()
    media = {item["id"]: item for item in client.get("/sysop/tv/api/state").get_json()["media"]}
    assert [media[i]["episode"] for i in ids] == [3, 2, 1]
    assert media[ids[0]]["genre"] == "Sci Fi" and media[ids[0]]["content_type"] == "miniseries"


def test_auto_scheduler_generate_publish_lock_and_regenerate(tmp_path):
    app, client, admin, member = _setup(tmp_path)
    _signin(client, admin)
    _enable(client)
    episodes = [_media_with_duration(client, app, f"Rover S01E0{n}.mp4", 22 * 60) for n in range(1, 5)]
    movie = _media_with_duration(client, app, "feature.mp4", 95 * 60)
    ads = [_media_with_duration(client, app, f"ad{n}.mp4", 30) for n in range(3)]
    ident = _media_with_duration(client, app, "ident.mp4", 10)
    assert _post(client, "/sysop/tv/api/media/bulk", {"ids": ads, "kind": "commercial"}).status_code == 200
    assert _post(client, "/sysop/tv/api/media/bulk", {"ids": [ident], "kind": "ident"}).status_code == 200
    assert _post(client, "/sysop/tv/api/media/bulk", {"ids": [movie], "content_type": "movie"}).status_code == 200
    channel = _post(client, "/sysop/tv/api/channels", {"number": 7, "name": "Auto"}).get_json()["id"]
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    manual = _post(client, "/sysop/tv/api/blocks", {"channel_id": channel, "kind": "once", "start_date": tomorrow,
                                                    "start_time": "12:00", "items": [{"media_id": movie}]}).get_json()["id"]

    _signin(client, member)
    assert client.get(f"/sysop/tv/api/auto/{channel}").status_code == 403
    _signin(client, admin)
    assert client.post(f"/sysop/tv/api/auto/{channel}/generate", json={}).status_code == 400
    assert _post(client, f"/sysop/tv/api/auto/{channel}/config", {"commercials": "loud"}).status_code == 400
    saved = _post(client, f"/sysop/tv/api/auto/{channel}/config",
                  {"on_air_from": "08:00", "on_air_until": "20:00", "days": 1, "mix": {"show": 70, "movie": 30}})
    assert saved.status_code == 200 and saved.get_json()["config"]["on_air_until"] == "20:00"
    state = client.get(f"/sysop/tv/api/auto/{channel}").get_json()
    assert state["library"]["types"] == {"show": 4, "movie": 1} and state["draft"] is None

    draft = _post(client, f"/sysop/tv/api/auto/{channel}/generate", {"start_date": tomorrow}).get_json()["draft"]
    assert draft["slots"] and draft["warnings"]
    assert any(entry["status"] == "manual" for entry in draft["fixed"])
    noon = f"{tomorrow}T12:00:00.000Z"
    for slot in draft["slots"]:
        assert slot["start"] >= f"{tomorrow}T08:00" and slot["end"] <= f"{tomorrow}T20:00:00.000Z"
        assert not (slot["start"] < f"{tomorrow}T13:35:00.000Z" and slot["end"] > noon)
    rerolled = _post(client, f"/sysop/tv/api/auto/{channel}/generate", {"start_date": tomorrow}).get_json()["draft"]
    assert rerolled["slots"]
    published = _post(client, f"/sysop/tv/api/auto/{channel}/publish").get_json()
    assert published["published"] == len(rerolled["slots"]) and published["replaced"] == 0
    assert client.get(f"/sysop/tv/api/auto/{channel}").get_json()["draft"] is None

    day = client.get(f"/sysop/tv/api/schedule?channel={channel}&date={tomorrow}").get_json()
    generated = [block for block in day["blocks"] if block["origin"] == "auto"]
    assert len(generated) == published["published"]
    assert next(block for block in day["blocks"] if block["id"] == manual)["origin"] == "manual"
    first_episode = next(block for block in generated if block["items"][0]["media_id"] in episodes)
    assert first_episode["loop_until"] and any(item["media_id"] in ads for item in first_episode["items"])

    _signin(client, member)
    guide = client.get(f"/tv/api/guide?start={tomorrow}T08:00:00Z&hours=6").get_json()
    titles = [airing["title"] for airing in guide["channels"][0]["airings"]]
    assert not any(title.startswith("ad") for title in titles), titles

    _signin(client, admin)
    locked, edited = generated[0], generated[1]
    assert _post(client, f"/sysop/tv/api/blocks/{locked['id']}/lock", {"locked": True}).status_code == 200
    body = {key: edited[key] for key in ("channel_id", "title", "kind", "start_date", "start_time", "loop_until")}
    body["items"] = [{"media_id": item["media_id"]} for item in edited["items"]]
    assert _post(client, f"/sysop/tv/api/blocks/{edited['id']}", body).status_code == 200
    _post(client, f"/sysop/tv/api/auto/{channel}/generate", {"start_date": tomorrow})
    assert _post(client, f"/sysop/tv/api/media/{ident}", {"title": "ident", "kind": "ident"}).status_code == 200
    assert _post(client, f"/sysop/tv/api/auto/{channel}/publish").status_code == 409
    again = _post(client, f"/sysop/tv/api/auto/{channel}/generate", {"start_date": tomorrow}).get_json()["draft"]
    assert again["replaced"] == len(generated) - 2
    assert {"locked"} <= {entry["status"] for entry in again["fixed"]}
    assert _post(client, f"/sysop/tv/api/auto/{channel}/publish").status_code == 200
    blocks = {block["id"]: block for block in
              client.get(f"/sysop/tv/api/schedule?channel={channel}&date={tomorrow}").get_json()["blocks"]}
    assert locked["id"] in blocks and edited["id"] in blocks and manual in blocks
    assert blocks[edited["id"]]["locked"] == 1

    cleared = _post(client, f"/sysop/tv/api/auto/{channel}/clear", {"start_date": tomorrow, "days": 1}).get_json()
    assert cleared["removed"] > 0
    remaining = client.get(f"/sysop/tv/api/schedule?channel={channel}&date={tomorrow}").get_json()["blocks"]
    assert {block["id"] for block in remaining} == {locked["id"], edited["id"], manual}


def test_station_activity_lists_scheduled_channels_lowest_first(tmp_path):
    app, client, admin, member = _setup(tmp_path)
    _signin(client, admin)
    _enable(client)
    show = _media_with_duration(client, app, "show.mp4", 3600)
    short = _media_with_duration(client, app, "short.mp4", 600)
    start = (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(second=0, microsecond=0)
    channels = {}
    for number in (7, 4, 2):
        channels[number] = _post(client, "/sysop/tv/api/channels",
                                 {"number": number, "name": f"Channel {number}"}).get_json()["id"]
    for number in (7, 2):
        assert _post(client, "/sysop/tv/api/blocks", {
            "channel_id": channels[number], "kind": "once", "start_date": start.date().isoformat(),
            "start_time": start.strftime("%H:%M"),
            "items": [{"media_id": show}, {"media_id": short}]}).status_code == 200

    with app.test_request_context():
        from flask import g
        from stationnet import tv
        g.station = get_db().execute("SELECT * FROM station").fetchone()
        listings = tv.activity_listings()
    assert [entry["number"] for entry in listings] == [2, 7]
    assert listings[0]["now"]["title"] == "show" and listings[0]["now"]["kind"] == "program"
    assert listings[0]["next"]["title"] == "short" and not listings[0]["next"]["far"]

    _signin(client, member)
    page = client.get("/terminal").get_data(as_text=True)
    assert "TV LISTINGS" in page and "CH 2 / Channel 2" in page and "Channel 4" not in page
    assert "data-activity-broadcast-toggle" not in page.split("TV LISTINGS", 1)[1]


def test_terminal_tv_listings_refresh_after_schedule_and_media_edits(tmp_path, monkeypatch):
    from stationnet import tv

    app, client, admin, _member = _setup(tmp_path)
    _signin(client, admin)
    _enable(client)
    show = _media_with_duration(client, app, "show.mp4", 1800)
    other = _media_with_duration(client, app, "other.mp4", 1800)
    channel = _post(client, "/sysop/tv/api/channels", {"number": 1, "name": "Test channel"}).get_json()["id"]
    now = datetime(2026, 11, 6, 12, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(tv, "_now", lambda: now)
    block = {"channel_id": channel, "kind": "once", "start_date": "2026-11-06", "start_time": "12:00",
             "items": [{"media_id": show}, {"media_id": other}]}
    block_id = _post(client, "/sysop/tv/api/blocks", block).get_json()["id"]
    resolve = Mock(wraps=tv_library.resolve)
    monkeypatch.setattr(tv_library, "resolve", resolve)

    cold = client.get("/terminal")
    assert cold.status_code == 200 and b"<em>show</em>" in cold.data
    assert resolve.call_count == 2
    warm = client.get("/terminal")
    assert warm.status_code == 200 and b"<em>show</em>" in warm.data
    assert resolve.call_count == 2

    block["items"].reverse()
    assert _post(client, f"/sysop/tv/api/blocks/{block_id}", block).status_code == 200
    resolve.reset_mock()
    changed_schedule = client.get("/terminal")
    assert changed_schedule.status_code == 200 and b"<em>other</em>" in changed_schedule.data
    assert resolve.call_count == 2
    assert _post(client, f"/sysop/tv/api/media/{other}", {"title": "Updated programme"}).status_code == 200
    resolve.reset_mock()
    changed_media = client.get("/terminal")
    assert changed_media.status_code == 200 and b"<em>Updated programme</em>" in changed_media.data
    assert resolve.call_count == 2


@pytest.mark.parametrize("authenticated,tv_enabled", [(False, True), (True, False)])
def test_terminal_skips_tv_listings_when_not_displayed(tmp_path, monkeypatch, authenticated, tv_enabled):
    from stationnet import tv

    app, client, _admin, member = _setup(tmp_path)
    with app.app_context():
        get_db().execute("UPDATE station SET tv_enabled=?", (tv_enabled,))
        get_db().commit()
    if authenticated:
        _signin(client, member)
    listings = Mock(side_effect=AssertionError("TV listings should not be computed"))
    monkeypatch.setattr(tv, "activity_listings", listings)
    page = client.get("/terminal")
    assert page.status_code == 200 and b"TV LISTINGS" not in page.data
    listings.assert_not_called()
