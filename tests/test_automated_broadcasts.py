from pathlib import Path

import pytest

from stationnet import create_app
from stationnet.broadcast import HostedBroadcastStore
from stationnet.db import get_db


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATA_DIR": str(tmp_path),
        "DATABASE": str(tmp_path / "station.db"),
        "SECRET_KEY": "test-secret",
    })
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(id,name,description,callsign) VALUES(1,?,?,?)",
            ("Test Station", "", "AAA-BBB"),
        )
        sysop_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,1)",
            ("sysop", "CCC-DDD", "unused"),
        ).lastrowid
        member_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,0)",
            ("member", "EEE-FFF", "unused"),
        ).lastrowid
        operators = database.execute(
            "SELECT id FROM radio_permission_groups WHERE name='OPERATORS'"
        ).fetchone()[0]
        database.execute(
            "INSERT INTO radio_group_memberships(user_id,group_id) VALUES(?,?)",
            (sysop_id, operators),
        )
        playlist_id = database.execute(
            "INSERT INTO radio_playlists(owner_id,name) VALUES(?,?)",
            (sysop_id, "Alerts"),
        ).lastrowid
        database.commit()
        app.config.update(SYSOP_ID=sysop_id, MEMBER_ID=member_id, PLAYLIST_ID=playlist_id)
    return app


def login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "csrf"


def test_only_sysops_can_manage_automated_broadcasts(app):
    client = app.test_client()
    login(client, app.config["MEMBER_ID"])
    response = client.get("/radio/manage/automated")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/terminal")


def test_sysop_can_create_multiple_station_owned_definitions(app):
    client = app.test_client()
    login(client, app.config["SYSOP_ID"])
    for name, frequency in (("Hourly reminder", "7.10"), ("Safety loop", "7.20")):
        response = client.post("/radio/manage/automated", data={
            "csrf_token": "csrf", "name": name, "message": "Station message",
            "frequency": frequency, "playlist_id": app.config["PLAYLIST_ID"],
            "runtime_minutes": "20", "delay_minutes": "15",
        })
        assert response.status_code == 302
    with app.app_context():
        audio_dir = Path(app.config["DATA_DIR"]) / "radio_audio" / str(app.config["SYSOP_ID"])
        audio_dir.mkdir(parents=True)
        (audio_dir / "reminder.wav").write_bytes(b"test audio")
        audio_id = get_db().execute(
            "INSERT INTO radio_audio_files"
            "(owner_id,display_name,stored_name,media_type,size_bytes,duration_seconds,title) "
            "VALUES(?,?,?,?,?,?,?)",
            (app.config["SYSOP_ID"], "reminder.wav", "reminder.wav", "audio/wav", 10, 60, "Reminder"),
        ).lastrowid
        get_db().execute(
            "INSERT INTO radio_playlist_tracks(playlist_id,audio_file_id,display_name,position) "
            "VALUES(?,?,?,0)", (app.config["PLAYLIST_ID"], audio_id, "reminder.wav"),
        )
        get_db().commit()
        rows = get_db().execute(
            "SELECT id,name,frequency_khz,runtime_minutes,delay_minutes "
            "FROM radio_automated_broadcasts ORDER BY frequency_khz"
        ).fetchall()
        assert [tuple(row)[1:] for row in rows] == [
            ("Hourly reminder", 7100, 20, 15),
            ("Safety loop", 7200, 20, 15),
        ]
        broadcast_ids = [row["id"] for row in rows]
    page = client.get("/radio/manage/automated")
    assert page.status_code == 200
    assert b"Hourly reminder" in page.data
    assert b"Safety loop" in page.data
    for broadcast_id in broadcast_ids:
        response = client.post(
            f"/radio/manage/automated/{broadcast_id}/control",
            data={"csrf_token": "csrf", "action": "start"},
        )
        assert response.status_code == 302
    with app.app_context():
        sessions = app.extensions["hosted_broadcasts"].all_public()
        assert {key for key in sessions if key.startswith("automated:")} == {
            f"automated:{broadcast_id}" for broadcast_id in broadcast_ids
        }
        assert all(sessions[f"automated:{broadcast_id}"]["user_id"] is None
                   for broadcast_id in broadcast_ids)
    for broadcast_id in broadcast_ids:
        client.post(
            f"/radio/manage/automated/{broadcast_id}/control",
            data={"csrf_token": "csrf", "action": "stop"},
        )


class FakeRadio:
    def __init__(self):
        self.live = {}

    def start_hosted(self, owner_key, frequency, callsign, **metadata):
        if frequency in (item["frequency"] for item in self.live.values()):
            raise RuntimeError("Frequency busy.")
        self.live[owner_key] = {"frequency": frequency, **metadata}
        return len(self.live), {}

    def end_hosted(self, owner_key, reason="ended"):
        return self.live.pop(owner_key, None)

    def update_hosted(self, owner_key, **metadata):
        if owner_key not in self.live:
            return False
        self.live[owner_key].update(metadata)
        return True


def test_automated_sessions_are_independent_and_wait_after_playlist_completion(app):
    radio = FakeRadio()
    store = HostedBroadcastStore(radio)
    track = [{
        "entry_id": 1, "position": 0, "file_id": 1, "name": "reminder.wav",
        "title": "Reminder", "artist": "", "duration": 60,
    }]
    with app.app_context():
        for key, frequency in (("automated:1", 7100), ("automated:2", 7200)):
            store.start(
                key, "Station Automation", frequency, 1, "Alerts", track,
                False, 120, delay_seconds=600,
                permission_user_id=app.config["SYSOP_ID"],
                public_user_id=None, automated=True,
            )
        assert set(radio.live) == {"automated:1", "automated:2"}
        first = store.public("automated:1")
        assert first["automated"] is True
        assert first["user_id"] is None
        assert store.advance("automated:1", first["audio_file_id"], first["track_serial"])
        waiting = store.public("automated:1")
        assert waiting["status"] == "waiting"
        assert waiting["next_run_seconds"] > 590
        assert "automated:1" not in radio.live
        assert "automated:2" in radio.live
        store.stop("automated:1")
        store.stop("automated:2")
