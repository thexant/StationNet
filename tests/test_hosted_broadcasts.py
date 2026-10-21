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


def test_browser_duration_correction_advances_an_overdue_track(monkeypatch):
    monkeypatch.setattr("stationnet.radio.transmission_authorized", lambda *_args: True)
    store = HostedBroadcastStore(FakeRadio())
    store.start(11, "HOST", 7540, 5, "TIMED", sample_tracks(), True, 9999)
    item = store._sessions[11]
    item["clock_started"] -= 10
    assert store.correct_duration(11, 1, item["track_serial"], 5.0)
    assert item["current_track"]["file_id"] == 2
    assert item["track_offset"] < 5.1
    store.stop(11)


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


def tagged_mp3(title, artist):
    def frame(name, value):
        encoded = b"\x03" + value.encode("utf-8")
        return name.encode("ascii") + len(encoded).to_bytes(4, "big") + b"\0\0" + encoded

    tag = frame("TIT2", title) + frame("TPE1", artist)
    size = bytes(((len(tag) >> 21) & 0x7f, (len(tag) >> 14) & 0x7f,
                  (len(tag) >> 7) & 0x7f, len(tag) & 0x7f))
    return b"ID3\x03\0\0" + size + tag + b"\xff\xfb\x90\x64" + b"\0" * 256


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


def test_registered_broadcast_identity_is_owner_scoped_persistent_and_live(app, initialized):
    alice = register(app, "alice")
    bob = register(app, "bob")
    alice_id, _ = authorize_host(app, "alice")
    bob_id, _ = authorize_host(app, "bob")
    assign(initialized, alice_id, "7.50")
    assign(initialized, bob_id, "7.60")

    # An unnamed assignment falls back to its callsign for listeners.
    fallback = alice.post(
        "/radio/tune",
        data={"csrf_token": csrf(alice, "/radio/"), "frequency": "7.50"},
    ).get_json()["identity"]
    assert fallback["name"] == fallback["callsign"]
    assert fallback["configured_name"] == ""

    saved = alice.post(
        "/radio/manage/identity",
        data={"csrf_token": csrf(alice, "/radio/manage"),
              "broadcast_name": "The <Mix> FM",
              "broadcast_message": "Local news & late-night music."},
        follow_redirects=True,
    )
    assert b"Broadcast identity saved" in saved.data
    assert b"The &lt;Mix&gt; FM" in saved.data

    # Duplicate labels are display text, not identifiers.
    duplicate = bob.post(
        "/radio/manage/identity",
        data={"csrf_token": csrf(bob, "/radio/manage"),
              "broadcast_name": "The <Mix> FM", "broadcast_message": "Bob's service"},
        follow_redirects=True,
    )
    assert b"Broadcast identity saved" in duplicate.data

    tuned = bob.post(
        "/radio/tune",
        data={"csrf_token": csrf(bob, "/radio/"), "frequency": "7.50"},
    ).get_json()
    assert tuned["identity"]["name"] == "The <Mix> FM"
    assert tuned["identity"]["message"] == "Local news & late-night music."

    # Existing receiver polling observes edits without a retune or reload.
    alice.post(
        "/radio/manage/identity",
        data={"csrf_token": csrf(alice, "/radio/manage"),
              "broadcast_name": "Community Radio", "broadcast_message": "Updated bulletin"},
    )
    received = bob.get("/radio/receive?frequency=7.50&after=0").get_json()
    assert received["identity"]["name"] == "Community Radio"
    assert received["identity"]["message"] == "Updated bulletin"

    # The owner-only route cannot be used to overwrite another assignment.
    bob.post(
        "/radio/manage/identity",
        data={"csrf_token": csrf(bob, "/radio/manage"),
              "broadcast_name": "Bob Radio", "broadcast_message": ""},
    )
    with app.app_context():
        rows = get_db().execute(
            "SELECT user_id,broadcast_name FROM radio_broadcast_assignments ORDER BY user_id"
        ).fetchall()
        assert {row["user_id"]: row["broadcast_name"] for row in rows} == {
            alice_id: "Community Radio", bob_id: "Bob Radio",
        }

    too_long = alice.post(
        "/radio/manage/identity",
        data={"csrf_token": csrf(alice, "/radio/manage"),
              "broadcast_name": "X" * 81, "broadcast_message": ""},
        follow_redirects=True,
    )
    assert b"cannot exceed 80 characters" in too_long.data

    restarted = create_app({
        "TESTING": True, "DATA_DIR": app.config["DATA_DIR"], "SECRET_KEY": "restart-secret",
    })
    restarted_client = restarted.test_client()
    login(restarted_client, "alice")
    assert b"Community Radio" in restarted_client.get("/radio/manage").data

    # Revocation removes identity even for a receiver still on the old frequency.
    assign(initialized, alice_id, "")
    assert bob.get("/radio/receive?frequency=7.50&after=0").get_json()["identity"] is None
    away = bob.post(
        "/radio/tune",
        data={"csrf_token": csrf(bob, "/radio/"), "frequency": "7.70"},
    ).get_json()
    assert away["identity"] is None


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

    timing_active = bob.get("/radio/receive?frequency=7.50&after=0").get_json()["active"]
    timing = bob.post(
        "/radio/manage/timing",
        data={"csrf_token": csrf(bob, "/radio/"), "file_id": audio_id,
              "track_serial": timing_active["track_serial"], "duration": "599.5"},
    )
    assert timing.get_json()["updated"] is True
    with app.app_context():
        assert get_db().execute(
            "SELECT duration_seconds FROM radio_audio_files WHERE id=?", (audio_id,),
        ).fetchone()[0] == 599.5

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


def test_receiver_now_playing_uses_embedded_metadata_and_clears_on_stop(app, initialized):
    alice = register(app, "alice")
    bob = register(app, "bob")
    charlie = register(app, "charlie")
    alice_id, _ = authorize_host(app, "alice")
    assign(initialized, alice_id)
    upload(alice, "fallback-name.mp3", tagged_mp3("Midnight Signal", "The Relays"))
    alice.post(
        "/radio/manage/playlists",
        data={"csrf_token": csrf(alice, "/radio/manage"), "name": "Metadata Hour"},
    )
    with app.app_context():
        database = get_db()
        audio = database.execute(
            "SELECT id,title,artist FROM radio_audio_files WHERE owner_id=?", (alice_id,),
        ).fetchone()
        playlist_id = database.execute(
            "SELECT id FROM radio_playlists WHERE owner_id=?", (alice_id,),
        ).fetchone()["id"]
        assert (audio["title"], audio["artist"]) == ("Midnight Signal", "The Relays")
    alice.post(
        f"/radio/manage/playlists/{playlist_id}/tracks",
        data={"csrf_token": csrf(alice, "/radio/manage"), "audio_file_id": audio["id"]},
    )
    alice.post(
        "/radio/manage/start",
        data={"csrf_token": csrf(alice, "/radio/"), "playlist_id": playlist_id},
    )

    # Tuning returns the authoritative current item immediately; polling does not
    # have to wait for a transition before the display can be populated.
    tuned = bob.post(
        "/radio/tune", data={"csrf_token": csrf(bob, "/radio/"), "frequency": "7.50"},
    ).get_json()
    assert tuned["broadcast"] == {
        "active": True, "artist": "The Relays", "kind": "automated",
        "label": "AUTOMATED PLAYBACK", "title": "Midnight Signal",
    }
    assert tuned["active"]["track_title"] == "Midnight Signal"
    assert tuned["active"]["track_artist"] == "The Relays"

    with app.app_context():
        database = get_db()
        charlie_id = database.execute(
            "SELECT id FROM users WHERE username='charlie'"
        ).fetchone()["id"]
        denied_group = database.execute(
            "INSERT INTO radio_permission_groups(name) VALUES('NOW PLAYING DENY')"
        ).lastrowid
        database.execute(
            "INSERT INTO radio_group_memberships(user_id,group_id) VALUES(?,?)",
            (charlie_id, denied_group),
        )
        database.execute(
            "INSERT INTO radio_permission_rules(group_id,scope_type,start_khz,end_khz,permission) "
            "VALUES(?,'frequency',7500,7500,'DENIED')", (denied_group,),
        )
        database.commit()
    denied = charlie.post(
        "/radio/tune", data={"csrf_token": csrf(charlie, "/radio/"), "frequency": "7.50"},
    ).get_json()
    assert denied["access"] == "DENIED"
    assert denied["active"] is denied["broadcast"] is denied["identity"] is None
    assert all(item["frequency"] != "7.50"
               for item in charlie.get("/radio/manage/status").get_json()["directory"])

    alice.post("/radio/manage/stop", data={"csrf_token": csrf(alice, "/radio/")})
    stopped = bob.get("/radio/receive?frequency=7.50&after=0").get_json()
    assert stopped["active"] is None
    assert stopped["broadcast"] == {
        "active": False, "artist": "", "kind": "inactive",
        "label": "OFF AIR", "title": "",
    }

    restarted = create_app({
        "TESTING": True, "DATA_DIR": app.config["DATA_DIR"], "SECRET_KEY": "restart-secret",
    })
    restarted_bob = restarted.test_client()
    login(restarted_bob, "bob")
    after_restart = restarted_bob.post(
        "/radio/tune",
        data={"csrf_token": csrf(restarted_bob, "/radio/"), "frequency": "7.50"},
    ).get_json()
    assert after_restart["active"] is None
    assert after_restart["broadcast"]["kind"] == "inactive"
    assert after_restart["broadcast"]["title"] == ""


def test_live_voice_is_identified_without_track_metadata(app, initialized):
    alice = register(app, "voicealice")
    for client in (alice, initialized):
        client.post(
            "/radio/tune",
            data={"csrf_token": csrf(client, "/radio/"), "frequency": "145.20"},
        )
    started = alice.post(
        "/radio/transmit/start",
        data={"csrf_token": csrf(alice, "/radio/"), "frequency": "145.20", "mode": "VOICE"},
    )
    assert started.status_code == 201
    received = initialized.get("/radio/receive?frequency=145.20&after=0").get_json()
    assert received["broadcast"] == {
        "active": True, "artist": "", "kind": "live_voice",
        "label": "LIVE VOICE TRANSMISSION", "title": "",
    }


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

