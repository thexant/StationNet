"""Browser checks for the built-in ASCII/ANSI Art Studio."""

import json
import threading
import time

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "touch"])
def test_art_studio_editor_and_exports(tmp_path, mobile):
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
            ("artist", "ART-1", "unused"),
        ).lastrowid
        database.commit()

    @app.get("/test-art-login")
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
                    is_mobile=mobile, has_touch=mobile, accept_downloads=True,
                )
                page = context.new_page()
                base = f"http://127.0.0.1:{server.server_port}"
                page.goto(base + "/test-art-login")
                page.goto(base + "/games-apps/ascii-ansi-art-studio")
                if page.locator("[data-app-launch-anyway]").is_visible():
                    page.locator("[data-app-launch-anyway]").click()
                page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)
                grid = page.locator('[data-app-component="art-canvas"]')
                grid.locator(".app-cell").first.wait_for()
                assert grid.locator(".app-cell").count() == 32 * 16
                page.screenshot(path=str(tmp_path / "art-studio-initial.png"), full_page=True)

                # Pointer capture routes touch movement to the grid. The host
                # resolves the cell from screen coordinates, then the editor fills
                # the cells between pointer samples.
                page.evaluate("""() => {
                    const grid = document.querySelector('[data-app-component="art-canvas"]');
                    const cells = grid.querySelectorAll('.app-cell');
                    const center = cell => {
                      const rect = cell.getBoundingClientRect();
                      return { clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };
                    };
                    cells[0].dispatchEvent(new PointerEvent('pointerdown', {
                      bubbles: true, button: 0, pointerId: 42, pointerType: 'touch', ...center(cells[0])
                    }));
                    grid.dispatchEvent(new PointerEvent('pointermove', {
                      bubbles: true, button: 0, pointerId: 42, pointerType: 'touch', ...center(cells[4])
                    }));
                    grid.dispatchEvent(new PointerEvent('pointerup', {
                      bubbles: true, button: 0, pointerId: 42, pointerType: 'touch', ...center(cells[4])
                    }));
                }""")
                page.wait_for_function("""() => {
                  const cells = document.querySelectorAll('[data-app-component="art-canvas"] .app-cell');
                  return cells[4]?.textContent === '#';
                }""")
                assert [grid.locator(".app-cell").nth(i).inner_text() for i in range(5)] == ["#"] * 5
                page.get_by_role("button", name="Undo", exact=True).click()
                page.wait_for_function("""() =>
                  document.querySelector('[data-app-component="art-canvas"] .app-cell')?.textContent === ' '
                """)
                assert grid.locator(".app-cell").nth(4).inner_text() == ""

                page.get_by_label("Width (4–80)").fill("80")
                page.get_by_label("Height (4–50)").fill("50")
                page.get_by_role("button", name="Resize", exact=True).click()
                page.wait_for_function("""() =>
                  document.querySelectorAll('[data-app-component="art-canvas"] .app-cell').length === 4000
                """, timeout=15000)
                started = time.perf_counter()
                grid.locator(".app-cell").first.click()
                page.wait_for_function("""() =>
                  document.querySelector('[data-app-component="art-canvas"] .app-cell')?.textContent === '#'
                """, timeout=10000)
                assert time.perf_counter() - started < 8

                page.get_by_role("tab", name="Import / Export").click()
                page.get_by_label("Paste artwork").fill("HELLO")
                page.get_by_role("button", name="Import pasted artwork").click()
                page.wait_for_timeout(300)
                assert page.locator("dialog.app-dialog").count() == 1, page.locator('[data-app-component="studio-status"]').inner_text()
                page.locator("dialog.app-dialog").get_by_role("button", name="Import").click()
                page.wait_for_function("""() =>
                  document.querySelector('[data-app-component="art-canvas"] .app-cell')?.textContent === 'H'
                """)
                page.get_by_label("Paste format").select_option("ansi")
                page.get_by_label("Paste artwork").fill("\x1b[91;44mR")
                page.get_by_role("button", name="Import pasted artwork").click()
                page.locator("dialog.app-dialog").get_by_role("button", name="Import").click()
                page.wait_for_function("""() =>
                  document.querySelector('[data-app-component="art-canvas"] .app-cell')?.textContent === 'R'
                """)
                assert "app-fg-9" in grid.locator(".app-cell").first.get_attribute("class")
                page.get_by_label("Paste artwork").fill("\x1b[2J")
                page.get_by_role("button", name="Import pasted artwork").click()
                page.locator('[data-app-component="studio-status"]').get_by_text("Unsupported ANSI", exact=False).wait_for()
                assert grid.locator(".app-cell").first.inner_text() == "R"

                page.get_by_role("button", name="Open .txt or .ans").click()
                with page.expect_file_chooser() as chooser:
                    page.locator(".application-pointer-prompt").get_by_role("button", name="CHOOSE FILE").click()
                chooser.value.set_files({"name": "sample.ans", "mimeType": "text/plain", "buffer": b"\x1b[32mG"})
                page.locator("dialog.app-dialog").get_by_role("button", name="Import").click()
                page.wait_for_function("""() =>
                  document.querySelector('[data-app-component="art-canvas"] .app-cell')?.textContent === 'G'
                """)
                assert "app-fg-2" in grid.locator(".app-cell").first.get_attribute("class")

                project = {
                    "format": 1, "name": "Portable", "width": 4, "height": 4,
                    "chars": ["Q   ", "    ", "    ", "    "],
                    "fg": [[7] * 4 for _ in range(4)],
                    "bg": [[0] * 4 for _ in range(4)],
                }
                page.get_by_role("button", name="Open project JSON").click()
                with page.expect_file_chooser() as chooser:
                    page.locator(".application-pointer-prompt").get_by_role("button", name="CHOOSE FILE").click()
                chooser.value.set_files({
                    "name": "portable.json", "mimeType": "application/json",
                    "buffer": json.dumps(project).encode("utf-8"),
                })
                page.locator("dialog.app-dialog").get_by_role("button", name="Open").click()
                page.wait_for_function("""() =>
                  document.querySelector('[data-app-component="art-canvas"] .app-cell')?.textContent === 'Q'
                """)

                page.get_by_label("Copy and text download format").select_option("ansi")
                with page.expect_download() as result:
                    page.get_by_role("button", name="Download selected text").click()
                assert result.value.suggested_filename.endswith(".ans")
                with page.expect_download() as result:
                    page.get_by_role("button", name="Download PNG").click()
                assert result.value.suggested_filename.endswith(".png")
                with page.expect_download() as result:
                    page.get_by_role("button", name="Download project JSON").click()
                assert result.value.suggested_filename.endswith(".json")

                page.wait_for_timeout(750)
                page.reload()
                page.locator("[data-app-status]").get_by_text("RUNNING").wait_for(timeout=15000)
                page.locator("dialog.app-dialog").get_by_role("button", name="Restore").click()
                page.wait_for_function("""() =>
                  document.querySelector('[data-app-component="art-canvas"] .app-cell')?.textContent === 'Q'
                """)
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
