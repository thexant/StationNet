"""Offline EmulatorJS catalogue and sysop-managed cores."""
from pathlib import Path
import base64
import hashlib
from io import BytesIO
import json
import re
import secrets
import tarfile
import urllib.request
import zipfile

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, send_file, send_from_directory, url_for)
from PIL import Image, ImageOps, UnidentifiedImageError

from .auth import login_required
from .db import get_db
from .moderation import audit
from .security import validate_csrf

bp = Blueprint("arcade", __name__, url_prefix="/arcade")

# EmulatorJS 4.2.3 platform names and compatible core names.
PLATFORMS = {
    "nes": ("Nintendo Entertainment System", ("fceumm", "nestopia"), (".nes", ".zip")),
    "snes": ("Super Nintendo", ("snes9x",), (".sfc", ".smc", ".zip")),
    "gb": ("Game Boy", ("gambatte",), (".gb", ".zip")),
    "gbc": ("Game Boy Color", ("gambatte",), (".gbc", ".zip")),
    "gba": ("Game Boy Advance", ("mgba",), (".gba", ".zip")),
    "segaMD": ("Sega Genesis / Mega Drive", ("genesis_plus_gx", "picodrive"), (".md", ".gen", ".bin", ".zip")),
    "segaMS": ("Sega Master System", ("smsplus", "genesis_plus_gx", "picodrive"), (".sms", ".zip")),
    "segaGG": ("Sega Game Gear", ("genesis_plus_gx",), (".gg", ".zip")),
    "atari2600": ("Atari 2600", ("stella2014",), (".a26", ".bin", ".zip")),
    "atari7800": ("Atari 7800", ("prosystem",), (".a78", ".bin", ".zip")),
    "lynx": ("Atari Lynx", ("handy",), (".lnx", ".zip")),
    "pce": ("TurboGrafx-16 / PC Engine", ("mednafen_pce",), (".pce", ".zip")),
    "coleco": ("ColecoVision", ("gearcoleco",), (".col", ".rom", ".zip")),
    "nds": ("Nintendo DS", ("melonds", "desmume", "desmume2015"), (".nds", ".zip")),
    "n64": ("Nintendo 64", ("mupen64plus_next", "parallel_n64"), (".n64", ".z64", ".v64", ".zip")),
}
DEFAULT_CORES = {name: info[1][0] for name, info in PLATFORMS.items()}
ASSETS = Path(__file__).parent / "static" / "emulatorjs" / "data"
MAX_ROM_BYTES = 96 * 1024 * 1024
MAX_COVER_BYTES = 8 * 1024 * 1024
MAX_CORE_BYTES = 32 * 1024 * 1024
CORE_VERSION = "4.2.3"
CORE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@bp.before_request
def require_enabled():
    if not g.station or not g.station["arcade_enabled"]:
        abort(404)


def _root():
    return Path(current_app.config["DATA_DIR"]) / "arcade"


def _game_path(game):
    return _root() / "roms" / game["stored_name"]


def _cover_path(game):
    return _root() / "covers" / game["cover_name"]


def _game(game_id):
    game = get_db().execute(
        "SELECT a.*,u.username FROM arcade_games a JOIN users u ON u.id=a.uploader_id WHERE a.id=?",
        (game_id,),
    ).fetchone()
    if game is None:
        abort(404)
    return game


def _preferred_core(platform):
    row = get_db().execute("SELECT core FROM arcade_core_preferences WHERE platform=?", (platform,)).fetchone()
    return row["core"] if row and row["core"] in PLATFORMS[platform][1] else DEFAULT_CORES[platform]


def _core_path(core, legacy=False):
    name = core + ("-legacy" if legacy else "") + "-wasm.data"
    installed = _root() / "cores" / name
    return installed if installed.is_file() else ASSETS / "cores" / name


def _core_available(core):
    return _core_path(core).is_file() and _core_path(core, True).is_file()


def _quota_remaining(user_id, exclude_game_id=None):
    row = get_db().execute(
        "SELECT COALESCE(u.arcade_quota_mb,s.arcade_default_quota_mb) quota_mb "
        "FROM users u CROSS JOIN station s WHERE u.id=?", (user_id,),
    ).fetchone()
    used = get_db().execute(
        "SELECT COALESCE(SUM(size_bytes),0) FROM arcade_games WHERE uploader_id=? "
        "AND station_post=0 AND id<>?", (user_id, exclude_game_id or -1),
    ).fetchone()[0]
    return row["quota_mb"] * 1024 * 1024 - used


def settings_data():
    """Values used by the Sysop Settings panel, even when Arcade is disabled."""
    games = get_db().execute(
        "SELECT a.id,a.title,a.platform,a.station_post,a.uploader_id,u.username "
        "FROM arcade_games a JOIN users u ON u.id=a.uploader_id ORDER BY a.id DESC"
    ).fetchall()
    return {"arcade_platforms": PLATFORMS, "arcade_games": games,
            "arcade_core_preferences": {p: _preferred_core(p) for p in PLATFORMS},
            "arcade_installed_cores": {c for p in PLATFORMS for c in PLATFORMS[p][1] if _core_available(c)}}


def _metadata():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    platform = request.form.get("platform", "")
    genre = request.form.get("genre", "").strip()
    developer = request.form.get("developer", "").strip()
    publisher = request.form.get("publisher", "").strip()
    players = request.form.get("players", "").strip()
    year_text = request.form.get("release_year", "").strip()
    if not 1 <= len(title) <= 150 or len(description) > 5000 or platform not in PLATFORMS:
        raise ValueError("Enter a title, description, and supported platform.")
    if any(len(s) > n for s, n in ((genre, 80), (developer, 120), (publisher, 120), (players, 40))):
        raise ValueError("One or more metadata fields is too long.")
    if year_text and (not year_text.isdigit() or not 1970 <= int(year_text) <= 2100):
        raise ValueError("Release year must be between 1970 and 2100.")
    return (title, description, platform, genre, int(year_text) if year_text else None,
            developer, publisher, players)


def _read_upload(upload, max_bytes):
    data = upload.stream.read(max_bytes + 1)
    if not data or len(data) > max_bytes:
        raise ValueError("File is empty or exceeds the upload limit.")
    return data


def _validate_rom(upload, platform):
    original_name = Path(upload.filename or "").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in PLATFORMS[platform][2]:
        raise ValueError("ROM file extension does not match the selected platform.")
    data = _read_upload(upload, MAX_ROM_BYTES)
    if suffix == ".zip":
        from io import BytesIO
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                if len(archive.infolist()) > 100 or not any(
                    Path(item.filename).suffix.lower() in PLATFORMS[platform][2][:-1]
                    and not item.is_dir() for item in archive.infolist()
                ):
                    raise ValueError("ZIP must contain a ROM for the selected platform.")
        except zipfile.BadZipFile as exc:
            raise ValueError("Invalid ZIP archive.") from exc
    return original_name, suffix, data


def _save_cover(upload):
    from io import BytesIO
    if not upload or not upload.filename:
        return ""
    data = _read_upload(upload, MAX_COVER_BYTES)
    try:
        with Image.open(BytesIO(data)) as image:
            if image.width * image.height > 20_000_000:
                raise ValueError("Cover image is too large.")
            image = ImageOps.exif_transpose(image)
            image.thumbnail((900, 1200))
            image = image.convert("RGB")
            name = secrets.token_hex(16) + ".jpg"
            path = _root() / "covers" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path, "JPEG", quality=88, optimize=True)
            return name
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Cover must be a valid image.") from exc


@bp.get("/")
def index():
    q = request.args.get("q", "").strip()[:100]
    all_genres = [row[0] for row in get_db().execute(
        "SELECT DISTINCT genre FROM arcade_games WHERE genre<>'' ORDER BY genre COLLATE NOCASE"
    )]
    platforms = list(dict.fromkeys(p for p in request.args.getlist("platform") if p in PLATFORMS))
    genres = list(dict.fromkeys(x for x in request.args.getlist("genre") if x in all_genres))[:100]
    sort = request.args.get("sort", "title")
    order = {"title": "a.title COLLATE NOCASE, a.id",
             "newest": "a.id DESC", "oldest": "a.id ASC",
             "year_desc": "a.release_year DESC, a.title COLLATE NOCASE",
             "year_asc": "a.release_year ASC, a.title COLLATE NOCASE"}.get(sort, "a.title COLLATE NOCASE, a.id")
    conditions, values = [], []
    if q:
        conditions.append("(a.title LIKE ? OR a.description LIKE ? OR a.developer LIKE ?)")
        values.extend([f"%{q}%"] * 3)
    if platforms:
        conditions.append("a.platform IN (" + ",".join("?" for _ in platforms) + ")")
        values.extend(platforms)
    if genres:
        conditions.append("a.genre IN (" + ",".join("?" for _ in genres) + ")")
        values.extend(genres)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    games = get_db().execute(
        "SELECT a.*,u.username FROM arcade_games a JOIN users u ON u.id=a.uploader_id" + where +
        " ORDER BY " + order, values,
    ).fetchall()
    return render_template("arcade/index.html", games=games, platforms=PLATFORMS,
                           genres=all_genres, selected_platforms=platforms,
                           selected_genres=genres, query=q, sort=sort)


@bp.get("/<int:game_id>")
def view(game_id):
    game = _game(game_id)
    core = _preferred_core(game["platform"])
    return render_template("arcade/view.html", game=game, platforms=PLATFORMS,
                           core=core, playable=_core_available(core))


@bp.get("/<int:game_id>/player")
def player(game_id):
    game = _game(game_id)
    core = _preferred_core(game["platform"])
    if not _core_available(core) or not _game_path(game).is_file():
        abort(404)
    return render_template("arcade/player.html", game=game, core=core)


@bp.get("/<int:game_id>/rom")
def rom(game_id):
    game = _game(game_id)
    if not _game_path(game).is_file():
        abort(404)
    response = send_file(_game_path(game), mimetype="application/octet-stream",
                         conditional=True, download_name=game["original_name"])
    response.headers["Content-Disposition"] = "inline"
    return response


@bp.get("/<int:game_id>/cover")
def cover(game_id):
    game = _game(game_id)
    if not game["cover_name"] or not _cover_path(game).is_file():
        abort(404)
    return send_file(_cover_path(game), mimetype="image/jpeg", conditional=True)


@bp.get("/emulatorjs/data/<path:asset>")
def asset(asset):
    # Only packaged player resources and exact allowlisted core files are served.
    if asset.startswith("cores/reports/"):
        name = asset.removeprefix("cores/reports/")
        if name.endswith(".json") and name[:-5] in {c for p in PLATFORMS for c in PLATFORMS[p][1]}:
            installed = _root() / "cores" / "reports" / name
            if installed.is_file():
                return send_file(installed, mimetype="application/json", conditional=True)
            return send_from_directory(ASSETS / "cores" / "reports", name)
        abort(404)
    if asset.startswith("cores/"):
        name = asset.removeprefix("cores/")
        match = re.fullmatch(r"([a-z][a-z0-9_]*)(-legacy)?-wasm\.data", name)
        if match and match.group(1) in {c for p in PLATFORMS for c in PLATFORMS[p][1]}:
            path = _core_path(match.group(1), bool(match.group(2)))
            if path.is_file():
                return send_file(path, mimetype="application/octet-stream", conditional=True)
        abort(404)
    return send_from_directory(ASSETS, asset)


@bp.route("/upload", methods=("GET", "POST"))
@login_required
def upload():
    if g.station["arcade_mode"] != "community":
        abort(404)
    if request.method == "POST":
        validate_csrf()
        try:
            game_id = _create_game(False)
            flash("Game added to Arcade.", "success")
            return redirect(url_for("arcade.view", game_id=game_id))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("arcade/form.html", game=None, platforms=PLATFORMS,
                           station_post=False)


@bp.post("/station-upload")
@login_required
def station_upload():
    if not g.user["is_admin"]:
        abort(403)
    validate_csrf()
    try:
        game_id = _create_game(True)
        flash("Station game added.", "success")
        return redirect(url_for("arcade.view", game_id=game_id))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("station.settings") + "#arcade-service")


def _create_game(station_post):
    fields = _metadata()
    upload = request.files.get("rom")
    if not upload or not upload.filename:
        raise ValueError("Select a ROM file.")
    original, suffix, data = _validate_rom(upload, fields[2])
    if not station_post and len(data) > _quota_remaining(g.user["id"]):
        raise ValueError("Your Arcade upload quota is full.")
    name = secrets.token_hex(16) + suffix
    path = _root() / "roms" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    cover_name = ""
    try:
        path.write_bytes(data)
        cover_name = _save_cover(request.files.get("cover"))
        db = get_db()
        cursor = db.execute(
            "INSERT INTO arcade_games(uploader_id,station_post,title,description,platform,genre,"
            "release_year,developer,publisher,players,original_name,stored_name,size_bytes,cover_name) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (g.user["id"], station_post, *fields, original, name, len(data), cover_name),
        )
        db.commit()
        return cursor.lastrowid
    except Exception:
        path.unlink(missing_ok=True)
        if cover_name:
            (_root() / "covers" / cover_name).unlink(missing_ok=True)
        raise


@bp.route("/<int:game_id>/edit", methods=("GET", "POST"))
@login_required
def edit(game_id):
    game = _game(game_id)
    if game["uploader_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403)
    if request.method == "POST":
        validate_csrf()
        try:
            fields = _metadata()
            rom_upload = request.files.get("rom")
            cover_upload = request.files.get("cover")
            replacement = _validate_rom(rom_upload, fields[2]) if rom_upload and rom_upload.filename else None
            if replacement and not game["station_post"] and len(replacement[2]) > _quota_remaining(game["uploader_id"], game_id):
                raise ValueError("Your Arcade upload quota is full.")
            if not replacement and fields[2] != game["platform"]:
                raise ValueError("Upload a matching ROM when changing platform.")
            new_rom = secrets.token_hex(16) + replacement[1] if replacement else game["stored_name"]
            new_cover = _save_cover(cover_upload) if cover_upload and cover_upload.filename else game["cover_name"]
            if replacement:
                (_root() / "roms" / new_rom).write_bytes(replacement[2])
            if request.form.get("remove_cover") == "on":
                if new_cover != game["cover_name"]:
                    (_root() / "covers" / new_cover).unlink(missing_ok=True)
                new_cover = ""
            db = get_db()
            db.execute(
                "UPDATE arcade_games SET title=?,description=?,platform=?,genre=?,release_year=?,"
                "developer=?,publisher=?,players=?,original_name=?,stored_name=?,size_bytes=?,"
                "cover_name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (*fields, replacement[0] if replacement else game["original_name"],
                 new_rom, len(replacement[2]) if replacement else game["size_bytes"], new_cover, game_id),
            )
            db.commit()
            if replacement:
                _game_path(game).unlink(missing_ok=True)
            if game["cover_name"] and game["cover_name"] != new_cover:
                _cover_path(game).unlink(missing_ok=True)
            flash("Game updated.", "success")
            return redirect(url_for("arcade.view", game_id=game_id))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("arcade/form.html", game=game, platforms=PLATFORMS,
                           station_post=bool(game["station_post"]))


@bp.post("/<int:game_id>/delete")
@login_required
def delete(game_id):
    validate_csrf()
    game = _game(game_id)
    if game["uploader_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403)
    try:
        _game_path(game).unlink(missing_ok=True)
        if game["cover_name"]:
            _cover_path(game).unlink(missing_ok=True)
    except OSError:
        flash("Game files could not be removed.", "error")
        return redirect(url_for("arcade.view", game_id=game_id))
    db = get_db()
    db.execute("DELETE FROM arcade_games WHERE id=?", (game_id,))
    if game["uploader_id"] != g.user["id"]:
        audit(db, g.user["id"], game["uploader_id"], "delete_arcade_game",
              "SYSOP removal", "arcade_game", game_id)
    db.commit()
    flash("Game removed.", "success")
    return redirect(url_for("arcade.index"))


@bp.post("/cores/preference")
@login_required
def set_core():
    if not g.user["is_admin"]:
        abort(403)
    validate_csrf()
    platform, core = request.form.get("platform", ""), request.form.get("core", "")
    if platform not in PLATFORMS or core not in PLATFORMS[platform][1] or not _core_available(core):
        flash("Select an installed compatible core.", "error")
    else:
        db = get_db()
        db.execute("INSERT INTO arcade_core_preferences(platform,core) VALUES(?,?) "
                   "ON CONFLICT(platform) DO UPDATE SET core=excluded.core", (platform, core))
        db.commit()
        flash("Arcade core preference saved.", "success")
    return redirect(url_for("station.settings") + "#arcade-service")


@bp.post("/quota/<int:user_id>")
@login_required
def set_user_quota(user_id):
    if not g.user["is_admin"]:
        abort(403)
    validate_csrf()
    raw = request.form.get("arcade_quota_mb", "").strip()
    quota = int(raw) if raw.isdigit() else None
    if raw and (quota is None or not 1 <= quota <= 10240):
        flash("User Arcade quota must be blank or between 1 and 10240 MB.", "error")
    else:
        db = get_db()
        if not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            abort(404)
        db.execute("UPDATE users SET arcade_quota_mb=? WHERE id=?", (quota, user_id))
        db.commit()
        flash("User Arcade quota saved.", "success")
    return redirect(url_for("station.settings") + "#arcade-service")


@bp.post("/cores/install")
@login_required
def install_core():
    if not g.user["is_admin"]:
        abort(403)
    validate_csrf()
    core = request.form.get("core", "")
    if not CORE_RE.fullmatch(core) or core not in {c for p in PLATFORMS for c in PLATFORMS[p][1]}:
        abort(400)
    folder = _root() / "cores"
    folder.mkdir(parents=True, exist_ok=True)
    pending = []
    try:
        metadata_url = f"https://registry.npmjs.org/@emulatorjs%2fcore-{core}/{CORE_VERSION}"
        with urllib.request.urlopen(metadata_url, timeout=30) as source:
            metadata = json.load(source)
        package_url = metadata["dist"]["tarball"]
        expected_url = (f"https://registry.npmjs.org/@emulatorjs/core-{core}/-/"
                        f"core-{core}-{CORE_VERSION}.tgz")
        if package_url != expected_url:
            raise ValueError("Official package URL did not match the expected release.")
        with urllib.request.urlopen(package_url, timeout=30) as source:
            package = source.read(MAX_CORE_BYTES + 1)
        if len(package) > MAX_CORE_BYTES:
            raise ValueError("Official core package is too large.")
        integrity = metadata["dist"].get("integrity", "")
        if not integrity.startswith("sha512-") or base64.b64encode(hashlib.sha512(package).digest()).decode() != integrity[7:]:
            raise ValueError("Official core package failed integrity verification.")
        with tarfile.open(fileobj=BytesIO(package), mode="r:gz") as archive:
            for legacy in (False, True):
                name = core + ("-legacy" if legacy else "") + "-wasm.data"
                member = archive.getmember("package/" + name)
                if not member.isfile() or not 1024 <= member.size <= MAX_CORE_BYTES:
                    raise ValueError("Official core package is incomplete.")
                data = archive.extractfile(member).read()
                temporary = folder / (name + "." + secrets.token_hex(6) + ".tmp")
                temporary.write_bytes(data)
                pending.append((temporary, folder / name))
            report_name = core + ".json"
            report = archive.getmember("package/reports/" + report_name)
            if not report.isfile() or not 2 <= report.size <= 10000:
                raise ValueError("Official core report is missing.")
            report_folder = folder / "reports"
            report_folder.mkdir(parents=True, exist_ok=True)
            temporary = report_folder / (report_name + "." + secrets.token_hex(6) + ".tmp")
            temporary.write_bytes(archive.extractfile(report).read())
            pending.append((temporary, report_folder / report_name))
        for temporary, target in pending:
            temporary.replace(target)
        flash(f"{core} installed locally for offline use.", "success")
    except (OSError, ValueError, KeyError, tarfile.TarError, json.JSONDecodeError) as exc:
        flash(f"Core download failed: {exc}", "error")
    finally:
        for temporary, _ in pending:
            temporary.unlink(missing_ok=True)
    return redirect(url_for("station.settings") + "#arcade-service")
