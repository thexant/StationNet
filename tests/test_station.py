from stationnet import create_app
from stationnet.db import get_db
from .conftest import csrf

def test_uninitialized_redirects(client):
    response=client.get("/"); assert response.status_code==302; assert response.headers["Location"].endswith("/setup")

def test_setup_persists_and_hashes(app, initialized):
    with app.app_context():
        station=get_db().execute("SELECT * FROM station").fetchone(); admin=get_db().execute("SELECT * FROM users").fetchone()
        assert station["callsign"]=="TST-01"; assert admin["is_admin"]==1; assert admin["password_hash"].startswith("scrypt:"); assert "correct horse" not in admin["password_hash"]

def test_register_login_and_unique_callsign(app, initialized):
    response=initialized.post("/register",data={"csrf_token":csrf(initialized,"/register"),"username":"visitor","password":"a secure visitor pass","confirm_password":"a secure visitor pass"},follow_redirects=True)
    assert b"Account linked" in response.data
    with app.app_context():
        values=[r["callsign"] for r in get_db().execute("SELECT callsign FROM users")]; assert len(values)==len(set(values))==2
    initialized.post("/logout",data={"csrf_token":csrf(initialized,"/terminal")})
    response=initialized.post("/login",data={"csrf_token":csrf(initialized,"/login"),"username":"visitor","password":"a secure visitor pass"},follow_redirects=True)
    assert b"Link established" in response.data

def test_board_reply_guest_read_and_restart(app, initialized):
    response=initialized.post("/board/new",data={"csrf_token":csrf(initialized,"/board/new"),"title":"First transmission","body":"Hello local network."},follow_redirects=True)
    assert b"First transmission" in response.data
    initialized.post("/board/1",data={"csrf_token":csrf(initialized,"/board/1"),"body":"Signal received."})
    initialized.post("/logout",data={"csrf_token":csrf(initialized,"/terminal")})
    assert b"First transmission" in initialized.get("/board").data; assert b"Signal received" in initialized.get("/board/1").data
    restarted=create_app({"TESTING":True,"DATA_DIR":app.config["DATA_DIR"],"SECRET_KEY":"new-process"}).test_client()
    assert b"First transmission" in restarted.get("/board").data

def test_admin_delete_and_csrf(app, initialized):
    initialized.post("/board/new",data={"csrf_token":csrf(initialized,"/board/new"),"title":"Moderate me","body":"Content"})
    response=initialized.post("/board/1/delete",data={"csrf_token":csrf(initialized,"/board/1")},follow_redirects=True)
    assert b"removed by SYSOP" in response.data
    with app.app_context(): assert get_db().execute("SELECT COUNT(*) FROM posts").fetchone()[0]==0
    assert initialized.post("/board/new",data={"title":"No token","body":"Rejected"}).status_code==400

