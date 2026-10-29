"""Keep each station's folder named after the station.

A station's id (in station.json) never changes; its folder starts out as the
id and is renamed to a filesystem-safe form of the station's name. Renames are
a single same-directory ``os.rename``, which is atomic and never copies, so a
failure leaves the folder exactly as it was. A station that is running (or
starting, or stopping) is never renamed.
"""
import os
import re
import sys
import unicodedata
from pathlib import Path

from . import instance
from .station_runtime import AlreadyRunning, StationRuntime

MAX_SLUG = 56  # leaves room for a "-NN" suffix within the 64-character id limit
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}


def folder_slug(name):
    """A safe folder name for ``name``, or None if nothing usable is left."""
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:MAX_SLUG].strip("-")
    if not slug:
        return None
    if slug in _RESERVED:
        slug += "-station"  # Windows refuses these as file names
    return slug


def desired_folder(stations_dir, current, slug):
    """The folder name a station named ``slug`` should have.

    A folder that is already ``slug`` or ``slug-N`` is kept, so two stations
    with the same name do not trade folders back and forth.
    """
    current = Path(current)
    if re.fullmatch(rf"{re.escape(slug)}(-[0-9]+)?", current.name):
        return current.name
    taken = {path.name.lower() for path in Path(stations_dir).iterdir() if path != current}
    # Another station's id is kept free too, so that looking a station up by
    # its id never lands in a different station's folder first.
    taken |= {station_id for station_id, folders in instance.scan_stations(stations_dir).items()
              if current not in folders}
    candidate, number = slug, 2
    while candidate in taken:
        candidate, number = f"{slug}-{number}", number + 1
    return candidate


def pending_folder(data_dir, name):
    """The folder a station should move to, or None if it is already right."""
    slug = folder_slug(name)
    data_dir = Path(data_dir)
    if slug is None or not data_dir.is_dir():
        return None
    target = desired_folder(data_dir.parent, data_dir, slug)
    return None if target == data_dir.name else target


def rename_if_needed(data_dir, station_id):
    """Rename a stopped station's folder after its name.

    Returns ``(data_dir, error)``: the folder the station now lives in, and a
    message if a needed rename failed (the folder is unchanged). A station
    that is running is left alone without an error; its rename stays pending.
    """
    data_dir = Path(data_dir)
    try:
        config = instance.load_config(data_dir)
    except ValueError as exc:
        return data_dir, str(exc)
    # Only a station whose id is written down can safely leave a folder that
    # bears its id.
    if config.get("id") != station_id:
        return data_dir, None
    target_name = pending_folder(data_dir, config.get("name"))
    if target_name is None:
        return data_dir, None
    target = data_dir.parent / target_name
    runtime = StationRuntime(data_dir)
    if runtime.is_locked():
        return data_dir, None  # still pending; it happens once the station stops
    try:
        # On POSIX, holding the lock across the rename keeps the station from
        # starting meanwhile. Windows cannot rename a folder with an open file
        # in it, so the lock is let go first; a station that starts in that
        # instant has files open and the rename simply fails.
        runtime.acquire(wait=0)
    except AlreadyRunning:
        return data_dir, None
    except OSError as exc:
        return data_dir, f"Could not rename {data_dir.name} to {target_name}: {exc}"
    try:
        if sys.platform == "win32":
            runtime.release()
        if target.exists():
            return data_dir, f"Could not rename {data_dir.name} to {target_name}: it already exists."
        os.rename(data_dir, target)
    except OSError as exc:
        return data_dir, f"Could not rename {data_dir.name} to {target_name}: {exc}"
    finally:
        runtime.release()
    if instance.config_id(target) != station_id:
        # Never expected after an atomic rename, but do not report success.
        return target, f"{target} does not hold station '{station_id}' after the rename."
    return target, None
