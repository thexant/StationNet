"""Canvas pointer coordinates follow the rendered canvas through page zoom."""

import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


@pytest.fixture
def application_server(tmp_path):
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
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def open_calculator(browser, base_url, mobile=False):
    context = browser.new_context(
        viewport={"width": 390, "height": 844} if mobile else {"width": 1280, "height": 800},
        is_mobile=mobile,
        has_touch=mobile,
    )
    page = context.new_page()
    page.goto(base_url + "/test-application-login")
    page.goto(base_url + "/games-apps/calculator-v2")
    page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)
    canvas = page.locator(".app-canvas")
    canvas.wait_for()
    frame = page.locator("[data-app-runtime]").element_handle().content_frame()
    return context, page, canvas, frame


@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "touch"])
def test_canvas_pointer_coordinates_across_presentations(application_server, mobile):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as error:
            pytest.skip(f"Chromium is unavailable: {error}")
        try:
            context, page, canvas, frame = open_calculator(browser, application_server, mobile)
            frame.evaluate("""() => {
              window.__pointerEvents = [];
              window.addEventListener('message', event => {
                if (event.data?.type === 'interaction' && event.data.phase === 'start' &&
                    typeof event.data.x === 'number') window.__pointerEvents.push(event.data);
              });
            }""")

            def check_points():
                for fraction in (0.15, 0.5, 0.85):
                    canvas.scroll_into_view_if_needed()
                    box = canvas.bounding_box()
                    x = box["x"] + box["width"] * fraction
                    y = box["y"] + box["height"] * fraction
                    count = frame.evaluate("window.__pointerEvents.length")
                    if mobile:
                        page.touchscreen.tap(x, y)
                    else:
                        page.mouse.click(x, y)
                    frame.wait_for_function("count => window.__pointerEvents.length > count", arg=count)
                    event = frame.evaluate("window.__pointerEvents.at(-1)")
                    assert event["x"] == pytest.approx(480 * fraction, abs=2)
                    assert event["y"] == pytest.approx(800 * fraction, abs=2)
                    assert event["pointerType"] == ("touch" if mobile else "mouse")

            assert page.evaluate("getComputedStyle(document.documentElement).zoom") == ("1" if mobile else "1.25")
            check_points()
            page.locator("[data-app-immersive]").click()
            page.wait_for_function("() => document.body.classList.contains('application-immersive')")
            check_points()
            page.evaluate("""() => {
              const frame = document.querySelector('[data-app-runtime]');
              window.dispatchEvent(new MessageEvent('message', { source: frame.contentWindow,
                data: { protocol: 'stationnet-app-v1', type: 'capability',
                  operation: 'presentation-request-fullscreen', target: 'canvas', request: 'pointer-test' } }));
            }""")
            page.get_by_role("button", name="ENTER FULLSCREEN").click()
            page.wait_for_function("() => document.fullscreenElement?.dataset.fullscreenTarget === 'canvas'")
            check_points()
            context.close()
        finally:
            browser.close()


def test_calculator_keypad_clicks_match_visible_keys(application_server):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as error:
            pytest.skip(f"Chromium is unavailable: {error}")
        try:
            context, page, canvas, _ = open_calculator(browser, application_server)
            page.evaluate("""() => {
              window.__calculatorDisplay = {};
              window.addEventListener('message', event => {
                if (event.data?.type !== 'render' || event.data.target !== 'calc') return;
                const commands = event.data.components?.[0]?.commands || [];
                window.__calculatorDisplay = {
                  expression: commands.find(command => command.type === 'text' && command.x === 450 && command.y === 155)?.text,
                  result: commands.find(command => command.type === 'text' && command.x === 451 && command.y === 207)?.text,
                };
              });
            }""")

            def press(x, y, expression, result=None):
                canvas.scroll_into_view_if_needed()
                box = canvas.bounding_box()
                page.mouse.click(box["x"] + box["width"] * x / 480,
                                 box["y"] + box["height"] * y / 800)
                page.wait_for_function(
                    "expected => window.__calculatorDisplay.expression === expected",
                    arg=expression,
                )
                if result is not None:
                    assert page.evaluate("window.__calculatorDisplay.result") == result

            press(65, 350, "0")  # Clear any saved expression.
            press(65, 507, "7")
            press(410, 663, "7+")
            press(180, 663, "7+2")
            press(410, 741, "7+2 =", "9")
            context.close()
        finally:
            browser.close()
