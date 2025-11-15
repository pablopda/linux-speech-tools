#!/usr/bin/env bash
# Linux Speech Tools Installer - Main Entry Point
# This is a wrapper that calls the actual installer in the reorganized structure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER_PATH="$SCRIPT_DIR/scripts/install/installer.sh"

if [[ -f "$INSTALLER_PATH" ]]; then
    echo "Running installer from reorganized structure..."
    exec "$INSTALLER_PATH" "$@"
else
    echo "Error: Installer not found at $INSTALLER_PATH" >&2
    echo "Please ensure you have the complete linux-speech-tools repository." >&2
    exit 1
fi