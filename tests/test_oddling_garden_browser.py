"""Oddling Garden keeps its forms and controls usable in desktop and touch layouts."""

import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "touch"])
def test_oddling_garden_flow_in_browser(tmp_path, mobile):
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
            ("gardener", "GRD-1", "unused"),
        ).lastrowid
        database.commit()

    @app.get("/test-oddling-login")
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
                page.goto(base + "/test-oddling-login")
                page.goto(base + "/games-apps/oddling-garden")
                if mobile and page.locator("[data-app-launch-anyway]").is_visible():
                    page.locator("[data-app-launch-anyway]").click()
                page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)
                garden = page.locator("[data-app-surface]")

                name = garden.get_by_label("Give your Oddling a name")
                name.fill("Moth")
                garden.get_by_role("button", name="Quibble").click()
                assert name.input_value() == "Moth"
                garden.get_by_role("button", name="Immersive").click()
                assert name.input_value() == "Moth"
                garden.get_by_role("button", name="Adopt egg").click()
                garden.get_by_text("Stage: Egg").wait_for()
                for _ in range(4):
                    garden.get_by_role("button", name="Warm egg").click()
                page.wait_for_timeout(1300)
                page.reload()
                page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)
                garden.get_by_text("Stage: Baby").wait_for()

                canvas = garden.locator("canvas.app-canvas")
                canvas_handle = canvas.element_handle()
                garden.get_by_role("tab", name="Food").click()
                garden.get_by_role("button", name="Feed meal").click()
                assert canvas_handle.evaluate("element => element.isConnected")

                garden.get_by_role("tab", name="Garden").click()
                garden.get_by_role("button", name="Moonfern").click()
                garden.get_by_text("Moonfern | Growth").wait_for()
                garden.get_by_role("button", name="Life journal").click()
                garden.get_by_label("One favorite memory (140 characters)").fill("A lovely day")
                garden.get_by_role("button", name="Save memory").click()
                garden.get_by_role("button", name="Return to habitat").click()

                garden.get_by_role("tab", name="Options").click()
                garden.get_by_role("button", name="Zoom in").click()
                garden.get_by_label("Reduce animation").check()
                assert garden.get_by_label("Reduce animation").is_checked()

                garden.get_by_role("tab", name="Play").click()
                garden.get_by_role("button", name="Play Spark Chase (20 seconds)").click()
                garden.get_by_role("button", name="End round early").click()
                garden.get_by_text("no care bonus was awarded", exact=False).wait_for()
                assert garden.evaluate("element => element.getBoundingClientRect().width <= innerWidth + 1")
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
