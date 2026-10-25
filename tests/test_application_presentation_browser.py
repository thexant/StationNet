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


@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "mobile"])
def test_browser_fullscreen_restores_layout_and_scales_canvas(tmp_path, mobile):
    playwright = pytest.importorskip("playwright.sync_api")
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute("INSERT INTO station(name,description,callsign) VALUES(?,?,?)", ("Test Station", "Test", "TST-1"))
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
                context = browser.new_context(
                    viewport={"width": 390, "height": 844} if mobile else {"width": 1280, "height": 800},
                    is_mobile=mobile, has_touch=mobile,
                )
                page = context.new_page()
                url = f"http://127.0.0.1:{server.server_port}"
                page.goto(url + "/test-application-login")
                page.goto(url + "/games-apps/light-cycles")
                if mobile and page.locator("[data-app-launch-anyway]").is_visible():
                    page.locator("[data-app-launch-anyway]").click()
                page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)
                page.get_by_role("button", name="START (Enter)").click()
                canvas = page.locator(".app-canvas")
                canvas.wait_for()
                if mobile:
                    page.evaluate("""() => {
                      const frame = document.querySelector('[data-app-runtime]');
                      window.dispatchEvent(new MessageEvent('message', { source: frame.contentWindow,
                        data: { protocol: 'stationnet-app-v1', type: 'render', mode: 'update', target: 'field',
                          components: [{ type: 'canvas', id: 'field', width: 640, height: 422,
                            commands: [], touchLayout: 'gamepad', controls: [{ label: 'Fire', code: 'KeyZ' }] }] } }));
                    }""")
                    assert page.locator(".app-canvas-gamepad").count() == 1
                runtime_node = page.locator("[data-app-runtime]").element_handle()
                if not mobile:
                    page.locator("[data-app-immersive]").click()
                was_immersive = page.locator("body").evaluate("node => node.classList.contains('application-immersive')")
                page.locator("[data-app-fullscreen]").click()
                page.wait_for_function("document.fullscreenElement === document.querySelector('.application-display')")
                assert page.locator("[data-app-fullscreen-exit]").is_visible()
                assert page.locator(".station-header").is_hidden()
                metrics = canvas.evaluate("""node => {
                  const r = node.getBoundingClientRect();
                  return { width: r.width, height: r.height, x: r.x, y: r.y,
                    screenWidth: innerWidth, screenHeight: innerHeight,
                    logicalWidth: node.width, logicalHeight: node.height };
                }""")
                assert metrics["x"] >= 0 and metrics["y"] >= 0
                assert metrics["x"] + metrics["width"] <= metrics["screenWidth"] + 1
                assert metrics["y"] + metrics["height"] <= metrics["screenHeight"] + 1
                assert abs(metrics["width"] / metrics["height"] - metrics["logicalWidth"] / metrics["logicalHeight"]) < 0.03
                if mobile:
                    assert page.locator(".app-canvas-gamepad").is_visible()
                    page.set_viewport_size({"width": 844, "height": 390})
                    page.wait_for_function("innerWidth > innerHeight")
                    assert page.locator(".app-canvas-gamepad").is_visible()
                    landscape = canvas.bounding_box()
                    assert landscape["x"] >= 0 and landscape["y"] >= 0
                    assert landscape["x"] + landscape["width"] <= 845
                    assert landscape["y"] + landscape["height"] <= 391
                assert runtime_node.evaluate("node => node === document.querySelector('[data-app-runtime]')")
                page.locator("[data-app-fullscreen-exit]").click()
                page.wait_for_function("document.fullscreenElement === null")
                assert page.locator("body").evaluate("node => node.classList.contains('application-immersive')") is was_immersive
                assert page.locator("[data-app-fullscreen]").get_attribute("aria-pressed") == "false"
                assert page.locator("[data-app-status]").inner_text().endswith("RUNNING")
                if not mobile:
                    page.locator("[data-app-fullscreen]").click()
                    page.wait_for_function("document.fullscreenElement !== null")
                    page.keyboard.press("Escape")
                    page.wait_for_function("document.fullscreenElement === null")
                    assert page.locator("body").evaluate("node => node.classList.contains('application-immersive')") is was_immersive
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
