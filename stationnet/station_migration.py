"""Move a pre-stations ``data/`` folder into ``stations/<id>/``.

The migration copies into a private staging folder, verifies every byte (and
the database's integrity and row counts), then renames the staging folder into
place. Only after that does it remove the originals it verified. ZIM archives
are never copied or removed; the operator moves them by hand. Any failure
before the rename leaves the legacy folder untouched.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import instance

MARKER = "MIGRATED.txt"
MANIFEST = "MIGRATION_MANIFEST.json"
DATABASE = "station.db"
_DB_SIDECARS = (DATABASE + "-wal", DATABASE + "-shm", DATABASE + "-journal")


class MigrationError(Exception):
    pass


@dataclass
class Result:
    status: str  # "noop", "dry-run", "migrated" or "refused"
    message: str
    target: Path | None = None
    copied: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    kept: list = field(default_factory=list)
    leftovers: list = field(default_factory=list)


def needs_migration(legacy_dir):
    legacy_dir = Path(legacy_dir)
    return (not (legacy_dir / MARKER).exists()
            and any((legacy_dir / name).is_file() for name in (DATABASE, "secret.key")))


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_archive(rel):
    return rel.parts[0] == "kiwix_data" and rel.suffix.lower() == ".zim"


def _inventory(legacy_dir):
    """Return (files to copy, directories, skipped archives) as relative paths."""
    files, dirs, skipped = [], [], []
    for path in sorted(legacy_dir.rglob("*")):
        rel = path.relative_to(legacy_dir)
        if path.is_symlink():
            raise MigrationError(f"Refusing to migrate symbolic link {path}")
        if path.is_dir():
            dirs.append(rel)
        elif len(rel.parts) == 1 and rel.name in (MARKER, *_DB_SIDECARS):
            continue
        elif _is_archive(rel):
            skipped.append(rel)
        else:
            files.append(rel)
    return files, dirs, skipped


def _table_counts(connection):
    names = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name: connection.execute(f'SELECT COUNT(*) FROM "{name.replace(chr(34), chr(34) * 2)}"').fetchone()[0]
            for name in names}


def _leftovers(stations_root, station_id):
    if not stations_root.is_dir():
        return []
    return sorted(p for p in stations_root.glob(f".{station_id}.migrating-*") if p.is_dir())


def migrate_legacy(legacy_dir, stations_root, station_id=instance.DEFAULT_STATION, dry_run=False):
    legacy_dir, stations_root = Path(legacy_dir), Path(stations_root)
    instance.validate_station_id(station_id)
    target = stations_root / station_id
    leftovers = _leftovers(stations_root, station_id)
    if not needs_migration(legacy_dir):
        return Result("noop", "No legacy station data to migrate.", target, leftovers=leftovers)
    if target.exists():
        return Result("refused", (
            f"Both {legacy_dir} and {target} contain a station. Nothing was changed. "
            f"Move one of them aside, or write {legacy_dir / MARKER} if the legacy copy is obsolete."
        ), target, leftovers=leftovers)
    try:
        files, dirs, skipped = _inventory(legacy_dir)
    except MigrationError as exc:
        return Result("refused", str(exc), target, leftovers=leftovers)
    if dry_run:
        return Result("dry-run", f"Would migrate {len(files)} files from {legacy_dir} to {target}.",
                      target, copied=files, skipped=skipped, leftovers=leftovers)

    source_db = writer_lock = None
    staging = stations_root / f".{station_id}.migrating-{uuid.uuid4().hex}"
    try:
        manifest = []
        if (legacy_dir / DATABASE).is_file():
            writer_lock = sqlite3.connect(legacy_dir / DATABASE, timeout=0, isolation_level=None)
            busy = writer_lock.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if busy and busy[0]:
                raise MigrationError("The station database is in use. Stop StationNet first.")
            # Hold the write lock until the copy is committed in its new home.
            # SQLite cannot back up through the connection holding it, so a
            # second connection reads the database.
            writer_lock.execute("BEGIN IMMEDIATE")
            source_db = sqlite3.connect(legacy_dir / DATABASE, timeout=0)
        stations_root.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
        for rel in dirs:
            (staging / rel).mkdir(parents=True, exist_ok=True)
        (staging / "kiwix_data").mkdir(exist_ok=True)
        for rel in files:
            source, copy = legacy_dir / rel, staging / rel
            copy.parent.mkdir(parents=True, exist_ok=True)
            stat = source.stat()
            if rel == Path(DATABASE):
                with sqlite3.connect(copy) as destination:
                    source_db.backup(destination)
                destination.close()
                entry = {"path": rel.as_posix(), "size": copy.stat().st_size, "sha256": _sha256(copy)}
            else:
                shutil.copy2(source, copy)
                entry = {"path": rel.as_posix(), "size": stat.st_size, "sha256": _sha256(source)}
            entry["source_mtime_ns"] = stat.st_mtime_ns
            entry["source_size"] = stat.st_size
            manifest.append(entry)

        for entry in manifest:
            copy = staging / entry["path"]
            if copy.stat().st_size != entry["size"] or _sha256(copy) != entry["sha256"]:
                raise MigrationError(f"Verification failed for {entry['path']}")
        if source_db is not None:
            staged = sqlite3.connect(staging / DATABASE)
            try:
                check = staged.execute("PRAGMA integrity_check").fetchone()[0]
                if check != "ok":
                    raise MigrationError(f"Copied database failed its integrity check: {check}")
                if _table_counts(staged) != _table_counts(source_db):
                    raise MigrationError("Copied database row counts do not match the original")
            finally:
                staged.close()

        migrated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (staging / MANIFEST).write_text(json.dumps({
            "source": str(legacy_dir.resolve()), "migrated_at": migrated_at,
            "files": manifest, "not_copied": [rel.as_posix() for rel in skipped],
        }, indent=2) + "\n", encoding="utf-8")
        instance.write_config_if_missing(staging, instance.new_config(
            station_id, migrated_from=str(legacy_dir.resolve()), migrated_at=migrated_at))
        if target.exists():
            raise MigrationError(f"{target} appeared during migration")
        os.rename(staging, target)
    except (OSError, sqlite3.Error, MigrationError) as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        reason = "The station database is in use. Stop StationNet first." if (
            isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc)) else str(exc)
        return Result("refused", f"Migration did not complete; {legacy_dir} is unchanged. {reason}",
                      target, leftovers=leftovers)
    finally:
        if source_db is not None:
            source_db.close()
        if writer_lock is not None:
            if writer_lock.in_transaction:
                writer_lock.rollback()
            writer_lock.close()

    kept = _remove_originals(legacy_dir, manifest, dirs)
    remaining = sorted({rel.as_posix() for rel in skipped} | set(kept))
    lines = [
        f"This station was migrated to {target.resolve()} on {migrated_at}.",
        "StationNet no longer reads this folder.", "",
    ]
    if remaining:
        lines += ["These files were left here. Move any ZIM archives into",
                  f"{(target / 'kiwix_data').resolve()}", ""]
        lines += [f"  {name}" for name in remaining]
    (legacy_dir / MARKER).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Result("migrated", f"Migrated {len(manifest)} files to {target}.", target,
                  copied=[Path(entry["path"]) for entry in manifest], skipped=skipped,
                  kept=kept, leftovers=leftovers)


def _remove_originals(legacy_dir, manifest, dirs):
    """Delete verified originals; files changed since copying are kept."""
    kept = []
    for entry in manifest:
        source = legacy_dir / entry["path"]
        try:
            stat = source.stat()
            if stat.st_size != entry["source_size"] or stat.st_mtime_ns != entry["source_mtime_ns"]:
                kept.append(entry["path"])
                continue
            source.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            kept.append(entry["path"])
    if not (legacy_dir / DATABASE).exists():
        for name in _DB_SIDECARS:
            try:
                (legacy_dir / name).unlink(missing_ok=True)
            except OSError:
                kept.append(name)
    for rel in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        try:
            (legacy_dir / rel).rmdir()  # only succeeds when empty
        except OSError:
            pass
    return kept
