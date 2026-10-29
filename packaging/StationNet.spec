# -*- mode: python ; coding: utf-8 -*-
# Windows build used by packaging/build.py: one core\ folder holding the
# console station server (StationNet.exe) and the windowed Station Manager
# (StationNetManager.exe), sharing the same libraries.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "icon.png"), ".")]
binaries = []
hiddenimports = []
for package in ("stationnet", "pyfiglet"):
    package_datas, package_binaries, package_imports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_imports

server = Analysis([str(ROOT / "run.py")], pathex=[str(ROOT)], binaries=binaries, datas=datas,
                  hiddenimports=hiddenimports, noarchive=False, optimize=0)
# pystray picks its platform backend at runtime, so PyInstaller cannot see it.
manager = Analysis([str(ROOT / "manager.py")], pathex=[str(ROOT)],
                   hiddenimports=["tkinter", *collect_submodules("pystray")],
                   noarchive=False, optimize=0)

server_exe = EXE(PYZ(server.pure), server.scripts, [], exclude_binaries=True, name="StationNet",
                 debug=False, bootloader_ignore_signals=False, strip=False, upx=True, console=True)
manager_exe = EXE(PYZ(manager.pure), manager.scripts, [], exclude_binaries=True, name="StationNetManager",
                  debug=False, bootloader_ignore_signals=False, strip=False, upx=True, console=False)

COLLECT(server_exe, manager_exe, server.binaries, server.datas, manager.binaries, manager.datas,
        strip=False, upx=True, upx_exclude=[], name="StationNet")
