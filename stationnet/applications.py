"""StationNet games/applications catalog and isolated script delivery."""
import base64
import hmac
import json
import re
from dataclasses import dataclass
from pathlib import Path

from flask import Blueprint, abort, g, jsonify, make_response, render_template, request, session

from .auth import login_required
from .db import get_db

bp = Blueprint("applications", __name__, url_prefix="/games-apps")

GAMES = "Games"
APPS = "Apps"
CATEGORIES = (GAMES, APPS)
STORAGE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
STORAGE_MAX_KEYS = 100
STORAGE_MAX_VALUE_BYTES = 64 * 1024
STORAGE_USER_BYTES = 512 * 1024
STORAGE_SHARED_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class Application:
    slug: str
    name: str
    description: str
    author: str
    version: str
    category: str
    entrypoint: str | None = None

    @property
    def runnable(self) -> bool:
        return self.entrypoint is not None


_catalog: dict[str, Application] = {}


def _package_root() -> Path:
    return Path(__file__).with_name("application_packages").resolve()


def _entrypoint(application: Application) -> Path | None:
    if not application.entrypoint:
        return None
    root = _package_root()
    path = (root / application.entrypoint).resolve()
    return path if root in path.parents and path.is_file() else None


def register(application: Application) -> None:
    """Add application metadata to the station-wide catalog."""
    if application.category not in CATEGORIES:
        raise ValueError(f"Unknown application category: {application.category}")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", application.slug) or application.slug in _catalog:
        raise ValueError(f"Invalid or duplicate application slug: {application.slug}")
    if any(not value or len(value) > limit for value, limit in (
        (application.name, 100), (application.description, 500),
        (application.author, 100), (application.version, 30),
    )):
        raise ValueError(f"Invalid application metadata: {application.slug}")
    _catalog[application.slug] = application


def catalog() -> tuple[Application, ...]:
    """Return registered applications in stable registration order."""
    return tuple(_catalog.values())


def find(slug: str) -> Application | None:
    return _catalog.get(slug)


def _load_package(folder: str) -> Application:
    """Load one explicitly bundled package from its small public manifest."""
    manifest_path = _package_root() / folder / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = ("slug", "name", "description", "author", "version", "category", "entrypoint")
    if any(not isinstance(data.get(key), str) for key in required):
        raise ValueError(f"Invalid application manifest: {manifest_path}")
    return Application(
        slug=data["slug"], name=data["name"], description=data["description"],
        author=data["author"], version=data["version"], category=data["category"],
        entrypoint=f"{folder}/{data['entrypoint']}",
    )


for _application in (
    Application(
        slug="word-chain",
        name="Word Chain [Placeholder]",
        description="A future word game built for quick rounds and high scores.",
        author="StationNet Core",
        version="0.0.1",
        category=GAMES,
    ),
    _load_package("number_guess"),
    Application(
        slug="event-board",
        name="Event Board [Placeholder]",
        description="A future utility for organizing community events and sign-ups.",
        author="StationNet Core",
        version="0.0.1",
        category=APPS,
    ),
    Application(
        slug="note-stack",
        name="Note Stack [Placeholder]",
        description="A future utility for keeping small personal notes and lists.",
        author="StationNet Core",
        version="0.0.1",
        category=APPS,
    ),
):
    register(_application)


@bp.get("")
@login_required
def index():
    grouped = {
        category: tuple(item for item in catalog() if item.category == category)
        for category in CATEGORIES
    }
    return render_template(
        "applications/index.html", categories=CATEGORIES, applications=grouped
    )


@bp.get("/<slug>")
@login_required
def display(slug):
    application = find(slug)
    if application is None:
        abort(404)
    return render_template("applications/display.html", application=application)


def _storage_error(message, status=400):
    return jsonify(ok=False, error=message), status


def _storage_namespace(scope):
    if scope == "user":
        return f"user:{g.user['id']}"
    if scope == "shared":
        return "shared"
    return None


@bp.post("/<slug>/storage")
@login_required
def storage(slug):
    """Broker bounded JSON storage without exposing SQL or namespace ownership."""
    application = find(slug)
    if application is None or not application.runnable:
        abort(404)
    token = request.headers.get("X-CSRF-Token", "")
    if not session.get("csrf_token") or not hmac.compare_digest(token, session["csrf_token"]):
        return _storage_error("Invalid or expired request token.", 400)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _storage_error("A JSON request body is required.")
    operation = data.get("operation")
    scope = data.get("scope", "user")
    owner_key = _storage_namespace(scope)
    if owner_key is None:
        return _storage_error("Storage scope must be 'user' or 'shared'.")
    database = get_db()
    params = (slug, scope, owner_key)

    if operation == "keys":
        rows = database.execute(
            "SELECT storage_key FROM application_storage "
            "WHERE application_slug=? AND storage_scope=? AND owner_key=? ORDER BY storage_key",
            params,
        ).fetchall()
        return jsonify(ok=True, value=[row["storage_key"] for row in rows])

    key = data.get("key")
    if not isinstance(key, str) or not STORAGE_KEY_RE.fullmatch(key):
        return _storage_error("Keys must be 1-80 characters using letters, numbers, '.', '_', ':', or '-'.")
    item_params = (*params, key)
    if operation == "get":
        row = database.execute(
            "SELECT value_json FROM application_storage WHERE application_slug=? "
            "AND storage_scope=? AND owner_key=? AND storage_key=?", item_params,
        ).fetchone()
        return jsonify(ok=True, value=None if row is None else json.loads(row["value_json"]), found=row is not None)
    if operation == "delete":
        cursor = database.execute(
            "DELETE FROM application_storage WHERE application_slug=? AND storage_scope=? "
            "AND owner_key=? AND storage_key=?", item_params,
        )
        database.commit()
        return jsonify(ok=True, value=bool(cursor.rowcount))
    if operation != "set" or "value" not in data:
        return _storage_error("Unknown storage operation.")
    try:
        serialized = json.dumps(data["value"], ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return _storage_error("Storage values must be JSON-compatible.")
    size = len(serialized.encode("utf-8"))
    if size > STORAGE_MAX_VALUE_BYTES:
        return _storage_error("Storage value exceeds the 64 KiB limit.", 413)
    usage = database.execute(
        "SELECT COUNT(*) AS key_count,COALESCE(SUM(size_bytes),0) AS used_bytes "
        "FROM application_storage WHERE application_slug=? AND storage_scope=? AND owner_key=?",
        params,
    ).fetchone()
    previous = database.execute(
        "SELECT size_bytes FROM application_storage WHERE application_slug=? AND storage_scope=? "
        "AND owner_key=? AND storage_key=?", item_params,
    ).fetchone()
    if previous is None and usage["key_count"] >= STORAGE_MAX_KEYS:
        return _storage_error("Storage namespace has reached its 100-key limit.", 413)
    projected = usage["used_bytes"] - (previous["size_bytes"] if previous else 0) + size
    byte_limit = STORAGE_SHARED_BYTES if scope == "shared" else STORAGE_USER_BYTES
    if projected > byte_limit:
        return _storage_error("Storage namespace has reached its size limit.", 413)
    database.execute(
        "INSERT INTO application_storage(application_slug,storage_scope,owner_key,storage_key,value_json,size_bytes) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(application_slug,storage_scope,owner_key,storage_key) "
        "DO UPDATE SET value_json=excluded.value_json,size_bytes=excluded.size_bytes,updated_at=CURRENT_TIMESTAMP",
        (*item_params, serialized, size),
    )
    database.commit()
    return jsonify(ok=True, value=True)


@bp.get("/<slug>/runtime")
@login_required
def runtime(slug):
    """Return the tiny sandbox document used to execute an application."""
    application = find(slug)
    entrypoint = _entrypoint(application) if application is not None else None
    if application is None or entrypoint is None:
        abort(404)
    worker = Path(__file__).with_name("static") / "application_runtime.js"
    return render_template(
        "applications/runtime.html",
        application=application,
        application_source=base64.b64encode(entrypoint.read_bytes()).decode("ascii"),
        worker_source=base64.b64encode(worker.read_bytes()).decode("ascii"),
    )


@bp.get("/<slug>/source.js")
@login_required
def source(slug):
    """Serve only a registered entrypoint; paths never come from the request."""
    application = find(slug)
    if application is None or not application.entrypoint:
        abort(404)
    path = _entrypoint(application)
    if path is None:
        abort(404)
    response = make_response(path.read_text(encoding="utf-8"))
    response.mimetype = "application/javascript"
    response.headers["Cache-Control"] = "no-store"
    return response
