#!/bin/bash
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

check_dependencies() {
    print_step "Checking dependencies..."

    local missing_deps=()

    # Check for required tools
    for cmd in talk2claude gnome-shell gsettings notify-send; do
        if ! command -v "$cmd" &> /dev/null; then
            missing_deps+=("$cmd")
        fi
    done

    # Check for STT environment
    if [ ! -d "$HOME/.venvs/stt" ]; then
        missing_deps+=("STT Python environment")
    fi

    if [ ${#missing_deps[@]} -ne 0 ]; then
        print_error "Missing dependencies:"
        for dep in "${missing_deps[@]}"; do
            echo "  - $dep"
        done
        echo ""
        echo "Please install the main speech-tools first:"
        echo "  curl -fsSL https://raw.githubusercontent.com/pablopda/linux-speech-tools/main/installer.sh | bash"
        exit 1
    fi

    print_info "✓ All dependencies found"
}

install_basic_integration() {
    print_step "Installing basic GNOME integration..."

    # Copy all speech tools
    mkdir -p "$INSTALL_DIR"
    cp "$REPO_ROOT/bin/gnome-dictation" "$INSTALL_DIR/"
    cp "$REPO_ROOT/scripts/toggle-speech.sh" "$INSTALL_DIR/"
    cp "$REPO_ROOT/scripts/simple-speech.sh" "$INSTALL_DIR/"
    cp "$REPO_ROOT/scripts/setup/choose-recording-mode.sh" "$INSTALL_DIR/"
    cp "$REPO_ROOT/scripts/setup/setup-hotkey.sh" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR"/{gnome-dictation,toggle-speech.sh,simple-speech.sh,choose-recording-mode.sh,setup-hotkey.sh}

    print_info "✓ Speech integration scripts installed to $INSTALL_DIR"

    # Setup keyboard shortcut with toggle mode as default
    print_info "Setting up keyboard shortcut (toggle mode)..."

    gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/']"
    gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/ name "Speech Dictation (Toggle)"
    gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/ command "$INSTALL_DIR/toggle-speech.sh toggle"
    gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/ binding "<Ctrl><Alt>v"

    print_info "✓ Basic integration complete!"
    echo ""
    echo "🎤 Toggle Mode Usage (Default):"
    echo "  Ctrl+Alt+V (1st press) - Start recording 🔴"
    echo "  Ctrl+Alt+V (2nd press) - Stop & transcribe ⏹️"
    echo ""
    echo "📋 Management Commands:"
    echo "  choose-recording-mode.sh  - Switch between toggle/fixed modes"
    echo "  setup-hotkey.sh          - Change hotkey"
    echo "  toggle-speech.sh status  - Check recording status"
}

install_extension() {
    print_step "Installing GNOME Shell extension..."

    # Check if extensions are supported
    if ! command -v gnome-extensions &> /dev/null; then
        print_error "gnome-extensions command not found. Install with:"
        print_error "  sudo apt install gnome-shell-extension-prefs"
        return 1
    fi

    # Create extension directory
    mkdir -p "$EXTENSION_DIR"

    # Copy extension files
    cp "$REPO_ROOT/gnome-extension/metadata.json" "$EXTENSION_DIR/"
    cp "$REPO_ROOT/gnome-extension/extension.js" "$EXTENSION_DIR/"

    print_info "✓ Extension files copied to $EXTENSION_DIR"

    # Enable extension
    gnome-extensions enable speech-to-clipboard@linux-speech-tools 2>/dev/null || true

    print_info "✓ Extension installed!"
    echo ""
    echo "Features:"
    echo "  - System tray icon with recording status"
    echo "  - Right-click menu for all functions"
    echo "  - Global hotkey (Super+Shift+Space)"
    echo "  - Visual recording indicator"
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
    echo "2) GNOME Shell Extension (Advanced)"
    echo "   └─ System tray integration + visual indicators"
    echo ""
    echo "3) Both"
    echo "   └─ Complete integration experience"
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
        if gnome-extensions list | grep -q "speech-to-clipboard@linux-speech-tools"; then
            print_info "✓ Extension is enabled"
        else
            print_warning "! Extension exists but may not be enabled"
        fi
    else
        print_info "- GNOME extension not installed"
    fi
}

uninstall() {
    print_step "Uninstalling GNOME integration..."

    # Remove basic integration
    if [ -f "$INSTALL_DIR/gnome-dictation" ]; then
        rm -f "$INSTALL_DIR/gnome-dictation"
        print_info "✓ Removed gnome-dictation script"
    fi

    # Remove keyboard shortcut
    gsettings reset org.gnome.settings-daemon.plugins.media-keys custom-keybindings 2>/dev/null || true
    print_info "✓ Reset custom keybindings"

    # Remove extension
    if [ -d "$EXTENSION_DIR" ]; then
        gnome-extensions disable speech-to-clipboard@linux-speech-tools 2>/dev/null || true
        rm -rf "$EXTENSION_DIR"
        print_info "✓ Removed GNOME extension"
    fi

    print_info "✓ Uninstallation complete"
}

main() {
    print_header

    check_dependencies

    show_menu

    read -p "Enter your choice (1-5): " choice

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
    print_info "Installation complete! 🎉"
    echo ""
    echo "Next steps:"
    echo "1. Try the hotkey: Super+Shift+Space"
    echo "2. Check system notifications for feedback"
    echo "3. Use 'gnome-dictation status' to check recording state"
    echo ""
    echo "For help: gnome-dictation help"
}

main "$@"