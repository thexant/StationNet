"""Echo Choir remains playable and readable at desktop and touch widths."""

import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "touch"])
def test_echo_choir_checkpoint_and_layout(tmp_path, mobile):
    playwright = pytest.importorskip("playwright.sync_api")
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name,description,callsign) VALUES(?,?,?)",
            ("Test Station", "Test", "TST-1"),
        )
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("singer", "ECH-1", "unused"),
        ).lastrowid
        database.commit()

    @app.get("/test-echo-login")
    def login():
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
                context = browser.new_context(
                    viewport={"width": 390, "height": 844} if mobile else {"width": 1280, "height": 800},
                    is_mobile=mobile,
                    has_touch=mobile,
                )
                page = context.new_page()
                base = f"http://127.0.0.1:{server.server_port}"
                page.goto(base + "/test-echo-login")
                page.goto(base + "/games-apps/echo-choir")
                if mobile and page.locator("[data-app-launch-anyway]").is_visible():
                    page.locator("[data-app-launch-anyway]").click()
                page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)
                surface = page.locator("[data-app-surface]")
                surface.locator("canvas.app-canvas").first.wait_for()
                assert surface.locator("canvas.app-canvas").count() == 2
                canvas = surface.locator("canvas.app-canvas").first
                assert canvas.evaluate("element => element.width") == (350 if mobile else 700)
                assert canvas.bounding_box()["width"] <= (390 if mobile else 1280)
                surface.get_by_role("button", name="RIGHT", exact=True).click()
                surface.locator('[data-app-component="signals"]').get_by_text("Beat 1/18", exact=False).wait_for()
                page.reload()
                page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)
                surface.get_by_text("previous timeline is ready", exact=False).wait_for()
                surface.locator('[data-app-component="signals"]').get_by_text("Beat 1/18", exact=False).wait_for()
                surface.get_by_role("button", name="UNDO", exact=True).click()
                surface.locator('[data-app-component="signals"]').get_by_text("Beat 0/18", exact=False).wait_for()
                surface.get_by_role("button", name="SHOW NEXT HINT").click()
                surface.get_by_text("Walk onto pad 1", exact=False).wait_for()
                surface.get_by_role("button", name="SHOW TEXT MAP").click()
                surface.get_by_text("@ player", exact=False).wait_for()
                surface.get_by_role("button", name="SHOW WHOLE MAP" if mobile else "ZOOM TO PLAYER").click()
                assert surface.evaluate("element => element.getBoundingClientRect().width <= innerWidth + 1")
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
