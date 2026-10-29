#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
# The shared core lives in core/; each station's data lives in stations/<id>/.
# A pre-stations data/ folder beside this script is migrated on first start.
export STATIONNET_HOME="$ROOT"
exec "$ROOT/venv/bin/python" "$ROOT/core/run.py" "$@"
