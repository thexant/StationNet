"""Clock controls remain usable on desktop and touch layouts while time updates."""

import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "touch"])
def test_clock_forms_and_tabs_in_browser(tmp_path, mobile):
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

    @app.get("/test-clock-login")
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
                    is_mobile=mobile, has_touch=mobile,
                )
                page = context.new_page()
                base = f"http://127.0.0.1:{server.server_port}"
                page.goto(base + "/test-clock-login")
                page.goto(base + "/games-apps/clock")
                if mobile and page.locator("[data-app-launch-anyway]").is_visible():
                    page.locator("[data-app-launch-anyway]").click()
                page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)

                page.get_by_role("tab", name="Alarms").click()
                page.get_by_role("button", name="New alarm").click()
                clock = page.locator("[data-app-surface]")
                clock.locator('input[type="time"]').wait_for()
                name = clock.get_by_label("Name")
                name.fill("Station check")
                assert name.evaluate("element => element === document.activeElement")
                page.wait_for_timeout(1200)
                assert name.input_value() == "Station check"
                assert name.evaluate("element => element === document.activeElement")
                clock.locator('input[type="time"]').fill("07:30")
                clock.get_by_label("Repeat").select_option("weekdays")
                page.get_by_role("button", name="Save alarm").click()
                clock.get_by_text("07:30 · Station check").wait_for()

                page.get_by_role("tab", name="Alarms").focus()
                page.keyboard.press("ArrowRight")
                assert page.get_by_role("tab", name="Timers").get_attribute("aria-selected") == "true"

                page.get_by_role("tab", name="World").click()
                clock.get_by_label("Add a place").wait_for()
                assert clock.locator('input[type="date"]').count() == 1
                assert clock.locator('input[type="time"]').count() == 1
                page.get_by_role("tab", name="Settings").click()
                page.get_by_role("button", name="Test sound").click()
                assert page.locator("[data-app-status]").inner_text().endswith("RUNNING")
                assert clock.evaluate("element => element.getBoundingClientRect().width <= innerWidth + 1")

                page.reload()
                page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)
                page.get_by_role("tab", name="Alarms").click()
                clock.get_by_text("07:30 · Station check").wait_for()
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
