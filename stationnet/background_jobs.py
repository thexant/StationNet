"""Run long station work on a worker thread so no request or writer waits on it."""
import threading

_lock = threading.Lock()
_jobs = {}


def status(key):
    """Latest state for a job key: {"state": running|done|error, "result": ..., "error": ...} or None."""
    with _lock:
        job = _jobs.get(key)
        return dict(job) if job else None


def finished_keys(prefix):
    with _lock:
        return [key for key, job in _jobs.items() if key.startswith(prefix) and job["state"] != "running"]


def forget(key):
    with _lock:
        _jobs.pop(key, None)


def running(key):
    job = status(key)
    return bool(job and job["state"] == "running")


def start(app, key, work):
    """Call work(database) on a daemon thread with its own connection.

    Returns False when a job with the same key is already running.
    """
    with _lock:
        if _jobs.get(key, {}).get("state") == "running":
            return False
        _jobs[key] = {"state": "running", "result": None, "error": None}

    def run():
        from .db import close_db, get_db
        try:
            with app.app_context():
                try:
                    database = get_db()
                    # Waiting for the write lock is fine here; nobody is waiting on this thread.
                    database.execute("PRAGMA busy_timeout=60000")
                    result = work(database)
                    update = {"state": "done", "result": result}
                except Exception as error:
                    app.logger.exception("Background job %s failed", key)
                    update = {"state": "error", "error": str(error) or error.__class__.__name__}
                finally:
                    close_db()
        except Exception as error:  # app context itself failed
            update = {"state": "error", "error": str(error)}
        with _lock:
            _jobs[key].update(update)

    threading.Thread(target=run, name=f"job-{key}", daemon=True).start()
    return True
