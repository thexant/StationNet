import base64
import json
import time
import pytest

from stationnet.db import get_db
from stationnet.radio import (can_host_broadcast, effective_access, parse_frequency,
                              transmission_authorized)

from .conftest import csrf
from .test_community import register


def tune(client, frequency):
    return client.post("/radio/tune", data={"csrf_token": csrf(client, "/terminal"), "frequency": frequency})


def start(client, frequency, mode="CW", **extra):
    return client.post(
        "/radio/transmit/start",
        data={"csrf_token": csrf(client, "/terminal"), "frequency": frequency, "mode": mode, **extra},
    )


def signal(client, transmission_id, kind, **extra):
    return client.post(
        "/radio/transmit/signal",
        data={"csrf_token": csrf(client, "/terminal"), "transmission_id": transmission_id, "kind": kind, **extra},
    )


def end(client, transmission_id):
    return client.post(
        "/radio/transmit/end",
        data={"csrf_token": csrf(client, "/terminal"), "transmission_id": transmission_id},
    )


def test_compact_radio_is_in_shared_shell_and_console_has_advanced_controls(app, initialized):
    for path in ("/terminal", "/board", "/users"):
        response = initialized.get(path)
        assert b"data-radio-player" in response.data
        assert b"data-radio-player-toggle" in response.data
        assert b'class="radio-player minimized"' in response.data
        assert b"POWER: OFF" in response.data
        assert b"OPEN RADIO" in response.data
        assert b"data-radio-scope" not in response.data
        assert b"BEGIN CW TRANSMISSION" not in response.data
        assert b"TRANSMIT PICTURE" not in response.data
        assert b"data-radio-log" not in response.data

    console = initialized.get("/radio/")
    assert console.status_code == 200
    assert b"data-radio-console" in console.data
    assert b"data-radio-player" not in console.data
    assert b"data-radio-power" in console.data
    assert b"data-radio-scope" in console.data
    assert b"data-radio-mode" not in console.data
    assert b"BEGIN CW TRANSMISSION" in console.data
    assert b"TRANSMIT PICTURE" in console.data

    state = initialized.get("/radio/state").get_json()
    assert state["frequency"] == "7.00"
    assert state["powered"] is False
    assert state["step_khz"] == 500
    assert [band["name"] for band in state["bands"]] == [
        "BROADCASTS", "MORSE / CW", "DATA TRANSMISSIONS",
        "GENERAL COMMUNICATION", "EXPERIMENTAL / UNRESTRICTED",
    ]
    assert [(band["start"], band["end"]) for band in state["bands"]] == [
        ("7.00", "13.99"), ("14.00", "27.99"), ("28.00", "144.99"),
        ("145.00", "432.99"), ("433.00", "999.99"),
    ]
    with initialized.session_transaction() as user_session:
        connection_id = user_session["connection_id"]
    assert app.extensions["radio"].receiver_frequency(connection_id) is None


def test_power_off_disconnects_receiver_and_ends_transmission(app, initialized):
    assert tune(initialized, "14.50").status_code == 200
    opened = start(initialized, "14.50", tone_hz="650").get_json()
    response = initialized.post(
        "/radio/power-off", data={"csrf_token": csrf(initialized, "/terminal")}
    )
    assert response.status_code == 200
    assert response.get_json()["powered"] is False
    with initialized.session_transaction() as user_session:
        connection_id = user_session["connection_id"]
    assert app.extensions["radio"].receiver_frequency(connection_id) is None
    assert opened["transmission_id"] not in app.extensions["radio"]._transmissions


def test_frequency_parser_and_tuning_are_strict_and_synchronized(app, initialized):
    assert parse_frequency("145.25") == 145250
    assert parse_frequency("145.5") == 145500
    assert parse_frequency("145") == 145000
    for invalid in ("145.125", "145.2500", " 145.25x", "NaN", "1000.00"):
        assert parse_frequency(invalid) is None
        assert tune(initialized, invalid).status_code == 400

    tuned = tune(initialized, "145.25")
    assert tuned.status_code == 200
    assert tuned.get_json()["frequency"] == "145.25"
    with initialized.session_transaction() as user_session:
        connection_id = user_session["connection_id"]
    assert app.extensions["radio"].receiver_frequency(connection_id) == 145250
    assert initialized.get("/radio/receive?frequency=145.26&after=0").status_code == 409


def test_tuning_is_continuous_across_bands_and_unassigned_spectrum(app, initialized):
    # Start from the broadcast preset, then cross its former upper boundary using
    # the same 0.50 MHz steps used by both radio interfaces.
    for expected in ("7.00", "7.50", "8.00", "8.50", "9.00"):
        response = tune(initialized, expected)
        assert response.status_code == 200
        assert response.get_json()["frequency"] == expected
    broadcast = tune(initialized, "13.50").get_json()
    assert broadcast["frequency"] == "13.50"
    assert broadcast["band"]["name"] == "BROADCASTS"

    morse = tune(initialized, "14.00").get_json()
    assert morse["band"]["name"] == "MORSE / CW"
    assert tune(initialized, "14.50").get_json()["frequency"] == "14.50"
    assert tune(initialized, "14.00").get_json()["frequency"] == "14.00"
    assert tune(initialized, "13.50").get_json()["frequency"] == "13.50"
    outside = tune(initialized, "6.50").get_json()
    assert outside["band"]["name"] == "OUTSIDE DEFINED BAND"


def test_range_permissions_apply_outside_presets_and_manual_entry_cannot_bypass_denial(app, initialized):
    alice = register(app, "alice")
    with app.app_context():
        database = get_db()
        user_id = database.execute("SELECT id FROM users WHERE username='alice'").fetchone()["id"]
        granted = database.execute(
            "INSERT INTO radio_permission_groups(name) VALUES('CONTINUOUS TEST')"
        ).lastrowid
        denied = database.execute(
            "INSERT INTO radio_permission_groups(name) VALUES('CONTINUOUS DENIAL')"
        ).lastrowid
        database.executemany(
            "INSERT INTO radio_group_memberships(user_id,group_id) VALUES(?,?)",
            [(user_id, granted), (user_id, denied)],
        )
        database.execute(
            "INSERT INTO radio_permission_rules(group_id,scope_type,start_khz,end_khz,permission) "
            "VALUES(?,'range',1000,6990,'FULL')", (granted,),
        )
        database.execute(
            "INSERT INTO radio_permission_rules(group_id,scope_type,start_khz,end_khz,permission) "
            "VALUES(?,'frequency',6500,6500,'DENIED')", (denied,),
        )
        database.commit()
        assert effective_access(user_id, 6000) == "FULL"
        assert effective_access(user_id, 6500) == "DENIED"

    tuned = tune(alice, "6.00").get_json()
    assert tuned["band"]["name"] == "OUTSIDE DEFINED BAND"
    assert tuned["access"] == "FULL"
    # FULL permission does not invent a transmission capability outside configured bands.
    assert start(alice, "6.00", tone_hz="650").status_code == 403

    restricted = tune(alice, "6.50").get_json()
    assert restricted["frequency"] == "6.50"
    assert restricted["access"] == "DENIED"
    assert start(alice, "6.50", tone_hz="650").status_code == 403


def test_cw_is_live_key_timing_without_plaintext_or_history(app, initialized):
    alice = register(app, "alice")
    assert tune(initialized, "14.50").status_code == 200
    assert tune(alice, "14.50").status_code == 200
    receiver_cursor = initialized.get("/radio/state").get_json()["cursor"]

    opened = start(alice, "14.50", tone_hz="700")
    assert opened.status_code == 201
    transmission_id = opened.get_json()["transmission_id"]
    assert signal(alice, transmission_id, "key_down").status_code == 200
    time.sleep(0.045)
    assert signal(alice, transmission_id, "key_up").status_code == 200
    assert end(alice, transmission_id).status_code == 200

    payload = initialized.get(f"/radio/receive?frequency=14.50&after={receiver_cursor}").get_json()
    assert [event["kind"] for event in payload["events"]] == ["start", "key_down", "key_up", "end"]
    assert payload["events"][1]["tone_hz"] == 700
    assert payload["events"][2]["duration_ms"] >= 35
    assert all("body" not in event and "text" not in event for event in payload["events"])
    with app.app_context():
        assert get_db().execute("SELECT name FROM sqlite_master WHERE name='radio_messages'").fetchone() is None


def test_one_transmitter_per_frequency_and_other_frequencies_are_independent(app, initialized):
    alice = register(app, "alice")
    bob = register(app, "bob")
    for client in (initialized, alice):
        assert tune(client, "14.50").status_code == 200
    assert tune(bob, "14.51").status_code == 200

    first = start(alice, "14.50", tone_hz="650")
    assert first.status_code == 201
    assert start(initialized, "14.50", tone_hz="650").status_code == 409
    separate = start(bob, "14.51", tone_hz="650")
    assert separate.status_code == 201
    assert end(alice, first.get_json()["transmission_id"]).status_code == 200
    assert end(bob, separate.get_json()["transmission_id"]).status_code == 200
    assert start(initialized, "14.50", tone_hz="650").status_code == 201


def test_live_voice_half_duplex_signaling_alternating_speakers_and_disconnect(app, initialized):
    alice = register(app, "voicealice")
    bob = register(app, "voicebob")
    for client in (initialized, alice, bob):
        assert tune(client, "145.20").status_code == 200

    opened = start(alice, "145.20", mode="VOICE")
    assert opened.status_code == 201
    transmission_id = opened.get_json()["transmission_id"]
    assert opened.get_json()["event"]["transmission_type"] == "LIVE_VOICE"
    assert opened.get_json()["event"]["mode"] == "VOICE"
    assert start(bob, "145.20", mode="VOICE").status_code == 409

    with initialized.session_transaction() as listener_session:
        listener_peer = listener_session["connection_id"]
    peers = alice.get(f"/radio/voice/peers?transmission_id={transmission_id}")
    assert peers.status_code == 200
    assert listener_peer in {peer["peer_id"] for peer in peers.get_json()["peers"]}

    offer = {"type": "offer", "sdp": "test offer without media content"}
    relayed = alice.post(
        "/radio/voice/signals",
        data={"csrf_token": csrf(alice, "/terminal"), "transmission_id": transmission_id,
              "target": listener_peer, "type": "offer", "payload": json.dumps(offer)},
    )
    assert relayed.status_code == 200
    inbox = initialized.get(
        f"/radio/voice/signals?transmission_id={transmission_id}"
    ).get_json()["signals"]
    assert inbox[0]["type"] == "offer"
    assert inbox[0]["payload"] == offer
    assert "stamp" not in inbox[0]
    assert initialized.get(
        f"/radio/voice/signals?transmission_id={transmission_id}"
    ).get_json()["signals"] == []

    assert end(alice, transmission_id).status_code == 200
    bob_voice = start(bob, "145.20", mode="VOICE")
    assert bob_voice.status_code == 201
    bob_id = bob_voice.get_json()["transmission_id"]
    bob.post("/radio/power-off", data={"csrf_token": csrf(bob, "/terminal")})
    assert bob_id not in app.extensions["radio"]._transmissions
    assert start(initialized, "145.20", mode="VOICE").status_code == 201


def test_live_voice_requires_voice_capable_band_and_valid_signaling_peer(app, initialized):
    alice = register(app, "voicecap")
    assert tune(alice, "14.50").status_code == 200
    assert start(alice, "14.50", mode="VOICE").status_code == 403

    # FULL access alone cannot bypass the configured operation capabilities.
    assert tune(alice, "145.20").status_code == 200
    assert start(alice, "145.20", mode="CW", tone_hz="650").status_code == 403
    assert start(alice, "145.20", mode="SSTV", width="2", height="2").status_code == 403

    opened = start(alice, "145.20", mode="VOICE").get_json()
    rejected = alice.post(
        "/radio/voice/signals",
        data={"csrf_token": csrf(alice, "/terminal"),
              "transmission_id": opened["transmission_id"], "target": "not-a-peer",
              "type": "offer", "payload": json.dumps({"type": "offer", "sdp": "x"})},
    )
    assert rejected.status_code == 403


def test_late_listener_only_gets_subsequent_signal_events(app, initialized):
    alice = register(app, "alice")
    late = register(app, "late")
    assert tune(alice, "14.50").status_code == 200
    opened = start(alice, "14.50", tone_hz="650").get_json()
    signal(alice, opened["transmission_id"], "key_down")
    signal(alice, opened["transmission_id"], "key_up")

    late_tune = tune(late, "14.50").get_json()
    signal(alice, opened["transmission_id"], "key_down")
    signal(alice, opened["transmission_id"], "key_up")
    received = late.get(f"/radio/receive?frequency=14.50&after={late_tune['cursor']}").get_json()["events"]
    assert [event["kind"] for event in received] == ["key_down", "key_up"]


def test_sstv_scanlines_are_validated_and_received_progressively(app, initialized):
    alice = register(app, "alice")
    assert tune(initialized, "28.20").status_code == 200
    assert tune(alice, "28.20").status_code == 200
    cursor = initialized.get("/radio/state").get_json()["cursor"]
    opened = start(alice, "28.20", mode="SSTV", width="2", height="2")
    assert opened.status_code == 201
    transmission_id = opened.get_json()["transmission_id"]
    first = base64.b64encode(bytes((255, 0, 0, 0, 255, 0))).decode()
    second = base64.b64encode(bytes((0, 0, 255, 255, 255, 255))).decode()
    assert signal(alice, transmission_id, "scanline", row="1", pixels=second).status_code == 400
    assert signal(alice, transmission_id, "scanline", row="0", pixels=first).status_code == 200
    partial = initialized.get(f"/radio/receive?frequency=28.20&after={cursor}").get_json()
    assert [event["kind"] for event in partial["events"]] == ["start", "scanline"]
    assert partial["active"]["mode"] == "SSTV"
    assert signal(alice, transmission_id, "scanline", row="1", pixels=second).status_code == 200
    assert end(alice, transmission_id).status_code == 200
    complete = initialized.get(
        f"/radio/receive?frequency=28.20&after={partial['cursor']}"
    ).get_json()["events"]
    assert [event["kind"] for event in complete] == ["scanline", "end"]
    assert base64.b64decode(complete[0]["pixels"]) == base64.b64decode(second)


def test_retuning_and_disconnect_end_transmissions_safely(app, initialized):
    alice = register(app, "alice")
    assert tune(initialized, "14.50").status_code == 200
    assert tune(alice, "14.50").status_code == 200
    cursor = initialized.get("/radio/state").get_json()["cursor"]
    transmission_id = start(alice, "14.50", tone_hz="650").get_json()["transmission_id"]
    signal(alice, transmission_id, "key_down")
    assert tune(alice, "14.51").status_code == 200
    events = initialized.get(f"/radio/receive?frequency=14.50&after={cursor}").get_json()["events"]
    assert events[-1]["kind"] == "end"
    assert events[-1]["reason"] == "disconnected"
    assert start(initialized, "14.50", tone_hz="650").status_code == 201


def test_band_permissions_and_activity_scan(app, initialized):
    alice = register(app, "alice")
    assert tune(alice, "7.00").status_code == 200
    assert start(alice, "7.00", tone_hz="650").status_code == 403

    assert tune(alice, "14.50").status_code == 200
    active = start(alice, "14.50", tone_hz="650").get_json()
    assert tune(initialized, "7.00").status_code == 200
    scan = initialized.post(
        "/radio/scan",
        data={"csrf_token": csrf(initialized, "/terminal"), "frequency": "7.00", "direction": "up"},
    ).get_json()
    assert scan["found"] is True
    assert scan["frequency"] == "14.50"
    assert end(alice, active["transmission_id"]).status_code == 200


def test_logout_releases_frequency_occupancy(app, initialized):
    assert tune(initialized, "14.50").status_code == 200
    opened = start(initialized, "14.50", tone_hz="650").get_json()
    initialized.post("/logout", data={"csrf_token": csrf(initialized, "/terminal")})
    assert app.extensions["radio"]._frequency_owners == {}
    assert opened["transmission_id"] not in app.extensions["radio"]._transmissions


def test_default_membership_multiple_groups_and_permission_precedence(app, initialized):
    alice = register(app, "alice")
    with app.app_context():
        database = get_db()
        user_id = database.execute("SELECT id FROM users WHERE username='alice'").fetchone()["id"]
        default = database.execute(
            "SELECT id FROM radio_permission_groups WHERE is_default=1"
        ).fetchone()["id"]
        assert database.execute(
            "SELECT 1 FROM radio_group_memberships WHERE user_id=? AND group_id=?",
            (user_id, default),
        ).fetchone()
        operators = database.execute(
            "INSERT INTO radio_permission_groups(name) VALUES('FIELD OPERATORS')"
        ).lastrowid
        restricted = database.execute(
            "INSERT INTO radio_permission_groups(name) VALUES('RESTRICTED')"
        ).lastrowid
        database.executemany(
            "INSERT INTO radio_group_memberships(user_id,group_id) VALUES(?,?)",
            [(user_id, operators), (user_id, restricted)],
        )
        database.execute(
            "INSERT INTO radio_permission_rules(group_id,scope_type,start_khz,end_khz,permission) "
            "VALUES(?,'frequency',145250,145250,'FULL')", (operators,),
        )
        database.commit()
        assert effective_access(user_id, 145250) == "FULL"
        # An unrelated group with no matching rule is UNSET and cannot cancel a grant.
        assert effective_access(user_id, 145260) == "FULL"
        database.execute(
            "INSERT INTO radio_permission_rules(group_id,scope_type,start_khz,end_khz,permission) "
            "VALUES(?,'range',145000,145500,'DENIED')", (restricted,),
        )
        database.commit()
        assert effective_access(user_id, 145250) == "DENIED"

    denied = tune(alice, "145.25").get_json()
    assert denied["access"] == "DENIED"
    assert alice.get("/radio/receive?frequency=145.25&after=0").status_code == 403
    assert start(alice, "145.25", tone_hz="650").status_code == 403


def test_sysop_group_management_and_live_revocation(app, initialized):
    alice = register(app, "alice")
    initialized.post(
        "/sysop/radio/groups",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "name": "Emergency Services"},
    )
    with app.app_context():
        database = get_db()
        group_id = database.execute(
            "SELECT id FROM radio_permission_groups WHERE name='EMERGENCY SERVICES'"
        ).fetchone()["id"]
        user_id = database.execute("SELECT id FROM users WHERE username='alice'").fetchone()["id"]

    initialized.post(
        f"/sysop/radio/groups/{group_id}/rules",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "scope_type": "frequency",
              "start": "14.50", "permission": "DENIED"},
    )
    initialized.post(
        f"/sysop/radio/users/{user_id}",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "group_id": str(group_id)},
    )
    assert tune(alice, "14.50").get_json()["access"] == "DENIED"

    initialized.post(
        f"/sysop/radio/groups/{group_id}/rules",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "scope_type": "frequency",
              "start": "14.50", "permission": "FULL"},
    )
    assert tune(alice, "14.50").get_json()["access"] == "FULL"
    transmission_id = start(alice, "14.50", tone_hz="650").get_json()["transmission_id"]
    initialized.post(
        f"/sysop/radio/groups/{group_id}/rules",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "scope_type": "frequency",
              "start": "14.50", "permission": "DENIED"},
    )
    assert transmission_id not in app.extensions["radio"]._transmissions
    assert signal(alice, transmission_id, "heartbeat").status_code == 409

    initialized.post(
        f"/sysop/radio/groups/{group_id}/delete",
        data={"csrf_token": csrf(initialized, "/sysop/settings")},
    )
    with app.app_context():
        database = get_db()
        assert database.execute(
            "SELECT 1 FROM radio_group_memberships WHERE user_id=? AND group_id=?",
            (user_id, group_id),
        ).fetchone() is None
        assert database.execute(
            "SELECT 1 FROM radio_group_memberships m JOIN radio_permission_groups g ON g.id=m.group_id "
            "WHERE m.user_id=? AND g.is_default=1", (user_id,),
        ).fetchone()


def test_builtin_operators_group_rules_save_and_change_effective_access(app, initialized):
    with app.app_context():
        database = get_db()
        operator_group = database.execute(
            "SELECT id FROM radio_permission_groups WHERE name='OPERATORS'"
        ).fetchone()["id"]
        broadcast_band = database.execute(
            "SELECT id FROM radio_bands WHERE name='BROADCASTS'"
        ).fetchone()["id"]
        sysop_id = database.execute("SELECT id FROM users WHERE is_admin=1").fetchone()["id"]
        assert effective_access(sysop_id, 7000) == "FULL"

    response = initialized.post(
        f"/sysop/radio/groups/{operator_group}/rules",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "scope_type": "band",
              "band_id": str(broadcast_band), "permission": "LISTEN"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"OPERATORS: band BROADCASTS rule updated (LISTEN)." in response.data
    with app.app_context():
        database = get_db()
        rule = database.execute(
            "SELECT permission FROM radio_permission_rules "
            "WHERE group_id=? AND scope_type='band' AND band_id=?",
            (operator_group, broadcast_band),
        ).fetchone()
        assert rule["permission"] == "LISTEN"
        assert effective_access(sysop_id, 7000) == "LISTEN"

    with app.app_context():
        general_band = get_db().execute(
            "SELECT id FROM radio_bands WHERE name='GENERAL COMMUNICATION'"
        ).fetchone()["id"]
    added = initialized.post(
        f"/sysop/radio/groups/{operator_group}/rules",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "scope_type": "band",
              "band_id": str(general_band), "permission": "DENIED"},
        follow_redirects=True,
    )
    assert b"BAND / GENERAL COMMUNICATION" in added.data
    assert b"DENIED" in added.data
    with app.app_context():
        database = get_db()
        added_rule = database.execute(
            "SELECT id FROM radio_permission_rules "
            "WHERE group_id=? AND scope_type='band' AND band_id=?",
            (operator_group, general_band),
        ).fetchone()
        assert added_rule is not None

    initialized.post(
        f"/sysop/radio/rules/{added_rule['id']}/delete",
        data={"csrf_token": csrf(initialized, "/sysop/settings")},
    )
    readded = initialized.post(
        f"/sysop/radio/groups/{operator_group}/rules",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "scope_type": "band",
              "band_id": str(general_band), "permission": "FULL"},
        follow_redirects=True,
    )
    assert b"BAND / GENERAL COMMUNICATION" in readded.data
    with app.app_context():
        assert get_db().execute(
            "SELECT permission FROM radio_permission_rules "
            "WHERE group_id=? AND scope_type='band' AND band_id=?",
            (operator_group, general_band),
        ).fetchone()["permission"] == "FULL"


def test_broadcast_hosting_requires_capability_full_access_and_no_denial(app, initialized):
    alice = register(app, "alice")
    with app.app_context():
        database = get_db()
        user_id = database.execute(
            "SELECT id FROM users WHERE username='alice'"
        ).fetchone()["id"]
        broadcast_band = database.execute(
            "SELECT id FROM radio_bands WHERE name='BROADCASTS'"
        ).fetchone()["id"]

    created = initialized.post(
        "/sysop/radio/groups",
        data={"csrf_token": csrf(initialized, "/sysop/settings"),
              "name": "Broadcast Hosts", "host_broadcasts": "on"},
    )
    assert created.status_code == 302
    with app.app_context():
        database = get_db()
        host_group = database.execute(
            "SELECT id,host_broadcasts FROM radio_permission_groups WHERE name='BROADCAST HOSTS'"
        ).fetchone()
        full_group = database.execute(
            "INSERT INTO radio_permission_groups(name) VALUES('BROADCAST FULL')"
        ).lastrowid
        denied_group = database.execute(
            "INSERT INTO radio_permission_groups(name) VALUES('BROADCAST DENIED')"
        ).lastrowid
        database.execute(
            "INSERT INTO radio_permission_rules(group_id,scope_type,band_id,permission) "
            "VALUES(?,'band',?,'FULL')", (full_group, broadcast_band),
        )
        database.execute(
            "INSERT INTO radio_permission_rules(group_id,scope_type,band_id,permission) "
            "VALUES(?,'band',?,'DENIED')", (denied_group, broadcast_band),
        )
        database.commit()
        assert host_group["host_broadcasts"] == 1

    initialized.post(
        f"/sysop/radio/users/{user_id}",
        data={"csrf_token": csrf(initialized, "/sysop/settings"),
              "group_id": [str(host_group["id"])]},
    )
    with app.app_context():
        assert effective_access(user_id, 7000) == "LISTEN"
        assert can_host_broadcast(user_id, 7000) is False

    initialized.post(
        f"/sysop/radio/users/{user_id}",
        data={"csrf_token": csrf(initialized, "/sysop/settings"),
              "group_id": [str(host_group["id"]), str(full_group)]},
    )
    with app.app_context():
        assert effective_access(user_id, 7000) == "FULL"
        assert can_host_broadcast(user_id, 7000) is True
        assert transmission_authorized(user_id, 7000, "HOSTED_BROADCAST") is True

    with alice.session_transaction() as user_session:
        connection_id = user_session["connection_id"]
    store = app.extensions["radio"]
    store.tune(connection_id, user_id, 7000)
    hosted_id, _opened = store.start(
        connection_id, user_id, 7000, "ALICE", "HOSTED_BROADCAST"
    )
    initialized.post(
        f"/sysop/radio/groups/{host_group['id']}/capabilities",
        data={"csrf_token": csrf(initialized, "/sysop/settings")},
    )
    assert hosted_id not in store._transmissions
    initialized.post(
        f"/sysop/radio/groups/{host_group['id']}/capabilities",
        data={"csrf_token": csrf(initialized, "/sysop/settings"), "host_broadcasts": "on"},
    )

    initialized.post(
        f"/sysop/radio/users/{user_id}",
        data={"csrf_token": csrf(initialized, "/sysop/settings"),
              "group_id": [str(host_group["id"]), str(full_group), str(denied_group)]},
    )
    with app.app_context():
        assert effective_access(user_id, 7000) == "DENIED"
        assert can_host_broadcast(user_id, 7000) is False
        assert transmission_authorized(user_id, 7000, "HOSTED_BROADCAST") is False

    with app.app_context():
        assert get_db().execute(
            "SELECT host_broadcasts FROM radio_permission_groups WHERE id=?",
            (host_group["id"],),
        ).fetchone()["host_broadcasts"] == 1


def test_band_transmission_capabilities_are_configurable_without_locking_tuning(app, initialized):
    state = initialized.get("/radio/state").get_json()
    by_name = {band["name"]: band for band in state["bands"]}
    assert by_name["MORSE / CW"]["supports_cw"] == 1
    assert by_name["MORSE / CW"]["supports_sstv"] == 0
    assert by_name["DATA TRANSMISSIONS"]["supports_cw"] == 0
    assert by_name["DATA TRANSMISSIONS"]["supports_sstv"] == 1
    assert by_name["EXPERIMENTAL / UNRESTRICTED"]["supports_cw"] == 1
    assert by_name["EXPERIMENTAL / UNRESTRICTED"]["supports_sstv"] == 1
    assert by_name["GENERAL COMMUNICATION"]["supports_live_voice"] == 1
    assert by_name["GENERAL COMMUNICATION"]["supports_hosted_broadcasts"] == 0
    assert by_name["BROADCASTS"]["supports_live_voice"] == 0
    assert by_name["BROADCASTS"]["supports_hosted_broadcasts"] == 1

    with app.app_context():
        bands = [dict(row) for row in get_db().execute(
            "SELECT * FROM radio_bands ORDER BY id"
        )]
    form = {"csrf_token": csrf(initialized, "/sysop/settings"), "settings_section": "radio"}
    for band in bands:
        prefix = f"band_{band['id']}"
        form[f"{prefix}_name"] = band["name"]
        form[f"{prefix}_start"] = f"{band['start_khz'] / 1000:.2f}"
        form[f"{prefix}_end"] = f"{band['end_khz'] / 1000:.2f}"
    general = next(band for band in bands if band["name"] == "GENERAL COMMUNICATION")
    form[f"band_{general['id']}_hosted_broadcasts"] = "on"
    response = initialized.post("/sysop/settings", data=form)
    assert response.status_code == 302

    with app.app_context():
        sysop_id = get_db().execute("SELECT id FROM users WHERE is_admin=1").fetchone()["id"]
        assert transmission_authorized(sysop_id, 145000, "LIVE_VOICE") is False
        assert transmission_authorized(sysop_id, 145000, "HOSTED_BROADCAST") is True
    # Capability flags govern future transmit modes, not continuous receiver tuning.
    assert tune(initialized, "145.00").status_code == 200
    assert tune(initialized, "144.50").status_code == 200


def test_occupancy_reports_type_owner_frequency_and_lifecycle_for_all_types(app):
    store = app.extensions["radio"]
    store.tune("voice-owner", 10, 145000)
    store.tune("broadcast-owner", 20, 145000)
    store.tune("listener", 30, 145000)
    transmission_id, opened = store.start(
        "voice-owner", 10, 145000, "VOICE1", "LIVE_VOICE"
    )
    assert opened["transmission_type"] == "LIVE_VOICE"
    assert opened["owner"] == {"user_id": 10, "callsign": "VOICE1"}
    assert opened["frequency_khz"] == 145000
    assert opened["state"] == "active"
    assert opened["started_at"]

    with pytest.raises(RuntimeError, match="Frequency busy"):
        store.start("broadcast-owner", 20, 145000, "HOST20", "HOSTED_BROADCAST")
    with pytest.raises(LookupError):
        store.signal("broadcast-owner", transmission_id, "heartbeat")
    with pytest.raises(LookupError):
        store.end("broadcast-owner", transmission_id)

    _events, _cursor, active = store.receive("listener", 30, 145000, 0)
    assert active == {
        "id": transmission_id,
        "mode": "LIVE_VOICE",
        "transmission_type": "LIVE_VOICE",
        "owner": {"user_id": 10, "callsign": "VOICE1"},
        "callsign": "VOICE1",
        "frequency_khz": 145000,
        "frequency": "145.00",
        "started_at": active["started_at"],
        "state": "active",
    }
    closed = store.end("voice-owner", transmission_id)
    assert closed["kind"] == "end"
    assert closed["state"] == "ended"
    assert closed["ended_at"]
    assert store.active_frequencies() == []
