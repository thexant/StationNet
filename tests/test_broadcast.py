import io
from pathlib import Path

from stationnet import create_app
from stationnet.db import get_db

from .conftest import csrf
from .test_community import login, register


def authorize_host(app, username):
    with app.app_context():
        database = get_db()
        user_id = database.execute(
            "SELECT id FROM users WHERE username=?", (username,)
        ).fetchone()["id"]
        group_id = database.execute(
            "SELECT id FROM radio_permission_groups WHERE name='OPERATORS'"
        ).fetchone()["id"]
        database.execute(
            "INSERT OR IGNORE INTO radio_group_memberships(user_id,group_id) VALUES(?,?)",
            (user_id, group_id),
        )
        database.commit()
        return user_id, group_id


def upload(client, name="signal.mp3", payload=b"\xff\xfb\x90\x64" + b"\0" * 64):
    return client.post(
        "/radio/manage/audio",
        data={"csrf_token": csrf(client, "/radio/manage"), "file": (io.BytesIO(payload), name)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def batch_upload(client, files):
    return client.post(
        "/radio/manage/audio",
        data={
            "csrf_token": csrf(client, "/radio/manage"),
            "file": [(io.BytesIO(payload), name) for name, payload in files],
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_management_link_and_direct_access_follow_current_permissions(app, initialized):
    alice = register(app, "alice")
    assert b"MANAGE BROADCASTS" not in alice.get("/radio/").data
    assert alice.get("/radio/manage").status_code == 403

    _user_id, group_id = authorize_host(app, "alice")
    assert b"MANAGE BROADCASTS" in alice.get("/radio/").data
    assert alice.get("/radio/manage").status_code == 200

    initialized.post(
        f"/sysop/radio/groups/{group_id}/capabilities",
        data={"csrf_token": csrf(initialized, "/sysop/settings")},
    )
    assert b"MANAGE BROADCASTS" not in alice.get("/radio/").data
    assert alice.get("/radio/manage").status_code == 403
    assert alice.post(
        "/radio/manage/playlists", data={"csrf_token": csrf(alice, "/terminal"), "name": "NOPE"}
    ).status_code == 403


def test_audio_upload_validation_quota_ownership_and_delete(app, initialized):
    alice = register(app, "alice")
    bob = register(app, "bob")
    alice_id, _group_id = authorize_host(app, "alice")
    authorize_host(app, "bob")

    invalid = upload(alice, "not-audio.mp3", b"plain text")
    assert b"does not contain valid supported audio data" in invalid.data
    wrong_type = upload(alice, "signal.exe")
    assert b"must use MP3, OGG, or WAV" in wrong_type.data

    valid = upload(alice)
    assert b"signal.mp3" in valid.data
    with app.app_context():
        item = get_db().execute(
            "SELECT * FROM radio_audio_files WHERE owner_id=?", (alice_id,)
        ).fetchone()
        stored = Path(app.config["DATA_DIR"]) / "radio_audio" / str(alice_id) / item["stored_name"]
        assert stored.read_bytes().startswith(b"\xff\xfb")

    assert b"signal.mp3" not in bob.get("/radio/manage").data
    assert bob.post(
        f"/radio/manage/audio/{item['id']}/delete",
        data={"csrf_token": csrf(bob, "/radio/manage")},
    ).status_code == 404

    initialized.post(
        f"/sysop/radio/users/{alice_id}/quota",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "radio_quota_mb": "1"},
    )
    over_quota = upload(alice, "large.mp3", b"\xff\xfb\x90\x64" + b"\0" * (1024 * 1024))
    assert b"would exceed your radio library storage quota" in over_quota.data

    deleted = alice.post(
        f"/radio/manage/audio/{item['id']}/delete",
        data={"csrf_token": csrf(alice, "/radio/manage")}, follow_redirects=True,
    )
    assert b"Audio file removed" in deleted.data
    assert not stored.exists()


def test_batch_upload_is_capped_validated_and_atomic(app, initialized):
    frame = b"\xff\xfb\x90\x64" + b"\0" * 64
    invalid_batch = batch_upload(initialized, [("good.mp3", frame), ("bad.mp3", b"not audio")])
    assert b"does not contain valid supported audio data" in invalid_batch.data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM radio_audio_files").fetchone()[0] == 0

    accepted = batch_upload(initialized, [(f"track-{index:02}.mp3", frame) for index in range(20)])
    assert b"20 audio files added" in accepted.data
    with app.app_context():
        rows = get_db().execute("SELECT stored_name FROM radio_audio_files").fetchall()
        assert len(rows) == 20
        directory = Path(app.config["DATA_DIR"]) / "radio_audio" / "1"
        assert all((directory / row["stored_name"]).is_file() for row in rows)

    too_many = batch_upload(initialized, [(f"extra-{index:02}.mp3", frame) for index in range(21)])
    assert b"no more than 20 audio files" in too_many.data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM radio_audio_files").fetchone()[0] == 20


def test_combined_batch_size_cannot_exceed_remaining_quota(app, initialized):
    with app.app_context():
        sysop_id = get_db().execute("SELECT id FROM users WHERE username='sysop'").fetchone()["id"]
    initialized.post(
        f"/sysop/radio/users/{sysop_id}/quota",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "radio_quota_mb": "1"},
    )
    large_frame = b"\xff\xfb\x90\x64" + b"\0" * (600 * 1024)
    response = batch_upload(
        initialized, [("part-one.mp3", large_frame), ("part-two.mp3", large_frame)],
    )
    assert b"batch would exceed your radio library storage quota" in response.data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM radio_audio_files").fetchone()[0] == 0


def test_playlist_editing_order_missing_tracks_and_restart(app, initialized):
    upload(initialized, "one.mp3")
    upload(initialized, "two.mp3", b"\xff\xfb\x90\x65" + b"\0" * 64)
    initialized.post(
        "/radio/manage/playlists",
        data={"csrf_token": csrf(initialized, "/radio/manage"), "name": "Night Signal"},
    )
    with app.app_context():
        database = get_db()
        playlist_id = database.execute("SELECT id FROM radio_playlists").fetchone()["id"]
        files = database.execute("SELECT id,display_name FROM radio_audio_files ORDER BY id").fetchall()

    for item in files:
        initialized.post(
            f"/radio/manage/playlists/{playlist_id}/tracks",
            data={"csrf_token": csrf(initialized, "/radio/manage"), "audio_file_id": item["id"]},
        )
    with app.app_context():
        tracks = get_db().execute(
            "SELECT id,display_name FROM radio_playlist_tracks ORDER BY position"
        ).fetchall()
    initialized.post(
        f"/radio/manage/playlists/{playlist_id}/tracks/{tracks[1]['id']}/move",
        data={"csrf_token": csrf(initialized, "/radio/manage"), "direction": "up"},
    )
    initialized.post(
        f"/radio/manage/playlists/{playlist_id}",
        data={"csrf_token": csrf(initialized, "/radio/manage"),
              "name": "Night Signal Revised", "loop_enabled": "on",
              "shuffle_enabled": "on"},
    )
    initialized.post(
        f"/radio/manage/audio/{files[1]['id']}/delete",
        data={"csrf_token": csrf(initialized, "/radio/manage")},
    )
    page = initialized.get("/radio/manage")
    assert b"Night Signal Revised" in page.data
    assert b"MISSING FILE" in page.data

    restarted = create_app({
        "TESTING": True, "DATA_DIR": app.config["DATA_DIR"], "SECRET_KEY": "restart-secret",
    })
    restarted_client = restarted.test_client()
    login(restarted_client, "sysop")
    persisted = restarted_client.get("/radio/manage")
    assert b"Night Signal Revised" in persisted.data
    assert b"MISSING FILE" in persisted.data
    with restarted.app_context():
        playlist = get_db().execute("SELECT * FROM radio_playlists").fetchone()
        order = get_db().execute(
            "SELECT display_name,audio_file_id FROM radio_playlist_tracks ORDER BY position"
        ).fetchall()
        assert playlist["loop_enabled"] == 1
        assert playlist["shuffle_enabled"] == 1
        assert [row["display_name"] for row in order] == ["two.mp3", "one.mp3"]
        assert order[0]["audio_file_id"] is None
    assert b"SHUFFLE PLAYBACK" in persisted.data
    assert b'name="shuffle_enabled" checked' in persisted.data


def test_non_host_cannot_change_playlist_shuffle(app, initialized):
    alice = register(app, "alice")
    response = alice.post(
        "/radio/manage/playlists/1",
        data={"csrf_token": csrf(alice, "/terminal"), "name": "NOPE",
              "shuffle_enabled": "on"},
    )
    assert response.status_code == 403


def test_sysop_quota_controls_do_not_require_broadcast_permission(app, initialized):
    with app.app_context():
        database = get_db()
        sysop_id = database.execute("SELECT id FROM users WHERE username='sysop'").fetchone()["id"]
        operator_id = database.execute(
            "SELECT id FROM radio_permission_groups WHERE name='OPERATORS'"
        ).fetchone()["id"]
        database.execute(
            "UPDATE radio_permission_groups SET host_broadcasts=0 WHERE id=?", (operator_id,)
        )
        database.commit()
    assert initialized.get("/radio/manage").status_code == 403

    default = initialized.post(
        "/sysop/radio/storage",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "radio_default_quota_mb": "250"},
    )
    override = initialized.post(
        f"/sysop/radio/users/{sysop_id}/quota",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "radio_quota_mb": "400"},
    )
    assert default.status_code == override.status_code == 302
    with app.app_context():
        database = get_db()
        assert database.execute("SELECT radio_default_quota_mb FROM station").fetchone()[0] == 250
        assert database.execute("SELECT radio_quota_mb FROM users WHERE id=?", (sysop_id,)).fetchone()[0] == 400
