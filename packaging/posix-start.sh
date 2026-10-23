#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
export STATIONNET_DATA="$ROOT/data"
exec "$ROOT/venv/bin/python" "$ROOT/app/run.py"
