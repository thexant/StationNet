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
 radio_default_quota_mb INTEGER NOT NULL DEFAULT 100,
 radio_broadcast_limit_minutes INTEGER NOT NULL DEFAULT 120,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
 callsign TEXT NOT NULL COLLATE NOCASE UNIQUE, password_hash TEXT NOT NULL,
 is_admin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 tagline TEXT NOT NULL DEFAULT '', about TEXT NOT NULL DEFAULT '', birthday TEXT,
 gender TEXT NOT NULL DEFAULT '', last_seen TEXT, connection_id TEXT,
 radio_quota_mb INTEGER);
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
CREATE TABLE IF NOT EXISTS radio_bands (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL,
 start_khz INTEGER NOT NULL, end_khz INTEGER NOT NULL,
 supports_cw INTEGER NOT NULL DEFAULT 0 CHECK(supports_cw IN (0,1)),
 supports_sstv INTEGER NOT NULL DEFAULT 0 CHECK(supports_sstv IN (0,1)),
 supports_live_voice INTEGER NOT NULL DEFAULT 0 CHECK(supports_live_voice IN (0,1)),
 supports_hosted_broadcasts INTEGER NOT NULL DEFAULT 0 CHECK(supports_hosted_broadcasts IN (0,1)),
 allow_open_broadcasts INTEGER NOT NULL DEFAULT 0 CHECK(allow_open_broadcasts IN (0,1)),
 tx_permission TEXT NOT NULL DEFAULT 'members'
 CHECK(tx_permission IN ('members','admins','receive')),
 CHECK(start_khz > 0 AND end_khz >= start_khz));
CREATE TABLE IF NOT EXISTS radio_permission_groups (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
 is_default INTEGER NOT NULL DEFAULT 0 CHECK(is_default IN (0,1)),
 host_broadcasts INTEGER NOT NULL DEFAULT 0 CHECK(host_broadcasts IN (0,1)));
CREATE UNIQUE INDEX IF NOT EXISTS idx_radio_default_group
 ON radio_permission_groups(is_default) WHERE is_default=1;
CREATE TABLE IF NOT EXISTS radio_group_memberships (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 group_id INTEGER NOT NULL REFERENCES radio_permission_groups(id) ON DELETE CASCADE,
 PRIMARY KEY(user_id,group_id));
CREATE TABLE IF NOT EXISTS radio_permission_rules (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 group_id INTEGER NOT NULL REFERENCES radio_permission_groups(id) ON DELETE CASCADE,
 scope_type TEXT NOT NULL CHECK(scope_type IN ('band','range','frequency')),
 band_id INTEGER REFERENCES radio_bands(id) ON DELETE CASCADE,
 start_khz INTEGER, end_khz INTEGER,
 permission TEXT NOT NULL CHECK(permission IN ('FULL','LISTEN','DENIED')),
 CHECK((scope_type='band' AND band_id IS NOT NULL AND start_khz IS NULL AND end_khz IS NULL)
    OR (scope_type IN ('range','frequency') AND band_id IS NULL AND start_khz IS NOT NULL
        AND end_khz IS NOT NULL AND start_khz>0 AND end_khz>=start_khz)));
CREATE INDEX IF NOT EXISTS idx_radio_memberships_user ON radio_group_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_radio_rules_group ON radio_permission_rules(group_id);
CREATE TABLE IF NOT EXISTS radio_audio_files (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 display_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE,
 media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes>0),
 duration_seconds REAL NOT NULL DEFAULT 180 CHECK(duration_seconds>0),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_radio_audio_owner ON radio_audio_files(owner_id,id);
CREATE TABLE IF NOT EXISTS radio_playlists (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL COLLATE NOCASE, loop_enabled INTEGER NOT NULL DEFAULT 0
 CHECK(loop_enabled IN (0,1)), shuffle_enabled INTEGER NOT NULL DEFAULT 0
 CHECK(shuffle_enabled IN (0,1)), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(owner_id,name));
CREATE INDEX IF NOT EXISTS idx_radio_playlists_owner ON radio_playlists(owner_id,id);
CREATE TABLE IF NOT EXISTS radio_playlist_tracks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 playlist_id INTEGER NOT NULL REFERENCES radio_playlists(id) ON DELETE CASCADE,
 audio_file_id INTEGER REFERENCES radio_audio_files(id) ON DELETE SET NULL,
 display_name TEXT NOT NULL, position INTEGER NOT NULL CHECK(position>=0));
CREATE INDEX IF NOT EXISTS idx_radio_playlist_tracks
 ON radio_playlist_tracks(playlist_id,position,id);
CREATE TABLE IF NOT EXISTS radio_broadcast_assignments (
 user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 frequency_khz INTEGER NOT NULL UNIQUE CHECK(frequency_khz BETWEEN 100 AND 999990),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS application_storage (
 application_slug TEXT NOT NULL,
 storage_scope TEXT NOT NULL CHECK(storage_scope IN ('user','shared')),
 owner_key TEXT NOT NULL,
 storage_key TEXT NOT NULL,
 value_json TEXT NOT NULL,
 size_bytes INTEGER NOT NULL CHECK(size_bytes>=0),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(application_slug,storage_scope,owner_key,storage_key));
CREATE INDEX IF NOT EXISTS idx_application_storage_namespace
 ON application_storage(application_slug,storage_scope,owner_key,storage_key);
CREATE TABLE IF NOT EXISTS installed_applications (
 slug TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 description TEXT NOT NULL,
 author TEXT NOT NULL,
 version TEXT NOT NULL,
 category TEXT NOT NULL CHECK(category IN ('Games','Apps')),
 entrypoint TEXT NOT NULL,
 installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS application_states (
 application_slug TEXT PRIMARY KEY,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TRIGGER IF NOT EXISTS radio_default_membership_after_user_insert
AFTER INSERT ON users
BEGIN
 INSERT OR IGNORE INTO radio_group_memberships(user_id,group_id)
 SELECT NEW.id,id FROM radio_permission_groups WHERE is_default=1;
END;
"""

DEFAULT_RADIO_BANDS = (
    (1, "GENERAL COMMUNICATION", 145000, 432990, "members", 0, 0, 1, 0, 0),
    (2, "BROADCASTS", 7000, 13990, "admins", 0, 0, 0, 1, 0),
    (3, "MORSE / CW", 14000, 27990, "members", 1, 0, 0, 0, 0),
    (4, "DATA TRANSMISSIONS", 28000, 144990, "members", 0, 1, 0, 0, 0),
    (5, "EXPERIMENTAL / UNRESTRICTED", 433000, 999990, "members", 1, 1, 0, 0, 1),
)

NARROW_DEFAULT_RADIO_BANDS = (
    (1, "GENERAL COMMUNICATION", 145000, 145990, "members"),
    (2, "BROADCASTS", 7000, 7990, "admins"),
    (3, "MORSE / CW", 14000, 14990, "members"),
    (4, "DATA TRANSMISSIONS", 28000, 28990, "members"),
    (5, "EXPERIMENTAL / UNRESTRICTED", 433000, 433990, "members"),
)

LEGACY_DEFAULT_RADIO_BANDS = (
    (1, "GENERAL COMMUNICATION", 144000, 144399, "members"),
    (2, "BROADCASTS", 144400, 144599, "admins"),
    (3, "MORSE / CW", 144600, 144799, "members"),
    (4, "DATA TRANSMISSIONS", 144800, 144999, "members"),
    (5, "EXPERIMENTAL / UNRESTRICTED", 145000, 145999, "members"),
)

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
    _migrate_hosted_broadcast_fields(database)
    _migrate_legacy_callsigns(database)
    _migrate_factory_radio_bands(database)
    _align_radio_bands_to_display_precision(database)
    added_band_capabilities, added_signal_capabilities, added_group_capability = \
        _migrate_radio_capability_columns(database)
    database.executemany(
        "INSERT OR IGNORE INTO radio_bands(id,name,start_khz,end_khz,tx_permission,"
        "supports_cw,supports_sstv,supports_live_voice,supports_hosted_broadcasts,"
        "allow_open_broadcasts) VALUES(?,?,?,?,?,?,?,?,?,?)",
        DEFAULT_RADIO_BANDS,
    )
    _migrate_radio_permissions(database)
    if added_band_capabilities:
        database.execute(
            "UPDATE radio_bands SET supports_live_voice=(name='GENERAL COMMUNICATION'),"
            "supports_hosted_broadcasts=(name='BROADCASTS')"
        )
    if added_signal_capabilities:
        database.execute(
            "UPDATE radio_bands SET supports_cw="
            "(name IN ('MORSE / CW','EXPERIMENTAL / UNRESTRICTED'))"
            ",supports_sstv=(name IN ('DATA TRANSMISSIONS','EXPERIMENTAL / UNRESTRICTED'))"
        )
    if added_group_capability:
        database.execute(
            "UPDATE radio_permission_groups SET host_broadcasts=1 WHERE name='OPERATORS'"
        )
    database.commit()


def _migrate_radio_capability_columns(database):
    """Add voice/broadcast preparation fields without resetting later sysop choices."""
    band_columns = {row["name"] for row in database.execute("PRAGMA table_info(radio_bands)")}
    added_bands = "supports_live_voice" not in band_columns
    added_signals = "supports_cw" not in band_columns or "supports_sstv" not in band_columns
    if "supports_cw" not in band_columns:
        database.execute(
            "ALTER TABLE radio_bands ADD COLUMN supports_cw INTEGER NOT NULL DEFAULT 0 "
            "CHECK(supports_cw IN (0,1))"
        )
    if "supports_sstv" not in band_columns:
        database.execute(
            "ALTER TABLE radio_bands ADD COLUMN supports_sstv INTEGER NOT NULL DEFAULT 0 "
            "CHECK(supports_sstv IN (0,1))"
        )
    if added_bands:
        database.execute(
            "ALTER TABLE radio_bands ADD COLUMN supports_live_voice INTEGER NOT NULL DEFAULT 0 "
            "CHECK(supports_live_voice IN (0,1))"
        )
        database.execute(
            "ALTER TABLE radio_bands ADD COLUMN supports_hosted_broadcasts INTEGER NOT NULL DEFAULT 0 "
            "CHECK(supports_hosted_broadcasts IN (0,1))"
        )

    group_columns = {
        row["name"] for row in database.execute("PRAGMA table_info(radio_permission_groups)")
    }
    added_groups = "host_broadcasts" not in group_columns
    if added_groups:
        database.execute(
            "ALTER TABLE radio_permission_groups ADD COLUMN host_broadcasts INTEGER NOT NULL DEFAULT 0 "
            "CHECK(host_broadcasts IN (0,1))"
        )
    return added_bands, added_signals, added_groups


def _migrate_factory_radio_bands(database):
    """Expand known factory plans while preserving operator customizations."""
    rows = database.execute(
        "SELECT id,name,start_khz,end_khz,tx_permission FROM radio_bands ORDER BY id"
    ).fetchall()
    current = [tuple(row) for row in rows]
    if current not in (list(LEGACY_DEFAULT_RADIO_BANDS), list(NARROW_DEFAULT_RADIO_BANDS)):
        return
    database.executemany(
        "UPDATE radio_bands SET name=?,start_khz=?,end_khz=?,tx_permission=? WHERE id=?",
        [(name, start, end, permission, band_id)
         for band_id, name, start, end, permission, _cw, _sstv, _voice, _broadcast, _open
         in DEFAULT_RADIO_BANDS],
    )


def _align_radio_bands_to_display_precision(database):
    """Minimally align legacy custom limits with the two-decimal tuning grid."""
    for row in database.execute("SELECT id,start_khz,end_khz FROM radio_bands").fetchall():
        start = min(999990, ((row["start_khz"] + 9) // 10) * 10)
        end = min(999990, (row["end_khz"] // 10) * 10)
        if start > end:
            point = min(999990, max(100, ((row["start_khz"] + 5) // 10) * 10))
            start = end = point
        if start != row["start_khz"] or end != row["end_khz"]:
            database.execute(
                "UPDATE radio_bands SET start_khz=?,end_khz=? WHERE id=?",
                (start, end, row["id"]),
            )


def _migrate_radio_permissions(database):
    """Create baseline groups/rules and guarantee DEFAULT membership for every account."""
    default = database.execute(
        "SELECT id FROM radio_permission_groups WHERE is_default=1"
    ).fetchone()
    first_install = default is None
    if first_install:
        cursor = database.execute(
            "INSERT INTO radio_permission_groups(name,is_default) VALUES('DEFAULT',1)"
        )
        default_id = cursor.lastrowid
        operator_id = database.execute(
            "INSERT INTO radio_permission_groups(name,host_broadcasts) VALUES('OPERATORS',1)"
        ).lastrowid
        bands = database.execute(
            "SELECT id,tx_permission FROM radio_bands ORDER BY id"
        ).fetchall()
        for band in bands:
            baseline = "FULL" if band["tx_permission"] == "members" else "LISTEN"
            database.execute(
                "INSERT INTO radio_permission_rules(group_id,scope_type,band_id,permission) "
                "VALUES(?, 'band', ?, ?)", (default_id, band["id"], baseline),
            )
            if band["tx_permission"] == "admins":
                database.execute(
                    "INSERT INTO radio_permission_rules(group_id,scope_type,band_id,permission) "
                    "VALUES(?, 'band', ?, 'FULL')", (operator_id, band["id"]),
                )
        database.execute(
            "INSERT OR IGNORE INTO radio_group_memberships(user_id,group_id) "
            "SELECT id,? FROM users WHERE is_admin=1", (operator_id,),
        )
    else:
        default_id = default["id"]
    database.execute(
        "INSERT OR IGNORE INTO radio_group_memberships(user_id,group_id) "
        "SELECT id,? FROM users", (default_id,),
    )

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
        "radio_default_quota_mb": "INTEGER NOT NULL DEFAULT 100",
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
        "radio_quota_mb": "INTEGER",
    }
    for name, declaration in user_additions.items():
        if name not in user_columns:
            database.execute(f"ALTER TABLE users ADD COLUMN {name} {declaration}")

def _migrate_hosted_broadcast_fields(database):
    """Add hosted-radio controls without overwriting established band choices."""
    station_columns = {row["name"] for row in database.execute("PRAGMA table_info(station)")}
    if "radio_broadcast_limit_minutes" not in station_columns:
        database.execute(
            "ALTER TABLE station ADD COLUMN radio_broadcast_limit_minutes INTEGER NOT NULL DEFAULT 120"
        )
    band_columns = {row["name"] for row in database.execute("PRAGMA table_info(radio_bands)")}
    if "allow_open_broadcasts" not in band_columns:
        database.execute(
            "ALTER TABLE radio_bands ADD COLUMN allow_open_broadcasts INTEGER NOT NULL DEFAULT 0 "
            "CHECK(allow_open_broadcasts IN (0,1))"
        )
        database.execute(
            "UPDATE radio_bands SET allow_open_broadcasts=1 "
            "WHERE name='EXPERIMENTAL / UNRESTRICTED'"
        )
    audio_columns = {row["name"] for row in database.execute("PRAGMA table_info(radio_audio_files)")}
    if "duration_seconds" not in audio_columns:
        database.execute(
            "ALTER TABLE radio_audio_files ADD COLUMN duration_seconds REAL NOT NULL DEFAULT 180"
        )
    playlist_columns = {row["name"] for row in database.execute("PRAGMA table_info(radio_playlists)")}
    if "shuffle_enabled" not in playlist_columns:
        database.execute(
            "ALTER TABLE radio_playlists ADD COLUMN shuffle_enabled INTEGER NOT NULL DEFAULT 0 "
            "CHECK(shuffle_enabled IN (0,1))"
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

