#!/usr/bin/env bash
# Compatibility wrapper for the old faster-whisper installer.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "install-faster.sh is now a compatibility wrapper."
echo "Using installer.sh --with-stt --download-models instead."
echo ""
echo "Pass --setup-uinput if you want optional direct typing permissions."
echo ""

exec "$SCRIPT_DIR/installer.sh" --with-stt --download-models "$@"
