"""TV programming library storage: uploads, server-folder imports and probing."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time

from flask import current_app

FORMATS = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg"}
CHUNK_BYTES = 8 * 1024 * 1024
FINGERPRINT_BYTES = 1024 * 1024
STALE_UPLOAD_SECONDS = 48 * 3600
UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
IMPORT_BATCH = 20


def root():
    path = Path(current_app.config["DATA_DIR"]) / "tv"
    path.mkdir(parents=True, exist_ok=True)
    return path


def folder(name):
    path = root() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve(rel_path):
    """Absolute path of a library entry, or None if it escapes the TV folder."""
    base = root().resolve()
    try:
        path = (base / rel_path).resolve()
    except (OSError, ValueError):
        return None
    return path if path.is_relative_to(base) and path != base else None


def supported(name):
    return Path(name).suffix.lower() in FORMATS


def valid_signature(path):
    try:
        with open(path, "rb") as handle:
            header = handle.read(16)
    except OSError:
        return False
    return header[4:8] == b"ftyp" or header.startswith(b"\x1a\x45\xdf\xa3") or header.startswith(b"OggS")


def fingerprint(path):
    """Fast identity for very large files: size plus the first and last MiB."""
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode())
    with open(path, "rb") as handle:
        digest.update(handle.read(FINGERPRINT_BYTES))
        if size > FINGERPRINT_BYTES:
            handle.seek(max(FINGERPRINT_BYTES, size - FINGERPRINT_BYTES))
            digest.update(handle.read(FINGERPRINT_BYTES))
    return digest.hexdigest()


def clean_folder(value):
    parts = [part for part in PurePosixPath(str(value or "").replace("\\", "/")).parts
             if part not in {"", ".", "..", "/"}]
    return "/".join(part[:80] for part in parts)[:300]


def title_from(name):
    return re.sub(r"[_\.]+", " ", Path(name).stem).strip()[:150] or "Untitled"


EPISODE_RE = re.compile(r"(?i)(?:\bS(\d{1,3})[ ._-]*E(\d{1,4})\b|\b(\d{1,2})x(\d{1,3})\b)")


def guess_episode(name, folder=""):
    """Series, season and episode from names like ``Show.S01E02.mkv`` or ``Show 1x02``; empty if none."""
    title = title_from(name)
    match = EPISODE_RE.search(title)
    if not match:
        return {}
    season, episode = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
    series = re.sub(r"[\s\-–:]+$", "", title[:match.start()]).strip()
    if not series and folder:
        parts = [part for part in folder.split("/") if not re.fullmatch(r"(?i)season\s*\d+|s\d+", part.strip())]
        series = parts[-1].strip() if parts else ""
    return {"series": series[:150], "season": int(season), "episode": int(episode)}


def natural_key(text):
    """Sort key treating digit runs as numbers, so "Part 10" follows "Part 9"."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text or "")]


def probe(path):
    """Duration and frame size via optional ffprobe; empty when unavailable."""
    executable = shutil.which("ffprobe")
    if not executable:
        return {}
    try:
        output = subprocess.run(
            [executable, "-v", "error", "-print_format", "json", "-show_entries",
             "format=duration:stream=codec_type,width,height", str(path)],
            capture_output=True, timeout=20, check=False).stdout
        data = json.loads(output or b"{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}
    result = {}
    try:
        duration = float(data.get("format", {}).get("duration", 0))
        if duration > 0:
            result["duration_s"] = duration
    except (TypeError, ValueError):
        pass
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and stream.get("width"):
            result["width"], result["height"] = int(stream["width"]), int(stream.get("height") or 0)
            break
    return result


# Chunked uploads ---------------------------------------------------------

def _session_paths(upload_id):
    if not UPLOAD_ID_RE.fullmatch(upload_id or ""):
        return None, None
    incoming = folder("incoming")
    return incoming / f"{upload_id}.json", incoming / f"{upload_id}.part"


def purge_stale_uploads():
    cutoff = time.time() - STALE_UPLOAD_SECONDS
    for path in folder("incoming").iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def create_session(meta):
    upload_id = secrets.token_hex(16)
    meta_path, part_path = _session_paths(upload_id)
    part_path.touch()
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return upload_id


def load_session(upload_id):
    meta_path, part_path = _session_paths(upload_id)
    if meta_path is None or not meta_path.is_file() or not part_path.is_file():
        return None, None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8")), part_path
    except (OSError, ValueError):
        return None, None


def discard_session(upload_id):
    for path in _session_paths(upload_id):
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def append_chunk(part_path, offset, data):
    """Append when ``offset`` matches the stored size; return the new size or None."""
    with open(part_path, "r+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() != offset:
            return None
        handle.write(data)
        return handle.tell()


def store_upload(part_path, name):
    """Move a completed part into managed storage; return its library rel_path."""
    stored = secrets.token_hex(20) + Path(name).suffix.lower()
    target = folder("media") / stored
    os.replace(part_path, target)
    return f"media/{stored}"


# Server-folder import ----------------------------------------------------

def import_candidates():
    """Sorted relative POSIX paths of every file below tv/import (no symlinked dirs)."""
    base = folder("import")
    found = []
    for directory, dirs, files in os.walk(base, followlinks=False):
        dirs.sort()
        for name in files:
            found.append(Path(directory, name).relative_to(base).as_posix())
    return sorted(found)
