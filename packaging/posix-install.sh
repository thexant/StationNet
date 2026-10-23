#!/usr/bin/env sh
set -eu
PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
TARGET=${STATIONNET_INSTALL_DIR:-"$HOME/.local/share/stationnet"}
case "$TARGET" in /*) ;; *) echo 'STATIONNET_INSTALL_DIR must be absolute' >&2; exit 1;; esac
test -d "$PACKAGE_DIR/app/stationnet" || { echo 'Incomplete package' >&2; exit 1; }
test -d "$PACKAGE_DIR/wheels" || { echo 'Offline wheels missing' >&2; exit 1; }
if [ -d "$TARGET/app" ]; then
    echo 'Stop the station before upgrading.'
fi
mkdir -p "$TARGET/data"
STAGE="$TARGET/app.new.$$"
OLD="$TARGET/app.old.$$"
trap 'rm -rf -- "$STAGE"' EXIT HUP INT TERM
cp -R "$PACKAGE_DIR/app" "$STAGE"
if [ ! -x "$TARGET/venv/bin/python" ]; then
    python3 -m venv "$TARGET/venv"
fi
"$TARGET/venv/bin/python" -m pip install --no-index --find-links "$PACKAGE_DIR/wheels" -r "$STAGE/requirements.txt"
if [ -d "$TARGET/app" ]; then mv "$TARGET/app" "$OLD"; fi
if ! mv "$STAGE" "$TARGET/app"; then
    if [ -d "$OLD" ]; then mv "$OLD" "$TARGET/app"; fi
    exit 1
fi
cp "$PACKAGE_DIR/start.sh" "$TARGET/start.sh"
chmod +x "$TARGET/start.sh"
if [ -d "$OLD" ]; then rm -rf -- "$OLD"; fi
echo "Installed. Start with: $TARGET/start.sh"
echo 'Open http://127.0.0.1:8080 to create the first sysop account.'
