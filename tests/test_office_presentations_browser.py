"""End-to-end smoke test for the browser-only presentation editor."""
import base64
import threading
import zipfile
from xml.etree import ElementTree

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


def test_presentations_editor_round_trip(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name,description,callsign,office_enabled) VALUES(?,?,?,1)",
            ("Test Station", "Test", "TST-1"),
        )
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("presenter", "PRS-1", "unused"),
        ).lastrowid
        database.commit()

    @app.get("/test-office-login")
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
                url = f"http://127.0.0.1:{server.server_port}"
                page.goto(url + "/test-office-login")
                page.goto(url + "/office/presentations")
                page.locator("#presentations-password").fill("correct horse battery staple")
                page.locator("#presentations-unlock-form button").click()
                page.locator("#presentations-confirm").fill("correct horse battery staple")
                page.locator("#presentations-unlock-form button").click()
                page.locator("#presentations-status").get_by_text("SAVED").wait_for(timeout=15000)
                page.locator("#presentations-title").fill("Browser deck")
                page.locator("#presentations-add-slide").click()
                page.locator('[data-action="text"]').click()
                page.locator("#presentations-notes").fill("Speaker note")
                page.locator("#presentations-status").get_by_text("SAVED").wait_for(timeout=15000)
                assert page.locator("#presentations-slide-list li").count() == 2
                with page.expect_download() as download_info:
                    page.locator("#presentations-export").click()
                download = download_info.value
                pptx = tmp_path / download.suggested_filename
                download.save_as(pptx)
                with zipfile.ZipFile(pptx) as archive:
                    for name in archive.namelist():
                        if name.endswith((".xml", ".rels")):
                            ElementTree.fromstring(archive.read(name))
                    assert b"Speaker note" in archive.read("ppt/notesSlides/notesSlide2.xml")
                    assert b"New text" in archive.read("ppt/slides/slide2.xml")
                page.locator("#presentations-file").set_input_files(pptx)
                page.locator("#presentations-message").get_by_text("Imported 2 slides").wait_for(timeout=15000)
                page.locator("#presentations-slide-list li").nth(1).click()
                assert page.locator("#presentations-notes").input_value() == "Speaker note"
                page.locator("#presentations-duplicate-slide").click()
                assert page.locator("#presentations-slide-list li").count() == 3
                page.locator("#presentations-delete-slide").click()
                assert page.locator("#presentations-slide-list li").count() == 2
                page.locator("#presentations-present").click()
                assert page.locator("#presentations-counter").inner_text() == "2 / 2"
                page.locator("#presentations-prev").click()
                assert page.locator("#presentations-counter").inner_text() == "1 / 2"
                page.locator("#presentations-exit").click()
                with page.expect_download() as png_info:
                    page.locator("#presentations-png").click()
                png = tmp_path / "slide.png"
                png_info.value.save_as(png)
                assert png.read_bytes().startswith(base64.b64decode("iVBORw0KGgo="))
                with page.expect_download() as pdf_info:
                    page.locator("#presentations-pdf").click()
                pdf = tmp_path / "deck.pdf"
                pdf_info.value.save_as(pdf)
                assert pdf.read_bytes().startswith(b"%PDF")
                page.locator("#presentations-status").get_by_text("SAVED").wait_for(timeout=15000)
                page.goto(url + "/office/documents")
                page.locator("#document-workspace").wait_for(state="visible", timeout=15000)
                page.locator("#document-status").get_by_text("NEW DOCUMENT").wait_for(timeout=15000)
                page.goto(url + "/office/sheets")
                page.locator("#sheets-workspace").wait_for(state="visible", timeout=15000)
                page.locator("#sheets-status").get_by_text("SAVED").wait_for(timeout=15000)
                page.locator("#sheets-lock").click()
                page.goto(url + "/office/presentations")
                page.locator("#presentations-unlock").wait_for(state="visible", timeout=15000)
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
