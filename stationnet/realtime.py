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

    def disconnect(self, user_id, connection_id=None):
        with self._lock:
            for key in list(self._messages):
                if any(uid == user_id and (connection_id is None or conn == connection_id) for uid, conn in key):
                    del self._messages[key]
