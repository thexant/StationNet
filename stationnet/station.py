"""Welcome, workspace, and SYSOP settings."""
import secrets
import sqlite3
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from .auth import admin_required, invitation_digest
from .ansi import HEADER_FONTS, HEADER_FONT_NAMES
from .db import get_db
from .markdown import render_markdown
from .radio import (can_host_broadcast, effective_access, format_frequency,
                    parse_frequency, reconcile_radio_permissions)
from .security import validate_csrf
bp = Blueprint("station", __name__)

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
        broadcasts.sort(key=lambda item: (item["frequency_khz"], item["name"].casefold()))
        activity = {
            "bulletin": latest_bulletin,
            "reply": latest_reply,
            "chat": latest_chat,
            "file": newest_file,
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
    """Enable or disable the native MUD without deleting its persisted data."""
    validate_csrf()
    enabled = request.form.get("mud_enabled") == "on"
    database = get_db()
    database.execute("UPDATE station SET mud_enabled=? WHERE id=1", (enabled,))
    database.commit()
    flash(f"MUD service {'enabled' if enabled else 'disabled'}.", "success")
    return redirect(url_for("station.settings") + "#mud-service")


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

@bp.route("/sysop/settings", methods=("GET", "POST"))
@admin_required
def settings():
    if request.method == "POST":
        validate_csrf()
        if request.form.get("settings_section") == "radio":
            return _save_radio_bands()
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
        upload_permission = request.form.get("upload_permission", g.station["upload_permission"])
        storage_limit = request.form.get("file_storage_limit_mb", g.station["file_storage_limit_mb"], type=int)
        file_limit = request.form.get("file_size_limit_mb", g.station["file_size_limit_mb"], type=int)
        if not 2 <= len(name) <= 80: flash("Station name must be between 2 and 80 characters.", "error")
        elif len(description) > 500: flash("Station description cannot exceed 500 characters.", "error")
        elif len(landing_heading) > 100: flash("Landing message heading cannot exceed 100 characters.", "error")
        elif len(landing_message) > 4000: flash("Landing message cannot exceed 4000 characters.", "error")
        elif not 1 <= len(brand_tagline) <= 160: flash("Header tagline must be between 1 and 160 characters.", "error")
        elif header_font not in HEADER_FONT_NAMES: flash("Select a valid header style.", "error")
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
                "landing_message_enabled=?,brand_tagline=?,header_font=?,"
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
                    upload_permission,
                    storage_limit,
                    file_limit,
                ) + access_values,
            )
            database.commit(); flash("Station parameters updated.", "success")
            return redirect(url_for("station.settings"))
    database = get_db()
    header_fonts = HEADER_FONTS
    bands = database.execute("SELECT * FROM radio_bands ORDER BY start_khz").fetchall()
    groups = database.execute("SELECT * FROM radio_permission_groups ORDER BY is_default DESC,name").fetchall()
    rules = database.execute(
        "SELECT r.*,b.name band_name FROM radio_permission_rules r "
        "LEFT JOIN radio_bands b ON b.id=r.band_id ORDER BY r.group_id,r.id"
    ).fetchall()
    users = database.execute(
        "SELECT id,username,callsign,radio_quota_mb FROM users ORDER BY username COLLATE NOCASE"
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
    return render_template("settings.html", header_fonts=header_fonts,
                           bands=bands, groups=groups, users=users,
                           memberships=memberships, effective=effective,
                           broadcast_hosts=broadcast_hosts,
                           assignments=assignments, broadcast_sessions=broadcast_sessions,
                           rules_by_group=rules_by_group, invitations=invitations,
                           format_frequency=format_frequency)


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
                "INSERT INTO radio_permission_groups(name,host_broadcasts) VALUES(?,?)",
                (name, request.form.get("host_broadcasts") == "on"),
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
        database.execute(
            "UPDATE radio_permission_groups SET host_broadcasts=? WHERE id=?",
            (enabled, group_id),
        )
        database.commit()
        reconcile_radio_permissions()
        state = "enabled" if enabled else "disabled"
        flash(f"{group['name']}: HOST BROADCASTS {state}.", "success")
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

