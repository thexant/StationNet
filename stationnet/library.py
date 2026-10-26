"""Offline EPUB library and themed chapter reader."""
import html
from html.parser import HTMLParser
import io
from pathlib import Path
import posixpath
import secrets
from urllib.parse import unquote
import zipfile
import xml.etree.ElementTree as ET

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, send_file, url_for)

from .auth import login_required
from .db import get_db
from .moderation import audit
from .security import validate_csrf

bp = Blueprint("library", __name__, url_prefix="/library")
MAX_BOOK_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 150 * 1024 * 1024
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}


@bp.before_request
def require_enabled():
    if not g.station or not g.station["library_enabled"]:
        abort(404)


def _path(book):
    return (Path(current_app.config["DATA_DIR"]) / "library" /
            ("station" if book["station_post"] else f"users/{book['uploader_id']}") /
            book["stored_name"])


def _book(book_id):
    book = get_db().execute(
        "SELECT b.*,u.username FROM library_books b JOIN users u ON u.id=b.uploader_id WHERE b.id=?",
        (book_id,)).fetchone()
    if book is None:
        abort(404)
    return book


def _inside(base, href):
    """Resolve a package-relative href without leaving the EPUB archive."""
    href = unquote(href.split("#", 1)[0].split("?", 1)[0])
    if not href or href.startswith(("/", "\\")) or ":" in href or "\\" in href:
        raise ValueError("Unsafe EPUB resource path.")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), href))
    if path == ".." or path.startswith("../"):
        raise ValueError("Unsafe EPUB resource path.")
    return path


def _archive(zf):
    infos = zf.infolist()
    if len(infos) > 3000 or sum(i.file_size for i in infos) > MAX_EXPANDED_BYTES:
        raise ValueError("EPUB has too many or oversized resources.")
    names = set()
    for info in infos:
        name = info.filename
        if name in names or name.startswith("/") or "\\" in name or ":" in name or \
                posixpath.normpath(name) != name.rstrip("/") or name.startswith("../"):
            raise ValueError("EPUB has an unsafe resource path.")
        names.add(name)
    if zf.read("mimetype") != b"application/epub+zip":
        raise ValueError("File is not an EPUB book.")
    if "META-INF/encryption.xml" in names:
        raise ValueError("Encrypted EPUB books are not supported.")
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    rootfile = next((node.attrib.get("full-path") for node in container.iter()
                     if node.tag.endswith("rootfile")), None)
    if not rootfile or rootfile not in names:
        raise ValueError("EPUB package is missing.")
    package = ET.fromstring(zf.read(rootfile))
    metadata = next((node for node in package if node.tag.endswith("metadata")), None)
    manifest_node = next((node for node in package if node.tag.endswith("manifest")), None)
    spine_node = next((node for node in package if node.tag.endswith("spine")), None)
    if manifest_node is None or spine_node is None:
        raise ValueError("EPUB has no readable chapters.")
    manifest = {}
    for node in manifest_node:
        if not node.tag.endswith("item"):
            continue
        item_id = node.get("id")
        if item_id:
            path = _inside(rootfile, node.get("href", ""))
            if path not in names:
                raise ValueError("EPUB references a missing resource.")
            manifest[item_id] = {"path": path, "type": node.get("media-type", ""),
                                 "properties": node.get("properties", "")}
    chapters = [manifest[node.get("idref")]["path"] for node in spine_node
                if node.tag.endswith("itemref") and node.get("idref") in manifest
                and manifest[node.get("idref")]["type"] in
                {"application/xhtml+xml", "text/html"}]
    if not chapters:
        raise ValueError("EPUB has no readable chapters.")
    fields = {}
    creators = []
    if metadata is not None:
        for node in metadata:
            key = node.tag.rsplit("}", 1)[-1].lower()
            if key == "creator" and node.text:
                creators.append(" ".join(node.text.split()))
            if key in {"title", "creator", "description", "language", "publisher", "identifier"} \
                    and key not in fields and node.text:
                fields[key] = " ".join(node.text.split())
    if creators:
        fields["creator"] = ", ".join(creators[:8])
    cover = ""
    cover_id = next((node.get("content") for node in (metadata if metadata is not None else [])
                     if node.tag.endswith("meta") and node.get("name") == "cover"), None)
    if cover_id in manifest and manifest[cover_id]["type"].startswith("image/"):
        cover = manifest[cover_id]["path"]
    else:
        cover = next((v["path"] for v in manifest.values() if "cover-image" in v["properties"]), "")
    if Path(cover).suffix.lower() not in IMAGE_TYPES:
        cover = ""
    titles = [f"Chapter {i + 1}" for i in range(len(chapters))]
    nav = next((v["path"] for v in manifest.values() if "nav" in v["properties"].split()), "")
    toc_id = spine_node.get("toc")
    toc = nav or (manifest[toc_id]["path"] if toc_id in manifest else "")
    if toc:
        try:
            tree = ET.fromstring(zf.read(toc))
            for node in tree.iter():
                href = node.get("href") or node.get("src")
                if not href:
                    continue
                try:
                    path = _inside(toc, href)
                    if path in chapters:
                        label = " ".join("".join(node.itertext()).split())[:100]
                        if label:
                            titles[chapters.index(path)] = label
                except ValueError:
                    pass
        except ET.ParseError:
            pass
    return fields, manifest, chapters, titles, cover


class _ChapterHTML(HTMLParser):
    TAGS = {"p", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr",
            "blockquote", "em", "strong", "b", "i", "u", "s", "ul", "ol", "li",
            "dl", "dt", "dd", "pre", "code", "sup", "sub", "a", "img", "table",
            "thead", "tbody", "tr", "td", "th", "figure", "figcaption", "section"}
    EMPTY = {"br", "hr", "img"}
    DROP = {"script", "style", "svg", "iframe", "object", "form", "audio", "video"}

    def __init__(self, book_id, chapter_path, chapters, image_paths):
        super().__init__(convert_charrefs=True)
        self.book_id, self.chapter_path = book_id, chapter_path
        self.chapters, self.image_paths = chapters, image_paths
        self.parts, self.stack, self.drop = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.DROP:
            self.drop += 1
        if self.drop or tag not in self.TAGS:
            return
        attrs = dict(attrs)
        safe = []
        anchor = attrs.get("id") or attrs.get("name")
        if anchor and len(anchor) <= 120:
            safe.append(('id', anchor))
        if tag == "img":
            try:
                path = _inside(self.chapter_path, attrs.get("src", ""))
                if path in self.image_paths:
                    safe.extend((('src', url_for("library.resource", book_id=self.book_id,
                                                 path=path)), ('alt', attrs.get("alt", "")[:200])))
                else:
                    return
            except ValueError:
                return
        if tag == "a" and attrs.get("href"):
            href = attrs["href"]
            try:
                path = self.chapter_path if href.startswith("#") else _inside(self.chapter_path, href)
                if path in self.chapters:
                    fragment = href.partition("#")[2]
                    safe.append(('href', url_for("library.read", book_id=self.book_id,
                                                  chapter=self.chapters.index(path)) +
                                 ("#" + fragment if fragment else "")))
            except ValueError:
                pass
        attr_text = "".join(f' {key}="{html.escape(value, quote=True)}"' for key, value in safe)
        self.parts.append(f"<{tag}{attr_text}>")
        if tag not in self.EMPTY:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.DROP and self.drop:
            self.drop -= 1
        if self.drop or tag not in self.stack:
            return
        while self.stack:
            closing = self.stack.pop()
            self.parts.append(f"</{closing}>")
            if closing == tag:
                break

    def handle_data(self, data):
        if not self.drop:
            self.parts.append(html.escape(data))

    def result(self):
        return "".join(self.parts) + "".join(f"</{tag}>" for tag in reversed(self.stack))


def _quota_remaining(database):
    row = database.execute(
        "SELECT COALESCE(u.library_quota_mb,s.library_default_quota_mb) quota_mb,"
        "(SELECT COALESCE(SUM(size_bytes),0) FROM library_books "
        "WHERE uploader_id=u.id AND station_post=0) used_bytes "
        "FROM users u CROSS JOIN station s WHERE u.id=?", (g.user["id"],)).fetchone()
    return row["quota_mb"] * 1024 * 1024 - row["used_bytes"]


@bp.get("")
def index():
    query = request.args.get("q", "").strip()[:100]
    page = max(1, request.args.get("page", 1, type=int) or 1)
    where = "WHERE instr(lower(b.title || ' ' || b.author || ' ' || b.description),lower(?)) > 0" if query else ""
    params = (query,) if query else ()
    database = get_db()
    total = database.execute(f"SELECT COUNT(*) FROM library_books b {where}", params).fetchone()[0]
    pages = max(1, (total + 19) // 20)
    if page > pages:
        abort(404)
    books = database.execute(
        f"SELECT b.*,u.username FROM library_books b JOIN users u ON u.id=b.uploader_id "
        f"{where} ORDER BY b.id DESC LIMIT 20 OFFSET ?", (*params, (page - 1) * 20)).fetchall()
    return render_template("library/index.html", books=books, query=query, page=page, pages=pages)


def _save_upload(station_post):
    uploaded = request.files.get("file")
    if not uploaded or Path(uploaded.filename or "").suffix.lower() != ".epub":
        return "Select an EPUB book."
    data = uploaded.stream.read(MAX_BOOK_BYTES + 1)
    if len(data) > MAX_BOOK_BYTES:
        return "EPUB exceeds the 50 MB limit."
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            fields, _, _, _, cover = _archive(zf)
    except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError, RuntimeError):
        return "EPUB is invalid, encrypted, or contains unsafe resources."
    title = fields.get("title", "").strip()[:150] or Path(uploaded.filename).stem[:150]
    author = fields.get("creator", "")[:150]
    description = fields.get("description", "")[:5000]
    database = get_db()
    folder = Path(current_app.config["DATA_DIR"]) / "library" / (
        "station" if station_post else f"users/{g.user['id']}")
    folder.mkdir(parents=True, exist_ok=True)
    filename = secrets.token_hex(20) + ".epub"
    path = folder / filename
    saved = False
    try:
        if not station_post:
            database.execute("BEGIN IMMEDIATE")
            if len(data) > _quota_remaining(database):
                database.rollback()
                return "Library upload quota exceeded. Remove a book or ask a SYSOP to raise your quota."
        with path.open("xb") as output:
            output.write(data)
        cursor = database.execute(
            "INSERT INTO library_books(uploader_id,station_post,title,author,description,language,"
            "publisher,identifier,stored_name,original_name,size_bytes,cover_path) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (g.user["id"], station_post, title, author, description,
             fields.get("language", "")[:80], fields.get("publisher", "")[:150],
             fields.get("identifier", "")[:250], filename, Path(uploaded.filename).name[:250],
             len(data), cover))
        database.commit()
        saved = True
        return cursor.lastrowid
    except Exception:
        database.rollback()
        raise
    finally:
        if not saved:
            path.unlink(missing_ok=True)


@bp.route("/upload", methods=("GET", "POST"))
@login_required
def upload():
    if g.station["library_mode"] != "community":
        abort(404)
    if request.method == "POST":
        validate_csrf()
        result = _save_upload(False)
        if isinstance(result, int):
            flash("Book uploaded.", "success")
            return redirect(url_for("library.view", book_id=result))
        flash(result, "error")
    return render_template("library/upload.html")


@bp.post("/station-upload")
@login_required
def station_upload():
    if not g.user["is_admin"]:
        abort(403)
    validate_csrf()
    result = _save_upload(True)
    flash("Station book uploaded." if isinstance(result, int) else result,
          "success" if isinstance(result, int) else "error")
    return redirect(url_for("station.settings") + "#library-service")


@bp.get("/<int:book_id>")
def view(book_id):
    book = _book(book_id)
    progress = None
    if g.user:
        progress = get_db().execute(
            "SELECT chapter,fraction FROM library_progress WHERE book_id=? AND user_id=?",
            (book_id, g.user["id"])).fetchone()
    return render_template("library/view.html", book=book, progress=progress)


@bp.get("/<int:book_id>/download")
def download(book_id):
    book = _book(book_id)
    path = _path(book)
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="application/epub+zip", as_attachment=True,
                     download_name=book["original_name"], conditional=True)


@bp.get("/<int:book_id>/cover")
def cover(book_id):
    book = _book(book_id)
    if not book["cover_path"]:
        abort(404)
    return resource(book_id, book["cover_path"])


@bp.get("/<int:book_id>/resource/<path:path>")
def resource(book_id, path):
    book = _book(book_id)
    if Path(path).suffix.lower() not in IMAGE_TYPES or not _path(book).is_file():
        abort(404)
    try:
        with zipfile.ZipFile(_path(book)) as zf:
            _, manifest, _, _, _ = _archive(zf)
            images = {v["path"] for v in manifest.values() if v["type"].startswith("image/")}
            if path not in images or zf.getinfo(path).file_size > 10 * 1024 * 1024:
                abort(404)
            data = zf.read(path)
    except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError):
        abort(404)
    response = send_file(io.BytesIO(data), mimetype=IMAGE_TYPES[Path(path).suffix.lower()])
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@bp.get("/<int:book_id>/read")
def read(book_id):
    book = _book(book_id)
    path = _path(book)
    if not path.is_file():
        abort(404)
    try:
        with zipfile.ZipFile(path) as zf:
            _, manifest, chapters, titles, _ = _archive(zf)
            chapter = request.args.get("chapter", type=int)
            if chapter is None and g.user:
                last = get_db().execute(
                    "SELECT chapter FROM library_progress WHERE book_id=? AND user_id=?",
                    (book_id, g.user["id"])).fetchone()
                chapter = last["chapter"] if last else 0
            elif chapter is None:
                chapter = 0
            if chapter < 0 or chapter >= len(chapters):
                abort(404)
            chapter_path = chapters[chapter]
            image_paths = {v["path"] for v in manifest.values() if v["type"].startswith("image/")
                           and Path(v["path"]).suffix.lower() in IMAGE_TYPES}
            parser = _ChapterHTML(book_id, chapter_path, chapters, image_paths)
            parser.feed(zf.read(chapter_path).decode("utf-8-sig", errors="replace"))
            content = parser.result()
    except (ValueError, KeyError, zipfile.BadZipFile, ET.ParseError):
        abort(404)
    if request.args.get("format") == "json":
        return jsonify(content=content, chapter=chapter, title=titles[chapter])
    progress = bookmarks = None
    if g.user:
        database = get_db()
        progress = database.execute("SELECT * FROM library_progress WHERE book_id=? AND user_id=?",
                                    (book_id, g.user["id"])).fetchone()
        bookmarks = database.execute("SELECT * FROM library_bookmarks WHERE book_id=? AND user_id=? "
                                     "ORDER BY id DESC", (book_id, g.user["id"])).fetchall()
    return render_template("library/read.html", book=book, chapters=titles, chapter=chapter,
                           content=content, progress=progress, bookmarks=bookmarks or [])


@bp.post("/<int:book_id>/progress")
@login_required
def save_progress(book_id):
    validate_csrf()
    _book(book_id)
    payload = request.get_json(silent=True) or {}
    try:
        chapter = int(payload.get("chapter"))
        fraction = float(payload.get("fraction"))
        size = int(payload.get("text_size"))
    except (TypeError, ValueError):
        abort(400)
    if chapter < 0 or chapter > 10000 or not 0 <= fraction <= 1 or not 70 <= size <= 180:
        abort(400)
    database = get_db()
    database.execute("INSERT INTO library_progress(book_id,user_id,chapter,fraction,text_size) "
                     "VALUES(?,?,?,?,?) ON CONFLICT(book_id,user_id) DO UPDATE SET "
                     "chapter=excluded.chapter,fraction=excluded.fraction,text_size=excluded.text_size,"
                     "updated_at=CURRENT_TIMESTAMP", (book_id, g.user["id"], chapter, fraction, size))
    database.commit()
    return jsonify(ok=True)


@bp.post("/<int:book_id>/bookmarks")
@login_required
def add_bookmark(book_id):
    validate_csrf()
    _book(book_id)
    payload = request.get_json(silent=True) or {}
    try:
        chapter = int(payload.get("chapter"))
        fraction = float(payload.get("fraction"))
    except (TypeError, ValueError):
        abort(400)
    if chapter < 0 or chapter > 10000 or not 0 <= fraction <= 1:
        abort(400)
    label = str(payload.get("label", ""))[:100]
    database = get_db()
    cursor = database.execute("INSERT INTO library_bookmarks(book_id,user_id,chapter,fraction,label) "
                              "VALUES(?,?,?,?,?)", (book_id, g.user["id"], chapter, fraction, label))
    database.commit()
    return jsonify(id=cursor.lastrowid, label=label, chapter=chapter, fraction=fraction)


@bp.post("/<int:book_id>/bookmarks/<int:bookmark_id>/delete")
@login_required
def delete_bookmark(book_id, bookmark_id):
    validate_csrf()
    database = get_db()
    database.execute("DELETE FROM library_bookmarks WHERE id=? AND book_id=? AND user_id=?",
                     (bookmark_id, book_id, g.user["id"]))
    database.commit()
    return jsonify(ok=True)


@bp.post("/<int:book_id>/delete")
@login_required
def delete(book_id):
    validate_csrf()
    book = _book(book_id)
    if book["uploader_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403)
    try:
        _path(book).unlink(missing_ok=True)
    except OSError:
        flash("Book could not be removed from storage.", "error")
        return redirect(url_for("library.view", book_id=book_id))
    database = get_db()
    database.execute("DELETE FROM library_books WHERE id=?", (book_id,))
    if book["uploader_id"] != g.user["id"]:
        audit(database, g.user["id"], book["uploader_id"], "delete_book",
              request.form.get("reason", "SYSOP removal")[:500], "library_book", book_id)
    database.commit()
    flash("Book removed.", "success")
    return redirect(url_for("library.index"))
