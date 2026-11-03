"""EPUB and PDF library upload, reader, and access rules."""
import io
import zipfile
from flask import request
from PIL import Image
import pymupdf
from pypdf import PdfWriter

from stationnet import create_app, library
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


def pdf():
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": "Station Manual", "/Author": "Ada Reader",
                         "/Subject": "Operational reference"})
    writer.write(stream)
    return stream.getvalue()


def illustrated_pdf():
    with pymupdf.open() as document:
        for color in ((1, 0, 0), (0, 0, 1)):
            page = document.new_page(width=360, height=480)
            page.draw_rect(page.rect, color=color, fill=color)
        return document.tobytes()


def test_library_file_size_setting_controls_member_and_station_uploads(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,library_enabled) VALUES('Test','Test','TST-1',1)")
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('admin','ADM-1','unused',1)").lastrowid
        member = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('member','MEM-1','unused')").lastrowid
        db.commit()

    @app.after_request
    def report_request_limit(response):
        response.headers["X-Test-Request-Limit"] = str(request.max_content_length)
        return response

    client = app.test_client()
    sign_in(client, member)
    assert b"up to 512 MB" in client.get("/library/upload").data
    response = client.post("/library/upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(pdf()), "default.pdf")}, content_type="multipart/form-data")
    assert response.status_code == 302
    assert int(response.headers["X-Test-Request-Limit"]) > 512 * 1024 * 1024
    large_epub = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(epub())) as source, zipfile.ZipFile(large_epub, "w") as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr("OPS/large-resource.bin", b"x" * (51 * 1024 * 1024),
                        compress_type=zipfile.ZIP_STORED)
    response = client.post("/library/upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(large_epub.getvalue()), "large.epub")}, content_type="multipart/form-data")
    assert response.status_code == 302 and response.headers["Location"].endswith("/library/2")

    sign_in(client, admin)
    settings = {"csrf_token": "library-test", "settings_section": "library",
                "library_enabled": "on", "library_mode": "community",
                "library_default_quota_mb": "512", "library_max_upload_mb": "1"}
    assert client.post("/sysop/settings", data=settings).status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT library_max_upload_mb FROM station").fetchone()[0] == 1
    assert b"up to 1 MB" in client.get("/sysop/settings").data
    invalid = client.post("/sysop/settings", data={**settings, "library_max_upload_mb": "0"},
                          follow_redirects=True)
    assert b"Library file size limit must be between 1 and 10240 MB" in invalid.data
    with app.app_context():
        assert get_db().execute("SELECT library_max_upload_mb FROM station").fetchone()[0] == 1

    oversized = pdf() + b" " * (1024 * 1024)
    sign_in(client, member)
    assert b"up to 1 MB" in client.get("/library/upload").data
    rejected = client.post("/library/upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(oversized), "oversized.pdf")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"Document exceeds the 1 MB limit" in rejected.data
    sign_in(client, admin)
    rejected = client.post("/library/station-upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(oversized), "oversized.pdf")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"Document exceeds the 1 MB limit" in rejected.data
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM library_books").fetchone()[0] == 2
    assert client.post("/sysop/settings", data={**settings, "library_max_upload_mb": "2"}).status_code == 302
    sign_in(client, member)
    accepted = client.post("/library/upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(oversized), "accepted.pdf")}, content_type="multipart/form-data")
    assert accepted.status_code == 302 and accepted.headers["Location"].endswith("/library/3")


def test_library_downloads_setting_hides_controls_and_blocks_route(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,library_enabled) VALUES('Test','Test','TST-1',1)")
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('admin','ADM-1','unused',1)").lastrowid
        db.commit()
    client = app.test_client()
    sign_in(client, admin)
    data = pdf()
    client.post("/library/station-upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(data), "manual.pdf")}, content_type="multipart/form-data")
    assert b"Allow Library Downloads" in client.get("/sysop/settings").data
    assert b"DOWNLOAD PDF" in client.get("/library/1").data
    assert b"Open PDF in a new tab" in client.get("/library/1/read").data
    assert client.get("/library/1/download").data == data

    settings = {"csrf_token": "library-test", "settings_section": "library",
                "library_enabled": "on", "library_mode": "community",
                "library_default_quota_mb": "512", "library_max_upload_mb": "512"}
    assert client.post("/sysop/settings", data=settings).status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT library_downloads_enabled FROM station").fetchone()[0] == 0
    assert b"DOWNLOAD PDF" not in client.get("/library/1").data
    reader = client.get("/library/1/read")
    assert reader.status_code == 200 and b"Open PDF in a new tab" not in reader.data
    assert client.get("/library/1/download").status_code == 404
    assert client.get("/library/1/pdf").status_code == 200

    assert client.post("/sysop/settings", data={**settings, "library_downloads_enabled": "on"}).status_code == 302
    assert b"DOWNLOAD PDF" in client.get("/library/1").data
    assert client.get("/library/1/download").data == data


def test_pdf_library_upload_reader_and_modes(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,library_enabled) VALUES('Test','Test','TST-1',1)")
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('admin','ADM-1','unused',1)").lastrowid
        member = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('member','MEM-1','unused')").lastrowid
        db.commit()
    client = app.test_client()
    sign_in(client, member)
    data = pdf()
    result = client.post("/library/upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(data), "manual.pdf")}, content_type="multipart/form-data")
    assert result.status_code == 302
    with app.app_context():
        book = get_db().execute("SELECT * FROM library_books").fetchone()
        assert book["format"] == "pdf" and book["page_count"] == 2
        assert book["title"] == "Station Manual" and book["author"] == "Ada Reader"
    assert b"Station Manual" in client.get("/library?q=Station").data
    assert client.get("/library/1/cover").mimetype == "image/jpeg"
    assert client.get("/library/1/download").data == data
    inline = client.get("/library/1/pdf")
    assert inline.data == data and inline.mimetype == "application/pdf"
    assert inline.headers["Content-Disposition"] == "inline"
    assert inline.headers["X-Frame-Options"] == "SAMEORIGIN"
    reader = client.get("/library/1/read?page=2")
    assert b"library-pdf-reader.js" in reader.data and b'data-page="2"' in reader.data
    assert client.get("/library/1/read?page=3").status_code == 404
    assert client.get("/library/1/resource/cover.png").status_code == 404
    assert client.post("/library/1/progress", json={"chapter": 1, "fraction": 0, "text_size": 100},
                       headers={"X-CSRF-Token": "library-test"}).status_code == 200
    assert b"page=2" in client.get("/library/1").data
    mark = client.post("/library/1/bookmarks", json={"chapter": 1, "fraction": 0, "label": "Page 2"},
                       headers={"X-CSRF-Token": "library-test"})
    assert mark.status_code == 200 and b"Page 2" in client.get("/library/1/read").data
    bad = client.post("/library/upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(b"not a pdf"), "bad.pdf")}, content_type="multipart/form-data", follow_redirects=True)
    assert b"invalid" in bad.data
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET library_mode='curated'")
        db.commit()
    assert client.get("/library/upload").status_code == 404
    sign_in(client, admin)
    assert client.post("/library/station-upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(data), "station.pdf")}, content_type="multipart/form-data").status_code == 302


def test_pdf_first_page_cover_is_cached_for_new_and_existing_books(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,library_enabled) VALUES('Test','Test','TST-1',1)")
        member = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('member','MEM-1','unused')").lastrowid
        db.commit()
    client = app.test_client()
    sign_in(client, member)
    data = illustrated_pdf()
    result = client.post("/library/upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(data), "illustrated.pdf")}, content_type="multipart/form-data")
    assert result.status_code == 302
    with app.app_context():
        book = get_db().execute("SELECT * FROM library_books").fetchone()
        cover_path = tmp_path / "library" / f"users/{member}" / (book["stored_name"] + ".cover.jpg")
    assert cover_path.is_file()
    assert b'/library/1/cover' in client.get("/library").data
    assert b'/library/1/cover' in client.get("/library/1").data
    cover = client.get("/library/1/cover")
    assert cover.mimetype == "image/jpeg"
    with Image.open(io.BytesIO(cover.data)) as image:
        assert image.size == (360, 480)
        red, green, blue = image.getpixel((180, 240))
        assert red > 200 and green < 50 and blue < 50
    cover.close()

    cover_path.unlink()  # Simulate a PDF uploaded before thumbnail support.
    regenerated = client.get("/library/1/cover")
    assert regenerated.mimetype == "image/jpeg"
    regenerated.close()
    assert cover_path.is_file()
    assert client.post("/library/1/delete", data={"csrf_token": "library-test"}).status_code == 302
    assert not cover_path.exists()


def test_pdf_cover_render_failure_preserves_document_and_fallback(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,library_enabled) VALUES('Test','Test','TST-1',1)")
        member = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('member','MEM-1','unused')").lastrowid
        db.commit()
    client = app.test_client()
    sign_in(client, member)
    def fail(*args):
        raise RuntimeError("Render failed")
    monkeypatch.setattr(library, "_render_pdf_cover", fail)
    data = pdf()
    result = client.post("/library/upload", data={"csrf_token": "library-test",
        "file": (io.BytesIO(data), "manual.pdf")}, content_type="multipart/form-data")
    assert result.status_code == 302
    assert client.get("/library/1/cover").mimetype == "image/svg+xml"
    assert client.get("/library/1/read").status_code == 200
    assert client.get("/library/1/download").data == data


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


def test_library_categories_filters_sorting_and_curated_mode(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,library_enabled) VALUES('Test','Test','TST-1',1)")
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('admin','ADM-1','unused',1)").lastrowid
        member = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('member','MEM-1','unused')").lastrowid
        fantasy = db.execute("SELECT id FROM library_categories WHERE name='Fantasy'").fetchone()[0]
        db.commit()
    client = app.test_client()
    sign_in(client, member)
    assert client.post("/sysop/library/categories", data={"csrf_token": "library-test", "name": "Manuals"}).status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT id FROM library_categories WHERE name='Manuals'").fetchone() is None
    sign_in(client, admin)
    assert client.post("/sysop/library/categories", data={"csrf_token": "library-test", "name": "Manuals"}).status_code == 302
    with app.app_context():
        manuals = get_db().execute("SELECT id FROM library_categories WHERE name='Manuals'").fetchone()[0]
    duplicate = client.post("/sysop/library/categories", data={"csrf_token": "library-test", "name": "manuals"}, follow_redirects=True)
    assert b"already exists" in duplicate.data
    sign_in(client, member)
    assert client.post("/library/upload", data={"csrf_token": "library-test", "category_id": str(fantasy),
        "file": (io.BytesIO(epub()), "story.epub")}, content_type="multipart/form-data").status_code == 302
    assert client.post("/library/upload", data={"csrf_token": "library-test", "category_id": str(manuals),
        "file": (io.BytesIO(pdf()), "manual.pdf")}, content_type="multipart/form-data").status_code == 302
    with app.app_context():
        db = get_db()
        db.execute("UPDATE library_books SET title='Zebra Story',author='Zoe' WHERE format='epub'")
        db.execute("UPDATE library_books SET title='Alpha Manual',author='Amy' WHERE format='pdf'")
        db.commit()
    filtered = client.get(f"/library?category={fantasy}&format=epub&sort=title&q=Zebra")
    assert b"Zebra Story" in filtered.data and b"Alpha Manual" not in filtered.data
    assert b'option value="epub" selected' in filtered.data
    assert b'option value="title" selected' in filtered.data
    assert b"No books found" in client.get(f"/library?category={fantasy}&format=pdf").data
    title_page = client.get("/library?sort=title").data
    assert title_page.index(b"Alpha Manual") < title_page.index(b"Zebra Story")
    author_page = client.get("/library?sort=author").data
    assert author_page.index(b"Alpha Manual") < author_page.index(b"Zebra Story")
    newest_page = client.get("/library?sort=newest").data
    assert newest_page.index(b"Alpha Manual") < newest_page.index(b"Zebra Story")
    oldest_page = client.get("/library?sort=oldest").data
    assert oldest_page.index(b"Zebra Story") < oldest_page.index(b"Alpha Manual")
    assert client.post("/library/2/category", data={"csrf_token": "library-test", "category_id": str(fantasy)}).status_code == 302
    assert b"Fantasy" in client.get("/library/2").data
    assert client.post("/library/2/category", data={"csrf_token": "library-test", "category_id": str(manuals)}).status_code == 302
    sign_in(client, admin)
    assert client.post(f"/sysop/library/categories/{fantasy}/availability", data={
        "csrf_token": "library-test", "enabled": "0"}).status_code == 302
    assert b"Fantasy (disabled)" in client.get("/sysop/settings").data
    assert b"Fantasy" not in client.get("/library").data.split(b'All categories</option>')[1].split(b'</select>')[0]
    sign_in(client, member)
    assert b"Fantasy" in client.get("/library/1").data
    invalid = client.post("/library/1/category", data={"csrf_token": "library-test", "category_id": str(fantasy)}, follow_redirects=True)
    assert b"Select an available category" in invalid.data
    with app.app_context():
        db = get_db()
        db.execute("UPDATE station SET library_mode='curated'")
        db.commit()
    assert client.get("/library/upload").status_code == 404
    assert b"Alpha Manual" in client.get(f"/library?category={manuals}&format=pdf").data
    sign_in(client, admin)
    assert client.post("/library/station-upload", data={"csrf_token": "library-test", "category_id": str(manuals),
        "file": (io.BytesIO(pdf()), "station.pdf")}, content_type="multipart/form-data").status_code == 302
    assert b"Station Manual" in client.get(f"/library?category={manuals}&format=pdf").data
    assert client.post(f"/sysop/library/categories/{fantasy}/availability", data={
        "csrf_token": "library-test", "enabled": "1"}).status_code == 302
    assert b"Fantasy" in client.get("/library").data.split(b'All categories</option>')[1].split(b'</select>')[0]


def test_library_metadata_edit_permissions_and_audit(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,library_enabled) VALUES('Test','Test','TST-1',1)")
        admin = db.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES('admin','ADM-1','unused',1)").lastrowid
        member = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('member','MEM-1','unused')").lastrowid
        other = db.execute("INSERT INTO users(username,callsign,password_hash) VALUES('other','OTH-1','unused')").lastrowid
        fantasy = db.execute("SELECT id FROM library_categories WHERE name='Fantasy'").fetchone()[0]
        db.commit()
    client = app.test_client()
    sign_in(client, member)
    assert client.post("/library/upload", data={"csrf_token": "library-test", "category_id": str(fantasy),
        "file": (io.BytesIO(pdf()), "manual.pdf")}, content_type="multipart/form-data").status_code == 302
    assert b"EDIT DETAILS" in client.get("/library/1").data
    assert client.get("/library/1/edit").status_code == 200
    fields = {"csrf_token": "library-test", "title": "  Field   Guide ", "author": "Grace Hopper",
              "description": "Line one\nLine two", "publisher": "Station Press", "language": "en",
              "identifier": "x" * 300, "category_id": str(fantasy)}
    assert client.post("/library/1/edit", data=fields).status_code == 302
    with app.app_context():
        book = get_db().execute("SELECT * FROM library_books WHERE id=1").fetchone()
        assert (book["title"], book["author"], book["description"], book["publisher"], book["language"]) == \
            ("Field Guide", "Grace Hopper", "Line one\nLine two", "Station Press", "en")
        assert len(book["identifier"]) == 250 and book["category_id"] == fantasy
        assert get_db().execute("SELECT COUNT(*) FROM moderation_audit").fetchone()[0] == 0
    assert b"Grace Hopper" in client.get("/library/1").data
    assert b"Field Guide" in client.get("/library?q=hopper").data
    blank = client.post("/library/1/edit", data={**fields, "title": "   "}, follow_redirects=True)
    assert b"Enter a title" in blank.data
    sign_in(client, other)
    assert b"EDIT DETAILS" not in client.get("/library/1").data
    assert client.get("/library/1/edit").status_code == 403
    assert client.post("/library/1/edit", data={**fields, "title": "Hijacked"}).status_code == 403
    sign_in(client, admin)
    assert client.post(f"/sysop/library/categories/{fantasy}/availability", data={
        "csrf_token": "library-test", "enabled": "0"}).status_code == 302
    assert b"SYSOP EDIT DETAILS" in client.get("/library/1").data
    assert b"Fantasy (disabled)" in client.get("/library/1/edit").data
    assert client.post("/library/1/edit", data={**fields, "title": "Admin Title"}).status_code == 302
    with app.app_context():
        db = get_db()
        book = db.execute("SELECT title,category_id FROM library_books WHERE id=1").fetchone()
        assert (book["title"], book["category_id"]) == ("Admin Title", fantasy)
        entry = db.execute("SELECT * FROM moderation_audit").fetchone()
        assert (entry["actor_id"], entry["user_id"], entry["action"], entry["target_id"]) == \
            (admin, member, "edit_book", 1)
