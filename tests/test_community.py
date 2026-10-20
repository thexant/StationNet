import io

from stationnet import create_app
from stationnet.db import get_db
from .conftest import csrf


PASSWORD = "correct horse battery staple"


def register(app, username):
    client = app.test_client()
    response = client.post(
        "/register",
        data={
            "csrf_token": csrf(client, "/register"),
            "username": username,
            "password": PASSWORD,
            "confirm_password": PASSWORD,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    return client


def login(client, username):
    return client.post(
        "/login",
        data={"csrf_token": csrf(client, "/login"), "username": username, "password": PASSWORD},
        follow_redirects=True,
    )


def test_station_chat_persists_across_restart(app, initialized):
    response = initialized.post(
        "/chat/messages",
        data={"csrf_token": csrf(initialized, "/chat"), "body": "Persistent station signal."},
    )
    assert response.status_code == 201
    assert initialized.get("/chat/messages").get_json()["messages"][0]["body"] == "Persistent station signal."

    restarted = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"], "SECRET_KEY": "restart"})
    client = restarted.test_client()
    login(client, "sysop")
    assert client.get("/chat/messages").get_json()["messages"][0]["body"] == "Persistent station signal."


def test_direct_chat_is_private_online_only_and_temporary(app, initialized):
    alice = register(app, "alice")
    bob = register(app, "bob")
    with app.app_context():
        ids = {row["username"]: row["id"] for row in get_db().execute("SELECT id,username FROM users")}

    response = alice.post(
        f"/chat/direct/{ids['bob']}/messages",
        data={"csrf_token": csrf(alice, f"/chat/direct/{ids['bob']}"), "body": "For Bob only."},
    )
    assert response.status_code == 201
    payload = bob.get(f"/chat/direct/{ids['alice']}/messages").get_json()
    assert payload["messages"][0]["body"] == "For Bob only."
    assert payload["messages"][0]["username"] == "alice"
    assert payload["messages"][0]["callsign"]
    assert initialized.get(f"/chat/direct/{ids['alice']}/messages").get_json()["messages"] == []

    bob.post("/logout", data={"csrf_token": csrf(bob, "/terminal")})
    login(bob, "bob")
    assert bob.get(f"/chat/direct/{ids['alice']}/messages").get_json()["messages"] == []


def test_mail_folders_privacy_and_delete(app, initialized):
    alice = register(app, "alice")
    bob = register(app, "bob")
    with app.app_context():
        ids = {row["username"]: row["id"] for row in get_db().execute("SELECT id,username FROM users")}
    response = alice.post(
        "/mail/compose",
        data={
            "csrf_token": csrf(alice, "/mail/compose"),
            "recipient_id": ids["bob"], "subject": "Private subject", "body": "Private body",
        },
        follow_redirects=True,
    )
    assert b"Private mail delivered" in response.data
    assert b"Private subject" in bob.get("/mail").data
    assert initialized.get("/mail/1").status_code == 404
    bob.get("/mail/1")
    with app.app_context():
        assert get_db().execute("SELECT is_read FROM mail WHERE id=1").fetchone()[0] == 1
    bob.post("/mail/1/delete", data={"csrf_token": csrf(bob, "/mail")})
    assert b"Private subject" not in bob.get("/mail").data
    assert b"Private subject" in alice.get("/mail/sent").data


def test_profiles_owner_only_and_directory_status(app, initialized):
    alice = register(app, "alice")
    with app.app_context():
        alice_id = get_db().execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
    response = alice.post(
        f"/users/{alice_id}",
        data={
            "csrf_token": csrf(alice, f"/users/{alice_id}"), "tagline": "Listening closely",
            "about": "Local operator", "birthday": "1990-05-04", "gender": "Optional text",
        }, follow_redirects=True,
    )
    assert b"Profile updated" in response.data
    assert b"Listening closely" in initialized.get(f"/users/{alice_id}").data
    assert initialized.post(
        f"/users/{alice_id}", data={"csrf_token": csrf(initialized, f"/users/{alice_id}"), "tagline": "No"}
    ).status_code == 403
    directory = initialized.get("/users")
    assert b"alice" in directory.data and b"ONLINE" in directory.data


def test_archive_upload_download_policy_and_ownership(app, initialized):
    alice = register(app, "alice")
    response = alice.post(
        "/files/upload",
        data={
            "csrf_token": csrf(alice, "/files/upload"), "category": "Documents",
            "description": "A tiny document", "file": (io.BytesIO(b"station data"), "notes.txt"),
        }, content_type="multipart/form-data", follow_redirects=True,
    )
    assert b"File added" in response.data
    download = initialized.get("/files/1/download")
    assert download.data == b"station data"
    download.close()
    assert b"notes.txt" in initialized.get("/files").data

    initialized.post(
        "/sysop/settings",
        data={
            "csrf_token": csrf(initialized, "/sysop/settings"), "name": "Test Relay",
            "description": "Local test station", "upload_permission": "admins",
            "file_storage_limit_mb": "100", "file_size_limit_mb": "10",
        },
    )
    assert alice.get("/files/upload").status_code == 403
    assert initialized.post("/files/1/delete", data={"csrf_token": csrf(initialized, "/files")}).status_code == 302


def test_announcement_management_is_admin_only(app, initialized):
    alice = register(app, "alice")
    assert alice.get("/announcements/new").status_code == 302
    response = initialized.post(
        "/announcements/new",
        data={"csrf_token": csrf(initialized, "/announcements/new"), "title": "Maintenance", "body": "At midnight."},
        follow_redirects=True,
    )
    assert b"Maintenance" in response.data
    initialized.post(
        "/announcements/1/edit",
        data={"csrf_token": csrf(initialized, "/announcements/1/edit"), "title": "Maintenance moved", "body": "At dawn."},
    )
    assert b"Maintenance moved" in alice.get("/announcements").data
    initialized.post("/announcements/1/delete", data={"csrf_token": csrf(initialized, "/announcements")})
    assert b"Maintenance moved" not in alice.get("/announcements").data
