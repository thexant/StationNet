"""In-process, connection-scoped direct chat storage and presence helpers."""
from collections import defaultdict
from datetime import datetime, timezone
from threading import RLock


ONLINE_SECONDS = 45


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DirectChatStore:
    """Temporary direct messages. Nothing in this store is written to disk."""

    def __init__(self):
        self._lock = RLock()
        self._messages = defaultdict(list)
        self._read = {}
        self._announced = set()
        self._next_id = 1

    @staticmethod
    def _key(left_id, left_connection, right_id, right_connection):
        return tuple(sorted(((left_id, left_connection), (right_id, right_connection))))

    def send(self, sender_id, sender_connection, recipient_id, recipient_connection, body):
        key = self._key(sender_id, sender_connection, recipient_id, recipient_connection)
        with self._lock:
            message = {
                "id": self._next_id,
                "sender_id": sender_id,
                "body": body,
                "created_at": utc_now(),
            }
            self._next_id += 1
            self._messages[key].append(message)
            return message

    def list(self, left_id, left_connection, right_id, right_connection, after=0):
        key = self._key(left_id, left_connection, right_id, right_connection)
        with self._lock:
            return [dict(item) for item in self._messages.get(key, ()) if item["id"] > after]

    def mark_read(self, reader_id, reader_connection, peer_id, peer_connection, through_id):
        key = self._key(reader_id, reader_connection, peer_id, peer_connection)
        with self._lock:
            latest = max((item["id"] for item in self._messages.get(key, ())
                          if item["sender_id"] == peer_id and item["id"] <= through_id), default=0)
            self._read[(key, reader_id)] = max(self._read.get((key, reader_id), 0), latest)

    def notifications(self, recipient_id, recipient_connection, online_peers, claim=False):
        """Return unread summaries and atomically claim new alerts for one browser tab."""
        unread = []
        alerts = []
        with self._lock:
            for key, messages in self._messages.items():
                if (recipient_id, recipient_connection) not in key:
                    continue
                peer_id, peer_connection = next(participant for participant in key
                                                if participant != (recipient_id, recipient_connection))
                peer = online_peers.get(peer_id)
                if peer is None or peer["connection_id"] != peer_connection:
                    continue
                pending = [item for item in messages if item["sender_id"] == peer_id
                           and item["id"] > self._read.get((key, recipient_id), 0)]
                if not pending:
                    continue
                unread.append({"user_id": peer_id, "username": peer["username"],
                               "callsign": peer["callsign"], "count": len(pending)})
                if claim:
                    fresh = [item for item in pending if item["id"] not in self._announced]
                    if fresh:
                        alerts.append({"user_id": peer_id, "username": peer["username"],
                                       "callsign": peer["callsign"], "count": len(fresh)})
                        self._announced.update(item["id"] for item in fresh)
        return unread, alerts

    def disconnect(self, user_id, connection_id=None):
        with self._lock:
            for key in list(self._messages):
                if any(uid == user_id and (connection_id is None or conn == connection_id) for uid, conn in key):
                    ids = {item["id"] for item in self._messages[key]}
                    self._announced.difference_update(ids)
                    for reader in (key[0][0], key[1][0]):
                        self._read.pop((key, reader), None)
                    del self._messages[key]
