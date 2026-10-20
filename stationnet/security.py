"""Password, CSRF, validation, and callsign helpers."""
import hashlib
import hmac
import re
import secrets
from flask import abort, request, session

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,24}$")
CALLSIGN_RE = re.compile(r"^[A-Z0-9-]{3,12}$")

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
    if not session.get("csrf_token") or not hmac.compare_digest(request.form.get("csrf_token", ""), session["csrf_token"]):
        abort(400, "Invalid or expired form token")

def normalize_callsign(value): return re.sub(r"[^A-Z0-9-]", "", value.upper())
def valid_username(value): return bool(USERNAME_RE.fullmatch(value))
def valid_callsign(value): return bool(CALLSIGN_RE.fullmatch(value))
def validate_password(value):
    if len(value) < 10: return "Password must contain at least 10 characters."
    if len(value) > 128: return "Password cannot exceed 128 characters."

def generate_station_callsign():
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(3)) + str(secrets.randbelow(90)+10)

def generate_user_callsign(database, station_callsign):
    prefix = re.sub(r"[^A-Z0-9]", "", station_callsign)[:4] or "NODE"
    for _ in range(100):
        value = f"{prefix}-{secrets.randbelow(9000)+1000}"
        if not database.execute("SELECT 1 FROM users WHERE callsign=?", (value,)).fetchone(): return value
    raise RuntimeError("Unable to allocate a unique callsign")
