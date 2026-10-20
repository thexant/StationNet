"""SQLite persistence."""
import sqlite3
from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS station (
 id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL, description TEXT NOT NULL,
 callsign TEXT NOT NULL UNIQUE, registration_open INTEGER NOT NULL DEFAULT 1,
 guest_access INTEGER NOT NULL DEFAULT 1, landing_message TEXT NOT NULL DEFAULT '',
 brand_tagline TEXT NOT NULL DEFAULT 'H7/dS! // YOUR INDEPENDENT COMMUNITY NODE //',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
 callsign TEXT NOT NULL COLLATE NOCASE UNIQUE, password_hash TEXT NOT NULL,
 is_admin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS posts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 parent_id INTEGER REFERENCES posts(id) ON DELETE CASCADE, title TEXT, body TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK((parent_id IS NULL AND title IS NOT NULL) OR parent_id IS NOT NULL));
CREATE INDEX IF NOT EXISTS idx_posts_parent ON posts(parent_id);
"""

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA busy_timeout=5000")
    return g.db

def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()

def init_db():
    database = get_db()
    database.executescript(SCHEMA)
    _migrate_station_customization(database)
    _migrate_legacy_callsigns(database)
    database.commit()

def _migrate_station_customization(database):
    """Add customizable station copy to databases created by older versions."""
    columns = {
        row["name"] for row in database.execute("PRAGMA table_info(station)")
    }
    if "landing_message" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN landing_message TEXT NOT NULL DEFAULT ''"
        )
    if "brand_tagline" not in columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN brand_tagline TEXT NOT NULL "
            "DEFAULT 'H7/dS! // YOUR INDEPENDENT COMMUNITY NODE //'"
        )

def _migrate_legacy_callsigns(database):
    """Replace callsigns created under earlier, prefixed formats."""
    from .security import generate_user_callsign, valid_callsign

    station = database.execute("SELECT callsign FROM station WHERE id=1").fetchone()
    if station and not valid_callsign(station["callsign"]):
        database.execute(
            "UPDATE station SET callsign=? WHERE id=1",
            (generate_user_callsign(database),),
        )

    users = database.execute("SELECT id,callsign FROM users").fetchall()
    for user in users:
        if not valid_callsign(user["callsign"]):
            database.execute(
                "UPDATE users SET callsign=? WHERE id=?",
                (generate_user_callsign(database), user["id"]),
            )

def get_station():
    return get_db().execute("SELECT * FROM station WHERE id=1").fetchone()

def init_app(app):
    app.teardown_appcontext(close_db)

