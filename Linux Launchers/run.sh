#!/usr/bin/env bash
# Launch StationNet from this extracted copy.
set -euo pipefail

INSTALL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
VENV_PYTHON="$INSTALL_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    printf 'ERROR: StationNet is not installed in this folder.\n' >&2
    printf 'Run ./install.sh first.\n' >&2
    exit 1
fi

printf 'Starting StationNet at http://127.0.0.1:8080\n'
printf 'Press Ctrl+C in this terminal to stop the server.\n\n'
cd -- "$INSTALL_DIR"
exec "$VENV_PYTHON" "$INSTALL_DIR/run.py"
