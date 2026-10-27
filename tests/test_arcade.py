"""Arcade catalogue, upload permissions, offline assets, and core selection."""
import base64
import hashlib
from io import BytesIO
import json
import tarfile
import zipfile

from PIL import Image

from stationnet import create_app
from stationnet.arcade import ASSETS, AVAILABLE_CORES, DEFAULT_CORES, PLATFORMS
from stationnet.account_deletion import delete_account
from stationnet.db import get_db


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
        session["csrf_token"] = "arcade-test"


def _upload(client, title, platform, genre, *, station=False, cover=False):
    rom_data = b"NES\x1a" + b"\0" * 100
    rom_name = title + {"gb": ".gb", "gbc": ".gbc", "gba": ".gba"}.get(platform, ".nes")
    if platform == "arcade":
        archive = BytesIO()
        with zipfile.ZipFile(archive, "w") as rom_set:
            rom_set.writestr("game.rom", b"test arcade rom")
        rom_data, rom_name = archive.getvalue(), title + ".zip"
    payload = {"csrf_token": "arcade-test", "title": title, "platform": platform,
               "genre": genre, "description": "A test game", "release_year": "1992",
               "developer": "Test Studio", "rom": (BytesIO(rom_data), rom_name)}
    if cover:
        image = Image.new("RGB", (64, 80), "red")
        data = BytesIO()
        image.save(data, "PNG")
        data.seek(0)
        payload["cover"] = (data, "cover.png")
    return client.post("/arcade/station-upload" if station else "/arcade/upload",
                       data=payload, content_type="multipart/form-data")


def test_default_cores_are_bundled_for_offline_play():
    bundled_defaults = {core for platform, core in DEFAULT_CORES.items() if platform != "arcade"}
    for core in bundled_defaults:
        for suffix in ("-wasm.data", "-legacy-wasm.data"):
            assert (ASSETS / "cores" / (core + suffix)).is_file()
    for asset in ("loader.js", "emulator.css", "src/emulator.js",
                  "src/gamepad.js", "compression/extractzip.js"):
        assert (ASSETS / asset).is_file()


def test_mame_arcade_platform_offers_downloadable_cores():
    assert PLATFORMS["arcade"][0] == "MAME / Arcade"
    assert PLATFORMS["arcade"][2] == (".zip",)
    assert {"mame2003_plus", "mame2003", "fbneo"}.issubset(AVAILABLE_CORES)


def test_catalogue_filters_metadata_and_permissions(tmp_path):
    app, client, admin, member = _setup(tmp_path)
    _signin(client, admin)
    assert client.get("/arcade/").status_code == 404
    saved = client.post("/sysop/settings", data={"csrf_token": "arcade-test",
                        "settings_section": "arcade", "arcade_enabled": "on",
                        "arcade_mode": "community", "arcade_default_quota_mb": "512"})
    assert saved.status_code == 302
    _signin(client, member)
    for title, platform, genre in (("A Game", "gb", "Puzzle"),
                                   ("B Game", "gbc", "Action"),
                                   ("C Game", "gba", "Puzzle"),
                                   ("D Game", "nes", "Action")):
        assert _upload(client, title, platform, genre, cover=(title == "A Game")).status_code == 302
    catalogue = client.get("/arcade/").get_data(as_text=True)
    assert all(title in catalogue for title in ("A Game", "B Game", "C Game", "D Game"))
    filtered = client.get("/arcade/?platform=gb&platform=gbc&platform=gba&genre=Puzzle")
    html = filtered.get_data(as_text=True)
    assert "A Game" in html and "C Game" in html
    assert "B Game" not in html and "D Game" not in html
    assert client.get("/arcade/1/cover").mimetype == "image/jpeg"
    edited = client.post("/arcade/1/edit", data={"csrf_token": "arcade-test",
                         "title": "A Game", "platform": "gb", "genre": "Puzzle",
                         "description": "Updated description", "release_year": "1992",
                         "developer": "Test Studio"})
    assert edited.status_code == 302
    game = client.get("/arcade/1").get_data(as_text=True)
    assert "Test Studio" in game and "1992" in game and "Updated description" in game
    assert "data-player-url" in game and "data-play" in game
    assert "arcade-fullscreen" not in game
    player = client.get("/arcade/1/player")
    assert player.status_code == 200
    assert b'EJS_core = "gambatte"' in player.data
    assert b'EJS_gameUrl = "/arcade/1/rom"' in player.data
    assert b"'mame2003-plus_skip_warnings': 'enabled'" in player.data
    assert b"'mame2003-plus_skip_disclaimer': 'enabled'" in player.data
    assert player.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "connect-src 'self'" in player.headers["Content-Security-Policy"]
    assert client.get("/arcade/emulatorjs/data/loader.js").status_code == 200
    assert client.get("/arcade/emulatorjs/data/cores/gambatte-wasm.data").status_code == 200
    assert client.get("/arcade/emulatorjs/data/cores/reports/gambatte.json").status_code == 200
    assert client.get("/arcade/emulatorjs/data/cores/../../secret.key").status_code == 404
    assert client.post("/arcade/station-upload", data={"csrf_token": "arcade-test"}).status_code == 403
    _signin(client, admin)
    client.post(f"/arcade/quota/{member}", data={"csrf_token": "arcade-test", "arcade_quota_mb": "1"})
    _signin(client, member)
    over_quota = client.post("/arcade/upload", data={"csrf_token": "arcade-test",
                             "title": "Too Big", "platform": "nes",
                             "rom": (BytesIO(b"NES\x1a" + b"\0" * 1100000), "large.nes")},
                             content_type="multipart/form-data", follow_redirects=True)
    assert b"quota is full" in over_quota.data
    _signin(client, admin)
    client.post("/sysop/settings", data={"csrf_token": "arcade-test",
                "settings_section": "arcade", "arcade_enabled": "on",
                "arcade_mode": "curated", "arcade_default_quota_mb": "512"})
    _signin(client, member)
    assert client.get("/arcade/upload").status_code == 404
    assert client.get("/arcade/1").status_code == 200
    _signin(client, admin)
    assert _upload(client, "Station Game", "nes", "Action", station=True).status_code == 302
    with app.app_context():
        record = get_db().execute("SELECT stored_name,cover_name FROM arcade_games WHERE id=1").fetchone()
    assert client.post("/arcade/1/delete", data={"csrf_token": "arcade-test"}).status_code == 302
    assert not (tmp_path / "arcade" / "roms" / record["stored_name"]).exists()
    assert not (tmp_path / "arcade" / "covers" / record["cover_name"]).exists()
    with app.app_context():
        delete_account(member)
        assert get_db().execute("SELECT COUNT(*) FROM arcade_games WHERE uploader_id=?", (member,)).fetchone()[0] == 0
    assert not any((tmp_path / "arcade" / "roms").glob("*.gb*"))


def test_mame_core_install_is_pinned_verified_and_selected(tmp_path, monkeypatch):
    app, client, admin, member = _setup(tmp_path)
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET arcade_enabled=1")
        db.commit()
    _signin(client, member)
    assert client.post("/arcade/cores/install", data={"csrf_token": "arcade-test",
                "core": "mame2003_plus"}).status_code == 403
    _signin(client, admin)
    settings = client.get("/sysop/settings").get_data(as_text=True)
    assert "MAME / Arcade" in settings
    assert "DOWNLOAD MISSING CORE: mame2003_plus" in settings
    assert _upload(client, "Arcade Game", "arcade", "Action", station=True).status_code == 302
    package = BytesIO()
    with tarfile.open(fileobj=package, mode="w:gz") as archive:
        for name in ("mame2003_plus-wasm.data", "mame2003_plus-legacy-wasm.data"):
            data = b"core" * 512
            entry = tarfile.TarInfo("package/" + name)
            entry.size = len(data)
            archive.addfile(entry, BytesIO(data))
        report = b'{"core":"mame2003_plus","buildStart":"test","options":{}}'
        entry = tarfile.TarInfo("package/reports/mame2003_plus.json")
        entry.size = len(report)
        archive.addfile(entry, BytesIO(report))
    blob = package.getvalue()
    package_url = "https://registry.npmjs.org/@emulatorjs/core-mame2003_plus/-/core-mame2003_plus-4.2.3.tgz"
    metadata = {"dist": {"tarball": package_url,
                         "integrity": "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode()}}

    class Response(BytesIO):
        pass

    def fake_open(url, timeout):
        if url.endswith("/4.2.3"):
            return Response(json.dumps(metadata).encode())
        assert url == package_url
        return Response(blob)

    monkeypatch.setattr("stationnet.arcade.urllib.request.urlopen", fake_open)
    result = client.post("/arcade/cores/install", data={"csrf_token": "arcade-test",
                            "core": "mame2003_plus"}, follow_redirects=True)
    assert b"installed locally" in result.data
    assert (tmp_path / "arcade" / "cores" / "mame2003_plus-wasm.data").is_file()
    result = client.post("/arcade/cores/preference", data={"csrf_token": "arcade-test",
                            "platform": "arcade", "core": "mame2003_plus"})
    assert result.status_code == 302
    assert b'EJS_core = "mame2003_plus"' in client.get("/arcade/1/player").data
    player = client.get("/arcade/1/player").get_data(as_text=True)
    assert 'EJS_gameUrl = "/arcade/1/rom/Arcade%20Game.zip"' in player
    rom = client.get("/arcade/1/rom/Arcade%20Game.zip")
    assert rom.status_code == 200
    with zipfile.ZipFile(BytesIO(rom.data)) as archive:
        assert archive.read("game.rom") == b"test arcade rom"
    assert client.get("/arcade/1/rom/wrong.zip").status_code == 404
    assert client.get("/arcade/1/rom").status_code == 200
    assert client.get("/arcade/emulatorjs/data/cores/mame2003_plus-wasm.data").data == b"core" * 512
    assert b'"core":"mame2003_plus"' in client.get("/arcade/emulatorjs/data/cores/reports/mame2003_plus.json").data
