"""Create, start, stop, and inspect the stations of one StationNet install.

Every answer comes from the stations' own folders (``station.json`` and the
runtime files in ``run/``), so a launcher that is closed and reopened sees
exactly what is running. Stations are started as detached processes and keep
running after the launcher exits.
"""
import ipaddress
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from . import instance, station_dirs, station_hours, themes
from .security import hash_password, valid_username, validate_password
from .station_runtime import StationRuntime

# Settings a station takes from station.json; inherited values would make
# every station started from here share one port or data folder.
STATION_ENV = ("STATIONNET_DATA", "STATIONNET_STATION", "STATIONNET_HOST", "STATIONNET_PORT",
               "STATIONNET_HTTPS", "STATIONNET_KIWIX_SERVE")
FIRST_PORT = 8080
FORCE_STOP_AFTER = 20
LAUNCH_GRACE = 15
LOCAL_HOST = "127.0.0.1"
LAN_HOST = "0.0.0.0"
ACTIVE = ("starting", "running", "stopping")
RENAME_RETRY = 30
REGISTRATION_MODES = ("open", "private", "disabled")
DEFAULT_THEME = "cyan"


@dataclass
class StationInfo:
    id: str
    name: str
    data_dir: Path
    state: str = "stopped"
    host: str = LOCAL_HOST
    port: int | None = None
    detail: str | None = None
    error: str | None = None
    addresses: list = field(default_factory=list)
    can_force_stop: bool = False
    https: bool = False
    pending_folder: str | None = None
    rename_error: str | None = None
    # Settings saved while the station runs that take effect when it restarts.
    restart_needed: list = field(default_factory=list)
    # The station-wide site theme from its database; personal themes are ignored.
    theme: str = DEFAULT_THEME
    # Enabled station hours (station_hours.Hours), and when a cancelled
    # closing stops counting.
    hours: object = None
    extended_until: object = None
    # Started by the Station Manager when it opens, if not already running.
    start_with_manager: bool = False

    @property
    def folder(self):
        return self.data_dir.name

    @property
    def lan(self):
        return self.host not in (LOCAL_HOST, "localhost", "::1")

    @property
    def access(self):
        """"local", "lan" (every interface) or "address" (one chosen address)."""
        return "lan" if self.host == LAN_HOST else "address" if self.lan else "local"

    @property
    def log_path(self):
        return StationRuntime(self.data_dir).log_path


def clean_environment():
    """Drop per-station overrides from this process (call once at startup)."""
    for name in STATION_ENV:
        os.environ.pop(name, None)


def port_in_use(port, host=LAN_HOST):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        if probe.connect_ex((LOCAL_HOST, port)) == 0:
            return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if sys.platform != "win32":
            # Matches uvicorn; on Windows this flag would allow stealing a port.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return True
    return False


def lan_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # no packet is sent for UDP connect
            address = probe.getsockname()[0]
    except OSError:
        try:
            address = socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    return None if address.startswith("127.") or address == "0.0.0.0" else address


def check_name(name, required=False):
    name = (name or "").strip()
    if (name or required) and not 2 <= len(name) <= 80:
        raise ValueError("Station names must be between 2 and 80 characters.")
    return name


def check_host(host):
    """``host`` if a station can listen on it; one chosen address must be an
    IPv4 address of this computer."""
    host = (host or "").strip()
    if host in (LOCAL_HOST, LAN_HOST):
        return host
    try:
        host = str(ipaddress.IPv4Address(host))
    except ValueError:
        raise ValueError("Enter an IPv4 address such as 192.168.1.20.") from None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, 0))
        except OSError:
            raise ValueError(f"{host} is not an address of this computer.") from None
    return host


def check_sysop(sysop):
    """Validate a first SYSOP account as the first-run page does; returns the
    values for setup.json, which hold a password hash only."""
    username = (sysop.get("username") or "").strip()
    password = sysop.get("password") or ""
    description = (sysop.get("description") or "").strip()
    mode = sysop.get("registration_mode") or "open"
    if not valid_username(username):
        raise ValueError("SYSOP usernames must be 3–24 letters, numbers, underscores, or hyphens.")
    if password != sysop.get("confirm_password", password):
        raise ValueError("The SYSOP passwords do not match.")
    error = validate_password(password)
    if error:
        raise ValueError(error)
    if len(description) > 500:
        raise ValueError("Station descriptions cannot exceed 500 characters.")
    if mode not in REGISTRATION_MODES:
        raise ValueError("Choose a registration policy.")
    return {"username": username, "password_hash": hash_password(password),
            "description": description, "registration_mode": mode}


def _write_private(path, values):
    # Exclusive create, owner-only where the OS supports it: it holds a password hash.
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as output:
        output.write(json.dumps(values, indent=2) + "\n")


def _last_log_line(path):
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 4096))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return next((line.strip() for line in reversed(lines) if line.strip()), None)


class StationManager:
    def __init__(self, root, core_dir):
        self.root = Path(root)
        self.core_dir = Path(core_dir)
        self._launches = {}
        self._exits = {}
        self._restarts = set()
        self._rename_failures = {}
        self._themes = {}

    @property
    def stations_dir(self):
        return instance.stations_root(self.root)

    def _station_ids(self):
        found = instance.scan_stations(self.stations_dir)
        for station_id, folders in found.items():
            # Write the id into configs that predate ids, so their folder can
            # later be renamed without the station losing track of itself.
            if len(folders) == 1 and folders[0].name == station_id:
                try:
                    config = instance.load_config(folders[0])
                    if config and "id" not in config:
                        instance.update_config(folders[0], id=station_id)
                except (OSError, ValueError):
                    pass
        return list(found)

    def _data_dir(self, station_id):
        try:
            return instance.find_station_dir(self.stations_dir, station_id)
        except ValueError:
            return self.stations_dir / station_id

    def _load(self, station_id):
        data_dir = self._data_dir(station_id)
        info = StationInfo(id=station_id, name=station_id.replace("-", " ").title(), data_dir=data_dir)
        try:
            station = instance.resolve(self.root, station_id)
            info.host, info.port, info.https = station.host, station.port, station.https
            info.name = str(station.config.get("name") or info.name)
        except ValueError as exc:
            info.state, info.error = "error", str(exc)
        return info

    def list_stations(self):
        stations = [self.station(station_id) for station_id in self._station_ids()]
        return sorted(stations, key=lambda info: (info.id != instance.DEFAULT_STATION, info.name.lower()))

    def station(self, station_id):
        info = self._load(station_id)
        if info.error:
            return info
        runtime = StationRuntime(info.data_dir)
        launch = self._launches.get(station_id)
        if launch and launch[0].poll() is not None:
            self._exits[station_id] = launch[0].returncode
            del self._launches[station_id]
            launch = None
        status = runtime.read_status()
        if runtime.is_locked():
            self._launches.pop(station_id, None)
            requested = runtime.stop_requested_at()
            if requested is not None:
                info.state = "stopping"
                info.can_force_stop = time.time() - requested >= FORCE_STOP_AFTER
            elif status and status.get("state") == "running":
                info.state = "running"
            else:
                info.state = "starting"
        elif launch and time.monotonic() - launch[1] < LAUNCH_GRACE:
            info.state = "starting"  # spawned but not yet holding its lock
        elif status or self._exits.get(station_id):
            # A leftover status, or a launch that exited with an error before
            # it could write one (bad config, migration refused, ...).
            info.state = "crashed"
            info.detail = _last_log_line(runtime.log_path) or "The station stopped unexpectedly."
        if info.state in ACTIVE:
            info.addresses = self.addresses(info)
        try:
            config = instance.load_config(info.data_dir)
        except ValueError:
            config = {}
        name = config.get("name")
        info.hours = station_hours.Hours.from_config(config)
        info.extended_until = station_hours.extended_until(config)
        info.start_with_manager = bool(config.get("start_with_manager"))
        if info.state in ("starting", "running") and status:
            info.restart_needed = self._restart_needed(info, status, config)
        info.pending_folder = station_dirs.pending_folder(info.data_dir, name)
        info.theme = self._site_theme(station_id, info.data_dir)
        failure = self._rename_failures.get(station_id)
        if failure and info.pending_folder:
            info.rename_error = failure[0]
        return info

    def _site_theme(self, station_id, data_dir):
        """The station's own site theme, re-read only when its database changes."""
        database = data_dir / "station.db"
        key = [data_dir]
        for path in (database, database.with_name(database.name + "-wal")):
            try:
                stat = path.stat()
                key.append((stat.st_mtime_ns, stat.st_size))
            except OSError:
                key.append(None)
        if key[1] is None:
            return DEFAULT_THEME
        cached = self._themes.get(station_id)
        if cached and cached[0] == key:
            return cached[1]
        theme = DEFAULT_THEME
        try:
            # Read-only, so a station that never ran is not given a database.
            with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=0.2)) as connection:
                row = connection.execute("SELECT site_theme FROM station WHERE id=1").fetchone()
            if row and isinstance(row[0], str) and row[0]:
                theme = row[0]
        except (sqlite3.Error, OSError, ValueError):
            pass
        self._themes[station_id] = (key, theme)
        return theme

    @staticmethod
    def palette(info):
        """The colours of ``info``'s theme, falling back to the default theme."""
        return themes.palette(info.theme, "site", folder=info.data_dir / "themes")

    @staticmethod
    def _restart_needed(info, status, config):
        """The settings station.json changed since the live process started."""
        changed = []
        if "pending_name" in config:
            changed.append("name")
        if "port" in status and status["port"] != info.port:
            changed.append("port")
        if "host" in status and status["host"] != info.host:
            changed.append("network access")
        if "https" in status and bool(status["https"]) != info.https:
            changed.append("HTTPS proxy")
        return changed

    def rename_folder(self, station_id, force=False):
        """Rename a stopped station's folder after its name; returns the folder.

        A failed rename is retried after RENAME_RETRY seconds, or right away
        with ``force`` (before a start).
        """
        data_dir = self._data_dir(station_id)
        failure = self._rename_failures.get(station_id)
        if failure and not force and time.monotonic() < failure[1]:
            return data_dir
        data_dir, error = station_dirs.rename_if_needed(data_dir, station_id)
        if error:
            self._rename_failures[station_id] = (error, time.monotonic() + RENAME_RETRY)
        else:
            self._rename_failures.pop(station_id, None)
        return data_dir

    def addresses(self, info):
        result = [f"http://{LOCAL_HOST}:{info.port}"]
        if info.host == LAN_HOST:
            address = lan_address()
            if address:
                result.append(f"http://{address}:{info.port}")
        elif info.lan:
            result = [f"http://{info.host}:{info.port}"]
        return result

    def configured_ports(self, exclude=None):
        return {info.port for info in map(self._load, self._station_ids())
                if info.id != exclude and info.port is not None}

    def suggest_name(self):
        taken = {info.name.lower() for info in map(self._load, self._station_ids())}
        number = 1
        while f"station {number}" in taken:
            number += 1
        return f"Station {number}"

    def check_port(self, port, host, station_id=None, probe=True):
        """``port`` as a number if the station may use it.

        ``probe`` also refuses a port another program is listening on.
        """
        try:
            port = int(str(port).strip())
        except (TypeError, ValueError):
            raise ValueError("The port must be a number.") from None
        if not 1024 <= port <= 65535:
            raise ValueError("Choose a port between 1024 and 65535.")
        if port in self.configured_ports(exclude=station_id):
            raise ValueError(f"Port {port} is already assigned to another station.")
        if probe and port_in_use(port, host):
            raise ValueError(f"Port {port} is in use by another program.")
        return port

    def suggest_port(self):
        taken = self.configured_ports()
        for port in range(FIRST_PORT, 65536):
            if port not in taken and not port_in_use(port):
                return port
        raise ValueError("No free port is available.")

    def create_station(self, name, port, lan=False, host=None, https=False, sysop=None, start_with_manager=False):
        """Create a stopped station in a temporary ``station-xxxxxx`` folder.

        ``host`` defaults to every interface with ``lan``, else this computer
        only. ``sysop`` (username, password, confirm_password, description,
        registration_mode) prepares the first SYSOP account; the station
        creates it on its first start instead of showing first-run setup.
        A named station's folder is renamed after it by the next poll().
        """
        name = check_name(name, required=sysop is not None)
        host = check_host(host or (LAN_HOST if lan else LOCAL_HOST))
        port = self.check_port(port, host)
        setup = None if sysop is None else {"name": name, **check_sysop(sysop)}
        self.stations_dir.mkdir(parents=True, exist_ok=True)
        for _attempt in range(10):
            station_id = instance.new_station_id(self.stations_dir)
            data_dir = self.stations_dir / station_id
            try:
                data_dir.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise ValueError("Could not create a station folder.")
        values = instance.new_config(station_id, host=host, port=port)
        if https:
            values["https"] = True
        if start_with_manager:
            values["start_with_manager"] = True
        if name:
            values["name"] = name
        try:
            if setup:
                _write_private(data_dir / instance.SETUP_NAME, setup)
            instance.write_config_if_missing(data_dir, values)
        except OSError:
            (data_dir / instance.SETUP_NAME).unlink(missing_ok=True)
            data_dir.rmdir()
            raise
        return self.station(station_id)

    def update_station(self, station_id, name, port, host, https=False, start_with_manager=False):
        """Save a station's server settings to its station.json.

        A running station keeps its current settings until it restarts; the
        returned info lists them in ``restart_needed``.
        """
        info = self.station(station_id)
        if info.error:
            raise ValueError(info.error)
        config = instance.load_config(info.data_dir)
        name = check_name(name, required=bool(config.get("name")))
        host = check_host(host)
        changed = str(port).strip() != str(info.port) or host != info.host
        # Unchanged, the port is the running station's own; start() probes it anyway.
        port = self.check_port(port, host, station_id, probe=changed)
        changes = {"host": host, "port": port, "https": bool(https), "start_with_manager": bool(start_with_manager)}
        # An unnamed station shows a placeholder name; keeping it is no rename.
        if name and name != info.name:
            # The station's database holds its name; it takes this one at
            # its next start.
            changes.update(name=name, pending_name=name)
            self._rename_prepared_setup(info.data_dir, name)
        instance.update_config(info.data_dir, **changes)
        return self.station(station_id)

    def update_hours(self, station_id, values):
        """Save a station's hours; a running station shows them at once."""
        info = self.station(station_id)
        if info.error:
            raise ValueError(info.error)
        instance.update_config(info.data_dir, remove=(station_hours.EXTENDED_KEY,),
                               hours=station_hours.check(values))
        return self.station(station_id)

    def set_extended(self, station_id, until):
        """Keep a station open past its closing time until ``until`` (None ends it)."""
        data_dir = self._data_dir(station_id)
        if until is None:
            instance.update_config(data_dir, remove=(station_hours.EXTENDED_KEY,))
        else:
            instance.update_config(data_dir, **{station_hours.EXTENDED_KEY: until.isoformat(timespec="minutes")})

    @staticmethod
    def _rename_prepared_setup(data_dir, name):
        path = data_dir / instance.SETUP_NAME
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        values["name"] = name
        temp = path.with_name(f".{instance.SETUP_NAME}.{os.getpid()}.tmp")
        try:
            _write_private(temp, values)
            os.replace(temp, path)
        except OSError:
            temp.unlink(missing_ok=True)

    def station_command(self, station_id):
        if getattr(sys, "frozen", False):
            exe = "StationNet.exe" if sys.platform == "win32" else "StationNet"
            return [str(Path(sys.executable).with_name(exe)), "--station", station_id]
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe" and python.with_name("python.exe").is_file():
            python = python.with_name("python.exe")
        return [str(python), str(self.core_dir / "run.py"), "--station", station_id]

    def child_environment(self):
        env = {key: value for key, value in os.environ.items() if key not in STATION_ENV}
        env["STATIONNET_HOME"] = str(self.root)
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def start(self, station_id):
        info = self.station(station_id)
        if info.error:
            raise ValueError(info.error)
        if info.state not in ("stopped", "crashed"):
            raise ValueError(f"{info.name} is already {info.state}.")
        for other in self.list_stations():
            if other.id != station_id and other.port == info.port and other.state in ACTIVE:
                raise ValueError(f"Port {info.port} is used by {other.name}, which is {other.state}.")
        if port_in_use(info.port, info.host):
            raise ValueError(f"Port {info.port} is in use by another program.")
        # A stopped station is where a pending folder rename can happen.
        runtime = StationRuntime(self.rename_folder(station_id, force=True))
        runtime.folder.mkdir(parents=True, exist_ok=True)
        runtime.clear_stop()
        runtime.clear_status()
        options = {"stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT,
                   "cwd": str(self.root), "env": self.child_environment()}
        with open(runtime.log_path, "wb") as log:
            options["stdout"] = log
            if sys.platform == "win32":
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                try:
                    # Leave any job object (terminal, IDE) that would kill the
                    # station when the launcher closes.
                    process = subprocess.Popen(self.station_command(station_id),
                                               creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB,
                                               **options)
                except OSError:
                    process = subprocess.Popen(self.station_command(station_id), creationflags=flags, **options)
            else:
                process = subprocess.Popen(self.station_command(station_id), start_new_session=True, **options)
        self._launches[station_id] = (process, time.monotonic())
        self._exits.pop(station_id, None)
        return process

    def stop(self, station_id):
        self._restarts.discard(station_id)
        runtime = StationRuntime(self._data_dir(station_id))
        if runtime.is_locked():
            runtime.request_stop()

    def restart(self, station_id):
        self.stop(station_id)
        self._restarts.add(station_id)

    def force_stop(self, station_id):
        """Kill a station that ignored a stop request. Last resort only."""
        runtime = StationRuntime(self._data_dir(station_id))
        status = runtime.read_status()
        # The pid is trustworthy only while its process still holds the lock.
        if not runtime.is_locked() or not status or not isinstance(status.get("pid"), int):
            return False
        pid = status["pid"]
        if pid == os.getpid():
            return False
        os.kill(pid, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)
        return True

    def poll(self):
        """Refresh every station and carry out pending renames and restarts."""
        stations = self.list_stations()
        for index, info in enumerate(stations):
            if info.error or info.state not in ("stopped", "crashed"):
                continue
            if info.id in self._restarts:
                self._restarts.discard(info.id)
                try:
                    self.start(info.id)  # renames the folder first
                except (ValueError, OSError) as exc:
                    info = stations[index] = self.station(info.id)
                    info.detail = f"Restart failed: {exc}"
                else:
                    stations[index] = self.station(info.id)
            elif info.pending_folder and info.id not in self._launches:
                self.rename_folder(info.id)
                stations[index] = self.station(info.id)
        return stations

    def restarting(self, station_id):
        return station_id in self._restarts
