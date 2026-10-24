"""Video module settings, permissions, browsing, and storage."""
import io
import shutil
import unittest
from pathlib import Path

from stationnet import create_app, db


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.data_dir = Path(__file__).parent / "videotestdir"
        self.data_dir.mkdir(exist_ok=True)
        (self.data_dir / "station.db").unlink(missing_ok=True)
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

    def setting(self, enabled=True, mode="community"):
        return self.client.post("/sysop/settings", data={"csrf_token": "test-token",
                                "settings_section": "videos", "videos_mode": mode,
                                "videos_enabled": "on" if enabled else ""})

    def upload(self, station=False, title="A clip"):
        return self.client.post("/videos/station-upload" if station else "/videos/upload",
                                data={"csrf_token": "test-token", "title": title,
                                      "description": "A description",
                                      "file": (io.BytesIO(b"\0\0\0\x18ftypisom" + b"x" * 100), "clip.mp4")},
                                content_type="multipart/form-data")

    def test_disabled_default_and_mode_permissions(self):
        self.assertEqual(self.client.get("/videos").status_code, 404)
        self.assertNotIn(b">VIDEOS</a>", self.client.get("/terminal").data)
        self.setting(mode="curated")
        self.login(1)
        self.assertEqual(self.client.get("/videos/upload").status_code, 404)
        self.assertEqual(self.upload().status_code, 404)
        self.assertEqual(self.upload(station=True).status_code, 403)
        self.login(2)
        self.assertEqual(self.upload(station=True).status_code, 302)
        page = self.client.get("/videos/1").data
        self.assertIn(b"A clip", page)
        self.assertNotIn(b"Add a comment", page)
        self.assertNotIn(b"sysop /", page)
        self.assertEqual(self.client.post("/videos/1/comments", data={"csrf_token": "test-token", "body": "hi"}).status_code, 404)
        self.assertTrue(list((self.data_dir / "videos" / "station").glob("*.mp4")))
        self.assertEqual(self.client.get("/videos/1/media", headers={"Range": "bytes=0-3"}).status_code, 206)
        self.setting(mode="community")
        self.login(1)
        self.assertEqual(self.upload(title="Member clip").status_code, 302)
        self.assertTrue(list((self.data_dir / "videos" / "users" / "1").glob("*.mp4")))
        self.assertEqual(self.client.post("/videos/2/comments", data={"csrf_token": "test-token", "body": "Nice"}).status_code, 302)
        self.assertIn(b"Nice", self.client.get("/videos/2").data)
        self.assertNotIn(b"Add a comment", self.client.get("/videos/1").data)
        self.login(2)
        self.setting(enabled=False)
        self.assertEqual(self.client.get("/videos/1/media").status_code, 404)

    def test_search_and_twenty_per_page(self):
        self.setting()
        with self.app.app_context():
            database = db.get_db()
            database.executemany(
                "INSERT INTO videos(uploader_id,title,stored_name,mime_type,size_bytes) VALUES(1,?,?,?,1)",
                [(f"Clip {i}", f"{i}.mp4", "video/mp4") for i in range(22)],
            )
            database.commit()
        first = self.client.get("/videos").data
        self.assertEqual(first.count(b'class="terminal-card video-card"'), 20)
        self.assertIn(b"PAGE 1 OF 2", first)
        self.assertEqual(self.client.get("/videos?page=2").data.count(b'class="terminal-card video-card"'), 2)
        self.assertIn(b"Clip 21", self.client.get("/videos?q=Clip%2021").data)
        self.assertNotIn(b"Clip 20", self.client.get("/videos?q=Clip%2021").data)

    def test_video_and_comment_moderation(self):
        self.setting()
        self.login(1)
        self.assertEqual(self.upload().status_code, 302)
        video_path = next((self.data_dir / "videos" / "users" / "1").glob("*.mp4"))
        self.assertEqual(self.client.post("/videos/1/comments", data={"csrf_token": "test-token", "body": "Remove me"}).status_code, 302)
        self.login(2)
        page = self.client.get("/videos/1").data
        self.assertIn(b'aria-label="Video actions"', page)
        self.assertIn(b'aria-label="Comment actions"', page)
        self.assertNotIn(b"ACTIONS", page)
        self.assertEqual(self.client.post("/videos/comments/1/delete", data={"csrf_token": "test-token", "reason": "Moderation"}).status_code, 302)
        with self.app.app_context():
            database = db.get_db()
            self.assertEqual(database.execute("SELECT COUNT(*) FROM video_comments").fetchone()[0], 0)
            self.assertEqual(database.execute("SELECT action FROM moderation_audit WHERE target_type='video_comment'").fetchone()[0], "delete_video_comment")
        self.assertEqual(self.client.post("/videos/1/delete", data={"csrf_token": "test-token", "reason": "Moderation"}).status_code, 302)
        self.assertFalse(video_path.exists())
        self.assertEqual(self.client.get("/videos/1").status_code, 404)
        with self.app.app_context():
            self.assertEqual(db.get_db().execute("SELECT action FROM moderation_audit WHERE target_type='video'").fetchone()[0], "delete_video")

    def test_member_cannot_moderate_another_video(self):
        self.setting()
        self.login(2)
        self.assertEqual(self.upload(station=True).status_code, 302)
        self.login(1)
        self.assertNotIn(b'aria-label="Video actions"', self.client.get("/videos/1").data)
        self.assertEqual(self.client.post("/videos/1/delete", data={"csrf_token": "test-token"}).status_code, 403)

    def test_member_quota_override_and_station_upload_exemption(self):
        self.setting()
        self.assertIn(b'Default per-user upload quota', self.client.get('/sysop/settings').data)
        with self.app.app_context():
            database = db.get_db()
            database.execute("UPDATE station SET videos_default_quota_mb=1 WHERE id=1")
            database.execute("INSERT INTO videos(uploader_id,title,stored_name,mime_type,size_bytes) "
                             "VALUES(1,'Existing','existing.mp4','video/mp4',1048500)")
            database.commit()
        self.login(1)
        self.assertEqual(self.upload().status_code, 200)
        with self.app.app_context():
            self.assertEqual(db.get_db().execute("SELECT COUNT(*) FROM videos").fetchone()[0], 1)
        self.login(2)
        self.client.post('/sysop/videos/users/1/quota', data={'csrf_token': 'test-token',
                                                               'videos_quota_mb': '2'})
        self.login(1)
        self.assertEqual(self.upload().status_code, 302)
        self.login(2)
        self.assertEqual(self.upload(station=True).status_code, 302)


if __name__ == "__main__":
    unittest.main()
