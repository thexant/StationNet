"""MIDI validation and the existing mixed-format broadcast clock."""
import io
import struct
from time import monotonic

import pytest

from stationnet import create_app
from stationnet.broadcast import _available_tracks, _valid_audio
from stationnet.db import get_db
from stationnet.midi import InvalidMidi, inspect_midi


def vlq(value):
    result = [value & 0x7f]
    value >>= 7
    while value:
        result.insert(0, 0x80 | (value & 0x7f))
        value >>= 7
    return bytes(result)


def midi_file(tracks=None):
    if tracks is None:
        tracks = [b"\0\xff\x51\x03\x07\xa1\x20\0\xc0\x28\0\x90\x3c\x64"
                  + vlq(480) + b"\x80\x3c\0\0\xff\x2f\0"]
    header = b"MThd" + struct.pack(">IHHH", 6, 0 if len(tracks) == 1 else 1,
                                     len(tracks), 480)
    return header + b"".join(b"MTrk" + struct.pack(">I", len(track)) + track for track in tracks)


def test_midi_tempo_channels_and_validation_limits():
    tempo = b"\0\xff\x51\x03\x07\xa1\x20" + vlq(480) + b"\xff\x51\x03\x0f\x42\x40" + vlq(480) + b"\xff\x2f\0"
    notes = b"\0\xc1\x2a\0\x91\x40\x64\0\x99\x24\x7f" + vlq(960) + b"\x81\x40\0\0\x89\x24\0\0\xff\x2f\0"
    assert inspect_midi(midi_file([tempo, notes])) == pytest.approx(1.5)
    assert inspect_midi(midi_file()) == pytest.approx(.5)
    for bad in (b"not midi", midi_file()[:-1], midi_file().replace(b"MTrk", b"JUNK"),
                midi_file([b"\0\xff\x2f\0"]), midi_file() + b"extra"):
        with pytest.raises(InvalidMidi):
            inspect_midi(bad)
    with pytest.raises(InvalidMidi):
        inspect_midi(midi_file([b"\0\x90\x3c\x64" * 200_001 + b"\0\xff\x2f\0"]))


def test_upload_and_mixed_playlist_broadcast(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(id,name,description,callsign) VALUES(1,'Test','','TST-001')")
        db.execute("INSERT INTO users(id,username,callsign,password_hash,is_admin) "
                   "VALUES(1,'sysop','SYS-001','test',1)")
        db.execute("INSERT INTO radio_group_memberships(user_id,group_id) "
                   "SELECT 1,id FROM radio_permission_groups WHERE name='OPERATORS'")
        db.execute("INSERT INTO radio_playlists(owner_id,name) VALUES(1,'Mixed')")
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1, csrf_token="token")
    payload = midi_file()
    response = client.post("/radio/manage/audio", data={"csrf_token": "token",
        "file": [(io.BytesIO(payload), "first.mid"), (io.BytesIO(payload), "second.midi")]})
    assert response.status_code == 302
    assert client.post("/radio/manage/audio", data={"csrf_token": "token",
        "file": [(io.BytesIO(payload), "valid.mid"), (io.BytesIO(b"not-midi"), "broken.midi")]}).status_code == 302
    with app.app_context():
        db = get_db()
        rows = db.execute("SELECT * FROM radio_audio_files ORDER BY id").fetchall()
        assert len(rows) == 2
        assert all(row["media_type"] == "audio/midi" and row["duration_seconds"] == .5 for row in rows)
        # A conventional file in the same playlist retains the legacy media path.
        path = tmp_path / "radio_audio" / "1" / "existing.wav"
        path.write_bytes(b"RIFF" + b"\0" * 4 + b"WAVEfmt " + b"\0" * 100 + b"data")
        db.execute("INSERT INTO radio_audio_files(owner_id,display_name,stored_name,media_type,size_bytes,duration_seconds) "
                   "VALUES(1,'existing.wav','existing.wav','audio/wav',120,10)")
        ids = [rows[0]["id"], db.execute("SELECT id FROM radio_audio_files WHERE stored_name='existing.wav'").fetchone()[0]]
        db.executemany("INSERT INTO radio_playlist_tracks(playlist_id,audio_file_id,display_name,position) VALUES(1,?,?,?)",
                       [(file_id, name, index) for index, (file_id, name) in enumerate(zip(ids, ("first.mid", "existing.wav")))])
        db.commit()
        tracks = _available_tracks(db, 1)
        assert [track["media_type"] for track in tracks] == ["audio/midi", "audio/wav"]
        store = app.extensions["hosted_broadcasts"]
        live = store.start(1, "SYS-001", 7200, 1, "Mixed", tracks, True, 600)
        assert live["audio_media_type"] == "audio/midi"
        assert app.extensions["radio"].current(7200)["audio_media_type"] == "audio/midi"
        # A late receiver gets the authoritative MIDI offset, not a new local start.
        store._sessions[1]["clock_started"] = monotonic() - .2
        store.refresh(1)
        assert app.extensions["radio"].current(7200)["track_offset"] == pytest.approx(.2, abs=.05)
        assert store.skip(1)
        assert store.public(1)["audio_media_type"] == "audio/wav"
        assert app.extensions["radio"].current(7200)["audio_media_type"] == "audio/wav"
        assert store.skip(1)
        assert store.public(1)["audio_media_type"] == "audio/midi"
        store.stop(1)
        automated = store.start("automated:1", "AUTO", 7300, 1, "Mixed", tracks,
                                False, 600, automated=True, permission_user_id=1)
        assert automated["automated"] is True
        assert app.extensions["radio"].current(7300)["audio_media_type"] == "audio/midi"
        store.stop("automated:1")
    assert not _valid_audio(payload, ".mp3")
