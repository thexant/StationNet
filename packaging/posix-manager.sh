#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
# Desktop launcher for every station under stations/; stations it starts keep
# running after it closes.
export STATIONNET_HOME="$ROOT"
exec "$ROOT/venv/bin/python" "$ROOT/core/manager.py" "$@"
