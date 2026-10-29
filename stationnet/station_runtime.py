"""Runtime files a running station keeps in ``<data_dir>/run/``.

``station.lock`` is held with an OS file lock for the station process's whole
life. The OS drops the lock when the process exits for any reason, so a held
lock means "alive" without trusting a PID that may have been reused.
``status.json`` describes the live process; finding it while the lock is free
means the process died without cleaning up. ``stop.request`` asks the station
to shut down gracefully.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

RUN_DIR = "run"


class AlreadyRunning(RuntimeError):
    pass


def _try_lock(handle):
    try:
        if sys.platform == "win32":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle):
    if sys.platform == "win32":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _retry(action, attempts=20):
    # Windows refuses to replace or read a file another process has open for
    # a moment; the other side always lets go quickly.
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05)


class StationRuntime:
    def __init__(self, data_dir):
        self.folder = Path(data_dir) / RUN_DIR
        self.lock_path = self.folder / "station.lock"
        self.status_path = self.folder / "status.json"
        self.stop_path = self.folder / "stop.request"
        self.log_path = self.folder / "station.log"
        self._handle = None

    def acquire(self, wait=2.0):
        """Take the station lock or raise AlreadyRunning.

        Retries briefly because a launcher probing the lock holds it for an
        instant; a station that is really running holds it indefinitely.
        """
        self.folder.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+b")
        deadline = time.monotonic() + wait
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                handle.close()
                raise AlreadyRunning(f"{self.folder.parent} is already in use by another StationNet process.")
            time.sleep(0.1)
        self._handle = handle

    def release(self):
        if self._handle is None:
            return
        try:
            _unlock(self._handle)
        except OSError:
            pass
        self._handle.close()
        self._handle = None

    def is_locked(self):
        """True while some process holds the station lock."""
        if self._handle is not None:
            return True
        try:
            handle = open(self.lock_path, "r+b")
        except FileNotFoundError:
            return False
        with handle:
            if not _try_lock(handle):
                return True
            _unlock(handle)
            return False

    def write_status(self, **values):
        self.folder.mkdir(parents=True, exist_ok=True)
        values.setdefault("pid", os.getpid())
        values.setdefault("updated_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        temp = self.status_path.with_name(f".status.{os.getpid()}.tmp")
        temp.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
        _retry(lambda: os.replace(temp, self.status_path))

    def read_status(self):
        try:
            value = json.loads(_retry(lambda: self.status_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def clear_status(self):
        _retry(lambda: self.status_path.unlink(missing_ok=True))

    def request_stop(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        self.stop_path.write_text(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n",
                                  encoding="utf-8")

    def stop_requested_at(self):
        """When a pending stop was requested (epoch seconds), else None."""
        try:
            return self.stop_path.stat().st_mtime
        except OSError:
            return None

    def clear_stop(self):
        _retry(lambda: self.stop_path.unlink(missing_ok=True))
