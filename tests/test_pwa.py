from io import BytesIO

from PIL import Image

from stationnet import create_app
from stationnet.db import get_db


def _png(color):
    output = BytesIO()
    Image.new("RGB", (24, 24), color).save(output, "PNG")
    output.seek(0)
    return output


def _pixel(response):
    with Image.open(BytesIO(response.data)) as image:
        return image.size, image.getpixel((0, 0))[:3]


def test_pwa_uses_station_identity_and_icon_setting(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign) VALUES(?,?,?)",
                   ("Test Station", "Test description", "TST-1"))
        user_id = db.execute(
            "INSERT INTO users(username,callsign,password_hash,is_admin) VALUES(?,?,?,1)",
            ("sysop", "SYS-1", "unused"),
        ).lastrowid
        db.commit()

    client = app.test_client()
    manifest = client.get("/manifest.webmanifest")
    assert manifest.mimetype == "application/manifest+json"
    assert manifest.json["name"] == "Test Station"
    assert manifest.json["start_url"] == "/"
    assert manifest.json["scope"] == "/"
    assert {item["sizes"] for item in manifest.json["icons"]} == {"192x192", "512x512"}
    assert b'/manifest.webmanifest' in client.get("/").data
    assert b'/manifest.webmanifest' in client.get("/login").data
    assert client.get("/service-worker.js").status_code == 200
    assert client.get("/service-worker.js").headers["Service-Worker-Allowed"] == "/"
    assert _pixel(client.get("/pwa-icon-192.png"))[0] == (192, 192)

    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["csrf_token"] = "test-token"
    response = client.post("/sysop/settings/icon", data={
        "csrf_token": "test-token",
        "use_station_icon_for_web_app": "on",
        "station_icon": (_png("cyan"), "icon.png"),
    })
    assert response.status_code == 302
    assert _pixel(client.get("/pwa-icon-192.png")) == ((192, 192), (0, 255, 255))
    assert _pixel(client.get("/pwa-icon-512.png")) == ((512, 512), (0, 255, 255))
    assert b'class="masthead-icon"' not in client.get("/").data

    client.post("/sysop/settings/icon", data={"csrf_token": "test-token"})
    assert _pixel(client.get("/pwa-icon-192.png"))[1] != (0, 255, 255)
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT use_station_icon_for_web_app FROM station WHERE id=1").fetchone()[0] == 0
        db.execute("UPDATE station SET name='Renamed Station' WHERE id=1")
        db.commit()
    assert client.get("/manifest.webmanifest").json["name"] == "Renamed Station"


def test_pwa_icon_falls_back_when_station_icon_missing(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(name,description,callsign,use_station_icon_for_web_app) VALUES(?,?,?,1)",
                   ("Test Station", "Test description", "TST-1"))
        db.commit()
    assert app.test_client().get("/pwa-icon-512.png").status_code == 200
