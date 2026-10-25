from io import BytesIO

from PIL import Image

from stationnet import create_app
from stationnet.db import get_db


def _png():
    output = BytesIO()
    Image.new("RGB", (24, 24), "cyan").save(output, "PNG")
    output.seek(0)
    return output


def test_station_icon_visibility_controls_both_masthead_icons(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name,description,callsign) VALUES(?,?,?)",
            ("Test Station", "Test description", "TST-1"),
        )
        cursor = database.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,1)",
            ("sysop", "SYS-1", "unused"),
        )
        user_id = cursor.lastrowid
        database.commit()
        assert database.execute(
            "SELECT display_station_icon FROM station WHERE id=1"
        ).fetchone()[0] == 0

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"

    response = client.post(
        "/sysop/settings/icon",
        data={
            "csrf_token": "test-token",
            "display_station_icon": "on",
            "station_icon": (_png(), "icon.png"),
        },
    )
    assert response.status_code == 302
    assert client.get("/").data.count(b'class="masthead-icon"') == 2

    response = client.post(
        "/sysop/settings/icon",
        data={"csrf_token": "test-token"},
    )
    assert response.status_code == 302
    assert client.get("/").data.count(b'class="masthead-icon"') == 0
    assert (tmp_path / "station_icon.png").is_file()

    with app.app_context():
        assert get_db().execute(
            "SELECT display_station_icon FROM station WHERE id=1"
        ).fetchone()[0] == 0
