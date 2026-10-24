"""User feed integration and station switch behavior."""
import unittest
import shutil
from pathlib import Path

from stationnet import create_app, db


class UserFeedTests(unittest.TestCase):
    def setUp(self):
        self.data_dir = Path(__file__).parent / "feedtestdir"
        self.data_dir.mkdir(exist_ok=True)
        (self.data_dir / "station.db").unlink(missing_ok=True)
        self.app = create_app({"TESTING": True, "DATA_DIR": str(self.data_dir)})
        coordinator = self.app.extensions["broadcast_coordinator"]
        coordinator._closed = True
        coordinator._timer.cancel()
        with self.app.app_context():
            database = db.get_db()
            database.execute(
                "INSERT INTO station(id,name,description,callsign) VALUES(1,'Test','', 'TST-001')"
            )
            database.execute(
                "INSERT INTO users(id,username,callsign,password_hash,is_admin) "
                "VALUES(1,'owner','OWN-001','x',0),(2,'visitor','VIS-002','x',0),"
                "(3,'sysop','SYS-003','x',1)"
            )
            database.commit()
        self.client = self.app.test_client()
        self.login(1)

    def tearDown(self):
        shutil.rmtree(self.data_dir)

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["csrf_token"] = "test-token"

    def post(self, path, **data):
        return self.client.post(path, data={"csrf_token": "test-token", **data})

    def test_ownership_comments_reactions_and_escaping(self):
        self.assertEqual(self.post("/feeds/users/2/posts", body="wrong feed").status_code, 403)
        self.assertEqual(self.post("/feeds/users/1/posts", body="x" * 201).status_code, 400)
        response = self.post("/feeds/users/1/posts", body="<script>alert(1)</script>")
        self.assertEqual(response.status_code, 302)
        page = self.client.get("/users/1").data.decode()
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.login(2)
        self.assertEqual(self.post("/feeds/posts/1/delete").status_code, 403)
        self.assertEqual(self.post("/feeds/posts/1/comments", body="Nice post").status_code, 302)
        self.assertEqual(self.post("/feeds/posts/1/react", kind="like").status_code, 302)
        self.assertIn("LIKE 1", self.client.get("/users/1").data.decode())
        self.assertEqual(self.post("/feeds/posts/1/react", kind="like").status_code, 302)
        self.assertIn("LIKE 0", self.client.get("/users/1").data.decode())
        self.login(1)
        self.assertEqual(self.post("/feeds/comments/1/delete").status_code, 403)
        self.login(2)
        self.assertEqual(self.post("/feeds/comments/1/delete").status_code, 302)
        self.login(1)
        self.assertEqual(self.post("/feeds/posts/1/delete").status_code, 302)
        self.assertNotIn("alert(1)", self.client.get("/users/1").data.decode())

    def test_disable_blocks_all_routes_and_preserves_content(self):
        self.post("/feeds/users/1/posts", body="Persisted thought")
        self.login(2)
        self.post("/feeds/posts/1/comments", body="Persisted comment")
        self.post("/feeds/posts/1/react", kind="heart")
        self.login(1)
        self.assertIn("FEED: New comment", self.client.get("/mail").data.decode())
        self.login(3)
        response = self.post("/sysop/settings", settings_section="user_feeds")
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("user-feed", self.client.get("/users/1").data.decode())
        self.login(1)
        self.assertNotIn("FEED: New comment", self.client.get("/mail").data.decode())
        self.assertEqual(self.client.get("/mail/1").status_code, 404)
        self.login(3)
        for path, data in [
            ("/feeds/users/1/posts", {"body": "blocked"}),
            ("/feeds/posts/1/comments", {"body": "blocked"}),
            ("/feeds/posts/1/react", {"kind": "like"}),
            ("/feeds/posts/1/delete", {}),
            ("/feeds/comments/1/delete", {}),
            ("/reports", {"target_type": "feed_post", "target_id": "1", "reason": "A valid reason"}),
        ]:
            self.assertEqual(self.post(path, **data).status_code, 404, path)
        with self.app.app_context():
            database = db.get_db()
            self.assertEqual(database.execute("SELECT COUNT(*) FROM user_feed_posts").fetchone()[0], 1)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM user_feed_comments").fetchone()[0], 1)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM user_feed_reactions").fetchone()[0], 1)
        self.post("/sysop/settings", settings_section="user_feeds", user_feeds_enabled="on")
        page = self.client.get("/users/1").data.decode()
        self.assertIn("Persisted thought", page)
        self.assertIn("Persisted comment", page)
        self.assertIn("HEART 1", page)
        self.login(1)
        self.assertIn("FEED: New comment", self.client.get("/mail").data.decode())

    def test_post_pagination_and_rate_limit(self):
        with self.app.app_context():
            database = db.get_db()
            database.executemany(
                "INSERT INTO user_feed_posts(owner_id,body) VALUES(1,?)",
                [(f"Entry {i}",) for i in range(25)],
            )
            database.commit()
        page = self.client.get("/users/1").data.decode()
        self.assertIn("Entry 24", page)
        self.assertNotIn("Entry 0</p>", page)
        self.assertIn("OLDER POSTS", page)
        older = self.client.get("/users/1?page=2").data.decode()
        self.assertIn("Entry 0", older)
        self.assertIn("NEWER POSTS", older)
        self.assertEqual(self.post("/feeds/users/1/posts", body="limited").status_code, 429)

    def test_existing_report_table_is_upgraded_without_losing_reports(self):
        with self.app.app_context():
            database = db.get_db()
            database.execute("ALTER TABLE station DROP COLUMN user_feeds_enabled")
            database.execute("DROP TABLE moderation_reports")
            database.execute("""CREATE TABLE moderation_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER NOT NULL REFERENCES users(id),
                target_type TEXT NOT NULL CHECK(target_type IN ('user','chat','bulletin','file')),
                target_id INTEGER NOT NULL, subject_user_id INTEGER REFERENCES users(id),
                reason TEXT NOT NULL, created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','dismissed')),
                resolver_id INTEGER REFERENCES users(id),
                resolution_note TEXT NOT NULL DEFAULT '', resolved_at TEXT)""")
            database.execute(
                "INSERT INTO moderation_reports(reporter_id,target_type,target_id,reason,created_at) "
                "VALUES(1,'user',2,'Existing report','2020-01-01')"
            )
            database.commit()
            db.init_db()
            self.assertEqual(database.execute("SELECT user_feeds_enabled FROM station").fetchone()[0], 1)
            self.assertEqual(database.execute("SELECT reason FROM moderation_reports").fetchone()[0],
                             "Existing report")
            database.execute(
                "INSERT INTO moderation_reports(reporter_id,target_type,target_id,reason,created_at) "
                "VALUES(1,'feed_post',1,'Feed report','2020-01-02')"
            )
            database.commit()

    def test_reports_and_sysop_removal(self):
        self.post("/feeds/users/1/posts", body="Needs moderation")
        self.login(2)
        self.post("/feeds/posts/1/comments", body="Needs comment moderation")
        self.assertEqual(self.post("/reports", target_type="feed_post", target_id="1",
                                   reason="A valid report reason").status_code, 302)
        self.login(3)
        self.assertIn("feed_post #1", self.client.get("/sysop/moderation").data.decode())
        page = self.client.get("/users/1").data.decode()
        self.assertIn('aria-label="Post actions"', page)
        self.assertIn('aria-label="Comment actions"', page)
        self.assertNotIn('<summary>ACTIONS</summary>', page)
        self.assertEqual(self.post("/feeds/comments/1/delete", reason="Spam comment").status_code, 302)
        self.assertEqual(self.post("/feeds/posts/1/delete").status_code, 302)
        with self.app.app_context():
            database = db.get_db()
            self.assertEqual(database.execute(
                "SELECT action FROM moderation_audit WHERE target_type='feed_post'"
            ).fetchone()[0], "delete_feed_post")
            self.assertEqual(tuple(database.execute(
                "SELECT action,reason FROM moderation_audit WHERE target_type='feed_comment'"
            ).fetchone()), ("delete_feed_comment", "Spam comment"))


if __name__ == "__main__":
    unittest.main()
