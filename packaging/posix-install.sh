#!/usr/bin/env sh
set -eu
PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
TARGET=${STATIONNET_INSTALL_DIR:-"$HOME/.local/share/stationnet"}
case "$TARGET" in /*) ;; *) echo 'STATIONNET_INSTALL_DIR must be absolute' >&2; exit 1;; esac
test -d "$PACKAGE_DIR/core/stationnet" || { echo 'Incomplete package' >&2; exit 1; }
test -d "$PACKAGE_DIR/wheels" || { echo 'Offline wheels missing' >&2; exit 1; }
if [ -d "$TARGET/core" ] || [ -d "$TARGET/app" ]; then
    echo 'Stop the station before upgrading.'
fi
# Station data lives in stations/<id>. An existing data/ folder is left alone
# here; StationNet migrates it into stations/default on first start.
mkdir -p "$TARGET/stations"
STAGE="$TARGET/core.new.$$"
OLD="$TARGET/core.old.$$"
trap 'rm -rf -- "$STAGE"' EXIT HUP INT TERM
cp -R "$PACKAGE_DIR/core" "$STAGE"
if [ ! -x "$TARGET/venv/bin/python" ]; then
    python3 -m venv "$TARGET/venv"
fi
"$TARGET/venv/bin/python" -m pip install --no-index --find-links "$PACKAGE_DIR/wheels" -r "$STAGE/requirements.txt"
if [ -d "$TARGET/core" ]; then mv "$TARGET/core" "$OLD"; fi
if ! mv "$STAGE" "$TARGET/core"; then
    if [ -d "$OLD" ]; then mv "$OLD" "$TARGET/core"; fi
    exit 1
fi
cp "$PACKAGE_DIR/start.sh" "$TARGET/start.sh"
chmod +x "$TARGET/start.sh"
cp "$PACKAGE_DIR/manager.sh" "$TARGET/manager.sh"
chmod +x "$TARGET/manager.sh"
if [ -d "$OLD" ]; then rm -rf -- "$OLD"; fi
# Pre-stations installs kept the application (code only, never data) in app/.
if [ -f "$TARGET/app/run.py" ] && [ -f "$TARGET/core/run.py" ]; then rm -rf -- "$TARGET/app"; fi
echo "Installed. Start with: $TARGET/start.sh"
echo "To create and run several stations: $TARGET/manager.sh (needs Tkinter, e.g. python3-tk)"
echo 'Open http://127.0.0.1:8080 to create the first sysop account.'
