#!/bin/bash
# Modern installer using uv for fast, reliable Python package management

set -euo pipefail

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

# Install uv if not available
install_uv() {
    if command -v uv >/dev/null; then
        print_info "✅ uv already installed: $(uv --version)"
        return 0
    fi

    print_step "Installing uv (modern Python package manager)..."

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
        sudo apt update
        sudo apt install -y \
            python3 python3-dev \
            ffmpeg espeak-ng \
            portaudio19-dev libsndfile1-dev \
            pulseaudio-utils \
            curl git
    elif command -v dnf >/dev/null; then
        print_info "Using dnf package manager..."
        # Enable RPM Fusion for ffmpeg
        sudo dnf install -y \
            https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
        sudo dnf install -y \
            python3 python3-devel \
            ffmpeg espeak-ng \
            portaudio-devel libsndfile-devel \
            pulseaudio-utils \
            curl git
    elif command -v pacman >/dev/null; then
        print_info "Using pacman package manager..."
        sudo pacman -S --needed \
            python python-pip \
            ffmpeg espeak-ng \
            portaudio libsndfile \
            pulseaudio-alsa \
            curl git
    else
        print_warning "Unknown package manager. Please install manually:"
        print_info "- python3, python3-dev"
        print_info "- ffmpeg, espeak-ng"
        print_info "- portaudio development headers"
        print_info "- libsndfile development headers"
        print_info "- pulseaudio-utils"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        [[ $REPLY =~ ^[Yy]$ ]] || exit 1
    fi

    print_info "✅ System dependencies installed"
}

# Install Python package using uv
install_python_package() {
    print_step "Installing linux-speech-tools Python package..."

    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local project_root="$(cd "$script_dir/../.." && pwd)"

    cd "$project_root"

    # Install the project with all optional dependencies
    if uv pip install --system -e ".[all]"; then
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

    # Check if uv can find our package
    if uv pip show linux-speech-tools >/dev/null 2>&1; then
        print_info "✅ Python package installed correctly"
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

    print_info "🚀 Starting modern installation with uv..."
    echo

    # Check if running as root
    if [ "$EUID" -eq 0 ]; then
        print_error "Don't run this installer as root"
        print_info "Run as your regular user (sudo will be used when needed)"
        exit 1
    fi

    # Installation steps
    install_uv || exit 1
    install_system_deps || exit 1
    install_python_package || exit 1
    install_executables || exit 1
    verify_installation

    echo
    print_info "🎉 Installation complete!"
    echo
    print_info "📚 Getting started:"
    print_info "  say 'Hello from Linux Speech Tools!'"
    print_info "  uv run src/tts/say_read.py --help"
    print_info "  talk2claude  # Voice input with transcription"
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