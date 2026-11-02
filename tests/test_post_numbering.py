"""Public pages hide post IDs; moderators keep them and can follow reports to the content."""
import re

from stationnet.db import get_db
from test_self_delete import client_for, seed_bulletin, seed_imageboard, station  # noqa: F401

NUMBER_LABEL = re.compile(r"(?<![&\w])#\d+(?!\d*;)")


def visible_text(page):
    return re.sub(r"<(script|style)\b.*?</\1>|<[^>]+>", " ", page, flags=re.S)


def page_for(client, path):
    response = client.get(path)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def report(app, reporter, target_type, target_id):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO moderation_reports(reporter_id,target_type,target_id,reason,created_at) VALUES(?,?,?,?,?)",
            (reporter, target_type, target_id, "a sufficiently long reason", "2026-01-03T00:00:00+00:00"),
        )
        db.commit()


def test_bulletin_pages_hide_numbers_and_keep_anchors(station):
    app, _, author, other = station
    thread, reply = seed_bulletin(app, author, other)
    client = client_for(app, author)
    index = page_for(client, "/board")
    page = page_for(client, f"/board/{thread}")
    assert "REF / ORIGIN" not in index
    assert not NUMBER_LABEL.search(visible_text(index))
    assert not NUMBER_LABEL.search(visible_text(page))
    assert f'id="reply-{reply}"' in page
    assert f'<option value="{reply}">Reply by other · ' in page


def test_imageboard_pages_hide_numbers_and_keep_anchors(station):
    app, _, author, other = station
    thread, reply = seed_imageboard(app, author, other)
    client = client_for(app, author)
    catalog = page_for(client, "/imageboard")
    page = page_for(client, f"/imageboard/{thread}")
    assert not NUMBER_LABEL.search(visible_text(catalog))
    assert not NUMBER_LABEL.search(visible_text(page))
    assert f'id="reply-{reply}"' in page
    assert f'<option value="imageboard_thread:{thread}">Thread: thread</option>' in page
    assert f'<option value="imageboard_reply:{reply}">Reply by other · ' in page


def test_catalog_status_label_has_no_stray_separator(station):
    app, _, author, other = station
    thread, _ = seed_imageboard(app, author, other)
    client = client_for(app, author)
    assert 'class="eyebrow">PINNED' not in page_for(client, "/imageboard")
    with app.app_context():
        db = get_db()
        db.execute("UPDATE imageboard_threads SET pinned=1 WHERE id=?", (thread,))
        db.commit()
    assert '<p class="eyebrow">PINNED</p>' in page_for(client, "/imageboard")
    with app.app_context():
        db = get_db()
        db.execute("UPDATE imageboard_threads SET locked=1 WHERE id=?", (thread,))
        db.commit()
    assert '<p class="eyebrow">PINNED / LOCKED</p>' in page_for(client, "/imageboard")


def test_moderation_reports_link_to_content_and_keep_ids(station):
    app, admin, author, other = station
    bulletin, bulletin_reply = seed_bulletin(app, author, other)
    board_thread, board_reply = seed_imageboard(app, author, other)
    report(app, other, "bulletin", bulletin)
    report(app, other, "bulletin", bulletin_reply)
    report(app, other, "imageboard_thread", board_thread)
    report(app, other, "imageboard_reply", board_reply)
    page = page_for(client_for(app, admin), "/sysop/moderation")
    assert f'<a href="/board/{bulletin}">bulletin #{bulletin}</a>' in page
    assert f'<a href="/board/{bulletin}#reply-{bulletin_reply}">bulletin #{bulletin_reply}</a>' in page
    assert f'<a href="/imageboard/{board_thread}">imageboard_thread #{board_thread}</a>' in page
    assert f'<a href="/imageboard/{board_thread}#reply-{board_reply}">imageboard_reply #{board_reply}</a>' in page


def test_moderation_report_for_deleted_content_has_no_link(station):
    app, admin, author, other = station
    report(app, other, "bulletin", 9999)
    page = page_for(client_for(app, admin), "/sysop/moderation")
    assert "bulletin #9999" in page
    assert "bulletin #9999</a>" not in page
