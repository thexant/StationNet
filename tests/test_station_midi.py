from io import BytesIO
import sqlite3

import pytest

from stationnet import create_app
from stationnet.db import _migrate_station_midi, get_db
from stationnet.midi import InvalidMidi, inspect_midi


def _midi(velocity=0x40):
    track = b"\x00\x90\x3c" + bytes([velocity]) + b"\x83\x60\x80\x3c\x00\x00\xff\x2f\x00"
    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0MTrk" + len(track).to_bytes(4, "big") + track


def test_station_theme_allows_extended_note_on_velocity_only():
    with pytest.raises(InvalidMidi, match="Invalid MIDI channel event"):
        inspect_midi(_midi(0x9a))
    assert inspect_midi(_midi(0x9a), allow_extended_velocity=True) == 0.5


def test_existing_station_gets_full_midi_volume(tmp_path):
    database = sqlite3.connect(tmp_path / "legacy.db")
    database.row_factory = sqlite3.Row
    database.execute("CREATE TABLE station(id INTEGER PRIMARY KEY, play_station_midi INTEGER NOT NULL DEFAULT 0)")
    database.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, station_midi_enabled INTEGER NOT NULL DEFAULT 0)")
    database.execute("INSERT INTO station(id,play_station_midi) VALUES(1,1)")
    _migrate_station_midi(database)
    assert database.execute("SELECT play_station_midi,station_midi_volume FROM station").fetchone()[:] == (1, 100)
    database.close()


def _login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"


def test_station_midi_upload_preference_radio_and_clear(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign) VALUES(?,?,?)", ("Test", "", "TST-1"))
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,1)", ("sysop", "SYS-1", "unused")).lastrowid
        member = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)", ("member", "MEM-1", "unused")).lastrowid
        db.commit()
        assert db.execute("SELECT play_station_midi FROM station").fetchone()[0] == 0
        assert db.execute("SELECT station_midi_volume FROM station").fetchone()[0] == 100
        assert db.execute("SELECT station_midi_enabled FROM users WHERE id=?", (member,)).fetchone()[0] == 0

    client = app.test_client()
    _login(client, admin)
    assert client.post("/sysop/settings/midi", data={"csrf_token": "test-token", "play_station_midi": "on"}).status_code == 302
    assert not (tmp_path / "station_theme.mid").exists()
    assert client.post("/sysop/settings/midi", data={"csrf_token": "test-token", "station_midi": (BytesIO(b"bad"), "bad.mid")}).status_code == 302
    assert not (tmp_path / "station_theme.mid").exists()

    response = client.post("/sysop/settings/midi", data={"csrf_token": "test-token", "station_midi": (BytesIO(_midi(0x9a)), "theme.mid")})
    assert response.status_code == 302
    assert (tmp_path / "station_theme.mid").read_bytes() == _midi(0x9a)
    assert b"data-music-toggle" not in client.get("/").data
    assert client.post("/sysop/settings/midi", data={"csrf_token": "test-token", "play_station_midi": "on"}).status_code == 302
    assert b"data-music-toggle" in client.get("/").data
    assert client.post("/sysop/settings/midi", data={"csrf_token": "test-token", "play_station_midi": "on", "station_midi_volume": "35"}).status_code == 302
    assert b'data-midi-volume="35"' in client.get("/").data
    assert client.post("/sysop/settings/midi", data={"csrf_token": "test-token", "play_station_midi": "on", "station_midi_volume": "-1"}).status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT station_midi_volume FROM station").fetchone()[0] == 35
    assert client.post("/sysop/settings/midi", data={"csrf_token": "test-token", "play_station_midi": "on", "station_midi_volume": "0"}).status_code == 302
    assert b'data-midi-volume="0"' in client.get("/").data
    assert client.get("/station-theme.mid").data == _midi(0x9a)

    _login(client, member)
    assert b"MUSIC: OFF" in client.get("/").data
    assert client.post("/station-theme/preference", data={"csrf_token": "test-token", "enabled": "true"}).json == {"enabled": True}
    assert b"MUSIC: ON" in client.get("/").data
    assert client.post("/station-theme/preference", data={"csrf_token": "test-token", "enabled": "false"}).json == {"enabled": False}
    assert b"MUSIC: OFF" in client.get("/").data
    assert client.post("/sysop/settings/midi/clear", data={"csrf_token": "test-token"}).status_code == 302
    assert (tmp_path / "station_theme.mid").exists()

    _login(client, admin)
    assert client.post("/sysop/settings/midi/clear", data={"csrf_token": "test-token"}).status_code == 302
    assert not (tmp_path / "station_theme.mid").exists()
    assert client.get("/station-theme.mid").status_code == 404
    assert b"data-music-toggle" not in client.get("/").data
    with app.app_context():
        assert get_db().execute("SELECT play_station_midi FROM station").fetchone()[0] == 0


def test_station_midi_preference_is_per_user(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,play_station_midi) VALUES(?,?,?,1)", ("Test", "", "TST-1"))
        first = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)", ("first", "ONE-1", "unused")).lastrowid
        second = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)", ("second", "TWO-1", "unused")).lastrowid
        db.commit()
    (tmp_path / "station_theme.mid").write_bytes(_midi())
    client = app.test_client()
    _login(client, first)
    assert client.post("/station-theme/preference", data={"csrf_token": "test-token", "enabled": "true"}).status_code == 200
    _login(client, second)
    assert b"MUSIC: OFF" in client.get("/").data
    _login(client, first)
    assert b"MUSIC: ON" in client.get("/").data
