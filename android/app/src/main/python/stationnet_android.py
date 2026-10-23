"""Android bridge for the shared StationNet application factory."""
import os
import threading
from waitress import create_server

_lock = threading.Lock()
_server = None
_stop_requested = False


def run(data_dir):
    """Run in the foreground service's worker thread until stopped."""
    global _server, _stop_requested
    os.environ["STATIONNET_DATA"] = data_dir
    from stationnet import create_app
    server = create_server(create_app(), host="0.0.0.0", port=8080, threads=4)
    with _lock:
        _server = server
        should_stop = _stop_requested
    try:
        if not should_stop:
            server.run()
    finally:
        with _lock:
            if _server is server:
                _server = None
            _stop_requested = False
        server.task_dispatcher.shutdown()
        server.close()


def stop():
    global _stop_requested
    with _lock:
        _stop_requested = True
        server = _server
    if server is not None:
        server.close()
