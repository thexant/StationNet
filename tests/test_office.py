from stationnet import create_app
from stationnet.db import get_db
from pathlib import Path
import base64

STATIC = Path(__file__).parents[1] / "stationnet" / "static"


def _configured_app(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name,description,callsign) VALUES(?,?,?)",
            ("Test Station", "Test description", "TST-1"),
        )
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,1)",
            ("sysop", "SYS-1", "unused"),
        ).lastrowid
        database.commit()
    return app, user_id


def _sign_in(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"


def test_office_is_disabled_and_hidden_by_default(tmp_path):
    app, user_id = _configured_app(tmp_path)
    client = app.test_client()
    _sign_in(client, user_id)

    terminal = client.get("/terminal")
    assert terminal.status_code == 200
    assert b'href="/office"' not in terminal.data
    settings = client.get("/sysop/settings")
    assert settings.status_code == 200
    assert b'id="office-suite"' in settings.data
    assert b"Enable StationNet Office Suite" in settings.data
    assert client.get("/office").status_code == 404


def test_sysop_can_enable_and_disable_office(tmp_path):
    app, user_id = _configured_app(tmp_path)
    client = app.test_client()
    _sign_in(client, user_id)

    response = client.post(
        "/sysop/settings",
        data={
            "csrf_token": "test-token",
            "settings_section": "office",
            "office_enabled": "on",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/sysop/settings#office-suite")
    assert client.get("/terminal").data.count(b'href="/office"') == 2
    office = client.get("/office")
    assert office.status_code == 200
    assert b"STATIONNET OFFICE" in office.data

    response = client.post(
        "/sysop/settings",
        data={"csrf_token": "test-token", "settings_section": "office"},
    )
    assert response.status_code == 302
    assert b'href="/office"' not in client.get("/terminal").data
    assert client.get("/office").status_code == 404


def test_office_programs_remain_available_without_navigation_links(tmp_path):
    app, user_id = _configured_app(tmp_path)
    client = app.test_client()
    _sign_in(client, user_id)

    disabled = client.get("/games-apps").data
    assert b'data-program-filter="Office"' not in disabled
    assert b'data-program-filter="Apps"' not in disabled
    for category in (b"Games", b"Utilities", b"Creative", b"Educational", b"Productivity", b"Other"):
        assert b'data-program-filter="' + category + b'"' in disabled

    client.post("/sysop/settings", data={
        "csrf_token": "test-token", "settings_section": "office", "office_enabled": "on",
    })
    settings = client.get("/sysop/settings").data
    assert b"Show OFFICE navigation links" in settings
    assert b'name="office_nav_links_enabled" checked' in settings
    programs = client.get("/games-apps").data
    assert b'data-program-filter="Office"' in programs
    for slug, destination in (("office-sheets", "/office/sheets"),
                              ("office-documents", "/office/documents"),
                              ("office-presentations", "/office/presentations"),
                              ("office-agenda", "/office/agenda"),
                              ("office-draw", "/office/draw"),
                              ("office-basic", "/office/basic")):
        assert programs.count(f'data-program-row data-slug="{slug}"'.encode()) == 1
        response = client.get(f"/games-apps/{slug}")
        assert response.status_code == 302
        assert response.headers["Location"].endswith(destination)
        assert b"OFFICE Password" in client.get(destination).data

    client.post("/sysop/settings", data={
        "csrf_token": "test-token", "settings_section": "office", "office_enabled": "on",
        "office_nav_setting_present": "1",
    })
    assert b'href="/office"' not in client.get("/terminal").data
    assert b'href="/office"' not in client.get("/games-apps").data
    assert b'data-program-filter="Office"' in client.get("/games-apps").data
    assert client.get("/games-apps/office-sheets").status_code == 302

    client.post("/sysop/settings", data={
        "csrf_token": "test-token", "settings_section": "office",
        "office_nav_setting_present": "1",
    })
    assert b'data-program-filter="Office"' not in client.get("/games-apps").data
    assert client.get("/games-apps/office-sheets").status_code == 404


def test_enabled_office_is_available_only_while_signed_in(tmp_path):
    app, _user_id = _configured_app(tmp_path)
    with app.app_context():
        database = get_db()
        database.execute("UPDATE station SET office_enabled=1 WHERE id=1")
        database.commit()

    client = app.test_client()
    terminal = client.get("/terminal")
    assert terminal.status_code == 200
    assert b'href="/office"' not in terminal.data
    response = client.get("/office")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=/office")


def test_documents_are_owner_scoped_encrypted_and_revision_checked(tmp_path):
    app, sysop_id = _configured_app(tmp_path)
    with app.app_context():
        database = get_db()
        database.execute("UPDATE station SET office_enabled=1 WHERE id=1")
        member_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("member", "MEM-1", "unused"),
        ).lastrowid
        database.commit()
    member = app.test_client()
    sysop = app.test_client()
    _sign_in(member, member_id)
    _sign_in(sysop, sysop_id)
    assert b"DOCUMENTS" in member.get("/office").data
    assert member.get("/office/documents").status_code == 200
    assert member.post("/office/documents/api/vault", headers={"X-CSRF-Token": "test-token"}).status_code == 200
    envelope = {
        "v": 1,
        "iv": base64.b64encode(b"twelve-bytes").decode(),
        "data": base64.b64encode(b"encrypted payload tag").decode(),
    }
    no_csrf = member.post("/office/documents/api", json={"envelope": envelope})
    assert no_csrf.status_code == 400
    created = member.post(
        "/office/documents/api", json={"envelope": envelope},
        headers={"X-CSRF-Token": "test-token"},
    )
    assert created.status_code == 201
    document_id = created.json["id"]
    assert member.get("/office/documents/api").json["documents"][0]["envelope"] == envelope
    assert sysop.get("/office/documents/api").json["documents"] == []
    assert sysop.get(f"/office/documents/api/{document_id}").status_code == 404
    assert sysop.put(
        f"/office/documents/api/{document_id}", json={"envelope": envelope, "revision": 1},
        headers={"X-CSRF-Token": "test-token"},
    ).status_code == 404
    updated = member.put(
        f"/office/documents/api/{document_id}", json={"envelope": envelope, "revision": 1},
        headers={"X-CSRF-Token": "test-token"},
    )
    assert updated.json["revision"] == 2
    conflict = member.put(
        f"/office/documents/api/{document_id}", json={"envelope": envelope, "revision": 1},
        headers={"X-CSRF-Token": "test-token"},
    )
    assert conflict.status_code == 409
    assert "Revision conflict" in conflict.json["error"]
    assert sysop.delete(
        f"/office/documents/api/{document_id}", headers={"X-CSRF-Token": "test-token"},
    ).status_code == 404
    with app.app_context():
        row = get_db().execute("SELECT envelope FROM office_documents WHERE id=?", (document_id,)).fetchone()
        assert "encrypted payload tag" not in row["envelope"]


def test_drawings_are_owner_scoped_encrypted_and_revision_checked(tmp_path):
    app, sysop_id = _configured_app(tmp_path)
    with app.app_context():
        database = get_db()
        database.execute("UPDATE station SET office_enabled=1 WHERE id=1")
        member_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("artist", "ART-1", "unused"),
        ).lastrowid
        database.commit()
    member, sysop = app.test_client(), app.test_client()
    _sign_in(member, member_id)
    _sign_in(sysop, sysop_id)
    assert b"DRAW" in member.get("/office").data
    assert member.get("/office/draw").status_code == 200
    envelope = {
        "v": 1,
        "iv": base64.b64encode(b"twelve-bytes").decode(),
        "data": base64.b64encode(b"encrypted drawing payload tag").decode(),
    }
    assert member.post("/office/draw/api", json={"envelope": envelope}).status_code == 400
    headers = {"X-CSRF-Token": "test-token"}
    created = member.post("/office/draw/api", json={"envelope": envelope}, headers=headers)
    assert created.status_code == 201
    drawing_id = created.json["id"]
    assert member.get("/office/draw/api").json["drawings"][0]["envelope"] == envelope
    assert sysop.get("/office/draw/api").json["drawings"] == []
    assert sysop.get(f"/office/draw/api/{drawing_id}").status_code == 404
    assert sysop.put(f"/office/draw/api/{drawing_id}", json={"envelope": envelope, "revision": 1}, headers=headers).status_code == 404
    updated = member.put(f"/office/draw/api/{drawing_id}", json={"envelope": envelope, "revision": 1}, headers=headers)
    assert updated.json["revision"] == 2
    assert member.put(f"/office/draw/api/{drawing_id}", json={"envelope": envelope, "revision": 1}, headers=headers).status_code == 409
    with app.app_context():
        stored = get_db().execute("SELECT envelope FROM office_drawings WHERE id=?", (drawing_id,)).fetchone()["envelope"]
        assert "encrypted drawing payload tag" not in stored
    assert sysop.delete(f"/office/draw/api/{drawing_id}", headers=headers).status_code == 404
    assert member.delete(f"/office/draw/api/{drawing_id}", headers=headers).status_code == 200
    assert member.get(f"/office/draw/api/{drawing_id}").status_code == 404


def test_basic_programs_are_owner_scoped_encrypted_and_revision_checked(tmp_path):
    app, sysop_id = _configured_app(tmp_path)
    with app.app_context():
        database = get_db()
        database.execute("UPDATE station SET office_enabled=1 WHERE id=1")
        member_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("coder", "COD-1", "unused"),
        ).lastrowid
        database.commit()
    member, sysop = app.test_client(), app.test_client()
    _sign_in(member, member_id)
    _sign_in(sysop, sysop_id)
    office = member.get("/office").data
    assert b"BASIC" in office and b'href="/office/basic"' in office
    envelope = {
        "v": 1,
        "iv": base64.b64encode(b"twelve-bytes").decode(),
        "data": base64.b64encode(b"encrypted basic payload tag").decode(),
    }
    headers = {"X-CSRF-Token": "test-token"}
    assert member.post("/office/basic/api", json={"envelope": envelope}).status_code == 400
    assert member.post("/office/basic/api", json={"envelope": {**envelope, "v": 2}}, headers=headers).status_code == 400
    assert member.post("/office/basic/api", json={"envelope": {**envelope, "title": "x"}}, headers=headers).status_code == 400
    too_large = {**envelope, "data": base64.b64encode(b"x" * (8 * 1024 * 1024 + 1)).decode()}
    assert member.post("/office/basic/api", json={"envelope": too_large}, headers=headers).status_code == 400
    created = member.post("/office/basic/api", json={"envelope": envelope}, headers=headers)
    assert created.status_code == 201
    program_id = created.json["id"]
    assert member.get("/office/basic/api").json["programs"][0]["envelope"] == envelope
    assert member.get(f"/office/basic/api/{program_id}").json["revision"] == 1
    assert sysop.get("/office/basic/api").json["programs"] == []
    assert sysop.get(f"/office/basic/api/{program_id}").status_code == 404
    assert sysop.put(f"/office/basic/api/{program_id}", json={"envelope": envelope, "revision": 1}, headers=headers).status_code == 404
    assert member.put(f"/office/basic/api/{program_id}", json={"envelope": envelope, "revision": 0}, headers=headers).status_code == 400
    updated = member.put(f"/office/basic/api/{program_id}", json={"envelope": envelope, "revision": 1}, headers=headers)
    assert updated.json["revision"] == 2
    conflict = member.put(f"/office/basic/api/{program_id}", json={"envelope": envelope, "revision": 1}, headers=headers)
    assert conflict.status_code == 409 and conflict.json["revision"] == 2
    with app.app_context():
        stored = get_db().execute("SELECT envelope FROM office_basic_programs WHERE id=?", (program_id,)).fetchone()["envelope"]
        assert "encrypted basic payload tag" not in stored
    assert sysop.delete(f"/office/basic/api/{program_id}", headers=headers).status_code == 404
    assert member.delete(f"/office/basic/api/{program_id}").status_code == 400
    assert member.delete(f"/office/basic/api/{program_id}", headers=headers).status_code == 200
    assert member.get(f"/office/basic/api/{program_id}").status_code == 404

    member.post("/office/basic/api", json={"envelope": envelope}, headers=headers)
    with app.app_context():
        database = get_db()
        database.execute("DELETE FROM users WHERE id=?", (member_id,))
        database.commit()
        assert database.execute(
            "SELECT COUNT(*) FROM office_basic_programs WHERE owner_id=?", (member_id,)
        ).fetchone()[0] == 0


def test_basic_page_frames_a_locked_down_runner(tmp_path):
    app, user_id = _configured_app(tmp_path)
    client = app.test_client()
    _sign_in(client, user_id)
    assert client.get("/office/basic").status_code == 404
    assert client.get("/office/basic/runner").status_code == 404
    with app.app_context():
        database = get_db()
        database.execute("UPDATE station SET office_enabled=1 WHERE id=1")
        database.commit()

    page = client.get("/office/basic")
    assert page.status_code == 200
    html = page.data.decode()
    assert 'data-runner="/office/basic/runner"' in html
    assert "Agenda, Draw, and BASIC" in html
    assert "office-basic.js" in html and "vendor/qbjs/codemirror/qb-lang.js" in html
    assert page.headers["X-Frame-Options"] == "DENY"
    assert "'unsafe-eval'" not in page.headers["Content-Security-Policy"]
    # The sandbox flags are set in office-basic.js; same-origin access must never be granted.
    bundle = (STATIC / "office-basic.js").read_text(encoding="utf-8")
    assert '"sandbox","allow-scripts"' in bundle
    assert "allow-same-origin" not in bundle and "allow-top-navigation" not in bundle and "allow-popups" not in bundle

    runner = client.get("/office/basic/runner")
    assert runner.status_code == 200
    assert runner.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert runner.headers["Cache-Control"] == "no-store"
    csp = runner.headers["Content-Security-Policy"]
    for directive in ("default-src 'none'", "connect-src 'none'", "frame-src 'none'", "worker-src 'none'",
                      "child-src 'none'", "object-src 'none'", "base-uri 'none'", "form-action 'none'",
                      "frame-ancestors 'self'", "img-src data: blob:"):
        assert directive in csp
    script_src = next(part for part in csp.split(";") if part.strip().startswith("script-src")).split()[1:]
    assert "'self'" not in script_src and "'unsafe-inline'" not in script_src and "*" not in script_src
    assert all(source == "'unsafe-eval'" or "/static/vendor/qbjs/" in source or source.endswith("/static/office-basic-runner.js")
               for source in script_src)
    assert "camera=()" in runner.headers["Permissions-Policy"]
    body = runner.data.decode()
    assert "<script>" not in body and "csrf" not in body.lower()
    assert 'id="gx-container"' in body and "office-basic-runner.js" in body

    client.get("/logout")
    with client.session_transaction() as session:
        session.clear()
    assert client.get("/office/basic/runner").status_code == 302


def test_every_office_program_counts_basic_files_when_unlocking(tmp_path):
    app, user_id = _configured_app(tmp_path)
    client = app.test_client()
    _sign_in(client, user_id)
    with app.app_context():
        database = get_db()
        database.execute("UPDATE station SET office_enabled=1 WHERE id=1")
        database.commit()
    for name in ("documents", "sheets", "presentations", "agenda", "draw"):
        page = client.get(f"/office/{name}").data.decode()
        assert 'data-basic-api="/office/basic/api"' in page
        assert "Agenda, Draw, and BASIC" in page
        assert "basicApi" in (STATIC / f"office-{name}.js").read_text(encoding="utf-8")


def test_sheets_are_owner_scoped_and_share_office_vault(tmp_path):
    app, sysop_id = _configured_app(tmp_path)
    with app.app_context():
        database = get_db()
        database.execute("UPDATE station SET office_enabled=1 WHERE id=1")
        member_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("sheet_member", "SHT-1", "unused"),
        ).lastrowid
        database.commit()
    member, sysop = app.test_client(), app.test_client()
    _sign_in(member, member_id)
    _sign_in(sysop, sysop_id)
    assert b"SHEETS" in member.get("/office").data
    assert member.get("/office/sheets").status_code == 200
    assert b"OFFICE Password" in member.get("/office/documents").data
    vault = member.post("/office/documents/api/vault", headers={"X-CSRF-Token": "test-token"}).json
    assert member.post("/office/documents/api/vault", headers={"X-CSRF-Token": "test-token"}).json == vault
    envelope = {
        "v": 1,
        "iv": base64.b64encode(b"twelve-bytes").decode(),
        "data": base64.b64encode(b"encrypted sheet payload tag").decode(),
    }
    assert member.post("/office/sheets/api", json={"envelope": envelope}).status_code == 400
    created = member.post(
        "/office/sheets/api", json={"envelope": envelope},
        headers={"X-CSRF-Token": "test-token"},
    )
    assert created.status_code == 201
    sheet_id = created.json["id"]
    assert member.get("/office/sheets/api").json["sheets"][0]["envelope"] == envelope
    assert sysop.get("/office/sheets/api").json["sheets"] == []
    assert sysop.get(f"/office/sheets/api/{sheet_id}").status_code == 404
    assert sysop.put(
        f"/office/sheets/api/{sheet_id}", json={"envelope": envelope, "revision": 1},
        headers={"X-CSRF-Token": "test-token"},
    ).status_code == 404
    updated = member.put(
        f"/office/sheets/api/{sheet_id}", json={"envelope": envelope, "revision": 1},
        headers={"X-CSRF-Token": "test-token"},
    )
    assert updated.json["revision"] == 2
    assert member.put(
        f"/office/sheets/api/{sheet_id}", json={"envelope": envelope, "revision": 1},
        headers={"X-CSRF-Token": "test-token"},
    ).status_code == 409
    assert sysop.delete(
        f"/office/sheets/api/{sheet_id}", headers={"X-CSRF-Token": "test-token"},
    ).status_code == 404
    with app.app_context():
        row = get_db().execute("SELECT envelope FROM office_sheets WHERE id=?", (sheet_id,)).fetchone()
        assert "encrypted sheet payload tag" not in row["envelope"]


def test_presentations_are_owner_scoped_encrypted_and_revision_checked(tmp_path):
    app, sysop_id = _configured_app(tmp_path)
    with app.app_context():
        database = get_db()
        database.execute("UPDATE station SET office_enabled=1 WHERE id=1")
        owner_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("presenter", "PRS-1", "unused"),
        ).lastrowid
        database.commit()
    owner, sysop = app.test_client(), app.test_client()
    _sign_in(owner, owner_id)
    _sign_in(sysop, sysop_id)
    assert b"PRESENTATIONS" in owner.get("/office").data
    assert owner.get("/office/presentations").status_code == 200
    envelope = {
        "v": 1,
        "iv": base64.b64encode(b"twelve-bytes").decode(),
        "data": base64.b64encode(b"opaque encrypted slide payload tag").decode(),
    }
    assert owner.post("/office/presentations/api", json={"envelope": envelope}).status_code == 400
    created = owner.post(
        "/office/presentations/api", json={"envelope": envelope},
        headers={"X-CSRF-Token": "test-token"},
    )
    assert created.status_code == 201
    presentation_id = created.json["id"]
    assert owner.get("/office/presentations/api").json["presentations"][0]["envelope"] == envelope
    assert sysop.get("/office/presentations/api").json["presentations"] == []
    assert sysop.get(f"/office/presentations/api/{presentation_id}").status_code == 404
    assert sysop.put(
        f"/office/presentations/api/{presentation_id}",
        json={"envelope": envelope, "revision": 1},
        headers={"X-CSRF-Token": "test-token"},
    ).status_code == 404
    updated = owner.put(
        f"/office/presentations/api/{presentation_id}",
        json={"envelope": envelope, "revision": 1},
        headers={"X-CSRF-Token": "test-token"},
    )
    assert updated.json["revision"] == 2
    assert owner.put(
        f"/office/presentations/api/{presentation_id}",
        json={"envelope": envelope, "revision": 1},
        headers={"X-CSRF-Token": "test-token"},
    ).status_code == 409
    assert sysop.delete(
        f"/office/presentations/api/{presentation_id}",
        headers={"X-CSRF-Token": "test-token"},
    ).status_code == 404
    with app.app_context():
        row = get_db().execute(
            "SELECT envelope FROM office_presentations WHERE id=?", (presentation_id,)
        ).fetchone()
        assert "opaque encrypted slide payload tag" not in row["envelope"]


def test_agenda_uses_shared_vault_and_owner_scoped_encrypted_store(tmp_path):
    app, sysop_id = _configured_app(tmp_path)
    with app.app_context():
        database = get_db()
        database.execute("UPDATE station SET office_enabled=1 WHERE id=1")
        member_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("planner", "PLN-1", "unused"),
        ).lastrowid
        database.commit()
    owner, other = app.test_client(), app.test_client()
    _sign_in(owner, member_id)
    _sign_in(other, sysop_id)
    assert b"AGENDA" in owner.get("/office").data
    assert b"OFFICE Password" in owner.get("/office/agenda").data
    salt = owner.post("/office/documents/api/vault", headers={"X-CSRF-Token": "test-token"}).json
    assert owner.post("/office/documents/api/vault", headers={"X-CSRF-Token": "test-token"}).json == salt
    assert owner.get("/office/agenda/api").json == {"agenda": None}
    envelope = {
        "v": 1,
        "iv": base64.b64encode(b"twelve-bytes").decode(),
        "data": base64.b64encode(b"opaque agenda ciphertext tag").decode(),
    }
    assert owner.post("/office/agenda/api", json={"envelope": envelope}).status_code == 400
    assert owner.post("/office/agenda/api", json={"envelope": {"v": 1}}, headers={"X-CSRF-Token": "test-token"}).status_code == 400
    created = owner.post("/office/agenda/api", json={"envelope": envelope}, headers={"X-CSRF-Token": "test-token"})
    assert created.status_code == 201 and created.json["revision"] == 1
    assert owner.post("/office/agenda/api", json={"envelope": envelope}, headers={"X-CSRF-Token": "test-token"}).status_code == 409
    assert owner.get("/office/agenda/api").json["agenda"]["envelope"] == envelope
    assert other.get("/office/agenda/api").json == {"agenda": None}
    assert owner.put("/office/agenda/api", json={"envelope": envelope, "revision": 1}).status_code == 400
    assert other.put("/office/agenda/api", json={"envelope": envelope, "revision": 1}, headers={"X-CSRF-Token": "test-token"}).status_code == 404
    updated = owner.put("/office/agenda/api", json={"envelope": envelope, "revision": 1}, headers={"X-CSRF-Token": "test-token"})
    assert updated.json["revision"] == 2
    assert owner.put("/office/agenda/api", json={"envelope": envelope, "revision": 1}, headers={"X-CSRF-Token": "test-token"}).status_code == 409
    with app.app_context():
        row = get_db().execute("SELECT envelope FROM office_agendas WHERE owner_id=?", (member_id,)).fetchone()
        assert "opaque agenda ciphertext tag" not in row["envelope"]
