"""First-run setup and local authentication."""
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from .db import get_db, get_station
from .security import check_password, generate_station_callsign, generate_user_callsign, hash_password, valid_username, validate_csrf, validate_password
from .realtime import ONLINE_SECONDS, utc_now

bp = Blueprint("auth", __name__)


def generate_temporary_password():
    """Return a random password that satisfies the station password policy."""
    return secrets.token_urlsafe(18)


def reset_password_for_user(database, user):
    """Reset an account and privately notify every SYSOP in one transaction."""
    sysops = database.execute(
        "SELECT id FROM users WHERE is_admin=1 ORDER BY id"
    ).fetchall()
    if not sysops:
        return False

    temporary_password = generate_temporary_password()
    subject = f"SYSTEM: Password reset for {user['username']}"
    body = (
        "SYSTEM-GENERATED PASSWORD RESET\n\n"
        f"User: {user['username']} - [{user['callsign']}]\n"
        f"Temporary password: {temporary_password}\n\n"
        "Give this password to the user manually. After signing in, they should "
        "change it from Edit Profile."
    )
    try:
        database.execute("BEGIN IMMEDIATE")
        database.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (hash_password(temporary_password), user["id"]),
        )
        for sysop in sysops:
            cursor = database.execute(
                "INSERT INTO mail(sender_id,recipient_id,subject,body,sender_deleted) "
                "VALUES(?,?,?,?,1)",
                (user["id"], sysop["id"], subject, body),
            )
            database.execute(
                "UPDATE mail SET thread_id=id WHERE id=?", (cursor.lastrowid,)
            )
        database.commit()
    except Exception:
        database.rollback()
        raise
    return True


def invitation_digest(code):
    """Return a keyed, non-reversible lookup value for an invitation code."""
    key = current_app.config["SECRET_KEY"]
    if isinstance(key, str):
        key = key.encode("utf-8")
    return hmac.new(key, code.encode("utf-8"), hashlib.sha256).hexdigest()


@bp.after_request
def protect_auth_secrets(response):
    if request.endpoint == "auth.register":
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response

def load_logged_in_user():
    user_id = session.get("user_id")
    g.user = None if user_id is None else get_db().execute("SELECT id,username,callsign,is_admin,created_at FROM users WHERE id=?", (user_id,)).fetchone()

def establish_connection(user_id):
    connection_id = secrets.token_urlsafe(18)
    session["connection_id"] = connection_id
    database = get_db()
    database.execute(
        "UPDATE users SET connection_id=?,last_seen=? WHERE id=?",
        (connection_id, utc_now(), user_id),
    )
    database.commit()

def touch_presence():
    connection_id = session.get("connection_id")
    if not connection_id:
        establish_connection(g.user["id"])
        return
    database = get_db()
    presence = database.execute(
        "SELECT connection_id,last_seen FROM users WHERE id=?", (g.user["id"],)
    ).fetchone()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ONLINE_SECONDS)).replace(microsecond=0).isoformat()
    connection_is_stale = (
        presence is None
        or presence["connection_id"] != connection_id
        or not presence["last_seen"]
        or presence["last_seen"] < cutoff
    )
    if connection_is_stale:
        # A remembered session can outlive its presence heartbeat (or be
        # superseded by a login elsewhere).  It must get a new connection
        # identity before serving a page, otherwise its conditional heartbeat
        # update cannot bring it back online.
        if presence and presence["connection_id"]:
            old_connection_id = presence["connection_id"]
            current_app.extensions["direct_chat"].disconnect(g.user["id"], old_connection_id)
            current_app.extensions["radio"].disconnect(old_connection_id)
        establish_connection(g.user["id"])
        return
    database.execute(
        "UPDATE users SET last_seen=? WHERE id=? AND connection_id=?",
        (utc_now(), g.user["id"], connection_id),
    )
    database.commit()

def login_required(view):
    @wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            flash("Authentication required to transmit.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view(**kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(**kwargs):
        if g.user is None or not g.user["is_admin"]:
            flash("SYSOP authorization required.", "error")
            return redirect(url_for("station.terminal"))
        return view(**kwargs)
    return wrapped

@bp.route("/setup", methods=("GET", "POST"))
def setup():
    if request.method == "POST":
        validate_csrf()
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        error = None
        if not 2 <= len(name) <= 80: error = "Station name must be between 2 and 80 characters."
        elif len(description) > 500: error = "Station description cannot exceed 500 characters."
        elif not valid_username(username): error = "Username must be 3–24 letters, numbers, underscores, or hyphens."
        elif password != request.form.get("confirm_password"): error = "Passwords do not match."
        else: error = validate_password(password)
        if error:
            flash(error, "error")
        else:
            database = get_db(); callsign = generate_station_callsign()
            try:
                database.execute("BEGIN IMMEDIATE")
                registration_mode = request.form.get("registration_mode")
                if registration_mode not in {"open", "private", "disabled"}:
                    registration_mode = (
                        "open" if request.form.get("registration_open") == "open" else "disabled"
                    )
                database.execute(
                    "INSERT INTO station(id,name,description,callsign,registration_open,"
                    "registration_mode,guest_access) VALUES(1,?,?,?,?,?,1)",
                    (name, description, callsign, registration_mode == "open", registration_mode),
                )
                user_callsign = generate_user_callsign(database)
                cursor = database.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,1)", (username, user_callsign, hash_password(password)))
                database.execute(
                    "INSERT OR IGNORE INTO radio_group_memberships(user_id,group_id) "
                    "SELECT ?,id FROM radio_permission_groups WHERE name='OPERATORS'",
                    (cursor.lastrowid,),
                )
                database.commit(); session.clear(); session["user_id"] = cursor.lastrowid; establish_connection(cursor.lastrowid)
                flash(
                    f"Station initialized. Operator: {username} - [{user_callsign}]",
                    "success",
                )
                return redirect(url_for("station.terminal"))
            except sqlite3.IntegrityError:
                database.rollback(); flash("Station setup could not be saved. Please retry.", "error")
    return render_template("setup.html")

@bp.route("/register", methods=("GET", "POST"))
def register():
    station = get_station()
    if station["registration_mode"] == "disabled":
        flash("This station is not accepting registrations.", "warning")
        return redirect(url_for("auth.login"))
    invite_code = request.values.get("invite", "").strip()
    if request.method == "POST":
        validate_csrf(); username = request.form.get("username", "").strip(); password = request.form.get("password", "")
        error = None
        if not valid_username(username): error = "Username must be 3–24 letters, numbers, underscores, or hyphens."
        elif password != request.form.get("confirm_password"): error = "Passwords do not match."
        else: error = validate_password(password)
        if error: flash(error, "error")
        else:
            database = get_db(); callsign = generate_user_callsign(database)
            try:
                database.execute("BEGIN IMMEDIATE")
                mode = database.execute(
                    "SELECT registration_mode FROM station WHERE id=1"
                ).fetchone()["registration_mode"]
                if mode == "disabled":
                    database.rollback()
                    flash("This station is not accepting registrations.", "warning")
                    return redirect(url_for("auth.login"))
                invitation = None
                if mode == "private":
                    invitation = database.execute(
                        "SELECT id FROM registration_invites WHERE token_hash=? "
                        "AND revoked_at IS NULL AND used_at IS NULL",
                        (invitation_digest(invite_code),),
                    ).fetchone()
                    if invitation is None:
                        database.rollback()
                        flash("That invitation is invalid or no longer available.", "error")
                        return render_template("register.html", invite_code=invite_code)
                cursor = database.execute(
                    "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
                    (username, callsign, hash_password(password)),
                )
                if invitation is not None:
                    redeemed = database.execute(
                        "UPDATE registration_invites SET used_by=?,used_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND revoked_at IS NULL AND used_at IS NULL",
                        (cursor.lastrowid, invitation["id"]),
                    )
                    if redeemed.rowcount != 1:
                        database.rollback()
                        flash("That invitation is invalid or no longer available.", "error")
                        return render_template("register.html", invite_code=invite_code)
                database.commit(); session.clear(); session["user_id"] = cursor.lastrowid; establish_connection(cursor.lastrowid)
                flash(f"Account linked: {username} - [{callsign}].", "success")
                return redirect(url_for("station.terminal"))
            except sqlite3.IntegrityError:
                database.rollback()
                flash("That username is already registered.", "error")
    return render_template("register.html", invite_code=invite_code)

@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        validate_csrf(); username = request.form.get("username", "").strip()
        database = get_db()
        user = database.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()
        if request.form.get("action") == "forgot_password":
            if user is None:
                flash("No registered user has that username.", "error")
                return render_template("login.html", login_failed=True)
            if not reset_password_for_user(database, user):
                flash("No SYSOP is available to receive the reset message.", "error")
                return render_template("login.html", login_failed=True)
            flash(
                "A temporary password was generated and sent privately to the SYSOP(s). "
                "Contact a SYSOP to receive it.",
                "success",
            )
            return redirect(url_for("auth.login"))

        password = request.form.get("password", "")
        if user is None or not check_password(user["password_hash"], password):
            flash("Invalid username or password.", "error")
            return render_template("login.html", login_failed=True)
        else:
            remember_me = request.form.get("remember_me") == "on"
            session.clear(); session.permanent = remember_me; session["user_id"] = user["id"]; establish_connection(user["id"])
            flash(
                f"Link established. Welcome, {user['username']} - [{user['callsign']}].",
                "success",
            )
            destination = request.args.get("next", "")
            if not destination.startswith("/") or destination.startswith("//"): destination = url_for("station.terminal")
            return redirect(destination)
    return render_template("login.html")

@bp.post("/logout")
def logout():
    validate_csrf()
    if g.user is not None:
        connection_id = session.get("connection_id")
        current_app.extensions["direct_chat"].disconnect(g.user["id"], connection_id)
        current_app.extensions["radio"].disconnect(connection_id)
        database = get_db()
        database.execute(
            "UPDATE mud_presence SET disconnected_at=? "
            "WHERE user_id=? AND connection_id=?",
            (utc_now(), g.user["id"], connection_id),
        )
        database.execute(
            "UPDATE users SET connection_id=NULL WHERE id=? AND connection_id=?",
            (g.user["id"], connection_id),
        )
        database.commit()
    session.clear(); flash("Connection closed.", "success")
    return redirect(url_for("station.index"))

