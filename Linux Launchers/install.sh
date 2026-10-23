#!/usr/bin/env bash
# StationNet installer for Linux, macOS, other Unix systems, and Termux.
set -euo pipefail

INSTALL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
VENV_DIR="$INSTALL_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

printf '\n============================================================\n'
printf ' StationNet installer\n'
printf '============================================================\n'
printf ' Install directory: %s\n\n' "$INSTALL_DIR"

if [[ ! -f "$INSTALL_DIR/run.py" || ! -f "$INSTALL_DIR/requirements.txt" ]]; then
    printf 'ERROR: run.py or requirements.txt is missing.\n'
    printf 'Extract a complete StationNet copy, then run install.sh from that folder.\n' >&2
    exit 1
fi

PYTHON_COMMAND=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
        PYTHON_COMMAND="$candidate"
        break
    fi
done

if [[ -z "$PYTHON_COMMAND" ]]; then
    printf 'ERROR: Python 3.11 or newer was not found.\n' >&2
    printf 'Install Python 3.11+ (Termux: pkg install python), then try again.\n' >&2
    exit 1
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
    printf '[1/3] Creating the Python environment...\n'
    "$PYTHON_COMMAND" -m venv "$VENV_DIR"
else
    printf '[1/3] Existing Python environment found.\n'
fi

printf '[2/3] Installing StationNet dependencies...\n'
"$VENV_PYTHON" -m pip install --disable-pip-version-check -r "$INSTALL_DIR/requirements.txt"

printf '[3/3] Preparing the data directory...\n'
mkdir -p "$INSTALL_DIR/data"

printf '\nStationNet is installed successfully.\n'
printf 'Station data will be stored in "%s/data".\n' "$INSTALL_DIR"
printf 'Use ./run.sh to start StationNet later.\n'

if [[ -t 0 ]]; then
    read -r -p 'Start StationNet now? [y/N]: ' start_now
    if [[ "$start_now" =~ ^[Yy]$ ]]; then
        exec bash "$INSTALL_DIR/run.sh"
    fi
fi
