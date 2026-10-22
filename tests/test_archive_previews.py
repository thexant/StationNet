import io
from pathlib import Path

import pytest
from PIL import Image

from stationnet import create_app
from stationnet.db import get_db


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATA_DIR": str(tmp_path),
        "DATABASE": str(tmp_path / "station.db"),
        "SECRET_KEY": "test-secret",
    })
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(id,name,description,callsign) VALUES(1,?,?,?)",
            ("Test Station", "", "AAA-BBB"),
        )
        user_id = database.execute(
            "INSERT INTO users(username,callsign,password_hash) VALUES(?,?,?)",
            ("member", "CCC-DDD", "unused"),
        ).lastrowid
        database.commit()
        app.config["USER_ID"] = user_id
    return app


def login(client, user_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "csrf"


def add_archive_file(app, display_name, payload):
    with app.app_context():
        uploads = Path(app.config["DATA_DIR"]) / "uploads"
        uploads.mkdir()
        stored_name = f"stored-{display_name}"
        (uploads / stored_name).write_bytes(payload)
        file_id = get_db().execute(
            "INSERT INTO archive_files"
            "(uploader_id,display_name,stored_name,category,size_bytes) VALUES(?,?,?,?,?)",
            (app.config["USER_ID"], display_name, stored_name, "Images", len(payload)),
        ).lastrowid
        get_db().commit()
        return file_id


def test_archive_page_displays_image_preview(app):
    source = io.BytesIO()
    Image.new("RGB", (1200, 800), "red").save(source, "PNG")
    file_id = add_archive_file(app, "landscape.PNG", source.getvalue())
    client = app.test_client()
    login(client, app.config["USER_ID"])

    page = client.get("/files")
    assert page.status_code == 200
    assert f'/files/{file_id}/preview'.encode() in page.data

    response = client.get(f"/files/{file_id}/preview")
    assert response.status_code == 200
    assert response.content_type == "image/jpeg"
    with Image.open(io.BytesIO(response.data)) as preview:
        assert preview.size == (480, 320)


def test_preview_rejects_non_image_and_requires_login(app):
    file_id = add_archive_file(app, "not-really-an-image.png", b"plain text")
    client = app.test_client()

    response = client.get(f"/files/{file_id}/preview")
    assert response.status_code == 302

    login(client, app.config["USER_ID"])
    response = client.get(f"/files/{file_id}/preview")
    assert response.status_code == 404
