"""Initial bulletin board service."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from . import auto_removal, notifications, polls
from .auth import login_required
from .db import get_db
from .security import validate_csrf
from .moderation import audit
from .realtime import utc_now
bp = Blueprint("board", __name__, url_prefix="/board")

def _allowed():
    if g.user is None and not g.station["guest_access"]:
        flash("Guest bulletin access is disabled. Sign in to continue.", "warning")
        return redirect(url_for("auth.login", next=request.path))

def _notify_reply(database, post, reply_id, body):
    """Alert the thread author and earlier repliers, plus anyone @mentioned."""
    url=url_for("board.thread", post_id=post["id"])+f"#reply-{reply_id}"
    mentioned=set(notifications.notify_mentions(database,body,url,g.user,f'a reply to "{post["title"]}"'))
    participants=[post["author_id"]]+[row["author_id"] for row in database.execute(
        "SELECT DISTINCT author_id FROM posts WHERE parent_id=? AND author_id<>? ORDER BY author_id",
        (post["id"],post["author_id"]))]
    for user_id in participants:
        if user_id in mentioned: continue
        whose="your bulletin" if user_id==post["author_id"] else "a thread you replied to"
        notifications.notify(database,user_id,"reply",f'{g.user["username"]} replied to {whose}: "{post["title"]}"',
            url=url,body=notifications.excerpt(body),actor_id=g.user["id"],dedupe_key=f"board:{post['id']}")

@bp.get("")
def index():
    denied = _allowed()
    if denied: return denied
    posts = get_db().execute("""SELECT p.id,p.title,p.body,p.created_at,u.username,u.callsign,
      (SELECT COUNT(*) FROM posts r WHERE r.parent_id=p.id) reply_count,
      EXISTS(SELECT 1 FROM polls l WHERE l.post_id=p.id) has_poll
      FROM posts p JOIN users u ON u.id=p.author_id WHERE p.parent_id IS NULL ORDER BY p.created_at DESC,p.id DESC""").fetchall()
    return render_template("board/index.html", posts=posts)

@bp.route("/new", methods=("GET", "POST"))
@login_required
def create():
    if request.method == "POST":
        validate_csrf(); title=request.form.get("title", "").strip(); body=request.form.get("body", "").strip()
        if not 3 <= len(title) <= 120: flash("Subject must be between 3 and 120 characters.", "error")
        elif not 1 <= len(body) <= 5000: flash("Message must be between 1 and 5000 characters.", "error")
        else:
            poll,error=polls.parse_form(request.form); expires_at,_,removal_error=auto_removal.parse_form(request.form)
            if error or removal_error: flash(error or removal_error, "error")
            else:
                database=get_db(); cursor=database.execute("INSERT INTO posts(author_id,title,body,expires_at) VALUES(?,?,?,?)", (g.user["id"],title,body,expires_at))
                if poll: polls.create(database,g.user["id"],poll,post_id=cursor.lastrowid)
                notifications.notify_mentions(database,title+" "+body,url_for("board.thread",post_id=cursor.lastrowid),g.user,f'the bulletin "{title}"')
                database.commit()
                flash("Bulletin transmitted.", "success"); return redirect(url_for("board.thread", post_id=cursor.lastrowid))
    return render_template("board/new.html")

@bp.route("/<int:post_id>", methods=("GET", "POST"))
def thread(post_id):
    denied=_allowed()
    if denied: return denied
    database=get_db(); post=database.execute("SELECT p.*,u.username,u.callsign FROM posts p JOIN users u ON u.id=p.author_id WHERE p.id=? AND p.parent_id IS NULL", (post_id,)).fetchone()
    if post is None: abort(404)
    if request.method == "POST":
        if g.user is None:
            flash("Sign in to reply.", "warning"); return redirect(url_for("auth.login", next=request.path))
        validate_csrf(); body=request.form.get("body", "").strip()
        if not 1 <= len(body) <= 5000: flash("Reply must be between 1 and 5000 characters.", "error")
        else:
            cursor=database.execute("INSERT INTO posts(author_id,parent_id,body) VALUES(?,?,?)", (g.user["id"],post_id,body))
            _notify_reply(database,post,cursor.lastrowid,body); database.commit()
            flash("Reply transmitted.", "success"); return redirect(url_for("board.thread", post_id=post_id))
    replies=database.execute("SELECT p.*,u.username,u.callsign FROM posts p JOIN users u ON u.id=p.author_id WHERE p.parent_id=? ORDER BY p.created_at,p.id", (post_id,)).fetchall()
    return render_template("board/thread.html", post=post, replies=replies, poll=polls.view_for_post(post_id))

@bp.post("/<int:post_id>/delete")
@login_required
def delete(post_id):
    validate_csrf(); database=get_db(); post=database.execute("SELECT id,parent_id,author_id FROM posts WHERE id=?", (post_id,)).fetchone()
    if post is None: abort(404)
    own=post["author_id"] == g.user["id"]
    if not own and not g.user["is_admin"]: abort(403)
    destination=url_for("board.thread", post_id=post["parent_id"]) if post["parent_id"] else url_for("board.index")
    database.execute("DELETE FROM posts WHERE id=?", (post_id,))
    if not own: audit(database,g.user["id"],post["author_id"],"delete_bulletin",request.form.get("reason", "SYSOP removal")[:500],"bulletin",post_id)
    database.commit(); flash("Bulletin content removed." if own else "Bulletin content removed by SYSOP.", "success")
    return redirect(destination)
