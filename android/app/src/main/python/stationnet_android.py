"""Android bridge for the shared StationNet application factory."""
import os
import threading
import uvicorn

_lock = threading.Lock()
_server = None


def run(data_dir):
    """Run in the foreground service's worker thread until stopped."""
    global _server
    os.environ["STATIONNET_DATA"] = data_dir
    from stationnet import create_app
    from stationnet.multiplayer_socket import create_asgi_app
    server = uvicorn.Server(uvicorn.Config(
        create_asgi_app(create_app()), host="0.0.0.0", port=8080,
        ws_max_size=16 * 1024, ws_max_queue=64,
    ))
    with _lock:
        _server = server
    try:
        server.run()
    finally:
        with _lock:
            if _server is server:
                _server = None


def stop():
    with _lock:
        server = _server
    if server is not None:
        server.should_exit = True
