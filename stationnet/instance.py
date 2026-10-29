"""Station instance resolution: shared core install, per-station data folders.

An install root holds the shared core (code, Python environment, Kiwix tools)
and a ``stations/`` directory with one self-contained folder per station.
Setting STATIONNET_DATA selects standalone mode instead: that directory is the
station, exactly as before the stations layout existed (Android, tests, and
custom deployments rely on this).
"""
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATION = "default"
CONFIG_NAME = "station.json"
CONFIG_FORMAT = 1
_STATION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_station_id(station_id):
    if not isinstance(station_id, str) or not _STATION_ID.fullmatch(station_id):
        raise ValueError(
            f"Invalid station id {station_id!r}: use 1-64 lowercase letters, digits, "
            "'-' or '_', starting with a letter or digit."
        )
    return station_id


def install_root(source_root=None):
    """The folder containing the core and ``stations/``."""
    if os.environ.get("STATIONNET_HOME"):
        return Path(os.environ["STATIONNET_HOME"]).resolve()
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        return exe_dir.parent if exe_dir.name in {"core", "app"} else exe_dir
    if source_root is None:
        raise ValueError("source_root is required for source installs")
    return Path(source_root).resolve()


def stations_root(root):
    override = os.environ.get("STATIONNET_STATIONS_DIR")
    return Path(override).resolve() if override else Path(root) / "stations"


def load_config(data_dir):
    path = Path(data_dir) / CONFIG_NAME
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_config_if_missing(data_dir, values):
    """Create station.json atomically; an existing file is never replaced."""
    path = Path(data_dir) / CONFIG_NAME
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{CONFIG_NAME}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    try:
        # os.link fails if the target appeared meanwhile, unlike os.replace.
        os.link(temp, path)
    except FileExistsError:
        return False
    except OSError:
        # Filesystems without hard links (FAT/exFAT): exclusive create instead.
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(temp.read_text(encoding="utf-8"))
        except FileExistsError:
            return False
    finally:
        temp.unlink(missing_ok=True)
    return True


def update_config(data_dir, **changes):
    """Merge ``changes`` into an existing station.json, replacing it atomically."""
    path = Path(data_dir) / CONFIG_NAME
    values = load_config(data_dir)
    if not path.is_file():
        raise ValueError(f"{path} does not exist")
    values.update(changes)
    temp = path.with_name(f".{CONFIG_NAME}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    try:
        # Windows refuses the replace while a reader has the file open.
        for attempt in range(20):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        temp.unlink(missing_ok=True)
    return values


def sync_name(config_path, name):
    """Record the station's display name in station.json for the launcher.

    Best effort: the database stays authoritative, and the next start or
    settings save retries. Returns True when station.json holds ``name``.
    """
    if not config_path or not name:
        return False
    data_dir = Path(config_path).parent
    try:
        if load_config(data_dir).get("name") != name:
            update_config(data_dir, name=name)
    except (OSError, ValueError):
        return False
    return True


def new_config(station_id, **extra):
    return {"format": CONFIG_FORMAT, "id": station_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}


def config_id(data_dir):
    """The permanent id in a folder's station.json, or None if it has none."""
    value = load_config(data_dir).get("id")
    return value if isinstance(value, str) and _STATION_ID.fullmatch(value) else None


def scan_stations(stations_dir):
    """Map each station id to its folders (more than one means a conflict).

    The id comes from station.json; a folder without one (or without an id
    in it) is identified by its own name, as before ids and folders differed.
    Unreadable configs map to their folder name too, so they still show up.
    """
    found = {}
    stations_dir = Path(stations_dir)
    if not stations_dir.is_dir():
        return found
    for path in sorted(stations_dir.iterdir()):
        if not path.is_dir() or path.name.startswith("."):
            continue
        try:
            station_id = config_id(path)
        except ValueError:
            station_id = None
        if station_id is None:
            if not _STATION_ID.fullmatch(path.name):
                continue
            station_id = path.name
        found.setdefault(station_id, []).append(path)
    return found


def find_station_dir(stations_dir, station_id):
    """The folder holding ``station_id``; ``stations_dir/station_id`` if none does."""
    stations_dir = Path(stations_dir)
    direct = stations_dir / station_id
    try:
        direct_id = config_id(direct) if direct.is_dir() else None
    except ValueError:
        direct_id = None
    if direct.is_dir() and direct_id in (None, station_id):
        others = [path for path in scan_stations(stations_dir).get(station_id, []) if path != direct]
    else:
        matches = scan_stations(stations_dir).get(station_id, [])
        if not matches:
            if direct.exists():
                raise ValueError(f"No station has the id '{station_id}'; the folder {direct.name} "
                                 "belongs to another station.")
            return direct
        direct, others = matches[0], matches[1:]
    if others:
        names = ", ".join(path.name for path in [direct, *others])
        raise ValueError(f"Station id '{station_id}' is used by more than one folder ({names}). "
                         "Give each copy its own id in station.json.")
    return direct


def new_station_id(stations_dir):
    """A fresh ``station-xxxxxx`` id that is not an existing id or folder name."""
    stations_dir = Path(stations_dir)
    taken = set(scan_stations(stations_dir))
    if stations_dir.is_dir():
        taken |= {path.name.lower() for path in stations_dir.iterdir()}
    while True:
        station_id = f"station-{secrets.token_hex(3)}"
        if station_id not in taken:
            return station_id


@dataclass
class Station:
    id: str
    data_dir: Path
    standalone: bool
    root: Path | None = None
    config: dict = field(default_factory=dict)

    def _setting(self, env, key, default=None):
        if os.environ.get(env) is not None:
            return os.environ[env]
        return self.config.get(key, default)

    @property
    def host(self):
        return str(self._setting("STATIONNET_HOST", "host", "0.0.0.0"))

    @property
    def port(self):
        return int(self._setting("STATIONNET_PORT", "port", 8080))

    @property
    def https(self):
        value = self._setting("STATIONNET_HTTPS", "https", False)
        return value is True or str(value) == "1"

    def app_config(self):
        """Overrides for create_app(); standalone mode keeps its old defaults."""
        if self.standalone:
            return {"DATA_DIR": str(self.data_dir)}
        return {
            "DATA_DIR": str(self.data_dir),
            # The station keeps its display name here for the launcher.
            "STATION_CONFIG": str(self.data_dir / CONFIG_NAME),
            "SESSION_COOKIE_SECURE": self.https,
            "KIWIX_SERVE_EXECUTABLE": self._setting("STATIONNET_KIWIX_SERVE", "kiwix_serve"),
            # Stations never adopt archives from outside their own folder.
            "KIWIX_LEGACY_DIR": None,
        }


def resolve(source_root=None, station_id=None):
    data = os.environ.get("STATIONNET_DATA")
    if data:
        data_dir = Path(data)
        return Station(id=data_dir.name or DEFAULT_STATION, data_dir=data_dir,
                       standalone=True, config=load_config(data_dir))
    root = install_root(source_root)
    if station_id is None:
        station_id = os.environ.get("STATIONNET_STATION") or DEFAULT_STATION
    validate_station_id(station_id)
    data_dir = find_station_dir(stations_root(root), station_id)
    return Station(id=station_id, data_dir=data_dir, standalone=False,
                   root=root, config=load_config(data_dir))
