#!/usr/bin/env bash
# User-local installer for Linux Speech Tools.
#
# uv owns Python dependencies. This shell script owns system checks, selected
# install profiles, launcher installation, runtime config, optional model
# downloads, and optional desktop/uinput setup.

set -euo pipefail

DRY_RUN=false
WITH_KOKORO=false
WITH_STT=false
WITH_GNOME=false
SETUP_UINPUT=false
DOWNLOAD_MODELS=false
WITH_DEV=false
INSTALL_ALL=false
INSTALL_SYSTEM_DEPS=false
CHECK_SYSTEM_DEPS=false
NO_SYSTEM_DEPS=false
NO_PATH_EDIT=false
NONINTERACTIVE=false
WHISPER_MODEL="${WHISPER_MODEL:-tiny}"
WHISPER_DEVICE="${WHISPER_DEVICE:-cpu}"
WHISPER_COMPUTE_TYPE="${WHISPER_COMPUTE_TYPE:-int8}"
ASR_LANG="${ASR_LANG:-en}"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

usage() {
    cat <<'EOF'
Usage: installer.sh [install|verify] [options]

Options:
  --dry-run          Show actions without changing the system
  --with-kokoro      Install offline Kokoro/read-aloud Python deps
  --with-stt         Install faster-whisper dictation Python deps
  --with-gnome       Include GNOME integration helpers/checks
  --install-system-deps
                     Install system packages with sudo
  --check-system-deps
                     Check required system packages without installing
  --no-system-deps   Skip system dependency checks and installation
  --no-path-edit     Do not append ~/.local/bin to ~/.bashrc
  --noninteractive   Do not prompt; fail instead of waiting for input
  --setup-uinput     Configure /dev/uinput permissions for direct typing
  --download-models  Download/check selected model assets after uv sync
  --whisper-model M  faster-whisper model to prefetch and use at runtime
  --all              Enable kokoro, stt, and gnome profiles
  --dev              Include development dependency group
  -h, --help         Show this help

Examples:
  ./installer.sh --with-kokoro --download-models
  ./installer.sh --with-stt --download-models --whisper-model base
  ./installer.sh --all --download-models

uv bootstrap:
  If uv is missing, this installer downloads a pinned, versioned Astral
  installer and verifies its SHA256 before running it. Custom installer URLs
  require both LST_UV_INSTALLER_URL and LST_UV_INSTALLER_SHA256.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        install|verify)
            COMMAND="$1"
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --with-kokoro)
            WITH_KOKORO=true
            shift
            ;;
        --with-stt)
            WITH_STT=true
            shift
            ;;
        --with-gnome)
            WITH_GNOME=true
            shift
            ;;
        --install-system-deps)
            INSTALL_SYSTEM_DEPS=true
            shift
            ;;
        --check-system-deps)
            CHECK_SYSTEM_DEPS=true
            shift
            ;;
        --no-system-deps)
            NO_SYSTEM_DEPS=true
            shift
            ;;
        --no-path-edit)
            NO_PATH_EDIT=true
            shift
            ;;
        --noninteractive)
            NONINTERACTIVE=true
            shift
            ;;
        --setup-uinput)
            SETUP_UINPUT=true
            WITH_STT=true
            shift
            ;;
        --download-models)
            DOWNLOAD_MODELS=true
            shift
            ;;
        --whisper-model)
            WHISPER_MODEL="${2:?missing model name}"
            shift 2
            ;;
        --all)
            INSTALL_ALL=true
            WITH_KOKORO=true
            WITH_STT=true
            WITH_GNOME=true
            shift
            ;;
        --dev)
            WITH_DEV=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}[ERROR]${NC} Unknown option: $1" >&2
            usage
            exit 2
            ;;
    esac
done

COMMAND="${COMMAND:-install}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_DIR="$HOME/.local/bin"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/linux-speech-tools"
CONFIG_FILE="$CONFIG_DIR/install.env"

# A checkout normally keeps its uv environment beside pyproject.toml. Native
# packages install the project under read-only /usr/share, so their ordinary
# user setup must put the environment in user-owned data instead. uv honors
# UV_PROJECT_ENVIRONMENT for both sync and run; persist the exact absolute path
# below so installed launchers use the same environment on later invocations.
if [ -z "${UV_PROJECT_ENVIRONMENT:-}" ] && [ ! -w "$PROJECT_ROOT" ]; then
    RUNTIME_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
    case "$RUNTIME_DATA_HOME" in
        /*) ;;
        *) RUNTIME_DATA_HOME="$HOME/.local/share" ;;
    esac
    UV_PROJECT_ENVIRONMENT="$RUNTIME_DATA_HOME/linux-speech-tools/uv-runtime"
    export UV_PROJECT_ENVIRONMENT
fi

print_header() {
    echo -e "${BLUE}"
    echo "Linux Speech Tools Installer"
    echo "uv dependencies + shell system setup"
    echo -e "${NC}"
}

step() {
    if [ "$DRY_RUN" = true ]; then
        echo -e "${GREEN}[DRY-RUN STEP]${NC} $1"
    else
        echo -e "${GREEN}[STEP]${NC} $1"
    fi
}

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

run_or_print() {
    if [ "$DRY_RUN" = true ]; then
        printf '%b[DRY-RUN]%b' "$YELLOW" "$NC"
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

install_uv() {
    # Astral documents versioned installer URLs for reproducible installs. Keep
    # this immutable URL and its verified hash in sync when updating uv.
    local default_uv_installer_url="https://astral.sh/uv/0.11.32/install.sh"
    local default_uv_installer_sha256="43aff33a967fe40e8c17949d8c85c65bc43f3b5c94742393c957f56ab5ba80f4"
    local uv_installer_url="${LST_UV_INSTALLER_URL:-$default_uv_installer_url}"
    local uv_installer_sha256="${LST_UV_INSTALLER_SHA256:-}"

    if command -v uv >/dev/null 2>&1; then
        info "uv found: $(uv --version)"
        return 0
    fi

    step "Installing uv"
    local tmp_installer
    if [ -z "$uv_installer_sha256" ]; then
        if [ "$uv_installer_url" = "$default_uv_installer_url" ]; then
            uv_installer_sha256="$default_uv_installer_sha256"
        else
            error "LST_UV_INSTALLER_SHA256 is required for custom uv installer URLs."
            exit 1
        fi
    fi
    if [ "$DRY_RUN" = true ]; then
        info "Would download $uv_installer_url"
        info "Would verify uv installer SHA256 $uv_installer_sha256 and run it"
        return 0
    fi
    tmp_installer="$(mktemp)"
    trap 'rm -f "$tmp_installer"' RETURN
    if ! curl -LsSf "$uv_installer_url" -o "$tmp_installer"; then
        error "Failed to download the uv installer from $uv_installer_url"
        error "Install uv manually (https://docs.astral.sh/uv/) and re-run, or set"
        error "LST_UV_INSTALLER_URL to a reachable versioned installer."
        exit 1
    fi
    if ! command -v sha256sum >/dev/null 2>&1; then
        error "sha256sum is required to verify the uv installer."
        exit 1
    fi
    local actual_sha256
    actual_sha256="$(sha256sum "$tmp_installer" | awk '{print $1}')"
    if [ "$actual_sha256" != "$uv_installer_sha256" ]; then
        error "uv installer checksum mismatch: got $actual_sha256, expected $uv_installer_sha256"
        if [ "$uv_installer_url" = "$default_uv_installer_url" ]; then
            warn "The pinned uv installer failed integrity verification and will not be run."
            warn "To recover, do one of the following:"
            warn "  1. Install uv yourself: https://docs.astral.sh/uv/ (then re-run this installer)."
            warn "  2. Choose another official versioned installer and verify its hash:"
            warn "       LST_UV_INSTALLER_URL=https://astral.sh/uv/<version>/install.sh \\"
            warn "       LST_UV_INSTALLER_SHA256=<sha256> $0 ..."
        fi
        exit 1
    fi
    sh "$tmp_installer"
    export PATH="$HOME/.local/bin:$PATH"
}

system_packages_for_profile() {
    local manager="${1:-generic}"
    local packages=()

    case "$manager" in
        apt)
            packages=(python3 python3-dev ffmpeg espeak-ng curl git tar)
            if [ "$WITH_STT" = true ]; then
                packages+=(build-essential wl-clipboard xclip)
            fi
            if [ "$SETUP_UINPUT" = true ]; then
                packages+=(ydotool xdotool)
            fi
            if [ "$WITH_GNOME" = true ]; then
                packages+=(libnotify-bin dbus-bin python3-dbus python3-gi gir1.2-glib-2.0)
            fi
            ;;
        dnf)
            packages=(python3 python3-devel ffmpeg-free espeak-ng curl git tar)
            if [ "$WITH_STT" = true ]; then
                packages+=(gcc wl-clipboard xclip)
            fi
            if [ "$SETUP_UINPUT" = true ]; then
                packages+=(ydotool xdotool)
            fi
            if [ "$WITH_GNOME" = true ]; then
                packages+=(libnotify dbus-tools python3-dbus python3-gobject)
            fi
            ;;
        pacman)
            packages=(python ffmpeg espeak-ng curl git tar)
            if [ "$WITH_STT" = true ]; then
                packages+=(base-devel wl-clipboard xclip)
            fi
            if [ "$SETUP_UINPUT" = true ]; then
                packages+=(ydotool xdotool)
            fi
            if [ "$WITH_GNOME" = true ]; then
                packages+=(libnotify dbus python-dbus python-gobject)
            fi
            ;;
        *)
            packages=(python3 python3-dev ffmpeg espeak-ng curl git tar)
            if [ "$WITH_STT" = true ]; then
                packages+=(build-essential wl-clipboard xclip)
            fi
            if [ "$SETUP_UINPUT" = true ]; then
                packages+=(ydotool xdotool)
            fi
            if [ "$WITH_GNOME" = true ]; then
                packages+=(libnotify-bin dbus-bin python3-dbus python3-gi gir1.2-glib-2.0)
            fi
            ;;
    esac

    printf '%s\n' "${packages[@]}"
}

install_system_deps() {
    step "Installing system dependencies"
    local packages=()

    if command -v apt >/dev/null 2>&1; then
        mapfile -t packages < <(system_packages_for_profile apt)
        run_or_print sudo apt update
        run_or_print sudo apt install -y "${packages[@]}"
    elif command -v dnf >/dev/null 2>&1; then
        mapfile -t packages < <(system_packages_for_profile dnf)
        run_or_print sudo dnf install -y "${packages[@]}"
    elif command -v pacman >/dev/null 2>&1; then
        mapfile -t packages < <(system_packages_for_profile pacman)
        run_or_print sudo pacman -S --needed "${packages[@]}"
    else
        mapfile -t packages < <(system_packages_for_profile generic)
        warn "Unknown package manager. Install manually: ${packages[*]}"
    fi
}

check_system_deps() {
    step "Checking system dependencies"
    local packages=()
    local missing=()
    local manager="generic"

    if command -v apt >/dev/null 2>&1; then
        manager="apt"
    elif command -v dnf >/dev/null 2>&1; then
        manager="dnf"
    elif command -v pacman >/dev/null 2>&1; then
        manager="pacman"
    fi

    mapfile -t packages < <(system_packages_for_profile "$manager")
    for pkg in "${packages[@]}"; do
        case "$pkg" in
            python3|python3-dev|python3-devel|python|python-devel)
                command -v python3 >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            ffmpeg|ffmpeg-free)
                command -v ffmpeg >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            espeak-ng)
                command -v espeak-ng >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            curl)
                command -v curl >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            git)
                command -v git >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            tar)
                command -v tar >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            build-essential|base-devel|gcc)
                command -v gcc >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            wl-clipboard)
                command -v wl-copy >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            xclip)
                command -v xclip >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            ydotool)
                command -v ydotool >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            xdotool)
                command -v xdotool >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            libnotify-bin|libnotify)
                command -v notify-send >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            dbus-bin|dbus-tools|dbus)
                command -v dbus-send >/dev/null 2>&1 || missing+=("$pkg")
                ;;
            *)
                ;;
        esac
    done

    if [ ${#missing[@]} -eq 0 ]; then
        info "System dependency commands look available"
    else
        warn "Missing or unverified system dependencies: ${missing[*]}"
        warn "Run with --install-system-deps to install via sudo, or install them manually."
    fi
}

uv_sync_args() {
    local args=(sync --locked)

    if [ "$WITH_DEV" != true ]; then
        args+=(--no-dev)
    fi

    if [ "$INSTALL_ALL" = true ]; then
        args+=(--extra all)
    else
        if [ "$WITH_KOKORO" = true ]; then
            args+=(--extra kokoro --extra read)
        fi
        if [ "$WITH_STT" = true ]; then
            args+=(--extra stt)
        fi
        if [ "$WITH_GNOME" = true ]; then
            args+=(--extra gnome)
        fi
    fi

    printf '%s\n' "${args[@]}"
}

install_python_deps() {
    step "Installing Python dependencies with uv"
    mapfile -t args < <(uv_sync_args)

    if [ "$DRY_RUN" = true ]; then
        info "Would cd to $PROJECT_ROOT"
        printf '%b[DRY-RUN]%b uv' "$YELLOW" "$NC"
        printf ' %q' "${args[@]}"
        printf '\n'
        return 0
    fi

    cd "$PROJECT_ROOT"
    uv "${args[@]}"
}

install_launchers() {
    step "Installing command launchers"
    local launchers=(
        linux-speech-tools-env
        say
        say-local
        say-read
        say-read-es
        say-read-continuous
        say-read-mvp
        say-read-gnome
        talk2claude
        talk2claude-faster
        talk2claude-faster-toggle
        lst-dictate
        lst-agent
        lst-asr-corpus
        lst-gnome-acceptance
        lst-ibus-check
        lst-insertion-metrics
        dictate-prompt
        gnome-dictation
        linux-speech-tools-setup
    )

    if [ "$DRY_RUN" = true ]; then
        info "Would create $INSTALL_DIR"
    else
        mkdir -p "$INSTALL_DIR"
    fi

    for name in "${launchers[@]}"; do
        local exe="$PROJECT_ROOT/bin/$name"
        [ -f "$exe" ] || continue
        if [ "$DRY_RUN" != true ]; then
            rm -f "$INSTALL_DIR/$name"
        fi
        run_or_print cp "$exe" "$INSTALL_DIR/"
    done

    if [ "$NO_PATH_EDIT" != true ] && [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        if [ "$DRY_RUN" = true ]; then
            info "Would add ~/.local/bin to PATH in ~/.bashrc"
        else
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
            warn "Added ~/.local/bin to PATH in ~/.bashrc"
        fi
    fi
}

write_runtime_config() {
    step "Writing runtime configuration"

    if [ "$DRY_RUN" = true ]; then
        info "Would write $CONFIG_FILE with mode 0600 and LST_PROJECT_ROOT=$PROJECT_ROOT"
        if [ -n "${UV_PROJECT_ENVIRONMENT:-}" ]; then
            info "Would set UV_PROJECT_ENVIRONMENT=$UV_PROJECT_ENVIRONMENT"
        fi
        return 0
    fi

    mkdir -p "$CONFIG_DIR"
    chmod 700 "$CONFIG_DIR" 2>/dev/null || true
    install -m 600 /dev/null "$CONFIG_FILE"
    {
        printf 'LST_PROJECT_ROOT=%s\n' "$PROJECT_ROOT"
        printf 'LST_INSTALL_DIR=%s\n' "$INSTALL_DIR"
        printf 'WHISPER_MODEL=%s\n' "$WHISPER_MODEL"
        printf 'WHISPER_DEVICE=%s\n' "$WHISPER_DEVICE"
        printf 'WHISPER_COMPUTE_TYPE=%s\n' "$WHISPER_COMPUTE_TYPE"
        printf 'ASR_LANG=%s\n' "$ASR_LANG"
        if [ -n "${UV_PROJECT_ENVIRONMENT:-}" ]; then
            printf 'UV_PROJECT_ENVIRONMENT=%s\n' "$UV_PROJECT_ENVIRONMENT"
        fi
    } > "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
    info "Wrote $CONFIG_FILE"
}

setup_models() {
    if [ "$DOWNLOAD_MODELS" != true ]; then
        return 0
    fi

    step "Installing selected model assets"
    local model_args=()
    if [ "$WITH_KOKORO" = true ]; then
        model_args+=(--kokoro)
    fi
    if [ "$WITH_STT" = true ]; then
        model_args+=(
            --stt
            --whisper-model "$WHISPER_MODEL"
            --whisper-device "$WHISPER_DEVICE"
            --whisper-compute-type "$WHISPER_COMPUTE_TYPE"
        )
    fi

    if [ ${#model_args[@]} -eq 0 ]; then
        warn "--download-models was requested but no model profile was selected"
        return 0
    fi

    if [ "$DRY_RUN" = true ]; then
        model_args+=(--dry-run)
    fi

    local uv_args=(run --locked)
    if [ "$WITH_STT" = true ]; then
        uv_args+=(--extra stt)
    fi

    cd "$PROJECT_ROOT"
    run_or_print uv "${uv_args[@]}" python -m src.utils.setup_models "${model_args[@]}"
}

setup_uinput() {
    if [ "$SETUP_UINPUT" != true ]; then
        return 0
    fi

    step "Configuring uinput permissions"
    warn "Direct typing grants broad input injection through /dev/uinput."
    if [ "$NONINTERACTIVE" = true ]; then
        error "--setup-uinput requires interactive sudo/user confirmation; rerun without --noninteractive."
        exit 1
    fi
    run_or_print sudo "$PROJECT_ROOT/scripts/setup/setup-uinput-permissions.sh"
    warn "uinput group changes require logout/login before direct typing works"
}

verify_installation() {
    step "Verifying installation"

    if [ "$DRY_RUN" = true ]; then
        info "Would verify uv environment, launchers, and runtime config"
        return 0
    fi

    cd "$PROJECT_ROOT"
    mapfile -t args < <(uv_sync_args)
    args+=(--check)
    if uv "${args[@]}" >/dev/null 2>&1; then
        info "uv environment is synchronized"
    else
        warn "uv environment is not fully synchronized for the current profile"
    fi

    for exe in say say-read linux-speech-tools-setup; do
        if command -v "$exe" >/dev/null 2>&1; then
            info "found command: $exe"
        else
            warn "command not on PATH yet: $exe"
        fi
    done

    if [ -f "$CONFIG_FILE" ]; then
        info "runtime config exists: $CONFIG_FILE"
    else
        warn "runtime config missing: $CONFIG_FILE"
    fi
}

main() {
    print_header

    if [ "$EUID" -eq 0 ] && [ "$DRY_RUN" != true ]; then
        error "Do not run this installer as root. It will use sudo when needed."
        exit 1
    fi

    if [ "$NO_SYSTEM_DEPS" = true ]; then
        info "Skipping system dependency checks"
    elif [ "$INSTALL_SYSTEM_DEPS" = true ]; then
        install_system_deps
    else
        check_system_deps
    fi
    install_uv
    install_python_deps
    install_launchers
    write_runtime_config
    setup_models
    setup_uinput
    verify_installation

    if [ "$DRY_RUN" = true ]; then
        info "Dry run complete"
    else
        info "Installation complete"
        info "Model checks: linux-speech-tools-setup --check"
    fi
}

case "$COMMAND" in
    install) main ;;
    verify) verify_installation ;;
    *)
        error "Unknown command: $COMMAND"
        usage
        exit 2
        ;;
esac
