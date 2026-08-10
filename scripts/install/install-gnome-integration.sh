#!/usr/bin/env bash
# GNOME Speech-to-Clipboard Integration Installer
# Provides multiple installation options for different levels of integration

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_DIR="$HOME/.local/bin"
EXTENSION_DIR="$HOME/.local/share/gnome-shell/extensions/speech-to-clipboard@linux-speech-tools"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/linux-speech-tools"
CONFIG_FILE="$CONFIG_DIR/install.env"
DICTATION_BINDING_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/"
PROMPT_DICTATION_BINDING_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/developer-prompt-dictation/"
LEGACY_DICTATION_BINDING_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/faster-dictation/"
MEDIA_KEYS_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
ACTION=""
NONINTERACTIVE=false
DRY_RUN=false

usage() {
    cat <<EOF
usage: install-gnome-integration.sh [--basic|--extension|--both|--test|--uninstall] [--noninteractive] [--dry-run]

Install or manage GNOME speech integration.

Options:
  --basic           Install keyboard shortcut and notification helpers
  --extension       Install the GNOME focus provider and experimental panel UI
  --both            Install hotkeys, focus provider, and experimental panel UI
  --test            Test current installation
  --uninstall       Remove GNOME integration
  --noninteractive  Require an explicit action flag; do not prompt
  --dry-run         Show actions without changing files or GNOME settings
  -h, --help        Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --basic) ACTION="basic"; shift ;;
        --extension) ACTION="extension"; shift ;;
        --both) ACTION="both"; shift ;;
        --test) ACTION="test"; shift ;;
        --uninstall) ACTION="uninstall"; shift ;;
        --noninteractive) NONINTERACTIVE=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *)
            echo -e "${RED}[ERROR]${NC} Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

print_header() {
    echo -e "${BLUE}"
    echo "╭────────────────────────────────────────────────────────────╮"
    echo "│              GNOME Speech Integration Installer             │"
    echo "│                    Linux Speech Tools                      │"
    echo "╰────────────────────────────────────────────────────────────╯"
    echo -e "${NC}"
}

print_step() {
    echo -e "${GREEN}[STEP]${NC} $1"
}

print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

run_or_print() {
    if [ "$DRY_RUN" = true ]; then
        printf 'dry-run: would'
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

gsettings_set() {
    run_or_print gsettings set "$@"
}

gsettings_reset() {
    if [ "$DRY_RUN" = true ]; then
        run_or_print gsettings reset "$@"
        return 0
    fi
    gsettings reset "$@"
}

check_dependencies() {
    print_step "Checking dependencies..."

    local missing_deps=()

    # Check for required tools
    for cmd in gsettings notify-send; do
        if ! command -v "$cmd" &> /dev/null; then
            missing_deps+=("$cmd")
        fi
    done

    if ! command -v talk2claude-faster &> /dev/null && [ ! -x "$REPO_ROOT/bin/talk2claude-faster" ]; then
        missing_deps+=("talk2claude-faster")
    fi

    if [ ${#missing_deps[@]} -ne 0 ]; then
        if [ "$DRY_RUN" = true ]; then
            print_warning "Missing dependencies for a real install:"
            for dep in "${missing_deps[@]}"; do
                echo "  - $dep"
            done
            print_warning "Continuing because --dry-run was requested"
            return 0
        fi

        print_error "Missing dependencies:"
        for dep in "${missing_deps[@]}"; do
            echo "  - $dep"
        done
        echo ""
        echo "Please install the main speech-tools first:"
        echo "  ./installer.sh --with-stt --with-gnome"
        exit 1
    fi

    print_info "✓ All dependencies found"
}

current_keybinding_list() {
    if [ "$DRY_RUN" = true ]; then
        echo "[]"
        return 0
    fi
    gsettings get "$MEDIA_KEYS_SCHEMA" custom-keybindings 2>/dev/null || echo "[]"
}

update_keybinding_list() {
    local action="$1"
    local path="$2"
    local current
    current="$(current_keybinding_list)"
    python3 - "$current" "$path" "$action" <<'PY'
import ast
import sys

current, path, action = sys.argv[1:]
current = current.replace("@as ", "")
try:
    values = ast.literal_eval(current)
except Exception:
    values = []
if not isinstance(values, list):
    values = []

if action == "add" and path not in values:
    values.append(path)
elif action == "remove":
    values = [value for value in values if value != path]

print("[" + ", ".join(repr(value) for value in values) + "]")
PY
}

remove_keybinding_paths_from_list() {
    local current
    current="$(current_keybinding_list)"
    python3 - "$current" "$@" <<'PY'
import ast
import sys

current = sys.argv[1].replace("@as ", "")
paths = set(sys.argv[2:])
try:
    values = ast.literal_eval(current)
except Exception:
    values = []
if not isinstance(values, list):
    values = []

values = [value for value in values if value not in paths]
print("[" + ", ".join(repr(value) for value in values) + "]")
PY
}

write_runtime_config() {
    if [ "$DRY_RUN" = true ]; then
        print_info "dry-run: would update $CONFIG_FILE while preserving existing keys"
        print_info "dry-run: would set LST_PROJECT_ROOT=$REPO_ROOT"
        print_info "dry-run: would set LST_INSTALL_DIR=$INSTALL_DIR"
        return 0
    fi

    if [ -L "$CONFIG_FILE" ]; then
        print_error "Refusing to write symlinked config: $CONFIG_FILE"
        return 1
    fi

    mkdir -p "$CONFIG_DIR"
    chmod 700 "$CONFIG_DIR" 2>/dev/null || true

    local tmp
    tmp="$(mktemp "$CONFIG_DIR/install.env.XXXXXX")"
    chmod 600 "$tmp"
    if [ -f "$CONFIG_FILE" ]; then
        grep -Ev '^(LST_PROJECT_ROOT|LST_INSTALL_DIR)=' "$CONFIG_FILE" > "$tmp" || true
    fi
    {
        printf 'LST_PROJECT_ROOT=%s\n' "$REPO_ROOT"
        printf 'LST_INSTALL_DIR=%s\n' "$INSTALL_DIR"
    } >> "$tmp"
    mv "$tmp" "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
    print_info "✓ Runtime config written to $CONFIG_FILE"
}

install_basic_integration() {
    print_step "Installing basic GNOME integration..."

    # Copy all speech tools
    local installed_paths=()
    local name
    run_or_print mkdir -p "$INSTALL_DIR"
    for name in gnome-dictation linux-speech-tools-env talk2claude-faster talk2claude-faster-toggle lst-dictate dictate-prompt linux-speech-tools-setup; do
        if [ "$DRY_RUN" != true ]; then
            rm -f "$INSTALL_DIR/$name"
        fi
        run_or_print cp "$REPO_ROOT/bin/$name" "$INSTALL_DIR/"
        installed_paths+=("$INSTALL_DIR/$name")
    done
    run_or_print cp "$REPO_ROOT/scripts/setup/setup-faster-hotkey.sh" "$INSTALL_DIR/"
    installed_paths+=("$INSTALL_DIR/setup-faster-hotkey.sh")
    run_or_print chmod +x "${installed_paths[@]}"
    write_runtime_config

    print_info "✓ Speech integration scripts installed to $INSTALL_DIR"

    # Setup keyboard shortcut with toggle mode as default
    print_info "Setting up keyboard shortcut (toggle mode)..."

    gsettings_set "$MEDIA_KEYS_SCHEMA" custom-keybindings "$(update_keybinding_list add "$DICTATION_BINDING_PATH")"
    gsettings_set "$MEDIA_KEYS_SCHEMA.custom-keybinding:$DICTATION_BINDING_PATH" name "Speech Dictation (Toggle)"
    gsettings_set "$MEDIA_KEYS_SCHEMA.custom-keybinding:$DICTATION_BINDING_PATH" command "$INSTALL_DIR/talk2claude-faster-toggle"
    gsettings_set "$MEDIA_KEYS_SCHEMA.custom-keybinding:$DICTATION_BINDING_PATH" binding "<Control><Alt>v"

    print_info "Setting up developer prompt dictation shortcut..."
    gsettings_set "$MEDIA_KEYS_SCHEMA" custom-keybindings "$(update_keybinding_list add "$PROMPT_DICTATION_BINDING_PATH")"
    gsettings_set "$MEDIA_KEYS_SCHEMA.custom-keybinding:$PROMPT_DICTATION_BINDING_PATH" name "Developer Prompt Dictation (Live)"
    gsettings_set "$MEDIA_KEYS_SCHEMA.custom-keybinding:$PROMPT_DICTATION_BINDING_PATH" command "$INSTALL_DIR/lst-dictate toggle"
    gsettings_set "$MEDIA_KEYS_SCHEMA.custom-keybinding:$PROMPT_DICTATION_BINDING_PATH" binding "<Control><Alt>space"

    print_info "✓ Basic integration complete!"
    echo ""
    echo "🎤 Toggle Mode Usage (Default):"
    echo "  Ctrl+Alt+V (1st press) - Start recording 🔴"
    echo "  Ctrl+Alt+V (2nd press) - Stop & transcribe ⏹️"
    echo "  Ctrl+Alt+Space       - Live developer prompt dictation"
    echo ""
    echo "📋 Management Commands:"
    echo "  setup-faster-hotkey.sh     - Change hotkey"
    echo "  talk2claude-faster --check - Check dictation capabilities"
    echo "  lst-dictate --check        - Check live prompt dictation"
}

install_extension() {
    print_step "Installing GNOME focus provider and experimental panel UI..."
    print_warning "The panel/menu UI remains experimental. The bundled read-only focus provider lets lst-dictate verify a stable GNOME Wayland target."

    # Check if extensions are supported
    if ! command -v gnome-extensions &> /dev/null; then
        if [ "$DRY_RUN" = true ]; then
            print_warning "gnome-extensions command not found; continuing because --dry-run was requested"
        else
            print_error "gnome-extensions command not found. Install with:"
            print_error "  sudo apt install gnome-shell-extension-prefs"
            return 1
        fi
    fi

    # Create extension directory
    run_or_print mkdir -p "$EXTENSION_DIR"

    # Copy extension files
    run_or_print cp "$REPO_ROOT/gnome-extension/metadata.json" "$EXTENSION_DIR/"
    run_or_print cp "$REPO_ROOT/gnome-extension/"*.js "$EXTENSION_DIR/"

    print_info "✓ Extension files copied to $EXTENSION_DIR"

    # Enable extension
    if [ "$DRY_RUN" = true ]; then
        run_or_print gnome-extensions enable speech-to-clipboard@linux-speech-tools
    elif gnome-extensions enable speech-to-clipboard@linux-speech-tools 2>/dev/null; then
        print_info "✓ Extension enabled"
    else
        print_warning "Extension files were copied, but GNOME did not enable the extension."
        print_warning "Use 'gnome-extensions enable speech-to-clipboard@linux-speech-tools' in a live GNOME session to retry."
    fi

    print_info "✓ Focus provider and experimental extension install step complete"
    echo ""
    echo "Notes:"
    echo "  - Use --both when you want the supported hotkeys plus stable Wayland target checks."
    echo "  - The panel/menu UI is experimental; the canonical hotkey still uses talk2claude-faster-toggle."
    echo "  - The focus service is read-only and available only on the user session bus."
    echo "  - Verify it after Shell reload with:"
    echo "      gdbus call --session --dest org.linux_speech_tools.Focus \\"
    echo "        --object-path /org/linux_speech_tools/Focus \\"
    echo "        --method org.linux_speech_tools.Focus.GetFocus"
    echo ""
    print_warning "You may need to restart GNOME Shell (Alt+F2, type 'r', press Enter)"
    print_warning "or log out and back in for the extension to activate."
}

show_menu() {
    echo ""
    echo "Choose installation type:"
    echo ""
    echo "1) Basic Integration (Recommended)"
    echo "   └─ Keyboard shortcut + enhanced notifications"
    echo ""
    echo "2) GNOME Focus Provider + Panel UI"
    echo "   └─ Stable Wayland target identity; panel/menu UI remains experimental"
    echo ""
    echo "3) Both"
    echo "   └─ Recommended when testing safe live typing on GNOME Wayland"
    echo ""
    echo "4) Test Current Installation"
    echo ""
    echo "5) Uninstall"
    echo ""
}

test_installation() {
    print_step "Testing installation..."

    if [ -x "$INSTALL_DIR/gnome-dictation" ]; then
        print_info "✓ gnome-dictation found"
        "$INSTALL_DIR/gnome-dictation" status
    else
        print_error "✗ gnome-dictation not found"
    fi

    if [ -d "$EXTENSION_DIR" ]; then
        print_info "✓ GNOME extension installed"
        if gnome-extensions list --enabled 2>/dev/null | \
                grep -Fxq "speech-to-clipboard@linux-speech-tools"; then
            print_info "✓ Extension is enabled"
        elif gnome-extensions list --disabled 2>/dev/null | \
                grep -Fxq "speech-to-clipboard@linux-speech-tools"; then
            print_warning "! GNOME extension is installed but disabled"
        else
            print_warning "! Extension exists on disk but GNOME Shell has not registered it"
        fi
    else
        print_info "- GNOME extension not installed"
    fi
}

uninstall() {
    print_step "Uninstalling GNOME integration..."

    # Remove basic integration
    if [ -f "$INSTALL_DIR/gnome-dictation" ]; then
        run_or_print rm -f "$INSTALL_DIR/gnome-dictation"
        print_info "✓ Removed gnome-dictation script"
    fi

    # Remove current and legacy keybindings created by this project.
    gsettings_set "$MEDIA_KEYS_SCHEMA" custom-keybindings "$(remove_keybinding_paths_from_list "$DICTATION_BINDING_PATH" "$PROMPT_DICTATION_BINDING_PATH" "$LEGACY_DICTATION_BINDING_PATH")" 2>/dev/null || true
    for path in "$DICTATION_BINDING_PATH" "$PROMPT_DICTATION_BINDING_PATH" "$LEGACY_DICTATION_BINDING_PATH"; do
        gsettings_reset "$MEDIA_KEYS_SCHEMA.custom-keybinding:$path" name 2>/dev/null || true
        gsettings_reset "$MEDIA_KEYS_SCHEMA.custom-keybinding:$path" command 2>/dev/null || true
        gsettings_reset "$MEDIA_KEYS_SCHEMA.custom-keybinding:$path" binding 2>/dev/null || true
    done
    print_info "✓ Removed speech dictation keybinding"

    # Remove extension
    if [ -d "$EXTENSION_DIR" ]; then
        if [ "$DRY_RUN" = true ]; then
            run_or_print gnome-extensions disable speech-to-clipboard@linux-speech-tools
        else
            gnome-extensions disable speech-to-clipboard@linux-speech-tools 2>/dev/null || true
        fi
        run_or_print rm -rf "$EXTENSION_DIR"
        print_info "✓ Removed GNOME extension"
    fi

    print_info "✓ Uninstallation complete"
}

main() {
    print_header

    check_dependencies

    if [ -z "$ACTION" ]; then
        if [ "$NONINTERACTIVE" = true ]; then
            print_error "--noninteractive requires one of --basic, --extension, --both, --test, or --uninstall"
            exit 2
        fi
        show_menu
        read -p "Enter your choice (1-5): " choice
    else
        case "$ACTION" in
            basic) choice=1 ;;
            extension) choice=2 ;;
            both) choice=3 ;;
            test) choice=4 ;;
            uninstall) choice=5 ;;
            *) print_error "Unknown action: $ACTION"; exit 2 ;;
        esac
    fi

    case $choice in
        1)
            install_basic_integration
            ;;
        2)
            install_extension
            ;;
        3)
            install_basic_integration
            install_extension
            ;;
        4)
            test_installation
            ;;
        5)
            uninstall
            ;;
        *)
            print_error "Invalid choice. Please run the script again."
            exit 1
            ;;
    esac

    echo ""
    if [ "$DRY_RUN" = true ]; then
        print_info "Dry run complete"
    else
        print_info "Installation complete! 🎉"
    fi
    echo ""
    echo "Next steps:"
    echo "1. Try the hotkeys: Ctrl+Alt+V or Ctrl+Alt+Space"
    echo "2. Check system notifications for feedback"
    echo "3. Use 'gnome-dictation status' or 'lst-dictate status' to check recording state"
    echo ""
    echo "For help: gnome-dictation help"
}

main "$@"
