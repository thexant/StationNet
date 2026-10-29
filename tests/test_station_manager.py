import json
import os
import re
import socket
import time
from datetime import datetime
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


SYSOP = {"username": "sysop", "password": "correct horse battery", "confirm_password": "correct horse battery",
         "description": "Night shift", "registration_mode": "private"}


def test_create_station_prepares_the_first_sysop(manager):
    info = manager.create_station("Night Owl", _free_port(), sysop=SYSOP)
    text = (info.data_dir / instance.SETUP_NAME).read_text(encoding="utf-8")
    setup = json.loads(text)
    assert setup["name"] == "Night Owl" and setup["username"] == "sysop"
    assert setup["registration_mode"] == "private" and setup["password_hash"].startswith("scrypt:")
    assert "correct horse" not in text


@pytest.mark.parametrize("name, changes, message", [
    ("", {}, "between 2 and 80"),
    ("Valid", {"username": "x"}, "usernames"),
    ("Valid", {"confirm_password": "something else"}, "do not match"),
    ("Valid", {"password": "short", "confirm_password": "short"}, "at least 10"),
    ("Valid", {"registration_mode": "anyone"}, "registration policy"),
])
def test_create_station_rejects_a_bad_sysop(manager, name, changes, message):
    with pytest.raises(ValueError, match=message):
        manager.create_station(name, _free_port(), sysop={**SYSOP, **changes})
    assert manager.list_stations() == []


def test_create_station_with_a_chosen_address(manager):
    info = manager.create_station("Gamma", _free_port(), host="127.0.0.1", https=True)
    assert (info.access, info.https) == ("local", True)
    with pytest.raises(ValueError, match="IPv4"):
        manager.create_station("Delta", _free_port(), host="not-an-ip")
    with pytest.raises(ValueError, match="not an address of this computer"):
        manager.create_station("Delta", _free_port(), host="192.0.2.77")


def test_suggest_name_skips_taken_names(manager):
    assert manager.suggest_name() == "Station 1"
    manager.create_station("Station 1", _free_port())
    manager.create_station("station 2", _free_port())
    assert manager.suggest_name() == "Station 3"


def test_update_station_writes_its_config(manager):
    alpha = manager.create_station("Alpha", _free_port())
    beta = manager.create_station("Beta", _free_port())
    port = _free_port()
    info = manager.update_station(alpha.id, "Alpha Prime", port, station_manager.LAN_HOST, https=True)
    config = instance.load_config(info.data_dir)
    assert (config["port"], config["host"], config["https"]) == (port, "0.0.0.0", True)
    assert config["name"] == config["pending_name"] == "Alpha Prime"
    assert (info.name, info.port, info.access) == ("Alpha Prime", port, "lan")
    assert info.pending_folder == "alpha-prime"
    with pytest.raises(ValueError, match="another station"):
        manager.update_station(alpha.id, "Alpha", beta.port, station_manager.LOCAL_HOST)
    with pytest.raises(ValueError, match="between 2 and 80"):
        manager.update_station(alpha.id, "", port, station_manager.LOCAL_HOST)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        busy = listener.getsockname()[1]
        with pytest.raises(ValueError, match="in use"):
            manager.update_station(alpha.id, "Alpha", busy, station_manager.LOCAL_HOST)
        # An unchanged port is the station's own and is not probed again.
        instance.update_config(manager.station(beta.id).data_dir, port=busy)
        assert manager.update_station(beta.id, "Beta", busy, station_manager.LOCAL_HOST).port == busy


def test_update_station_keeps_an_unnamed_station_unnamed(manager):
    info = manager.create_station("", _free_port())
    manager.update_station(info.id, info.name, info.port, info.host, https=True)
    config = instance.load_config(info.data_dir)
    assert "name" not in config and "pending_name" not in config and config["https"] is True


def test_update_station_renames_a_prepared_setup(manager):
    info = manager.create_station("Night Owl", _free_port(), sysop=SYSOP)
    manager.update_station(info.id, "Early Bird", info.port, info.host)
    setup = json.loads((info.data_dir / instance.SETUP_NAME).read_text(encoding="utf-8"))
    assert setup["name"] == "Early Bird" and setup["username"] == "sysop"


def test_running_station_reports_settings_waiting_for_a_restart(manager):
    info = manager.create_station("Alpha", _free_port())
    runtime = StationRuntime(info.data_dir)
    runtime.acquire()
    try:
        runtime.write_status(state="running", host=info.host, port=info.port, https=False)
        assert manager.station(info.id).restart_needed == []
        updated = manager.update_station(info.id, "Alpha Prime", _free_port(), station_manager.LAN_HOST,
                                         https=True)
        assert updated.restart_needed == ["name", "port", "network access", "HTTPS proxy"]
        assert updated.folder == info.folder  # the rename waits for the station to stop
    finally:
        runtime.release()
        runtime.clear_status()
    assert manager.station(info.id).restart_needed == []


def _set_station_theme(data_dir, theme):
    import sqlite3
    with sqlite3.connect(data_dir / "station.db") as database:
        database.execute("CREATE TABLE IF NOT EXISTS station (id INTEGER PRIMARY KEY, site_theme TEXT)")
        database.execute("INSERT OR REPLACE INTO station (id, site_theme) VALUES (1, ?)", (theme,))
    database.close()


def test_station_theme_defaults_without_a_database(manager):
    info = manager.create_station("Alpha", _free_port())
    assert info.theme == "cyan"
    assert not (info.data_dir / "station.db").exists()
    assert manager.palette(info)["green"] == station_manager.themes.palette("cyan")["green"]


def test_station_theme_follows_the_station_setting(manager):
    info = manager.create_station("Alpha", _free_port())
    _set_station_theme(info.data_dir, "amber")
    assert manager.station(info.id).theme == "amber"
    _set_station_theme(info.data_dir, "violet")
    info = manager.station(info.id)
    assert info.theme == "violet"
    assert manager.palette(info) == station_manager.themes.palette("violet")


def test_station_theme_falls_back_for_unreadable_database(manager):
    info = manager.create_station("Alpha", _free_port())
    (info.data_dir / "station.db").write_bytes(b"not a database")
    assert manager.station(info.id).theme == "cyan"


def test_station_palette_reads_custom_themes(manager):
    info = manager.create_station("Alpha", _free_port())
    _set_station_theme(info.data_dir, "custom-sunset")
    info = manager.station(info.id)
    default = station_manager.themes.palette("cyan")
    assert manager.palette(info) == default  # theme file missing
    site = {key: "#123456" for key in station_manager.themes.SITE_KEYS}
    (info.data_dir / "themes").mkdir()
    (info.data_dir / "themes" / "sunset.json").write_text(json.dumps(
        {"id": "sunset", "name": "Sunset", "author": "Tester", "site": site}), encoding="utf-8")
    assert manager.palette(info)["green"] == "#123456"


def test_update_hours_and_extension(manager):
    info = manager.create_station("Alpha", _free_port())
    assert info.hours is None
    info = manager.update_hours(info.id, {"enabled": True, "open": "8am", "close": "21:00", "sleep": True})
    assert (info.hours.open, info.hours.close, info.hours.sleep) == ("08:00", "21:00", True)
    until = datetime(2026, 10, 29, 8, 0)
    manager.set_extended(info.id, until)
    assert manager.station(info.id).extended_until == until
    assert instance.load_config(info.data_dir)["hours_extended_until"] == "2026-10-29T08:00"
    # Saving hours again ends a running extension.
    info = manager.update_hours(info.id, {"enabled": False, "open": "8am", "close": "21:00"})
    assert info.hours is None and info.extended_until is None
    with pytest.raises(ValueError, match="must differ"):
        manager.update_hours(info.id, {"enabled": True, "open": "9pm", "close": "21:00"})
