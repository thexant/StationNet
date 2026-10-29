import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from stationnet import instance, station_manager
from stationnet.station_manager import StationManager, slugify
from stationnet.station_runtime import AlreadyRunning, StationRuntime

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("STATIONNET_DATA", "STATIONNET_HOME", "STATIONNET_STATIONS_DIR", "STATIONNET_STATION",
                 "STATIONNET_HOST", "STATIONNET_PORT", "STATIONNET_HTTPS", "STATIONNET_KIWIX_SERVE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def manager(tmp_path):
    return StationManager(tmp_path, ROOT)


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait(predicate, timeout=90, message="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.25)
    raise AssertionError(f"Timed out waiting for {message}")


def test_create_station_writes_config(manager, tmp_path):
    port = _free_port()
    info = manager.create_station("Community BBS", "community-bbs", str(port), lan=True)
    config = json.loads((tmp_path / "stations" / "community-bbs" / "station.json").read_text(encoding="utf-8"))
    assert config["id"] == "community-bbs" and config["name"] == "Community BBS"
    assert config["port"] == port and config["host"] == "0.0.0.0"
    assert (info.name, info.port, info.state, info.lan) == ("Community BBS", port, "stopped", True)
    assert [station.id for station in manager.list_stations()] == ["community-bbs"]


def test_create_station_rejects_bad_input(manager):
    port = _free_port()
    manager.create_station("Alpha", "alpha", port)
    with pytest.raises(ValueError, match="already exists"):
        manager.create_station("Alpha again", "alpha", _free_port())
    with pytest.raises(ValueError, match="another station"):
        manager.create_station("Beta", "beta", port)
    with pytest.raises(ValueError, match="Invalid station id"):
        manager.create_station("Beta", "Bad Id", _free_port())
    with pytest.raises(ValueError, match="between"):
        manager.create_station("Beta", "beta", 80)
    with pytest.raises(ValueError, match="number"):
        manager.create_station("Beta", "beta", "eighty")
    with pytest.raises(ValueError, match="name"):
        manager.create_station("  ", "beta", _free_port())
    assert [station.id for station in manager.list_stations()] == ["alpha"]


def test_create_station_rejects_port_in_use(manager):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(ValueError, match="in use"):
            manager.create_station("Gamma", "gamma", listener.getsockname()[1])


def test_suggest_port_skips_configured_and_bound(manager, monkeypatch):
    first = _free_port()
    monkeypatch.setattr(station_manager, "FIRST_PORT", first)
    manager.create_station("Alpha", "alpha", first)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", first + 1))
        listener.listen()
        assert manager.suggest_port() not in (first, first + 1)


def test_slugify():
    assert slugify("Station Alpha!") == "station-alpha"
    assert slugify("  ") == "station"
    assert instance.validate_station_id(slugify("Ünïcode -- Test__")) == "n-code-test"


def test_listing_skips_staging_and_reports_bad_config(manager, tmp_path):
    stations = tmp_path / "stations"
    (stations / ".default.migrating").mkdir(parents=True)
    (stations / "Not Valid").mkdir()
    (stations / "broken").mkdir()
    (stations / "broken" / "station.json").write_text("{nope", encoding="utf-8")
    (stations / "default").mkdir()
    listed = manager.list_stations()
    assert [station.id for station in listed] == ["default", "broken"]
    assert listed[0].port == 8080 and listed[0].state == "stopped"
    assert listed[1].state == "error" and "Cannot read" in listed[1].error


def test_states_follow_lock_and_status(manager):
    info = manager.create_station("Alpha", "alpha", _free_port())
    station = StationRuntime(info.data_dir)
    assert manager.station("alpha").state == "stopped"
    station.acquire()
    try:
        with pytest.raises(AlreadyRunning):
            StationRuntime(info.data_dir).acquire(wait=0.2)
        assert StationRuntime(info.data_dir).is_locked()
        assert manager.station("alpha").state == "starting"
        station.write_status(state="running", port=info.port)
        running = manager.station("alpha")
        assert running.state == "running" and running.addresses == [f"http://127.0.0.1:{info.port}"]
        manager.stop("alpha")
        stopping = manager.station("alpha")
        assert stopping.state == "stopping" and not stopping.can_force_stop
    finally:
        station.release()
    # Lock gone but status left behind: the process died without cleaning up.
    station.clear_stop()
    assert manager.station("alpha").state == "crashed"
    station.clear_status()
    assert manager.station("alpha").state == "stopped"


def test_child_environment_ignores_inherited_overrides(manager, monkeypatch, tmp_path):
    monkeypatch.setenv("STATIONNET_PORT", "1")
    monkeypatch.setenv("STATIONNET_DATA", str(tmp_path / "elsewhere"))
    env = manager.child_environment()
    assert "STATIONNET_PORT" not in env and "STATIONNET_DATA" not in env
    assert env["STATIONNET_HOME"] == str(tmp_path)


def _http_ok(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status < 500
    except urllib.error.HTTPError as error:
        return error.code < 500
    except OSError:
        return False


def test_two_stations_run_independently(manager):
    alpha = manager.create_station("Alpha", "alpha", _free_port())
    beta = manager.create_station("Beta", "beta", _free_port())
    try:
        manager.start("alpha")
        manager.start("beta")
        with pytest.raises(ValueError, match="already"):
            manager.start("alpha")
        for info in (alpha, beta):
            _wait(lambda: manager.station(info.id).state == "running", message=f"{info.id} running")
            assert _http_ok(f"http://127.0.0.1:{info.port}/")
        assert (alpha.data_dir / "station.db").is_file() and (beta.data_dir / "station.db").is_file()

        manager.stop("alpha")
        _wait(lambda: manager.station("alpha").state == "stopped", message="alpha stopped")
        assert not StationRuntime(alpha.data_dir).status_path.exists()
        assert manager.station("beta").state == "running" and _http_ok(f"http://127.0.0.1:{beta.port}/")

        old_pid = StationRuntime(beta.data_dir).read_status()["pid"]
        manager.restart("beta")
        _wait(lambda: manager.poll() and manager.station("beta").state == "running"
              and StationRuntime(beta.data_dir).read_status()["pid"] != old_pid, message="beta restarted")

        assert manager.force_stop("beta")
        _wait(lambda: manager.station("beta").state == "crashed", timeout=30, message="beta crashed")
    finally:
        for info in (alpha, beta):
            manager.stop(info.id)
        for info in (alpha, beta):
            try:
                _wait(lambda: not StationRuntime(info.data_dir).is_locked(), timeout=30)
            except AssertionError:
                manager.force_stop(info.id)
        for process, _started in manager._launches.values():
            process.wait(timeout=30)
