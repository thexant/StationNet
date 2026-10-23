"""FIGlet masthead discovery, previewing, and persisted customization."""
from html import unescape
import pytest

from stationnet import create_app
from stationnet.ansi import available_header_fonts, station_banner
from stationnet.db import get_db


@pytest.fixture()
def app(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(id,name,description,callsign) VALUES(1,'Test Node','','TST-001')"
        )
        database.execute(
            "INSERT INTO users(id,username,callsign,password_hash,is_admin) "
            "VALUES(1,'sysop','SYS-001','unused',1)"
        )
        database.commit()
    return app


def sysop(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1, csrf_token="banner-token")
    return client


def settings_form(**changes):
    values = {
        "csrf_token": "banner-token", "name": "Test Node", "description": "",
        "landing_message_control": "1", "brand_tagline": "Test tagline",
        "landing_heading": "", "landing_message": "", "header_font": "small",
        "header_font_size": "100", "header_text": "", "header_color": "",
        "upload_permission": "members", "file_storage_limit_mb": "100",
        "file_size_limit_mb": "10",
    }
    values.update(changes)
    return values


def test_font_inventory_is_complete_and_sorted():
    fonts = available_header_fonts()
    assert "small" in fonts
    assert fonts == tuple(sorted(set(fonts), key=str.casefold))
    # pyfiglet ships substantially more than the old curated selector.
    assert len(fonts) > 100


def test_preview_uses_production_renderer_and_requires_sysop(app):
    assert app.test_client().get("/sysop/banner-preview").status_code in {302, 403}
    response = sysop(app).get("/sysop/banner-preview", query_string={
        "text": "Node", "font": "small", "size": 100,
    })
    assert response.status_code == 200
    assert response.json["banner"] == station_banner("Node", "small", 100)
    assert sysop(app).get("/sysop/banner-preview", query_string={
        "font": "not-a-font", "size": 100,
    }).status_code == 400


def test_banner_settings_persist_and_apply_to_masthead(app):
    client = sysop(app)
    response = client.post("/sysop/settings", data=settings_form(
        header_font="standard", header_text="Radio", header_color="#e6a431",
    ))
    assert response.status_code == 302
    with app.app_context():
        station = get_db().execute(
            "SELECT header_font,header_text,header_color FROM station WHERE id=1"
        ).fetchone()
        assert tuple(station) == ("standard", "Radio", "#e6a431")
    masthead = client.get("/masthead.css")
    assert "color: #e6a431" in masthead.get_data(as_text=True)
    page = client.get("/")
    assert station_banner("Radio", "standard", 100) in unescape(page.get_data(as_text=True))


def test_invalid_color_is_rejected_without_overwriting_settings(app):
    response = sysop(app).post("/sysop/settings", data=settings_form(header_color="red"))
    assert response.status_code == 200
    assert b"six-digit hexadecimal" in response.data
    with app.app_context():
        assert get_db().execute("SELECT header_color FROM station WHERE id=1").fetchone()[0] == ""
