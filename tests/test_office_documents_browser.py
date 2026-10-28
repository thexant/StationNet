"""Browser test for Documents formatting: styles, fonts, spacing, save/reload, and export."""
import threading
import zipfile

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db

EDITOR = "#document-editor .tiptap"
# ProseMirror reads DOM selection changes asynchronously; wait before using toolbar controls.
SELECTION_READY = "() => !document.querySelector('#document-editor .tiptap').editor.state.selection.empty"


def test_document_formatting_round_trip(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    expect = playwright.expect
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name,description,callsign,office_enabled) VALUES(?,?,?,1)",
            ("Docs Test", "Test", "DOC-1"),
        )
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("writer", "WRT-1", "unused"),
        ).lastrowid
        database.commit()

    @app.get("/test-documents-login")
    def test_login():
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
        return "OK"

    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as error:
                pytest.skip(f"Chromium is unavailable: {error}")
            try:
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                base = f"http://127.0.0.1:{server.server_port}"
                page.goto(base + "/test-documents-login")
                page.goto(base + "/office/documents")
                page.locator("#document-passphrase").fill("correct horse battery staple")
                page.locator("#document-unlock-form button").click()
                page.locator("#document-passphrase-confirm").fill("correct horse battery staple")
                page.locator("#document-unlock-form button").click()
                page.locator("#document-status").get_by_text("NEW DOCUMENT").wait_for(timeout=15000)
                page.locator("#document-title").fill("Formatting test")

                def style(value):
                    page.locator("#document-block").select_option(value)

                page.locator(EDITOR).click()
                page.keyboard.type("Mixed Case Heading")
                style("h1")
                heading = page.locator(f"{EDITOR} h1")
                assert heading.evaluate("element => getComputedStyle(element).textTransform") == "none"
                assert heading.inner_text() == "Mixed Case Heading"

                page.keyboard.press("End")
                page.keyboard.press("Enter")
                page.keyboard.type("Report Title")
                style("title")
                page.keyboard.press("Enter")
                page.keyboard.type("A subtitle")
                style("subtitle")
                page.keyboard.press("Enter")
                page.keyboard.type("Quoted words")
                style("quote")
                page.keyboard.press("Enter")
                style("normal")
                page.keyboard.type("Body text in Georgia")
                page.keyboard.press("Shift+Home")
                page.wait_for_function(SELECTION_READY)
                page.locator("#document-font").select_option(label="Georgia")
                page.locator("#document-size").select_option("18pt")
                page.locator('[data-command="alignJustify"]').click()
                page.locator('[data-command="indent"]').click()
                page.locator("#document-line-height").select_option("2")
                expect(page.locator('[data-command="alignJustify"]')).to_have_attribute("aria-pressed", "true")

                page.locator(f"{EDITOR} h1").click()
                expect(page.locator("#document-block")).to_have_value("h1")
                page.locator(f"{EDITOR} .doc-title").click()
                expect(page.locator("#document-block")).to_have_value("title")

                page.locator("#document-status").get_by_text("SAVED", exact=True).wait_for(timeout=15000)
                page.reload()
                page.locator("#document-status").get_by_text("SAVED", exact=True).wait_for(timeout=15000)
                html = page.locator(EDITOR).inner_html()
                assert page.locator(f"{EDITOR} h1").inner_text() == "Mixed Case Heading"
                assert html.count("doc-title") == 1 and "doc-subtitle" in html
                assert "<blockquote><p>Quoted words</p></blockquote>" in html
                for fragment in ("font-family: Georgia", "font-size: 18pt", "text-align: justify", "margin-left: 2em", "line-height: 2"):
                    assert fragment in html, (fragment, html)

                page.locator(f"{EDITOR} span").click()
                expect(page.locator("#document-font")).to_have_value("Georgia, serif")
                expect(page.locator("#document-size")).to_have_value("18pt")
                expect(page.locator("#document-line-height")).to_have_value("2")

                def export(fmt, name):
                    page.locator("#document-export").click()
                    with page.expect_download() as download:
                        page.locator(f'#document-export-dialog button[value="{fmt}"]').click()
                    path = tmp_path / name
                    download.value.save_as(path)
                    return path

                odt = export("odt", "formatting.odt")
                with zipfile.ZipFile(odt) as archive:
                    content = archive.read("content.xml").decode()
                assert 'text:style-name="Title"' in content and 'text:style-name="Subtitle"' in content
                assert 'fo:font-family="Georgia"' in content and 'fo:text-align="justify"' in content
                assert 'fo:line-height="200%"' in content and 'text:style-name="Quotations"' in content

                exported_html = export("html", "formatting.html").read_text(encoding="utf-8")
                assert 'class="doc-title"' in exported_html and ".doc-title{" in exported_html
                assert export("docx", "formatting.docx").read_bytes().startswith(b"PK")

                page.locator("#document-file").set_input_files(str(odt))
                page.locator("#document-title").wait_for(state="visible")
                page.wait_for_function("document.querySelector('#document-title').value === 'formatting'", timeout=15000)
                imported = page.locator(EDITOR).inner_html()
                assert page.locator(f"{EDITOR} h1").inner_text() == "Mixed Case Heading"
                assert "doc-title" in imported and "doc-subtitle" in imported and "<blockquote>" in imported
                for fragment in ("font-family: Georgia", "font-size: 18pt", "text-align: justify", "margin-left: 2em", "line-height: 2"):
                    assert fragment in imported, fragment
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
