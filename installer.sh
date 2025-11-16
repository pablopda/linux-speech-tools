#!/usr/bin/env bash
# Linux Speech Tools Installer
# Modern installation using uv package manager

set -euo pipefail

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV_INSTALLER="$SCRIPT_DIR/scripts/install/install-with-uv.sh"

# Simply redirect to the modern installer
exec "$UV_INSTALLER" "$@"