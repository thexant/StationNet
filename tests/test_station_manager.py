import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from stationnet import instance, station_manager
from stationnet.station_dirs import folder_slug
from stationnet.station_manager import StationManager
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
    info = manager.create_station("Community BBS", str(port), lan=True)
    assert re.fullmatch(r"station-[0-9a-f]{6}", info.id) and info.folder == info.id
    config = json.loads((tmp_path / "stations" / info.id / "station.json").read_text(encoding="utf-8"))
    assert config["id"] == info.id and config["name"] == "Community BBS"
    assert config["port"] == port and config["host"] == "0.0.0.0"
    assert (info.name, info.port, info.state, info.lan) == ("Community BBS", port, "stopped", True)
    assert info.pending_folder == "community-bbs"
    assert [station.id for station in manager.list_stations()] == [info.id]


def test_create_station_rejects_bad_input(manager):
    port = _free_port()
    alpha = manager.create_station("Alpha", port)
    with pytest.raises(ValueError, match="another station"):
        manager.create_station("Beta", port)
    with pytest.raises(ValueError, match="between 1024"):
        manager.create_station("Beta", 80)
    with pytest.raises(ValueError, match="number"):
        manager.create_station("Beta", "eighty")
    with pytest.raises(ValueError, match="between 2 and 80"):
        manager.create_station("B", _free_port())
    with pytest.raises(ValueError, match="between 2 and 80"):
        manager.create_station("x" * 81, _free_port())
    assert [station.id for station in manager.list_stations()] == [alpha.id]


def test_create_station_without_name_keeps_temporary_folder(manager):
    info = manager.create_station("  ", _free_port())
    assert "name" not in instance.load_config(info.data_dir)
    manager.poll()
    assert manager.station(info.id).folder == info.id and not info.pending_folder


def test_create_station_rejects_port_in_use(manager):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(ValueError, match="in use"):
            manager.create_station("Gamma", listener.getsockname()[1])


def test_suggest_port_skips_configured_and_bound(manager, monkeypatch):
    first = _free_port()
    monkeypatch.setattr(station_manager, "FIRST_PORT", first)
    manager.create_station("Alpha", first)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", first + 1))
        listener.listen()
        assert manager.suggest_port() not in (first, first + 1)


def test_folder_slug():
    assert folder_slug("Station Alpha!") == "station-alpha"
    assert folder_slug("  ") is None and folder_slug("🚀📡") is None
    assert instance.validate_station_id(folder_slug("Ünïcode -- Test__")) == "unicode-test"
    assert folder_slug("CON") == "con-station" and folder_slug("lpt1") == "lpt1-station"
    assert folder_slug('My/BBS: <"Prime">?*|\\') == "my-bbs-prime"
    assert len(folder_slug("x" * 200)) == 56


def _rename_in_config(info, name):
    instance.update_config(info.data_dir, name=name)


def test_named_station_folder_follows_its_name(manager):
    info = manager.create_station("Night Owl BBS", _free_port())
    (info.data_dir / "uploads").mkdir()
    (info.data_dir / "uploads" / "note.txt").write_bytes(b"keep me")
    manager.poll()
    moved = manager.station(info.id)
    assert moved.folder == "night-owl-bbs" and not moved.pending_folder
    assert not info.data_dir.exists()
    assert (moved.data_dir / "uploads" / "note.txt").read_bytes() == b"keep me"
    assert instance.resolve(manager.root, info.id).data_dir == moved.data_dir


def test_folder_is_not_renamed_while_the_station_holds_its_lock(manager):
    info = manager.create_station("", _free_port())
    runtime = StationRuntime(info.data_dir)
    runtime.acquire()
    try:
        _rename_in_config(info, "Locked Station")
        manager.poll()
        held = manager.station(info.id)
        assert held.folder == info.id and held.pending_folder == "locked-station"
        assert manager.rename_folder(info.id, force=True) == info.data_dir
    finally:
        runtime.release()
    manager.poll()
    assert manager.station(info.id).folder == "locked-station"


def test_duplicate_names_get_numbered_folders(manager):
    first = manager.create_station("Alpha", _free_port())
    second = manager.create_station("Alpha", _free_port())
    third = manager.create_station("ALPHA!", _free_port())
    manager.poll()
    folders = sorted(manager.station(info.id).folder for info in (first, second, third))
    assert folders == ["alpha", "alpha-2", "alpha-3"]
    # A station already in a numbered folder keeps it.
    for _ in range(2):
        manager.poll()
    assert sorted(manager.station(info.id).folder for info in (first, second, third)) == folders


def test_folder_names_avoid_other_station_ids_and_strays(manager):
    (manager.stations_dir / "beta").mkdir(parents=True)
    (manager.stations_dir / "beta" / "station.json").write_text(json.dumps({"id": "gamma"}), encoding="utf-8")
    (manager.stations_dir / "beta-2.txt").write_text("not a station", encoding="utf-8")
    (manager.stations_dir / "gamma-old").mkdir()
    (manager.stations_dir / "gamma-old" / "station.json").write_text(json.dumps({"id": "delta"}),
                                                                     encoding="utf-8")
    info = manager.create_station("Beta", _free_port())
    other = manager.create_station("Delta", _free_port())
    manager.poll()
    assert manager.station(info.id).folder == "beta-2"  # "beta" is taken, "beta-2.txt" is not "beta-2"
    assert manager.station(other.id).folder == "delta-2"


def test_unusable_names_leave_the_folder_alone(manager):
    info = manager.create_station("📡📡", _free_port())
    manager.poll()
    assert manager.station(info.id).folder == info.id
    _rename_in_config(info, "Aux")
    manager.poll()
    assert manager.station(info.id).folder == "aux-station"


def test_failed_rename_keeps_data_and_retries(manager, monkeypatch):
    info = manager.create_station("", _free_port())
    (info.data_dir / "station.db").write_bytes(b"database")
    _rename_in_config(info, "Retry Me")
    real_rename = os.rename

    def refuse(source, target):
        raise PermissionError("in use by another program")

    monkeypatch.setattr(os, "rename", refuse)
    manager.poll()
    failed = manager.station(info.id)
    assert failed.folder == info.id and failed.pending_folder == "retry-me"
    assert "in use by another program" in failed.rename_error
    assert (info.data_dir / "station.db").read_bytes() == b"database"
    monkeypatch.setattr(os, "rename", real_rename)
    manager.poll()  # still inside the retry delay
    assert manager.station(info.id).folder == info.id
    monkeypatch.setattr(station_manager, "RENAME_RETRY", 0)
    manager._rename_failures[info.id] = (failed.rename_error, 0)
    manager.poll()
    renamed = manager.station(info.id)
    assert renamed.folder == "retry-me" and renamed.rename_error is None
    assert (renamed.data_dir / "station.db").read_bytes() == b"database"


def test_legacy_config_gets_its_id_pinned_before_renaming(manager):
    legacy = manager.stations_dir / "old-board"
    legacy.mkdir(parents=True)
    (legacy / "station.json").write_text(json.dumps({"name": "Old Board Renamed"}), encoding="utf-8")
    manager.poll()  # pins the id
    manager.poll()  # then renames
    info = manager.station("old-board")
    assert info.folder == "old-board-renamed"
    assert instance.load_config(info.data_dir)["id"] == "old-board"


def test_folders_claiming_the_same_id_are_an_error(manager):
    for folder in ("copy-a", "copy-b"):
        (manager.stations_dir / folder).mkdir(parents=True)
        (manager.stations_dir / folder / "station.json").write_text(
            json.dumps({"id": "twin", "name": "Twin"}), encoding="utf-8")
    listed = manager.poll()
    assert [info.id for info in listed] == ["twin"]
    assert listed[0].state == "error" and "more than one folder" in listed[0].error
    assert sorted(path.name for path in manager.stations_dir.iterdir()) == ["copy-a", "copy-b"]


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
    info = manager.create_station("", _free_port())
    station = StationRuntime(info.data_dir)
    assert manager.station(info.id).state == "stopped"
    station.acquire()
    try:
        with pytest.raises(AlreadyRunning):
            StationRuntime(info.data_dir).acquire(wait=0.2)
        assert StationRuntime(info.data_dir).is_locked()
        assert manager.station(info.id).state == "starting"
        station.write_status(state="running", port=info.port)
        running = manager.station(info.id)
        assert running.state == "running" and running.addresses == [f"http://127.0.0.1:{info.port}"]
        manager.stop(info.id)
        stopping = manager.station(info.id)
        assert stopping.state == "stopping" and not stopping.can_force_stop
    finally:
        station.release()
    # Lock gone but status left behind: the process died without cleaning up.
    station.clear_stop()
    assert manager.station(info.id).state == "crashed"
    station.clear_status()
    assert manager.station(info.id).state == "stopped"


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
    alpha = manager.create_station("Alpha", _free_port())
    beta = manager.create_station("Beta", _free_port())
    manager.poll()  # both are stopped, so their folders take their names now
    folder = lambda info: manager.station(info.id).data_dir
    assert (folder(alpha).name, folder(beta).name) == ("alpha", "beta")
    try:
        manager.start(alpha.id)
        manager.start(beta.id)
        with pytest.raises(ValueError, match="already"):
            manager.start(alpha.id)
        for info in (alpha, beta):
            _wait(lambda: manager.station(info.id).state == "running", message=f"{info.id} running")
            assert _http_ok(f"http://127.0.0.1:{info.port}/")
        assert (folder(alpha) / "station.db").is_file() and (folder(beta) / "station.db").is_file()

        manager.stop(alpha.id)
        _wait(lambda: manager.station(alpha.id).state == "stopped", message="alpha stopped")
        assert not StationRuntime(folder(alpha)).status_path.exists()
        assert manager.station(beta.id).state == "running" and _http_ok(f"http://127.0.0.1:{beta.port}/")

        # Renamed while running (as Sysop settings do): the folder waits for the restart.
        instance.update_config(folder(beta), name="Beta Prime")
        for _ in range(3):
            manager.poll()
        running = manager.station(beta.id)
        assert running.folder == "beta" and running.pending_folder == "beta-prime"
        old_pid = StationRuntime(folder(beta)).read_status()["pid"]
        manager.restart(beta.id)
        _wait(lambda: manager.poll() and manager.station(beta.id).state == "running"
              and StationRuntime(folder(beta)).read_status()["pid"] != old_pid, message="beta restarted")
        assert folder(beta).name == "beta-prime" and (folder(beta) / "station.db").is_file()
        assert not (manager.stations_dir / "beta").exists()
        assert _http_ok(f"http://127.0.0.1:{beta.port}/")

        assert manager.force_stop(beta.id)
        _wait(lambda: manager.station(beta.id).state == "crashed", timeout=30, message="beta crashed")
    finally:
        for info in (alpha, beta):
            manager.stop(info.id)
        for info in (alpha, beta):
            try:
                _wait(lambda: not StationRuntime(folder(info)).is_locked(), timeout=30)
            except AssertionError:
                manager.force_stop(info.id)
        for process, _started in manager._launches.values():
            process.wait(timeout=30)
