"""Optional browser smoke check: python scripts/verify-office-documents.py."""
import tempfile
import threading
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import WSGIRequestHandler, make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stationnet import create_app
from stationnet.db import get_db
from stationnet.security import hash_password


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


def main():
    with tempfile.TemporaryDirectory(prefix="office-browser-", dir=Path.cwd()) as data_dir:
        app = create_app({"TESTING": True, "DATA_DIR": data_dir})
        with app.app_context():
            database = get_db()
            database.execute(
                "INSERT INTO station(name,description,callsign,office_enabled) VALUES(?,?,?,1)",
                ("Test Station", "Office test", "TST-123"),
            )
            database.execute(
                "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
                ("member", "MEM-123", hash_password("station-password")),
            )
            database.commit()
        server = make_server("127.0.0.1", 0, app, request_handler=QuietHandler)
        base_url = f"http://127.0.0.1:{server.port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"{base_url}/login")
                page.locator('input[name="username"]').fill("member")
                page.locator('input[name="password"]').fill("station-password")
                page.locator(".auth-submit").click()
                page.goto(f"{base_url}/office")
                assert page.get_by_role("link", name="DOCUMENTS").count() == 1
                page.get_by_role("link", name="DOCUMENTS").click()
                page.locator("body.documents-page").wait_for(timeout=10000)
                page.locator("#document-passphrase").fill("document-passphrase-123")
                page.locator("#document-unlock-form button").click()
                page.locator("#document-passphrase-confirm").fill("document-passphrase-123")
                page.locator("#document-unlock-form button").click()
                page.wait_for_timeout(5000)
                assert page.locator("#document-workspace").is_visible(), (page.locator("#document-status").inner_text(), page.locator("#document-unlock-error").inner_text(), errors)
                page.locator("#document-title").fill("Browser test")
                page.locator(".documents-paper .tiptap").fill("Private content with a sentence.")
                page.wait_for_timeout(5000)
                assert page.locator("#document-status").inner_text() == "SAVED", (page.locator("#document-status").inner_text(), page.locator("#document-message").inner_text(), errors)
                with app.app_context():
                    envelope = get_db().execute("SELECT envelope FROM office_documents").fetchone()["envelope"]
                    assert "Private content" not in envelope
                    assert "Browser test" not in envelope
                page.reload()
                page.locator("#document-passphrase").fill("document-passphrase-123")
                page.locator("#document-unlock-form button").click()
                page.locator("#document-workspace").wait_for(state="visible", timeout=30000)
                expect(page.locator("#document-title")).to_have_value("Browser test", timeout=30000)
                assert "Private content" in page.locator(".documents-paper").inner_text()
                station_background = page.locator(".documents-paper").evaluate("element => getComputedStyle(element).backgroundColor")
                page.locator("#document-theme").click()
                assert page.locator("#document-theme").get_attribute("aria-pressed") == "true"
                assert page.locator(".documents-paper").evaluate("element => getComputedStyle(element).backgroundColor") == "rgb(255, 255, 255)"
                page.locator("#document-theme").click()
                assert page.locator(".documents-paper").evaluate("element => getComputedStyle(element).backgroundColor") == station_background
                page.locator("#document-file").set_input_files({
                    "name": "malicious.html", "mimeType": "text/html",
                    "buffer": b'<p>Safe</p><script>alert(1)</script><img src="https://example.org/leak">',
                })
                page.wait_for_timeout(5000)
                assert page.locator("#document-status").inner_text() == "SAVED", (page.locator("#document-status").inner_text(), page.locator("#document-message").inner_text(), errors)
                assert page.locator(".documents-paper script").count() == 0
                assert page.locator(".documents-paper img").count() == 0
                page.locator("#document-export").click()
                with page.expect_download(timeout=30000) as info:
                    page.locator('#document-export-dialog button[value="docx"]').click()
                assert info.value.suggested_filename == "malicious.docx"
                page.locator("#document-file").set_input_files(info.value.path())
                page.wait_for_timeout(1000)
                assert "Safe" in page.locator(".documents-paper").inner_text()
                page.locator("#document-export").click()
                with page.expect_download(timeout=30000) as odt_download:
                    page.locator('#document-export-dialog button[value="odt"]').click()
                assert odt_download.value.suggested_filename == "malicious.odt"
                page.locator("#document-file").set_input_files(odt_download.value.path())
                page.wait_for_timeout(1000)
                assert "Safe" in page.locator(".documents-paper").inner_text()
                page.locator("#document-export").click()
                with page.expect_download(timeout=60000) as pdf_download:
                    page.locator('#document-export-dialog button[value="pdf"]').click()
                assert pdf_download.value.suggested_filename == "malicious.pdf"
                page.locator("#document-file").set_input_files({
                    "name": "simple.rtf", "mimeType": "application/rtf",
                    "buffer": b'{\\rtf1\\ansi This is \\b bold\\b0\\par Another line.}',
                })
                page.wait_for_timeout(1000)
                assert "Another line" in page.locator(".documents-paper").inner_text()
                page.locator("#document-file").set_input_files({
                    "name": "table.html", "mimeType": "text/html",
                    "buffer": b'<h2>Table</h2><table><tr><th>One</th><th>Two</th></tr><tr><td>A</td><td>B</td></tr></table>',
                })
                page.wait_for_timeout(1000)
                page.locator("#document-export").click()
                with page.expect_download(timeout=30000) as markdown_download:
                    page.locator('#document-export-dialog button[value="md"]').click()
                markdown = Path(markdown_download.value.path()).read_text(encoding="utf-8")
                assert "| One" in markdown, markdown
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.locator(".documents-toolbar").is_visible()
                assert page.locator(".documents-toolbar").evaluate("element => getComputedStyle(element).overflowX") == "auto"
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()


if __name__ == "__main__":
    main()
