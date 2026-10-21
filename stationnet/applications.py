"""StationNet games/apps catalog, package management, and isolated delivery."""
import base64
import hmac
import io
import json
import os
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from flask import (
    Blueprint, abort, current_app, flash, g, has_app_context, jsonify,
    make_response, redirect, render_template, request, session, url_for,
)

from .auth import admin_required, login_required
from .db import get_db
from .security import validate_csrf

bp = Blueprint("applications", __name__, url_prefix="/games-apps")
admin_bp = Blueprint("application_admin", __name__, url_prefix="/sysop/applications")

GAMES = "Games"
APPS = "Apps"
CATEGORIES = (GAMES, APPS)
PACKAGE_FORMAT = 1
PACKAGE_MAX_BYTES = 2 * 1024 * 1024
PACKAGE_MAX_EXPANDED_BYTES = 1024 * 1024
PACKAGE_MAX_FILES = 10
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
ENTRYPOINT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.js")
STORAGE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
STORAGE_MAX_KEYS = 100
STORAGE_MAX_VALUE_BYTES = 64 * 1024
STORAGE_USER_BYTES = 512 * 1024
STORAGE_SHARED_BYTES = 2 * 1024 * 1024


class PackageError(ValueError):
    """A package is invalid or cannot be safely installed."""


@dataclass(frozen=True)
class Application:
    slug: str
    name: str
    description: str
    author: str
    version: str
    category: str
    entrypoint: str | None = None
    builtin: bool = True
    enabled: bool = True

    @property
    def runnable(self) -> bool:
        return self.entrypoint is not None


_catalog: dict[str, Application] = {}


def _package_root() -> Path:
    return Path(__file__).with_name("application_packages").resolve()


def _installed_root() -> Path:
    root = Path(current_app.config["APPLICATIONS_DIR"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _entrypoint(application: Application) -> Path | None:
    if not application.entrypoint:
        return None
    root = _package_root() if application.builtin else _installed_root() / application.slug
    root = root.resolve()
    path = (root / application.entrypoint).resolve()
    return path if root in path.parents and path.is_file() else None


def _validate_metadata(application: Application) -> None:
    if application.category not in CATEGORIES:
        raise ValueError(f"Unknown application category: {application.category}")
    if not SLUG_RE.fullmatch(application.slug):
        raise ValueError(f"Invalid application identifier: {application.slug}")
    if any(not value or value != value.strip() or len(value) > limit for value, limit in (
        (application.name, 100), (application.description, 500),
        (application.author, 100), (application.version, 30),
    )):
        raise ValueError(f"Invalid application metadata: {application.slug}")


def register(application: Application) -> None:
    """Add bundled application metadata to the station-wide catalog."""
    _validate_metadata(application)
    if application.slug in _catalog:
        raise ValueError(f"Duplicate application identifier: {application.slug}")
    _catalog[application.slug] = application


def catalog() -> tuple[Application, ...]:
    """Return bundled and installed applications in stable display order."""
    bundled = tuple(_catalog.values())
    if not has_app_context():
        return bundled
    database = get_db()
    states = {
        row["application_slug"]: bool(row["enabled"])
        for row in database.execute("SELECT application_slug,enabled FROM application_states")
    }
    applications = [replace(item, enabled=states.get(item.slug, True)) for item in bundled]
    for row in database.execute(
        "SELECT slug,name,description,author,version,category,entrypoint "
        "FROM installed_applications ORDER BY installed_at,slug"
    ):
        if row["slug"] in _catalog:
            continue
        applications.append(Application(
            slug=row["slug"], name=row["name"], description=row["description"],
            author=row["author"], version=row["version"], category=row["category"],
            entrypoint=row["entrypoint"], builtin=False,
            enabled=states.get(row["slug"], True),
        ))
    return tuple(applications)


def find(slug: str) -> Application | None:
    return next((item for item in catalog() if item.slug == slug), None)


def _load_package(folder: str) -> Application:
    """Load one explicitly bundled package from its public manifest."""
    manifest_path = _package_root() / folder / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = ("slug", "name", "description", "author", "version", "category", "entrypoint")
    if any(not isinstance(data.get(key), str) for key in required):
        raise ValueError(f"Invalid application manifest: {manifest_path}")
    application = Application(
        slug=data["slug"], name=data["name"], description=data["description"],
        author=data["author"], version=data["version"], category=data["category"],
        entrypoint=f"{folder}/{data['entrypoint']}",
    )
    _validate_metadata(application)
    return application


for _application in (
    Application(
        slug="word-chain", name="Word Chain [Placeholder]",
        description="A future word game built for quick rounds and high scores.",
        author="StationNet Core", version="0.0.1", category=GAMES,
    ),
    _load_package("number_guess"),
    Application(
        slug="event-board", name="Event Board [Placeholder]",
        description="A future utility for organizing community events and sign-ups.",
        author="StationNet Core", version="0.0.1", category=APPS,
    ),
    Application(
        slug="note-stack", name="Note Stack [Placeholder]",
        description="A future utility for keeping small personal notes and lists.",
        author="StationNet Core", version="0.0.1", category=APPS,
    ),
):
    register(_application)


def _safe_archive_name(name: str) -> PurePosixPath:
    if not name or "\\" in name or name.startswith("/"):
        raise PackageError(f"Unsafe archive path: {name or '(empty)'}.")
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts) or ":" in path.parts[0]:
        raise PackageError(f"Unsafe archive path: {name}.")
    return path


def validate_package(package_bytes: bytes) -> tuple[Application, dict[str, bytes]]:
    """Validate an uploaded ZIP fully before anything is written or registered."""
    if not package_bytes:
        raise PackageError("Choose a non-empty ZIP package.")
    if len(package_bytes) > PACKAGE_MAX_BYTES:
        raise PackageError("Package exceeds the 2 MiB upload limit.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(package_bytes))
    except (zipfile.BadZipFile, OSError):
        raise PackageError("The uploaded file is not a valid ZIP archive.") from None
    with archive:
        infos = archive.infolist()
        files = [info for info in infos if not info.is_dir()]
        if len(files) > PACKAGE_MAX_FILES:
            raise PackageError(f"Package contains more than {PACKAGE_MAX_FILES} files.")
        if not files:
            raise PackageError("Package does not contain any files.")
        names: dict[str, zipfile.ZipInfo] = {}
        expanded = 0
        for info in infos:
            path = _safe_archive_name(info.filename)
            if info.flag_bits & 0x1:
                raise PackageError("Encrypted package entries are not supported.")
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise PackageError(f"Symbolic links are not supported: {info.filename}.")
            if info.is_dir():
                raise PackageError(f"Package folders are not supported: {info.filename}.")
            normalized = path.as_posix()
            folded = normalized.casefold()
            if folded in names:
                raise PackageError(f"Package contains a duplicate path: {normalized}.")
            names[folded] = info
            expanded += info.file_size
        if expanded > PACKAGE_MAX_EXPANDED_BYTES:
            raise PackageError("Expanded package exceeds the 1 MiB limit.")
        manifest_info = names.get("manifest.json")
        if manifest_info is None or PurePosixPath(manifest_info.filename).parent != PurePosixPath("."):
            raise PackageError("Package must contain manifest.json at its root.")
        try:
            manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
        except (
            UnicodeDecodeError, json.JSONDecodeError, RuntimeError,
            NotImplementedError, zipfile.BadZipFile,
        ):
            raise PackageError("manifest.json must contain valid UTF-8 JSON.") from None
        required = ("slug", "name", "description", "author", "version", "category", "entrypoint")
        if not isinstance(manifest, dict) or any(not isinstance(manifest.get(key), str) for key in required):
            raise PackageError("Manifest is missing required text fields.")
        if type(manifest.get("package_format")) is not int or manifest["package_format"] != PACKAGE_FORMAT:
            raise PackageError(f"Unsupported package format; expected package_format {PACKAGE_FORMAT}.")
        entrypoint = manifest["entrypoint"]
        if not ENTRYPOINT_RE.fullmatch(entrypoint):
            raise PackageError("Entrypoint must be a root-level JavaScript (.js) file.")
        allowed = {"manifest.json", entrypoint.casefold()}
        unsupported = [info.filename for key, info in names.items() if key not in allowed]
        if unsupported:
            raise PackageError(f"Unsupported package file: {unsupported[0]}.")
        entry_info = names.get(entrypoint.casefold())
        if entry_info is None or PurePosixPath(entry_info.filename).parent != PurePosixPath("."):
            raise PackageError(f"Package entrypoint is missing: {entrypoint}.")
        application = Application(
            slug=manifest["slug"], name=manifest["name"],
            description=manifest["description"], author=manifest["author"],
            version=manifest["version"], category=manifest["category"],
            entrypoint=entrypoint, builtin=False,
        )
        try:
            _validate_metadata(application)
        except ValueError as error:
            raise PackageError(str(error)) from None
        try:
            script = archive.read(entry_info)
            script.decode("utf-8")
        except (UnicodeDecodeError, RuntimeError, NotImplementedError, zipfile.BadZipFile):
            raise PackageError("Application script must be valid UTF-8 text.") from None
        if not script.strip():
            raise PackageError("Application script cannot be empty.")
        return application, {"manifest.json": archive.read(manifest_info), entrypoint: script}


def install_package(package_bytes: bytes) -> Application:
    """Atomically install and register one validated third-party package."""
    application, files = validate_package(package_bytes)
    if application.slug in _catalog:
        raise PackageError(f"Identifier '{application.slug}' belongs to a built-in application.")
    database = get_db()
    if database.execute("SELECT 1 FROM installed_applications WHERE slug=?", (application.slug,)).fetchone():
        raise PackageError(f"Application '{application.slug}' is already installed.")
    root = _installed_root()
    destination = root / application.slug
    if destination.exists():
        raise PackageError("An application directory with that identifier already exists.")
    staging = root / f".installing-{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        for name, content in files.items():
            (staging / name).write_bytes(content)
        os.replace(staging, destination)
        database.execute("BEGIN IMMEDIATE")
        database.execute(
            "INSERT INTO installed_applications(slug,name,description,author,version,category,entrypoint) "
            "VALUES(?,?,?,?,?,?,?)",
            (application.slug, application.name, application.description, application.author,
             application.version, application.category, application.entrypoint),
        )
        database.execute(
            "INSERT INTO application_states(application_slug,enabled) VALUES(?,1) "
            "ON CONFLICT(application_slug) DO UPDATE SET enabled=1,updated_at=CURRENT_TIMESTAMP",
            (application.slug,),
        )
        database.commit()
    except Exception:
        database.rollback()
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return application


@bp.get("")
@login_required
def index():
    visible = tuple(item for item in catalog() if item.enabled)
    grouped = {
        category: tuple(item for item in visible if item.category == category)
        for category in CATEGORIES
    }
    return render_template("applications/index.html", categories=CATEGORIES, applications=grouped)


@bp.get("/<slug>")
@login_required
def display(slug):
    application = find(slug)
    if application is None or not application.enabled:
        abort(404)
    return render_template("applications/display.html", application=application)


@admin_bp.get("")
@admin_required
def manage():
    return render_template("applications/manage.html", applications=catalog())


@admin_bp.post("/install")
@admin_required
def install():
    validate_csrf()
    upload = request.files.get("package")
    if upload is None or not upload.filename:
        flash("Choose a StationNet application ZIP package.", "error")
    elif not upload.filename.lower().endswith(".zip"):
        flash("Application packages must use the .zip extension.", "error")
    else:
        package_bytes = upload.stream.read(PACKAGE_MAX_BYTES + 1)
        try:
            application = install_package(package_bytes)
        except PackageError as error:
            flash(f"Package rejected: {error}", "error")
        except Exception:
            current_app.logger.exception("Application installation failed")
            flash("Installation failed cleanly; no application was registered.", "error")
        else:
            flash(f"{application.name} {application.version} installed and enabled.", "success")
    return redirect(url_for("application_admin.manage"))


@admin_bp.post("/<slug>/state")
@admin_required
def set_state(slug):
    validate_csrf()
    application = find(slug)
    if application is None:
        abort(404)
    enabled = request.form.get("enabled") == "1"
    database = get_db()
    database.execute(
        "INSERT INTO application_states(application_slug,enabled) VALUES(?,?) "
        "ON CONFLICT(application_slug) DO UPDATE SET enabled=excluded.enabled,updated_at=CURRENT_TIMESTAMP",
        (slug, enabled),
    )
    database.commit()
    flash(f"{application.name} is now {'enabled' if enabled else 'disabled'}.", "success")
    return redirect(url_for("application_admin.manage"))


@admin_bp.post("/<slug>/uninstall")
@admin_required
def uninstall(slug):
    validate_csrf()
    application = find(slug)
    if application is None:
        abort(404)
    if application.builtin:
        flash("Built-in applications cannot be uninstalled.", "error")
        return redirect(url_for("application_admin.manage"))
    if request.form.get("confirm") != slug:
        flash("Confirm the application identifier before uninstalling.", "error")
        return redirect(url_for("application_admin.manage"))
    root = _installed_root()
    directory = (root / slug).resolve()
    if directory.parent != root:
        abort(400)
    removing = root / f".removing-{uuid.uuid4().hex}"
    database = get_db()
    try:
        if directory.exists():
            os.replace(directory, removing)
        database.execute("BEGIN IMMEDIATE")
        database.execute("DELETE FROM installed_applications WHERE slug=?", (slug,))
        database.execute("DELETE FROM application_states WHERE application_slug=?", (slug,))
        database.commit()
    except Exception:
        database.rollback()
        if removing.exists() and not directory.exists():
            os.replace(removing, directory)
        current_app.logger.exception("Application uninstall failed")
        flash("Uninstall failed; the application remains registered.", "error")
        return redirect(url_for("application_admin.manage"))
    if removing.exists():
        shutil.rmtree(removing, ignore_errors=True)
    flash(f"{application.name} was uninstalled. Its saved user data was retained.", "success")
    return redirect(url_for("application_admin.manage"))


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
    if application is None or not application.enabled or not application.runnable:
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
            "WHERE application_slug=? AND storage_scope=? AND owner_key=? ORDER BY storage_key", params,
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
        "FROM application_storage WHERE application_slug=? AND storage_scope=? AND owner_key=?", params,
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
    entrypoint = _entrypoint(application) if application is not None and application.enabled else None
    if application is None or entrypoint is None:
        abort(404)
    worker = Path(__file__).with_name("static") / "application_runtime.js"
    return render_template(
        "applications/runtime.html", application=application,
        application_source=base64.b64encode(entrypoint.read_bytes()).decode("ascii"),
        worker_source=base64.b64encode(worker.read_bytes()).decode("ascii"),
    )


@bp.get("/<slug>/source.js")
@login_required
def source(slug):
    """Serve only a registered entrypoint; paths never come from the request."""
    application = find(slug)
    if application is None or not application.enabled or not application.entrypoint:
        abort(404)
    path = _entrypoint(application)
    if path is None:
        abort(404)
    response = make_response(path.read_text(encoding="utf-8"))
    response.mimetype = "application/javascript"
    response.headers["Cache-Control"] = "no-store"
    return response
