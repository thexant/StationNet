"""Android bridge for the shared StationNet application factory."""
import os
import threading
from waitress import create_server

_lock = threading.Lock()
_server = None
_stop_event = threading.Event()


def run(data_dir):
    """Run in the foreground service's worker thread until stopped."""
    global _server
    os.environ["STATIONNET_DATA"] = data_dir
    from stationnet import create_app
    server = create_server(create_app(), host="0.0.0.0", port=8080, threads=4)
    with _lock:
        _server = server
    try:
        # Waitress's run() loops while any socket remains in its map. Closing
        # only the listener leaves browser keep-alive connections in that map.
        while not _stop_event.is_set():
            server.asyncore.loop(
                timeout=server.adj.asyncore_loop_timeout,
                map=server._map,
                use_poll=server.adj.asyncore_use_poll,
                count=1,
            )
    finally:
        server.close()
        for channel in list(server.active_channels.values()):
            channel.close()
        server.task_dispatcher.shutdown()
        with _lock:
            if _server is server:
                _server = None
            _stop_event.clear()


def stop():
    with _lock:
        _stop_event.set()
        server = _server
    if server is not None:
        server.trigger.pull_trigger()
