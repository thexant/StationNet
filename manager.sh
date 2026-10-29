#!/usr/bin/env bash
# Open the StationNet Station Manager for this extracted copy.
set -euo pipefail

INSTALL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
VENV_PYTHON="$INSTALL_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    printf 'ERROR: StationNet is not installed in this folder.\n' >&2
    printf 'Run ./install.sh first.\n' >&2
    exit 1
fi

cd -- "$INSTALL_DIR"
exec "$VENV_PYTHON" "$INSTALL_DIR/manager.py"
