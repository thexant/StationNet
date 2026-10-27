"""Browser smoke test for Agenda and the shared OFFICE unlock."""
import threading

import pytest
from flask import session
from werkzeug.serving import make_server

from stationnet import create_app
from stationnet.db import get_db


def test_agenda_calendar_and_shared_unlock(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name,description,callsign,office_enabled) VALUES(?,?,?,1)",
            ("Test Station", "Test", "TST-1"),
        )
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("planner", "PLN-1", "unused"),
        ).lastrowid
        database.commit()

    @app.get("/test-agenda-login")
    def test_login():
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
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                base = f"http://127.0.0.1:{server.server_port}"
                page.goto(base + "/test-agenda-login")
                page.goto(base + "/office/agenda")
                page.locator("#agenda-password").fill("correct horse battery staple")
                page.locator("#agenda-unlock-form button").click()
                page.locator("#agenda-confirm").fill("correct horse battery staple")
                page.locator("#agenda-unlock-form button").click()
                page.locator("#agenda-workspace").wait_for(state="visible", timeout=15000)
                date = page.evaluate("""() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; }""")
                birthday = "2000" + date[4:]

                page.locator("#agenda-add-contact").click()
                page.locator('#agenda-contact-form [name="name"]').fill("Ada Lovelace")
                page.locator('#agenda-contact-form [name="birthday"]').fill(birthday)
                page.locator('#agenda-contact-form button[type="submit"]').click()

                page.locator("#agenda-add-event").click()
                page.locator('#agenda-event-form [name="title"]').fill("Planning meeting")
                page.locator('#agenda-event-form [name="startDate"]').fill(date)
                page.locator('#agenda-event-form [name="endDate"]').fill(date)
                page.locator('#agenda-event-form [name="contactId"]').select_option(label="Ada Lovelace")
                page.locator('#agenda-event-form button[type="submit"]').click()

                page.locator("#agenda-add-task").click()
                page.locator('#agenda-task-form [name="title"]').fill("Prepare brief")
                page.locator('#agenda-task-form [name="dueDate"]').fill(date)
                page.locator('#agenda-task-form [name="eventId"]').select_option(label="Planning meeting")
                page.locator("#agenda-add-subtask").click()
                page.locator("#agenda-subtask-rows input[type=text]").fill("Outline")
                page.locator('#agenda-task-form button[type="submit"]').click()
                page.locator("#agenda-status").get_by_text("SAVED", exact=True).wait_for(timeout=15000)

                page.locator("#agenda-add-task").click()
                assert page.locator('#agenda-task-form [name="showOnCalendar"]').is_checked()
                page.locator('#agenda-task-form [name="title"]').fill("Internal checklist")
                page.locator('#agenda-task-form [name="dueDate"]').fill(date)
                page.locator('#agenda-task-form [name="showOnCalendar"]').uncheck()
                page.locator('#agenda-task-form button[type="submit"]').click()
                page.locator("#agenda-status").get_by_text("SAVED", exact=True).wait_for(timeout=15000)
                assert page.locator("#agenda-content").get_by_text("Internal checklist").count() == 0
                page.locator("#agenda-search").fill("Internal checklist")
                assert page.locator("#agenda-results-list").get_by_text("Internal checklist").count() == 0
                for calendar_view in ("month", "week", "day"):
                    page.locator(f'[data-view="{calendar_view}"]').click()
                    page.locator("#agenda-current").click()
                    assert page.locator("#agenda-content").get_by_text("Internal checklist").count() == 0
                page.locator('[data-view="tasks"]').click()
                assert page.locator("#agenda-results-list").get_by_text("Internal checklist").count() == 1
                page.locator("#agenda-search").fill("")
                page.locator("#agenda-content").get_by_text("Internal checklist").click()
                assert not page.locator('#agenda-task-form [name="showOnCalendar"]').is_checked()
                page.locator('#agenda-task-form [name="showOnCalendar"]').check()
                page.locator('#agenda-task-form button[type="submit"]').click()
                page.locator('[data-view="today"]').click()
                assert page.locator("#agenda-content").get_by_text("Internal checklist").count() >= 1
                page.locator('#agenda-content [data-kind="task"]').filter(has_text="Internal checklist").first.click()
                page.locator('#agenda-task-form [name="showOnCalendar"]').uncheck()
                page.locator('#agenda-task-form button[type="submit"]').click()
                assert page.locator("#agenda-content").get_by_text("Internal checklist").count() == 0

                page.locator('[data-view="month"]').click()
                page.locator("#agenda-current").click()
                assert page.locator("#agenda-content .agenda-month-cell").filter(has_text="Planning meeting").count() == 1
                assert page.locator("#agenda-content").get_by_text("Ada Lovelace · Birthday").count() == 1
                page.locator(f'#agenda-content [data-date="{date}"]').first.click()
                assert page.locator('[data-view="day"]').get_attribute("aria-current") == "page"
                assert page.locator("#agenda-content").get_by_text("Planning meeting").count() == 1
                page.locator('[data-view="tasks"]').click()
                page.locator('#agenda-content [data-subtask-id]').check()
                page.locator("#agenda-status").get_by_text("UNSAVED", exact=True).wait_for(timeout=5000)
                page.locator("#agenda-status").get_by_text("SAVED", exact=True).wait_for(timeout=15000)
                assert page.locator('#agenda-content [data-subtask-id]').is_checked()
                saved = page.evaluate("""async () => {
                  const {agenda} = await (await fetch('/office/agenda/api')).json();
                  const {key} = JSON.parse(sessionStorage.getItem('stationnet.office.unlock.v1'));
                  const bytes = Uint8Array.from(atob(key), c => c.charCodeAt(0));
                  const iv = Uint8Array.from(atob(agenda.envelope.iv), c => c.charCodeAt(0));
                  const ciphertext = Uint8Array.from(atob(agenda.envelope.data), c => c.charCodeAt(0));
                  const cryptoKey = await crypto.subtle.importKey('raw', bytes, 'AES-GCM', false, ['decrypt']);
                  return JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM', iv}, cryptoKey, ciphertext)));
                }""")
                assert saved["tasks"][0]["subtasks"][0]["done"] is True, saved

                page.goto(base + "/office/documents")
                page.locator("#document-workspace").wait_for(state="visible", timeout=15000)
                page.goto(base + "/office/agenda")
                page.locator("#agenda-workspace").wait_for(state="visible", timeout=15000)
                page.locator('[data-view="tasks"]').click()
                assert page.locator('#agenda-content [data-subtask-id]').count() == 1, (page.locator('#agenda-content').inner_text(), errors)
                assert page.locator('#agenda-content [data-subtask-id]').is_checked()
                page.locator("#agenda-search").fill("Outline")
                assert page.locator("#agenda-results-list").get_by_text("Prepare brief").count() == 1
                page.locator("#agenda-search").fill("")
                page.locator('[data-view="day"]').click()
                page.locator("#agenda-current").click()
                page.locator('#agenda-content [data-kind="event"]').click()
                page.locator('#agenda-event-form [name="showOnMonth"]').uncheck()
                page.locator('#agenda-event-form button[type="submit"]').click()
                page.locator("#agenda-status").get_by_text("SAVED", exact=True).wait_for(timeout=15000)
                page.locator('[data-view="month"]').click()
                assert page.locator("#agenda-content").get_by_text("Planning meeting").count() == 0
                page.locator('[data-view="week"]').click()
                assert page.locator("#agenda-content").get_by_text("Planning meeting").count() == 1
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.locator("#agenda-content").evaluate("element => element.scrollWidth <= element.clientWidth + 2")
                page.locator('[data-view="tasks"]').click()
                page.locator('#agenda-content [data-kind="task"]').filter(has_text="Prepare brief").click()
                for index in range(18):
                    page.locator("#agenda-add-subtask").click()
                    page.locator("#agenda-subtask-rows input[type=text]").last.fill(f"Step {index + 1}")
                page.locator('#agenda-task-form button[type="submit"]').click()
                page.locator("#agenda-content").get_by_text("Prepare brief").click()
                assert page.locator("#agenda-subtask-rows input[type=text]").count() == 19
                geometry = page.locator("#agenda-task-dialog").evaluate("""dialog => {
                    const scroll = dialog.querySelector('.agenda-task-scroll');
                    const save = dialog.querySelector('button[type=submit]').getBoundingClientRect();
                    const bounds = dialog.getBoundingClientRect();
                    return { top: bounds.top, bottom: bounds.bottom, scrollable: scroll.scrollHeight > scroll.clientHeight,
                        saveTop: save.top, saveBottom: save.bottom, scrollTop: scroll.scrollTop, viewport: innerHeight };
                }""")
                assert geometry["top"] >= 0 and geometry["bottom"] <= geometry["viewport"] + 1, geometry
                assert geometry["scrollable"] and geometry["scrollTop"] == 0, geometry
                assert geometry["saveTop"] >= 0 and geometry["saveBottom"] <= geometry["viewport"], geometry
                page.locator(".agenda-task-scroll").evaluate("element => element.scrollTop = element.scrollHeight")
                assert page.locator(".agenda-task-scroll").evaluate("element => element.scrollTop > 0")
                page.locator('#agenda-task-dialog [data-close]').click()
                page.goto(base + "/office/documents")
                page.locator("#document-workspace").wait_for(state="visible", timeout=15000)
                page.locator("#document-lock").click()
                page.locator("#document-unlock").wait_for(state="visible", timeout=15000)
                page.locator("#document-passphrase").fill("correct horse battery staple")
                page.locator("#document-unlock-form button[type=submit]").click()
                page.locator("#document-workspace").wait_for(state="visible", timeout=15000)
                assert page.locator("#document-confirm-row").is_hidden()
                page.goto(base + "/office/agenda")
                page.locator("#agenda-workspace").wait_for(state="visible", timeout=15000)
                assert not errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)
