"""Terminal services stay in navigation order with consecutive numbers."""

import re
import shutil
import uuid
from pathlib import Path
from unittest import TestCase

from stationnet import create_app, db


class TerminalServicesTests(TestCase):
    def setUp(self):
        self.data_dir = Path.cwd() / ".test-data" / f"terminal-{uuid.uuid4().hex}"
        self.app = create_app({"TESTING": True, "DATA_DIR": str(self.data_dir)})
        coordinator = self.app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()
        with self.app.app_context():
            database = db.get_db()
            database.execute("INSERT INTO station(id,name,description,callsign) "
                             "VALUES(1,'Test','','TST-001')")
            database.execute("INSERT INTO users(id,username,callsign,password_hash,is_admin) "
                             "VALUES(1,'member','MEM-001','x',0)")
            database.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["kiwix"].stop()
        shutil.rmtree(self.data_dir)

    def services(self):
        page = self.client.get("/terminal").data.decode()
        directory = re.search(r'<div class="service-list">(.*?)</div>', page, re.S).group(1)
        entries = re.findall(
            r'<a href="([^"]+)">\s*<span class="service-number">\[([^]]+)\]</span>'
            r'\s*<span class="service-copy"><strong>([^<]+)</strong>', directory
        )
        self.assertEqual([number for _, number, _ in entries],
                         [f"{index:02d}" for index in range(1, len(entries) + 1)])
        return [(path, name) for path, _, name in entries]

    def test_optional_services_are_numbered_and_ordered_for_members(self):
        with self.client.session_transaction() as session:
            session["user_id"] = 1
        self.assertEqual([name for _, name in self.services()[:3]],
                         ["BULLETIN BOARD", "ANNOUNCEMENTS", "STATION CHAT"])
        with self.app.app_context():
            database = db.get_db()
            database.execute("UPDATE station SET imageboard_enabled=1,kiwix_enabled=1,"
                             "videos_enabled=1,mud_enabled=1 WHERE id=1")
            database.commit()
        services = self.services()
        self.assertEqual([name for _, name in services], [
            "BULLETIN BOARD", "IMAGEBOARD", "ANNOUNCEMENTS", "WIKI", "VIDEOS",
            "STATION CHAT", "PRIVATE MAIL", "RADIO", "FILE ARCHIVE",
            "USER DIRECTORY", "GAMES AND APPS", "STATION MUD",
        ])
        self.assertEqual(services[1][0], "/imageboard")
        self.assertEqual(services[3][0], "/wiki/")

    def test_guest_sees_only_public_services(self):
        with self.app.app_context():
            database = db.get_db()
            database.execute("UPDATE station SET imageboard_enabled=1,kiwix_enabled=1 "
                             "WHERE id=1")
            database.commit()
        self.assertEqual([name for _, name in self.services()],
                         ["BULLETIN BOARD", "ANNOUNCEMENTS", "WIKI"])
