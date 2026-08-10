#!/usr/bin/env bash
# Linux Speech Tools Installer
#
# This file is intentionally self-contained: it must work both from a cloned
# checkout and when downloaded from a release's versioned bootstrap tag.
#
# TRUST BOUNDARY (read before changing the bootstrap below):
#   The bootstrap script cannot verify its own bytes. Users should fetch it from
#   the advertised `bootstrap-vX.Y.Z` tag and may inspect it before execution.
#   Everything it subsequently downloads is a versioned GitHub release asset
#   whose SHA256 is pinned in that bootstrap tag. The release workflow never
#   advertises a mutable branch entry point. Keep the asset URL versioned and
#   the SHA256 gate fail-closed.

set -euo pipefail

BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

has_arg() {
    local needle="$1"
    shift
    for arg in "$@"; do
        [ "$arg" = "$needle" ] && return 0
    done
    return 1
}

script_source="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [ -n "$script_source" ] && [ -f "$script_source" ] \
    && [[ "$script_source" != /dev/fd/* ]] \
    && [[ "$script_source" != /proc/self/fd/* ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$script_source")" && pwd)"
fi

BOOTSTRAP_CHECK=false
if has_arg "--bootstrap-check" "$@"; then
    BOOTSTRAP_CHECK=true
fi

if [ "$BOOTSTRAP_CHECK" = false ] && [ -n "$SCRIPT_DIR" ] \
    && [ -x "$SCRIPT_DIR/scripts/install/install-with-uv.sh" ]; then
    UV_INSTALLER="$SCRIPT_DIR/scripts/install/install-with-uv.sh"
    exec "$UV_INSTALLER" "$@"
fi

DRY_RUN=false
if has_arg "--dry-run" "$@"; then
    DRY_RUN=true
fi

# Pinned release the bootstrap installer installs. These constants
# are RELEASE-MANAGED: scripts/release/release.sh rewrites INSTALLER_REF,
# DEFAULT_INSTALLER_REF and DEFAULT_TARBALL_SHA256 when it cuts a tag, and
# scripts/release/pre-release-check.sh validates they exist and agree.
#
# The source tag is created first. release.sh then builds and uploads a stable,
# versioned release asset, verifies the uploaded bytes, writes their SHA256 in a
# follow-up commit, and creates `bootstrap-vX.Y.Z`. If that second phase is not
# completed, no bootstrap command is advertised and stale values fail closed.
INSTALLER_REF="${LST_INSTALLER_REF:-v1.1.0}"
DEFAULT_INSTALLER_REF="v1.1.0"
DEFAULT_INSTALLER_VERSION="${DEFAULT_INSTALLER_REF#v}"
DEFAULT_TARBALL_URL="https://github.com/pablopda/linux-speech-tools/releases/download/${DEFAULT_INSTALLER_REF}/linux-speech-tools-${DEFAULT_INSTALLER_VERSION}.tar.gz"
DEFAULT_TARBALL_SHA256="5b079798cd2859e2d44cb022c8df3f3b2fac0d66f34cf94afb65426bd01d98b8"
INSTALLER_VERSION="${INSTALLER_REF#v}"
REPO_TARBALL_URL="${LST_INSTALLER_TARBALL_URL:-https://github.com/pablopda/linux-speech-tools/releases/download/${INSTALLER_REF}/linux-speech-tools-${INSTALLER_VERSION}.tar.gz}"
REPO_TARBALL_SHA256="${LST_INSTALLER_SHA256:-}"
SOURCE_DIR="${LST_SOURCE_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/linux-speech-tools/source}"

info "No adjacent checkout found; bootstrapping Linux Speech Tools source."
info "Source target: $SOURCE_DIR"

if [ -z "$REPO_TARBALL_SHA256" ]; then
    if [ "$INSTALLER_REF" = "$DEFAULT_INSTALLER_REF" ] && [ "$REPO_TARBALL_URL" = "$DEFAULT_TARBALL_URL" ]; then
        REPO_TARBALL_SHA256="$DEFAULT_TARBALL_SHA256"
    else
        error "Streamed installs require LST_INSTALLER_SHA256 for custom refs or tarball URLs."
        exit 1
    fi
fi

validate_source_dir() {
    local target="$1"
    local resolved_parent
    local resolved_target
    local home_real

    case "$target" in
        ""|"/"|"/root"|"$HOME")
            error "Refusing unsafe LST_SOURCE_DIR: $target"
            exit 1
            ;;
    esac

    home_real="$(readlink -f "$HOME" 2>/dev/null || printf '%s\n' "$HOME")"
    resolved_target="$(readlink -m "$target" 2>/dev/null || printf '%s\n' "$target")"
    if [ "$resolved_target" = "$home_real" ]; then
        error "Refusing to use home directory as LST_SOURCE_DIR: $target"
        exit 1
    fi

    if [ -L "$target" ]; then
        error "Refusing symlink LST_SOURCE_DIR: $target"
        exit 1
    fi
    if [ -e "$target" ] && [ ! -d "$target" ]; then
        error "Refusing non-directory LST_SOURCE_DIR: $target"
        exit 1
    fi
    if [ -d "$target" ]; then
        if [ "$(stat -c '%u' "$target" 2>/dev/null || echo "")" != "$(id -u)" ]; then
            error "Refusing LST_SOURCE_DIR not owned by the current user: $target"
            exit 1
        fi
        if [ ! -f "$target/pyproject.toml" ] \
            || [ ! -f "$target/installer.sh" ] \
            || [ ! -f "$target/scripts/install/install-with-uv.sh" ]; then
            error "Refusing to replace unrecognized existing directory: $target"
            error "Choose a linux-speech-tools source directory or move the existing path manually."
            exit 1
        fi
    fi

    resolved_parent="$(readlink -m "$(dirname "$target")" 2>/dev/null || dirname "$target")"
    case "$resolved_parent" in
        "/"|"$home_real")
            error "Refusing unsafe source parent: $resolved_parent"
            exit 1
            ;;
    esac
}

validate_source_dir "$SOURCE_DIR"

if [ "$DRY_RUN" = true ]; then
    warn "Dry run: would download $REPO_TARBALL_URL"
    warn "Dry run: would verify SHA256 $REPO_TARBALL_SHA256"
    warn "Dry run: would extract to $SOURCE_DIR and run scripts/install/install-with-uv.sh"
    exit 0
fi

download_cmd=()
if command -v curl >/dev/null 2>&1; then
    download_cmd=(curl -fsSL "$REPO_TARBALL_URL")
elif command -v wget >/dev/null 2>&1; then
    download_cmd=(wget -qO- "$REPO_TARBALL_URL")
else
    error "curl or wget is required to bootstrap from the streamed installer."
    exit 1
fi

if ! command -v tar >/dev/null 2>&1; then
    error "tar is required to extract the streamed installer source."
    exit 1
fi

tmp_dir="$(mktemp -d)"
stage_dir=""
backup_dir=""
cleanup() {
    rm -rf "$tmp_dir"
    if [ -n "$stage_dir" ]; then
        rm -rf "$stage_dir"
    fi
}
trap cleanup EXIT

info "Downloading source archive..."
archive="$tmp_dir/source.tar.gz"
"${download_cmd[@]}" > "$archive"
if ! command -v sha256sum >/dev/null 2>&1; then
    error "sha256sum is required for streamed installer verification."
    exit 1
fi
actual_sha256="$(sha256sum "$archive" | awk '{print $1}')"
if [ "$actual_sha256" != "$REPO_TARBALL_SHA256" ]; then
    error "Downloaded source checksum mismatch: $actual_sha256 != $REPO_TARBALL_SHA256"
    exit 1
fi
tar -xzf "$archive" -C "$tmp_dir" --strip-components=1

if [ "$BOOTSTRAP_CHECK" = true ]; then
    if [ ! -f "$tmp_dir/pyproject.toml" ] \
        || [ ! -f "$tmp_dir/installer.sh" ] \
        || [ ! -x "$tmp_dir/scripts/install/install-with-uv.sh" ]; then
        error "Verified release asset is missing required installer files."
        exit 1
    fi
    info "Bootstrap asset download, SHA256, extraction, and layout check passed."
    exit 0
fi

source_parent="$(dirname "$SOURCE_DIR")"
mkdir -p "$source_parent"
stage_dir="$(mktemp -d "$source_parent/source.XXXXXX")"
cp -a "$tmp_dir/." "$stage_dir/"
if [ -e "$SOURCE_DIR" ]; then
    backup_dir="${SOURCE_DIR}.previous.$$"
    mv "$SOURCE_DIR" "$backup_dir"
fi
mv "$stage_dir" "$SOURCE_DIR"
trap - EXIT
rm -rf "$tmp_dir"
if [ -n "$backup_dir" ]; then
    warn "Previous source preserved at $backup_dir"
fi

UV_INSTALLER="$SOURCE_DIR/scripts/install/install-with-uv.sh"
if [ ! -x "$UV_INSTALLER" ]; then
    error "Downloaded source is missing scripts/install/install-with-uv.sh"
    exit 1
fi

exec "$UV_INSTALLER" "$@"
