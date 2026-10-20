"""StationNet application factory."""
import os
import secrets
from pathlib import Path
from flask import Flask, g, redirect, request, url_for
from . import auth, board, db, station
from .ansi import station_banner
from .security import ensure_csrf_token

def _secret(data_dir):
    path = data_dir / "secret.key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    value = secrets.token_hex(32)
    path.write_text(value, encoding="utf-8")
    return value

def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    data_dir = Path((test_config or {}).get("DATA_DIR") or os.environ.get("STATIONNET_DATA", Path.cwd() / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    app.config.from_mapping(
        DATABASE=str(data_dir / "station.db"), DATA_DIR=str(data_dir), SECRET_KEY=_secret(data_dir),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("STATIONNET_HTTPS") == "1", MAX_CONTENT_LENGTH=256 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    db.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(station.bp)
    app.register_blueprint(board.bp)
    with app.app_context():
        db.init_db()

    @app.before_request
    def context():
        auth.load_logged_in_user()
        g.station = db.get_station()
        g.station_banner = station_banner(g.station["name"] if g.station else "STATIONNET")
        ensure_csrf_token()
        endpoint = request.endpoint or ""
        if g.station is None and endpoint not in {"auth.setup", "static"} and not endpoint.startswith("static"):
            return redirect(url_for("auth.setup"))
        if g.station is not None and endpoint == "auth.setup":
            return redirect(url_for("station.index"))

    @app.after_request
    def headers(response):
        response.headers.update({
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        })
        return response
    return app

