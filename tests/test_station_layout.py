import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import run
from stationnet import create_app, instance, station_migration
from stationnet.db import get_db


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("STATIONNET_DATA", "STATIONNET_HOME", "STATIONNET_STATIONS_DIR", "STATIONNET_STATION",
                 "STATIONNET_HOST", "STATIONNET_PORT", "STATIONNET_HTTPS", "STATIONNET_KIWIX_SERVE"):
        monkeypatch.delenv(name, raising=False)


def _quiesce(app):
    """Stop background workers that would reopen the database after it moves."""
    app.extensions["kiwix"].stop()
    coordinator = app.extensions["broadcast_coordinator"]
    coordinator._closed = True
    if coordinator._timer is not None:
        coordinator._timer.cancel()


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legacy_station(root):
    """A pre-stations install: <root>/data with a real database and files."""
    data = root / "data"
    app = create_app({"TESTING": True, "DATA_DIR": str(data)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign) VALUES('Legacy','Test','LEG-1')")
        db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('sysop','SYS-1','x',1)")
        db.commit()
    _quiesce(app)
    (data / "uploads").mkdir(exist_ok=True)
    (data / "uploads" / "note.txt").write_bytes(b"hello archive")
    (data / "profile_photos").mkdir(exist_ok=True)
    (data / "profile_photos" / "1.png").write_bytes(bytes(range(256)) * 4)
    (data / "empty_dir").mkdir()
    (data / "kiwix_data").mkdir(exist_ok=True)
    (data / "kiwix_data" / "wiki.zim").write_bytes(b"ZIM" * 100)
    return data


def _snapshot(folder):
    return {p.relative_to(folder).as_posix(): (_digest(p) if p.is_file() else None)
            for p in sorted(folder.rglob("*"))}


def _station_rows(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT name FROM station").fetchall()


def test_resolve_default_station(tmp_path):
    station = instance.resolve(tmp_path)
    assert not station.standalone
    assert station.id == "default"
    assert station.data_dir == tmp_path.resolve() / "stations" / "default"
    assert station.app_config()["DATA_DIR"] == str(station.data_dir)
    assert station.app_config()["KIWIX_LEGACY_DIR"] is None


def test_resolve_named_station_and_env(tmp_path, monkeypatch):
    assert instance.resolve(tmp_path, "radio-2").data_dir.name == "radio-2"
    monkeypatch.setenv("STATIONNET_STATION", "west")
    assert instance.resolve(tmp_path).data_dir.name == "west"
    monkeypatch.setenv("STATIONNET_STATIONS_DIR", str(tmp_path / "elsewhere"))
    assert instance.resolve(tmp_path).data_dir == (tmp_path / "elsewhere").resolve() / "west"


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "A", "-x", "x" * 65, "a b"])
def test_invalid_station_ids_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        instance.resolve(tmp_path, bad)


def test_standalone_mode_keeps_legacy_behavior(tmp_path, monkeypatch):
    monkeypatch.setenv("STATIONNET_DATA", str(tmp_path / "custom"))
    station = instance.resolve(tmp_path)
    assert station.standalone
    assert station.app_config() == {"DATA_DIR": str(tmp_path / "custom")}
    app = create_app({"TESTING": True, **station.app_config()})
    assert app.config["KIWIX_LEGACY_DIR"] == str(tmp_path / "kiwix_data")
    _quiesce(app)


def test_station_json_settings_and_env_precedence(tmp_path, monkeypatch):
    folder = tmp_path / "stations" / "default"
    assert instance.write_config_if_missing(folder, {"port": 9001, "host": "127.0.0.1", "https": True})
    assert not instance.write_config_if_missing(folder, {"port": 1})
    station = instance.resolve(tmp_path)
    assert (station.host, station.port, station.https) == ("127.0.0.1", 9001, True)
    assert station.app_config()["SESSION_COOKIE_SECURE"] is True
    monkeypatch.setenv("STATIONNET_PORT", "9002")
    monkeypatch.setenv("STATIONNET_HTTPS", "0")
    assert (station.port, station.https) == (9002, False)


def test_migration_copies_verifies_and_cleans(tmp_path):
    data = _legacy_station(tmp_path)
    before = _snapshot(data)
    stations = tmp_path / "stations"

    result = station_migration.migrate_legacy(data, stations)

    assert result.status == "migrated", result.message
    target = stations / "default"
    for rel, digest in before.items():
        if rel.startswith("kiwix_data/") or rel.startswith("station.db"):
            continue
        assert (target / rel).exists(), rel
        if digest:
            assert _digest(target / rel) == digest, rel
    assert _station_rows(target / "station.db") == [("Legacy",)]
    assert not (target / "kiwix_data" / "wiki.zim").exists()
    config = json.loads((target / "station.json").read_text())
    assert config["id"] == "default" and config["migrated_from"]
    manifest = json.loads((target / "MIGRATION_MANIFEST.json").read_text())
    assert manifest["not_copied"] == ["kiwix_data/wiki.zim"]

    # Only the archive and the marker remain in the legacy folder.
    remaining = sorted(p.relative_to(data).as_posix() for p in data.rglob("*"))
    assert remaining == ["MIGRATED.txt", "kiwix_data", "kiwix_data/wiki.zim"]
    assert "kiwix_data/wiki.zim" in (data / "MIGRATED.txt").read_text()
    assert not list(stations.glob(".default.migrating-*"))

    again = station_migration.migrate_legacy(data, stations)
    assert again.status == "noop"


def test_migrated_station_boots_with_same_identity(tmp_path):
    data = _legacy_station(tmp_path)
    secret = (data / "secret.key").read_text()
    assert station_migration.migrate_legacy(data, tmp_path / "stations").status == "migrated"
    station = instance.resolve(tmp_path)
    app = create_app({"TESTING": True, **station.app_config()})
    try:
        assert app.config["SECRET_KEY"] == secret
        assert Path(app.config["DATABASE"]) == station.data_dir / "station.db"
        with app.app_context():
            assert get_db().execute("SELECT name FROM station").fetchone()[0] == "Legacy"
    finally:
        _quiesce(app)


def test_dry_run_changes_nothing(tmp_path):
    data = _legacy_station(tmp_path)
    before = _snapshot(data)
    result = station_migration.migrate_legacy(data, tmp_path / "stations", dry_run=True)
    assert result.status == "dry-run"
    assert Path("kiwix_data/wiki.zim") in result.skipped
    assert _snapshot(data) == before
    assert not (tmp_path / "stations").exists()


def test_existing_target_is_never_touched(tmp_path):
    data = _legacy_station(tmp_path)
    target = tmp_path / "stations" / "default"
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("mine")
    before = _snapshot(data)
    result = station_migration.migrate_legacy(data, tmp_path / "stations")
    assert result.status == "refused"
    assert _snapshot(data) == before
    assert _snapshot(target) == {"keep.txt": _digest(target / "keep.txt")}


def test_locked_database_refuses(tmp_path):
    data = _legacy_station(tmp_path)
    before = _snapshot(data)
    holder = sqlite3.connect(data / "station.db", isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        result = station_migration.migrate_legacy(data, tmp_path / "stations")
    finally:
        holder.rollback()
        holder.close()
    assert result.status == "refused"
    assert "in use" in result.message
    assert not (tmp_path / "stations" / "default").exists()
    assert not list((tmp_path / "stations").glob(".default.migrating-*"))
    assert set(_snapshot(data)) == set(before)
    assert _station_rows(data / "station.db") == [("Legacy",)]


def test_verification_failure_leaves_legacy_untouched(tmp_path, monkeypatch):
    data = _legacy_station(tmp_path)
    before = _snapshot(data)
    real_copy = station_migration.shutil.copy2

    def corrupt_copy(source, destination):
        real_copy(source, destination)
        if Path(source).name == "note.txt":
            Path(destination).write_bytes(b"corrupted")

    monkeypatch.setattr(station_migration.shutil, "copy2", corrupt_copy)
    result = station_migration.migrate_legacy(data, tmp_path / "stations")
    assert result.status == "refused"
    assert "note.txt" in result.message
    assert not (tmp_path / "stations" / "default").exists()
    assert not list((tmp_path / "stations").glob(".default.migrating-*"))
    assert set(_snapshot(data)) == set(before)
    assert (data / "uploads" / "note.txt").read_bytes() == b"hello archive"


def test_run_migrate_only_creates_default_station(tmp_path, monkeypatch):
    data = _legacy_station(tmp_path)
    monkeypatch.setenv("STATIONNET_HOME", str(tmp_path))
    monkeypatch.setenv("STATIONNET_PORT", "1")  # nothing listens here
    run.main(["--migrate-only"])
    assert _station_rows(tmp_path / "stations" / "default" / "station.db") == [("Legacy",)]
    assert (data / "MIGRATED.txt").is_file()


def test_run_refuses_when_both_layouts_exist(tmp_path, monkeypatch):
    _legacy_station(tmp_path)
    (tmp_path / "stations" / "default").mkdir(parents=True)
    monkeypatch.setenv("STATIONNET_HOME", str(tmp_path))
    monkeypatch.setenv("STATIONNET_PORT", "1")
    with pytest.raises(SystemExit) as exit_info:
        run.main(["--migrate-only"])
    assert exit_info.value.code == 1
