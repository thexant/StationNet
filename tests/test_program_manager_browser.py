"""Program directory paging and selection in a touch-sized browser."""

import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


def test_mobile_program_paging_and_selection(tmp_path):
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
            ("player", "PLY-1", "unused"),
        ).lastrowid
        database.commit()

    @app.get("/test-program-login")
    def test_login():
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
        return "OK"

    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as browser_driver:
            try:
                browser = browser_driver.chromium.launch(headless=True)
            except Exception as error:
                pytest.skip(f"Chromium is unavailable: {error}")
            try:
                page = browser.new_page(viewport={"width": 390, "height": 844},
                                        device_scale_factor=1, is_mobile=True, has_touch=True)
                base = f"http://127.0.0.1:{server.server_port}"
                page.goto(base + "/test-program-login")
                page.goto(base + "/games-apps")
                rows = page.locator("[data-program-row]:visible")
                assert rows.count() == 12
                next_button = page.locator("[data-program-next]")
                assert next_button.is_visible()
                assert next_button.is_enabled()
                next_button.tap()
                assert page.locator("[data-program-page-status]").inner_text() == "PAGE 2 / 2"
                assert rows.count() > 0
                selected_row = rows.last
                slug = selected_row.get_attribute("data-slug")
                selected_row.locator("a").tap()
                detail = page.locator(f'[data-program-detail="{slug}"]')
                assert detail.is_visible()
                assert detail.locator(".program-detail-actions a").get_attribute("href").endswith("/" + slug)
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
