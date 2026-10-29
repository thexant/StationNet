"""Build installable StationNet archives from the current checkout.

Run `python packaging/build.py posix` on the target OS/CPU/Python to make an
offline wheel bundle, `python packaging/build.py windows` on Windows to make a
Python-free executable bundle, or `python packaging/build.py source` to make a
source archive. Builds never include station data.
"""
import argparse
from contextlib import contextmanager
import platform
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path
from kiwix_tools import install as install_kiwix_tools

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist"


@contextmanager
def build_workspace():
    """Keep intermediate app trees beside output, away from station data."""
    path = OUT / (".workspace-" + uuid.uuid4().hex)
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def copy_app(destination):
    for asset in ("gm.sf2", "engine.js", "spessasynth_processor.min.js",
                  "SOUNDFONT-LICENSE.txt", "SPESSASYNTH-LICENSE.txt",
                  "SPESSASYNTH-CORE-LICENSE.txt", "THIRD-PARTY-NOTICES.md"):
        if not (ROOT / "stationnet" / "static" / "midi" / asset).is_file():
            raise FileNotFoundError(f"MIDI distribution asset is missing: {asset}")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "run.py", destination / "run.py")
    shutil.copy2(ROOT / "icon.png", destination / "icon.png")
    shutil.copy2(ROOT / "requirements.txt", destination / "requirements.txt")
    shutil.copytree(ROOT / "stationnet", destination / "stationnet",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def archive(folder, name):
    OUT.mkdir(exist_ok=True)
    target = OUT / name
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as result:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                result.write(path, path.relative_to(folder))
    print(target)


def build_posix():
    if sys.platform == "win32":
        raise SystemExit("Build POSIX wheel bundles on the target Linux or Raspberry Pi platform.")
    with build_workspace() as temp:
        package = temp / "StationNet"
        copy_app(package / "core")
        install_kiwix_tools(package / "core" / "kiwix_tools")
        wheels = package / "wheels"
        wheels.mkdir(parents=True)
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--wheel-dir", str(wheels),
                        "-r", str(ROOT / "requirements.txt")], check=True)
        shutil.copy2(ROOT / "packaging" / "posix-install.sh", package / "install.sh")
        shutil.copy2(ROOT / "packaging" / "posix-start.sh", package / "start.sh")
        archive(package, f"StationNet-{sys.platform}-{platform.machine()}-{sys.implementation.cache_tag}.zip")


def build_windows():
    if sys.platform != "win32":
        raise SystemExit("Build the Windows executable on Windows.")
    subprocess.run([sys.executable, "-c", "import PyInstaller"], check=True)
    with build_workspace() as workspace:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                        "--onedir", "--name", "StationNet", "--collect-all", "stationnet",
                        "--add-data", f"{ROOT / 'icon.png'}{';' if sys.platform == 'win32' else ':'}.",
                        "--collect-all", "pyfiglet",
                        "--distpath", str(workspace / "frozen"), "--workpath", str(workspace / "build"),
                        "--specpath", str(workspace), str(ROOT / "run.py")], cwd=ROOT, check=True)
        package = workspace / "StationNet"
        shutil.copytree(workspace / "frozen" / "StationNet", package / "core")
        install_kiwix_tools(package / "core" / "kiwix_tools")
        shutil.copy2(ROOT / "packaging" / "windows-start.bat", package / "start.bat")
        shutil.copy2(ROOT / "packaging" / "windows-install.bat", package / "install.bat")
        shutil.copy2(ROOT / "packaging" / "windows-install.ps1", package / "install.ps1")
        archive(package, f"StationNet-Windows-{platform.machine()}.zip")


def build_source():
    """Create the optional local source download without runtime data/builds."""
    with build_workspace() as temp:
        package = temp / "StationNet-source"
        copy_app(package)
        for name in (
            "README.md", "DISTRIBUTION.md", "BROADCASTING.md",
            "package.json", "package-lock.json",
            "build-all.ps1", "install.bat", "uninstall.bat", "install.sh",
            "uninstall.sh", "run.sh",
        ):
            source = ROOT / name
            if not source.is_file():
                raise FileNotFoundError(f"Source package requires {source}")
            shutil.copy2(source, package / name)
        if (ROOT / "LICENSE").is_file():
            shutil.copy2(ROOT / "LICENSE", package / "LICENSE")
        shutil.copytree(ROOT / "packaging", package / "packaging",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "scripts", package / "scripts")
        shutil.copytree(ROOT / "android", package / "android", ignore=shutil.ignore_patterns(
            ".gradle", "build", "__pycache__", "*.pyc", "stationnet"
        ))
        archive(package, "StationNet-source.zip")


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=("posix", "windows", "source"))
    parser.add_argument("--output-dir", type=Path, default=OUT,
                        help="Directory for the finished archive (default: dist)")
    args = parser.parse_args()
    OUT = args.output_dir.resolve()
    choice = args.platform
    {"posix": build_posix, "windows": build_windows, "source": build_source}[choice]()


if __name__ == "__main__":
    main()
