"""Server-side World Forge drafts.

Each world has at most one draft, shared by every builder who opens it. A draft
is the world's package files as JSON; publishing turns it into a package and
installs or upgrades the live world. Every edit is written to ``draft.json``
before it takes effect, so a restart loses nothing but the undo history.
"""
from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .schema import FILES
from .world import WorldError

MAX_FILE = 8 * 1024 * 1024
MAX_ZIP = 16 * 1024 * 1024
MAX_NEW_DRAFTS = 50
HISTORY = 30
IDLE_SECONDS = 30 * 60
PRESENCE_SECONDS = 90
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
BACKUP_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-f0-9]{6}$")
HOME = "stationnet"


class Conflict(WorldError):
    """The draft moved on since the client last saw it."""


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def safe_json(raw, name):
    if len(raw) > MAX_FILE:
        raise WorldError(f"{name} exceeds 8 MB.")
    try:
        return json.loads(raw.decode("utf-8-sig"),
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite number")))
    except (ValueError, UnicodeError) as problem:
        raise WorldError(f"{name}: malformed UTF-8 JSON: {problem}") from None


def archive(files):
    """Package bytes for a draft: the same files a World ZIP holds, at its root."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        for name in FILES:
            if name in files:
                raw = (json.dumps(files[name], ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
                if len(raw) > MAX_FILE:
                    raise WorldError(f"{name} exceeds 8 MB.")
                info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                package.writestr(info, raw)
    raw = output.getvalue()
    if len(raw) > MAX_ZIP:
        raise WorldError("World ZIP exceeds 16 MB.")
    return raw


def unpack(raw):
    """Draft files from an uploaded World ZIP; nested paths, extras, and links are refused."""
    if len(raw) > MAX_ZIP:
        raise WorldError("World ZIP exceeds 16 MB.")
    files, seen = {}, set()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            for info in package.infolist():
                name = info.filename
                if name not in FILES or "/" in name or "\\" in name or info.is_dir():
                    raise WorldError("ZIP must contain only supported root-level world JSON files.")
                if name.casefold() in seen:
                    raise WorldError("Duplicate ZIP filename.")
                seen.add(name.casefold())
                if info.file_size > MAX_FILE:
                    raise WorldError(f"{name} exceeds 8 MB.")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise WorldError("ZIP symbolic links are not supported.")
                with package.open(info) as stream:
                    files[name] = safe_json(stream.read(MAX_FILE + 1), name)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as problem:
        raise WorldError(f"Invalid world ZIP: {problem}") from None
    if not all(name in files for name in FILES[:3]):
        raise WorldError("ZIP is missing manifest.json, rooms.json, or exits.json.")
    if not isinstance(files["manifest.json"], dict):
        raise WorldError("manifest.json must contain an object.")
    return files


def starter_files(world_id, name, author):
    return {"manifest.json": {"format_version": 2, "id": world_id, "name": name, "author": author,
                              "start_room": "arrival", "layers": [{"z": 0, "name": "Ground"}]},
            "rooms.json": [{"id": "arrival", "name": "Arrival", "description": "Your world begins here.",
                            "x": 0, "y": 0, "z": 0, "grid_link": False}],
            "exits.json": []}


def check_new_id(world_id):
    if not isinstance(world_id, str) or not ID_RE.fullmatch(world_id) or world_id == HOME:
        raise WorldError("Choose a world ID of lowercase letters, digits, and hyphens, starting with a letter. "
                         "'stationnet' is reserved for the home world.")


@dataclass
class Session:
    key: str
    kind: str          # "existing": bound to an installed world; "new": not installed yet
    world_id: str
    path: Path
    files: dict
    meta: dict
    revision: int = 0
    history: list = field(default_factory=list)
    future: list = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)
    publishing: bool = False
    touched: float = field(default_factory=time.monotonic)
    editors: dict = field(default_factory=dict)

    def payload(self, selected=None, viewer=None):
        now = time.time()
        others = sorted(name for user, (name, seen) in self.editors.items()
                        if user != viewer and now - seen < PRESENCE_SECONDS)
        manifest = self.files.get("manifest.json") if isinstance(self.files.get("manifest.json"), dict) else {}
        return {"id": self.key, "kind": self.kind, "worldId": self.world_id,
                "path": f"{'Draft of' if self.kind == 'existing' else 'New world'} {self.world_id}",
                "name": manifest.get("name", self.world_id), "files": self.files, "revision": self.revision,
                "dirty": self.revision != self.meta.get("published_revision"),
                "canUndo": bool(self.history), "canRedo": bool(self.future), "selected": selected,
                "publishing": self.publishing, "editors": others,
                "seededAt": self.meta.get("seeded_at"), "publishedAt": self.meta.get("published_at")}


def key_for(kind, world_id):
    return f"{'w' if kind == 'existing' else 'n'}:{world_id}"


def parse_key(key):
    prefix, _, world_id = (key or "").partition(":")
    if prefix not in ("w", "n") or not ID_RE.fullmatch(world_id):
        raise WorldError("Unknown draft.")
    return ("existing" if prefix == "w" else "new"), world_id


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.sessions = {}
        self.lock = threading.RLock()

    # -- locations -----------------------------------------------------------------
    def folder(self, kind, world_id):
        return self.root / ("worlds" if kind == "existing" else "new") / world_id

    def exists(self, kind, world_id):
        return (self.folder(kind, world_id) / "draft.json").is_file()

    def new_drafts(self):
        folder = self.root / "new"
        result = []
        for path in sorted(folder.iterdir()) if folder.is_dir() else []:
            draft = path / "draft.json"
            if path.is_dir() and ID_RE.fullmatch(path.name) and draft.is_file():
                try:
                    data = json.loads(draft.read_text(encoding="utf-8"))
                    manifest = data.get("files", {}).get("manifest.json", {})
                    result.append({"key": key_for("new", path.name), "id": path.name,
                                   "name": manifest.get("name", path.name), "author": manifest.get("author", ""),
                                   "saved_at": data.get("saved_at")})
                except (OSError, ValueError, AttributeError):
                    result.append({"key": key_for("new", path.name), "id": path.name, "name": path.name,
                                   "error": "Draft could not be read."})
        return result

    def draft_status(self, world_id):
        """For the world list: whether an installed world has a draft, and who has it open."""
        meta_path = self.folder("existing", world_id) / "meta.json"
        if not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        session = self.sessions.get(key_for("existing", world_id))
        now = time.time()
        editors = sorted(name for name, seen in (session.editors.values() if session else [])
                         if now - seen < PRESENCE_SECONDS)
        return {"seeded_at": meta.get("seeded_at"), "published_at": meta.get("published_at"),
                "dirty": bool(session and session.revision != meta.get("published_revision"))
                if session else meta.get("dirty", False), "editors": editors}

    # -- persistence ---------------------------------------------------------------
    def _write(self, session, files=None):
        atomic_json(session.path / "draft.json", {"saved_at": time.time(), "revision": session.revision,
                                                  "files": session.files if files is None else files})

    def _write_meta(self, session):
        session.meta["dirty"] = session.revision != session.meta.get("published_revision")
        atomic_json(session.path / "meta.json", session.meta)

    def _load(self, kind, world_id):
        path = self.folder(kind, world_id)
        try:
            draft = json.loads((path / "draft.json").read_text(encoding="utf-8"))
            meta = json.loads((path / "meta.json").read_text(encoding="utf-8")) if (path / "meta.json").exists() else {}
        except (OSError, ValueError) as problem:
            raise WorldError(f"The draft could not be read: {problem}") from None
        if not isinstance(draft, dict) or not isinstance(draft.get("files"), dict):
            raise WorldError("The draft is damaged; restore a backup.")
        return Session(key_for(kind, world_id), kind, world_id, path, draft["files"], meta,
                       revision=int(draft.get("revision", 0)))

    def get(self, key):
        kind, world_id = parse_key(key)
        with self.lock:
            self._evict()
            session = self.sessions.get(key)
            if session is None:
                if not self.exists(kind, world_id):
                    raise WorldError("This draft no longer exists. Reopen the world.")
                session = self.sessions[key] = self._load(kind, world_id)
            session.touched = time.monotonic()
            return session

    def _evict(self):
        cutoff = time.monotonic() - IDLE_SECONDS
        for key, session in list(self.sessions.items()):
            if session.touched < cutoff and not session.publishing and session.lock.acquire(blocking=False):
                try:
                    del self.sessions[key]
                finally:
                    session.lock.release()

    def _start(self, kind, world_id, files, meta):
        path = self.folder(kind, world_id)
        path.mkdir(parents=True, exist_ok=True)
        session = Session(key_for(kind, world_id), kind, world_id, path, files, meta)
        self._write(session)
        self._write_meta(session)
        self.sessions[session.key] = session
        return session

    # -- creating drafts -------------------------------------------------------------
    def create_new(self, world_id, name, author, installed):
        check_new_id(world_id)
        name, author = (name or "").strip(), (author or "").strip()
        if not 2 <= len(name) <= 80:
            raise WorldError("World name must be 2–80 characters.")
        if not 1 <= len(author) <= 80:
            raise WorldError("Author must be 1–80 characters.")
        return self.adopt_new(starter_files(world_id, name, author), installed)

    def adopt_new(self, files, installed):
        """Start a new-world draft from complete files (a template, an import, or a duplicate)."""
        world_id = files.get("manifest.json", {}).get("id")
        check_new_id(world_id)
        with self.lock:
            if world_id in installed:
                raise WorldError("A world with that ID is already installed. Open it instead.")
            if self.exists("new", world_id):
                raise WorldError("A new-world draft with that ID already exists.")
            if len(self.new_drafts()) >= MAX_NEW_DRAFTS:
                raise WorldError(f"A station keeps at most {MAX_NEW_DRAFTS} unpublished world drafts.")
            return self._start("new", world_id, files, {"kind": "new", "world_id": world_id,
                                                        "seeded_at": time.time()})

    def seed(self, world_id, files, base):
        """Bind a draft to an installed world, starting from its current published content."""
        with self.lock:
            if self.exists("existing", world_id):
                return self.get(key_for("existing", world_id))
            meta = {"kind": "existing", "world_id": world_id, "seeded_at": time.time(), **base,
                    "published_revision": 0}
            return self._start("existing", world_id, files, meta)

    # -- editing -----------------------------------------------------------------------
    def check_revision(self, session, revision):
        if revision != session.revision:
            raise Conflict("This draft changed in another tab or by another builder. "
                           "Refresh the editor state before editing again.")
        if session.publishing:
            raise Conflict("This draft is being published. Wait a moment, then refresh.")

    def transaction(self, key, revision, operation, args, mutate):
        session = self.get(key)
        with session.lock:
            self.check_revision(session, revision)
            before = session.files
            if operation == "undo":
                if not session.history:
                    raise WorldError("Nothing to undo.")
                after, selection = session.history[-1], None
            elif operation == "redo":
                if not session.future:
                    raise WorldError("Nothing to redo.")
                after, selection = session.future[-1], None
            else:
                after, selection = mutate(before, operation, args)
            if session.kind == "existing" or operation in ("undo", "redo"):
                manifest = after.get("manifest.json") if isinstance(after.get("manifest.json"), dict) else {}
                if manifest.get("id") != session.world_id:
                    raise WorldError("A draft keeps its world's ID. Duplicate the world to give it a new ID.")
            elif not ID_RE.fullmatch(str((after.get("manifest.json") or {}).get("id", ""))) or \
                    (after.get("manifest.json") or {}).get("id") != session.world_id:
                raise WorldError("A new world's ID is chosen when it is created; duplicate it to change the ID.")
            # Persist the next draft before changing the live revision or history.
            session.revision += 1
            try:
                self._write(session, after)
            except BaseException:
                session.revision -= 1
                raise
            if operation == "undo":
                session.history.pop()
                session.future.append(before)
            elif operation == "redo":
                session.future.pop()
                session.history.append(before)
            else:
                session.history.append(before)
                session.future.clear()
            session.history = session.history[-HISTORY:]
            session.files = after
            return session.payload(selection)

    def replace(self, key, revision, files, *, backup_reason=None):
        """Swap in whole new draft files (restore, rebase); the previous state stays undoable."""
        session = self.get(key)
        with session.lock:
            self.check_revision(session, revision)
            if backup_reason:
                self.backup(session, backup_reason)
            session.history.append(session.files)
            session.history = session.history[-HISTORY:]
            session.future.clear()
            session.files = files
            session.revision += 1
            self._write(session)
            return session.payload()

    # -- backups -----------------------------------------------------------------------
    def backup(self, session, reason="checkpoint"):
        folder = session.path / "backups"
        stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        atomic_json(folder / f"{stamp}.json", {"saved_at": time.time(), "reason": reason, "files": session.files})
        for old in sorted(folder.glob("*.json"), reverse=True)[20:]:
            old.unlink()
        return stamp

    def checkpoint(self, key, revision):
        session = self.get(key)
        with session.lock:
            self.check_revision(session, revision)
            self.backup(session, "checkpoint")
            return session.payload()

    def backups(self, key):
        folder = self.get(key).path / "backups"
        result = []
        for path in sorted(folder.glob("*.json"), reverse=True) if folder.exists() else []:
            try:
                reason = json.loads(path.read_text(encoding="utf-8")).get("reason", "checkpoint")
            except (OSError, ValueError):
                reason = "unreadable"
            result.append({"id": path.stem, "bytes": path.stat().st_size, "modified": path.stat().st_mtime,
                           "reason": reason})
        return result

    def restore(self, key, revision, backup_id):
        if not BACKUP_RE.fullmatch(backup_id or ""):
            raise WorldError("Invalid backup ID.")
        session = self.get(key)
        try:
            data = json.loads((session.path / "backups" / f"{backup_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise WorldError("That backup could not be read.") from None
        if not isinstance(data.get("files"), dict):
            raise WorldError("That backup is damaged.")
        return self.replace(key, revision, data["files"], backup_reason="before restore")

    def discard(self, key, revision):
        """Delete a draft (after a final backup for existing worlds, kept beside the next draft)."""
        session = self.get(key)
        with session.lock:
            self.check_revision(session, revision)
            with self.lock:
                self.sessions.pop(key, None)
                if session.kind == "existing":
                    keep = self.root / "discarded" / f"{session.world_id}-{uuid.uuid4().hex[:8]}"
                    keep.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(session.path), str(keep))
                    for old in sorted(keep.parent.glob(f"{session.world_id}-*"))[:-5]:
                        shutil.rmtree(old, ignore_errors=True)
                else:
                    shutil.rmtree(session.path, ignore_errors=True)

    def touch(self, session, user_id, name):
        session.editors[user_id] = (name, time.time())

    # -- publishing ----------------------------------------------------------------------
    def published(self, session, *, revision, base):
        """Record a publish: a new world's draft becomes the installed world's draft."""
        with self.lock:
            session.meta.update(base, published_at=time.time(), published_revision=revision)
            if session.kind == "new":
                target = self.folder("existing", session.world_id)
                if target.exists():
                    shutil.rmtree(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                self.sessions.pop(session.key, None)
                shutil.move(str(session.path), str(target))
                session.kind, session.path = "existing", target
                session.key = key_for("existing", session.world_id)
                session.meta["kind"] = "existing"
                self.sessions[session.key] = session
            self._write_meta(session)
            return session

    def remove_world(self, world_id):
        """Forget the draft of a world that was removed from the station."""
        with self.lock:
            self.sessions.pop(key_for("existing", world_id), None)
            shutil.rmtree(self.folder("existing", world_id), ignore_errors=True)


def snapshot(files):
    return copy.deepcopy(files)
