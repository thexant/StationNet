"""Large broadcast libraries remain navigable and bulk deletion stays owner scoped."""
from pathlib import Path

from stationnet import create_app
from stationnet.db import get_db
import stationnet.broadcast as broadcast


def setup_library(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    monkeypatch.setattr(broadcast, "can_manage_broadcasts", lambda _user_id: True)
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign) VALUES('Test','Test','TST-1')")
        owner = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('owner','OWN-1','unused',1)").lastrowid
        other = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('other','OTH-1','unused')").lastrowid
        playlist = db.execute("INSERT INTO radio_playlists(owner_id,name) VALUES(?,?)", (owner, "Set list")).lastrowid
        ids = []
        for index in range(30):
            name = f"Track {index:02d}.mp3"
            file_id = db.execute(
                "INSERT INTO radio_audio_files(owner_id,display_name,stored_name,media_type,size_bytes,title,artist) VALUES(?,?,?,?,?,?,?)",
                (owner, name, f"{index}.mp3", "audio/mpeg", index + 1,
                 "Featured title" if index == 14 else "", "Special artist" if index == 14 else ""),
            ).lastrowid
            ids.append(file_id)
        foreign_id = db.execute(
            "INSERT INTO radio_audio_files(owner_id,display_name,stored_name,media_type,size_bytes) VALUES(?,?,?,?,?)",
            (other, "Foreign.mp3", "foreign.mp3", "audio/mpeg", 9),
        ).lastrowid
        db.execute("INSERT INTO radio_playlist_tracks(playlist_id,audio_file_id,display_name,position) VALUES(?,?,?,0)",
                   (playlist, ids[0], "Track 00.mp3"))
        db.commit()
    audio_dir = Path(tmp_path) / "radio_audio" / str(owner)
    audio_dir.mkdir(parents=True)
    for index in range(30):
        (audio_dir / f"{index}.mp3").write_bytes(b"audio")
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = owner
        session["csrf_token"] = "library-test-token"
    return app, client, owner, ids, foreign_id


def test_search_sort_pages_and_playlist_choices(tmp_path, monkeypatch):
    _app, client, _owner, _ids, _foreign_id = setup_library(tmp_path, monkeypatch)
    first = client.get("/radio/manage")
    assert first.status_code == 200
    assert b"SHOWING 1" in first.data and b"OF 30 MATCHING FILES" in first.data
    assert first.data.count(b'class="file-row audio-file-row"') == 25
    assert b"Track 29.mp3" in first.data
    assert b"Track 00.mp3" not in first.data.split(b'id="playlists"')[0]
    assert first.data.count(b"<option value=") >= 35  # All 30 remain available to the playlist.

    second = client.get("/radio/manage?page=2&sort=name")
    assert second.data.count(b'class="file-row audio-file-row"') == 5
    assert b"Track 29.mp3" in second.data.split(b'id="playlists"')[0]
    searched = client.get("/radio/manage?q=Special+artist")
    assert searched.data.count(b'class="file-row audio-file-row"') == 1
    assert b"Track 14.mp3" in searched.data
    assert b"465 Bytes used" in searched.data


def test_bulk_delete_checks_ownership_and_keeps_playlist_entries(tmp_path, monkeypatch):
    app, client, owner, ids, foreign_id = setup_library(tmp_path, monkeypatch)
    endpoint = "/radio/manage/audio/bulk-delete"
    token = "library-test-token"
    rejected = client.post(endpoint, data={"csrf_token": token, "file_ids": [str(ids[0]), str(foreign_id)]})
    assert rejected.status_code == 404
    with app.app_context():
        assert get_db().execute("SELECT 1 FROM radio_audio_files WHERE id=?", (ids[0],)).fetchone()

    response = client.post(endpoint, data={"csrf_token": token, "file_ids": [str(ids[0]), str(ids[1])]})
    assert response.status_code == 302
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT 1 FROM radio_audio_files WHERE id IN (?,?)", ids[:2]).fetchone() is None
        assert db.execute("SELECT 1 FROM radio_audio_files WHERE id=?", (foreign_id,)).fetchone()
        track = db.execute("SELECT audio_file_id FROM radio_playlist_tracks WHERE display_name='Track 00.mp3'").fetchone()
        assert track["audio_file_id"] is None
    assert not (Path(tmp_path) / "radio_audio" / str(owner) / "0.mp3").exists()
    assert (Path(tmp_path) / "radio_audio" / str(owner) / "2.mp3").exists()


def test_audio_lookup_skips_external_broadcasts(tmp_path, monkeypatch):
    app, client, _owner, ids, _foreign_id = setup_library(tmp_path, monkeypatch)
    frequency = 95000
    sessions = {
        "external:1": {"status": "playing", "frequency_khz": frequency, "external": True},
        "playlist:1": {"status": "playing", "frequency_khz": frequency,
                       "audio_file_id": ids[0], "track_serial": 1, "session_key": "playlist:1"},
    }
    store = app.extensions["hosted_broadcasts"]
    monkeypatch.setattr(store, "all_public", lambda: sessions)
    monkeypatch.setattr(store, "advance", lambda *_args: True)
    monkeypatch.setattr(app.extensions["radio"], "receiver_frequency", lambda _connection: frequency)
    monkeypatch.setattr(broadcast, "effective_access", lambda _user, _frequency: "ALLOWED")

    response = client.get(f"/radio/manage/audio/{ids[0]}")
    assert response.status_code == 200
    assert response.data == b"audio"

    advanced = client.post("/radio/manage/advance", data={
        "csrf_token": "library-test-token", "file_id": ids[0], "track_serial": 1,
    })
    assert advanced.status_code == 200
    assert advanced.json == {"advanced": True}
