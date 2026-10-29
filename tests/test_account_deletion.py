"""Account deletion removes authored content and enforces both confirmation flows."""
from pathlib import Path

from stationnet import mud, create_app
from stationnet.account_deletion import delete_account, purge_pending_files
from stationnet.db import get_db


def station_with_users(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign) VALUES('Test','Test','TST-1')")
        owner = db.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('owner','OWN-1','unused',1)"
        ).lastrowid
        member = db.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES('member','MEM-1','unused')"
        ).lastrowid
        other = db.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES('other','OTH-1','unused')"
        ).lastrowid
        db.commit()
    return app, owner, member, other


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "deletion-test-token"


def seed_content(app, member, other):
    data = Path(app.config["DATA_DIR"])
    with app.app_context():
        db = get_db()
        db.execute("UPDATE users SET profile_photo_filename='member.png' WHERE id=?", (member,))
        db.execute("INSERT INTO posts(author_id,title,body) VALUES(?,?,?)", (member, "member bulletin", "body"))
        db.execute("INSERT INTO posts(author_id,title,body) VALUES(?,?,?)", (other, "other bulletin", "body"))
        db.execute("INSERT INTO station_chat_messages(author_id,body) VALUES(?,?)", (member, "member chat"))
        db.execute("INSERT INTO mail(sender_id,recipient_id,subject,body) VALUES(?,?,?,?)",
                   (member, other, "member mail", "body"))
        db.execute("INSERT INTO archive_files(uploader_id,display_name,stored_name,category,size_bytes) "
                   "VALUES(?,?,?,?,?)", (member, "upload", "member.txt", "General", 4))
        db.execute("INSERT INTO videos(uploader_id,title,stored_name,mime_type,size_bytes) "
                   "VALUES(?,?,?,?,?)", (member, "video", "member.mp4", "video/mp4", 4))
        db.execute("INSERT INTO library_books(uploader_id,title,stored_name,original_name,size_bytes,format,page_count) "
                   "VALUES(?,?,?,?,?,?,?)", (member, "manual", "member.pdf", "member.pdf", 4, "pdf", 1))
        db.execute("INSERT INTO imageboard_threads(author_id,subject,image_name,image_type,thumb_name,created_at,bumped_at) "
                   "VALUES(?,?,?,?,?,?,?)", (member, "thread", "member.jpg", "image/jpeg", "member-thumb.jpg", "now", "now"))
        db.execute("INSERT INTO office_documents(owner_id,envelope) VALUES(?,?)", (member, "encrypted"))
        db.execute("INSERT INTO radio_audio_files(owner_id,display_name,stored_name,media_type,size_bytes) "
                   "VALUES(?,?,?,?,?)", (member, "audio", "member.mp3", "audio/mpeg", 4))
        db.execute("INSERT INTO radio_broadcast_content(owner_id,kind,name,stored_name,size_bytes) "
                   "VALUES(?,?,?,?,?)", (member, "sstv", "picture", "member-radio.png", 4))
        member_audio = db.execute("SELECT id FROM radio_audio_files WHERE owner_id=?", (member,)).fetchone()[0]
        other_playlist = db.execute("INSERT INTO radio_playlists(owner_id,name) VALUES(?,?)",
                                    (other, "other playlist")).lastrowid
        db.execute("INSERT INTO radio_playlist_tracks(playlist_id,audio_file_id,display_name,position) "
                   "VALUES(?,?,?,0)", (other_playlist, member_audio, "member audio"))
        db.execute("INSERT INTO application_storage(application_slug,storage_scope,owner_key,storage_key,value_json,size_bytes) "
                   "VALUES('test','user',?,'save','{}',2)", (f"user:{member}",))
        db.execute("INSERT INTO application_storage(application_slug,storage_scope,owner_key,storage_key,value_json,size_bytes,last_writer_user_id) "
                   "VALUES('test','shared','shared','shared-save','{}',2,?)", (member,))
        db.commit()
    for relative in ("profile_photos/member.png", "uploads/member.txt", "videos/users/2/member.mp4",
                     "library/users/2/member.pdf", "library/users/2/member.pdf.cover.jpg",
                     "imageboard/member.jpg", "imageboard/member-thumb.jpg", "radio_audio/2/member.mp3",
                     "radio_content/2/member-radio.png"):
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")


def assert_wiped(app, member, other):
    with app.app_context():
        db = get_db()
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT 1 FROM users WHERE id=?", (member,)).fetchone() is None
        for table, column in (("posts", "author_id"), ("station_chat_messages", "author_id"),
                              ("archive_files", "uploader_id"), ("videos", "uploader_id"),
                              ("library_books", "uploader_id"),
                              ("imageboard_threads", "author_id"), ("office_documents", "owner_id"),
                              ("radio_audio_files", "owner_id"), ("radio_broadcast_content", "owner_id")):
            assert db.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (member,)).fetchone() is None
        assert db.execute("SELECT 1 FROM mail WHERE sender_id=? OR recipient_id=?",
                          (member, member)).fetchone() is None
        assert db.execute("SELECT 1 FROM application_storage WHERE owner_key=?",
                          (f"user:{member}",)).fetchone() is None
        assert db.execute("SELECT 1 FROM application_storage WHERE last_writer_user_id=?",
                          (member,)).fetchone() is None
        assert db.execute("SELECT 1 FROM radio_playlist_tracks WHERE display_name='member audio'").fetchone() is None
        assert db.execute("SELECT 1 FROM radio_playlists WHERE owner_id=?", (other,)).fetchone() is not None
        assert db.execute("SELECT 1 FROM posts WHERE author_id=?", (other,)).fetchone() is not None
    data = Path(app.config["DATA_DIR"])
    for relative in ("profile_photos/member.png", "uploads/member.txt", "videos/users/2/member.mp4",
                     "library/users/2/member.pdf", "library/users/2/member.pdf.cover.jpg",
                     "imageboard/member.jpg", "imageboard/member-thumb.jpg", "radio_audio/2/member.mp3",
                     "radio_content/2/member-radio.png"):
        assert not (data / relative).exists()


def test_self_deletion_requires_full_confirmation_and_ends_session(tmp_path):
    app, owner, member, other = station_with_users(tmp_path)
    seed_content(app, member, other)
    client = app.test_client()
    sign_in(client, member)
    assert b"DELETE ACCOUNT" in client.get(f"/users/{member}/edit").data
    page = client.get(f"/users/{member}/delete")
    assert page.status_code == 200
    assert b"ACCOUNT DELETION" in page.data
    for data in ({"confirmation": "wrong", "final_confirm": "on"},
                 {"confirmation": "member"}):
        response = client.post(f"/users/{member}/delete",
                               data={"csrf_token": "deletion-test-token", **data})
        assert response.status_code == 400
    assert client.post(f"/users/{other}/delete", data={"csrf_token": "deletion-test-token"}).status_code == 403
    response = client.post(f"/users/{member}/delete", data={
        "csrf_token": "deletion-test-token", "confirmation": "member", "final_confirm": "on",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    with client.session_transaction() as session:
        assert "user_id" not in session
    assert_wiped(app, member, other)


def test_sysop_deletion_and_owner_protection(tmp_path):
    app, owner, member, other = station_with_users(tmp_path)
    seed_content(app, member, other)
    client = app.test_client()
    sign_in(client, owner)
    assert b"Delete account" in client.get(f"/sysop/moderation/users/{member}").data
    assert b"The station owner&#39;s account cannot be deleted" in client.get(
        f"/sysop/moderation/users/{owner}").data or b"The station owner's account cannot be deleted" in client.get(
        f"/sysop/moderation/users/{owner}").data
    for data in ({"username": "wrong", "confirm": "on"}, {"username": "member"}):
        assert client.post(f"/sysop/moderation/users/{member}/delete",
                           data={"csrf_token": "deletion-test-token", **data}).status_code == 400
    assert client.post(f"/sysop/moderation/users/{owner}/delete", data={
        "csrf_token": "deletion-test-token", "username": "owner", "confirm": "on",
    }).status_code == 403
    deleted_client = app.test_client()
    sign_in(deleted_client, member)
    response = client.post(f"/sysop/moderation/users/{member}/delete", data={
        "csrf_token": "deletion-test-token", "username": "member", "confirm": "on",
    })
    assert response.status_code == 302
    assert_wiped(app, member, other)
    deleted_client.get("/")
    with deleted_client.session_transaction() as session:
        assert "user_id" not in session
    with client.session_transaction() as session:
        assert session["user_id"] == owner


def test_owner_cannot_delete_own_account(tmp_path):
    app, owner, _, _ = station_with_users(tmp_path)
    client = app.test_client()
    sign_in(client, owner)
    assert b"DELETE ACCOUNT" not in client.get(f"/users/{owner}/edit").data
    assert client.get(f"/users/{owner}/delete").status_code == 403
    assert client.post(f"/users/{owner}/delete", data={
        "csrf_token": "deletion-test-token", "confirmation": "owner", "final_confirm": "on",
    }).status_code == 403


def test_member_cannot_use_sysop_deletion(tmp_path):
    app, _, member, other = station_with_users(tmp_path)
    client = app.test_client()
    sign_in(client, member)
    response = client.post(f"/sysop/moderation/users/{other}/delete", data={
        "csrf_token": "deletion-test-token", "username": "other", "confirm": "on",
    })
    assert response.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT 1 FROM users WHERE id=?", (other,)).fetchone() is not None


def test_locked_upload_is_retried_after_deletion(tmp_path, monkeypatch):
    app, _, member, other = station_with_users(tmp_path)
    seed_content(app, member, other)
    target = tmp_path / "uploads" / "member.txt"
    original_unlink = Path.unlink
    locked = True

    def unlink_with_transient_lock(path, *args, **kwargs):
        if path == target and locked:
            raise PermissionError("file is open")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink_with_transient_lock)
    with app.app_context():
        delete_account(member)
        assert target.exists()
        assert get_db().execute("SELECT relative_path FROM account_deletion_files").fetchall()
        locked = False
        purge_pending_files()
        assert not target.exists()
        assert get_db().execute("SELECT relative_path FROM account_deletion_files").fetchall() == []


def test_mud_creations_and_nested_inventory_are_removed(tmp_path):
    app, owner, member, other = station_with_users(tmp_path)
    with app.app_context():
        db = get_db()
        # Characters are created per world on first visit.
        member_character = mud._character_id(db, member)
        other_character = mud._character_id(db, other)
        db.execute("INSERT INTO mud_item_definitions(id,name,description,created_by) "
                   "VALUES('made','Made by member','item',?)", (member,))
        db.execute("INSERT INTO mud_item_definitions(id,name,description) "
                   "VALUES('other-item','Other item','item')")
        db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id) "
                   "VALUES('held','made',?)", (other_character,))
        db.execute("INSERT INTO mud_item_instances(id,definition_id,owner_character_id) "
                   "VALUES('container','other-item',?)", (member_character,))
        db.execute("INSERT INTO mud_item_instances(id,definition_id,container_id) "
                   "VALUES('nested','other-item','container')")
        db.execute("INSERT INTO mud_npc_definitions(id,name,description,created_by) "
                   "VALUES('member-npc','Member NPC','npc',?)", (member,))
        db.execute("INSERT INTO mud_quests(id,name,description,created_by) "
                   "VALUES('member-quest','Member Quest','quest',?)", (member,))
        db.commit()
    client = app.test_client()
    sign_in(client, owner)
    response = client.post(f"/sysop/moderation/users/{member}/delete", data={
        "csrf_token": "deletion-test-token", "username": "member", "confirm": "on",
    })
    assert response.status_code == 302
    with app.app_context():
        db = get_db()
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        for item_id in ("held", "container", "nested"):
            assert db.execute("SELECT 1 FROM mud_item_instances WHERE id=?", (item_id,)).fetchone() is None
        for table, item_id in (("mud_item_definitions", "made"),
                               ("mud_npc_definitions", "member-npc"),
                               ("mud_quests", "member-quest")):
            assert db.execute(f"SELECT 1 FROM {table} WHERE id=?", (item_id,)).fetchone() is None
        assert db.execute("SELECT 1 FROM mud_item_definitions WHERE id='other-item'").fetchone() is not None
