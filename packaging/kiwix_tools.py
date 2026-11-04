"""Install the official Kiwix Tools archive into a StationNet app tree."""
import argparse
import hashlib
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


USER_AGENT = "StationNet-build"
ATTEMPTS = 3


def fetch(cache_dir):
    """Return a verified local copy of this platform's archive, downloading it if needed."""
    name = archive_name()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / name
    expected_md5 = _published_md5(name)
    if target.is_file():
        if _verify(target, name, expected_md5):
            print(f"Using cached {name}")
            return target
        print(f"Cached {name} is corrupt; downloading again")
        target.unlink()
    url = BASE + name
    partial = target.with_name(name + ".part")
    last_error = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            _download(url, partial)
            if not _verify(partial, name, expected_md5):
                raise RuntimeError("downloaded file failed verification")
            partial.replace(target)
            return target
        except (OSError, RuntimeError) as error:  # URLError and timeouts are OSErrors
            last_error = error
            print(f"Download attempt {attempt}/{ATTEMPTS} of {name} failed: {error}")
            partial.unlink(missing_ok=True)
    raise RuntimeError(
        f"Could not download Kiwix Tools from {url} ({last_error}). "
        f"You can download it manually and save it as {target}, then build again."
    )


def install(destination, cache_dir=None):
    """Download at install/build time, then keep the executable beside StationNet."""
    destination = Path(destination)
    executable_name = "kiwix-serve.exe" if sys.platform == "win32" else "kiwix-serve"
    if any(destination.rglob(executable_name)):
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        temporary = Path(temporary)
        downloaded = fetch(cache_dir if cache_dir is not None else temporary / "download")
        name = downloaded.name
        extracted = temporary / "extracted"
        extracted.mkdir()
        try:
            if name.endswith(".zip"):
                with zipfile.ZipFile(downloaded) as archive:
                    for member in archive.infolist():
                        _extract_member(archive.open, member.filename, member.is_dir(), extracted)
            else:
                with tarfile.open(downloaded, "r:gz") as archive:
                    for member in archive.getmembers():
                        if member.isfile() or member.isdir():
                            _extract_member(archive.extractfile, member.name, member.isdir(), extracted, member)
        except (zipfile.BadZipFile, tarfile.TarError, EOFError) as error:
            downloaded.unlink(missing_ok=True)
            raise RuntimeError(f"{downloaded} is not a valid archive ({error}); "
                               f"it has been removed, so build again to re-download it.") from None
        executable = next(extracted.rglob(executable_name), None)
        if executable is None:
            raise RuntimeError(f"{name} does not contain {executable_name}")
        if sys.platform != "win32":
            executable.chmod(executable.stat().st_mode | 0o111)
        # Keep sibling DLLs and libraries in their original relative locations.
        shutil.copytree(extracted, destination, dirs_exist_ok=True)


def _open(url, timeout=60):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                                  timeout=timeout)


def _published_md5(name):
    """Kiwix publishes `<archive>.md5`; return its digest, or None if it is unavailable."""
    try:
        with _open(BASE + name + ".md5", timeout=30) as response:
            digest = response.read(4096).decode("ascii", "replace").split()[0].lower()
    except (OSError, IndexError):
        return None
    return digest if len(digest) == 32 and all(c in "0123456789abcdef" for c in digest) else None


def _download(url, target):
    with _open(url) as response, target.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        received = 0
        last_report = -1
        while chunk := response.read(256 * 1024):
            output.write(chunk)
            received += len(chunk)
            step = received // (2 * 1024 * 1024)
            if step != last_report:
                last_report = step
                size = f"{received / 1e6:.1f}/{total / 1e6:.1f} MB" if total else f"{received / 1e6:.1f} MB"
                print(f"Downloading {target.name.removesuffix('.part')}: {size}", flush=True)
    if total and received != total:
        raise RuntimeError(f"download truncated ({received} of {total} bytes)")


def _verify(path, name, expected_md5):
    if expected_md5:
        digest = hashlib.md5()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest() == expected_md5
    return zipfile.is_zipfile(path) if name.endswith(".zip") else tarfile.is_tarfile(path)


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
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="Keep the downloaded archive here and reuse it on later runs")
    args = parser.parse_args()
    install(args.destination, args.cache_dir)
