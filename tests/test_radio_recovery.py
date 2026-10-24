"""Receiver leases recover after idle browser/network periods."""

from unittest import TestCase
from unittest.mock import patch

from stationnet.radio import RadioStore


class RadioReceiverRecoveryTests(TestCase):
    def test_expired_listener_rejoins_same_frequency_without_losing_cursor(self):
        store = RadioStore()
        with patch("stationnet.radio.monotonic", return_value=0):
            cursor = store.tune("listener", 1, 101500)
            store.start_hosted("host", 101500, "HOST", audio_file_id=7)

        with patch("stationnet.radio.monotonic", return_value=61):
            events, next_cursor, active = store.receive("listener", 1, 101500, cursor)
            tuned = store.receiver_frequency("listener")

        self.assertEqual(tuned, 101500)
        self.assertEqual([event["kind"] for event in events], [])  # expired events are not replayed
        self.assertGreaterEqual(next_cursor, cursor)
        self.assertEqual(active["audio_file_id"], 7)
        self.assertEqual(active["hosted"], True)

    def test_mismatched_frequency_still_rejected(self):
        store = RadioStore()
        store.tune("listener", 1, 101500)
        with self.assertRaisesRegex(ValueError, "tuning changed"):
            store.receive("listener", 1, 102000, 0)

    def test_server_side_receiver_drop_rejoins_without_restarting_host(self):
        store = RadioStore()
        cursor = store.tune("listener", 1, 101500)
        store.start_hosted("host", 101500, "HOST", audio_file_id=7)
        hosted_id = store.current(101500)["id"]
        store._receivers.pop("listener")  # simulate lost in-memory listener lease

        _events, next_cursor, active = store.receive("listener", 1, 101500, cursor)

        self.assertEqual(active["id"], hosted_id)
        self.assertEqual(store.current(101500)["id"], hosted_id)
        self.assertGreaterEqual(next_cursor, cursor)
