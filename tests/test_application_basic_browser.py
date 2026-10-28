"""Browser tests for installed BASIC applications in Games & Apps."""
import json
import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.applications import install_package
from stationnet.db import get_db
from basic_package_support import TEXT_PROGRAM, graphics_package, package
from test_office_basic_browser import ISOLATION_PROGRAM


@pytest.fixture()
def station(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute("INSERT INTO station(name,description,callsign) VALUES(?,?,?)", ("Basic Apps", "Test", "BAP-1"))
        sysop = database.execute("INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,1)",
                                 ("sysop", "SYS-1", "unused")).lastrowid
        member = database.execute("INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
                                  ("coder", "COD-1", "unused")).lastrowid
        database.commit()
        install_package(graphics_package())
        install_package(package("probe", ISOLATION_PROGRAM, category="Utilities"))

    @app.get("/test-login/<int:user_id>")
    def test_login(user_id):
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
        return "OK"

    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, channel="chromium")
            except Exception:
                try:
                    browser = p.chromium.launch(headless=True)
                except Exception as error:
                    pytest.skip(f"Chromium is unavailable: {error}")
            base = f"http://127.0.0.1:{server.server_port}"

            def open_page(user_id, **options):
                context = browser.new_context(accept_downloads=True, **options)
                page = context.new_page()
                page.errors = []
                page.on("pageerror", lambda error: page.errors.append(str(error)))
                page.base = base
                page.goto(f"{base}/test-login/{user_id}")
                return page
            try:
                yield app, open_page, sysop, member
            finally:
                for context in browser.contexts:
                    context.close()
                browser.close()
    finally:
        server.shutdown()


def console(page):
    return page.locator("[data-basic-console]").text_content()


def wait_console(page, text, timeout=20000):
    try:
        page.wait_for_function("text => document.querySelector('[data-basic-console]').textContent.includes(text)",
                               arg=text, timeout=timeout)
    except Exception as error:
        status = page.locator("[data-app-status]").inner_text()
        raise AssertionError(f"{text!r} not seen; {status}; console:\n{console(page)}") from error


def wait_status(page, label, timeout=20000):
    page.locator("[data-app-status]").get_by_text(f"PROGRAM STATUS / {label}", exact=True).wait_for(timeout=timeout)


def program_frame(page):
    page.locator("[data-basic-screen] iframe").wait_for(timeout=10000)
    return next(frame for frame in page.frames if "/basic-runner" in frame.url)


def pixel(page, x, y):
    return program_frame(page).evaluate(
        "([x, y]) => Array.from(document.getElementById('gx-canvas').getContext('2d').getImageData(x, y, 1, 1).data)", [x, y])


def saved_value(page):
    return page.evaluate("""async () => {
        const response = await fetch(location.pathname + '/storage', { method: 'POST', headers: {
          'Content-Type': 'application/json', 'X-CSRF-Token': 'test-token' },
          body: JSON.stringify({ operation: 'get', scope: 'user', key: 'basic.localStorage' }) });
        return (await response.json()).value;
    }""")


def test_sysop_uploads_an_exported_text_program_and_users_run_it(station, tmp_path):
    app, open_page, sysop, member = station
    archive = tmp_path / "text-demo.zip"
    archive.write_bytes(package("text-demo", TEXT_PROGRAM, required_inputs=["keyboard"]))
    admin = open_page(sysop)
    admin.goto(admin.base + "/sysop/applications")
    admin.locator("input[name=package]").set_input_files(str(archive))
    admin.get_by_role("button", name="VALIDATE + INSTALL").click()
    admin.get_by_text("Text Demo 1.0.0 installed and enabled.").wait_for(timeout=10000)
    assert admin.locator(".application-admin-row", has_text="Text Demo").get_by_text("THIRD-PARTY / BASIC / GAMES").count() == 1

    page = open_page(member)
    page.goto(page.base + "/games-apps?selected=text-demo")
    assert "GAMES / THIRD-PARTY / BASIC" in page.content()
    page.goto(page.base + "/games-apps/text-demo")
    wait_console(page, "TEXT APP READY")
    wait_status(page, "RUNNING")
    assert page.locator("[data-app-compatibility]").inner_text().startswith("COMPATIBILITY / SUPPORTED")
    page.locator("[data-basic-screen]").click()
    page.keyboard.type("Ada", delay=60)
    page.keyboard.press("Enter")
    wait_console(page, "HELLO Ada")
    wait_status(page, "FINISHED")
    assert page.locator("[data-basic-stop]").is_disabled()

    page.locator("[data-basic-restart]").click()
    wait_status(page, "RUNNING")
    assert console(page).count("TEXT APP READY") == 2
    page.locator("[data-basic-stop]").click()
    wait_status(page, "STOPPED")
    assert page.locator("[data-basic-screen] iframe").count() == 0
    assert page.locator("[data-basic-idle]").is_visible()
    assert not page.errors and not admin.errors


def test_graphical_program_draws_loads_assets_and_keeps_saves(station):
    app, open_page, _, member = station
    page = open_page(member)
    page.goto(page.base + "/games-apps/pixel-demo")
    wait_console(page, "GRAPHICS APP READY")
    assert "ASSET LOADED" in console(page) and "RUNS 1" in console(page)
    assert "ERROR" not in console(page)
    wait_status(page, "RUNNING")
    assert pixel(page, 20, 20)[:3] == [255, 200, 0]
    assert pixel(page, 205, 25)[:3] == [255, 0, 255]
    assert pixel(page, 5, 150)[:3] == [0, 0, 80]

    page.locator("[data-basic-restart]").click()
    wait_console(page, "RUNS 2")
    # Saves reach the per-user application storage and survive a reload.
    for _ in range(50):
        if saved_value(page) == {"runs": " 2"}:
            break
        page.wait_for_timeout(100)
    assert saved_value(page) == {"runs": " 2"}
    page.reload()
    wait_console(page, "RUNS 3")
    with app.app_context():
        row = get_db().execute("SELECT owner_key,value_json FROM application_storage WHERE application_slug='pixel-demo'").fetchone()
    assert row["owner_key"] == f"user:{member}"
    page.wait_for_timeout(800)
    assert not page.errors


def test_installed_programs_stay_inside_the_sandbox(station):
    _, open_page, _, member = station
    page = open_page(member)
    page.goto(page.base + "/games-apps/probe")
    wait_console(page, "ISOLATION")
    line = next(text for text in page.locator("[data-basic-console] li").all_text_contents() if text.startswith("ISOLATION"))
    for expected in ("parent:blocked", "cookie:blocked", "storage:null", "fetch:404", "xhr:blocked",
                     "rtc:undefined", "open:null", "child:null"):
        assert expected in line, line
    assert page.url.endswith("/games-apps/probe")
    frame = page.locator("[data-basic-screen] iframe")
    assert frame.get_attribute("sandbox") == "allow-scripts"


def test_immersive_fullscreen_and_mobile_layouts(station):
    _, open_page, _, member = station
    page = open_page(member)
    page.goto(page.base + "/games-apps/pixel-demo")
    wait_console(page, "GRAPHICS APP READY")
    standard = page.locator("[data-basic-screen]").bounding_box()

    page.locator("[data-app-immersive]").click()
    assert page.evaluate("document.body.classList.contains('application-immersive')")
    assert page.locator("[data-app-immersive-exit]").is_visible()
    assert page.locator(".station-header").is_hidden()
    immersive = page.locator("[data-basic-screen]").bounding_box()
    assert immersive["height"] > standard["height"]
    page.locator("[data-app-immersive-exit]").click()
    assert not page.evaluate("document.body.classList.contains('application-immersive')")

    page.locator("[data-app-fullscreen]").click()
    page.wait_for_function("document.fullscreenElement?.classList.contains('application-display')", timeout=5000)
    assert page.locator("[data-basic-stop]").is_hidden()
    assert page.locator("[data-app-fullscreen-exit]").is_visible()
    page.locator("[data-app-fullscreen-exit]").click()
    page.wait_for_function("!document.fullscreenElement", timeout=5000)
    assert "RUNNING" in page.locator("[data-app-status]").inner_text()

    phone = open_page(member, viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    phone.goto(phone.base + "/games-apps/pixel-demo")
    wait_console(phone, "GRAPHICS APP READY")
    assert phone.evaluate("document.documentElement.scrollWidth <= innerWidth")
    box = phone.locator("[data-basic-screen]").bounding_box()
    assert box["width"] <= 390 and box["height"] > 200
    # The canvas is scaled to fit the program screen.
    assert program_frame(phone).evaluate(
        "() => document.getElementById('gx-canvas').getBoundingClientRect().width <= innerWidth + 1")
    assert not page.errors and not phone.errors


def test_requirements_that_are_not_met_pause_launch(station):
    app, open_page, _, member = station
    with app.app_context():
        install_package(package("pad-only", TEXT_PROGRAM, platforms=["touch"], required_inputs=["touch"]))
    page = open_page(member)
    page.goto(page.base + "/games-apps/pad-only")
    page.locator("[data-app-launch-warning]").wait_for(timeout=5000)
    assert "REQUIREMENTS NOT MET" in page.locator("[data-app-compatibility]").inner_text()
    assert page.locator("[data-basic-screen] iframe").count() == 0
    page.locator("[data-app-launch-anyway]").click()
    wait_console(page, "TEXT APP READY")
    assert json.loads(page.locator(".application-display").get_attribute("data-app-inputs")) == ["touch"]
