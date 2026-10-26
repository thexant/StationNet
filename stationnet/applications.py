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
from .multiplayer import MultiplayerError, handle_multiplayer
from .security import validate_csrf

bp = Blueprint("applications", __name__, url_prefix="/games-apps")
admin_bp = Blueprint("application_admin", __name__, url_prefix="/sysop/applications")

GAMES = "Games"
CATEGORIES = (GAMES, "Utilities", "Creative", "Educational", "Productivity", "Other")
OFFICE_CATEGORY = "Office"
PACKAGE_FORMAT = 1
PACKAGE_MAX_BYTES = 5 * 1024 * 1024
PACKAGE_MAX_EXPANDED_BYTES = 4 * 1024 * 1024
PACKAGE_MAX_FILE_BYTES = 1024 * 1024
PACKAGE_MAX_FILES = 64
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
ENTRYPOINT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\.js")
PACKAGE_PART_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
WINDOWS_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
STORAGE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
STORAGE_MAX_KEYS = 100
STORAGE_MAX_VALUE_BYTES = 2 * 1024 * 1024
STORAGE_USER_BYTES = 16 * 1024 * 1024
STORAGE_SHARED_BYTES = 2 * 1024 * 1024
SCORE_MAX = 1_000_000_000
MANIFEST_PERMISSIONS = frozenset({"multiplayer"})
MANIFEST_PLATFORMS = frozenset({"desktop", "touch"})
MANIFEST_INPUTS = frozenset({"keyboard", "pointer", "fine-pointer", "touch", "pointer-lock", "gamepad"})
PRESENTATION_MODES = frozenset({"standard", "immersive"})


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
    permissions: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ("desktop", "touch")
    required_inputs: tuple[str, ...] = ()
    preferred_presentation: str = "standard"
    launch_endpoint: str | None = None

    @property
    def runnable(self) -> bool:
        return self.entrypoint is not None or self.launch_endpoint is not None


OFFICE_PROGRAMS = (
    Application("office-sheets", "Sheets", "Create and edit private spreadsheets.",
                "StationNet", "1", OFFICE_CATEGORY, launch_endpoint="office.sheets"),
    Application("office-documents", "Documents", "Create and edit private documents.",
                "StationNet", "1", OFFICE_CATEGORY, launch_endpoint="office.documents"),
    Application("office-presentations", "Presentations", "Create and edit private presentations.",
                "StationNet", "1", OFFICE_CATEGORY, launch_endpoint="office.presentations"),
)


def _office_program(slug: str) -> Application | None:
    if g.station and g.station["office_enabled"]:
        return next((item for item in OFFICE_PROGRAMS if item.slug == slug), None)
    return None


_catalog: dict[str, Application] = {}
_bundled_cache: dict[str, tuple[tuple[int, int], Application]] = {}


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


def _package_files(application: Application) -> dict[str, str]:
    """Embed bounded package resources in the isolated worker; no file URL is exposed."""
    folder = application.entrypoint.split("/", 1)[0] if application.builtin else application.slug
    root = (_package_root() / folder if application.builtin else
            _installed_root() / application.slug).resolve()
    result: dict[str, str] = {}
    total = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or root not in path.resolve().parents:
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.json":
            continue
        size = path.stat().st_size
        total += size
        if size > PACKAGE_MAX_FILE_BYTES or total > PACKAGE_MAX_EXPANDED_BYTES or len(result) >= PACKAGE_MAX_FILES:
            raise PackageError("Stored application exceeds package resource limits.")
        result[relative] = base64.b64encode(path.read_bytes()).decode("ascii")
    return result


def _validate_metadata(application: Application) -> None:
    if application.category not in CATEGORIES:
        raise ValueError(f"Unknown application category: {application.category}")
    if application.slug in {item.slug for item in OFFICE_PROGRAMS}:
        raise ValueError(f"Reserved application identifier: {application.slug}")
    if not SLUG_RE.fullmatch(application.slug):
        raise ValueError(f"Invalid application identifier: {application.slug}")
    if any(not value or value != value.strip() or len(value) > limit for value, limit in (
        (application.name, 100), (application.description, 500),
        (application.author, 100), (application.version, 30),
    )):
        raise ValueError(f"Invalid application metadata: {application.slug}")
    if (not isinstance(application.permissions, tuple)
            or any(item not in MANIFEST_PERMISSIONS for item in application.permissions)
            or len(set(application.permissions)) != len(application.permissions)):
        raise ValueError(f"Invalid application permissions: {application.slug}")
    if (not isinstance(application.platforms, tuple) or not application.platforms
            or any(item not in MANIFEST_PLATFORMS for item in application.platforms)
            or len(set(application.platforms)) != len(application.platforms)):
        raise ValueError(f"Invalid supported platforms: {application.slug}")
    if (not isinstance(application.required_inputs, tuple)
            or any(item not in MANIFEST_INPUTS for item in application.required_inputs)
            or len(set(application.required_inputs)) != len(application.required_inputs)):
        raise ValueError(f"Invalid input requirements: {application.slug}")
    if application.preferred_presentation not in PRESENTATION_MODES:
        raise ValueError(f"Invalid preferred presentation: {application.slug}")


def _manifest_capabilities(data: dict, error_type=ValueError):
    """Validate optional format-1 capability metadata and supply legacy defaults."""
    platforms = data.get("platforms", ["desktop", "touch"])
    if platforms == "universal":
        platforms = ["desktop", "touch"]
    if (not isinstance(platforms, list) or not platforms
            or any(not isinstance(item, str) for item in platforms)):
        raise error_type("Manifest platforms must be 'universal' or a non-empty array.")
    required_inputs = data.get("required_inputs", [])
    if (not isinstance(required_inputs, list)
            or any(not isinstance(item, str) for item in required_inputs)):
        raise error_type("Manifest required_inputs must be an array.")
    presentation = data.get("preferred_presentation", "standard")
    if not isinstance(presentation, str):
        raise error_type("Manifest preferred_presentation must be text.")
    return tuple(platforms), tuple(required_inputs), presentation


def register(application: Application) -> None:
    """Add bundled application metadata to the station-wide catalog."""
    _validate_metadata(application)
    if application.slug in _catalog:
        raise ValueError(f"Duplicate application identifier: {application.slug}")
    _catalog[application.slug] = application


def _discover_bundled() -> tuple[Application, ...]:
    """Discover bundled packages from disk using the public package layout."""
    discovered: list[Application] = []
    seen: set[str] = set()
    root = _package_root()
    if not root.is_dir():
        return ()
    for folder in sorted((item for item in root.iterdir() if item.is_dir()), key=lambda item: item.name):
        manifest = folder / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            signature = (manifest.stat().st_mtime_ns, manifest.stat().st_size)
            cached = _bundled_cache.get(folder.name)
            application = cached[1] if cached and cached[0] == signature else _load_package(folder.name)
            if application.slug in seen or application.slug in _catalog:
                raise ValueError(f"Duplicate application identifier: {application.slug}")
            if _entrypoint(application) is None:
                raise ValueError(f"Application entrypoint is missing: {manifest}")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            if has_app_context():
                current_app.logger.warning("Ignoring invalid bundled application %s: %s", folder, error)
            continue
        _bundled_cache[folder.name] = (signature, application)
        discovered.append(application)
        seen.add(application.slug)
    for folder in tuple(_bundled_cache):
        if not (root / folder / "manifest.json").is_file():
            _bundled_cache.pop(folder, None)
    return tuple(discovered)


def catalog() -> tuple[Application, ...]:
    """Return bundled and installed applications in stable display order."""
    bundled = (*_catalog.values(), *_discover_bundled())
    bundled_slugs = {item.slug for item in bundled}
    if not has_app_context():
        return bundled
    database = get_db()
    states = {
        row["application_slug"]: bool(row["enabled"])
        for row in database.execute("SELECT application_slug,enabled FROM application_states")
    }
    applications = [replace(item, enabled=states.get(item.slug, True)) for item in bundled]
    for row in database.execute(
        "SELECT slug,name,description,author,version,category,entrypoint,permissions_json,capabilities_json "
        "FROM installed_applications ORDER BY installed_at,slug"
    ):
        if row["slug"] in bundled_slugs:
            continue
        capabilities = json.loads(row["capabilities_json"] or "{}")
        platforms, required_inputs, presentation = _manifest_capabilities(capabilities)
        applications.append(Application(
            slug=row["slug"], name=row["name"], description=row["description"],
            author=row["author"], version=row["version"], category=row["category"],
            entrypoint=row["entrypoint"], builtin=False,
            enabled=states.get(row["slug"], True),
            permissions=tuple(json.loads(row["permissions_json"] or "[]")),
            platforms=platforms, required_inputs=required_inputs,
            preferred_presentation=presentation,
        ))
    return tuple(applications)


def find(slug: str) -> Application | None:
    return next((item for item in catalog() if item.slug == slug), None)


def _load_package(folder: str) -> Application:
    """Load one bundled package from its public manifest."""
    manifest_path = _package_root() / folder / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if type(data.get("package_format")) is not int or data["package_format"] != PACKAGE_FORMAT:
        raise ValueError(f"Unsupported package format: {manifest_path}")
    required = ("slug", "name", "description", "author", "version", "category", "entrypoint")
    if any(not isinstance(data.get(key), str) for key in required):
        raise ValueError(f"Invalid application manifest: {manifest_path}")
    permissions = data.get("permissions", [])
    if not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions):
        raise ValueError(f"Invalid application manifest: {manifest_path}")
    platforms, required_inputs, presentation = _manifest_capabilities(data)
    application = Application(
        slug=data["slug"], name=data["name"], description=data["description"],
        author=data["author"], version=data["version"], category=data["category"],
        entrypoint=f"{folder}/{data['entrypoint']}",
        permissions=tuple(permissions),
        platforms=platforms, required_inputs=required_inputs,
        preferred_presentation=presentation,
    )
    _validate_metadata(application)
    return application


def _safe_archive_name(name: str) -> PurePosixPath:
    if not name or "\\" in name or name.startswith("/") or len(name) > 240:
        raise PackageError(f"Unsafe archive path: {name or '(empty)'}.")
    parts = name.rstrip("/").split("/")
    if len(parts) > 8 or any(
        not PACKAGE_PART_RE.fullmatch(part) or part in {".", ".."}
        or part.endswith(".") or part.split(".")[0].upper() in WINDOWS_DEVICE_NAMES
        for part in parts
    ):
        raise PackageError(f"Unsafe archive path: {name}.")
    return PurePosixPath(*parts)


def _read_archive_file(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size > PACKAGE_MAX_FILE_BYTES:
        raise PackageError(f"Package file exceeds the 1 MiB limit: {info.filename}.")
    try:
        with archive.open(info) as stream:
            content = stream.read(PACKAGE_MAX_FILE_BYTES + 1)
    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile):
        raise PackageError(f"Cannot read package file: {info.filename}.") from None
    if len(content) != info.file_size or len(content) > PACKAGE_MAX_FILE_BYTES:
        raise PackageError(f"Invalid package file size: {info.filename}.")
    return content


def validate_package(package_bytes: bytes) -> tuple[Application, dict[str, bytes]]:
    """Validate an uploaded ZIP fully before anything is written or registered."""
    if not package_bytes:
        raise PackageError("Choose a non-empty ZIP package.")
    if len(package_bytes) > PACKAGE_MAX_BYTES:
        raise PackageError("Package exceeds the 5 MiB upload limit.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(package_bytes))
    except (zipfile.BadZipFile, OSError):
        raise PackageError("The uploaded file is not a valid ZIP archive.") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > PACKAGE_MAX_FILES * 8:
            raise PackageError("Package contains too many ZIP entries.")
        files = [info for info in infos if not info.is_dir()]
        if len(files) > PACKAGE_MAX_FILES:
            raise PackageError(f"Package contains more than {PACKAGE_MAX_FILES} files.")
        if not files:
            raise PackageError("Package does not contain any files.")
        names: dict[str, zipfile.ZipInfo] = {}
        directories: set[str] = set()
        expanded = 0
        for info in infos:
            path = _safe_archive_name(info.filename)
            if info.flag_bits & 0x1:
                raise PackageError("Encrypted package entries are not supported.")
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise PackageError(f"Symbolic links are not supported: {info.filename}.")
            normalized = path.as_posix()
            folded = normalized.casefold()
            if folded in names or folded in directories:
                raise PackageError(f"Package contains a duplicate path: {normalized}.")
            if info.is_dir():
                directories.add(folded)
                continue
            names[folded] = info
            if info.file_size > PACKAGE_MAX_FILE_BYTES:
                raise PackageError(f"Package file exceeds the 1 MiB limit: {normalized}.")
            expanded += info.file_size
        if expanded > PACKAGE_MAX_EXPANDED_BYTES:
            raise PackageError("Expanded package exceeds the 4 MiB limit.")
        for name in names:
            parts = name.split("/")
            if any("/".join(parts[:index]) in names for index in range(1, len(parts))):
                raise PackageError(f"Package file conflicts with a folder: {name}.")
        manifest_info = names.get("manifest.json")
        if manifest_info is None or manifest_info.filename != "manifest.json":
            raise PackageError("Package must contain manifest.json at its root.")
        try:
            manifest = json.loads(_read_archive_file(archive, manifest_info).decode("utf-8"))
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
        try:
            entry_path = _safe_archive_name(entrypoint)
        except PackageError:
            raise PackageError("Entrypoint must be a safe package-relative JavaScript (.js) path.") from None
        if not ENTRYPOINT_RE.fullmatch(entry_path.name):
            raise PackageError("Entrypoint must name a JavaScript (.js) file.")
        entry_info = names.get(entrypoint.casefold())
        if entry_info is None or entry_info.filename != entrypoint:
            raise PackageError(f"Package entrypoint is missing: {entrypoint}.")
        permissions = manifest.get("permissions", [])
        if not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions):
            raise PackageError("Manifest permissions must be an array of supported permission names.")
        platforms, required_inputs, presentation = _manifest_capabilities(manifest, PackageError)
        application = Application(
            slug=manifest["slug"], name=manifest["name"],
            description=manifest["description"], author=manifest["author"],
            version=manifest["version"], category=manifest["category"],
            entrypoint=entrypoint, builtin=False, permissions=tuple(permissions),
            platforms=platforms, required_inputs=required_inputs,
            preferred_presentation=presentation,
        )
        try:
            _validate_metadata(application)
        except ValueError as error:
            raise PackageError(str(error)) from None
        contents = {info.filename: _read_archive_file(archive, info) for info in files}
        try:
            script = contents[entrypoint]
            script.decode("utf-8")
            for name, content in contents.items():
                if name.lower().endswith(".js"):
                    content.decode("utf-8")
        except UnicodeDecodeError:
            raise PackageError("Application script must be valid UTF-8 text.") from None
        if not script.strip():
            raise PackageError("Application script cannot be empty.")
        return application, contents


def install_package(package_bytes: bytes) -> Application:
    """Atomically install and register one validated third-party package."""
    application, files = validate_package(package_bytes)
    if application.slug in _catalog or any(item.slug == application.slug for item in _discover_bundled()):
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
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        os.replace(staging, destination)
        database.execute("BEGIN IMMEDIATE")
        database.execute(
            "INSERT INTO installed_applications(slug,name,description,author,version,category,entrypoint,permissions_json,capabilities_json) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (application.slug, application.name, application.description, application.author,
             application.version, application.category, application.entrypoint,
             json.dumps(application.permissions), json.dumps({
                 "platforms": application.platforms,
                 "required_inputs": application.required_inputs,
                 "preferred_presentation": application.preferred_presentation,
             })),
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
    visible = tuple(item for item in catalog() if item.enabled and item.runnable)
    if g.station and g.station["office_enabled"]:
        visible += OFFICE_PROGRAMS
    preferences = {
        row["application_slug"]: row for row in get_db().execute(
            "SELECT application_slug,pinned_at,last_used_at FROM user_application_preferences WHERE user_id=?",
            (g.user["id"],),
        )
    }
    pinned = tuple(sorted(
        (item for item in visible if preferences.get(item.slug) and preferences[item.slug]["pinned_at"]),
        key=lambda item: item.name.casefold(),
    ))
    recent = tuple(sorted(
        (item for item in visible if preferences.get(item.slug) and preferences[item.slug]["last_used_at"]),
        key=lambda item: preferences[item.slug]["last_used_at"], reverse=True,
    ))[:6]
    selected = request.args.get("selected")
    if selected not in {item.slug for item in visible}:
        selected = visible[0].slug if visible else None
    categories = CATEGORIES + ((OFFICE_CATEGORY,) if g.station and g.station["office_enabled"] else ())
    return render_template("applications/index.html", applications=visible,
                           categories=categories, pinned=pinned, recent=recent, selected=selected)


@bp.post("/<slug>/pin")
@login_required
def toggle_pin(slug):
    validate_csrf()
    application = _office_program(slug) or find(slug)
    if application is None or not application.enabled or not application.runnable:
        abort(404)
    database = get_db()
    existing = database.execute(
        "SELECT pinned_at FROM user_application_preferences WHERE user_id=? AND application_slug=?",
        (g.user["id"], slug),
    ).fetchone()
    if existing and existing["pinned_at"]:
        database.execute(
            "UPDATE user_application_preferences SET pinned_at=NULL WHERE user_id=? AND application_slug=?",
            (g.user["id"], slug),
        )
    else:
        database.execute(
            "INSERT INTO user_application_preferences(user_id,application_slug,pinned_at) "
            "VALUES(?,?,CURRENT_TIMESTAMP) ON CONFLICT(user_id,application_slug) "
            "DO UPDATE SET pinned_at=CURRENT_TIMESTAMP",
            (g.user["id"], slug),
        )
    database.commit()
    return redirect(url_for("applications.index", selected=slug))


@bp.get("/<slug>")
@login_required
def display(slug):
    application = _office_program(slug) or find(slug)
    if application is None or not application.enabled or not application.runnable:
        abort(404)
    database = get_db()
    database.execute(
        "INSERT INTO user_application_preferences(user_id,application_slug,last_used_at) "
        "VALUES(?,?,strftime('%Y-%m-%d %H:%M:%f','now')) "
        "ON CONFLICT(user_id,application_slug) DO UPDATE SET last_used_at=excluded.last_used_at",
        (g.user["id"], slug),
    )
    database.commit()
    if application.launch_endpoint:
        return redirect(url_for(application.launch_endpoint))
    response = make_response(render_template("applications/display.html", application=application))
    response.headers["Cache-Control"] = "no-store"
    return response


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
        return _storage_error("Storage value exceeds the 2 MiB limit.", 413)
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


@bp.route("/<slug>/scores", methods=("GET", "POST"))
@login_required
def scores(slug):
    """Store an authenticated user's best and return this station's top ten."""
    application = find(slug)
    if application is None or not application.enabled or not application.runnable:
        abort(404)
    database = get_db()
    if request.method == "POST":
        token = request.headers.get("X-CSRF-Token", "")
        if not session.get("csrf_token") or not hmac.compare_digest(token, session["csrf_token"]):
            return _storage_error("Invalid or expired request token.", 400)
        data = request.get_json(silent=True)
        score = data.get("score") if isinstance(data, dict) else None
        if type(score) is not int or not 0 <= score <= SCORE_MAX:
            return _storage_error(f"Score must be a whole number from 0 to {SCORE_MAX}.")
        database.execute(
            "INSERT INTO application_scores(application_slug,user_id,best_score) VALUES(?,?,?) "
            "ON CONFLICT(application_slug,user_id) DO UPDATE SET "
            "best_score=MAX(application_scores.best_score,excluded.best_score),"
            "updated_at=CASE WHEN excluded.best_score>application_scores.best_score "
            "THEN CURRENT_TIMESTAMP ELSE application_scores.updated_at END",
            (slug, g.user["id"], score),
        )
        database.commit()
    personal = database.execute(
        "SELECT best_score FROM application_scores WHERE application_slug=? AND user_id=?",
        (slug, g.user["id"]),
    ).fetchone()
    leaders = database.execute(
        "SELECT u.username,s.best_score AS score FROM application_scores s "
        "JOIN users u ON u.id=s.user_id WHERE s.application_slug=? "
        "ORDER BY s.best_score DESC,s.updated_at,u.username COLLATE NOCASE LIMIT 10",
        (slug,),
    ).fetchall()
    return jsonify(
        ok=True,
        personal_best=personal["best_score"] if personal else 0,
        leaderboard=[{"username": row["username"], "score": row["score"]} for row in leaders],
    )


@bp.post("/<slug>/multiplayer")
@login_required
def multiplayer(slug):
    """Relay authenticated multiplayer operations for permissioned applications."""
    application = find(slug)
    if application is None or not application.enabled or not application.runnable:
        abort(404)
    if "multiplayer" not in application.permissions:
        return _storage_error("This application has not declared multiplayer access.", 403)
    token = request.headers.get("X-CSRF-Token", "")
    if not session.get("csrf_token") or not hmac.compare_digest(token, session["csrf_token"]):
        return _storage_error("Invalid or expired request token.", 400)
    try:
        payload = request.get_json(silent=True)
        value = handle_multiplayer(slug, g.user, payload)
    except MultiplayerError as error:
        return _storage_error(str(error), error.status)
    relay = current_app.extensions.get("multiplayer_relay")
    if relay and isinstance(payload, dict):
        operation = payload.get("operation")
        room = payload.get("session_id")
        if operation in {"join", "leave", "disconnect"} and isinstance(room, str):
            relay.publish(room, {"type": "presence", "player_id": f"player:{g.user['id']}",
                                 "connected": operation == "join"})
        if operation == "leave" and isinstance(room, str):
            host = get_db().execute("SELECT host_user_id FROM application_multiplayer_sessions WHERE id=? AND status='open'",
                                    (room,)).fetchone()
            if host:
                relay.publish_host(room, host["host_user_id"])
        if operation == "disconnect" and isinstance(room, str):
            host = get_db().execute("SELECT host_user_id FROM application_multiplayer_sessions WHERE id=? AND status='open'",
                                    (room,)).fetchone()
            if host:
                relay.publish_host(room, host["host_user_id"])
    return jsonify(ok=True, **value)


@bp.get("/<slug>/runtime")
@login_required
def runtime(slug):
    """Return the tiny sandbox document used to execute an application."""
    application = find(slug)
    entrypoint = _entrypoint(application) if application is not None and application.enabled else None
    if application is None or entrypoint is None:
        abort(404)
    worker = Path(__file__).with_name("static") / "application_runtime.js"
    try:
        package_files = _package_files(application)
    except (OSError, PackageError):
        abort(404)
    response = make_response(render_template(
        "applications/runtime.html", application=application,
        application_source=base64.b64encode(entrypoint.read_bytes()).decode("ascii"),
        worker_source=base64.b64encode(worker.read_bytes()).decode("ascii"),
        package_files=package_files,
    ))
    # The worker API and the app source must always come from the same live
    # response. A cached sandbox page can otherwise run a new app against an
    # older API after an update.
    response.headers["Cache-Control"] = "no-store"
    return response


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
