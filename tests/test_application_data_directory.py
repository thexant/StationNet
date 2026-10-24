"""Installed packages travel with station data, separate from bundled code."""
import io
import json
import shutil
import unittest
import uuid
import zipfile
from pathlib import Path

from stationnet import create_app
from stationnet.applications import _entrypoint, _package_root, catalog, install_package


class ApplicationDataDirectoryTests(unittest.TestCase):
    def test_installed_game_survives_data_directory_copy(self):
        test_root = Path(__file__).resolve().parents[1] / ".test-data"
        test_root.mkdir(exist_ok=True)
        base = test_root / f"application-copy-{uuid.uuid4().hex}"
        base.mkdir()
        try:
            original_data = base / "original" / "data"
            app = create_app({"TESTING": True, "DATA_DIR": str(original_data)})
            manifest = {
                "package_format": 1,
                "slug": "backup-test-game",
                "name": "Backup Test Game",
                "description": "Tests station data portability.",
                "author": "StationNet",
                "version": "1.0",
                "category": "Games",
                "entrypoint": "game.js",
            }
            package = io.BytesIO()
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                archive.writestr("game.js", "StationNet.start(() => {});")

            with app.app_context():
                installed = install_package(package.getvalue())
                self.assertFalse(installed.builtin)
                self.assertEqual(
                    _entrypoint(installed),
                    (original_data / "applications" / installed.slug / "game.js").resolve(),
                )
                self.assertFalse((_package_root() / installed.slug).exists())

            restored_data = base / "replacement" / "data"
            shutil.copytree(original_data, restored_data)
            restored_app = create_app({"TESTING": True, "DATA_DIR": str(restored_data)})
            with restored_app.app_context():
                restored = next(item for item in catalog() if item.slug == installed.slug)
                self.assertFalse(restored.builtin)
                self.assertEqual(
                    _entrypoint(restored),
                    (restored_data / "applications" / restored.slug / "game.js").resolve(),
                )
                self.assertEqual(_entrypoint(restored).read_text(), "StationNet.start(() => {});")
        finally:
            shutil.rmtree(base)


if __name__ == "__main__":
    unittest.main()
