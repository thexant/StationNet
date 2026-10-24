"""Check that trusted MIDI playback can compile WASM without enabling JS eval."""

from tempfile import TemporaryDirectory
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stationnet import create_app
from stationnet.db import get_db


with TemporaryDirectory() as data_dir:
    app = create_app({"TESTING": True, "DATA_DIR": data_dir})
    response = app.test_client().get("/static/midi/engine.js")
    assert response.status_code == 200
    policy = response.headers["Content-Security-Policy"]
    assert "'wasm-unsafe-eval'" in policy
    assert "'unsafe-eval'" not in policy
    with app.app_context():
        db = get_db()
        db.execute("INSERT INTO station(id,name,description,callsign) VALUES(1,'Test','','TST-001')")
        db.execute("INSERT INTO users(id,username,callsign,password_hash) VALUES(1,'tester','USR-001','test')")
        db.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1, csrf_token="token")
    runtime = client.get("/games-apps/midi-composer/runtime")
    assert runtime.status_code == 200
    assert "'wasm-unsafe-eval'" not in runtime.headers["Content-Security-Policy"]
    print("MIDI playback CSP: trusted WebAssembly allowed; sandbox eval blocked")
