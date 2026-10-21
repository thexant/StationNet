import json
import io
import shutil
import zipfile
from pathlib import Path

from stationnet import applications
from stationnet import create_app
from stationnet.applications import APPS, GAMES, catalog, find
from stationnet.db import get_db


def _demo_package(slug="portable-number-guess", extra=None, entrypoint="main.js"):
    manifest = json.loads((
        Path(__file__).parent.parent
        / "stationnet" / "application_packages" / "number_guess" / "manifest.json"
    ).read_text(encoding="utf-8"))
    manifest.update({"slug": slug, "name": "Portable Number Guess", "entrypoint": entrypoint})
    script = (
        Path(__file__).parent.parent
        / "stationnet" / "application_packages" / "number_guess" / "main.js"
    ).read_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(entrypoint, script)
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return output.getvalue()


def test_catalog_is_centralized_and_contains_both_categories():
    applications = catalog()

    assert applications
    assert {application.category for application in applications} == {GAMES, APPS}
    assert all(
        all(
            (
                application.slug,
                application.name,
                application.description,
                application.author,
                application.version,
            )
        )
        for application in applications
    )
    assert all(find(application.slug) is application for application in applications)


def test_directory_requires_login_and_is_linked_from_navigation(initialized):
    initialized.post("/logout", data={"csrf_token": _csrf(initialized)})
    response = initialized.get("/games-apps")
    assert response.status_code == 302
    assert "/login?next=/games-apps" in response.headers["Location"]

    response = initialized.post(
        "/login",
        data={
            "csrf_token": _csrf(initialized, "/login"),
            "username": "sysop",
            "password": "correct horse battery staple",
        },
        follow_redirects=True,
    )
    assert b'href="/games-apps"' in response.data
    assert b"GAMES AND APPS" in response.data


def test_directory_lists_metadata_and_display_uses_station_shell(initialized):
    response = initialized.get("/games-apps")
    assert response.status_code == 200
    assert b"Word Chain [Placeholder]" in response.data
    assert b"Event Board [Placeholder]" in response.data
    assert b"StationNet Core" in response.data
    assert b"0.0.1" in response.data

    display = initialized.get("/games-apps/word-chain")
    assert display.status_code == 200
    assert b"station-shell" in display.data
    assert b"STATION APPLICATION DISPLAY" in display.data
    assert b"FUNCTIONALITY HAS NOT BEEN IMPLEMENTED YET" in display.data
    assert b"RETURN TO GAMES AND APPS" in display.data
    assert b'aria-current="page"' in display.data


def test_unknown_application_returns_not_found(initialized):
    assert initialized.get("/games-apps/not-registered").status_code == 404


def test_number_guess_uses_isolated_runtime_and_public_api(initialized):
    directory = initialized.get("/games-apps")
    assert b"Number Guess" in directory.data
    assert b"Signal Runner" not in directory.data

    display = initialized.get("/games-apps/number-guess")
    assert display.status_code == 200
    assert b'sandbox="allow-scripts"' in display.data
    assert b"application_host.js" in display.data

    runtime = initialized.get("/games-apps/number-guess/runtime")
    assert runtime.status_code == 200
    assert b"stationnet-application-source" in runtime.data
    assert "connect-src 'none'" in runtime.headers["Content-Security-Policy"]
    assert "worker-src blob:" in runtime.headers["Content-Security-Policy"]
    assert runtime.headers["X-Frame-Options"] == "SAMEORIGIN"

    source = initialized.get("/games-apps/number-guess/source.js")
    assert source.status_code == 200
    assert source.mimetype == "application/javascript"
    assert b"ui.numberInput" in source.data


def test_ascii_ansi_art_studio_is_discovered_and_uses_public_capabilities(initialized):
    directory = initialized.get("/games-apps")
    assert b"ASCII/ANSI Art Studio" in directory.data
    assert initialized.get("/games-apps/ascii-ansi-art-studio").status_code == 200

    source = initialized.get("/games-apps/ascii-ansi-art-studio/source.js")
    assert source.status_code == 200
    assert b"ui.cellGrid" in source.data
    assert b"ui.storage.set" in source.data
    assert b"ui.clipboard.writeText" in source.data
    assert b"ui.download.text" in source.data
    assert b"ui.download.cellImage" in source.data
    assert b"floodFill" in source.data
    assert b"undoStack" in source.data
    assert b"\\u001b[" in source.data
    assert b'DOWNLOAD .PNG' in source.data


def test_bundled_folder_discovery_tracks_add_remove_and_restore(monkeypatch, app):
    root = Path(app.config["DATA_DIR"]) / "discovery-packages"
    shutil.rmtree(root, ignore_errors=True)
    package = root / "portable_art"
    package.mkdir(parents=True)
    manifest = {
        "package_format": 1, "slug": "portable-art", "name": "Portable Art",
        "description": "A dynamically discovered test application.", "author": "Tests",
        "version": "1.0.0", "category": "Apps", "entrypoint": "main.js",
    }
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    script = package / "main.js"
    script.write_text("StationNet.start((ui) => ui.render(ui.text('ok')));", encoding="utf-8")
    monkeypatch.setattr(applications, "_package_root", lambda: root.resolve())
    applications._bundled_cache.clear()

    assert applications.find("portable-art") is not None
    held = Path(app.config["DATA_DIR"]) / "discovery-package-held"
    shutil.rmtree(held, ignore_errors=True)
    package.rename(held)
    assert applications.find("portable-art") is None
    held.rename(package)
    assert applications.find("portable-art") is not None
    shutil.rmtree(root)


def test_art_studio_folder_is_a_valid_distributable_package():
    folder = Path(__file__).parent.parent / "stationnet" / "application_packages" / "ascii_ansi_art_studio"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(folder / "manifest.json", "manifest.json")
        archive.write(folder / "main.js", "main.js")
    application, files = applications.validate_package(output.getvalue())
    assert application.slug == "ascii-ansi-art-studio"
    assert set(files) == {"manifest.json", "main.js"}


def test_placeholder_has_no_runtime(initialized):
    assert initialized.get("/games-apps/word-chain/runtime").status_code == 404
    assert initialized.get("/games-apps/word-chain/source.js").status_code == 404


def test_application_storage_persists_and_supports_json(app, initialized):
    headers = {"X-CSRF-Token": _csrf(initialized), "Content-Type": "application/json"}
    endpoint = "/games-apps/number-guess/storage"
    value = {"best": 4, "history": [8, 4], "sound": True}

    saved = initialized.post(endpoint, data=json.dumps({
        "operation": "set", "key": "profile", "value": value,
    }), headers=headers)
    assert saved.status_code == 200
    assert saved.get_json() == {"ok": True, "value": True}

    # A later request represents a reopened runtime; storage is SQLite-backed.
    loaded = initialized.post(endpoint, data=json.dumps({
        "operation": "get", "key": "profile",
    }), headers=headers)
    assert loaded.get_json() == {"ok": True, "found": True, "value": value}
    keys = initialized.post(endpoint, data=json.dumps({"operation": "keys"}), headers=headers)
    assert keys.get_json()["value"] == ["profile"]
    deleted = initialized.post(endpoint, data=json.dumps({
        "operation": "delete", "key": "profile",
    }), headers=headers)
    assert deleted.get_json()["value"] is True
    missing = initialized.post(endpoint, data=json.dumps({
        "operation": "get", "key": "profile",
    }), headers=headers)
    assert missing.get_json() == {"ok": True, "found": False, "value": None}


def test_application_storage_isolates_users_apps_and_exposes_shared_scope(app, initialized):
    endpoint = "/games-apps/number-guess/storage"
    token = _csrf(initialized)
    headers = {"X-CSRF-Token": token, "Content-Type": "application/json"}
    initialized.post(endpoint, json={"operation": "set", "key": "private", "value": "sysop"}, headers=headers)
    initialized.post(endpoint, json={
        "operation": "set", "key": "scoreboard", "value": ["SYSOP"], "scope": "shared",
    }, headers=headers)
    with app.app_context():
        database = get_db()
        second_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES('second','TST-002','unused')"
        ).lastrowid
        database.execute(
            "INSERT INTO application_storage(application_slug,storage_scope,owner_key,storage_key,value_json,size_bytes) "
            "VALUES('another-app','user',?,'private','\"other app\"',11)", (f"user:{second_id}",),
        )
        database.commit()
    with initialized.session_transaction() as browser_session:
        browser_session["user_id"] = second_id
        browser_session.pop("connection_id", None)

    private = initialized.post(endpoint, json={"operation": "get", "key": "private"}, headers=headers)
    assert private.get_json() == {"ok": True, "found": False, "value": None}
    shared = initialized.post(endpoint, json={
        "operation": "get", "key": "scoreboard", "scope": "shared",
    }, headers=headers)
    assert shared.get_json()["value"] == ["SYSOP"]
    keys = initialized.post(endpoint, json={"operation": "keys"}, headers=headers)
    assert "private" not in keys.get_json()["value"]


def test_application_storage_enforces_tokens_keys_and_limits(initialized):
    endpoint = "/games-apps/number-guess/storage"
    assert initialized.post(endpoint, json={"operation": "keys"}).status_code == 400
    headers = {"X-CSRF-Token": _csrf(initialized)}
    invalid = initialized.post(endpoint, json={
        "operation": "set", "key": "../escape", "value": 1,
    }, headers=headers)
    assert invalid.status_code == 400
    oversized = initialized.post(endpoint, json={
        "operation": "set", "key": "large", "value": "x" * (64 * 1024),
    }, headers=headers)
    assert oversized.status_code == 413
    assert initialized.get("/terminal").status_code == 200


def test_expanded_runtime_and_demo_exercise_updates_timers_and_storage(initialized):
    source = initialized.get("/games-apps/number-guess/source.js")
    assert b'ui.storage.get("best-score")' in source.data
    assert b'ui.update("message"' in source.data
    assert b"ui.interval" in source.data

    runtime = initialized.get("/static/application_runtime.js")
    assert b"textarea" in runtime.data
    assert b"storage-result" in runtime.data
    assert b"download-cell-image" in runtime.data
    host = initialized.get("/static/application_host.js")
    assert b"PROGRAM STATUS / ERROR" in host.data
    assert b"data-app-component" in host.data
    assert b'canvas.toBlob(resolve, "image/png")' in host.data
    assert b'phase === "move"' in host.data


def test_sysop_can_install_disable_restart_enable_and_uninstall_demo(app, initialized):
    token = _csrf(initialized, "/sysop/applications")
    installed = initialized.post(
        "/sysop/applications/install",
        data={"csrf_token": token, "package": (io.BytesIO(_demo_package()), "number-guess.zip")},
        content_type="multipart/form-data", follow_redirects=True,
    )
    assert installed.status_code == 200
    assert b"Portable Number Guess 1.1.0 installed and enabled" in installed.data
    assert b"THIRD-PARTY" in installed.data
    assert initialized.get("/games-apps/portable-number-guess").status_code == 200
    assert b"ui.numberInput" in initialized.get(
        "/games-apps/portable-number-guess/source.js"
    ).data

    disabled = initialized.post(
        "/sysop/applications/portable-number-guess/state",
        data={"csrf_token": token, "enabled": "0"}, follow_redirects=True,
    )
    assert b"is now disabled" in disabled.data
    with app.app_context():
        database = get_db()
        member_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES('app-member','APP-001','unused')"
        ).lastrowid
        database.commit()
    with initialized.session_transaction() as browser_session:
        browser_session["user_id"] = member_id
    assert b"Portable Number Guess" not in initialized.get("/games-apps").data
    assert initialized.get("/games-apps/portable-number-guess").status_code == 404
    assert initialized.get("/games-apps/portable-number-guess/runtime").status_code == 404

    restarted = create_app({
        "TESTING": True, "DATA_DIR": app.config["DATA_DIR"],
        "APPLICATIONS_DIR": app.config["APPLICATIONS_DIR"], "SECRET_KEY": "restart-secret",
    }).test_client()
    restarted.post("/login", data={
        "csrf_token": _csrf(restarted, "/login"), "username": "sysop",
        "password": "correct horse battery staple",
    })
    assert b"Portable Number Guess" in restarted.get("/sysop/applications").data
    assert restarted.get("/games-apps/portable-number-guess").status_code == 404
    restart_token = _csrf(restarted, "/sysop/applications")
    restarted.post(
        "/sysop/applications/portable-number-guess/state",
        data={"csrf_token": restart_token, "enabled": "1"},
    )
    assert restarted.get("/games-apps/portable-number-guess").status_code == 200

    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO application_storage(application_slug,storage_scope,owner_key,storage_key,value_json,size_bytes) "
            "VALUES('portable-number-guess','shared','shared','wins','3',1)"
        )
        database.commit()
    removed = restarted.post(
        "/sysop/applications/portable-number-guess/uninstall",
        data={"csrf_token": restart_token, "confirm": "portable-number-guess"},
        follow_redirects=True,
    )
    assert b"was uninstalled" in removed.data
    assert b"saved user data was retained" in removed.data
    assert restarted.get("/games-apps/portable-number-guess").status_code == 404
    assert not (Path(app.config["APPLICATIONS_DIR"]) / "portable-number-guess").exists()
    with app.app_context():
        assert get_db().execute(
            "SELECT value_json FROM application_storage WHERE application_slug='portable-number-guess'"
        ).fetchone()["value_json"] == "3"


def test_application_management_is_admin_only_and_protects_builtins(app, initialized):
    token = _csrf(initialized, "/sysop/applications")
    protected = initialized.post(
        "/sysop/applications/number-guess/uninstall",
        data={"csrf_token": token, "confirm": "number-guess"}, follow_redirects=True,
    )
    assert b"Built-in applications cannot be uninstalled" in protected.data
    assert initialized.get("/games-apps/number-guess").status_code == 200
    with app.app_context():
        database = get_db()
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES('member','TST-099','unused')"
        ).lastrowid
        database.commit()
    with initialized.session_transaction() as browser_session:
        browser_session["user_id"] = user_id
    assert initialized.get("/sysop/applications").status_code == 302
    assert initialized.post("/sysop/applications/install", data={}).status_code == 302


def test_invalid_application_packages_are_rejected_without_partial_install(app, initialized):
    token = _csrf(initialized, "/sysop/applications")
    invalid_packages = (
        (b"not a zip", b"not a valid ZIP"),
        (_demo_package(extra={"tool.exe": b"MZ"}), b"Unsupported package file"),
        (_demo_package(extra={"../escape.js": b"bad"}), b"Unsafe archive path"),
        (_demo_package(slug="number-guess"), b"belongs to a built-in application"),
    )
    for package, message in invalid_packages:
        response = initialized.post(
            "/sysop/applications/install",
            data={"csrf_token": token, "package": (io.BytesIO(package), "invalid.zip")},
            content_type="multipart/form-data", follow_redirects=True,
        )
        assert message in response.data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) AS count FROM installed_applications").fetchone()["count"] == 0
    root = Path(app.config["APPLICATIONS_DIR"])
    assert not root.exists() or not tuple(root.iterdir())


def _csrf(client, path="/terminal"):
    client.get(path)
    with client.session_transaction() as session:
        return session["csrf_token"]
