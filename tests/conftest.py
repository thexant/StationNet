import pytest
from pathlib import Path
from stationnet import create_app

@pytest.fixture()
def app():
    data_dir = Path(__file__).parent.parent / ".test-data"
    data_dir.mkdir(exist_ok=True)
    for name in ("station.db", "station.db-shm", "station.db-wal", "secret.key"):
        path = data_dir / name
        if path.exists(): path.unlink()
    application = create_app({"TESTING": True, "DATA_DIR": str(data_dir), "SECRET_KEY": "test-secret"})
    yield application
    for name in ("station.db", "station.db-shm", "station.db-wal", "secret.key"):
        path = data_dir / name
        if path.exists(): path.unlink()

@pytest.fixture()
def client(app): return app.test_client()

def csrf(client, path="/setup"):
    client.get(path)
    with client.session_transaction() as session: return session["csrf_token"]

@pytest.fixture()
def initialized(client):
    response=client.post("/setup", data={"csrf_token":csrf(client),"name":"Test Relay","description":"Local test station","callsign":"TST-01","username":"sysop","password":"correct horse battery staple","confirm_password":"correct horse battery staple","registration_open":"open"}, follow_redirects=True)
    assert response.status_code==200
    return client

