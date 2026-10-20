"""Initial bulletin board service."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from .auth import admin_required, login_required
from .db import get_db
from .security import validate_csrf
bp = Blueprint("board", __name__, url_prefix="/board")

def _allowed():
    if g.user is None and not g.station["guest_access"]:
        flash("Guest bulletin access is disabled. Sign in to continue.", "warning")
        return redirect(url_for("auth.login", next=request.path))

@bp.get("")
def index():
    denied = _allowed()
    if denied: return denied
    posts = get_db().execute("""SELECT p.id,p.title,p.body,p.created_at,u.username,u.callsign,
      (SELECT COUNT(*) FROM posts r WHERE r.parent_id=p.id) reply_count
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
            database=get_db(); cursor=database.execute("INSERT INTO posts(author_id,title,body) VALUES(?,?,?)", (g.user["id"],title,body)); database.commit()
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
            database.execute("INSERT INTO posts(author_id,parent_id,body) VALUES(?,?,?)", (g.user["id"],post_id,body)); database.commit()
            flash("Reply transmitted.", "success"); return redirect(url_for("board.thread", post_id=post_id))
    replies=database.execute("SELECT p.*,u.username,u.callsign FROM posts p JOIN users u ON u.id=p.author_id WHERE p.parent_id=? ORDER BY p.created_at,p.id", (post_id,)).fetchall()
    return render_template("board/thread.html", post=post, replies=replies)

@bp.post("/<int:post_id>/delete")
@admin_required
def delete(post_id):
    validate_csrf(); database=get_db(); post=database.execute("SELECT id,parent_id FROM posts WHERE id=?", (post_id,)).fetchone()
    if post is None: abort(404)
    destination=url_for("board.thread", post_id=post["parent_id"]) if post["parent_id"] else url_for("board.index")
    database.execute("DELETE FROM posts WHERE id=?", (post_id,)); database.commit(); flash("Bulletin content removed by SYSOP.", "success")
    return redirect(destination)
