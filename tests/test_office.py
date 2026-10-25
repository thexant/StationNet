from stationnet import create_app
from stationnet.db import get_db
import base64


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
