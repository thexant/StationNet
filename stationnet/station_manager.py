"""Create, start, stop, and inspect the stations of one StationNet install.

Every answer comes from the stations' own folders (``station.json`` and the
runtime files in ``run/``), so a launcher that is closed and reopened sees
exactly what is running. Stations are started as detached processes and keep
running after the launcher exits.
"""
import os
import re
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import instance
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

    @property
    def lan(self):
        return self.host not in (LOCAL_HOST, "localhost", "::1")

    @property
    def log_path(self):
        return StationRuntime(self.data_dir).log_path


def clean_environment():
    """Drop per-station overrides from this process (call once at startup)."""
    for name in STATION_ENV:
        os.environ.pop(name, None)


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-_")[:64].strip("-_")
    return slug or "station"


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

    @property
    def stations_dir(self):
        return instance.stations_root(self.root)

    def _station_ids(self):
        if not self.stations_dir.is_dir():
            return []
        ids = []
        for path in self.stations_dir.iterdir():
            if not path.is_dir() or path.name.startswith("."):
                continue
            try:
                ids.append(instance.validate_station_id(path.name))
            except ValueError:
                continue
        return ids

    def _load(self, station_id):
        data_dir = self.stations_dir / station_id
        info = StationInfo(id=station_id, name=station_id.replace("-", " ").title(), data_dir=data_dir)
        try:
            station = instance.resolve(self.root, station_id)
            info.host, info.port = station.host, station.port
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
        return info

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

    def suggest_port(self):
        taken = self.configured_ports()
        for port in range(FIRST_PORT, 65536):
            if port not in taken and not port_in_use(port):
                return port
        raise ValueError("No free port is available.")

    def create_station(self, name, station_id, port, lan=False):
        name = (name or "").strip()
        if not name:
            raise ValueError("Enter a station name.")
        if len(name) > 80:
            raise ValueError("Station names are limited to 80 characters.")
        instance.validate_station_id(station_id)
        try:
            port = int(port)
        except (TypeError, ValueError):
            raise ValueError("The port must be a number.") from None
        if not 1024 <= port <= 65535:
            raise ValueError("Choose a port between 1024 and 65535.")
        data_dir = self.stations_dir / station_id
        if data_dir.exists():
            raise ValueError(f"A station with the id '{station_id}' already exists.")
        if port in self.configured_ports():
            raise ValueError(f"Port {port} is already assigned to another station.")
        host = LAN_HOST if lan else LOCAL_HOST
        if port_in_use(port, host):
            raise ValueError(f"Port {port} is in use by another program.")
        self.stations_dir.mkdir(parents=True, exist_ok=True)
        try:
            data_dir.mkdir()
        except FileExistsError:
            raise ValueError(f"A station with the id '{station_id}' already exists.") from None
        try:
            instance.write_config_if_missing(
                data_dir, instance.new_config(station_id, name=name, host=host, port=port))
        except OSError:
            data_dir.rmdir()
            raise
        return self.station(station_id)

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
        runtime = StationRuntime(info.data_dir)
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
        runtime = StationRuntime(self.stations_dir / station_id)
        if runtime.is_locked():
            runtime.request_stop()

    def restart(self, station_id):
        self.stop(station_id)
        self._restarts.add(station_id)

    def force_stop(self, station_id):
        """Kill a station that ignored a stop request. Last resort only."""
        runtime = StationRuntime(self.stations_dir / station_id)
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
        """Refresh every station and carry out pending restarts."""
        stations = self.list_stations()
        for info in stations:
            if info.id in self._restarts and info.state in ("stopped", "crashed"):
                self._restarts.discard(info.id)
                try:
                    self.start(info.id)
                except (ValueError, OSError) as exc:
                    info.detail = f"Restart failed: {exc}"
                    continue
                info.state = "starting"
                info.addresses = self.addresses(info)
        return stations

    def restarting(self, station_id):
        return station_id in self._restarts
