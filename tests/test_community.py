import io
from pathlib import Path

from PIL import Image

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
    inbox = bob.get("/mail")
    assert b"Private subject" in inbox.data
    assert b"alice - [" in inbox.data
    assert b"bob - [" in alice.get("/mail/sent").data
    assert initialized.get("/mail/1").status_code == 404
    bob.get("/mail/1")
    with app.app_context():
        assert get_db().execute("SELECT is_read FROM mail WHERE id=1").fetchone()[0] == 1
    bob.post("/mail/1/delete", data={"csrf_token": csrf(bob, "/mail")})
    assert b"Private subject" not in bob.get("/mail").data
    assert b"Private subject" in alice.get("/mail/sent").data


def test_mail_notifications_report_only_messages_after_the_browser_baseline(app, initialized):
    alice = register(app, "alice")
    bob = register(app, "bob")
    with app.app_context():
        ids = {row["username"]: row["id"] for row in get_db().execute("SELECT id,username FROM users")}

    baseline = bob.get("/terminal")
    assert b'data-mail-notifications-url="/mail/notifications"' in baseline.data
    assert b'data-mail-latest-id="0"' in baseline.data

    alice.post(
        "/mail/compose",
        data={
            "csrf_token": csrf(alice, "/mail/compose"),
            "recipient_id": ids["bob"], "subject": "Live traffic", "body": "Arriving now.",
        },
    )

    notification = bob.get("/mail/notifications?after=0").get_json()
    assert notification == {"latest_id": 1, "new_count": 1}
    assert bob.get("/mail/notifications?after=1").get_json()["new_count"] == 0
    assert alice.get("/mail/notifications?after=0").get_json()["new_count"] == 0
    assert b'data-mail-latest-id="1"' in bob.get("/terminal").data


def test_mail_replies_form_a_private_conversation(app, initialized):
    alice = register(app, "alice")
    bob = register(app, "bob")
    with app.app_context():
        ids = {row["username"]: row["id"] for row in get_db().execute("SELECT id,username FROM users")}
    alice.post(
        "/mail/compose",
        data={
            "csrf_token": csrf(alice, "/mail/compose"),
            "recipient_id": ids["bob"], "subject": "Relay status", "body": "Are you receiving?",
        },
    )

    thread = bob.get("/mail/1")
    assert b"REPLY TO" in thread.data
    assert b"Are you receiving?" in thread.data
    response = bob.post(
        "/mail/1/reply",
        data={"csrf_token": csrf(bob, "/mail/1"), "body": "Receiving clearly."},
        follow_redirects=True,
    )
    assert b"Reply delivered" in response.data
    assert b"Are you receiving?" in response.data
    assert b"Receiving clearly." in response.data

    response = alice.post(
        "/mail/2/reply",
        data={"csrf_token": csrf(alice, "/mail/2"), "body": "Acknowledged."},
        follow_redirects=True,
    )
    assert b"Acknowledged." in response.data
    assert response.data.index(b"Are you receiving?") < response.data.index(b"Receiving clearly.")
    assert response.data.index(b"Receiving clearly.") < response.data.index(b"Acknowledged.")
    with app.app_context():
        rows = get_db().execute(
            "SELECT id,thread_id,reply_to_id,subject FROM mail ORDER BY id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (1, 1, None, "Relay status"),
            (2, 1, 1, "Re: Relay status"),
            (3, 1, 2, "Re: Relay status"),
        ]
    assert initialized.get("/mail/2").status_code == 404


def test_profiles_owner_only_and_directory_status(app, initialized):
    alice = register(app, "alice")
    with app.app_context():
        alice_id = get_db().execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
    response = alice.post(
        f"/users/{alice_id}/edit",
        data={
            "csrf_token": csrf(alice, f"/users/{alice_id}/edit"), "tagline": "Listening closely",
            "about": "Local operator", "birthday": "1990-05-04", "gender": "Optional text",
        }, follow_redirects=True,
    )
    assert b"Profile updated" in response.data
    owner_profile = alice.get(f"/users/{alice_id}")
    assert b"Listening closely" in owner_profile.data
    assert f'/users/{alice_id}/edit'.encode() in owner_profile.data
    assert b"UPDATE MY PROFILE" not in owner_profile.data
    assert f'/users/{alice_id}/edit'.encode() not in initialized.get(f"/users/{alice_id}").data
    assert initialized.post(
        f"/users/{alice_id}/edit",
        data={"csrf_token": csrf(initialized, f"/users/{alice_id}"), "tagline": "No"},
    ).status_code == 403
    assert initialized.get(f"/users/{alice_id}/edit").status_code == 403
    directory = initialized.get("/users")
    assert b"alice" in directory.data and b"ONLINE" in directory.data
    assert directory.data.count(b'class="user-avatar empty"') == 2


def test_profile_photo_is_resized_served_and_replaced(app, initialized):
    alice = register(app, "alice")
    with app.app_context():
        alice_id = get_db().execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]

    first_upload = io.BytesIO()
    Image.new("RGB", (300, 200), "red").save(first_upload, "PNG")
    first_upload.seek(0)
    response = alice.post(
        f"/users/{alice_id}/edit",
        data={
            "csrf_token": csrf(alice, f"/users/{alice_id}/edit"),
            "profile_photo": (first_upload, "portrait.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Profile updated" in response.data
    assert f'/users/{alice_id}/photo'.encode() in response.data
    directory = initialized.get("/users")
    assert f'/users/{alice_id}/photo'.encode() in directory.data
    assert directory.data.count(b'class="user-avatar empty"') == 1
    photo = alice.get(f"/users/{alice_id}/photo")
    assert photo.status_code == 200
    with Image.open(io.BytesIO(photo.data)) as image:
        assert image.format == "PNG"
        assert image.size == (128, 85)
    photo.close()

    with app.app_context():
        first_name = get_db().execute(
            "SELECT profile_photo_filename FROM users WHERE id=?", (alice_id,)
        ).fetchone()[0]
    first_path = Path(app.config["DATA_DIR"], "profile_photos", first_name)

    second_upload = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(second_upload, "JPEG")
    second_upload.seek(0)
    alice.post(
        f"/users/{alice_id}/edit",
        data={
            "csrf_token": csrf(alice, f"/users/{alice_id}/edit"),
            "profile_photo": (second_upload, "replacement.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert not first_path.exists()
    with app.app_context():
        second_name = get_db().execute(
            "SELECT profile_photo_filename FROM users WHERE id=?", (alice_id,)
        ).fetchone()[0]
    assert second_name != first_name
    Path(app.config["DATA_DIR"], "profile_photos", second_name).unlink()


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
    archive = initialized.get("/files")
    assert b"notes.txt" in archive.data
    assert b"UPLOADED BY alice - [" in archive.data

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
    assert b"sysop - [" in response.data
    initialized.post(
        "/announcements/1/edit",
        data={"csrf_token": csrf(initialized, "/announcements/1/edit"), "title": "Maintenance moved", "body": "At dawn."},
    )
    assert b"Maintenance moved" in alice.get("/announcements").data
    initialized.post("/announcements/1/delete", data={"csrf_token": csrf(initialized, "/announcements")})
    assert b"Maintenance moved" not in alice.get("/announcements").data
