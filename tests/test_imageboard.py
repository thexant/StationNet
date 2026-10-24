"""Imageboard accounts, uploads, catalog, moderation, and feature switch."""
import io
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path

from PIL import Image

from stationnet import create_app, db


class ImageboardTests(unittest.TestCase):
    def setUp(self):
        self.data_dir = Path(__file__).resolve().parents[1] / ".test-data" / f"imageboard-{uuid.uuid4().hex}"
        self.app = create_app({"TESTING": True, "DATA_DIR": str(self.data_dir)})
        coordinator = self.app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()
        with self.app.app_context():
            database = db.get_db()
            database.execute("INSERT INTO station(id,name,description,callsign) VALUES(1,'Test Station','','TST-001')")
            database.execute("INSERT INTO users(id,username,callsign,password_hash,is_admin) VALUES"
                             "(1,'member','MEM-001','x',0),(2,'sysop','SYS-002','x',1)")
            database.commit()
        self.client = self.app.test_client()
        self.login(2)

    def tearDown(self):
        shutil.rmtree(self.data_dir)

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "test-token"

    def enable(self, enabled=True):
        return self.client.post("/sysop/settings", data={"csrf_token": "test-token",
                                "settings_section": "imageboard",
                                "imageboard_enabled": "on" if enabled else ""})

    @staticmethod
    def image():
        payload = io.BytesIO()
        Image.new("RGB", (30, 20), "red").save(payload, "PNG")
        payload.seek(0)
        return payload, "picture.png"

    def create_thread(self, subject="First image"):
        return self.client.post("/imageboard/new", data={"csrf_token": "test-token",
                                "subject": subject, "body": "A station picture",
                                "image": self.image()}, content_type="multipart/form-data")

    def test_accounts_uploads_bumping_and_moderation(self):
        self.assertEqual(self.client.get("/imageboard").status_code, 404)
        self.assertNotIn(b">IMAGEBOARD</a>", self.client.get("/terminal").data)
        self.enable()
        self.login(1)
        self.assertEqual(self.create_thread().status_code, 302)
        page = self.client.get("/imageboard/1").data
        self.assertIn(b"member - [MEM-001]", page)
        self.assertIn(b"First image", self.client.get("/imageboard").data)
        with self.app.app_context():
            database = db.get_db()
            row = database.execute("SELECT * FROM imageboard_threads WHERE id=1").fetchone()
            self.assertTrue((self.data_dir / "imageboard" / row["image_name"]).exists())
            self.assertEqual(self.client.get(f"/imageboard/media/{row['thumb_name']}").status_code, 200)
        self.assertEqual(self.client.post("/imageboard/1/reply", data={"csrf_token": "test-token", "body": "Nice shot"}).status_code, 302)
        self.assertEqual(self.client.post("/imageboard/1/reply", data={"csrf_token": "test-token", "image": self.image()}, content_type="multipart/form-data").status_code, 302)
        self.assertIn(b"Nice shot", self.client.get("/imageboard/1").data)
        self.assertEqual(self.client.post("/imageboard/1/moderate", data={"csrf_token": "test-token", "action": "lock"}).status_code, 403)
        self.login(2)
        self.assertEqual(self.client.post("/reports", data={"csrf_token": "test-token", "target": "imageboard_reply:1", "reason": "This reply needs review"}).status_code, 302)
        self.assertEqual(self.client.post("/imageboard/replies/2/delete", data={"csrf_token": "test-token"}).status_code, 302)
        self.assertEqual(self.client.post("/imageboard/1/moderate", data={"csrf_token": "test-token", "action": "pin"}).status_code, 302)
        self.assertEqual(self.client.post("/imageboard/1/moderate", data={"csrf_token": "test-token", "action": "lock"}).status_code, 302)
        self.assertEqual(self.client.post("/imageboard/1/reply", data={"csrf_token": "test-token", "body": "blocked"}).status_code, 403)
        self.assertEqual(self.client.post("/imageboard/1/moderate", data={"csrf_token": "test-token", "action": "delete"}).status_code, 302)
        self.assertEqual(self.client.get("/imageboard/1").status_code, 404)
        self.assertFalse(list((self.data_dir / "imageboard").iterdir()))
        self.enable(False)
        self.assertEqual(self.client.get("/imageboard").status_code, 404)

    def test_rejects_invalid_image_and_group_moderator(self):
        self.enable()
        self.login(1)
        response = self.client.post("/imageboard/new", data={"csrf_token": "test-token",
                                    "subject": "Unsafe", "image": (io.BytesIO(b"not an image"), "fake.png")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertFalse((self.data_dir / "imageboard").exists())
        self.assertEqual(self.create_thread().status_code, 302)
        with self.app.app_context():
            database = db.get_db()
            database.execute("INSERT INTO radio_permission_groups(name,moderate_imageboard) VALUES('BOARD MODS',1)")
            group_id = database.execute("SELECT id FROM radio_permission_groups WHERE name='BOARD MODS'").fetchone()[0]
            database.execute("INSERT INTO radio_group_memberships(user_id,group_id) VALUES(1,?)", (group_id,))
            database.commit()
        self.assertEqual(self.client.post("/imageboard/1/moderate", data={"csrf_token": "test-token", "action": "lock"}).status_code, 302)

    def test_existing_report_queue_migrates_without_losing_reports(self):
        with sqlite3.connect(":memory:") as database:
            database.execute("CREATE TABLE station(id INTEGER PRIMARY KEY)")
            database.execute("CREATE TABLE radio_permission_groups(id INTEGER PRIMARY KEY)")
            database.execute("""CREATE TABLE moderation_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER NOT NULL REFERENCES users(id),
                target_type TEXT NOT NULL CHECK(target_type IN
                    ('user','chat','bulletin','file','feed_post','feed_comment')),
                target_id INTEGER NOT NULL, subject_user_id INTEGER REFERENCES users(id),
                reason TEXT NOT NULL, created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','dismissed')),
                resolver_id INTEGER REFERENCES users(id), resolution_note TEXT NOT NULL DEFAULT '',
                resolved_at TEXT)""")
            database.execute("INSERT INTO moderation_reports(reporter_id,target_type,target_id,subject_user_id,reason,created_at) VALUES(1,'user',2,2,'Old report','2026-01-01')")
            database.row_factory = sqlite3.Row
            db._migrate_imageboard(database)
            self.assertEqual(database.execute("SELECT reason FROM moderation_reports WHERE id=1").fetchone()[0], "Old report")
            database.execute("INSERT INTO moderation_reports(reporter_id,target_type,target_id,subject_user_id,reason,created_at) VALUES(2,'imageboard_thread',1,1,'New report','2026-01-02')")


if __name__ == "__main__":
    unittest.main()
