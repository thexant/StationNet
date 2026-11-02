"""Permanently remove an account and the content attributable to it."""
from pathlib import Path

from flask import current_app

from .db import get_db
from .realtime import utc_now


def is_station_owner(database, user_id):
    # First-run setup creates the station and its owner as the first user.
    # AUTOINCREMENT keeps this identity stable for the life of the station.
    row = database.execute("SELECT MIN(id) FROM users").fetchone()
    return row[0] == user_id


def _stored_file(root, filename):
    if filename and filename not in {".", ".."} and Path(filename).name == filename:
        return root / filename
    return None


def purge_pending_files():
    """Retry upload removals left behind by transient Windows file locks."""
    database = get_db()
    root = Path(current_app.config["DATA_DIR"]).resolve()
    changed = False
    for row in database.execute("SELECT relative_path FROM account_deletion_files").fetchall():
        relative = Path(row["relative_path"])
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not relative.parts or ".." in relative.parts:
            current_app.logger.error("Unsafe account deletion path in queue: %s", relative)
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
        database.execute("DELETE FROM account_deletion_files WHERE relative_path=?", (row["relative_path"],))
        changed = True
    if changed:
        database.commit()


def delete_account(user_id):
    """Delete database content atomically, then remove its uploaded files."""
    database = get_db()
    data = Path(current_app.config["DATA_DIR"])
    files = set()
    database.execute("BEGIN IMMEDIATE")
    try:
        user = database.execute(
            "SELECT id,username,connection_id,profile_photo_filename FROM users WHERE id=?",
            (user_id,),
        ).fetchone()
        if user is None:
            raise LookupError("Account no longer exists.")
        if is_station_owner(database, user_id):
            raise PermissionError("The station owner's account cannot be deleted.")

        def collect(root, query, parameters=(user_id,), columns=("stored_name",)):
            for row in database.execute(query, parameters):
                for column in columns:
                    path = _stored_file(root, row[column])
                    if path is not None:
                        files.add(path)

        collect(data / "profile_photos", "SELECT profile_photo_filename FROM users WHERE id=?",
                columns=("profile_photo_filename",))
        collect(data / "uploads", "SELECT stored_name FROM archive_files WHERE uploader_id=?")
        collect(data / "videos" / "station", "SELECT stored_name FROM videos WHERE uploader_id=? AND station_post=1")
        collect(data / "videos" / "users" / str(user_id),
                "SELECT stored_name FROM videos WHERE uploader_id=? AND station_post=0")
        for station_post, root in ((1, data / "library" / "station"),
                                   (0, data / "library" / "users" / str(user_id))):
            for row in database.execute(
                    "SELECT stored_name,format FROM library_books WHERE uploader_id=? AND station_post=?",
                    (user_id, station_post)):
                path = _stored_file(root, row["stored_name"])
                if path is not None:
                    files.add(path)
                    if row["format"] == "pdf":
                        files.add(path.with_name(path.name + ".cover.jpg"))
        collect(data / "arcade" / "roms", "SELECT stored_name FROM arcade_games WHERE uploader_id=?")
        collect(data / "arcade" / "covers", "SELECT cover_name FROM arcade_games WHERE uploader_id=?",
                columns=("cover_name",))
        collect(data / "radio_audio" / str(user_id),
                "SELECT stored_name FROM radio_audio_files WHERE owner_id=?")
        collect(data / "radio_content" / str(user_id),
                "SELECT stored_name FROM radio_broadcast_content WHERE owner_id=?")
        collect(data / "imageboard",
                "SELECT image_name,thumb_name FROM imageboard_threads WHERE author_id=?",
                columns=("image_name", "thumb_name"))
        collect(data / "imageboard",
                "SELECT r.image_name,r.thumb_name FROM imageboard_replies r "
                "WHERE r.author_id=? OR r.thread_id IN "
                "(SELECT id FROM imageboard_threads WHERE author_id=?)",
                (user_id, user_id), ("image_name", "thumb_name"))

        # Remove references to content that is about to disappear, including
        # reports filed by or against this account.
        database.execute("DELETE FROM moderation_reports WHERE reporter_id=? OR subject_user_id=? OR resolver_id=?",
                         (user_id, user_id, user_id))
        database.execute("DELETE FROM moderation_audit WHERE actor_id=? OR user_id=?", (user_id, user_id))
        database.execute("DELETE FROM moderation_restrictions WHERE actor_id=? OR user_id=?", (user_id, user_id))
        database.execute("DELETE FROM registration_invites WHERE created_by=? OR used_by=?", (user_id, user_id))
        database.execute("DELETE FROM application_storage WHERE "
                         "(storage_scope='user' AND owner_key=?) OR last_writer_user_id=?",
                         (f"user:{user_id}", user_id))
        database.execute("DELETE FROM application_multiplayer_events WHERE sender_user_id=?", (user_id,))
        database.execute("DELETE FROM events WHERE creator_id=?", (user_id,))
        database.execute("DELETE FROM mud_events WHERE action_id IN "
                         "(SELECT id FROM mud_action_log WHERE actor_id=?)", (user_id,))

        database.execute("DELETE FROM mail WHERE sender_id=? OR recipient_id=?", (user_id, user_id))
        database.execute("DELETE FROM video_comments WHERE author_id=?", (user_id,))
        database.execute("DELETE FROM videos WHERE uploader_id=?", (user_id,))
        database.execute("DELETE FROM library_bookmarks WHERE user_id=?", (user_id,))
        database.execute("DELETE FROM library_progress WHERE user_id=?", (user_id,))
        database.execute("DELETE FROM library_books WHERE uploader_id=?", (user_id,))
        database.execute("DELETE FROM arcade_games WHERE uploader_id=?", (user_id,))
        database.execute("DELETE FROM archive_files WHERE uploader_id=?", (user_id,))
        database.execute("DELETE FROM user_feed_comments WHERE author_id=?", (user_id,))
        database.execute("DELETE FROM poll_votes WHERE user_id=?", (user_id,))
        database.execute("DELETE FROM posts WHERE author_id=?", (user_id,))
        database.execute("DELETE FROM announcements WHERE author_id=?", (user_id,))
        database.execute("DELETE FROM imageboard_replies WHERE author_id=?", (user_id,))
        database.execute("DELETE FROM imageboard_threads WHERE author_id=?", (user_id,))
        for row in database.execute("SELECT id FROM station_chat_messages WHERE author_id=?", (user_id,)):
            database.execute("INSERT OR IGNORE INTO station_chat_deletions(message_id,deleted_at) VALUES(?,?)",
                             (row["id"], utc_now()))
        database.execute("DELETE FROM station_chat_messages WHERE author_id=?", (user_id,))

        # Other operators may have scheduled the user's radio content.
        database.execute("DELETE FROM radio_automation_rules WHERE program_id IN "
                         "(SELECT id FROM radio_programs WHERE owner_id=?)", (user_id,))
        database.execute("DELETE FROM radio_automated_broadcasts WHERE playlist_id IN "
                         "(SELECT id FROM radio_playlists WHERE owner_id=?)", (user_id,))
        database.execute("DELETE FROM radio_program_actions WHERE content_id IN "
                         "(SELECT id FROM radio_broadcast_content WHERE owner_id=? OR audio_file_id IN "
                         "(SELECT id FROM radio_audio_files WHERE owner_id=?)) OR playlist_id IN "
                         "(SELECT id FROM radio_playlists WHERE owner_id=?)",
                         (user_id, user_id, user_id))
        database.execute("DELETE FROM radio_broadcast_content WHERE audio_file_id IN "
                         "(SELECT id FROM radio_audio_files WHERE owner_id=?)", (user_id,))
        database.execute("DELETE FROM radio_playlist_tracks WHERE audio_file_id IN "
                         "(SELECT id FROM radio_audio_files WHERE owner_id=?)", (user_id,))
        database.execute("DELETE FROM radio_broadcast_resume WHERE owner_key=? OR permission_user_id=?",
                         (str(user_id), user_id))

        # Remove player-owned items and instances of definitions the player
        # authored. Containers must be emptied before their parent because
        # an item is required to have exactly one location.
        items = database.execute(
            "WITH RECURSIVE doomed(id,depth) AS ("
            "SELECT id,0 FROM mud_item_instances WHERE owner_character_id IN "
            "(SELECT id FROM mud_characters WHERE user_id=?) OR definition_id IN "
            "(SELECT id FROM mud_item_definitions WHERE created_by=?) UNION ALL "
            "SELECT child.id,doomed.depth+1 FROM mud_item_instances child "
            "JOIN doomed ON child.container_id=doomed.id) "
            "SELECT id FROM doomed GROUP BY id ORDER BY MAX(depth) DESC", (user_id, user_id),
        ).fetchall()
        for item in items:
            database.execute("DELETE FROM mud_item_instances WHERE id=?", (item["id"],))

        # Builder creations are posted content, including entities spawned
        # from those definitions. Remove restrictive references first.
        database.execute("DELETE FROM mud_quests WHERE created_by=?", (user_id,))
        database.execute("DELETE FROM mud_combat_encounters WHERE npc_definition_id IN "
                         "(SELECT id FROM mud_npc_definitions WHERE created_by=?)", (user_id,))
        database.execute("DELETE FROM mud_npc_instances WHERE definition_id IN "
                         "(SELECT id FROM mud_npc_definitions WHERE created_by=?)", (user_id,))
        database.execute("DELETE FROM mud_npc_definitions WHERE created_by=?", (user_id,))
        database.execute("DELETE FROM mud_conversations WHERE created_by=?", (user_id,))
        database.execute("DELETE FROM mud_npc_equipment WHERE item_definition_id IN "
                         "(SELECT id FROM mud_item_definitions WHERE created_by=?)", (user_id,))
        database.execute("DELETE FROM mud_npc_combat_rewards WHERE target_id IN "
                         "(SELECT id FROM mud_item_definitions WHERE created_by=?)", (user_id,))
        database.execute("DELETE FROM mud_item_definitions WHERE created_by=?", (user_id,))

        database.execute("DELETE FROM users WHERE id=?", (user_id,))
        for path in files:
            database.execute("INSERT OR IGNORE INTO account_deletion_files(relative_path) VALUES(?)",
                             (str(path.relative_to(data)),))
        database.commit()
    except Exception:
        database.rollback()
        raise

    # Disconnect live connections after the durable account removal.
    try:
        current_app.extensions["direct_chat"].disconnect(user_id)
        if user["connection_id"]:
            current_app.extensions["radio"].disconnect(user["connection_id"])
        current_app.extensions["hosted_broadcasts"].stop(user_id, "account_deleted")
    except Exception:
        # The database deletion has committed. A live transport failure must
        # not keep the now-deleted account's browser session signed in.
        current_app.logger.exception("Could not close every live account connection")
    purge_pending_files()
    return user["username"]
