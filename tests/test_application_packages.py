import base64
import io
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from stationnet import create_app
from stationnet.applications import PackageError, _safe_archive_name, install_package, validate_package
from stationnet.db import get_db


def package(files, *, entrypoint="main.js", slug="bundle-demo"):
    manifest = {
        "package_format": 1, "slug": slug, "name": "Bundle Demo",
        "description": "Package test", "author": "Tester", "version": "1.0",
        "category": "Apps", "entrypoint": entrypoint,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


@pytest.fixture()
def app(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "test-secret"})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(id,name,description,callsign) VALUES(1,'Test','','AAA-BBB')")
        user_id = db.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('operator','CCC-DDD','unused',1)"
        ).lastrowid
        db.commit()
        app.config["TEST_USER"] = user_id
    return app


def login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "csrf"


def test_multifile_install_runtime_and_uninstall(app):
    files = {
        "src/main.js": "StationNet.start(async ui => { const m = await ui.modules.load('src/message.js'); ui.render(ui.text(m.message)); });",
        "src/message.js": "export const message = 'Hello';",
        "assets/config.json": '{"level":2}',
        "assets/sprite.png": b"\x89PNG\r\n\x1a\n",
        "assets/notes.txt": "offline content",
    }
    payload = package(files, entrypoint="src/main.js")
    application, extracted = validate_package(payload)
    assert application.entrypoint == "src/main.js"
    assert extracted["assets/sprite.png"] == files["assets/sprite.png"]
    with app.app_context():
        install_package(payload)
        root = app.config["APPLICATIONS_DIR"]
        assert (Path(root) / "bundle-demo" / "src" / "message.js").read_text() == files["src/message.js"]
        assert get_db().execute("SELECT entrypoint FROM installed_applications WHERE slug='bundle-demo'").fetchone()[0] == "src/main.js"

    client = app.test_client()
    login(client, app.config["TEST_USER"])
    response = client.get("/games-apps/bundle-demo/runtime")
    assert response.status_code == 200
    assert "connect-src 'none'" in response.headers["Content-Security-Policy"]
    assert b'assets/sprite.png' in response.data
    assert base64.b64encode(files["assets/sprite.png"]) in response.data
    assert b'loadModule' in base64.b64decode(
        re.search(rb'id="stationnet-worker-source">([^<]+)', response.data).group(1)
    )
    assert client.get("/games-apps/bundle-demo/source.js").data == files["src/main.js"].encode()
    assert client.get("/games-apps/bundle-demo/assets/config.json").status_code == 404
    assert b'sandbox="allow-scripts"' in client.get("/games-apps/bundle-demo").data
    result = client.post("/sysop/applications/bundle-demo/uninstall", data={"csrf_token": "csrf", "confirm": "bundle-demo"})
    assert result.status_code == 302
    with app.app_context():
        assert not (Path(root) / "bundle-demo").exists()
        assert get_db().execute("SELECT 1 FROM installed_applications WHERE slug='bundle-demo'").fetchone() is None


def test_two_file_package_still_valid(app):
    payload = package({"main.js": "StationNet.start(ui => ui.render(ui.text('OK')));"})
    application, files = validate_package(payload)
    assert application.entrypoint == "main.js"
    assert set(files) == {"manifest.json", "main.js"}
    with app.app_context():
        install_package(payload)


@pytest.mark.parametrize("extra", [
    {"../escape.txt": "bad"},
    {"assets/../../escape.txt": "bad"},
    {"/absolute.txt": "bad"},
    {"assets/CON.txt": "bad"},
    {"assets/config.json": b"x" * (1024 * 1024 + 1)},
    {f"assets/item-{i}.txt": "x" for i in range(64)},
    {"main.js/other.js": "bad"},
])
def test_invalid_archives_rejected_without_install(app, extra):
    with pytest.raises(PackageError):
        validate_package(package({"main.js": "StationNet.start(() => {});", **extra}))
    with app.app_context():
        assert get_db().execute("SELECT 1 FROM installed_applications WHERE slug='bundle-demo'").fetchone() is None


def test_missing_entrypoint_and_invalid_module_utf8():
    with pytest.raises(PackageError):
        _safe_archive_name("assets\\escape.txt")
    with pytest.raises(PackageError):
        validate_package(package({"other.js": "content"}))
    with pytest.raises(PackageError):
        validate_package(package({"main.js": "start();", "src/bad.js": b"\xff"}))


def test_duplicate_casefolded_paths_rejected():
    with pytest.raises(PackageError):
        validate_package(package({"main.js": "start();", "assets/data.json": "{}", "assets/DATA.json": "{}"}))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_worker_asset_api_reads_only_embedded_files():
    runtime = Path(__file__).resolve().parents[1] / "stationnet" / "static" / "application_runtime.js"
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const data = Buffer.from('hello').toString('base64');
const context = {
  self: { __stationnetPackageFiles: { 'assets/note.txt': data } },
  postMessage() {}, addEventListener() {}, TextDecoder, atob, Uint8Array,
  setTimeout, setInterval, clearTimeout, clearInterval
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const api = context.self.StationNet;
assert.equal(api.assets.text('assets/note.txt'), 'hello');
assert.equal(api.assets.bytes('assets/note.txt')[0], 104);
assert.throws(() => api.assets.text('../secret.txt'), /not found/);
assert.throws(() => api.assets.text('missing.txt'), /not found/);
assert.equal(context.self.__stationnetPackageFiles, undefined);
"""
    subprocess.run(["node", "-e", script, str(runtime)], check=True, capture_output=True, text=True)
