"""First-run setup and local authentication."""
import sqlite3
from functools import wraps
from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from .db import get_db, get_station
from .security import check_password, generate_station_callsign, generate_user_callsign, hash_password, normalize_callsign, valid_callsign, valid_username, validate_csrf, validate_password

bp = Blueprint("auth", __name__)

def load_logged_in_user():
    user_id = session.get("user_id")
    g.user = None if user_id is None else get_db().execute("SELECT id,username,callsign,is_admin,created_at FROM users WHERE id=?", (user_id,)).fetchone()

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
        callsign = normalize_callsign(request.form.get("callsign", ""))
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        error = None
        if not 2 <= len(name) <= 80: error = "Station name must be between 2 and 80 characters."
        elif len(description) > 500: error = "Station description cannot exceed 500 characters."
        elif callsign and not valid_callsign(callsign): error = "Station callsign must be 3–12 letters, numbers, or hyphens."
        elif not valid_username(username): error = "Username must be 3–24 letters, numbers, underscores, or hyphens."
        elif password != request.form.get("confirm_password"): error = "Passwords do not match."
        else: error = validate_password(password)
        if error:
            flash(error, "error")
        else:
            database = get_db(); callsign = callsign or generate_station_callsign()
            try:
                database.execute("BEGIN IMMEDIATE")
                database.execute("INSERT INTO station(id,name,description,callsign,registration_open,guest_access) VALUES(1,?,?,?,?,1)", (name, description, callsign, request.form.get("registration_open") == "open"))
                user_callsign = generate_user_callsign(database, callsign)
                cursor = database.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,1)", (username, user_callsign, hash_password(password)))
                database.commit(); session.clear(); session["user_id"] = cursor.lastrowid
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
            database = get_db(); callsign = generate_user_callsign(database, station["callsign"])
            try:
                cursor = database.execute("INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)", (username, callsign, hash_password(password)))
                database.commit(); session.clear(); session["user_id"] = cursor.lastrowid
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
            session.clear(); session["user_id"] = user["id"]
            flash(f"Link established. Welcome, {user['callsign']}.", "success")
            destination = request.args.get("next", "")
            if not destination.startswith("/") or destination.startswith("//"): destination = url_for("station.terminal")
            return redirect(destination)
    return render_template("login.html")

@bp.post("/logout")
def logout():
    validate_csrf(); session.clear(); flash("Connection closed.", "success")
    return redirect(url_for("station.index"))

