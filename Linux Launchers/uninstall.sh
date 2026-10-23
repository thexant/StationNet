#!/usr/bin/env bash
# Reset generated StationNet files while preserving this extracted source copy.
set -euo pipefail

INSTALL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PARENT_DIR="$(dirname -- "$INSTALL_DIR")"

printf '\n============================================================\n'
printf ' StationNet uninstaller\n'
printf '============================================================\n'
printf ' This resets StationNet to a clean extracted copy.\n\n'
printf ' PRESERVED:\n  %s\n  Application source, scripts, and documentation\n\n' "$INSTALL_DIR"
printf ' REMOVED:\n  Python environment, station data, and generated caches\n\n'

if [[ ! -f "$INSTALL_DIR/run.py" || ! -f "$INSTALL_DIR/requirements.txt" || ! -f "$INSTALL_DIR/stationnet/__init__.py" || ! -f "$INSTALL_DIR/uninstall.sh" ]]; then
    printf 'ERROR: This does not appear to be a complete StationNet source folder.\n' >&2
    printf 'No files were removed.\n' >&2
    exit 1
fi

if [[ -d "$INSTALL_DIR/data" ]]; then
    if [[ -t 0 ]]; then
        read -r -p 'Back up the station database and data first? [Y/n]: ' backup_data
    else
        backup_data="y"
    fi
    if [[ ! "$backup_data" =~ ^[Nn]$ ]]; then
        stamp="$(date +%Y%m%d-%H%M%S)"
        backup_dir="$PARENT_DIR/StationNet-data-backup-$stamp-$$"
        printf '\nBacking up station data to:\n  %s\n' "$backup_dir"
        if ! cp -a -- "$INSTALL_DIR/data" "$backup_dir"; then
            printf 'ERROR: The backup failed. Nothing has been removed.\n' >&2
            exit 1
        fi
        printf 'Backup completed successfully.\n'
    fi
else
    printf 'No station data directory was found; no backup is needed.\n'
fi

if [[ -t 0 ]]; then
    read -r -p 'Remove installed/runtime files and reset this copy? [y/N]: ' confirm_reset
    if [[ ! "$confirm_reset" =~ ^[Yy]$ ]]; then
        printf 'Uninstall cancelled. Nothing was removed.\n'
        exit 0
    fi
fi

printf '\nRemoving generated StationNet files...\n'
rm -rf -- "$INSTALL_DIR/.venv" "$INSTALL_DIR/data" "$INSTALL_DIR/instance" \
    "$INSTALL_DIR/.pytest_cache" "$INSTALL_DIR/.pytest-tmp" "$INSTALL_DIR/.test-data" \
    "$INSTALL_DIR/__pycache__" "$INSTALL_DIR/.coverage"

# Limit cache cleanup to known source and test trees; never recurse over INSTALL_DIR.
for tree in "$INSTALL_DIR/stationnet" "$INSTALL_DIR/tests"; do
    if [[ -d "$tree" ]]; then
        while IFS= read -r -d '' cache_dir; do
            rm -rf -- "$cache_dir"
        done < <(find "$tree" -type d -name __pycache__ -print0)
    fi
done

if [[ ! -f "$INSTALL_DIR/run.py" || ! -f "$INSTALL_DIR/stationnet/__init__.py" || ! -f "$INSTALL_DIR/install.sh" || ! -f "$INSTALL_DIR/uninstall.sh" ]]; then
    printf 'ERROR: The reset was incomplete; application source was preserved.\n' >&2
    exit 1
fi

printf '\nStationNet was uninstalled successfully.\n'
printf 'The working directory and application source remain in place.\n'
printf 'Run ./install.sh whenever you want to install this copy again.\n'
