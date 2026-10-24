"""Install the official Kiwix Tools archive into a StationNet app tree."""
import argparse
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

BASE = "https://download.kiwix.org/release/kiwix-tools/"
ARCHIVES = {
    ("win32", "amd64"): "kiwix-tools_win-x86_64-3.8.1.zip",
    ("win32", "x86_64"): "kiwix-tools_win-x86_64-3.8.1.zip",
    ("linux", "x86_64"): "kiwix-tools_linux-x86_64-3.8.2.tar.gz",
    ("linux", "amd64"): "kiwix-tools_linux-x86_64-3.8.2.tar.gz",
    ("linux", "aarch64"): "kiwix-tools_linux-aarch64-3.8.2.tar.gz",
    ("linux", "arm64"): "kiwix-tools_linux-aarch64-3.8.2.tar.gz",
    ("linux", "armv6l"): "kiwix-tools_linux-armv6-3.8.2.tar.gz",
    ("linux", "armv7l"): "kiwix-tools_linux-armv6-3.8.2.tar.gz",
    ("darwin", "x86_64"): "kiwix-tools_macos-x86_64-3.8.2.tar.gz",
    ("darwin", "arm64"): "kiwix-tools_macos-arm64-3.8.2.tar.gz",
}


def archive_name():
    system = sys.platform
    machine = platform.machine().lower()
    try:
        return ARCHIVES[(system, machine)]
    except KeyError:
        raise RuntimeError(f"No bundled Kiwix Tools build for {system}/{machine}") from None


def install(destination):
    """Download at install/build time, then keep the executable beside StationNet."""
    destination = Path(destination)
    executable_name = "kiwix-serve.exe" if sys.platform == "win32" else "kiwix-serve"
    if any(destination.rglob(executable_name)):
        return
    name = archive_name()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        temporary = Path(temporary)
        downloaded = temporary / name
        with urllib.request.urlopen(BASE + name, timeout=120) as response, downloaded.open("wb") as output:
            shutil.copyfileobj(response, output)
        extracted = temporary / "extracted"
        extracted.mkdir()
        if name.endswith(".zip"):
            with zipfile.ZipFile(downloaded) as archive:
                for member in archive.infolist():
                    _extract_member(archive.open, member.filename, member.is_dir(), extracted)
        else:
            with tarfile.open(downloaded, "r:gz") as archive:
                for member in archive.getmembers():
                    if member.isfile() or member.isdir():
                        _extract_member(archive.extractfile, member.name, member.isdir(), extracted, member)
        executable = next(extracted.rglob(executable_name), None)
        if executable is None:
            raise RuntimeError(f"{name} does not contain {executable_name}")
        if sys.platform != "win32":
            executable.chmod(executable.stat().st_mode | 0o111)
        # Keep sibling DLLs and libraries in their original relative locations.
        shutil.copytree(extracted, destination, dirs_exist_ok=True)


def _extract_member(opener, name, is_dir, root, member=None):
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if not parts or any(part in ("..", "") for part in parts) or parts[0].endswith(":") or name.startswith(("/", "\\")):
        raise RuntimeError(f"Unsafe Kiwix archive entry: {name}")
    target = root.joinpath(*parts)
    if is_dir:
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        with opener(member if member is not None else name) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    install(args.destination)
