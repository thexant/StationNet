"""BASIC programs as installable Games & Apps packages."""
import base64
import io
import json
import zipfile
from pathlib import Path

import pytest

from stationnet import applications, create_app
from stationnet.applications import PackageError, validate_package
from stationnet.db import get_db
from basic_package_support import GRAPHICS_PROGRAM, TEXT_PROGRAM, graphics_package, package, png


@pytest.fixture()
def station(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute("INSERT INTO station(name,description,callsign) VALUES(?,?,?)", ("Test", "Test", "TST-1"))
        sysop = database.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,1)",
                                 ("sysop", "SYS-1", "unused")).lastrowid
        member = database.execute("INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
                                  ("coder", "COD-1", "unused")).lastrowid
        database.commit()
    return app, sysop, member


def client_for(app, user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    return client


def upload(client, data, name="program.zip"):
    return client.post("/sysop/applications/install", data={
        "csrf_token": "test-token", "package": (io.BytesIO(data), name),
    }, content_type="multipart/form-data", follow_redirects=True)


def rejected(data, message):
    with pytest.raises(PackageError, match=message):
        validate_package(data)


def test_basic_packages_validate_with_their_own_entrypoint_rules():
    application, files = validate_package(graphics_package())
    assert application.runtime == "basic" and application.entrypoint == "main.bas"
    assert application.fullscreen_target == "application" and application.permissions == ()
    assert set(files) == {"manifest.json", "main.bas", "assets/tile.png"}

    # Latin-1 sources, a BOM, and a nested .BAS entrypoint are accepted.
    assert validate_package(package("latin", "PRINT \"caf\xe9\"".encode("iso-8859-1")))[0].runtime == "basic"
    assert validate_package(package("bom", b"\xef\xbb\xbfPRINT 1"))[0].slug == "bom"
    assert validate_package(package("nested", entrypoint="src/GAME.BAS"))[0].entrypoint == "src/GAME.BAS"

    rejected(package("js-entry", "PRINT 1", entrypoint="main.js"), r"BASIC \(\.bas\)")
    rejected(package("missing", source=None), "entrypoint is missing")
    rejected(package("empty", "  \r\n"), "cannot be empty")
    rejected(package("binary", b"PRINT\x00\x01"), "text file")
    rejected(package("perms", permissions=["multiplayer"]), "cannot request permissions")
    rejected(package("canvas", fullscreen_target="canvas"), "fullscreen target")
    rejected(package("python", runtime="python"), "runtime must be")
    rejected(package("traversal", files={"../evil.bas": "x"}), "Unsafe archive path")


def test_javascript_manifests_without_a_runtime_are_unchanged():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps({
            "package_format": 1, "slug": "legacy", "name": "Legacy", "description": "Old.", "author": "Me",
            "version": "1", "category": "Utilities", "entrypoint": "main.js",
        }))
        archive.writestr("main.js", "StationNet.start(ui => ui.render(ui.text('hi')));")
    application, _ = validate_package(buffer.getvalue())
    assert application.runtime == "javascript"
    # A .bas entrypoint is not valid JavaScript, and bundled packages still load.
    rejected(package("legacy-bas", runtime=None), r"JavaScript \(\.js\)")
    assert all(item.runtime == "javascript" for item in applications._discover_bundled())


def test_sysop_installs_launches_disables_and_uninstalls_a_basic_program(station, tmp_path):
    app, sysop, member = station
    member_client = client_for(app, member)
    # Members can export packages but never install them station-wide.
    assert member_client.post("/sysop/applications/install", data={
        "csrf_token": "test-token", "package": (io.BytesIO(graphics_package()), "pixel-demo.zip"),
    }, content_type="multipart/form-data").status_code in {302, 403, 404}
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM installed_applications").fetchone()[0] == 0

    admin = client_for(app, sysop)
    page = upload(admin, graphics_package()).data.decode()
    assert "Pixel Demo 1.0.0 installed and enabled." in page
    assert "THIRD-PARTY / BASIC / CREATIVE" in page
    assert (tmp_path / "applications" / "pixel-demo" / "assets" / "tile.png").read_bytes() == png(16, 16, (255, 0, 255))
    with app.app_context():
        row = get_db().execute("SELECT capabilities_json FROM installed_applications WHERE slug='pixel-demo'").fetchone()
        assert json.loads(row["capabilities_json"])["runtime"] == "basic"

    directory = member_client.get("/games-apps").data.decode()
    assert "Pixel Demo" in directory and "/ BASIC" in directory

    display = member_client.get("/games-apps/pixel-demo")
    html = display.data.decode()
    assert display.status_code == 200
    assert 'data-app-runtime-kind="basic"' in html and "application-basic-host.js" in html
    assert "application_host.js" not in html and "data-app-runtime" not in html.replace("data-app-runtime-kind", "")
    assert 'data-basic-runner-url="/games-apps/pixel-demo/basic-runner"' in html
    assert 'data-basic-program-url="/games-apps/pixel-demo/program.json"' in html

    program = member_client.get("/games-apps/pixel-demo/program.json")
    assert program.headers["Cache-Control"] == "no-store"
    body = program.get_json()
    assert body["entrypoint"] == "main.bas" and set(body["files"]) == {"main.bas", "assets/tile.png"}
    assert base64.b64decode(body["files"]["main.bas"]).decode() == GRAPHICS_PROGRAM.replace("\n", "\r\n")

    runner = member_client.get("/games-apps/pixel-demo/basic-runner")
    assert runner.status_code == 200 and runner.headers["X-Frame-Options"] == "SAMEORIGIN"
    csp = runner.headers["Content-Security-Policy"]
    for directive in ("default-src 'none'", "connect-src 'none'", "frame-src 'none'", "worker-src 'none'",
                      "form-action 'none'", "frame-ancestors 'self'"):
        assert directive in csp
    assert "camera=()" in runner.headers["Permissions-Policy"]
    assert "pixel" not in runner.data.decode().lower()

    # The JavaScript worker routes never serve a BASIC program.
    assert member_client.get("/games-apps/pixel-demo/runtime").status_code == 404
    assert member_client.get("/games-apps/pixel-demo/source.js").status_code == 404

    # The existing storage API persists the program's localStorage snapshot.
    saved = member_client.post("/games-apps/pixel-demo/storage", headers={"X-CSRF-Token": "test-token"},
                               json={"operation": "set", "scope": "user", "key": "basic.localStorage", "value": {"runs": " 3"}})
    assert saved.get_json() == {"ok": True, "value": True}

    admin.post("/sysop/applications/pixel-demo/state", data={"csrf_token": "test-token", "enabled": "0"})
    for path in ("", "/program.json", "/basic-runner"):
        assert member_client.get(f"/games-apps/pixel-demo{path}").status_code == 404
    admin.post("/sysop/applications/pixel-demo/state", data={"csrf_token": "test-token", "enabled": "1"})
    assert member_client.get("/games-apps/pixel-demo/program.json").status_code == 200

    page = admin.post("/sysop/applications/pixel-demo/uninstall", data={"csrf_token": "test-token", "confirm": "pixel-demo"},
                      follow_redirects=True).data.decode()
    assert "Pixel Demo was uninstalled" in page
    assert not (tmp_path / "applications" / "pixel-demo").exists()
    assert member_client.get("/games-apps/pixel-demo").status_code == 404
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM installed_applications").fetchone()[0] == 0


def test_basic_programs_do_not_need_office_and_reject_duplicates(station):
    app, sysop, member = station
    admin = client_for(app, sysop)
    with app.app_context():
        assert not get_db().execute("SELECT office_enabled FROM station").fetchone()[0]
    assert "installed and enabled" in upload(admin, package("text-demo", TEXT_PROGRAM)).data.decode()
    assert client_for(app, member).get("/games-apps/text-demo/basic-runner").status_code == 200
    assert "already installed" in upload(admin, package("text-demo", TEXT_PROGRAM)).data.decode()
    assert "Package rejected" in upload(admin, package("snake", TEXT_PROGRAM)).data.decode()
    assert "Package rejected" in upload(admin, package("office-basic", TEXT_PROGRAM)).data.decode()


def test_basic_routes_require_sign_in_and_a_basic_application(station):
    app, sysop, _ = station
    upload(client_for(app, sysop), package("text-demo"))
    anonymous = app.test_client()
    assert anonymous.get("/games-apps/text-demo/program.json").status_code == 302
    assert anonymous.get("/games-apps/text-demo/basic-runner").status_code == 302
    signed_in = client_for(app, sysop)
    # Bundled JavaScript applications have no BASIC routes.
    assert signed_in.get("/games-apps/snake/program.json").status_code == 404
    assert signed_in.get("/games-apps/snake/basic-runner").status_code == 404
    assert "application_host.js" in signed_in.get("/games-apps/snake").data.decode()


def test_package_host_bundle_keeps_the_sandbox_flags():
    static = Path(__file__).parents[1] / "stationnet" / "static"
    bundle = (static / "application-basic-host.js").read_text(encoding="utf-8")
    assert '"sandbox","allow-scripts"' in bundle
    assert "allow-same-origin" not in bundle and "allow-top-navigation" not in bundle and "allow-popups" not in bundle
