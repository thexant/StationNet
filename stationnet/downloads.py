"""Station-hosted StationNet distribution packages."""
import json
from pathlib import Path

from flask import Blueprint, abort, current_app, render_template, send_file

bp = Blueprint("downloads", __name__, url_prefix="/get-station")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SUFFIXES = {".zip", ".apk"}


def _package_roots():
    """Return the local output directories that may contain public builds."""
    configured = current_app.config["DISTRIBUTION_DIR"]
    android = current_app.config["ANDROID_APK_DIR"]
    return (Path(configured), Path(android))


def _package_kind(path):
    name = path.name.casefold()
    if path.suffix.casefold() == ".apk":
        return "Android APK", "Android 8+ on 64-bit ARM (arm64-v8a)."
    if "windows" in name:
        return "Windows offline package", "Windows x64. No Python or network connection required."
    if "raspberry" in name or "aarch64" in name or "arm" in name:
        return "Raspberry Pi offline package", "Build must match the Pi OS, CPU architecture, and Python 3.11+ ABI."
    if "source" in name:
        return "Source package", "Python 3.11+; includes the source checkout and packaging scripts."
    return "Linux offline package", "Build must match the Linux OS, CPU architecture, and Python 3.11+ ABI."


def available_packages():
    """Inventory only deliberate, locally present distribution artifacts."""
    packages = []
    seen = set()
    manifest = {}
    manifest_path = Path(current_app.config["DISTRIBUTION_DIR"]) / "packages.json"
    try:
        metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = {item["name"]: item for item in metadata["packages"]}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    staged_android = any(
        path.name.startswith("StationNet-Android") and path.suffix.casefold() == ".apk"
        for path in Path(current_app.config["DISTRIBUTION_DIR"]).glob("*")
    )
    for root in _package_roots():
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if staged_android and path.name == "app-debug.apk":
                continue
            if not path.is_file() or path.suffix.casefold() not in PACKAGE_SUFFIXES:
                continue
            # Build output directories can contain metadata files.  Keep the
            # public surface to StationNet-named archives and APKs only.
            if not path.name.startswith("StationNet") and path.name != "app-debug.apk":
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            kind, requirement = _package_kind(path)
            entry = manifest.get(path.name, {})
            if entry.get("size") != path.stat().st_size:
                entry = {}
            packages.append({
                "name": path.name, "path": resolved, "kind": kind,
                "requirement": requirement, "size": path.stat().st_size,
                "version": entry.get("version"), "sha256": entry.get("sha256"),
            })
    return sorted(packages, key=lambda item: (item["kind"], item["name"].casefold()))


def _format_size(value):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{value} B"
        value /= 1024


@bp.get("")
@bp.get("/")
def index():
    packages = available_packages()
    for package in packages:
        package["size_label"] = _format_size(package["size"])
    docs = []
    for filename, label in (("README.md", "Source README"),
                            ("DISTRIBUTION.md", "Distribution guide"),
                            ("LICENSE", "Licence")):
        if (PROJECT_ROOT / filename).is_file():
            docs.append({"filename": filename, "label": label})
    return render_template("downloads.html", packages=packages, docs=docs)


@bp.get("/download/<path:filename>")
def download(filename):
    # Match a fresh inventory instead of accepting a filesystem path. This
    # keeps package hosting local and prevents access to station data.
    package = next((item for item in available_packages() if item["name"] == filename), None)
    if package is None:
        abort(404)
    return send_file(package["path"], as_attachment=True, download_name=package["name"], conditional=True)


@bp.get("/info/<filename>")
def info(filename):
    if filename not in {"README.md", "DISTRIBUTION.md", "LICENSE"}:
        abort(404)
    path = PROJECT_ROOT / filename
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="text/plain; charset=utf-8", conditional=True)
