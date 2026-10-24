"""Profile feeds, comments, and reactions."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, flash, g, redirect, request, url_for

from .auth import login_required
from .db import get_db
from .moderation import active_restriction, audit
from .security import validate_csrf

bp = Blueprint("feeds", __name__, url_prefix="/feeds")
REACTIONS = ("like", "heart", "laugh", "sad", "angry")


def require_enabled():
    if not g.station or not g.station["user_feeds_enabled"]:
        abort(404)


def _can_write():
    if active_restriction(g.user["id"], ("mute",)):
        abort(403, "Your account is muted.")


def _limit(table, column, count, minutes):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    recent = get_db().execute(
        f"SELECT COUNT(*) FROM {table} WHERE {column}=? AND created_at>=?",
        (g.user["id"], cutoff),
    ).fetchone()[0]
    if recent >= count:
        abort(429, "Feed rate limit reached. Please try again later.")


def _post(post_id):
    post = get_db().execute("SELECT id,owner_id FROM user_feed_posts WHERE id=?", (post_id,)).fetchone()
    if post is None:
        abort(404)
    return post


def _destination(owner_id, post_id=None):
    path = url_for("users.profile", user_id=owner_id)
    return path + (f"?post={post_id}#feed-post-{post_id}" if post_id else "#user-feed")


@bp.post("/users/<int:user_id>/posts")
@login_required
def create_post(user_id):
    require_enabled()
    validate_csrf()
    if user_id != g.user["id"]:
        abort(403)
    _can_write()
    body = request.form.get("body", "").strip()
    if not 1 <= len(body) <= 200:
        abort(400, "Posts must contain 1 to 200 characters.")
    _limit("user_feed_posts", "owner_id", 5, 10)
    database = get_db()
    cursor = database.execute(
        "INSERT INTO user_feed_posts(owner_id,body) VALUES(?,?)", (user_id, body)
    )
    database.commit()
    flash("Post published.", "success")
    return redirect(_destination(user_id, cursor.lastrowid))


@bp.post("/posts/<int:post_id>/delete")
@login_required
def delete_post(post_id):
    require_enabled()
    validate_csrf()
    post = _post(post_id)
    if post["owner_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403)
    database = get_db()
    database.execute("DELETE FROM user_feed_posts WHERE id=?", (post_id,))
    if post["owner_id"] != g.user["id"]:
        audit(database, g.user["id"], post["owner_id"], "delete_feed_post",
              request.form.get("reason", "SYSOP removal")[:500], "feed_post", post_id)
    database.commit()
    flash("Post deleted.", "success")
    return redirect(_destination(post["owner_id"]))


@bp.post("/posts/<int:post_id>/comments")
@login_required
def create_comment(post_id):
    require_enabled()
    validate_csrf()
    _can_write()
    post = _post(post_id)
    body = request.form.get("body", "").strip()
    if not 1 <= len(body) <= 500:
        abort(400, "Comments must contain 1 to 500 characters.")
    _limit("user_feed_comments", "author_id", 20, 60)
    database = get_db()
    database.execute(
        "INSERT INTO user_feed_comments(post_id,author_id,body) VALUES(?,?,?)",
        (post_id, g.user["id"], body),
    )
    # Existing private mail is the station's notification channel. Notify the
    # profile owner only for another person's comment, in the same transaction.
    if post["owner_id"] != g.user["id"]:
        cursor = database.execute(
            "INSERT INTO mail(sender_id,recipient_id,subject,body,sender_deleted) "
            "VALUES(?,?,?,?,1)",
            (g.user["id"], post["owner_id"], "FEED: New comment on your post",
             f"{g.user['username']} commented on your profile feed.\n\n{body}\n\n"
             f"Open: {_destination(post['owner_id'], post_id)}"),
        )
        database.execute("UPDATE mail SET thread_id=id WHERE id=?", (cursor.lastrowid,))
    database.commit()
    flash("Comment added.", "success")
    return redirect(_destination(post["owner_id"], post_id))


@bp.post("/comments/<int:comment_id>/delete")
@login_required
def delete_comment(comment_id):
    require_enabled()
    validate_csrf()
    database = get_db()
    comment = database.execute(
        "SELECT c.author_id,c.post_id,p.owner_id FROM user_feed_comments c "
        "JOIN user_feed_posts p ON p.id=c.post_id WHERE c.id=?", (comment_id,)
    ).fetchone()
    if comment is None:
        abort(404)
    if comment["author_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403)
    database.execute("DELETE FROM user_feed_comments WHERE id=?", (comment_id,))
    if comment["author_id"] != g.user["id"]:
        audit(database, g.user["id"], comment["author_id"], "delete_feed_comment",
              request.form.get("reason", "SYSOP removal")[:500], "feed_comment", comment_id)
    database.commit()
    flash("Comment deleted.", "success")
    return redirect(_destination(comment["owner_id"], comment["post_id"]))


@bp.post("/posts/<int:post_id>/react")
@login_required
def react(post_id):
    require_enabled()
    validate_csrf()
    _can_write()
    post = _post(post_id)
    kind = request.form.get("kind", "")
    if kind not in REACTIONS:
        abort(400)
    _limit("user_feed_reaction_events", "user_id", 60, 60)
    database = get_db()
    database.execute(
        "DELETE FROM user_feed_reaction_events WHERE created_at<datetime('now','-1 day')"
    )
    database.execute("INSERT INTO user_feed_reaction_events(user_id) VALUES(?)", (g.user["id"],))
    existing = database.execute(
        "SELECT kind FROM user_feed_reactions WHERE post_id=? AND user_id=?",
        (post_id, g.user["id"]),
    ).fetchone()
    if existing and existing["kind"] == kind:
        database.execute("DELETE FROM user_feed_reactions WHERE post_id=? AND user_id=?",
                         (post_id, g.user["id"]))
    else:
        database.execute(
            "INSERT INTO user_feed_reactions(post_id,user_id,kind) VALUES(?,?,?) "
            "ON CONFLICT(post_id,user_id) DO UPDATE SET kind=excluded.kind,created_at=CURRENT_TIMESTAMP",
            (post_id, g.user["id"], kind),
        )
    database.commit()
    return redirect(_destination(post["owner_id"], post_id))
