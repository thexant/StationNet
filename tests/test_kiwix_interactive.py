from stationnet import create_app, kiwix
from stationnet.db import get_db


class Upstream:
    def __init__(self, status=200, headers=None, body=b"<html>books</html>"):
        self.status = status
        self.headers = headers or {"Content-Type": "text/html"}
        self.body = body

    def read(self, size=-1):
        body, self.body = self.body, b""
        return body

    def close(self):
        pass


def test_interactive_archive_navigation_keeps_frame_permissions(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path)})
    with app.app_context():
        database = get_db()
        database.execute(
            "INSERT INTO station(name, description, callsign, guest_access, kiwix_enabled) "
            "VALUES('Test Station', 'Test', 'TST-1', 1, 1)"
        )
        database.commit()
    server = app.extensions["kiwix"]
    monkeypatch.setattr(server, "ready", lambda: (True, None))
    server.port = 45937
    requested = []

    def upstream(request, timeout):
        requested.append(request.full_url)
        if request.full_url.endswith("/content/book"):
            return Upstream(302, {"Location": "/wiki/content/content/book/Home"})
        return Upstream()

    monkeypatch.setattr(kiwix._opener, "open", upstream)
    client = app.test_client()
    redirect = client.get("/wiki/interactive/content/book")
    assert redirect.status_code == 302
    assert redirect.headers["Location"] == "/wiki/interactive/content/book/Home"
    assert "frame-ancestors 'self'" in redirect.headers["Content-Security-Policy"]
    page = client.get(redirect.headers["Location"])
    assert page.status_code == 200
    assert page.data == b"<html>books</html>"
    assert page.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]
    assert requested == [
        "http://127.0.0.1:45937/wiki/content/content/book",
        "http://127.0.0.1:45937/wiki/content/content/book/Home",
    ]
