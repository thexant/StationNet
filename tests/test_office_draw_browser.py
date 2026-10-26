"""Browser smoke test for the encrypted Draw editor."""
import threading
from xml.etree import ElementTree

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


def test_draw_editor_round_trip(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name,description,callsign,office_enabled) VALUES(?,?,?,1)",
            ("Draw Test", "Test", "DRW-1"),
        )
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("artist", "ART-1", "unused"),
        ).lastrowid
        database.commit()

    @app.get("/test-draw-login")
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
                page.goto(base + "/test-draw-login")
                page.goto(base + "/office/draw")
                page.locator("#draw-password").fill("correct horse battery staple")
                page.locator("#draw-unlock-form button").click()
                page.locator("#draw-confirm").fill("correct horse battery staple")
                page.locator("#draw-unlock-form button").click()
                page.locator("#draw-status").get_by_text("SAVED").wait_for(timeout=15000)
                page.locator("#draw-title").fill("Test diagram")
                page.locator('[data-tool="rect"]').click()
                canvas = page.locator("#draw-canvas")
                box = canvas.bounding_box()
                assert box
                page.mouse.move(box["x"] + 35, box["y"] + 35)
                page.mouse.down()
                page.mouse.move(box["x"] + 155, box["y"] + 110)
                page.mouse.up()
                assert page.locator("#draw-canvas g[data-id]").count() == 1
                page.locator("#draw-fill").evaluate(
                    "element => { element.value = '#ff0000'; element.dispatchEvent(new Event('change', { bubbles: true })); }"
                )
                page.locator("#draw-status").get_by_text("SAVED").wait_for(timeout=15000)
                with page.expect_download() as svg_download:
                    page.locator("#draw-export-svg").click()
                svg_file = tmp_path / "diagram.svg"
                svg_download.value.save_as(svg_file)
                root = ElementTree.fromstring(svg_file.read_bytes())
                assert root.find(".//{http://www.w3.org/2000/svg}rect[@fill='#ff0000']") is not None
                with page.expect_download() as png_download:
                    page.locator("#draw-export-png").click()
                png_file = tmp_path / "diagram.png"
                png_download.value.save_as(png_file)
                assert png_file.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
                page.reload()
                page.locator("#draw-status").get_by_text("SAVED").wait_for(timeout=15000)
                assert page.locator("#draw-title").input_value() == "Test diagram"
                assert page.locator("#draw-canvas g[data-id]").count() == 1
                page.goto(base + "/office/documents")
                page.locator("#document-workspace").wait_for(state="visible", timeout=15000)
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
