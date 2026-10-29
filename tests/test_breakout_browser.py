"""Smoke-test the packaged Breakout UI in desktop and touch browsers."""

import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


@pytest.fixture
def breakout_server(tmp_path):
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

    @app.get("/test-breakout-login")
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
def test_breakout_play_and_presentation(breakout_server, mobile):
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
            page.goto(breakout_server + "/test-breakout-login")
            page.goto(breakout_server + "/games-apps/breakout")
            page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)
            status = page.locator('[data-app-component="game-status"]')
            status.get_by_text("READY", exact=False).wait_for(timeout=15000)
            assert page.locator(".application-display").get_attribute("data-app-fullscreen-target") == "application"
            assert page.locator('[data-app-component="game-controls"] button').count() == 2

            canvas = page.locator('[data-app-component="playfield"] canvas')
            paddle_x = """() => {
              const canvas = document.querySelector('[data-app-component="playfield"] canvas');
              const pixels = canvas.getContext('2d').getImageData(0, 410, 640, 1).data;
              for (let x = 0; x < 640; x++) {
                const i = x * 4;
                if (pixels[i] === 85 && pixels[i + 1] === 255 && pixels[i + 2] === 255) return x;
              }
              return -1;
            }"""
            first_x = page.evaluate(paddle_x)
            assert first_x > 0
            canvas.scroll_into_view_if_needed()
            box = canvas.bounding_box()
            pointer_x = box["x"] + box["width"] * 0.82
            pointer_y = box["y"] + box["height"] * 0.82
            if mobile:
                page.touchscreen.tap(pointer_x, pointer_y)
            else:
                page.mouse.move(pointer_x, pointer_y)
            page.wait_for_function("start => { const c = document.querySelector('[data-app-component=playfield] canvas'); "
                                   "const p = c.getContext('2d').getImageData(0, 410, 640, 1).data; "
                                   "for (let x = 0; x < 640; x++) { const i=x*4; "
                                   "if (p[i]===85 && p[i+1]===255 && p[i+2]===255) return x > start + 60; } "
                                   "return false; }", arg=first_x)

            page.locator('[data-app-component="playfield"] .app-canvas-controls button').click()
            status.get_by_text("RUNNING").wait_for(timeout=5000)
            page.evaluate("""() => {
              window.__breakoutFrames = 0;
              window.addEventListener('message', event => {
                if (event.data?.type === 'render' && event.data.target === 'playfield')
                  window.__breakoutFrames += 1;
              });
            }""")
            page.wait_for_timeout(1000)
            assert page.evaluate("window.__breakoutFrames") >= 15, "animated canvas keeps updating"
            page.keyboard.press("p")
            status.get_by_text("PAUSED", exact=False).wait_for(timeout=5000)
            page.keyboard.press("Space")
            status.get_by_text("RUNNING").wait_for(timeout=5000)

            page.locator('[data-app-component="actions"] button', has_text="Focus view").click()
            page.wait_for_function("() => document.body.classList.contains('application-immersive')")
            page.locator('[data-app-component="actions"] button', has_text="Exit focus").click()
            page.wait_for_function("() => !document.body.classList.contains('application-immersive')")

            page.locator("[data-app-fullscreen]").click()
            page.wait_for_function("() => document.fullscreenElement?.dataset.fullscreenTarget === 'application'")
            assert page.locator('[data-app-component="playfield"] canvas').is_visible()
            page.locator("[data-app-fullscreen-exit]").click()
            page.wait_for_function("() => document.fullscreenElement === null")
            assert "ERROR" not in page.locator("[data-app-status]").inner_text()
            context.close()
        finally:
            browser.close()
