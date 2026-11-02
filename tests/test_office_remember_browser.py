"""Browser test for OFFICE remember-me: what keeps, and what erases, the stored key."""
import itertools
import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db

PASSWORD = "correct horse battery staple"
REMEMBERED = "() => localStorage.getItem('stationnet.office.remembered.v1')"


def test_office_remember_me(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name,description,callsign,office_enabled) VALUES(?,?,?,1)",
            ("Office Test", "Test", "OFC-1"),
        )
        writer = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("writer", "WRT-1", "unused"),
        ).lastrowid
        reader = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("reader", "RDR-1", "unused"),
        ).lastrowid
        database.commit()

    sign_ins = itertools.count(1)

    @app.get("/test-office-login/<int:user_id>")
    def test_login(user_id):
        session.clear()
        session["user_id"] = user_id
        session["csrf_token"] = f"test-token-{next(sign_ins)}"
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
                context = browser.new_context()
                base = f"http://127.0.0.1:{server.server_port}"
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def sign_in(user_id):
                    page.goto(f"{base}/test-office-login/{user_id}")

                def open_tab(path="/office/documents"):
                    tab = context.new_page()
                    tab.goto(base + path)
                    return tab

                def expect_unlocked(tab, prefix="document"):
                    tab.locator(f"#{prefix}-lock").wait_for(state="visible", timeout=15000)
                    assert tab.locator(f"#{prefix}-unlock").is_hidden()
                    tab.close()

                def expect_locked(tab):
                    tab.wait_for_load_state("load")
                    assert tab.evaluate("() => sessionStorage.getItem('stationnet.office.unlock.v1')") is None
                    assert tab.locator("#document-unlock").is_visible()
                    assert tab.locator("#document-lock").is_hidden()
                    tab.close()

                def unlock(remember):
                    page.goto(base + "/office/documents")
                    page.locator("#document-passphrase").fill(PASSWORD)
                    page.locator("#document-remember").set_checked(remember)
                    page.locator("#document-unlock-form button").click()
                    # The password is confirmed only while no files are saved.
                    confirm = page.locator("#document-passphrase-confirm")
                    page.locator("#document-passphrase-confirm:visible, #document-lock:visible").first.wait_for(timeout=15000)
                    if confirm.is_visible():
                        confirm.fill(PASSWORD)
                        page.locator("#document-unlock-form button").click()
                    page.locator("#document-lock").wait_for(state="visible", timeout=15000)

                sign_in(writer)

                # Unticked: the unlock stays in the tab.
                unlock(False)
                assert page.evaluate(REMEMBERED) is None
                expect_locked(open_tab())
                page.locator("#document-lock").click()

                # Ticked: every program opens unlocked in a new tab.
                unlock(True)
                assert page.evaluate(REMEMBERED) is not None
                expect_unlocked(open_tab())
                expect_unlocked(open_tab("/office/sheets"), "sheets")

                # A network failure at startup does not erase the key.
                context.route("**/office/draw/api**", lambda route: route.abort())
                failed = open_tab("/office/draw")
                failed.wait_for_load_state("networkidle")
                assert failed.evaluate(REMEMBERED) is not None
                failed.close()
                context.unroute("**/office/draw/api**")

                # Survives a new sign-in, which has a new CSRF token.
                sign_in(writer)
                expect_unlocked(open_tab())

                # LOCK erases it.
                page.goto(base + "/office/documents")
                page.locator("#document-lock").click()
                assert page.evaluate(REMEMBERED) is None
                expect_locked(open_tab())

                # DISCONNECT erases it.
                unlock(True)
                page.goto(base + "/")
                page.locator("form[data-office-forget] button").click()
                page.wait_for_load_state("networkidle")
                assert page.evaluate(REMEMBERED) is None
                sign_in(writer)
                expect_locked(open_tab())

                # Another account cannot use it, and removes it.
                unlock(True)
                sign_in(reader)
                expect_locked(open_tab())
                assert page.evaluate(REMEMBERED) is None

                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join()
