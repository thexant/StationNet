"""Program directory account preferences and launch access."""
from stationnet import create_app
from stationnet.applications import catalog
from stationnet.db import get_db


def test_program_manager_preferences_are_per_account(tmp_path):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path})
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(id,name,description,callsign) VALUES(1,'Test','','TST-001')")
        for user_id in (1, 2):
            db.execute(
                "INSERT INTO users(id,username,callsign,password_hash) VALUES(?,?,?,'test')",
                (user_id, f"user{user_id}", f"USR-00{user_id}"),
            )
        db.commit()
        application = next(item for item in catalog() if item.enabled and item.runnable)

    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1, csrf_token="token")
    directory = client.get("/games-apps")
    assert directory.status_code == 200
    assert b"Program Manager" in directory.data
    assert application.name.encode() in directory.data
    assert client.post(f"/games-apps/{application.slug}/pin", data={"csrf_token": "bad"}).status_code == 400
    assert client.post(f"/games-apps/{application.slug}/pin", data={"csrf_token": "token"}).status_code == 302
    assert client.get(f"/games-apps/{application.slug}").status_code == 200
    with app.app_context():
        row = get_db().execute(
            "SELECT pinned_at,last_used_at FROM user_application_preferences WHERE user_id=1 AND application_slug=?",
            (application.slug,),
        ).fetchone()
        assert row["pinned_at"] and row["last_used_at"]

    with client.session_transaction() as session:
        session.update(user_id=2, csrf_token="token")
    other_directory = client.get("/games-apps")
    assert b"Pin programs from their details" in other_directory.data
    assert b"Launched programs will appear here" in other_directory.data

    with client.session_transaction() as session:
        session.update(user_id=1, csrf_token="token")
    assert client.post(f"/games-apps/{application.slug}/pin", data={"csrf_token": "token"}).status_code == 302
    with app.app_context():
        row = get_db().execute(
            "SELECT pinned_at,last_used_at FROM user_application_preferences WHERE user_id=1 AND application_slug=?",
            (application.slug,),
        ).fetchone()
        assert row["pinned_at"] is None and row["last_used_at"]
