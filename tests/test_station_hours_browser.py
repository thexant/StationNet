"""The early-open header changes on a page that remains open at opening."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def test_early_open_header_switches_without_reload():
    playwright = pytest.importorskip("playwright.sync_api")
    script = Path(__file__).resolve().parents[1] / "stationnet" / "static" / "station.js"
    with playwright.sync_playwright() as driver:
        try:
            browser = driver.chromium.launch(headless=True)
        except Exception as error:
            pytest.skip(f"Chromium is unavailable: {error}")
        try:
            page = browser.new_page()
            opening = datetime.now(timezone.utc) + timedelta(seconds=4)
            closing = opening + timedelta(hours=14)
            page.set_content(
                '<b data-station-hours data-clock-format="12h" data-early-open '
                'data-regular-hours="6:00AM - 8:00PM" '
                f'data-opens="{opening.isoformat()}" data-closes="{closing.isoformat()}">'
                'OPEN EARLY</b>'
            )
            page.add_script_tag(path=str(script))
            value = page.locator("[data-station-hours]")
            assert value.text_content() == "OPEN EARLY"
            page.wait_for_function(
                "document.querySelector('[data-station-hours]').textContent !== 'OPEN EARLY'",
                timeout=7000,
            )
            assert re.fullmatch(r"\d{1,2}:\d{2}(?:AM|PM) - \d{1,2}:\d{2}(?:AM|PM)",
                                value.text_content())
        finally:
            browser.close()
