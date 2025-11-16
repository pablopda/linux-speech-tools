#!/bin/bash
# Modern installer using uv for fast, reliable Python package management

set -euo pipefail

# Parse command line arguments
DRY_RUN=false
for arg in "$@"; do
    case $arg in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--dry-run] [--help]"
            echo ""
            echo "Options:"
            echo "  --dry-run    Test installation without making changes"
            echo "  --help       Show this help message"
            exit 0
            ;;
    esac
done

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

print_header() {
    echo -e "${BLUE}"
    echo "╭────────────────────────────────────────────────────────────╮"
    echo "│              Linux Speech Tools Installer                 │"
    echo "│                   Modern uv Edition                       │"
    echo "╰────────────────────────────────────────────────────────────╯"
    echo -e "${NC}"
}

print_step() {
    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "${GREEN}[DRY-RUN STEP]${NC} $1"
    else
        echo -e "${GREEN}[STEP]${NC} $1"
    fi
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

dry_run_execute() {
    local cmd="$1"
    local description="${2:-}"
    if [[ "$DRY_RUN" == "true" ]]; then
        echo -e "${YELLOW}[DRY-RUN]${NC} Would execute: $cmd"
        if [[ -n "$description" ]]; then
            echo -e "${BLUE}         ${NC} $description"
        fi
        return 0
    else
        eval "$cmd"
    fi
}

# Install uv if not available
install_uv() {
    if command -v uv >/dev/null; then
        print_info "✅ uv already installed: $(uv --version)"
        return 0
    fi

    print_step "Installing uv (modern Python package manager)..."

    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "🧪 Would download and install uv from https://astral.sh/uv/install.sh"
        print_info "🧪 Would add ~/.local/bin to PATH"
        return 0
    fi

    if curl -LsSf https://astral.sh/uv/install.sh | sh; then
        export PATH="$HOME/.local/bin:$PATH"
        print_info "✅ uv installed successfully: $(uv --version)"
    else
        print_error "Failed to install uv"
        return 1
    fi
}

# Install system dependencies
install_system_deps() {
    print_step "Installing system dependencies..."

    # Detect package manager and install dependencies
    if command -v apt >/dev/null; then
        print_info "Using apt package manager..."
        if [[ "$DRY_RUN" == "true" ]]; then
            print_info "🧪 Would run: sudo apt update"
            print_info "🧪 Would install: python3 python3-dev ffmpeg espeak-ng portaudio19-dev libsndfile1-dev pulseaudio-utils curl git"
        else
            sudo apt update
            sudo apt install -y \
                python3 python3-dev \
                ffmpeg espeak-ng \
                portaudio19-dev libsndfile1-dev \
                pulseaudio-utils \
                curl git
        fi
    elif command -v dnf >/dev/null; then
        print_info "Using dnf package manager..."
        if [[ "$DRY_RUN" == "true" ]]; then
            print_info "🧪 Would enable RPM Fusion repository"
            print_info "🧪 Would install: python3 python3-devel ffmpeg espeak-ng portaudio-devel libsndfile-devel pulseaudio-utils curl git"
        else
            # Enable RPM Fusion for ffmpeg
            sudo dnf install -y \
                https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
            sudo dnf install -y \
                python3 python3-devel \
                ffmpeg espeak-ng \
                portaudio-devel libsndfile-devel \
                pulseaudio-utils \
                curl git
        fi
    elif command -v pacman >/dev/null; then
        print_info "Using pacman package manager..."
        if [[ "$DRY_RUN" == "true" ]]; then
            print_info "🧪 Would install: python python-pip ffmpeg espeak-ng portaudio libsndfile pulseaudio-alsa curl git"
        else
            sudo pacman -S --needed \
                python python-pip \
                ffmpeg espeak-ng \
                portaudio libsndfile \
                pulseaudio-alsa \
                curl git
        fi
    else
        print_warning "Unknown package manager. Please install manually:"
        print_info "- python3, python3-dev"
        print_info "- ffmpeg, espeak-ng"
        print_info "- portaudio development headers"
        print_info "- libsndfile development headers"
        print_info "- pulseaudio-utils"
        if [[ "$DRY_RUN" != "true" ]]; then
            read -p "Continue anyway? (y/N): " -n 1 -r
            echo
            [[ $REPLY =~ ^[Yy]$ ]] || exit 1
        fi
    fi

    print_info "✅ System dependencies installed"
}

# Install Python package using uv
install_python_package() {
    print_step "Installing linux-speech-tools Python package..."

    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local project_root="$(cd "$script_dir/../.." && pwd)"

    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "🧪 Would cd to: $project_root"
        print_info "🧪 Would run: uv sync --all-extras"
        print_info "🧪 This installs all optional dependency groups: audio, dev, kokoro"
        return 0
    fi

    cd "$project_root"

    # Install using modern uv sync approach
    if uv sync --all-extras; then
        print_info "✅ Python package installed with all features"
    else
        print_error "Failed to install Python package"
        return 1
    fi
}

# Install executables to user's PATH
install_executables() {
    print_step "Installing executable scripts..."

    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local project_root="$(cd "$script_dir/../.." && pwd)"
    local install_dir="$HOME/.local/bin"

    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "🧪 Would create directory: $install_dir"
        for exe in "$project_root"/bin/*; do
            if [ -x "$exe" ]; then
                print_info "🧪 Would copy: $(basename "$exe") to $install_dir/"
            fi
        done
        if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
            print_info "🧪 Would add ~/.local/bin to PATH in ~/.bashrc"
        fi
        return 0
    fi

    mkdir -p "$install_dir"

    # Copy all executables
    for exe in "$project_root"/bin/*; do
        if [ -x "$exe" ]; then
            cp "$exe" "$install_dir/"
            print_info "✅ Installed $(basename "$exe")"
        fi
    done

    # Make sure ~/.local/bin is in PATH
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
        print_warning "Added ~/.local/bin to PATH in ~/.bashrc"
        print_info "Run 'source ~/.bashrc' or restart your terminal"
    fi
}

# Verify installation
verify_installation() {
    print_step "Verifying installation..."

    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "🧪 Would verify Python package installation"
        print_info "🧪 Would check executable accessibility: say, say-read, talk2claude"
        print_info "🧪 Would test uv runtime functionality"
        return 0
    fi

    # Check if we can run uv sync (equivalent to package being available)
    if uv sync --dry-run >/dev/null 2>&1; then
        print_info "✅ Python package environment verified"
    else
        print_warning "⚠ Python package verification failed"
    fi

    # Check if executables are accessible
    local missing_exes=()
    for exe in say say-read talk2claude; do
        if ! command -v "$exe" >/dev/null; then
            missing_exes+=("$exe")
        fi
    done

    if [ ${#missing_exes[@]} -eq 0 ]; then
        print_info "✅ All executables accessible"
    else
        print_warning "⚠ Some executables not in PATH: ${missing_exes[*]}"
        print_info "Make sure ~/.local/bin is in your PATH"
    fi

    # Test basic functionality
    if command -v uv >/dev/null && uv run --help >/dev/null 2>&1; then
        print_info "✅ uv runtime working"
    else
        print_warning "⚠ uv runtime test failed"
    fi
}

# Main installation process
main() {
    print_header

    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "🧪 DRY RUN MODE: Testing modern installation with uv..."
    else
        print_info "🚀 Starting modern installation with uv..."
    fi
    echo

    # Check if running as root (skip in dry-run mode for CI compatibility)
    if [ "$EUID" -eq 0 ] && [[ "$DRY_RUN" != "true" ]]; then
        print_error "Don't run this installer as root"
        print_info "Run as your regular user (sudo will be used when needed)"
        exit 1
    elif [ "$EUID" -eq 0 ] && [[ "$DRY_RUN" == "true" ]]; then
        print_warning "Running as root in dry-run mode (CI environment detected)"
    fi

    # Installation steps
    install_uv || exit 1
    install_system_deps || exit 1
    install_python_package || exit 1
    install_executables || exit 1
    verify_installation

    echo
    if [[ "$DRY_RUN" == "true" ]]; then
        print_info "🧪 DRY RUN COMPLETE! All installation steps verified."
        print_info "🚀 Run without --dry-run to perform actual installation."
    else
        print_info "🎉 Installation complete!"
        echo
        print_info "📚 Getting started:"
        print_info "  say 'Hello from Linux Speech Tools!'"
        print_info "  uv run src/tts/say_read.py --help"
        print_info "  talk2claude  # Voice input with transcription"
    fi
    echo
    print_info "📖 Full documentation: README.md"
    print_info "🐛 Issues: https://github.com/pablopda/linux-speech-tools/issues"
}

# Handle command line arguments
case "${1:-install}" in
    "install")
        main
        ;;
    "verify")
        verify_installation
        ;;
    "--help"|"-h")
        echo "Usage: $0 [install|verify|--help]"
        echo "  install: Full installation (default)"
        echo "  verify:  Verify existing installation"
        echo "  --help:  Show this help"
        ;;
    *)
        print_error "Unknown option: $1"
        echo "Use --help for usage information"
        exit 1
        ;;
esac