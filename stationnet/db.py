"""SQLite persistence."""
import sqlite3
from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS station (
 id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL, description TEXT NOT NULL,
 callsign TEXT NOT NULL UNIQUE, registration_open INTEGER NOT NULL DEFAULT 1,
 guest_access INTEGER NOT NULL DEFAULT 1, landing_message TEXT NOT NULL DEFAULT '',
 brand_tagline TEXT NOT NULL DEFAULT 'H7/dS! // YOUR INDEPENDENT COMMUNITY STATION //',
 upload_permission TEXT NOT NULL DEFAULT 'members',
 file_storage_limit_mb INTEGER NOT NULL DEFAULT 100,
 file_size_limit_mb INTEGER NOT NULL DEFAULT 10,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
 callsign TEXT NOT NULL COLLATE NOCASE UNIQUE, password_hash TEXT NOT NULL,
 is_admin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 tagline TEXT NOT NULL DEFAULT '', about TEXT NOT NULL DEFAULT '', birthday TEXT,
 gender TEXT NOT NULL DEFAULT '', last_seen TEXT, connection_id TEXT);
CREATE TABLE IF NOT EXISTS posts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 parent_id INTEGER REFERENCES posts(id) ON DELETE CASCADE, title TEXT, body TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK((parent_id IS NULL AND title IS NOT NULL) OR parent_id IS NOT NULL));
CREATE INDEX IF NOT EXISTS idx_posts_parent ON posts(parent_id);
CREATE TABLE IF NOT EXISTS station_chat_messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 body TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_station_chat_created ON station_chat_messages(id);
CREATE TABLE IF NOT EXISTS mail (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 subject TEXT NOT NULL, body TEXT NOT NULL,
 is_read INTEGER NOT NULL DEFAULT 0,
 sender_deleted INTEGER NOT NULL DEFAULT 0,
 recipient_deleted INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_mail_recipient ON mail(recipient_id,recipient_deleted,created_at);
CREATE INDEX IF NOT EXISTS idx_mail_sender ON mail(sender_id,sender_deleted,created_at);
CREATE TABLE IF NOT EXISTS archive_files (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 uploader_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 display_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE,
 category TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS announcements (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 title TEXT NOT NULL, body TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
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
    _migrate_community_fields(database)
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
            "DEFAULT 'H7/dS! // YOUR INDEPENDENT COMMUNITY STATION //'"
        )
    database.execute(
        "UPDATE station SET brand_tagline=? WHERE brand_tagline=?",
        (
            "H7/dS! // YOUR INDEPENDENT COMMUNITY STATION //",
            "H7/dS! // YOUR INDEPENDENT COMMUNITY NODE //",
        ),
    )

def _migrate_community_fields(database):
    """Add Milestone 2 settings and profile fields to existing stations."""
    station_columns = {row["name"] for row in database.execute("PRAGMA table_info(station)")}
    additions = {
        "upload_permission": "TEXT NOT NULL DEFAULT 'members'",
        "file_storage_limit_mb": "INTEGER NOT NULL DEFAULT 100",
        "file_size_limit_mb": "INTEGER NOT NULL DEFAULT 10",
    }
    for name, declaration in additions.items():
        if name not in station_columns:
            database.execute(f"ALTER TABLE station ADD COLUMN {name} {declaration}")

    user_columns = {row["name"] for row in database.execute("PRAGMA table_info(users)")}
    user_additions = {
        "tagline": "TEXT NOT NULL DEFAULT ''",
        "about": "TEXT NOT NULL DEFAULT ''",
        "birthday": "TEXT",
        "gender": "TEXT NOT NULL DEFAULT ''",
        "last_seen": "TEXT",
        "connection_id": "TEXT",
    }
    for name, declaration in user_additions.items():
        if name not in user_columns:
            database.execute(f"ALTER TABLE users ADD COLUMN {name} {declaration}")

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

