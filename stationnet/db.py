"""SQLite persistence."""
import sqlite3
from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS station (
 id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL, description TEXT NOT NULL,
 callsign TEXT NOT NULL UNIQUE, registration_open INTEGER NOT NULL DEFAULT 1,
 guest_access INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
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
    get_db().executescript(SCHEMA)
    get_db().commit()

def get_station():
    return get_db().execute("SELECT * FROM station WHERE id=1").fetchone()

def init_app(app):
    app.teardown_appcontext(close_db)

