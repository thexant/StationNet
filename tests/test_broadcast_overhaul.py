from pathlib import Path
from datetime import datetime, timezone

import pytest

from stationnet import create_app
from stationnet.broadcast import HostedBroadcastStore
from stationnet.programs import BroadcastCoordinator, compile_program
from stationnet.radio import RadioStore


def _track(entry, duration=30, kind="music", file_id=None, text=""):
    return {"entry_id": entry, "position": entry,
            "file_id": None if kind in {"morse", "sstv", "live"} else (file_id or entry),
            "name": f"item-{entry}", "title": f"Item {entry}", "artist": "",
            "duration": duration, "action_type": kind, "text": text}


def test_mixed_timeline_preserves_action_metadata_and_controls(monkeypatch):
    monkeypatch.setattr("stationnet.radio.transmission_authorized", lambda *_: True)
    radio = RadioStore()
    store = HostedBroadcastStore(radio)
    tracks = [_track(1), _track(2, kind="morse", file_id=None, text="CQ TEST")]
    result = store.start(7, "N0CALL", 145000, -1, "Mixed", tracks, False, 600)
    assert result["action_type"] == "music"
    assert store.skip(7)
    result = store.public(7)
    assert result["action_type"] == "morse"
    assert result["action_text"] == "CQ TEST"
    assert store.pause(7) and store.public(7)["status"] == "paused"
    assert store.resume(7) and store.public(7)["status"] == "playing"
    assert store.set_live_mix(7, True, .2, "connection-a")
    active = radio.current(145000)
    assert active["mic_active"] is True
    assert active["ducking"] == pytest.approx(.2)
    store.stop(7)


def test_live_injection_interrupts_then_resumes_current_track(monkeypatch):
    monkeypatch.setattr("stationnet.radio.transmission_authorized", lambda *_: True)
    store = HostedBroadcastStore(RadioStore())
    store.start(9, "N0CALL", 145010, -2, "Show", [_track(1), _track(2)], False, 600)
    announcement = _track(99, duration=2, kind="announcement")
    assert store.inject(9, announcement)
    assert store.public(9)["action_type"] == "announcement"
    assert store.skip(9)
    resumed = store.public(9)
    assert resumed["audio_file_id"] == 1
    assert resumed["track_serial"] == 2
    store.stop(9)


def test_program_compiler_expands_legacy_playlist_and_repeats_morse(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    coordinator = app.extensions["broadcast_coordinator"]
    coordinator._closed = True
    coordinator._timer.cancel()
    with app.app_context():
        from stationnet.db import get_db
        database = get_db()
        database.execute("INSERT INTO station(id,name,description,callsign) VALUES(1,'Test','','TEST')")
        database.execute("INSERT INTO users(id,username,callsign,password_hash,is_admin) VALUES(1,'sysop','N0CALL','x',1)")
        folder = Path(tmp_path) / "radio_audio" / "1"; folder.mkdir(parents=True)
        (folder / "one.wav").write_bytes(b"RIFFfakeWAVE")
        database.execute("INSERT INTO radio_audio_files(id,owner_id,display_name,stored_name,media_type,size_bytes,duration_seconds,title) VALUES(1,1,'one.wav','one.wav','audio/wav',12,4,'One')")
        database.execute("INSERT INTO radio_playlists(id,owner_id,name) VALUES(1,1,'Legacy')")
        database.execute("INSERT INTO radio_playlist_tracks(playlist_id,audio_file_id,display_name,position) VALUES(1,1,'one.wav',0)")
        database.execute("INSERT INTO radio_programs(id,owner_id,name) VALUES(1,1,'Mixed')")
        database.execute("INSERT INTO radio_program_actions(program_id,action_type,playlist_id,position) VALUES(1,'playlist',1,0)")
        database.execute("INSERT INTO radio_program_actions(program_id,action_type,text_content,duration_seconds,repeat_count,position) VALUES(1,'morse','CQ',2,2,1)")
        database.commit()
        program, items = compile_program(database, 1)
        assert program["name"] == "Mixed"
        assert [item["action_type"] for item in items] == ["music", "morse", "morse"]
        assert items[0]["title"] == "One"
        assert items[1]["text"] == "CQ"


class _MixStore:
    def __init__(self): self.calls = []
    def set_live_mix(self, *args, **kwargs): self.calls.append((args, kwargs))
    def public(self, _key): return {"frequency_khz": 145000}


def test_controller_lease_rejects_concurrent_operator_and_recovers_disconnect(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    original = app.extensions["broadcast_coordinator"]
    original._closed = True; original._timer.cancel()
    fake = _MixStore(); coordinator = BroadcastCoordinator(app, fake)
    coordinator._closed = True; coordinator._timer.cancel()
    coordinator.acquire("show", 1, "a")
    with pytest.raises(RuntimeError):
        coordinator.acquire("show", 2, "b")
    assert coordinator.heartbeat("show", 1, "a")
    assert coordinator.release("show", 1, "a")
    coordinator.acquire("show", 2, "b")
    assert coordinator.heartbeat("show", 2, "b")
    coordinator._leases["show"]["expires"] = 0
    assert coordinator.expire_leases() == ["show"]
    assert fake.calls[-1][0][:2] == ("show", False)


def test_schema_keeps_legacy_playlists_and_adds_program_automation(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    coordinator = app.extensions["broadcast_coordinator"]
    coordinator._closed = True; coordinator._timer.cancel()
    with app.app_context():
        from stationnet.db import get_db
        names = {row[0] for row in get_db().execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"radio_playlists", "radio_automated_broadcasts", "radio_programs",
                "radio_program_actions", "radio_broadcast_content",
                "radio_automation_rules"} <= names


def test_program_ui_and_mutations_enforce_host_permission(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    coordinator = app.extensions["broadcast_coordinator"]
    coordinator._closed = True; coordinator._timer.cancel()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with app.app_context():
        from stationnet.db import get_db
        database = get_db()
        database.execute("INSERT INTO station(id,name,description,callsign) VALUES(1,'Test','','TEST')")
        database.execute("INSERT INTO users(id,username,callsign,password_hash,is_admin,last_seen,connection_id) VALUES(1,'sysop','SYS-001','x',1,?,'host-a')", (now,))
        database.execute("INSERT INTO users(id,username,callsign,password_hash,is_admin,last_seen,connection_id) VALUES(2,'listener','USR-002','x',0,?,'listener-a')", (now,))
        operator = database.execute("SELECT id FROM radio_permission_groups WHERE name='OPERATORS'").fetchone()[0]
        database.execute("INSERT INTO radio_group_memberships(user_id,group_id) VALUES(1,?)", (operator,))
        database.commit()
    client = app.test_client()
    with client.session_transaction() as auth:
        auth.update(user_id=1, connection_id="host-a", csrf_token="token")
    assert client.get("/radio/manage/programs").status_code == 200
    assert client.get("/radio/manage/automation").status_code == 200
    response = client.post("/radio/manage/programs", data={"csrf_token": "token", "name": "Morning"})
    assert response.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT name FROM radio_programs WHERE owner_id=1").fetchone()[0] == "Morning"
    with client.session_transaction() as auth:
        auth.update(user_id=2, connection_id="listener-a", csrf_token="token")
    assert client.get("/radio/manage/programs").status_code == 403
    assert client.post("/radio/manage/programs", data={"csrf_token": "token", "name": "Denied"}).status_code == 403
