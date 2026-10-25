"""Placeholder for the optional StationNet Office module."""
from flask import Blueprint, abort, g, render_template

from .auth import login_required

bp = Blueprint("office", __name__, url_prefix="/office")


@bp.before_request
def require_enabled():
    if not g.station or not g.station["office_enabled"]:
        abort(404)


@bp.get("/")
@bp.get("")
@login_required
def index():
    return render_template("office/index.html")
