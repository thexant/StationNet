"""FIGlet rows stay aligned within the centered art block."""

import threading

import pytest
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.ansi import available_header_fonts, station_banner
from stationnet.db import get_db


def test_figlet_masthead_is_one_centered_block(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO station(name,description,callsign) VALUES(?,?,?)",
            ("StationNet", "Test", "TST-1"),
        )
        db.commit()

    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as driver:
            try:
                browser = driver.chromium.launch(headless=True)
            except Exception as error:
                pytest.skip(f"Chromium is unavailable: {error}")
            try:
                page = browser.new_page()
                for font, title, size, theme, width in (
                    ("small", "STATIONNET", 100, "cyan", 1280),
                    ("standard", "NORTH STAR", 150, "amber", 760),
                    ("slant", "LONG STATION TITLE", 200, "violet", 390),
                ):
                    if font not in available_header_fonts():
                        continue
                    with app.app_context():
                        db = get_db()
                        db.execute(
                            "UPDATE station SET header_font=?,header_text=?,header_font_size=?,masthead_theme=?",
                            (font, title, size, theme),
                        )
                        db.commit()
                    page.set_viewport_size({"width": width, "height": 800})
                    page.goto(f"http://127.0.0.1:{server.server_port}/")
                    logo = page.locator(".ansi-logo")
                    banner = station_banner(title, font, size)
                    assert logo.text_content() == banner
                    assert logo.evaluate("el => getComputedStyle(el).textAlign") == "center"
                    overflow = logo.evaluate("""el => {
                        const art = el.querySelector('.ansi-logo-art').getBoundingClientRect();
                        const box = el.getBoundingClientRect();
                        return {scrolls: el.scrollWidth > el.clientWidth,
                                artLeft: art.left, boxLeft: box.left};
                    }""")
                    if overflow["scrolls"]:
                        assert overflow["artLeft"] >= overflow["boxLeft"] - 1

                    # Different row lengths expose independent line centering.
                    # Every leading pipe must stay at the same horizontal position.
                    coordinates = logo.evaluate("""el => {
                        const art = el.querySelector('.ansi-logo-art');
                        art.textContent = '|\\n|========\\n|  ';
                        const text = art.firstChild;
                        const offsets = [0, 2, 12];
                        const xs = offsets.map(offset => {
                            const range = document.createRange();
                            range.setStart(text, offset);
                            range.setEnd(text, offset + 1);
                            return range.getBoundingClientRect().left;
                        });
                        const box = el.getBoundingClientRect();
                        const parent = el.parentElement.getBoundingClientRect();
                        const artBox = art.getBoundingClientRect();
                        return {xs, boxLeft: box.left, boxRight: box.right,
                                artLeft: artBox.left, artRight: artBox.right,
                                parentLeft: parent.left, parentRight: parent.right};
                    }""")
                    assert max(coordinates["xs"]) - min(coordinates["xs"]) < 1
                    assert abs(
                        (coordinates["artLeft"] + coordinates["artRight"])
                        - (coordinates["boxLeft"] + coordinates["boxRight"])
                    ) < 2
                    assert abs(
                        (coordinates["boxLeft"] + coordinates["boxRight"])
                        - (coordinates["parentLeft"] + coordinates["parentRight"])
                    ) < 2
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
