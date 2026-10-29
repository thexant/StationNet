"""Smoke-test Snake on desktop and touch browser layouts."""

import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


@pytest.fixture
def snake_server(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute("INSERT INTO station(name,description,callsign) VALUES(?,?,?)",
                         ("Test Station", "Test", "TST-1"))
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("player", "PLY-1", "unused"),
        ).lastrowid
        database.commit()

    @app.get("/test-snake-login")
    def test_login():
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
        return "OK"

    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "touch"])
def test_snake_layout_and_controls(snake_server, mobile):
    playwright = pytest.importorskip("playwright.sync_api")
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
            page.goto(snake_server + "/test-snake-login")
            page.goto(snake_server + "/games-apps/snake")
            status = page.locator('[data-app-component="game-status"]')
            status.get_by_text("READY", exact=False).wait_for(timeout=15000)
            canvas = page.locator('[data-app-component="playfield"] canvas')
            assert canvas.get_attribute("width") == "600"
            assert canvas.get_attribute("height") == "400"
            assert page.locator(".application-display").get_attribute("data-app-fullscreen-target") == "application"
            assert page.locator('[data-app-component="score-status"]').inner_text()
            if mobile:
                assert page.locator('[data-app-component="playfield"] .app-canvas-gamepad').is_visible()
                assert page.locator('[data-app-component="playfield"] .app-canvas-gamepad button').count() >= 8
                page.set_viewport_size({"width": 844, "height": 390})
                assert canvas.is_visible()
                page.set_viewport_size({"width": 390, "height": 844})

            page.locator('[data-app-component="mode"] button', has_text="Sprint").click()
            page.locator('[data-app-component="hud"]').get_by_text("SPRINT", exact=False).wait_for()
            if mobile:
                page.locator(".app-gamepad-up").tap()
                status.get_by_text("RUNNING").wait_for()
                page.locator(".app-gamepad-action-1").tap()
                status.get_by_text("PAUSED", exact=False).wait_for()
                page.locator(".app-gamepad-start").tap()
            else:
                page.locator('[data-app-component="primary"]').click()
                status.get_by_text("RUNNING").wait_for()
                page.keyboard.press("p")
                status.get_by_text("PAUSED", exact=False).wait_for()
                page.keyboard.press("Space")
            status.get_by_text("RUNNING").wait_for()
            page.locator('[data-app-component="game-actions"] button', has_text="Focus view").click()
            page.wait_for_function("() => document.body.classList.contains('application-immersive')")
            assert canvas.is_visible()
            page.locator('[data-app-component="game-actions"] button', has_text="Exit focus view").click()
            page.wait_for_function("() => !document.body.classList.contains('application-immersive')")
        finally:
            browser.close()
