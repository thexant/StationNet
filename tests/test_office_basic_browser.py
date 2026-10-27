"""Browser tests for Office BASIC: QBJS runs in an isolated sandbox frame."""
import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db

PASSWORD = "correct horse battery staple"

# QBJS treats ' as a comment even in native blocks, so the JavaScript uses double quotes.
ISOLATION_PROGRAM = '''PRINT "probing"
$If Javascript Then
    var r = [];
    try { r.push("parent:" + String(window.parent.document.title)); } catch (e) { r.push("parent:blocked"); }
    try { r.push("cookie:" + document.cookie.length); } catch (e) { r.push("cookie:blocked"); }
    try { r.push("storage:" + localStorage.getItem("stationnet.office.unlock.v1")); } catch (e) { r.push("storage:blocked"); }
    try { var res = await fetch("/office/basic/api"); r.push("fetch:" + res.status); } catch (e) { r.push("fetch:blocked"); }
    try { var x = new XMLHttpRequest(); x.open("GET", "/office/basic/api", false); x.send(); r.push("xhr:" + x.status); } catch (e) { r.push("xhr:blocked"); }
    try { top.location.href = "/logout"; r.push("nav:attempted"); } catch (e) { r.push("nav:blocked"); }
    r.push("rtc:" + typeof RTCPeerConnection);
    r.push("open:" + String(window.open("/logout")));
    try { var f = document.createElement("iframe"); document.body.appendChild(f); r.push("child:" + String(f.contentWindow)); } catch (e) { r.push("child:blocked"); }
    console.log("ISOLATION " + r.join(" "));
$End If
'''


@pytest.fixture()
def basic_page(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name,description,callsign,office_enabled) VALUES(?,?,?,1)",
            ("Basic Test", "Test", "BAS-1"),
        )
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("coder", "COD-1", "unused"),
        ).lastrowid
        database.commit()

    @app.get("/test-basic-login")
    def test_login():
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
        return "OK"

    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as p:
            # Prefer the full Chromium build: like desktop Chrome it runs sandboxed
            # frames out of process. Playwright's headless shell does not.
            isolated = True
            try:
                browser = p.chromium.launch(headless=True, channel="chromium")
            except Exception:
                isolated = False
                try:
                    browser = p.chromium.launch(headless=True)
                except Exception as error:
                    pytest.skip(f"Chromium is unavailable: {error}")
            try:
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                page.isolated = isolated
                page.errors = []
                page.on("pageerror", lambda error: page.errors.append(str(error)))
                page.base = f"http://127.0.0.1:{server.server_port}"
                page.goto(page.base + "/test-basic-login")
                page.goto(page.base + "/office/basic")
                page.locator("#basic-password").fill(PASSWORD)
                page.locator("#basic-unlock-form button").click()
                page.locator("#basic-confirm").fill(PASSWORD)
                page.locator("#basic-unlock-form button").click()
                page.locator("#basic-status").get_by_text("EXAMPLE").wait_for(timeout=20000)
                yield page
                # Closing the page and context first keeps browser.close() from
                # waiting on a sandbox process that was stopped while busy.
                page.close()
                context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()


def set_source(page, source):
    page.evaluate("source => document.querySelector('.CodeMirror').CodeMirror.setValue(source)", source)


def run_program(page, source, state="FINISHED", timeout=20000):
    set_source(page, source)
    page.locator("#basic-run").click()
    try:
        page.locator("#basic-run-state").get_by_text(state, exact=True).wait_for(timeout=timeout)
    except Exception as error:
        raise AssertionError(f"state {page.locator('#basic-run-state').inner_text()!r}; console:\n{console_text(page)}") from error


def console_text(page):
    return page.locator("#basic-console").inner_text()


def test_basic_runs_programs_and_reports_errors(basic_page):
    page = basic_page
    assert page.locator(".CodeMirror .cm-keyword").count() > 0, "QBJS syntax highlighting is active"
    run_program(page, 'PRINT "Hello"\n_ECHO "echo-ok " + STR$(6 * 7)\n')
    assert "echo-ok  42" in console_text(page)
    frame = page.locator("#basic-screen iframe")
    assert frame.get_attribute("sandbox") == "allow-scripts"
    assert page.frame_locator("#basic-screen iframe").locator("#gx-canvas").count() == 1

    run_program(page, 'PRINT "ok"\nIF 1 THEN\nPRINT "never closed"\n', state="ERROR")
    errors = page.locator('#basic-console li[data-kind="error"]')
    assert errors.count() >= 1
    errors.first.locator("button").click()
    assert page.evaluate("document.querySelector('.CodeMirror').CodeMirror.getCursor().line") >= 0
    assert page.locator(".basic-gutter-marker").count() >= 1

    run_program(page, 'PRINT "before"\n$If Javascript Then\n    throw new Error("boom");\n$End If\n', state="ERROR")
    runtime = page.locator('#basic-console li[data-kind="error"]').last
    assert "RUNTIME ERROR: boom" in runtime.inner_text()
    assert "Line 3" in runtime.inner_text()

    run_program(page, 'IMPORT G2D FROM "lib/graphics/2d.bas"\nSCREEN _NEWIMAGE(200, 100, 32)\nG2D.FillCircle 50, 50, 20, _RGB(255, 0, 0)\n_ECHO "lib-ok"\n')
    assert "lib-ok" in console_text(page)
    assert not page.errors


def test_basic_programs_are_isolated_from_stationnet(basic_page):
    page = basic_page
    key_before = page.evaluate("sessionStorage.getItem('stationnet.office.unlock.v1')")
    assert key_before
    run_program(page, ISOLATION_PROGRAM)
    line = next(text for text in console_text(page).splitlines() if text.startswith("ISOLATION"))
    for expected in ("parent:blocked", "cookie:blocked", "storage:null", "fetch:404", "xhr:blocked",
                     "rtc:undefined", "open:null", "child:null"):
        assert expected in line, line
    assert page.url.endswith("/office/basic")
    assert page.evaluate("sessionStorage.getItem('stationnet.office.unlock.v1')") == key_before
    assert not page.errors


def test_basic_stop_keeps_the_editor_responsive(basic_page):
    page = basic_page
    run_program(page, "DO\n    x = x + 1\nLOOP\n", state="RUNNING")
    page.wait_for_timeout(1500)
    assert page.evaluate("1 + 1") == 2
    page.locator("#basic-title").fill("Still typing")
    page.locator("#basic-stop").click()
    page.locator("#basic-run-state").get_by_text("STOPPED", exact=True).wait_for(timeout=5000)
    assert page.locator("#basic-screen iframe").count() == 0
    assert page.locator("#basic-stop").is_disabled()
    run_program(page, '_ECHO "after-stop"\n')
    assert "after-stop" in console_text(page)


def test_basic_examples_compile_and_start(basic_page):
    page = basic_page
    values = page.locator("#basic-examples option").evaluate_all("options => options.map(o => o.value).filter(Boolean)")
    assert len(values) >= 4
    for value in values:
        page.locator("#basic-examples").select_option(value)
        page.locator("#basic-status").get_by_text("EXAMPLE").wait_for(timeout=5000)
        page.locator("#basic-run").click()
        page.wait_for_function(
            "() => ['running', 'ended'].includes(document.querySelector('#basic-run-state').dataset.state)"
            " || document.querySelector('#basic-run-state').dataset.state === 'error'", timeout=20000)
        state = page.locator("#basic-run-state").get_attribute("data-state")
        assert state in ("running", "ended"), f"{value}: {state}\n{console_text(page)}"
        page.wait_for_timeout(300)
        assert page.locator("#basic-run-state").get_attribute("data-state") != "error", f"{value}\n{console_text(page)}"
        page.locator("#basic-stop").click()
    assert not page.errors


def test_basic_stop_ends_runaway_javascript(basic_page):
    page = basic_page
    if not page.isolated:
        pytest.skip("Only browsers that run sandboxed frames out of process can interrupt a JavaScript busy loop.")
    run_program(page, "$If Javascript Then\n    while (true) {}\n$End If\n", state="RUNNING")
    page.locator("#basic-run-state").get_by_text("NOT RESPONDING — PRESS STOP").wait_for(timeout=15000)
    page.locator("#basic-stop").click()
    page.locator("#basic-run-state").get_by_text("STOPPED", exact=True).wait_for(timeout=5000)
    run_program(page, '_ECHO "recovered"\n')
    assert "recovered" in console_text(page)


def test_basic_save_reopen_import_and_export(basic_page, tmp_path):
    page = basic_page
    page.locator("#basic-title").fill("Round trip")
    set_source(page, 'PRINT "saved program"\n')
    page.locator("#basic-save").click()
    page.locator("#basic-status").get_by_text("SAVED", exact=True).wait_for(timeout=15000)
    page.reload()
    page.locator("#basic-status").get_by_text("SAVED", exact=True).wait_for(timeout=15000)
    assert page.locator("#basic-title").input_value() == "Round trip"
    assert page.evaluate("document.querySelector('.CodeMirror').CodeMirror.getValue()") == 'PRINT "saved program"\n'
    assert page.locator("#basic-list button", has_text="Round trip").count() == 1

    with page.expect_download() as download:
        page.locator("#basic-export").click()
    exported = tmp_path / "export.bas"
    download.value.save_as(exported)
    assert download.value.suggested_filename == "Round trip.bas"
    assert exported.read_bytes() == b'PRINT "saved program"\r\n'

    source = tmp_path / "GREETING.BAS"
    source.write_bytes(b'PRINT "caf\xe9"\r\n')
    page.locator("#basic-import-file").set_input_files(str(source))
    page.locator("#basic-list button", has_text="GREETING").wait_for(timeout=15000)
    assert page.locator("#basic-title").input_value() == "GREETING"
    # CodeMirror normalizes line endings; EXPORT writes CRLF again.
    assert page.evaluate("document.querySelector('.CodeMirror').CodeMirror.getValue()") == 'PRINT "café"\n'
    assert not page.errors
