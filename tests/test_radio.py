import base64
import time

from stationnet.db import get_db
from stationnet.radio import parse_frequency

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


def test_radio_is_in_shared_shell_with_live_controls(app, initialized):
    for path in ("/terminal", "/board", "/users"):
        response = initialized.get(path)
        assert b"data-radio-scope" in response.data
        assert b"BEGIN CW TRANSMISSION" in response.data
        assert b"TRANSMIT PICTURE" in response.data
        assert b"data-radio-log" not in response.data

    state = initialized.get("/radio/state").get_json()
    assert state["frequency"] == "144.000"
    assert state["step_khz"] == 1
    assert [band["name"] for band in state["bands"]] == [
        "GENERAL COMMUNICATION", "BROADCASTS", "MORSE / CW",
        "DATA TRANSMISSIONS", "EXPERIMENTAL / UNRESTRICTED",
    ]


def test_frequency_parser_and_tuning_are_strict_and_synchronized(app, initialized):
    assert parse_frequency("144.125") == 144125
    for invalid in ("144.12", "144.1250", "144", " 144.125x", "NaN", "1000.000"):
        assert parse_frequency(invalid) is None
        assert tune(initialized, invalid).status_code == 400

    tuned = tune(initialized, "144.125")
    assert tuned.status_code == 200
    assert tuned.get_json()["frequency"] == "144.125"
    with initialized.session_transaction() as user_session:
        connection_id = user_session["connection_id"]
    assert app.extensions["radio"].receiver_frequency(connection_id) == 144125
    assert initialized.get("/radio/receive?frequency=144.126&after=0").status_code == 409


def test_cw_is_live_key_timing_without_plaintext_or_history(app, initialized):
    alice = register(app, "alice")
    assert tune(initialized, "144.650").status_code == 200
    assert tune(alice, "144.650").status_code == 200
    receiver_cursor = initialized.get("/radio/state").get_json()["cursor"]

    opened = start(alice, "144.650", tone_hz="700")
    assert opened.status_code == 201
    transmission_id = opened.get_json()["transmission_id"]
    assert signal(alice, transmission_id, "key_down").status_code == 200
    time.sleep(0.045)
    assert signal(alice, transmission_id, "key_up").status_code == 200
    assert end(alice, transmission_id).status_code == 200

    payload = initialized.get(f"/radio/receive?frequency=144.650&after={receiver_cursor}").get_json()
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
        assert tune(client, "144.650").status_code == 200
    assert tune(bob, "144.651").status_code == 200

    first = start(alice, "144.650", tone_hz="650")
    assert first.status_code == 201
    assert start(initialized, "144.650", tone_hz="650").status_code == 409
    separate = start(bob, "144.651", tone_hz="650")
    assert separate.status_code == 201
    assert end(alice, first.get_json()["transmission_id"]).status_code == 200
    assert end(bob, separate.get_json()["transmission_id"]).status_code == 200
    assert start(initialized, "144.650", tone_hz="650").status_code == 201


def test_late_listener_only_gets_subsequent_signal_events(app, initialized):
    alice = register(app, "alice")
    late = register(app, "late")
    assert tune(alice, "144.650").status_code == 200
    opened = start(alice, "144.650", tone_hz="650").get_json()
    signal(alice, opened["transmission_id"], "key_down")
    signal(alice, opened["transmission_id"], "key_up")

    late_tune = tune(late, "144.650").get_json()
    signal(alice, opened["transmission_id"], "key_down")
    signal(alice, opened["transmission_id"], "key_up")
    received = late.get(f"/radio/receive?frequency=144.650&after={late_tune['cursor']}").get_json()["events"]
    assert [event["kind"] for event in received] == ["key_down", "key_up"]


def test_sstv_scanlines_are_validated_and_received_progressively(app, initialized):
    alice = register(app, "alice")
    assert tune(initialized, "145.200").status_code == 200
    assert tune(alice, "145.200").status_code == 200
    cursor = initialized.get("/radio/state").get_json()["cursor"]
    opened = start(alice, "145.200", mode="SSTV", width="2", height="2")
    assert opened.status_code == 201
    transmission_id = opened.get_json()["transmission_id"]
    first = base64.b64encode(bytes((255, 0, 0, 0, 255, 0))).decode()
    second = base64.b64encode(bytes((0, 0, 255, 255, 255, 255))).decode()
    assert signal(alice, transmission_id, "scanline", row="1", pixels=second).status_code == 400
    assert signal(alice, transmission_id, "scanline", row="0", pixels=first).status_code == 200
    partial = initialized.get(f"/radio/receive?frequency=145.200&after={cursor}").get_json()
    assert [event["kind"] for event in partial["events"]] == ["start", "scanline"]
    assert partial["active"]["mode"] == "SSTV"
    assert signal(alice, transmission_id, "scanline", row="1", pixels=second).status_code == 200
    assert end(alice, transmission_id).status_code == 200
    complete = initialized.get(
        f"/radio/receive?frequency=145.200&after={partial['cursor']}"
    ).get_json()["events"]
    assert [event["kind"] for event in complete] == ["scanline", "end"]
    assert base64.b64decode(complete[0]["pixels"]) == base64.b64decode(second)


def test_retuning_and_disconnect_end_transmissions_safely(app, initialized):
    alice = register(app, "alice")
    assert tune(initialized, "144.650").status_code == 200
    assert tune(alice, "144.650").status_code == 200
    cursor = initialized.get("/radio/state").get_json()["cursor"]
    transmission_id = start(alice, "144.650", tone_hz="650").get_json()["transmission_id"]
    signal(alice, transmission_id, "key_down")
    assert tune(alice, "144.651").status_code == 200
    events = initialized.get(f"/radio/receive?frequency=144.650&after={cursor}").get_json()["events"]
    assert events[-1]["kind"] == "end"
    assert events[-1]["reason"] == "disconnected"
    assert start(initialized, "144.650", tone_hz="650").status_code == 201


def test_band_permissions_and_activity_scan(app, initialized):
    alice = register(app, "alice")
    assert tune(alice, "144.400").status_code == 200
    assert start(alice, "144.400", tone_hz="650").status_code == 403

    assert tune(alice, "144.650").status_code == 200
    active = start(alice, "144.650", tone_hz="650").get_json()
    assert tune(initialized, "144.000").status_code == 200
    scan = initialized.post(
        "/radio/scan",
        data={"csrf_token": csrf(initialized, "/terminal"), "frequency": "144.000", "direction": "up"},
    ).get_json()
    assert scan["found"] is True
    assert scan["frequency"] == "144.650"
    assert end(alice, active["transmission_id"]).status_code == 200


def test_logout_releases_frequency_occupancy(app, initialized):
    assert tune(initialized, "144.650").status_code == 200
    opened = start(initialized, "144.650", tone_hz="650").get_json()
    initialized.post("/logout", data={"csrf_token": csrf(initialized, "/terminal")})
    assert app.extensions["radio"]._frequency_owners == {}
    assert opened["transmission_id"] not in app.extensions["radio"]._transmissions
