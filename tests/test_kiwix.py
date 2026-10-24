"""Wiki switch, storage, and reader access."""
from pathlib import Path
from io import BytesIO
from email.message import Message
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import subprocess
import sys

from stationnet import create_app, db


class KiwixTests(TestCase):
    def setUp(self):
        test_root = Path.cwd() / ".test-data"
        test_root.mkdir(exist_ok=True)
        self.temp = TemporaryDirectory(dir=test_root)
        root = Path(self.temp.name)
        self.data_dir = root / "data"
        self.kiwix_dir = root / "kiwix_data"
        self.app = create_app({"TESTING": True, "DATA_DIR": str(self.data_dir),
                               "KIWIX_DATA_DIR": str(self.kiwix_dir)})
        coordinator = self.app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()
        with self.app.app_context():
            database = db.get_db()
            database.execute("INSERT INTO station(id,name,description,callsign) "
                             "VALUES(1,'Test','','TST-001')")
            database.execute("INSERT INTO users(id,username,callsign,password_hash,is_admin) "
                             "VALUES(1,'sysop','SYS-001','x',1)")
            database.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["kiwix"].stop()
        self.temp.cleanup()

    def test_disabled_by_default_and_sysop_switch(self):
        self.assertTrue(self.kiwix_dir.is_dir())
        self.assertFalse(self.kiwix_dir.is_relative_to(self.data_dir))
        self.assertNotIn(b">WIKI</a>", self.client.get("/terminal").data)
        self.assertEqual(self.client.get("/wiki/").status_code, 404)
        self.assertEqual(self.client.get("/wiki/content/").status_code, 404)
        with self.client.session_transaction() as session:
            session["user_id"] = 1
            session["csrf_token"] = "token"
        self.assertEqual(self.client.post("/sysop/settings", data={
            "settings_section": "kiwix", "kiwix_enabled": "on", "csrf_token": "token"
        }).status_code, 302)
        self.assertIn(b">WIKI</a>", self.client.get("/terminal").data)
        self.assertIn(b"LIBRARY UNAVAILABLE", self.client.get("/wiki/").data)
        self.assertEqual(self.client.post("/sysop/settings", data={
            "settings_section": "kiwix", "csrf_token": "token"
        }).status_code, 302)
        self.assertEqual(self.client.get("/wiki/").status_code, 404)

    def test_enabled_reader_is_framed_in_station_page(self):
        with self.app.app_context():
            db.get_db().execute("UPDATE station SET kiwix_enabled=1 WHERE id=1")
            db.get_db().commit()
        with patch.object(self.app.extensions["kiwix"], "ready", return_value=(True, None)):
            response = self.client.get("/wiki/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"class=\"wiki-reader\"", response.data)
        self.assertIn(b"/wiki/content/", response.data)

    def test_startup_failure_records_reader_output_and_skips_invalid_archives(self):
        (self.kiwix_dir / "sample.zim").write_bytes(b"not a ZIM")
        server = self.app.extensions["kiwix"]
        real_popen = subprocess.Popen
        commands = []

        def start(command, **kwargs):
            commands.append(command)
            return real_popen(command, **kwargs)

        with patch("stationnet.kiwix.find_executable", return_value=sys.executable), \
             patch("stationnet.kiwix.subprocess.Popen", side_effect=start):
            ready, error = server.ready()

        self.assertFalse(ready)
        self.assertEqual(error, "kiwix-serve stopped while opening the ZIM library.")
        self.assertIn("--skipInvalid", commands[0])
        self.assertIn("--address=127.0.0.1", commands[0])
        self.assertTrue(server.diagnostic)

    def test_running_process_is_not_ready_until_port_opens(self):
        archive = self.kiwix_dir / "sample.zim"
        archive.write_bytes(b"sample")
        server = self.app.extensions["kiwix"]
        server.process = Mock()
        server.process.poll.return_value = None
        server.port = 12345
        server.signature = ((archive.name, archive.stat().st_size, archive.stat().st_mtime_ns),)
        with patch("stationnet.kiwix.socket.create_connection", side_effect=OSError):
            ready, error = server.ready()
        self.assertFalse(ready)
        self.assertIn("starting", error)

    def test_startup_detail_is_visible_only_to_sysop(self):
        with self.app.app_context():
            db.get_db().execute("UPDATE station SET kiwix_enabled=1 WHERE id=1")
            db.get_db().commit()
        server = self.app.extensions["kiwix"]
        server.diagnostic = "Unable to read example.zim"
        with patch.object(server, "ready", return_value=(False, "Reader failed.")):
            self.assertNotIn(b"Unable to read", self.client.get("/wiki/").data)
            with self.client.session_transaction() as session:
                session["user_id"] = 1
            self.assertIn(b"Unable to read", self.client.get("/wiki/").data)

    def test_content_is_proxied_and_kept_inside_station(self):
        with self.app.app_context():
            db.get_db().execute("UPDATE station SET kiwix_enabled=1 WHERE id=1")
            db.get_db().commit()
        server = self.app.extensions["kiwix"]
        server.port = 12345
        payload = BytesIO(b"<h1>Offline article</h1>")
        payload.status = 200
        payload.headers = Message()
        payload.headers["Content-Type"] = "text/html; charset=utf-8"
        with patch.object(server, "ready", return_value=(True, None)), \
             patch("stationnet.kiwix._opener.open", return_value=payload) as opened:
            response = self.client.get("/wiki/content/book/article?x=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Offline article", response.data)
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(opened.call_args.args[0].full_url,
                         "http://127.0.0.1:12345/wiki/content/book/article?x=1")
