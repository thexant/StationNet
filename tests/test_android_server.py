"""Regression coverage for the Android foreground service's Waitress bridge."""
import importlib.util
from pathlib import Path
import socket
import sys
import threading
import types

from waitress import create_server


BRIDGE = Path(__file__).resolve().parents[1] / "android/app/src/main/python/stationnet_android.py"


def test_stop_closes_server_with_keep_alive_client(monkeypatch):
    app = types.ModuleType("stationnet")
    def application(environ, start_response):
        start_response("200 OK", [("Content-Length", "2")])
        return [b"ok"]

    app.create_app = lambda: application
    monkeypatch.setitem(sys.modules, "stationnet", app)

    spec = importlib.util.spec_from_file_location("stationnet_android_test", BRIDGE)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)

    ready = threading.Event()
    servers = []

    def make_server(application, **kwargs):
        server = create_server(application, host="127.0.0.1", port=0, threads=1)
        servers.append(server)
        ready.set()
        return server

    monkeypatch.setattr(bridge, "create_server", make_server)
    worker = threading.Thread(target=bridge.run, args=(str(BRIDGE.parent),), daemon=True)
    worker.start()
    assert ready.wait(5)

    with socket.create_connection(("127.0.0.1", servers[0].effective_port), timeout=5) as client:
        client.settimeout(5)
        client.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert b"200 OK" in client.recv(4096)
        assert worker.is_alive()
        bridge.stop()
        worker.join(5)

    assert not worker.is_alive()
    assert bridge._server is None


def test_stop_before_server_starts(monkeypatch):
    app = types.ModuleType("stationnet")
    app.create_app = lambda: lambda environ, start_response: []
    monkeypatch.setitem(sys.modules, "stationnet", app)

    spec = importlib.util.spec_from_file_location("stationnet_android_early_stop_test", BRIDGE)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    created = []

    def make_server(application, **kwargs):
        server = create_server(application, host="127.0.0.1", port=0, threads=1)
        created.append(server)
        return server

    monkeypatch.setattr(bridge, "create_server", make_server)
    bridge.stop()
    worker = threading.Thread(target=bridge.run, args=(str(BRIDGE.parent),), daemon=True)
    worker.start()
    worker.join(5)

    assert created
    assert not worker.is_alive()
    assert bridge._server is None
