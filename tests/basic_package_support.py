"""BASIC application packages shared by the package and browser tests."""
import io
import json
import struct
import zipfile
import zlib

# PRINT draws on the program screen; _ECHO also reaches the host's console,
# which is what the browser tests read.
TEXT_PROGRAM = '''CLS
PRINT "TEXT APP READY"
_ECHO "TEXT APP READY"
INPUT "Name"; who$
PRINT "HELLO "; who$
_ECHO "HELLO " + who$
'''

# Draws, loads a bundled asset from the virtual file system, plays a note,
# and keeps a run counter in localStorage (saved through StationNet storage).
GRAPHICS_PROGRAM = '''Import Storage From "lib/web/storage.bas"
SCREEN _NEWIMAGE(320, 200, 32)
LINE (0, 0)-(319, 199), _RGB32(0, 0, 80), BF
LINE (10, 10)-(150, 90), _RGB32(255, 200, 0), BF
CIRCLE (240, 100), 40, _RGB32(0, 255, 120)
img& = _LOADIMAGE("assets/tile.png")
_PUTIMAGE (200, 20), img&
runs = VAL(Storage.Get("runs", Storage.LOCAL)) + 1
Storage.Set "runs", STR$(runs), Storage.LOCAL
_ECHO "RUNS" + STR$(runs)
SOUND 440, 1
IF _WIDTH(img&) = 16 THEN _ECHO "ASSET LOADED"
_ECHO "GRAPHICS APP READY"
DO
    _LIMIT 30
LOOP
'''


def png(width=4, height=4, rgb=(255, 0, 0)):
    """Return a tiny solid-colour PNG."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def manifest(slug, **overrides):
    data = {
        "package_format": 1, "runtime": "basic", "slug": slug, "name": slug.replace("-", " ").title(),
        "description": "A BASIC test program.", "author": "coder", "version": "1.0.0",
        "category": "Games", "entrypoint": "main.bas",
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def package(slug, source=TEXT_PROGRAM, files=None, **overrides):
    """Build a ZIP the way Office BASIC exports it; overrides edit the manifest."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest(slug, **overrides)))
        if source is not None:
            archive.writestr(overrides.get("entrypoint") or "main.bas",
                             source if isinstance(source, bytes) else source.replace("\n", "\r\n"))
        for name, content in (files or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def graphics_package(slug="pixel-demo"):
    return package(slug, GRAPHICS_PROGRAM, {"assets/tile.png": png(16, 16, (255, 0, 255))},
                   category="Creative", preferred_presentation="standard")
