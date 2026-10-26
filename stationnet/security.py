"""Password, CSRF, validation, and callsign helpers."""
import hashlib
import hmac
import re
import secrets
from flask import abort, request, session

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,24}$")
CALLSIGN_RE = re.compile(r"^[A-Z0-9]{3}-[A-Z0-9]{3}$")
CALLSIGN_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt:16384:8:1${salt.hex()}${digest.hex()}"

def check_password(stored, password):
    try:
        algorithm, salt, expected = stored.split("$")
        name, n, r, p = algorithm.split(":")
        if name != "scrypt": return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(expected)))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False

def ensure_csrf_token():
    if "csrf_token" not in session: session["csrf_token"] = secrets.token_urlsafe(24)
    return session["csrf_token"]

def validate_csrf():
    token = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    if not session.get("csrf_token") or not hmac.compare_digest(token, session["csrf_token"]):
        abort(400, "Invalid or expired form token")

def valid_username(value): return bool(USERNAME_RE.fullmatch(value))
def valid_callsign(value): return bool(CALLSIGN_RE.fullmatch(value))
def validate_password(value):
    if len(value) < 10: return "Password must contain at least 10 characters."
    if len(value) > 128: return "Password cannot exceed 128 characters."

def generate_station_callsign():
    """Return a random six-character alphanumeric callsign as XXX-XXX."""
    raw = "".join(secrets.choice(CALLSIGN_ALPHABET) for _ in range(6))
    return f"{raw[:3]}-{raw[3:]}"

def generate_user_callsign(database):
    for _ in range(100):
        value = generate_station_callsign()
        user_exists = database.execute("SELECT 1 FROM users WHERE callsign=?", (value,)).fetchone()
        station_exists = database.execute("SELECT 1 FROM station WHERE callsign=?", (value,)).fetchone()
        if not user_exists and not station_exists:
            return value
    raise RuntimeError("Unable to allocate a unique callsign")
