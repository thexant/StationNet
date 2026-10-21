from stationnet import create_app
from stationnet.broadcast import HostedBroadcastStore
from stationnet.db import get_db

from .conftest import csrf
from .test_broadcast import authorize_host, upload
from .test_community import login, register


class FakeRadio:
    def __init__(self):
        self.metadata = {}

    def start_hosted(self, user_id, _frequency, _callsign, **metadata):
        self.metadata[user_id] = metadata
        return 1, {}

    def update_hosted(self, user_id, **metadata):
        self.metadata[user_id] = metadata
        return True

    def end_hosted(self, user_id, _reason="ended"):
        self.metadata.pop(user_id, None)


def sample_tracks(count=3):
    return [{"entry_id": index, "position": index - 1, "file_id": index,
             "name": f"track-{index}", "duration": 600.0}
            for index in range(1, count + 1)]


def test_store_sequential_progression_and_single_track_repeat(monkeypatch):
    monkeypatch.setattr("stationnet.radio.transmission_authorized", lambda *_args: True)
    store = HostedBroadcastStore(FakeRadio())
    tracks = sample_tracks()
    store.start(7, "HOST", 7500, 1, "ORDERED", tracks, True, 9999)
    item = store._sessions[7]
    heard = [item["current_track"]["file_id"]]
    for _ in range(3):
        item["clock_started"] -= 600.1
        store._position(item)
        heard.append(item["current_track"]["file_id"])
    assert heard == [1, 2, 3, 1]
    store.stop(7)

    store.start(8, "HOST", 7510, 2, "SINGLE", sample_tracks(1), False, 9999, True)
    item = store._sessions[8]
    serial = item["track_serial"]
    assert store.advance(8, 1, serial)
    assert item["current_track"]["file_id"] == 1
    assert item["track_serial"] == serial + 1
    store.stop(8)


def test_shuffle_cycles_do_not_mutate_saved_order_or_repeat_boundary(monkeypatch):
    monkeypatch.setattr("stationnet.radio.transmission_authorized", lambda *_args: True)
    monkeypatch.setattr("stationnet.broadcast.random.shuffle", lambda values: values.reverse())
    tracks = sample_tracks()
    store = HostedBroadcastStore(FakeRadio())
    store.start(9, "HOST", 7520, 3, "SHUFFLED", tracks, True, 9999, True)
    item = store._sessions[9]
    heard = []
    for _ in range(6):
        heard.append(item["current_track"]["file_id"])
        store.advance(9, item["current_track"]["file_id"], item["track_serial"])
    assert set(heard[:3]) == {1, 2, 3}
    assert set(heard[3:]) == {1, 2, 3}
    assert heard[2] != heard[3]
    assert [track["file_id"] for track in tracks] == [1, 2, 3]
    store.stop(9)


def test_shuffle_toggle_waits_for_transition_and_playlist_edits_are_safe(monkeypatch):
    monkeypatch.setattr("stationnet.radio.transmission_authorized", lambda *_args: True)
    monkeypatch.setattr("stationnet.broadcast.random.shuffle", lambda values: values.reverse())
    store = HostedBroadcastStore(FakeRadio())
    tracks = sample_tracks()
    store.start(10, "HOST", 7530, 4, "EDITABLE", tracks, False, 9999, False)
    item = store._sessions[10]
    current = item["current_track"]
    store.sync_playlist(10, 4, "EDITABLE", tracks, False, True)
    assert item["current_track"] == current
    assert item["shuffle"] is False
    store.advance(10, current["file_id"], item["track_serial"])
    assert item["shuffle"] is True

    remaining = [track for track in tracks if track["entry_id"] != item["current_track"]["entry_id"]]
    store.sync_playlist(10, 4, "EDITABLE", remaining, True, True)
    assert item["current_track"]["entry_id"] in {track["entry_id"] for track in remaining}
    store.sync_playlist(10, 4, "EDITABLE", [], True, True)
    assert 10 not in store._sessions


def prepare_playlist(app, client, username="alice"):
    upload(client, "program.mp3", b"\xff\xfb\x90\x64" + b"\0" * 4096)
    client.post(
        "/radio/manage/playlists",
        data={"csrf_token": csrf(client, "/radio/manage"), "name": "Local Hour"},
    )
    with app.app_context():
        database = get_db()
        playlist_id = database.execute(
            "SELECT id FROM radio_playlists WHERE owner_id=(SELECT id FROM users WHERE username=?)",
            (username,),
        ).fetchone()["id"]
        audio_id = database.execute(
            "SELECT id FROM radio_audio_files WHERE owner_id=(SELECT id FROM users WHERE username=?)",
            (username,),
        ).fetchone()["id"]
        database.execute("UPDATE radio_audio_files SET duration_seconds=600 WHERE id=?", (audio_id,))
        database.commit()
    client.post(
        f"/radio/manage/playlists/{playlist_id}/tracks",
        data={"csrf_token": csrf(client, "/radio/manage"), "audio_file_id": audio_id},
    )
    return playlist_id, audio_id


def assign(sysop, user_id, frequency="7.50"):
    return sysop.post(
        f"/sysop/radio/users/{user_id}/broadcast-frequency",
        data={"csrf_token": csrf(sysop, "/sysop/settings"), "frequency": frequency},
        follow_redirects=True,
    )


def test_assignment_is_unique_authorized_and_persistent(app, initialized):
    alice = register(app, "alice")
    bob = register(app, "bob")
    alice_id, _ = authorize_host(app, "alice")
    bob_id, _ = authorize_host(app, "bob")

    assert b"assigned 7.50 MHz" in assign(initialized, alice_id).data
    duplicate = assign(initialized, bob_id)
    assert b"already assigned" in duplicate.data

    restarted = create_app({
        "TESTING": True, "DATA_DIR": app.config["DATA_DIR"], "SECRET_KEY": "restart-secret",
    })
    restarted_client = restarted.test_client()
    login(restarted_client, "sysop")
    page = restarted_client.get("/sysop/settings")
    assert b'value="7.50"' in page.data

    # Merely assigning a frequency does not make an unauthorized account a host.
    with app.app_context():
        group_id = get_db().execute(
            "SELECT id FROM radio_permission_groups WHERE name='OPERATORS'"
        ).fetchone()["id"]
        get_db().execute(
            "DELETE FROM radio_group_memberships WHERE user_id=? AND group_id=?", (bob_id, group_id),
        )
        get_db().commit()
    denied = assign(initialized, bob_id, "7.60")
    assert b"FULL access and HOST BROADCASTS" in denied.data


def test_server_hosted_start_join_disconnect_pause_resume_and_release(app, initialized):
    alice = register(app, "alice")
    bob = register(app, "bob")
    charlie = register(app, "charlie")
    alice_id, _ = authorize_host(app, "alice")
    authorize_host(app, "bob")
    playlist_id, audio_id = prepare_playlist(app, alice)
    assign(initialized, alice_id)

    started = alice.post(
        "/radio/manage/start",
        data={"csrf_token": csrf(alice, "/radio/"), "playlist_id": playlist_id,
              "frequency": "999.99"},
    )
    assert started.status_code == 201
    assert started.get_json()["session"]["frequency"] == "7.50"

    # A separate listener joins the current server position and may fetch only while tuned.
    bob.post("/radio/tune", data={"csrf_token": csrf(bob, "/radio/"), "frequency": "7.50"})
    received = bob.get("/radio/receive?frequency=7.50&after=0").get_json()
    assert received["active"]["hosted"] is True
    assert received["active"]["audio_file_id"] == audio_id
    assert bob.get(f"/radio/manage/audio/{audio_id}").status_code == 200
    charlie.post(
        "/radio/tune", data={"csrf_token": csrf(charlie, "/radio/"), "frequency": "7.50"},
    )
    assert charlie.get("/radio/receive?frequency=7.50&after=0").get_json()["active"]["hosted"]
    assert charlie.get(f"/radio/manage/audio/{audio_id}").status_code == 200

    # A real media-ended report advances the one shared session. A stale report
    # from another listener cannot advance it a second time.
    before = bob.get("/radio/receive?frequency=7.50&after=0").get_json()["active"]
    advanced = bob.post(
        "/radio/manage/advance",
        data={"csrf_token": csrf(bob, "/radio/"), "file_id": audio_id,
              "track_serial": before["track_serial"]},
    )
    stale = charlie.post(
        "/radio/manage/advance",
        data={"csrf_token": csrf(charlie, "/radio/"), "file_id": audio_id,
              "track_serial": before["track_serial"]},
    )
    assert advanced.get_json()["advanced"] is True
    assert stale.get_json()["advanced"] is False
    bob_active = bob.get("/radio/receive?frequency=7.50&after=0").get_json()["active"]
    charlie_active = charlie.get("/radio/receive?frequency=7.50&after=0").get_json()["active"]
    assert bob_active["track_serial"] == charlie_active["track_serial"] == before["track_serial"] + 1

    # Playback is not tied to Alice's connection and remains owned by the server.
    with alice.session_transaction() as browser_session:
        app.extensions["radio"].disconnect(browser_session["connection_id"])
    with app.app_context():
        assert app.extensions["hosted_broadcasts"].active_user(alice_id)

    paused = initialized.post(
        f"/sysop/radio/users/{alice_id}/broadcast-control",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "action": "pause"},
    )
    assert paused.status_code == 302
    assert 7500 not in app.extensions["radio"].active_frequencies()
    resumed = initialized.post(
        f"/sysop/radio/users/{alice_id}/broadcast-control",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "action": "resume"},
    )
    assert resumed.status_code == 302
    assert 7500 in app.extensions["radio"].active_frequencies()

    reconnected = app.test_client()
    login(reconnected, "alice")
    stopped = reconnected.post(
        "/radio/manage/stop", data={"csrf_token": csrf(reconnected, "/radio/")},
    )
    assert stopped.status_code == 200
    assert 7500 not in app.extensions["radio"].active_frequencies()


def test_duration_and_permission_revocation_terminate_session(app, initialized):
    alice = register(app, "alice")
    alice_id, group_id = authorize_host(app, "alice")
    playlist_id, _audio_id = prepare_playlist(app, alice)
    assign(initialized, alice_id)
    alice.post(
        "/radio/manage/start",
        data={"csrf_token": csrf(alice, "/radio/"), "playlist_id": playlist_id},
    )

    store = app.extensions["hosted_broadcasts"]
    with store._lock:
        store._sessions[alice_id]["clock_started"] -= 3 * 60 * 60
    with app.app_context():
        store.refresh(alice_id)
    assert not store.active_user(alice_id)

    alice.post(
        "/radio/manage/start",
        data={"csrf_token": csrf(alice, "/radio/"), "playlist_id": playlist_id},
    )
    initialized.post(
        f"/sysop/radio/groups/{group_id}/capabilities",
        data={"csrf_token": csrf(initialized, "/sysop/settings")},
    )
    assert not store.active_user(alice_id)

