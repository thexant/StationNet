"""StationNet games and applications directory.

The catalog is deliberately independent from the views and templates. Future
packaged applications can register the same metadata without changing the
directory UI; execution and package management are intentionally out of scope.
"""
from dataclasses import dataclass

from flask import Blueprint, abort, render_template

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


_catalog: dict[str, Application] = {}


def register(application: Application) -> None:
    """Add application metadata to the station-wide catalog."""
    if application.category not in CATEGORIES:
        raise ValueError(f"Unknown application category: {application.category}")
    if not application.slug or application.slug in _catalog:
        raise ValueError(f"Invalid or duplicate application slug: {application.slug}")
    _catalog[application.slug] = application


def catalog() -> tuple[Application, ...]:
    """Return registered applications in stable registration order."""
    return tuple(_catalog.values())


def find(slug: str) -> Application | None:
    return _catalog.get(slug)


for _application in (
    Application(
        slug="orbit-command",
        name="Orbit Command [Placeholder]",
        description="A future turn-based station logistics game.",
        author="StationNet Core",
        version="0.0.1",
        category=GAMES,
    ),
    Application(
        slug="signal-runner",
        name="Signal Runner [Placeholder]",
        description="A future signal-routing arcade challenge.",
        author="StationNet Core",
        version="0.0.1",
        category=GAMES,
    ),
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
