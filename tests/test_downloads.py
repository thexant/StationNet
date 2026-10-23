from pathlib import Path
import json

from stationnet import create_app


def _app(tmp_path, distribution_dir):
    return create_app({
        "TESTING": True,
        "DATA_DIR": str(tmp_path / "data"),
        "DISTRIBUTION_DIR": str(distribution_dir),
        "ANDROID_APK_DIR": str(tmp_path / "missing-apks"),
    })


def test_download_page_only_lists_existing_station_packages(tmp_path):
    packages = tmp_path / "packages"
    packages.mkdir()
    package = packages / "StationNet-Windows-AMD64.zip"
    package.write_bytes(b"stationnet package")
    (packages / "unrelated.zip").write_bytes(b"not public")
    client = _app(tmp_path, packages).test_client()

    page = client.get("/get-station/")
    assert page.status_code == 200
    assert package.name.encode() in page.data
    assert b"unrelated.zip" not in page.data
    response = client.get("/get-station/download/StationNet-Windows-AMD64.zip")
    assert response.status_code == 200
    assert response.data == b"stationnet package"
    assert client.get("/get-station/download/../../data/station.db").status_code == 404


def test_download_page_is_linked_in_station_footer(tmp_path):
    packages = tmp_path / "packages"
    packages.mkdir()
    client = _app(tmp_path, packages).test_client()
    assert b"GET YOUR OWN STATION" in client.get("/setup").data


def test_download_page_displays_staged_manifest_metadata(tmp_path):
    packages = tmp_path / "packages"
    packages.mkdir()
    name = "StationNet-source.zip"
    (packages / name).write_bytes(b"bundle")
    (packages / "packages.json").write_text(json.dumps({
        "schema": 1, "packages": [{"name": name, "platform": "Source",
        "version": "20261023.120000+abc123", "size": 6, "sha256": "a" * 64}]
    }), encoding="utf-8")
    page = _app(tmp_path, packages).test_client().get("/get-station/")
    assert b"20261023.120000+abc123" in page.data
    assert ("a" * 64).encode() in page.data


def test_source_build_omits_station_data(tmp_path, monkeypatch):
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("build_bundle", root / "packaging" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "OUT", tmp_path)
    module.build_source()
    import zipfile
    with zipfile.ZipFile(tmp_path / "StationNet-source.zip") as archive:
        names = archive.namelist()
    assert "stationnet/templates/setup.html" in names
    assert not any(name.startswith("data/") for name in names)
