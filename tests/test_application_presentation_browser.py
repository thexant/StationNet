"""The sandboxed application keeps animating across presentation changes."""
import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


def test_game_continues_after_switching_presentation(tmp_path):
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
            ("player", "PLY-1", "unused"),
        ).lastrowid
        database.commit()

    @app.get("/test-application-login")
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
                page = browser.new_page()
                url = f"http://127.0.0.1:{server.server_port}"
                page.goto(url + "/test-application-login")
                page.goto(url + "/games-apps/light-cycles")
                page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)
                runtime = page.locator("[data-app-runtime]")
                runtime_node = runtime.element_handle()
                page.get_by_role("button", name="START (Enter)").click()
                canvas = page.locator(".app-canvas")
                canvas.wait_for()

                for immersive in (True, False, True, False):
                    if immersive:
                        page.locator("[data-app-immersive]").click()
                    else:
                        page.locator("[data-app-immersive-exit]").click()
                    assert page.locator("body").evaluate(
                        "element => element.classList.contains('application-immersive')"
                    ) is immersive
                    assert runtime_node.evaluate(
                        "node => node === document.querySelector('[data-app-runtime]')"
                    )
                    assert runtime.evaluate("element => { const r = element.getBoundingClientRect(); return r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth; }")
                    before = canvas.evaluate("element => element.toDataURL()")
                    page.wait_for_function(
                        "before => document.querySelector('.app-canvas').toDataURL() !== before",
                        arg=before,
                        timeout=3000,
                    )
                assert page.locator("[data-app-status]").inner_text().endswith("RUNNING")
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
