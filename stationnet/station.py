"""Welcome, workspace, and SYSOP settings."""
import json
import os
import re
import secrets
import sqlite3
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from .auth import admin_required, invitation_digest
from .ansi import available_header_fonts, is_available_header_font, station_banner
from .db import get_db
from .instance import sync_name
from . import arcade, character_creation, dungeons, header_taglines, mud_abilities, mud_effects, mud_scope, mud_skills, mud_worlds, tv
from . import themes
from .markdown import render_markdown
from .midi import InvalidMidi, MAX_MIDI_BYTES, inspect_midi
from .radio import (can_host_broadcast, effective_access, format_frequency,
                    parse_frequency, reconcile_radio_permissions)
from .security import validate_csrf
bp = Blueprint("station", __name__)

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
STATION_ICON_MAX_BYTES = 10 * 1024 * 1024
STATION_ICON_MAX_PIXELS = 20_000_000
STATION_ICON_SIZE = (128, 128)


def _station_icon_path():
    return Path(current_app.config["DATA_DIR"]) / "station_icon.png"


def _station_midi_path():
    return Path(current_app.config["DATA_DIR"]) / "station_theme.mid"


@bp.get("/station-theme.mid")
def station_midi_theme():
    if not g.user or not g.station or not g.station["play_station_midi"]:
        abort(404)
    path = _station_midi_path()
    if not path.is_file():
        abort(404)
    response = send_file(path, mimetype="audio/midi")
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/sysop/settings/midi")
@admin_required
def upload_station_midi():
    validate_csrf()
    volume_text = request.form.get("station_midi_volume", str(g.station["station_midi_volume"]))
    if not volume_text.isdecimal() or not 0 <= int(volume_text) <= 100:
        flash("Station MIDI volume must be a whole number from 0 to 100.", "error")
        return redirect(url_for("station.settings") + "#station-parameters")
    volume = int(volume_text)
    uploaded = request.files.get("station_midi")
    path = _station_midi_path()
    if uploaded is not None and uploaded.filename:
        payload = uploaded.read(MAX_MIDI_BYTES + 1)
        try:
            inspect_midi(payload, allow_extended_velocity=True)
        except InvalidMidi as error:
            flash(str(error), "error")
            return redirect(url_for("station.settings") + "#station-parameters")
        temporary = path.with_name(f"station_theme_{secrets.token_hex(8)}.mid")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    if not path.is_file() and request.form.get("play_station_midi") == "on":
        flash("Upload a Station MIDI Theme before enabling playback.", "error")
        return redirect(url_for("station.settings") + "#station-parameters")
    database = get_db()
    database.execute(
        "UPDATE station SET play_station_midi=?,station_midi_volume=? WHERE id=1",
        (int(path.is_file() and request.form.get("play_station_midi") == "on"), volume),
    )
    database.commit()
    flash("Station MIDI Theme settings saved.", "success")
    return redirect(url_for("station.settings") + "#station-parameters")


@bp.post("/sysop/settings/midi/clear")
@admin_required
def clear_station_midi():
    validate_csrf()
    get_db().execute("UPDATE station SET play_station_midi=0 WHERE id=1")
    get_db().commit()
    _station_midi_path().unlink(missing_ok=True)
    flash("Station MIDI Theme cleared.", "success")
    return redirect(url_for("station.settings") + "#station-parameters")


@bp.post("/station-theme/preference")
def station_midi_preference():
    if not g.user:
        abort(401)
    validate_csrf()
    if not g.station or not g.station["play_station_midi"] or not _station_midi_path().is_file():
        abort(404)
    enabled = request.form.get("enabled")
    if enabled not in {"true", "false"}:
        return jsonify(error="Expected a boolean enabled value."), 400
    get_db().execute(
        "UPDATE users SET station_midi_enabled=? WHERE id=?",
        (int(enabled == "true"), g.user["id"]),
    )
    get_db().commit()
    return jsonify(enabled=enabled == "true")


@bp.get("/station-icon.png")
def station_icon():
    path = _station_icon_path()
    if not path.is_file():
        abort(404)
    response = send_file(path, mimetype="image/png")
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/sysop/settings/icon")
@admin_required
def upload_station_icon():
    validate_csrf()
    uploaded = request.files.get("station_icon")
    display_station_icon = request.form.get("display_station_icon") == "on"
    use_station_icon_for_web_app = request.form.get("use_station_icon_for_web_app") == "on"
    has_upload = uploaded is not None and bool(uploaded.filename)
    if not has_upload:
        database = get_db()
        database.execute(
            "UPDATE station SET display_station_icon=?,use_station_icon_for_web_app=? WHERE id=1",
            (display_station_icon, use_station_icon_for_web_app),
        )
        database.commit()
        if display_station_icon and not _station_icon_path().is_file():
            flash("Station icon display enabled. Upload an icon to show it in the masthead.", "success")
        else:
            flash("Station icon settings saved.", "success")
        return redirect(url_for("station.settings") + "#station-parameters")
    payload = uploaded.read(STATION_ICON_MAX_BYTES + 1)
    if not payload or len(payload) > STATION_ICON_MAX_BYTES:
        flash("Station icon must be between 1 byte and 10 MB.", "error")
        return redirect(url_for("station.settings") + "#station-parameters")
    try:
        with Image.open(BytesIO(payload)) as source:
            if source.format not in {"PNG", "JPEG", "GIF", "WEBP"}:
                raise ValueError("Select a PNG, JPEG, GIF, or WebP image.")
            width, height = source.size
            if width < 1 or height < 1 or width * height > STATION_ICON_MAX_PIXELS:
                raise ValueError("Station icon dimensions are too large.")
            source.seek(0)
            image = ImageOps.exif_transpose(source)
            has_alpha = image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            )
            image = image.convert("RGBA" if has_alpha else "RGB")
            image = ImageOps.fit(image, STATION_ICON_SIZE, method=Image.Resampling.LANCZOS)
            clean = Image.new(image.mode, STATION_ICON_SIZE)
            clean.paste(image)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("station.settings") + "#station-parameters")
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError):
        flash("Select a valid PNG, JPEG, GIF, or WebP image.", "error")
        return redirect(url_for("station.settings") + "#station-parameters")
    path = _station_icon_path()
    temporary = path.with_name(f"station_icon_{secrets.token_hex(8)}.png")
    try:
        clean.save(temporary, format="PNG", optimize=True)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    database = get_db()
    database.execute(
        "UPDATE station SET display_station_icon=?,use_station_icon_for_web_app=? WHERE id=1",
        (display_station_icon, use_station_icon_for_web_app),
    )
    database.commit()
    flash("Station icon and web-app settings saved.", "success")
    return redirect(url_for("station.settings") + "#station-parameters")


@bp.post("/sysop/settings/theme")
@admin_required
def import_theme():
    validate_csrf()
    uploaded = request.files.get("theme_file")
    if uploaded is None or not uploaded.filename:
        flash("Choose a JSON theme file to import.", "error")
        return redirect(url_for("station.settings") + "#station-parameters")
    payload = uploaded.read(themes.MAX_THEME_BYTES + 1)
    if not payload or len(payload) > themes.MAX_THEME_BYTES:
        flash("Theme file must be between 1 byte and 16 KB.", "error")
        return redirect(url_for("station.settings") + "#station-parameters")
    try:
        document = themes.parse(payload.decode("utf-8"))
    except (UnicodeError, themes.ThemeError) as error:
        flash(str(error) if isinstance(error, themes.ThemeError) else "Theme must be valid UTF-8 JSON.", "error")
        return redirect(url_for("station.settings") + "#station-parameters")
    if document["id"] in themes.custom_themes():
        flash("A custom theme with that ID is already installed.", "error")
        return redirect(url_for("station.settings") + "#station-parameters")
    path = themes.directory() / f"{document['id']}.json"
    try:
        with path.open("x", encoding="utf-8") as target:
            json.dump(document, target, indent=2)
            target.write("\n")
    except FileExistsError:
        flash("A theme file with that ID already exists.", "error")
        return redirect(url_for("station.settings") + "#station-parameters")
    flash(f"Imported theme {document['name']} by {document['author']}.", "success")
    return redirect(url_for("station.settings") + "#station-parameters")


@bp.post("/sysop/settings/theme/remove")
@admin_required
def remove_theme():
    validate_csrf()
    destination = url_for("station.settings") + "#station-parameters"
    theme_id = request.form.get("theme_id", "")
    if request.form.get("confirm_remove") != "on":
        flash("Check the confirmation box before removing a theme.", "error")
        return redirect(destination)
    installed = themes.custom_themes()
    if theme_id not in installed:
        flash("Select an installed custom theme to remove.", "error")
        return redirect(destination)
    path = themes.directory() / f"{theme_id}.json"
    if path.is_symlink():
        flash("Cannot remove a linked theme file.", "error")
        return redirect(destination)
    try:
        path.unlink()
    except OSError:
        flash("Could not remove the theme file.", "error")
        return redirect(destination)
    choice = f"custom-{theme_id}"
    database = get_db()
    database.execute("UPDATE station SET site_theme='cyan' WHERE site_theme=?", (choice,))
    database.execute("UPDATE station SET masthead_theme='cyan' WHERE masthead_theme=?", (choice,))
    database.execute("UPDATE users SET site_theme=NULL WHERE site_theme=?", (choice,))
    database.execute("UPDATE users SET masthead_theme=NULL WHERE masthead_theme=?", (choice,))
    database.commit()
    flash(f"Removed theme {installed[theme_id]['name']}.", "success")
    return redirect(destination)


def _header_color(value):
    """Return a safe configured CSS color, or the theme's normal cyan."""
    return value if isinstance(value, str) and HEX_COLOR_RE.fullmatch(value) else ""


@bp.get("/masthead.css")
def masthead_css():
    """Serve the effective palettes and station-specific scale without inline styles."""
    size = g.masthead_font_size
    color = _header_color(g.station["header_color"] if g.station else "")
    site_theme, masthead_theme = themes.effective(g.station, g.user)
    if g.user and g.station["personal_themes_enabled"] and g.user["masthead_theme"]:
        color = ""
    stylesheet = (
        themes.css(site_theme, masthead_theme, color)
        + "\n"
        ".ansi-logo { font-size: clamp("
        f"{size * 0.004:.3f}rem, {size * 0.0155:.3f}vw, "
        f"{size * 0.0088:.3f}rem); }}"
    )
    response = Response(stylesheet, content_type="text/css")
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/sysop/banner-preview")
@admin_required
def banner_preview():
    """Render a single proposed masthead using the exact production renderer."""
    text = request.args.get("text", "").strip()
    font = request.args.get("font", "small")
    size = request.args.get("size", 100, type=int)
    if len(text) > 80:
        return jsonify(error="Banner text must be 80 characters or fewer."), 400
    if not is_available_header_font(font):
        return jsonify(error="That FIGlet font is not available."), 400
    if size is None or not 5 <= size <= 200:
        return jsonify(error="Header size must be between 5% and 200%."), 400
    return jsonify(banner=station_banner(text or g.station["name"], font, size))

@bp.get("/")
def index():
    default_headings = {
        "open": "STATION INFORMATION",
        "private": "RESTRICTED ACCESS NODE",
        "disabled": "CLOSED ACCESS NODE",
    }
    heading = (
        g.station["landing_heading"].strip()
        or default_headings.get(g.station["registration_mode"], "STATION INFORMATION")
    )
    body = g.station["landing_message"].strip()
    if not body:
        if g.station["registration_mode"] == "private":
            body = (
                f"**INVITATION ONLY.** Welcome to **{g.station['name']}** — "
                f"[{g.station['callsign']}]. Invited operators may enroll; existing users can sign in."
            )
        elif g.station["registration_mode"] == "disabled":
            body = (
                f"Welcome to **{g.station['name']}** — [{g.station['callsign']}]. "
                "Identify yourself to access station services. "
                "New enrollment is closed; existing users can still sign in."
            )
        elif g.station["guest_access"]:
            body = (
                f"Welcome to **{g.station['name']}** — [{g.station['callsign']}]. "
                "Explore the station as a visitor, or identify yourself to participate."
            )
        else:
            body = (
                f"Welcome to **{g.station['name']}** — [{g.station['callsign']}]. "
                "Identify yourself to access station services."
            )
    return render_template(
        "welcome.html", landing_heading=heading,
        landing_body_html=render_markdown(body),
    )

@bp.get("/terminal")
def terminal():
    activity = None
    if g.user:
        database = get_db()
        latest_bulletin = database.execute(
            "SELECT p.id,p.title,p.created_at,u.username,u.callsign "
            "FROM posts p JOIN users u ON u.id=p.author_id "
            "WHERE p.parent_id IS NULL ORDER BY p.created_at DESC,p.id DESC LIMIT 1"
        ).fetchone()
        latest_reply = database.execute(
            "SELECT r.id,r.body,r.created_at,u.username,u.callsign,"
            "parent.id AS bulletin_id,parent.title AS bulletin_title "
            "FROM posts r JOIN posts parent ON parent.id=r.parent_id "
            "JOIN users u ON u.id=r.author_id "
            "ORDER BY r.created_at DESC,r.id DESC LIMIT 1"
        ).fetchone()
        latest_chat = database.execute(
            "SELECT id,created_at FROM station_chat_messages "
            "ORDER BY created_at DESC,id DESC LIMIT 1"
        ).fetchone()
        newest_file = database.execute(
            "SELECT id,display_name,created_at FROM archive_files "
            "ORDER BY created_at DESC,id DESC LIMIT 1"
        ).fetchone()
        newest_member = database.execute(
            "SELECT id,username,created_at FROM users "
            "ORDER BY created_at DESC,id DESC LIMIT 1"
        ).fetchone()
        unread_mail = database.execute(
            "SELECT COUNT(*) FROM mail WHERE recipient_id=? AND recipient_deleted=0 AND is_read=0",
            (g.user["id"],),
        ).fetchone()[0]

        assignments = {
            row["user_id"]: row for row in database.execute(
                "SELECT a.user_id,a.frequency_khz,a.broadcast_name,a.broadcast_message,"
                "u.callsign,u.username "
                "FROM radio_broadcast_assignments a JOIN users u ON u.id=a.user_id"
            )
        }
        broadcasts = []
        live_broadcasts = current_app.extensions["hosted_broadcasts"].all_public()
        for user_id, session_info in live_broadcasts.items():
            assignment = assignments.get(user_id)
            if (session_info and session_info["status"] == "playing" and assignment
                    and assignment["frequency_khz"] == session_info["frequency_khz"]):
                broadcasts.append({
                    **session_info,
                    "name": assignment["broadcast_name"] or
                            f"{assignment['username']} - [{assignment['callsign']}]",
                    "username": assignment["username"],
                    "message": assignment["broadcast_message"],
                })
        automated = {f"automated:{row['id']}": row for row in database.execute(
            "SELECT id,name,message,frequency_khz FROM radio_automated_broadcasts"
        )}
        for key, definition in automated.items():
            session_info = live_broadcasts.get(key)
            if session_info and session_info["status"] == "playing":
                broadcasts.append({
                    **session_info, "name": definition["name"], "username": None,
                    "message": definition["message"],
                })
        for definition in database.execute(
                "SELECT id,name,message FROM radio_external_broadcasts"):
            session_info = live_broadcasts.get(f"external:{definition['id']}")
            if session_info and session_info["status"] == "playing":
                broadcasts.append({**session_info,
                                   "name": definition["name"] or session_info["station_name"],
                                   "username": None,
                                   "message": definition["message"] or session_info["description"]})
        program_rules = {f"rule:{row['id']}": row for row in database.execute(
            "SELECT id,name,message,frequency_khz FROM radio_automation_rules"
        )}
        for key, definition in program_rules.items():
            session_info = live_broadcasts.get(key)
            if session_info and session_info["status"] == "playing":
                broadcasts.append({
                    **session_info, "name": definition["name"], "username": None,
                    "message": definition["message"],
                })
        broadcasts.sort(key=lambda item: (item["frequency_khz"], item["name"].casefold()))
        activity = {
            "bulletin": latest_bulletin,
            "reply": latest_reply,
            "chat": latest_chat,
            "file": newest_file,
            "member": newest_member,
            "unread_mail": unread_mail,
            "broadcasts": broadcasts,
        }
    return render_template("terminal.html", activity=activity)

@bp.post("/sysop/settings/access")
@admin_required
def access_settings():
    """Save public access controls independently of unrelated station fields."""
    validate_csrf()
    database = get_db()
    mode = request.form.get("registration_mode")
    if mode is None:
        # Compatibility with the former checkbox form and older clients.
        mode = "open" if request.form.get("registration_open") == "on" else "disabled"
    if mode not in {"open", "private", "disabled"}:
        flash("Select a valid registration mode.", "error")
        return redirect(url_for("station.settings") + "#access-policy")
    database.execute(
        "UPDATE station SET registration_mode=?,registration_open=?,guest_access=? WHERE id=1",
        (mode, mode == "open", request.form.get("guest_access") == "on"),
    )
    database.commit()
    flash("Access policy updated.", "success")
    return redirect(url_for("station.settings") + "#access-policy")


@bp.post("/sysop/invitations/generate")
@admin_required
def generate_invitation():
    validate_csrf()
    code = "SN-" + secrets.token_urlsafe(24)
    database = get_db()
    cursor = database.execute(
        "INSERT INTO registration_invites(token_hash,code_hint,created_by) VALUES(?,?,?)",
        (invitation_digest(code), code[-8:], g.user["id"]),
    )
    database.commit()
    # Keep the secret in the URL fragment: browsers do not send fragments in
    # HTTP requests or referrer headers. station.js moves it into the field.
    invite_url = url_for("auth.register", _external=True) + "#invite=" + code
    return render_template(
        "invite_created.html", invitation_id=cursor.lastrowid,
        invite_code=code, invite_url=invite_url,
    )


@bp.post("/sysop/invitations/<int:invitation_id>/revoke")
@admin_required
def revoke_invitation(invitation_id):
    validate_csrf()
    database = get_db()
    changed = database.execute(
        "UPDATE registration_invites SET revoked_at=CURRENT_TIMESTAMP "
        "WHERE id=? AND revoked_at IS NULL AND used_at IS NULL",
        (invitation_id,),
    )
    database.commit()
    flash(
        "Invitation revoked." if changed.rowcount else "Invitation was already unavailable.",
        "success" if changed.rowcount else "warning",
    )
    return redirect(url_for("station.settings") + "#invitations")


@bp.post("/sysop/settings/mud")
@admin_required
def mud_settings():
    """Save station-wide MUD settings: wilderness travel and the protected portal presentation.

    Which worlds players can enter is managed per world in the Worlds list.
    """
    validate_csrf()
    database = get_db()
    current_portal = database.execute(
        "SELECT name,description FROM mud_portal_settings WHERE id=1"
    ).fetchone()
    current_travel = database.execute(
        "SELECT interval_seconds,max_steps FROM mud_travel_settings WHERE id=1"
    ).fetchone()
    portal_name = request.form.get(
        "mud_portal_name", current_portal["name"]
    ).strip()
    portal_description = request.form.get(
        "mud_portal_description", current_portal["description"]
    ).strip()
    if not 2 <= len(portal_name) <= 80:
        flash("Portal room name must be between 2 and 80 characters.", "error")
        return redirect(url_for("station.settings") + "#mud-service")
    if not 1 <= len(portal_description) <= 2000:
        flash("Portal room description must be between 1 and 2000 characters.", "error")
        return redirect(url_for("station.settings") + "#mud-service")
    try:
        travel_interval = int(request.form.get(
            "mud_travel_interval", current_travel["interval_seconds"] if current_travel else 6))
        travel_steps = int(request.form.get(
            "mud_travel_max_steps", current_travel["max_steps"] if current_travel else 200))
    except ValueError:
        travel_interval = travel_steps = 0
    if not 3 <= travel_interval <= 30:
        flash("Wilderness travel interval must be between 3 and 30 seconds.", "error")
        return redirect(url_for("station.settings") + "#mud-service")
    if not 1 <= travel_steps <= 1000:
        flash("Wilderness travel step limit must be between 1 and 1000 rooms.", "error")
        return redirect(url_for("station.settings") + "#mud-service")
    database.execute(
        "INSERT INTO mud_portal_settings(id,name,description) VALUES(1,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name,description=excluded.description,"
        "updated_at=CURRENT_TIMESTAMP", (portal_name, portal_description),
    )
    database.execute(
        "INSERT INTO mud_travel_settings(id,interval_seconds,max_steps) VALUES(1,?,?) "
        "ON CONFLICT(id) DO UPDATE SET interval_seconds=excluded.interval_seconds,"
        "max_steps=excluded.max_steps,updated_at=CURRENT_TIMESTAMP",
        (travel_interval, travel_steps),
    )
    database.commit()
    flash("MUD settings saved.", "success")
    return redirect(url_for("station.settings") + "#mud-service")


def _offer_worlds():
    """Worlds chosen on an import form to offer the new definitions in; None means every playable world."""
    return request.form.getlist("offer_worlds") if request.form.get("offer_form") else None


@bp.post("/sysop/settings/mud-origins/<kind>/import")
@admin_required
def import_mud_origin(kind):
    validate_csrf()
    if kind not in character_creation.KINDS:
        abort(404)
    destination = url_for("station.settings") + "#mud-origins"
    uploaded = request.files.get("origin_file")
    if uploaded is None or not uploaded.filename or not uploaded.filename.lower().endswith(".json"):
        flash("Choose a race or class JSON file to import.", "error")
        return redirect(destination)
    database = get_db()
    try:
        definition = character_creation.import_origin(
            database, kind, uploaded.read(character_creation.MAX_ORIGIN_BYTES + 1), _offer_worlds())
        database.commit()
    except (ValueError, sqlite3.Error) as error:
        database.rollback()
        flash(f"Import failed: {error}", "error")
    else:
        flash(f"Imported {character_creation.KIND_NAMES[kind]} {definition['name']}.", "success")
    return redirect(destination)


@bp.post("/sysop/settings/mud-origins/pack/import")
@admin_required
def import_mud_origin_pack():
    validate_csrf()
    destination = url_for("station.settings") + "#mud-origins"
    uploaded = request.files.get("origin_pack")
    if uploaded is None or not uploaded.filename or not uploaded.filename.lower().endswith(".json"):
        flash("Choose a race and class pack JSON file to import.", "error")
        return redirect(destination)
    database = get_db()
    try:
        definitions = character_creation.import_pack(
            database, uploaded.read(character_creation.MAX_ORIGIN_PACK_BYTES + 1), _offer_worlds())
        database.commit()
    except (ValueError, sqlite3.Error) as error:
        database.rollback()
        flash(f"Pack import failed: {error}", "error")
    else:
        counts = [f"{len(definitions[kind])} {kind}" for kind in character_creation.KINDS
                  if definitions[kind]]
        flash(f"Imported origin pack: {', '.join(counts)}.", "success")
    return redirect(destination)


@bp.get("/sysop/settings/mud-origins/<kind>/template")
@admin_required
def download_mud_origin_template(kind):
    if kind not in character_creation.KINDS:
        abort(404)
    return send_file(
        Path(__file__).with_name("mud_origin_templates") / f"{character_creation.KIND_NAMES[kind]}.json",
        as_attachment=True, download_name=f"mud-{character_creation.KIND_NAMES[kind]}-template.json",
        mimetype="application/json",
    )


@bp.get("/sysop/settings/mud-origins/pack/template")
@admin_required
def download_mud_origin_pack_template():
    return send_file(
        Path(__file__).with_name("mud_origin_templates") / "pack.json",
        as_attachment=True, download_name="mud-origin-pack-template.json",
        mimetype="application/json",
    )


@bp.post("/sysop/settings/mud-origins/<kind>/remove")
@admin_required
def remove_mud_origin(kind):
    validate_csrf()
    if kind not in character_creation.KINDS:
        abort(404)
    destination = url_for("station.settings") + "#mud-origins"
    if request.form.get("confirm_remove") != "on":
        flash("Confirm removal of the selected race or class.", "error")
        return redirect(destination)
    key = request.form.get("key", "")
    database = get_db()
    entry = next((item for item in character_creation.catalog(database)[kind]
                  if item["key"] == key and item["imported"]), None)
    if entry is None:
        flash("Select an imported race or class to remove.", "error")
        return redirect(destination)
    try:
        character_creation.remove_origins(database, {kind: {key}})
        restored = character_creation.restore_world_defaults(database)
        database.commit()
    except (ValueError, sqlite3.Error) as error:
        database.rollback()
        flash(str(error), "error")
    else:
        flash(f"Removed {character_creation.KIND_NAMES[kind]} {entry['definition']['name']}." + _restored_note(restored),
              "success")
    return redirect(destination)


@bp.post("/sysop/settings/mud-origins/remove")
@admin_required
def remove_mud_origins():
    validate_csrf()
    destination = url_for("station.settings") + "#mud-origin-removal"
    if request.form.get("confirm_remove") != "on":
        flash("Review the selected items and confirm bulk removal.", "error")
        return redirect(destination)
    selected = {kind: set(request.form.getlist(kind)) for kind in character_creation.KINDS}
    database = get_db()
    try:
        removed = character_creation.remove_origins(database, selected)
        restored = character_creation.restore_world_defaults(database)
        database.commit()
    except (ValueError, sqlite3.Error) as error:
        database.rollback()
        flash(f"Bulk removal failed: {error}", "error")
    else:
        names = ", ".join(entry["definition"]["name"] for entry in removed)
        flash(f"Removed {len(removed)} imported choices: {names}." + _restored_note(restored), "success")
    return redirect(destination)


def _world_selection():
    """Manual race, class, and skill choices posted by the world forms."""
    return {kind: request.form.getlist(kind) for kind in (*character_creation.KINDS, "skills")}


@bp.post("/sysop/settings/mud-skills/import")
@admin_required
def import_mud_skill():
    validate_csrf()
    destination = url_for("station.settings") + "#mud-skills"
    uploaded = request.files.get("skill_file")
    if uploaded is None or not uploaded.filename or not uploaded.filename.lower().endswith(".json"):
        flash("Choose a skill JSON file to import.", "error")
        return redirect(destination)
    database = get_db()
    try:
        definition = mud_skills.import_skill(database, uploaded.read(mud_skills.MAX_SKILL_BYTES + 1),
                                             _offer_worlds())
        database.commit()
    except (ValueError, sqlite3.Error) as error:
        database.rollback()
        flash(f"Import failed: {error}", "error")
    else:
        flash(f"Imported skill {definition['name']}.", "success")
    return redirect(destination)


@bp.post("/sysop/settings/mud-skills/pack/import")
@admin_required
def import_mud_skill_pack():
    validate_csrf()
    destination = url_for("station.settings") + "#mud-skills"
    uploaded = request.files.get("skill_pack")
    if uploaded is None or not uploaded.filename or not uploaded.filename.lower().endswith(".json"):
        flash("Choose a skill pack JSON file to import.", "error")
        return redirect(destination)
    database = get_db()
    try:
        definitions = mud_skills.import_pack(database, uploaded.read(mud_skills.MAX_PACK_BYTES + 1),
                                             _offer_worlds())
        database.commit()
    except (ValueError, sqlite3.Error) as error:
        database.rollback()
        flash(f"Pack import failed: {error}", "error")
    else:
        flash(f"Imported {len(definitions)} skills: "
              f"{', '.join(definition['name'] for definition in definitions)}.", "success")
    return redirect(destination)


@bp.get("/sysop/settings/mud-skills/template")
@admin_required
def download_mud_skill_template():
    return send_file(Path(__file__).with_name("mud_skill_templates") / "skill.json", as_attachment=True,
                     download_name="mud-skill-template.json", mimetype="application/json")


@bp.get("/sysop/settings/mud-skills/pack/template")
@admin_required
def download_mud_skill_pack_template():
    return send_file(Path(__file__).with_name("mud_skill_templates") / "pack.json", as_attachment=True,
                     download_name="mud-skill-pack-template.json", mimetype="application/json")


@bp.post("/sysop/settings/mud-skills/remove")
@admin_required
def remove_mud_skills():
    validate_csrf()
    destination = url_for("station.settings") + "#mud-skill-removal"
    if request.form.get("confirm_remove") != "on":
        flash("Review the selected skills and confirm removal.", "error")
        return redirect(destination)
    database = get_db()
    try:
        removed = mud_skills.remove_skills(database, set(request.form.getlist("skills")))
        restored = character_creation.restore_world_defaults(database)
        database.commit()
    except (ValueError, sqlite3.Error) as error:
        database.rollback()
        flash(f"Skill removal failed: {error}", "error")
    else:
        flash(f"Removed {len(removed)} imported skills: "
              f"{', '.join(entry['definition']['name'] for entry in removed)}. "
              "They were removed from every world; characters keep inactive ranks in skills they had "
              "learned." + _restored_note(restored), "success")
    return redirect(destination)


@bp.post("/sysop/settings/mud-abilities/import")
@admin_required
def import_mud_abilities():
    validate_csrf()
    destination = url_for("station.settings") + "#mud-abilities"
    uploaded = request.files.get("definition_file")
    if uploaded is None or not uploaded.filename or not uploaded.filename.lower().endswith(".json"):
        flash("Choose an ability or effect JSON file to import.", "error")
        return redirect(destination)
    database = get_db()
    try:
        definitions = mud_abilities.import_pack(database, uploaded.read(mud_abilities.MAX_PACK_BYTES + 1))
        database.commit()
    except (ValueError, sqlite3.Error) as error:
        database.rollback()
        flash(f"Import failed: {error}", "error")
    else:
        flash(f"Imported {', '.join(definition['name'] for definition in definitions)}.", "success")
    return redirect(destination)


@bp.get("/sysop/settings/mud-abilities/template/<name>")
@admin_required
def download_mud_ability_template(name):
    if name not in ("ability.json", "effect.json", "pack.json"):
        abort(404)
    return send_file(Path(__file__).with_name("mud_ability_templates") / name, as_attachment=True,
                     download_name=f"mud-{name.replace('.json', '')}-template.json", mimetype="application/json")


@bp.post("/sysop/settings/mud-abilities/remove")
@admin_required
def remove_mud_abilities():
    validate_csrf()
    destination = url_for("station.settings") + "#mud-abilities"
    if request.form.get("confirm_remove") != "on":
        flash("Review the selected abilities and effects and confirm removal.", "error")
        return redirect(destination)
    database = get_db()
    try:
        removed = []
        # Abilities go first so the effects they apply are free to leave.
        if request.form.getlist("abilities"):
            removed += mud_abilities.remove(database, set(request.form.getlist("abilities")))
        if request.form.getlist("effects"):
            removed += mud_effects.remove_effects(database, set(request.form.getlist("effects")))
        if not removed:
            raise ValueError("Select at least one imported ability or effect.")
        database.commit()
    except (ValueError, sqlite3.Error) as error:
        database.rollback()
        flash(f"Removal failed: {error}", "error")
    else:
        flash(f"Removed {', '.join(entry['name'] for entry in removed)}.", "success")
    return redirect(destination)


@bp.post("/sysop/settings/mud-worlds/import")
@admin_required
def import_mud_world():
    validate_csrf()
    destination = url_for("station.settings") + "#mud-worlds"
    uploaded = request.files.get("world_file")
    if uploaded is None or not uploaded.filename or not uploaded.filename.lower().endswith(".zip"):
        flash("Choose a world ZIP file to import.", "error")
        return redirect(destination)
    payload = uploaded.read(mud_worlds.MAX_ZIP_BYTES + 1)
    if request.form.get("mode") == "upgrade":
        try:
            world, summary = mud_worlds.upgrade(
                get_db(), payload, request.form.get("expected_hash", ""),
                reset_world_state=request.form.get("reset_world_state") == "on")
        except (mud_worlds.WorldError, sqlite3.Error, OSError) as error:
            flash(f"World upgrade failed: {error}", "error")
        else:
            flash(f"Upgraded world {world['name']}. Characters and their progress were kept"
                  + (f"; {summary['moved_to_start']} standing in removed rooms moved to the start room"
                     if summary["moved_to_start"] else "") + "."
                  + "".join(f" {note}" for note in summary["notes"]), "success")
        return redirect(destination)
    try:
        world = mud_worlds.install(
            get_db(), payload,
            origin_mode=request.form.get("origin_mode") or None,
            selected=_world_selection() if request.form.get("origin_mode") == "manual" else None,
            enable=request.form.get("enable") == "on")
    except (mud_worlds.WorldError, sqlite3.Error, OSError) as error:
        flash(f"World import failed: {error}", "error")
    else:
        state = "and enabled it" if request.form.get("enable") == "on" else "(disabled until you enable it)"
        flash(f"Imported world {world['name']} by {world['author']} {state}. Other worlds are unchanged.",
              "success")
    return redirect(destination)


@bp.post("/sysop/settings/mud-worlds/preview")
@admin_required
def preview_mud_world():
    validate_csrf()
    uploaded = request.files.get("world_file")
    payload = uploaded.read(mud_worlds.MAX_ZIP_BYTES + 1) if uploaded else b""
    database = get_db()
    upgrade = None
    try:
        document = mud_worlds.read_package(payload, database)
        if database.execute("SELECT 1 FROM mud_world_packages WHERE id=?", (document["id"],)).fetchone():
            upgrade = mud_worlds.upgrade_preview(database, payload)
            problems = upgrade["problems"]
        else:
            document, report = mud_worlds.validate_install(database, payload)
            problems = report.as_json()
        origins = {kind: [{"key": mud_worlds.bundled_key(document["id"], kind, item["key"]),
                           "name": item.get("name", item["key"])}
                          for item in (document["origins"].get(kind, []) if kind != "skills"
                                       else document["skills"])]
                   for kind in (*character_creation.KINDS, "skills")}
    except (mud_worlds.WorldError, KeyError, TypeError) as error:
        report = getattr(error, "report", None)
        return jsonify(error=str(error), problems=report.as_json() if report else []), 400
    return jsonify(world=document["name"], origins=origins, problems=problems,
                   format_version=document.get("format_version", 1), upgrade=upgrade,
                   abilities=[item["name"] for item in document.get("abilities", [])],
                   effects=[item["name"] for item in document.get("effects", [])])


@bp.post("/sysop/settings/mud-worlds/export")
@admin_required
def export_mud_world():
    validate_csrf()
    world_id = request.form.get("world_id", "")
    selected = {(kind, key) for kind in character_creation.KINDS
                for key in request.form.getlist(kind)}
    # By default a world travels with everything it uses; a custom selection may add more.
    custom = request.form.get("bundle_mode") == "custom"
    try:
        payload = mud_worlds.export(get_db(), world_id, origin_keys=selected if custom else None,
                                    skill_keys=set(request.form.getlist("skills")) if custom else None,
                                    strict=request.form.get("strict") == "on")
    except mud_worlds.WorldError as error:
        flash(f"World export failed: {error}", "error")
        return redirect(url_for("station.settings") + "#mud-worlds")
    return send_file(BytesIO(payload), mimetype="application/zip", as_attachment=True,
                     download_name=f"{world_id}-world.zip")


@bp.get("/sysop/settings/mud-worlds/template")
@admin_required
def download_mud_world_template():
    return send_file(BytesIO(mud_worlds.template_bytes()), mimetype="application/zip",
                     as_attachment=True, download_name="mud-world-template.zip")


@bp.get("/sysop/settings/mud-worlds/example")
@admin_required
def download_mud_world_example():
    return send_file(BytesIO(mud_worlds.example_bytes()), mimetype="application/zip",
                     as_attachment=True, download_name="lantern-isles-example-world.zip")


@bp.post("/sysop/settings/mud-worlds/enabled")
@admin_required
def set_mud_world_enabled():
    """Show or hide one world on the player world list; content and characters stay."""
    validate_csrf()
    world_id = request.form.get("world_id", "")
    enabled = request.form.get("enabled") == "1"
    database = get_db()
    try:
        mud_worlds.set_enabled(database, world_id, enabled)
        database.commit()
    except (mud_worlds.WorldError, sqlite3.Error) as error:
        database.rollback()
        flash(str(error), "error")
    else:
        name = mud_worlds.world_name(database, world_id)
        flash(f"{name} is now {'open to players' if enabled else 'hidden from players; its characters are kept'}.",
              "success")
    return redirect(url_for("station.settings") + "#mud-worlds")


def _availability_entries(database):
    """Installed definitions grouped by kind, each labelled with where it came from."""
    sources = {}
    for row in database.execute("SELECT w.kind,w.origin_key AS key,p.name FROM mud_world_origins AS w "
                                "JOIN mud_world_packages AS p ON p.id=w.world_id"):
        sources.setdefault((row["kind"], row["key"]), []).append(row["name"])
    for row in database.execute("SELECT w.skill_key AS key,p.name FROM mud_world_skills AS w "
                                "JOIN mud_world_packages AS p ON p.id=w.world_id"):
        sources.setdefault(("skills", row["key"]), []).append(row["name"])
    themed = {(row["kind"], row["origin_key"]) for row in database.execute(
        "SELECT kind,origin_key FROM mud_dungeon_origins")}
    themed |= {("skills", row["skill_key"]) for row in database.execute("SELECT skill_key FROM mud_dungeon_skills")}
    origins = character_creation.catalog(database, world="")
    entries = {kind: origins[kind] for kind in character_creation.KINDS}
    entries["skills"] = mud_skills.catalog(database, world="")
    for kind, items in entries.items():
        for entry in items:
            worlds = sources.get((kind, entry["key"]), [])
            entry["source"] = ("Built-in" if not entry["imported"] else
                               "Bundled with " + ", ".join(worlds) if worlds else
                               "Dungeon theme" if (kind, entry["key"]) in themed else "Standalone import")
    return entries


@bp.route("/sysop/settings/mud-worlds/<world_id>/availability", methods=["GET", "POST"])
@admin_required
def mud_world_availability(world_id):
    """Choose the races, classes, and skills one world offers; other worlds keep their own choices."""
    database = get_db()
    if world_id != mud_scope.PROCEDURAL and database.execute(
            "SELECT 1 FROM mud_world_packages WHERE id=?", (world_id,)).fetchone() is None:
        abort(404)
    if request.method == "POST":
        validate_csrf()
        try:
            character_creation.set_world_availability(database, world_id, _world_selection())
            database.commit()
        except (ValueError, sqlite3.Error) as error:
            database.rollback()
            flash(f"Availability was not saved: {error}", "error")
        else:
            flash(f"Updated races, classes, and skills for {mud_worlds.world_name(database, world_id)}. "
                  "Existing characters keep the choices they already made.", "success")
            return redirect(url_for("station.mud_world_availability", world_id=world_id))
    chosen = character_creation.world_availability(database, world_id)
    bundled = mud_worlds.world_origin_keys(database, world_id) if world_id != mud_scope.PROCEDURAL else \
        {kind: set() for kind in character_creation.AVAILABILITY_KINDS}
    entries = _availability_entries(database)
    for kind, items in entries.items():
        for entry in items:
            entry["offered"] = entry["key"] in chosen[kind]
            entry["bundled"] = entry["key"] in bundled[kind]
            entry["holders"] = database.execute(
                f"SELECT COUNT(*) FROM mud_characters WHERE world_key=? AND {'race_key' if kind == 'races' else 'class_key'}=?",
                (world_id, entry["key"])).fetchone()[0] if kind != "skills" else database.execute(
                "SELECT COUNT(*) FROM mud_character_skills AS s JOIN mud_characters AS c ON c.id=s.character_id "
                "WHERE c.world_key=? AND s.skill_key=?", (world_id, entry["key"])).fetchone()[0]
    return render_template("mud/world_availability.html", world_id=world_id,
                           world_name=mud_worlds.world_name(database, world_id), entries=entries,
                           configured=character_creation.availability_configured(database, world_id),
                           skill_groups=mud_skills.GROUPS, starting_choices=mud_skills.starting_choices())


def _restored_note(worlds):
    if not worlds:
        return ""
    return (" Built-in choices were restored for character creation in "
            + ", ".join(mud_worlds.world_name(get_db(), world) for world in worlds) + ".")


def _removal_message(label, result):
    kinds = (*character_creation.KINDS, "skills")
    removed = ", ".join(f"{len(result['removed'].get(kind, ()))} {kind}" for kind in kinds)
    preserved = ", ".join(f"{result['preserved'].get(kind, 0)} {kind}" for kind in kinds)
    message = f"{label} removed. Associated definitions removed: {removed}; preserved: {preserved}."
    message += _restored_note(result["restored"])
    if result["file_warning"]:
        message += " Package file cleanup failed; remove the leftover file manually."
    return message


@bp.post("/sysop/settings/mud-worlds/remove")
@admin_required
def remove_mud_world():
    validate_csrf()
    destination = url_for("station.settings") + "#mud-worlds"
    if request.form.get("confirm_remove") != "on":
        flash("Confirm world removal first.", "error")
        return redirect(destination)
    try:
        result = mud_worlds.remove(get_db(), request.form.get("world_id", ""),
                                   remove_origins=request.form.get("remove_origins") == "on")
    except (mud_worlds.WorldError, ValueError, sqlite3.Error) as error:
        flash(str(error), "error")
    else:
        flash(_removal_message("Custom world", result) + " Its characters are kept and return if it is reinstalled.",
              "success")
    return redirect(destination)


@bp.post("/sysop/settings/dungeons/import")
@admin_required
def import_dungeon():
    """Validate and install a playable theme for subsequent dungeon runs."""
    validate_csrf()
    destination = url_for("station.settings") + "#dungeon-themes"
    uploaded = request.files.get("dungeon_file")
    if uploaded is None or not uploaded.filename or not uploaded.filename.lower().endswith(".json"):
        flash("Choose a JSON dungeon theme file to import.", "error")
        return redirect(destination)
    payload = uploaded.read(dungeons.MAX_THEME_BYTES + 1)
    if not payload or len(payload) > dungeons.MAX_THEME_BYTES:
        flash("Dungeon theme must be between 1 byte and 1 MB.", "error")
        return redirect(destination)
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        flash("Dungeon theme must be valid UTF-8 JSON.", "error")
        return redirect(destination)
    errors = dungeons.validate_template(document, get_db())
    if isinstance(document, dict) and not document.get("author"):
        errors.append("$.author: must be a non-empty string")
    if errors:
        flash("Dungeon theme is invalid: " + "; ".join(dict.fromkeys(errors)), "error")
        return redirect(destination)
    try:
        installed = dungeons.discover_templates()
    except dungeons.TemplateError as problem:
        flash(f"Installed dungeon themes need attention: {problem}", "error")
        return redirect(destination)
    path = dungeons.custom_themes_directory() / f"{document['id']}.json"
    upgrade = request.form.get("upgrade") == "on"
    previous = installed.get(document["id"])
    if previous is not None:
        if not upgrade:
            flash("A dungeon theme with that ID is already installed. To replace it, raise its version and "
                  "tick Upgrade.", "error")
            return redirect(destination)
        if previous[0] != path or path.is_symlink():
            flash("Built-in dungeon themes cannot be replaced.", "error")
            return redirect(destination)
        if previous[1].get("version") == document["version"]:
            if dungeons.template_hash(previous[1]) != dungeons.template_hash(document):
                flash("This theme changes content without changing its version; raise its version to upgrade.",
                      "error")
            else:
                flash("That version of the theme is already installed.", "error")
            return redirect(destination)
    elif upgrade:
        upgrade = False
    staged = path.with_name(f"{path.name}.importing-{secrets.token_hex(4)}")
    try:
        with staged.open("x", encoding="utf-8") as target:
            json.dump(document, target, ensure_ascii=False, indent=2)
            target.write("\n")
        if not upgrade and path.exists():
            raise FileExistsError
    except FileExistsError:
        staged.unlink(missing_ok=True)
        flash("A dungeon theme file with that ID already exists.", "error")
        return redirect(destination)
    database = get_db()
    notes = []
    try:
        with database:
            dungeons.install_bundles(database, document, notes)
            # A theme's own races, classes, and skills are offered where its runs happen.
            for kind, keys in dungeons.bundled_keys(database, document).items():
                character_creation.offer(database, kind, keys, [mud_scope.PROCEDURAL])
            if not upgrade:
                database.execute("DELETE FROM mud_dungeon_theme_settings WHERE theme_id=?", (document["id"],))
            os.replace(staged, path)
    except (ValueError, sqlite3.Error, OSError) as error:
        try:
            staged.unlink(missing_ok=True)
            cleanup = ""
        except OSError:
            cleanup = " The imported file could not be cleaned up; remove it manually."
        flash(f"Dungeon theme import failed: {error}.{cleanup}", "error")
        return redirect(destination)
    verb = "Upgraded" if upgrade else "Imported"
    flash(f"{verb} dungeon theme {document['name']} by {document['author']}."
          + (" Runs already started keep the version they began with." if upgrade else "")
          + "".join(f" {note}" for note in notes), "success")
    return redirect(destination)


@bp.get("/sysop/settings/dungeons/<theme_id>/export")
@admin_required
def export_dungeon(theme_id):
    """Download an installed theme as a self-contained file another station can import."""
    try:
        document = dungeons.export_theme(get_db(), theme_id, embed_shared=request.args.get("embed") == "1")
    except (dungeons.TemplateError, ValueError) as problem:
        flash(f"Could not export dungeon theme: {problem}", "error")
        return redirect(url_for("station.settings") + "#dungeon-themes")
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return send_file(BytesIO(payload), mimetype="application/json", as_attachment=True,
                     download_name=f"{theme_id}-{document['version']}.json")


@bp.post("/sysop/settings/dungeons/remove")
@admin_required
def remove_dungeon():
    validate_csrf()
    destination = url_for("station.settings") + "#dungeon-themes"
    theme_id = request.form.get("theme_id", "")
    if request.form.get("confirm_remove") != "on":
        flash("Check the confirmation box before removing a dungeon theme.", "error")
        return redirect(destination)
    try:
        installed = dungeons.discover_templates()
    except dungeons.TemplateError as problem:
        flash(f"Installed dungeon themes need attention: {problem}", "error")
        return redirect(destination)
    entry = installed.get(theme_id)
    path = dungeons.custom_themes_directory() / f"{theme_id}.json"
    if entry is None or entry[0] != path or path.is_symlink():
        flash("Select an installed custom dungeon theme to remove.", "error")
        return redirect(destination)
    database = get_db()
    try:
        result = dungeons.remove_template(database, theme_id, path,
            remove_origins=request.form.get("remove_origins") == "on")
    except (ValueError, sqlite3.Error, OSError) as error:
        flash(f"Could not remove dungeon theme: {error}", "error")
    else:
        flash(f"Removed dungeon theme {entry[1]['name']}. " +
              _removal_message("Custom dungeon theme", result), "success")
    return redirect(destination)


@bp.post("/sysop/settings/dungeons/availability")
@admin_required
def set_dungeon_availability():
    """Enable or disable an installed theme without changing its package or history."""
    validate_csrf()
    destination = url_for("station.settings") + "#dungeon-themes"
    theme_id = request.form.get("theme_id", "")
    enabled_text = request.form.get("enabled")
    if enabled_text not in {"0", "1"}:
        abort(400)
    try:
        installed = dungeons.discover_templates()
    except dungeons.TemplateError as problem:
        flash(f"Installed dungeon themes need attention: {problem}", "error")
        return redirect(destination)
    entry = installed.get(theme_id)
    if entry is None:
        flash("Select an installed dungeon theme.", "error")
        return redirect(destination)
    enabled = enabled_text == "1"
    database = get_db()
    dungeons.set_theme_enabled(database, theme_id, enabled)
    database.commit()
    status = "enabled" if enabled else "disabled"
    flash(f"Dungeon theme {entry[1]['name']} {status}.", "success")
    return redirect(destination)


@bp.get("/sysop/settings/dungeons/template")
@admin_required
def download_dungeon_template():
    return send_file(
        Path(__file__).with_name("dungeon_templates") / "starter-theme.json",
        as_attachment=True, download_name="dungeon-theme-template.json",
        mimetype="application/json",
    )


@bp.post("/sysop/settings/mud-daily")
@admin_required
def mud_daily_settings():
    """Configure future daily rotations without mutating today's snapshot."""
    validate_csrf()
    database = get_db()
    timezone_name = request.form.get("daily_timezone", "UTC").strip()
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        flash("Enter a valid IANA timezone, such as UTC or America/Edmonton.", "error")
        return redirect(url_for("station.settings") + "#mud-daily")
    max_attempts = request.form.get("daily_max_attempts", type=int)
    if max_attempts is None or not 1 <= max_attempts <= 20:
        flash("Daily attempts must be between 1 and 20.", "error")
        return redirect(url_for("station.settings") + "#mud-daily")
    try:
        discovered = dungeons.playable_templates(database)
    except dungeons.TemplateError as problem:
        flash(f"Installed dungeon themes need attention: {problem}", "error")
        return redirect(url_for("station.settings") + "#mud-daily")
    eligible = sorted(set(request.form.getlist("daily_theme_id")))
    if request.form.get("daily_all_themes") == "on":
        eligible = []
    elif not eligible or any(item not in discovered for item in eligible):
        flash("Select at least one installed theme, or allow all playable themes.", "error")
        return redirect(url_for("station.settings") + "#mud-daily")
    difficulty_text = request.form.get("daily_difficulty", "").strip()
    room_count_text = request.form.get("daily_room_count", "").strip()
    layout = request.form.get("daily_layout", "").strip() or None
    try:
        difficulty = int(difficulty_text) if difficulty_text else None
        room_count = int(room_count_text) if room_count_text else None
    except ValueError:
        flash("Daily difficulty and room count must be whole numbers.", "error")
        return redirect(url_for("station.settings") + "#mud-daily")
    if difficulty is not None and not 1 <= difficulty <= 100000:
        flash("Daily difficulty must be between 1 and 100000.", "error")
        return redirect(url_for("station.settings") + "#mud-daily")
    if room_count is not None and not 2 <= room_count <= 500:
        flash("Daily room count must be between 2 and 500.", "error")
        return redirect(url_for("station.settings") + "#mud-daily")
    if layout not in {None, "linear", "branching", "maze", "hub"}:
        flash("Select a supported daily layout.", "error")
        return redirect(url_for("station.settings") + "#mud-daily")
    database.execute(
        "UPDATE mud_daily_dungeon_settings SET timezone=?,eligible_theme_ids_json=?,"
        "max_attempts=?,difficulty=?,room_count=?,layout=?,updated_at=CURRENT_TIMESTAMP WHERE id=1",
        (timezone_name, json.dumps(eligible), max_attempts, difficulty, room_count, layout),
    )
    database.commit()
    flash("Dungeon of the Day settings saved. Existing daily snapshots were preserved.", "success")
    return redirect(url_for("station.settings") + "#mud-daily")


@bp.post("/sysop/settings/mud-daily/retry")
@admin_required
def retry_mud_daily():
    """Retry only a failed current-day generation record."""
    validate_csrf()
    database = get_db()
    from .mud import ensure_daily_dungeon
    database.execute("BEGIN IMMEDIATE")
    daily = ensure_daily_dungeon(database, retry_failed=True)
    database.commit()
    flash(
        "Today's dungeon is ready." if daily["status"] == "ready"
        else f"Daily generation still failed: {daily['error_message']}",
        "success" if daily["status"] == "ready" else "error",
    )
    return redirect(url_for("station.settings") + "#mud-daily")


@bp.post("/sysop/radio/storage")
@admin_required
def save_radio_default_quota():
    validate_csrf()
    quota = request.form.get("radio_default_quota_mb", type=int)
    if quota is None or not 1 <= quota <= 10240:
        flash("Default radio quota must be between 1 and 10240 MB.", "error")
    else:
        get_db().execute("UPDATE station SET radio_default_quota_mb=? WHERE id=1", (quota,))
        get_db().commit()
        flash("Default radio library quota updated.", "success")
    return redirect(url_for("station.settings") + "#radio-storage")


@bp.post("/sysop/radio/broadcast-limit")
@admin_required
def save_broadcast_limit():
    validate_csrf()
    minutes = request.form.get("radio_broadcast_limit_minutes", type=int)
    if minutes is None or not 1 <= minutes <= 1440:
        flash("Broadcast duration must be between 1 and 1440 minutes.", "error")
    else:
        get_db().execute("UPDATE station SET radio_broadcast_limit_minutes=? WHERE id=1", (minutes,))
        get_db().commit()
        flash("Hosted broadcast duration limit updated.", "success")
    return redirect(url_for("station.settings") + "#broadcast-frequencies")


@bp.post("/sysop/radio/users/<int:user_id>/quota")
@admin_required
def save_radio_user_quota(user_id):
    validate_csrf()
    database = get_db()
    if database.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
        flash("User not found.", "error")
        return redirect(url_for("station.settings") + "#radio-storage")
    raw = request.form.get("radio_quota_mb", "").strip()
    quota = int(raw) if raw.isdigit() else None
    if raw and (quota is None or not 1 <= quota <= 10240):
        flash("User radio quota must be blank or between 1 and 10240 MB.", "error")
    else:
        database.execute("UPDATE users SET radio_quota_mb=? WHERE id=?", (quota, user_id))
        database.commit()
        flash("User radio library quota updated.", "success")
    return redirect(url_for("station.settings") + "#radio-storage")


@bp.post("/sysop/videos/users/<int:user_id>/quota")
@admin_required
def save_videos_user_quota(user_id):
    validate_csrf()
    database = get_db()
    if database.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
        flash("User not found.", "error")
    else:
        raw = request.form.get("videos_quota_mb", "").strip()
        quota = int(raw) if raw.isdigit() else None
        if raw and (quota is None or not 1 <= quota <= 10240):
            flash("User videos quota must be blank or between 1 and 10240 MB.", "error")
        else:
            database.execute("UPDATE users SET videos_quota_mb=? WHERE id=?", (quota, user_id))
            database.commit()
            flash("User videos quota updated.", "success")
    return redirect(url_for("station.settings") + "#videos-service")

@bp.post("/sysop/library/users/<int:user_id>/quota")
@admin_required
def save_library_user_quota(user_id):
    validate_csrf()
    database = get_db()
    if database.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
        flash("User not found.", "error")
    else:
        raw = request.form.get("library_quota_mb", "").strip()
        quota = int(raw) if raw.isdigit() else None
        if raw and (quota is None or not 1 <= quota <= 10240):
            flash("User library quota must be blank or between 1 and 10240 MB.", "error")
        else:
            database.execute("UPDATE users SET library_quota_mb=? WHERE id=?", (quota, user_id))
            database.commit()
            flash("User library quota updated.", "success")
    return redirect(url_for("station.settings") + "#library-service")


@bp.route("/sysop/settings", methods=("GET", "POST"))
@admin_required
def settings():
    if request.method == "POST":
        validate_csrf()
        if request.form.get("settings_section") == "modules":
            from .modules import ALL_MODULES

            values = [int(request.form.get(f"{name}_enabled") == "on")
                      for name, _label in ALL_MODULES]
            assignments = ",".join(f"{name}_enabled=?" for name, _label in ALL_MODULES)
            database = get_db()
            database.execute(f"UPDATE station SET {assignments} WHERE id=1", values)
            database.commit()
            flash("Module availability updated.", "success")
            return redirect(url_for("station.settings") + "#module-availability")
        if request.form.get("settings_section") == "header_taglines":
            try:
                entries = header_taglines.parse_form(request.form)
            except ValueError as error:
                flash(str(error), "error")
            else:
                database = get_db()
                database.execute(
                    "UPDATE station SET header_taglines_json=?,brand_tagline=? WHERE id=1",
                    (json.dumps(entries, ensure_ascii=False), entries[0]["text"] if entries else ""),
                )
                database.commit()
                flash("Header taglines updated.", "success")
            return redirect(url_for("station.settings") + "#header-taglines")
        if request.form.get("settings_section") == "events":
            permission = request.form.get("events_create_permission")
            if permission not in {"sysops", "groups", "members"}:
                flash("Select a valid event creation permission.", "error")
                return redirect(url_for("station.settings") + "#events-service")
            database = get_db()
            selected = request.form.getlist("event_group_id")
            if any(not value.isdigit() for value in selected):
                flash("Select valid permission groups.", "error")
                return redirect(url_for("station.settings") + "#events-service")
            valid = {row["id"] for row in database.execute("SELECT id FROM radio_permission_groups")}
            ids = {int(value) for value in selected}
            if not ids <= valid:
                flash("Select valid permission groups.", "error")
                return redirect(url_for("station.settings") + "#events-service")
            database.execute("UPDATE station SET events_enabled=?,events_create_permission=? WHERE id=1",
                             (request.form.get("events_enabled") == "on", permission))
            database.execute("DELETE FROM event_creation_groups")
            database.executemany("INSERT INTO event_creation_groups(group_id) VALUES(?)", [(item,) for item in ids])
            database.commit()
            flash("Events settings updated.", "success")
            return redirect(url_for("station.settings") + "#events-service")
        if request.form.get("settings_section") == "user_feeds":
            enabled = request.form.get("user_feeds_enabled") == "on"
            database = get_db()
            database.execute("UPDATE station SET user_feeds_enabled=? WHERE id=1", (enabled,))
            database.commit()
            flash("User Feeds enabled." if enabled else "User Feeds disabled.", "success")
            return redirect(url_for("station.settings") + "#user-feeds")
        if request.form.get("settings_section") == "office":
            enabled = request.form.get("office_enabled") == "on"
            database = get_db()
            if request.form.get("office_nav_setting_present") == "1":
                database.execute(
                    "UPDATE station SET office_enabled=?,office_nav_links_enabled=? WHERE id=1",
                    (enabled, request.form.get("office_nav_links_enabled") == "on"),
                )
            else:
                database.execute("UPDATE station SET office_enabled=? WHERE id=1", (enabled,))
            database.commit()
            flash("Office enabled." if enabled else "Office disabled.", "success")
            return redirect(url_for("station.settings") + "#office-suite")
        if request.form.get("settings_section") == "imageboard":
            enabled = request.form.get("imageboard_enabled") == "on"
            max_threads = request.form.get("imageboard_max_threads", g.station["imageboard_max_threads"], type=int)
            max_image_replies = request.form.get("imageboard_max_image_replies", g.station["imageboard_max_image_replies"], type=int)
            if (max_threads is None or not 1 <= max_threads <= 10000
                    or max_image_replies is None or not 0 <= max_image_replies <= 10000):
                flash("Imageboard limits must be 1–10000 threads and 0–10000 image replies.", "error")
                return redirect(url_for("station.settings") + "#imageboard-service")
            database = get_db()
            database.execute("UPDATE station SET imageboard_enabled=?,imageboard_max_threads=?,"
                             "imageboard_max_image_replies=? WHERE id=1",
                             (enabled, max_threads, max_image_replies))
            database.commit()
            flash("Imageboard enabled." if enabled else "Imageboard disabled.", "success")
            return redirect(url_for("station.settings") + "#imageboard-service")
        if request.form.get("settings_section") == "kiwix":
            enabled = request.form.get("kiwix_enabled") == "on"
            database = get_db()
            database.execute("UPDATE station SET kiwix_enabled=? WHERE id=1", (enabled,))
            database.commit()
            flash("Wiki enabled." if enabled else "Wiki disabled.", "success")
            return redirect(url_for("station.settings") + "#kiwix-service")
        if request.form.get("settings_section") == "radio":
            return _save_radio_bands()
        if request.form.get("settings_section") == "videos":
            mode = request.form.get("videos_mode")
            quota = request.form.get("videos_default_quota_mb", g.station["videos_default_quota_mb"], type=int)
            if mode not in {"community", "curated"}:
                flash("Select a valid videos mode.", "error")
            elif quota is None or not 1 <= quota <= 10240:
                flash("Default videos quota must be between 1 and 10240 MB.", "error")
            else:
                enabled = request.form.get("videos_enabled") == "on"
                database = get_db()
                database.execute("UPDATE station SET videos_enabled=?,videos_mode=?,"
                                 "videos_default_quota_mb=? WHERE id=1", (enabled, mode, quota))
                database.commit()
                flash("Videos settings updated.", "success")
            return redirect(url_for("station.settings") + "#videos-service")
        if request.form.get("settings_section") == "library":
            mode = request.form.get("library_mode")
            quota = request.form.get("library_default_quota_mb", type=int)
            max_upload = request.form.get("library_max_upload_mb", type=int)
            if mode not in {"community", "curated"}:
                flash("Select a valid library mode.", "error")
            elif quota is None or not 1 <= quota <= 10240:
                flash("Default library quota must be between 1 and 10240 MB.", "error")
            elif max_upload is None or not 1 <= max_upload <= 10240:
                flash("Library file size limit must be between 1 and 10240 MB.", "error")
            else:
                database = get_db()
                database.execute("UPDATE station SET library_enabled=?,library_mode=?,"
                                 "library_default_quota_mb=?,library_max_upload_mb=?,"
                                 "library_downloads_enabled=? WHERE id=1",
                                 (request.form.get("library_enabled") == "on", mode, quota, max_upload,
                                  request.form.get("library_downloads_enabled") == "on"))
                database.commit()
                flash("Library settings updated.", "success")
            return redirect(url_for("station.settings") + "#library-service")
        if request.form.get("settings_section") == "arcade":
            mode = request.form.get("arcade_mode")
            quota = request.form.get("arcade_default_quota_mb", type=int)
            psp_max = request.form.get("arcade_psp_max_upload_mb", type=int,
                                       default=g.station["arcade_psp_max_upload_mb"])
            if mode not in {"community", "curated"}:
                flash("Select a valid Arcade mode.", "error")
            elif quota is None or not 1 <= quota <= 10240:
                flash("Arcade quota must be between 1 and 10240 MB.", "error")
            elif psp_max is None or not 1 <= psp_max <= 4096:
                flash("Max PSP upload size must be between 1 and 4096 MB.", "error")
            else:
                database = get_db()
                database.execute("UPDATE station SET arcade_enabled=?,arcade_mode=?,"
                                 "arcade_default_quota_mb=?,arcade_psp_max_upload_mb=? WHERE id=1",
                                 (request.form.get("arcade_enabled") == "on", mode, quota, psp_max))
                database.commit()
                flash("Arcade settings updated.", "success")
            return redirect(url_for("station.settings") + "#arcade-service")
        if request.form.get("settings_section") == "tv":
            max_upload = request.form.get("tv_max_upload_mb", type=int)
            zone = request.form.get("tv_timezone", "").strip()
            default_channel = request.form.get("tv_default_channel_id", type=int)
            database = get_db()
            if max_upload is None or not 1 <= max_upload <= tv.MAX_UPLOAD_MB_LIMIT:
                flash(f"Max TV upload size must be between 1 and {tv.MAX_UPLOAD_MB_LIMIT} MB.", "error")
            elif not tv.valid_timezone(zone):
                flash("Enter a valid IANA time zone such as Europe/London.", "error")
            elif default_channel is not None and not database.execute(
                    "SELECT 1 FROM tv_channels WHERE id=?", (default_channel,)).fetchone():
                flash("Select a valid default channel.", "error")
            else:
                database.execute("UPDATE station SET tv_enabled=?,tv_max_upload_mb=?,tv_timezone=?,"
                                 "tv_default_channel_id=? WHERE id=1",
                                 (request.form.get("tv_enabled") == "on", max_upload, zone, default_channel))
                tv.bump_version(database)
                database.commit()
                flash("TV settings updated.", "success")
            return redirect(url_for("station.settings") + "#tv-service")
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        landing_message = request.form.get(
            "landing_message", g.station["landing_message"]
        ).strip()
        landing_heading = request.form.get(
            "landing_heading", g.station["landing_heading"]
        ).strip()
        landing_message_enabled = g.station["landing_message_enabled"]
        if "landing_message_control" in request.form:
            landing_message_enabled = request.form.get("landing_message_enabled") == "on"
        brand_tagline = request.form.get(
            "brand_tagline", g.station["brand_tagline"]
        ).strip()
        header_font = request.form.get("header_font", g.station["header_font"])
        header_text = request.form.get("header_text", g.station["header_text"]).strip()
        header_color = request.form.get("header_color", g.station["header_color"]).strip()
        site_theme = request.form.get("site_theme", g.station["site_theme"])
        masthead_theme = request.form.get("masthead_theme", g.station["masthead_theme"])
        personal_themes_enabled = g.station["personal_themes_enabled"]
        if "personal_themes_control" in request.form:
            personal_themes_enabled = request.form.get("personal_themes_enabled") == "on"
        if masthead_theme != g.station["masthead_theme"] and header_color == g.station["header_color"]:
            header_color = ""
        header_font_size = request.form.get(
            "header_font_size", g.station["header_font_size"], type=int
        )
        upload_permission = request.form.get("upload_permission", g.station["upload_permission"])
        storage_limit = request.form.get("file_storage_limit_mb", g.station["file_storage_limit_mb"], type=int)
        file_limit = request.form.get("file_size_limit_mb", g.station["file_size_limit_mb"], type=int)
        if not 2 <= len(name) <= 80: flash("Station name must be between 2 and 80 characters.", "error")
        elif len(description) > 500: flash("Station description cannot exceed 500 characters.", "error")
        elif len(landing_heading) > 100: flash("Landing message heading cannot exceed 100 characters.", "error")
        elif len(landing_message) > 4000: flash("Landing message cannot exceed 4000 characters.", "error")
        elif "brand_tagline" in request.form and not 1 <= len(brand_tagline) <= 160: flash("Header tagline must be between 1 and 160 characters.", "error")
        elif len(header_text) > 80: flash("Banner text must be 80 characters or fewer.", "error")
        elif not is_available_header_font(header_font): flash("Select a valid FIGlet header style.", "error")
        elif header_color and not HEX_COLOR_RE.fullmatch(header_color): flash("Banner color must be a six-digit hexadecimal color.", "error")
        elif not themes.valid(site_theme, "site") or not themes.valid(masthead_theme, "masthead"): flash("Select valid station themes.", "error")
        elif header_font_size is None or not 5 <= header_font_size <= 200: flash("Header size must be between 5% and 200%.", "error")
        elif upload_permission not in {"members", "admins", "disabled"}: flash("Select a valid upload permission.", "error")
        elif storage_limit is None or not 1 <= storage_limit <= 10240: flash("Archive storage must be between 1 and 10240 MB.", "error")
        elif file_limit is None or not 1 <= file_limit <= 64: flash("Per-file limit must be between 1 and 64 MB.", "error")
        elif file_limit > storage_limit: flash("Per-file limit cannot exceed total archive storage.", "error")
        else:
            database = get_db()
            access_values = ()
            access_update = ""
            # Accept the original combined form for compatibility with older
            # pages while allowing the new access-policy form to save alone.
            if "registration_open" in request.form or "guest_access" in request.form:
                access_update = ",registration_open=?,registration_mode=?,guest_access=?"
                legacy_mode = "open" if request.form.get("registration_open") == "on" else "disabled"
                access_values = (
                    legacy_mode == "open",
                    legacy_mode,
                    request.form.get("guest_access") == "on",
                )
            database.execute(
                "UPDATE station SET name=?,description=?,landing_message=?,landing_heading=?,"
                "landing_message_enabled=?,brand_tagline=?,header_font=?,header_font_size=?,header_text=?,header_color=?,site_theme=?,masthead_theme=?,personal_themes_enabled=?,"
                "upload_permission=?,file_storage_limit_mb=?,file_size_limit_mb=?"
                + access_update + " WHERE id=1",
                (
                    name,
                    description,
                    landing_message,
                    landing_heading,
                    landing_message_enabled,
                    brand_tagline,
                    header_font,
                    header_font_size,
                    header_text,
                    header_color,
                    site_theme,
                    masthead_theme,
                    personal_themes_enabled,
                    upload_permission,
                    storage_limit,
                    file_limit,
                ) + access_values,
            )
            if "brand_tagline" in request.form:
                # Older station settings forms still submit the single tagline.
                current = header_taglines.configured(g.station)
                if current:
                    current[0] = {**current[0], "text": brand_tagline}
                else:
                    current = [{"text": brand_tagline, "color": "", "effect": "none"}]
                database.execute(
                    "UPDATE station SET header_taglines_json=? WHERE id=1",
                    (json.dumps(current, ensure_ascii=False),),
                )
            database.commit(); flash("Station parameters updated.", "success")
            if name != g.station["name"] and current_app.config.get("STATION_CONFIG"):
                # The launcher renames the station's folder at its next stop or restart.
                sync_name(current_app.config["STATION_CONFIG"], name)
                flash("The station folder will be renamed to match at the next restart.", "success")
            return redirect(url_for("station.settings"))
    database = get_db()
    header_fonts = available_header_fonts()
    bands = database.execute("SELECT * FROM radio_bands ORDER BY start_khz").fetchall()
    groups = database.execute("SELECT * FROM radio_permission_groups ORDER BY is_default DESC,name").fetchall()
    rules = database.execute(
        "SELECT r.*,b.name band_name FROM radio_permission_rules r "
        "LEFT JOIN radio_bands b ON b.id=r.band_id ORDER BY r.group_id,r.id"
    ).fetchall()
    library_categories = database.execute(
        "SELECT id,name,enabled FROM library_categories ORDER BY name COLLATE NOCASE"
    ).fetchall()
    users = database.execute(
        "SELECT id,username,callsign,radio_quota_mb,videos_quota_mb,library_quota_mb,arcade_quota_mb FROM users ORDER BY username COLLATE NOCASE"
    ).fetchall()
    assignments = {row["user_id"]: row for row in database.execute(
        "SELECT a.*,u.callsign FROM radio_broadcast_assignments a JOIN users u ON u.id=a.user_id"
    )}
    broadcast_sessions = current_app.extensions["hosted_broadcasts"].all_public()
    memberships = {user["id"]: {row["group_id"] for row in database.execute(
        "SELECT group_id FROM radio_group_memberships WHERE user_id=?", (user["id"],)
    )} for user in users}
    effective = {user["id"]: {band["id"]: effective_access(user["id"], band["start_khz"])
                              for band in bands} for user in users}
    broadcast_hosts = {user["id"]: any(
        can_host_broadcast(user["id"], band["start_khz"])
        for band in bands if band["supports_hosted_broadcasts"]
    ) for user in users}
    rules_by_group = {group["id"]: [rule for rule in rules if rule["group_id"] == group["id"]]
                      for group in groups}
    invitations = database.execute(
        "SELECT i.id,i.code_hint,i.created_at,i.used_at,i.revoked_at,"
        "creator.callsign AS creator_callsign,creator.username AS creator_username,"
        "recipient.callsign AS used_callsign,recipient.username AS used_username "
        "FROM registration_invites i "
        "LEFT JOIN users creator ON creator.id=i.created_by "
        "LEFT JOIN users recipient ON recipient.id=i.used_by "
        "ORDER BY i.id DESC"
    ).fetchall()
    mud_portal = database.execute(
        "SELECT name,description FROM mud_portal_settings WHERE id=1"
    ).fetchone()
    mud_travel = database.execute(
        "SELECT interval_seconds,max_steps FROM mud_travel_settings WHERE id=1"
    ).fetchone()
    mud_worlds_all = mud_worlds.worlds(database, include_disabled=True)
    mud_world_list = [world for world in mud_worlds_all if world["kind"] == "authored"]
    for world in mud_world_list:
        world["id"] = world["key"]
    mud_origins = character_creation.catalog(database, world="")
    mud_skill_entries = mud_skills.catalog(database, world="")
    skill_holders = {row["skill_key"]: row["holders"] for row in database.execute(
        "SELECT skill_key,COUNT(*) AS holders FROM mud_character_skills GROUP BY skill_key")}
    for entry in mud_skill_entries:
        entry["holders"] = skill_holders.get(entry["key"], 0)
        entry["dependents"] = mud_skills.dependents(database, entry["key"]) if entry["imported"] else []
        entry["summary"] = mud_skills.describe_effects(entry["definition"])
    mud_ability_entries = [{**entry, "dependents": mud_abilities.dependents(database, entry["key"])
                            if entry["imported"] else []}
                           for entry in mud_abilities.catalog(database).values()]
    mud_effect_entries = [{**entry, "dependents": mud_effects.dependents(database, entry["key"])
                           if entry["imported"] else []}
                          for entry in mud_effects.catalog(database).values()]
    mud_world_origin_keys = {
        world["id"]: {kind: sorted(keys) for kind, keys in mud_worlds.world_origin_keys(
            database, world["id"]).items()}
        for world in mud_world_list}
    def removal_preview(linked, removable):
        lookup = {(kind, entry["key"]): entry["definition"]["name"]
                  for kind in character_creation.KINDS for entry in mud_origins[kind]}
        lookup.update({("skills", entry["key"]): entry["definition"]["name"] for entry in mud_skill_entries})
        return {kind: [{"name": lookup.get((kind, key), key),
                        "removable": key in {entry["key"] for entry in removable[kind]}}
                       for key in sorted(linked[kind])]
                for kind in (*character_creation.KINDS, "skills")}
    mud_world_removal = {world["id"]: removal_preview(
        mud_worlds.world_origin_keys(database, world["id"]),
        mud_worlds.removal_origins(database, world["id"]))
        for world in mud_world_list if world["imported"]}
    mud_daily_settings_row = database.execute(
        "SELECT * FROM mud_daily_dungeon_settings WHERE id=1"
    ).fetchone()
    try:
        mud_dungeon_themes = [
            {"id": key, "name": template["name"], "version": template["version"],
             "author": template.get("author"),
             "custom": path.parent == dungeons.custom_themes_directory() and not path.is_symlink(),
             "enabled": dungeons.theme_is_enabled(key, database)}
            for key, (path, template) in dungeons.discover_templates().items()
        ]
    except dungeons.TemplateError:
        mud_dungeon_themes = []
    mud_dungeon_removal = {theme["id"]: removal_preview(
        dungeons.theme_origin_keys(database, theme["id"]),
        dungeons.removal_origins(database, theme["id"]))
        for theme in mud_dungeon_themes if theme["custom"]}
    mud_daily_themes = [theme for theme in mud_dungeon_themes if theme["enabled"]]
    try:
        configured_daily_themes = set(json.loads(
            mud_daily_settings_row["eligible_theme_ids_json"] or "[]"
        ))
    except json.JSONDecodeError:
        configured_daily_themes = set()
    from .mud import ensure_daily_dungeon
    current_daily = ensure_daily_dungeon(database)
    database.commit()
    daily_failures = database.execute(
        "SELECT day_key,error_message,generated_at FROM mud_daily_dungeons "
        "WHERE status='failed' ORDER BY day_key DESC LIMIT 10"
    ).fetchall()
    generation_failures = database.execute(
        "SELECT theme_id,theme_version,seed,message,created_at "
        "FROM mud_dungeon_generation_log WHERE status='failure' ORDER BY id DESC LIMIT 10"
    ).fetchall()
    event_creation_groups = {row["group_id"] for row in database.execute("SELECT group_id FROM event_creation_groups")}
    from .modules import ALL_MODULES
    return render_template("settings.html", header_fonts=header_fonts,
                           available_modules=ALL_MODULES,
                           **arcade.settings_data(),
                           **tv.settings_data(),
                           header_taglines=header_taglines.configured(g.station),
                           site_themes=themes.options("site"), masthead_themes=themes.options("masthead"),
                           custom_themes=themes.custom_themes(),
                           station_icon_exists=_station_icon_path().is_file(),
                           station_midi_exists=_station_midi_path().is_file(),
                           event_creation_groups=event_creation_groups,
                           bands=bands, groups=groups, users=users,
                           library_categories=library_categories,
                           memberships=memberships, effective=effective,
                           broadcast_hosts=broadcast_hosts,
                           assignments=assignments, broadcast_sessions=broadcast_sessions,
                           rules_by_group=rules_by_group, invitations=invitations,
                           mud_portal=mud_portal,
                           mud_worlds_all=mud_worlds_all, mud_travel=mud_travel,
                           mud_world_list=mud_world_list,
                           mud_origins=mud_origins,
                           mud_skill_entries=mud_skill_entries,
                           mud_ability_entries=mud_ability_entries, mud_effect_entries=mud_effect_entries,
                           mud_skill_groups=mud_skills.GROUPS,
                           mud_starting_skill_choices=mud_skills.starting_choices(),
                           mud_world_origin_keys=mud_world_origin_keys,
                           mud_world_removal=mud_world_removal,
                           mud_dungeon_removal=mud_dungeon_removal,
                           mud_daily_settings=mud_daily_settings_row,
                           mud_dungeon_themes=mud_dungeon_themes,
                           mud_daily_themes=mud_daily_themes,
                           configured_daily_themes=configured_daily_themes,
                           current_daily=current_daily, daily_failures=daily_failures,
                           generation_failures=generation_failures,
                           format_frequency=format_frequency)


@bp.post("/sysop/library/categories")
@admin_required
def add_library_category():
    validate_csrf()
    name = " ".join(request.form.get("name", "").split())
    if not 2 <= len(name) <= 50:
        flash("Category names must be 2 to 50 characters.", "error")
    else:
        database = get_db()
        try:
            database.execute("INSERT INTO library_categories(name) VALUES(?)", (name,))
            database.commit()
            flash("Library category added.", "success")
        except sqlite3.IntegrityError:
            database.rollback()
            flash("That library category already exists.", "error")
    return redirect(url_for("station.settings") + "#library-service")


@bp.post("/sysop/library/categories/<int:category_id>/availability")
@admin_required
def set_library_category_availability(category_id):
    validate_csrf()
    enabled = request.form.get("enabled")
    if enabled not in {"0", "1"}:
        abort(400)
    database = get_db()
    cursor = database.execute(
        "UPDATE library_categories SET enabled=? WHERE id=?", (int(enabled), category_id)
    )
    if not cursor.rowcount:
        abort(404)
    database.commit()
    flash("Library category availability updated.", "success")
    return redirect(url_for("station.settings") + "#library-service")


def _save_radio_bands():
    database = get_db()
    existing = database.execute("SELECT id,tx_permission FROM radio_bands ORDER BY id").fetchall()
    proposed = []
    error = None
    for row in existing:
        band_id = row["id"]
        name = request.form.get(f"band_{band_id}_name", "").strip().upper()
        start = parse_frequency(request.form.get(f"band_{band_id}_start"))
        end = parse_frequency(request.form.get(f"band_{band_id}_end"))
        if not 2 <= len(name) <= 40:
            error = "Band names must be between 2 and 40 characters."
        elif start is None or end is None or start > end:
            error = "Each radio band requires a valid start and end frequency."
        if error:
            break
        supports_voice = request.form.get(f"band_{band_id}_live_voice") == "on"
        supports_cw = request.form.get(f"band_{band_id}_cw") == "on"
        supports_sstv = request.form.get(f"band_{band_id}_sstv") == "on"
        supports_broadcasts = request.form.get(f"band_{band_id}_hosted_broadcasts") == "on"
        allow_open = request.form.get(f"band_{band_id}_open_broadcasts") == "on"
        proposed.append((start, end, name, row["tx_permission"], supports_cw, supports_sstv, supports_voice,
                         supports_broadcasts, allow_open, band_id))
    ordered = sorted(proposed)
    if not error and any(left[1] >= right[0] for left, right in zip(ordered, ordered[1:])):
        error = "Radio band allocations cannot overlap."
    if error:
        flash(error, "error")
    else:
        database.executemany(
            "UPDATE radio_bands SET start_khz=?,end_khz=?,name=?,tx_permission=?,"
            "supports_cw=?,supports_sstv=?,supports_live_voice=?,"
            "supports_hosted_broadcasts=?,allow_open_broadcasts=? WHERE id=?",
            proposed,
        )
        database.commit()
        reconcile_radio_permissions()
        flash("Radio spectrum allocations updated.", "success")
    return redirect(url_for("station.settings") + "#radio-spectrum")


@bp.post("/sysop/radio/users/<int:user_id>/broadcast-frequency")
@admin_required
def save_broadcast_frequency(user_id):
    validate_csrf()
    database = get_db()
    user = database.execute(
        "SELECT username,callsign FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("station.settings") + "#broadcast-frequencies")
    hosted = current_app.extensions["hosted_broadcasts"]
    if hosted.active_user(user_id):
        flash("Stop or pause this user's active broadcast before changing its assignment.", "error")
        return redirect(url_for("station.settings") + "#broadcast-frequencies")
    raw = request.form.get("frequency", "").strip()
    if not raw:
        database.execute("DELETE FROM radio_broadcast_assignments WHERE user_id=?", (user_id,))
        database.commit()
        hosted.stop(user_id, "assignment_revoked")
        flash(
            f"{user['username']} - [{user['callsign']}] broadcast assignment revoked.",
            "success",
        )
        return redirect(url_for("station.settings") + "#broadcast-frequencies")
    frequency = parse_frequency(raw)
    band = next((row for row in database.execute("SELECT * FROM radio_bands")
                 if row["start_khz"] <= (frequency or 0) <= row["end_khz"]), None)
    if frequency is None or not band or not band["supports_hosted_broadcasts"]:
        flash("Choose a valid frequency inside a band that supports hosted broadcasts.", "error")
    elif database.execute(
            "SELECT 1 FROM radio_automated_broadcasts WHERE frequency_khz=?", (frequency,)).fetchone():
        flash("That frequency is reserved for an automated broadcast.", "error")
    elif database.execute(
            "SELECT 1 FROM radio_external_broadcasts WHERE frequency_khz=?", (frequency,)).fetchone():
        flash("That frequency is reserved for an external broadcast.", "error")
    elif database.execute(
            "SELECT 1 FROM radio_automation_rules WHERE frequency_khz=?", (frequency,)).fetchone():
        flash("That frequency is reserved for program automation.", "error")
    elif not can_host_broadcast(user_id, frequency):
        flash("That user needs effective FULL access and HOST BROADCASTS permission there.", "error")
    else:
        try:
            database.execute(
                "INSERT INTO radio_broadcast_assignments(user_id,frequency_khz) VALUES(?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET frequency_khz=excluded.frequency_khz,updated_at=CURRENT_TIMESTAMP",
                (user_id, frequency),
            )
            database.commit()
            hosted.stop(user_id, "assignment_changed")
            flash(
                f"{user['username']} - [{user['callsign']}] assigned "
                f"{format_frequency(frequency)} MHz.",
                "success",
            )
        except sqlite3.IntegrityError:
            flash("That frequency is already assigned to another broadcaster.", "error")
    return redirect(url_for("station.settings") + "#broadcast-frequencies")


@bp.post("/sysop/radio/users/<int:user_id>/broadcast-control")
@admin_required
def control_broadcast(user_id):
    validate_csrf()
    action = request.form.get("action")
    hosted = current_app.extensions["hosted_broadcasts"]
    try:
        changed = (hosted.pause(user_id) if action == "pause" else
                   hosted.resume(user_id) if action == "resume" else
                   hosted.stop(user_id, "sysop_stopped") if action == "stop" else
                   hosted.set_indefinite(user_id, request.form.get("enabled") == "1")
                   if action == "indefinite" else False)
    except RuntimeError as problem:
        flash(str(problem), "error")
        changed = False
    flash("Broadcast control updated." if changed else "No matching broadcast session.",
          "success" if changed else "error")
    return redirect(url_for("station.settings") + "#broadcast-frequencies")


def _radio_permissions_redirect(group_id=None):
    anchor = f"radio-group-{group_id}" if group_id is not None else "radio-permissions"
    return redirect(url_for("station.settings") + f"#{anchor}")


@bp.post("/sysop/radio/groups")
@admin_required
def create_radio_group():
    validate_csrf()
    name = request.form.get("name", "").strip().upper()
    if not 2 <= len(name) <= 40 or name == "DEFAULT":
        flash("Group names must be 2–40 characters and cannot use DEFAULT.", "error")
    else:
        try:
            get_db().execute(
                "INSERT INTO radio_permission_groups(name,host_broadcasts,moderate_imageboard) VALUES(?,?,?)",
                (name, request.form.get("host_broadcasts") == "on",
                 request.form.get("moderate_imageboard") == "on"),
            )
            get_db().commit()
            flash(f"Radio group {name} created.", "success")
        except sqlite3.IntegrityError:
            flash("A radio group with that name already exists.", "error")
    return _radio_permissions_redirect()


@bp.post("/sysop/radio/groups/<int:group_id>")
@admin_required
def rename_radio_group(group_id):
    validate_csrf()
    database = get_db()
    group = database.execute("SELECT * FROM radio_permission_groups WHERE id=?", (group_id,)).fetchone()
    if group is None:
        flash("Radio group not found.", "error")
    elif group["is_default"]:
        flash("DEFAULT is permanent and cannot be renamed.", "error")
    else:
        name = request.form.get("name", "").strip().upper()
        if not 2 <= len(name) <= 40 or name == "DEFAULT":
            flash("Group names must be 2–40 characters and cannot use DEFAULT.", "error")
        else:
            try:
                database.execute("UPDATE radio_permission_groups SET name=? WHERE id=?", (name, group_id))
                database.commit()
                flash("Radio group renamed.", "success")
            except sqlite3.IntegrityError:
                flash("A radio group with that name already exists.", "error")
    return _radio_permissions_redirect()


@bp.post("/sysop/radio/groups/<int:group_id>/capabilities")
@admin_required
def save_radio_group_capabilities(group_id):
    validate_csrf()
    database = get_db()
    group = database.execute(
        "SELECT name FROM radio_permission_groups WHERE id=?", (group_id,)
    ).fetchone()
    if group is None:
        flash("Radio group not found.", "error")
    else:
        enabled = request.form.get("host_broadcasts") == "on"
        imageboard_enabled = request.form.get("moderate_imageboard") == "on"
        database.execute(
            "UPDATE radio_permission_groups SET host_broadcasts=?,moderate_imageboard=? WHERE id=?",
            (enabled, imageboard_enabled, group_id),
        )
        database.commit()
        reconcile_radio_permissions()
        state = "enabled" if enabled else "disabled"
        flash(f"{group['name']}: capabilities saved (HOST BROADCASTS {state}).", "success")
    return _radio_permissions_redirect(group_id)


@bp.post("/sysop/radio/groups/<int:group_id>/delete")
@admin_required
def delete_radio_group(group_id):
    validate_csrf()
    database = get_db()
    group = database.execute("SELECT * FROM radio_permission_groups WHERE id=?", (group_id,)).fetchone()
    if group is None:
        flash("Radio group not found.", "error")
    elif group["is_default"]:
        flash("DEFAULT cannot be deleted.", "error")
    else:
        database.execute("DELETE FROM radio_permission_groups WHERE id=?", (group_id,))
        database.commit()
        reconcile_radio_permissions()
        flash("Radio group and its assignments were removed.", "success")
    return _radio_permissions_redirect()


@bp.post("/sysop/radio/groups/<int:group_id>/rules")
@admin_required
def save_radio_rule(group_id):
    validate_csrf()
    database = get_db()
    group = database.execute(
        "SELECT name FROM radio_permission_groups WHERE id=?", (group_id,)
    ).fetchone()
    if group is None:
        flash("Radio group not found.", "error")
        return _radio_permissions_redirect()
    scope = request.form.get("scope_type", "")
    permission = request.form.get("permission", "")
    band_id = start = end = None
    if scope == "band":
        band_id = request.form.get("band_id", type=int)
        band = database.execute("SELECT name FROM radio_bands WHERE id=?", (band_id,)).fetchone()
        valid_scope = band is not None
    elif scope in {"range", "frequency"}:
        start = parse_frequency(request.form.get("start"))
        end = start if scope == "frequency" else parse_frequency(request.form.get("end"))
        valid_scope = start is not None and end is not None and start <= end
    else:
        valid_scope = False
    if not valid_scope or permission not in {"FULL", "LISTEN", "DENIED", "UNSET"}:
        flash("Choose a valid band, frequency or range and permission.", "error")
        return _radio_permissions_redirect(group_id)
    if scope == "band":
        match_sql, match_values = "scope_type='band' AND band_id=?", (band_id,)
        scope_label = f"band {band['name']}"
    else:
        match_sql, match_values = "scope_type=? AND start_khz=? AND end_khz=?", (scope, start, end)
        scope_label = (f"frequency {format_frequency(start)} MHz" if scope == "frequency" else
                       f"range {format_frequency(start)}–{format_frequency(end)} MHz")
    existing = database.execute(
        f"SELECT id FROM radio_permission_rules WHERE group_id=? AND {match_sql} ORDER BY id DESC LIMIT 1",
        (group_id,) + match_values,
    ).fetchone()
    if permission == "UNSET":
        database.execute(
            f"DELETE FROM radio_permission_rules WHERE group_id=? AND {match_sql}",
            (group_id,) + match_values,
        )
        action = "removed"
    elif existing:
        database.execute(
            f"UPDATE radio_permission_rules SET permission=? WHERE group_id=? AND {match_sql}",
            (permission, group_id) + match_values,
        )
        action = "updated"
    else:
        database.execute(
            "INSERT INTO radio_permission_rules(group_id,scope_type,band_id,start_khz,end_khz,permission) "
            "VALUES(?,?,?,?,?,?)", (group_id, scope, band_id, start, end, permission),
        )
        action = "added"
    database.commit()
    reconcile_radio_permissions()
    flash(f"{group['name']}: {scope_label} rule {action} ({permission}).", "success")
    return _radio_permissions_redirect(group_id)


@bp.post("/sysop/radio/rules/<int:rule_id>/delete")
@admin_required
def delete_radio_rule(rule_id):
    validate_csrf()
    database = get_db()
    database.execute("DELETE FROM radio_permission_rules WHERE id=?", (rule_id,))
    database.commit()
    reconcile_radio_permissions()
    flash("Radio permission rule removed (UNSET).", "success")
    return _radio_permissions_redirect()


@bp.post("/sysop/radio/users/<int:user_id>")
@admin_required
def save_radio_memberships(user_id):
    validate_csrf()
    database = get_db()
    if database.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
        flash("User not found.", "error")
        return _radio_permissions_redirect()
    selected = {int(value) for value in request.form.getlist("group_id") if value.isdigit()}
    valid = {row["id"] for row in database.execute(
        "SELECT id FROM radio_permission_groups WHERE is_default=0"
    )}
    default_id = database.execute(
        "SELECT id FROM radio_permission_groups WHERE is_default=1"
    ).fetchone()["id"]
    database.execute("DELETE FROM radio_group_memberships WHERE user_id=? AND group_id<>?",
                     (user_id, default_id))
    database.executemany(
        "INSERT OR IGNORE INTO radio_group_memberships(user_id,group_id) VALUES(?,?)",
        [(user_id, group_id) for group_id in selected & valid],
    )
    database.execute(
        "INSERT OR IGNORE INTO radio_group_memberships(user_id,group_id) VALUES(?,?)",
        (user_id, default_id),
    )
    database.commit()
    reconcile_radio_permissions(user_id)
    flash("User radio groups updated.", "success")
    return _radio_permissions_redirect()

