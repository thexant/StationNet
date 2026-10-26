"""StationNet application factory."""
import os
import secrets
from datetime import timedelta
from pathlib import Path
from flask import Flask, g, jsonify, redirect, render_template, request, url_for
from . import announcements, applications, archive, auth, board, broadcast, chat, db, downloads, dungeons, events, feeds, imageboard, kiwix, mail, moderation, mud, mud_worlds, office, programs, pwa, radio, station, themes, users, videos
from .ansi import station_banner
from .realtime import DirectChatStore
from .radio import RadioStore
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
        APPLICATIONS_DIR=str(data_dir / "applications"),
        KIWIX_DATA_DIR=str(data_dir / "kiwix_data"),
        KIWIX_SERVE_EXECUTABLE=os.environ.get("STATIONNET_KIWIX_SERVE"),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=3650),
        SESSION_COOKIE_SECURE=os.environ.get("STATIONNET_HTTPS") == "1", MAX_CONTENT_LENGTH=128 * 1024 * 1024,
        DISTRIBUTION_DIR=os.environ.get("STATIONNET_DISTRIBUTION_DIR", str(Path(__file__).resolve().parents[1] / "dist")),
        ANDROID_APK_DIR=os.environ.get("STATIONNET_ANDROID_APK_DIR", str(Path(__file__).resolve().parents[1] / "android" / "app" / "build" / "outputs" / "apk" / "release")),
    )
    if test_config:
        app.config.update(test_config)
    kiwix.init_app(app)
    db.init_app(app)
    themes.init_app(app)
    dungeons.init_app(app)
    mud_worlds.init_app(app)
    app.extensions["direct_chat"] = DirectChatStore()
    app.extensions["radio"] = RadioStore()
    app.extensions["hosted_broadcasts"] = broadcast.HostedBroadcastStore(
        app.extensions["radio"], app
    )
    app.register_blueprint(auth.bp)
    app.register_blueprint(station.bp)
    app.register_blueprint(pwa.bp)
    app.register_blueprint(downloads.bp)
    app.register_blueprint(board.bp)
    app.register_blueprint(imageboard.bp)
    app.register_blueprint(chat.bp)
    app.register_blueprint(moderation.bp)
    app.register_blueprint(moderation.report_bp)
    app.register_blueprint(mail.bp)
    app.register_blueprint(archive.bp)
    app.register_blueprint(videos.bp)
    app.register_blueprint(users.bp)
    app.register_blueprint(feeds.bp)
    app.register_blueprint(announcements.bp)
    app.register_blueprint(events.bp)
    app.register_blueprint(applications.bp)
    app.register_blueprint(applications.admin_bp)
    app.register_blueprint(office.bp)
    app.register_blueprint(mud.bp)
    app.register_blueprint(kiwix.bp)
    app.register_blueprint(radio.bp)
    app.register_blueprint(broadcast.bp)
    app.register_blueprint(programs.bp)
    with app.app_context():
        db.init_db()
    broadcast.restore_resumable_broadcasts(app)
    app.extensions["broadcast_coordinator"] = programs.BroadcastCoordinator(
        app, app.extensions["hosted_broadcasts"]
    )

    @app.before_request
    def context():
        auth.load_logged_in_user()
        g.station = db.get_station()
        g.station_icon_exists = (data_dir / "station_icon.png").is_file()
        g.site_theme, g.masthead_theme = themes.effective(g.station, g.user)
        g.theme_background = themes.palette(g.site_theme)["black"]
        if g.station is not None and g.station["mud_procedural_enabled"]:
            # Lazy midnight rotation: the first request after local midnight
            # materializes the immutable station-wide design. The persisted
            # day key makes this restart-safe and idempotent across workers.
            mud.ensure_daily_dungeon(db.get_db())
            db.get_db().commit()
        g.mail_latest_id = 0
        if g.user is not None:
            g.mail_latest_id = db.get_db().execute(
                "SELECT COALESCE(MAX(id),0) FROM mail WHERE recipient_id=? "
                "AND (? OR subject NOT LIKE 'FEED:%')",
                (g.user["id"], g.station["user_feeds_enabled"] if g.station else 0),
            ).fetchone()[0]
        g.mud_can_build = bool(g.user and mud.can_build(db.get_db(), g.user))
        try:
            g.masthead_font_size = min(
                200, max(5, int(g.station["header_font_size"]))
            ) if g.station else 100
        except (TypeError, ValueError):
            g.masthead_font_size = 100
        g.station_banner = station_banner(
            (g.station["header_text"].strip() or g.station["name"])
            if g.station else "STATIONNET",
            g.station["header_font"] if g.station else "small",
            g.masthead_font_size,
        )
        ensure_csrf_token()
        endpoint = request.endpoint or ""
        if g.user is not None and endpoint not in {"auth.logout", "static"}:
            restriction = moderation.active_restriction(g.user["id"], ("ban", "suspension"))
            if restriction is not None:
                message = ("Account banned" if restriction["kind"] == "ban" else "Account suspended")
                message += f": {restriction['reason']}"
                if restriction["expires_at"]:
                    message += f" (until {restriction['expires_at']} UTC)"
                if request.is_json or request.accept_mimetypes.best == "application/json" or request.path.startswith(("/radio/", "/broadcast/", "/applications/")):
                    return jsonify(error=message), 403
                return render_template("moderation/restricted.html", message=message), 403
        if g.user is not None:
            auth.touch_presence()
        if (g.station is None and endpoint not in {"auth.setup", "static", "pwa.manifest", "pwa.icon", "pwa.service_worker", "downloads.index",
                                                   "downloads.download", "downloads.info"}
                and not endpoint.startswith("static")):
            return redirect(url_for("auth.setup"))
        if g.station is not None and endpoint == "auth.setup":
            return redirect(url_for("station.index"))
        if (g.station is not None and g.user is None and not g.station["guest_access"]
                and (endpoint == "station.terminal"
                     or request.blueprint in {"board", "announcements", "kiwix", "videos"})):
            return redirect(url_for("auth.login", next=request.path))

    @app.after_request
    def headers(response):
        response.headers.update({
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer" if request.endpoint == "auth.register" else "same-origin",
            "Permissions-Policy": "microphone=(self)",
            "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self' 'wasm-unsafe-eval'; img-src 'self' data: blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        })
        if request.endpoint in {"applications.runtime", "applications.source"}:
            # The iframe has an opaque origin (no allow-same-origin). Its CSP
            # also closes every network channel available to application code.
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self' blob:; connect-src 'none'; "
                "img-src 'none'; media-src 'none'; font-src 'none'; "
                "style-src 'none'; object-src 'none'; child-src blob:; "
                "frame-src 'none'; worker-src blob:; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'self'"
            )
        if request.endpoint in {"kiwix.content", "kiwix.search_source"}:
            if request.args.get("stationnet-interactive") == "1":
                # JavaScript-first ZIMs (notably official openZIM maps) need a
                # same-origin module graph: their startup probe fetches bundled
                # config before MapLibre initializes. These archives are
                # installed by the station operator, never uploaded by users.
                response.headers["X-Frame-Options"] = "SAMEORIGIN"
                response.headers["Content-Security-Policy"] = (
                    "sandbox allow-scripts allow-same-origin allow-forms allow-pointer-lock allow-downloads; "
                    "default-src 'self' data: blob:; "
                    "script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' blob:; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                    "media-src 'self' data: blob:; font-src 'self' data:; "
                    "connect-src 'self' data: blob:; worker-src 'self' blob:; "
                    "child-src 'self' blob:; object-src 'none'; base-uri 'self'; "
                    "form-action 'self'; frame-ancestors 'self'"
                )
            else:
                response.headers["Content-Security-Policy"] = (
                    "default-src 'none'; script-src 'none'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data: blob:; media-src 'self'; font-src 'self' data:; "
                    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                )
            # Retain support for browsers which issue sandboxed subresource
            # requests with an opaque origin during frame initialization.
            if request.headers.get("Origin") == "null":
                response.headers["Access-Control-Allow-Origin"] = "null"
                response.headers["Vary"] = "Origin"
        if g.get("user") is not None and request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response
    return app

