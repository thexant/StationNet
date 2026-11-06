import re

from stationnet import create_app
from stationnet.db import get_db


def _brand_href(html):
    match = re.search(rb'<a class="station-brand" href="([^"]*)"', html)
    assert match, "masthead link not found"
    return match.group(1).decode()


def test_masthead_links_to_service_directory_only_when_logged_in(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name,description,callsign) VALUES(?,?,?)",
            ("Test Station", "Test description", "TST-1"),
        )
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,0)",
            ("operator", "OPR-1", "unused"),
        ).lastrowid
        database.commit()

    client = app.test_client()
    anonymous = client.get("/")
    assert anonymous.status_code == 200
    assert _brand_href(anonymous.data) == "/"

    with client.session_transaction() as session:
        session["user_id"] = user_id

    welcome = client.get("/")
    assert welcome.status_code == 200
    assert b"ENTER THE STATION" in welcome.data
    assert _brand_href(welcome.data) == "/terminal"

    terminal = client.get("/terminal")
    assert terminal.status_code == 200
    assert _brand_href(terminal.data) == "/terminal"
