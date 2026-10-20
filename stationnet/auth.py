"""First-run setup and local authentication."""
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from .db import get_db, get_station
from .security import check_password, generate_station_callsign, generate_user_callsign, hash_password, valid_username, validate_csrf, validate_password
from .realtime import ONLINE_SECONDS, utc_now

bp = Blueprint("auth", __name__)

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
    if (
        presence and presence["connection_id"] == connection_id
        and presence["last_seen"] and presence["last_seen"] < cutoff
    ):
        current_app.extensions["direct_chat"].disconnect(g.user["id"], connection_id)
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
                database.execute("INSERT INTO station(id,name,description,callsign,registration_open,guest_access) VALUES(1,?,?,?,?,1)", (name, description, callsign, request.form.get("registration_open") == "open"))
                user_callsign = generate_user_callsign(database)
                cursor = database.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,1)", (username, user_callsign, hash_password(password)))
                database.commit(); session.clear(); session["user_id"] = cursor.lastrowid; establish_connection(cursor.lastrowid)
                flash(f"Station initialized. Operator callsign: {user_callsign}", "success")
                return redirect(url_for("station.terminal"))
            except sqlite3.IntegrityError:
                database.rollback(); flash("Station setup could not be saved. Please retry.", "error")
    return render_template("setup.html")

@bp.route("/register", methods=("GET", "POST"))
def register():
    station = get_station()
    if not station["registration_open"]:
        flash("This station is not accepting registrations.", "warning")
        return redirect(url_for("auth.login"))
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
                cursor = database.execute("INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)", (username, callsign, hash_password(password)))
                database.commit(); session.clear(); session["user_id"] = cursor.lastrowid; establish_connection(cursor.lastrowid)
                flash(f"Account linked. Your callsign is {callsign}.", "success")
                return redirect(url_for("station.terminal"))
            except sqlite3.IntegrityError: flash("That username is already registered.", "error")
    return render_template("register.html")

@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        validate_csrf(); username = request.form.get("username", "").strip(); password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user is None or not check_password(user["password_hash"], password): flash("Invalid username or password.", "error")
        else:
            session.clear(); session["user_id"] = user["id"]; establish_connection(user["id"])
            flash(f"Link established. Welcome, {user['callsign']}.", "success")
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
        database = get_db()
        database.execute(
            "UPDATE users SET last_seen=NULL,connection_id=NULL WHERE id=? AND connection_id=?",
            (g.user["id"], connection_id),
        )
        database.commit()
    session.clear(); flash("Connection closed.", "success")
    return redirect(url_for("station.index"))

