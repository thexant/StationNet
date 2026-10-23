import importlib.util
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_source_excludes_station_data(tmp_path):
    spec = importlib.util.spec_from_file_location("build_bundle", ROOT / "packaging" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    destination = tmp_path / "app"
    module.copy_app(destination)
    assert (destination / "stationnet" / "templates" / "setup.html").is_file()
    assert (destination / "stationnet" / "static" / "station.css").is_file()
    assert not (destination / "data").exists()


def test_android_bridge_runs_shared_app_and_stops(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("stationnet_android", ROOT / "android" / "app" / "src" / "main" / "python" / "stationnet_android.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from waitress import create_server as real_create_server
    module.create_server = lambda app, **kwargs: real_create_server(app, host="127.0.0.1", port=0, threads=2)
    monkeypatch.setenv("STATIONNET_DATA", str(tmp_path))
    worker = threading.Thread(target=module.run, args=(str(tmp_path),), daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while module._server is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert module._server is not None
        port = module._server.effective_port
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/setup", timeout=5) as response:
            assert b"Station" in response.read()
        assert (tmp_path / "station.db").is_file()
        assert (tmp_path / "secret.key").is_file()
    finally:
        module.stop()
        worker.join(timeout=10)
    assert not worker.is_alive()
