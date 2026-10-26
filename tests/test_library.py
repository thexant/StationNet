"""EPUB library upload, reader, and access rules."""
import io
import zipfile

from stationnet import create_app
from stationnet.db import get_db


def epub(chapter='<h1>Hello</h1><p>Offline reading.</p>'):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        zf.writestr("OPS/book.opf", '''<package xmlns:dc="http://purl.org/dc/elements/1.1/">
          <metadata><dc:title>Test Book</dc:title><dc:creator>Ada Reader</dc:creator>
          <dc:description>A local story</dc:description></metadata>
          <manifest><item id="one" href="chapter.xhtml" media-type="application/xhtml+xml"/>
          <item id="cover" href="cover.png" media-type="image/png" properties="cover-image"/></manifest>
          <spine><itemref idref="one"/></spine></package>''')
        zf.writestr("OPS/chapter.xhtml", f"<html><body>{chapter}</body></html>")
        zf.writestr("OPS/cover.png", b"\x89PNG\r\n\x1a\n")
    return stream.getvalue()


def sign_in(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "library-test"


def test_library_upload_read_progress_bookmarks_and_modes(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,library_enabled) VALUES('Test','Test','TST-1',1)")
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('admin','ADM-1','unused',1)").lastrowid
        member = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('member','MEM-1','unused')").lastrowid
        db.commit()
    client = app.test_client()
    sign_in(client, member)
    book_bytes = epub()
    response = client.post("/library/upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(book_bytes), "book.epub")}, content_type="multipart/form-data")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/library/1")
    page = client.get("/library?q=Ada")
    assert b"Test Book" in page.data and b"Ada Reader" in page.data
    assert client.get("/library/1/cover").status_code == 200
    assert client.get("/library/1/download").data == book_bytes
    chapter = client.get("/library/1/read")
    assert b"Offline reading." in chapter.data
    assert b"library-reader.js" in chapter.data
    saved = client.post("/library/1/progress", json={"chapter": 0, "fraction": .4, "text_size": 120},
                        headers={"X-CSRF-Token": "library-test"})
    assert saved.status_code == 200
    assert b"RESUME READING" in client.get("/library/1").data
    mark = client.post("/library/1/bookmarks", json={"chapter": 0, "fraction": .5, "label": "Halfway"},
                       headers={"X-CSRF-Token": "library-test"})
    assert mark.status_code == 200
    assert b"Halfway" in client.get("/library/1/read").data
    assert client.post(f"/library/1/bookmarks/{mark.json['id']}/delete",
                       headers={"X-CSRF-Token": "library-test"}).status_code == 200
    sign_in(client, admin)
    assert client.post("/reports", data={"csrf_token": "library-test", "target_type": "library_book",
        "target_id": "1", "reason": "Please review this book"}).status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT target_type FROM moderation_reports").fetchone()[0] == "library_book"
    sign_in(client, member)
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET library_mode='curated'")
        db.commit()
    assert client.get("/library/upload").status_code == 404
    sign_in(client, admin)
    response = client.post("/library/station-upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(epub()), "station.epub")}, content_type="multipart/form-data")
    assert response.status_code == 302
    assert client.get("/sysop/settings").status_code == 200
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET library_enabled=0")
        db.commit()
    assert client.get("/library").status_code == 404


def test_epub_rejects_unsafe_resources_and_sanitizes_chapters(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,library_enabled) VALUES('Test','Test','TST-1',1)")
        user = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('member','MEM-1','unused')").lastrowid
        db.commit()
    client = app.test_client()
    sign_in(client, user)
    unsafe = io.BytesIO()
    with zipfile.ZipFile(unsafe, "w") as zf:
        zf.writestr("../escape", "bad")
    response = client.post("/library/upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(unsafe.getvalue()), "bad.epub")}, content_type="multipart/form-data", follow_redirects=True)
    assert b"invalid" in response.data
    client.post("/library/upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(epub('<script>alert(1)</script><p onclick="evil()">Safe</p>')),
                 "safe.epub")}, content_type="multipart/form-data")
    response = client.get("/library/1/read?format=json")
    assert "<script" not in response.json["content"]
    assert "onclick" not in response.json["content"]
    assert "Safe" in response.json["content"]
