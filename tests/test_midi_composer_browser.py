"""MIDI Composer remains readable and editable in desktop and touch layouts."""

import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


@pytest.fixture
def composer_server(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign) VALUES(?,?,?)",
                   ("Composer Test", "Test", "CMP-1"))
        user_id = db.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("composer", "CMP-2", "unused"),
        ).lastrowid
        db.commit()

    @app.get("/test-composer-login")
    def login():
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
def test_composer_grid_is_readable_and_editable(composer_server, mobile):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as error:
            pytest.skip(f"Chromium is unavailable: {error}")
        context = browser.new_context(
            viewport={"width": 390, "height": 844} if mobile else {"width": 1280, "height": 800},
            is_mobile=mobile, has_touch=mobile,
        )
        try:
            page = context.new_page()
            page.goto(composer_server + "/test-composer-login")
            page.goto(composer_server + "/games-apps/midi-composer")
            page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)
            canvas = page.locator(".app-canvas")
            canvas.wait_for()
            dimensions = canvas.evaluate(
                "(c) => ({width:c.width,height:c.height,shown:c.getBoundingClientRect().width})"
            )
            assert dimensions["width"] == (372 if mobile else 840)
            assert dimensions["shown"] >= (300 if mobile else 650)
            row_size = 24 if mobile else 18
            assert dimensions["shown"] / dimensions["width"] * row_size >= (18 if mobile else 12)
            assert page.get_by_text("Transport", exact=True).count() == 1
            assert page.get_by_text("Tools", exact=True).count() == 1

            canvas.scroll_into_view_if_needed()
            box = canvas.bounding_box()
            x = box["x"] + box["width"] * 140 / dimensions["width"]
            y = box["y"] + box["height"] * 150 / dimensions["height"]
            if mobile:
                page.touchscreen.tap(x, y)
            else:
                page.mouse.click(x, y)
            page.wait_for_function("""() => {
              const canvas = document.querySelector('.app-canvas');
              const pixel = canvas.getContext('2d').getImageData(150, 159, 1, 1).data;
              return pixel[0] > 180 && pixel[1] > 130 && pixel[2] > 70;
            }""", timeout=5000)
            assert page.get_by_text("1 note selected", exact=False).count() == 1
        finally:
            context.close()
            browser.close()
