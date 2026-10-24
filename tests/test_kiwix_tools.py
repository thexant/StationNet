"""Bundled Kiwix Tools provisioning and runtime lookup."""
from io import BytesIO
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import sys
import zipfile

from stationnet import kiwix

spec = importlib.util.spec_from_file_location(
    "stationnet_kiwix_tools", Path(__file__).resolve().parents[1] / "packaging" / "kiwix_tools.py"
)
kiwix_tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kiwix_tools)


class KiwixToolsTests(TestCase):
    def test_installer_extracts_executable_and_sibling_dlls(self):
        archive_bytes = BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("kiwix-tools/kiwix-serve.exe", b"server")
            archive.writestr("kiwix-tools/dependency.dll", b"library")
        archive_bytes.seek(0)
        with TemporaryDirectory() as temporary, \
             patch.object(kiwix_tools, "archive_name", return_value="tools.zip"), \
             patch.object(kiwix_tools.sys, "platform", "win32"), \
             patch.object(kiwix_tools.urllib.request, "urlopen", return_value=archive_bytes):
            destination = Path(temporary) / "kiwix_tools"
            kiwix_tools.install(destination)
            self.assertEqual((destination / "kiwix-tools" / "kiwix-serve.exe").read_bytes(), b"server")
            self.assertEqual((destination / "kiwix-tools" / "dependency.dll").read_bytes(), b"library")

    def test_runtime_prefers_bundled_executable(self):
        with TemporaryDirectory() as temporary:
            app = Path(temporary)
            bundled = app / "kiwix_tools" / "kiwix-serve.exe"
            bundled.parent.mkdir()
            bundled.touch()
            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "executable", str(app / "StationNet.exe")), \
                 patch.object(kiwix.sys, "platform", "win32"):
                self.assertEqual(kiwix.find_executable(), str(bundled))
