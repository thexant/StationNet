"""StationNet games/applications catalog and isolated script delivery."""
import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path

from flask import Blueprint, abort, make_response, render_template

from .auth import login_required

bp = Blueprint("applications", __name__, url_prefix="/games-apps")

GAMES = "Games"
APPS = "Apps"
CATEGORIES = (GAMES, APPS)


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
        slug="orbit-command",
        name="Orbit Command [Placeholder]",
        description="A future turn-based station logistics game.",
        author="StationNet Core",
        version="0.0.1",
        category=GAMES,
    ),
    _load_package("number_guess"),
    Application(
        slug="crew-roster",
        name="Crew Roster [Placeholder]",
        description="A future utility for organizing station duty rotations.",
        author="StationNet Core",
        version="0.0.1",
        category=APPS,
    ),
    Application(
        slug="cargo-ledger",
        name="Cargo Ledger [Placeholder]",
        description="A future utility for tracking station stores and supplies.",
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
