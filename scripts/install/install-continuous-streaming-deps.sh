#!/bin/bash
# Install dependencies for continuous audio streaming testing

set -euo pipefail

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

print_header() {
    echo -e "${BLUE}"
    echo "╭────────────────────────────────────────────────────────╮"
    echo "│         Continuous Streaming Dependencies              │"
    echo "│              Installation Script                       │"
    echo "╰────────────────────────────────────────────────────────╯"
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

# Check what's already available
check_current_setup() {
    print_step "Checking current setup..."

    # Check Python modules
    local missing_python=()

    if ! python3 -c "import soundfile" 2>/dev/null; then
        missing_python+=("soundfile")
    fi

    if ! python3 -c "import requests" 2>/dev/null; then
        missing_python+=("requests")
    fi

    if ! python3 -c "import bs4" 2>/dev/null; then
        missing_python+=("beautifulsoup4")
    fi

    if ! python3 -c "import numpy" 2>/dev/null; then
        missing_python+=("numpy")
    fi

    # Check system tools
    local missing_system=()

    if ! command -v ffmpeg >/dev/null; then
        missing_system+=("ffmpeg")
    fi

    if ! command -v ffplay >/dev/null; then
        missing_system+=("ffmpeg (includes ffplay)")
    fi

    # Report status
    if [ ${#missing_python[@]} -eq 0 ] && [ ${#missing_system[@]} -eq 0 ]; then
        print_info "✅ All dependencies already available!"
        return 0
    fi

    if [ ${#missing_python[@]} -gt 0 ]; then
        print_warning "Missing Python packages: ${missing_python[*]}"
    fi

    if [ ${#missing_system[@]} -gt 0 ]; then
        print_warning "Missing system tools: ${missing_system[*]}"
    fi

    return 1
}

# Install Python dependencies
install_python_deps() {
    print_step "Installing Python dependencies..."

    # Check if we're in a virtual environment
    if [ -n "${VIRTUAL_ENV:-}" ]; then
        print_info "Using virtual environment: $VIRTUAL_ENV"
        pip install soundfile requests beautifulsoup4 numpy
    elif [ -d "$HOME/.venvs/tts" ]; then
        print_info "Using existing TTS virtual environment"
        "$HOME/.venvs/tts/bin/pip" install soundfile requests beautifulsoup4 numpy
    else
        print_info "Installing globally (you may need sudo)"
        python3 -m pip install --user soundfile requests beautifulsoup4 numpy || \
        sudo python3 -m pip install soundfile requests beautifulsoup4 numpy
    fi

    print_info "✅ Python dependencies installed"
}

# Install system dependencies
install_system_deps() {
    print_step "Installing system dependencies..."

    # Detect package manager
    if command -v apt >/dev/null; then
        print_info "Using apt package manager..."
        sudo apt update
        sudo apt install -y ffmpeg python3-dev libsndfile1-dev
    elif command -v dnf >/dev/null; then
        print_info "Using dnf package manager..."
        sudo dnf install -y ffmpeg python3-devel libsndfile-devel
    elif command -v yum >/dev/null; then
        print_info "Using yum package manager..."
        sudo yum install -y ffmpeg python3-devel libsndfile-devel
    elif command -v pacman >/dev/null; then
        print_info "Using pacman package manager..."
        sudo pacman -S --noconfirm ffmpeg python libsndfile
    else
        print_error "Unsupported package manager"
        print_info "Please install manually:"
        print_info "  - ffmpeg (with ffplay)"
        print_info "  - libsndfile development headers"
        return 1
    fi

    print_info "✅ System dependencies installed"
}

# Test the installation
test_installation() {
    print_step "Testing installation..."

    # Test Python imports
    python3 -c "
import soundfile
import requests
import bs4
import numpy
print('✅ All Python modules imported successfully')
"

    # Test system tools
    if command -v ffmpeg >/dev/null && command -v ffplay >/dev/null; then
        echo "✅ ffmpeg and ffplay available"
    else
        print_error "ffmpeg or ffplay not available"
        return 1
    fi

    # Test the continuous streaming module
    if python3 src/tts/say_read.py --test >/dev/null 2>&1; then
        echo "✅ Continuous streaming module working"
    else
        print_warning "Continuous streaming module needs attention"
    fi

    print_info "✅ Installation test completed"
}

# Main installation process
main() {
    print_header

    echo "This script will install dependencies for continuous audio streaming."
    echo ""
    echo "Dependencies to install:"
    echo "  Python: soundfile, requests, beautifulsoup4, numpy"
    echo "  System: ffmpeg (with ffplay), libsndfile"
    echo ""

    if check_current_setup; then
        echo ""
        print_info "🎉 No installation needed - ready to test!"
        echo ""
        echo "Try these commands:"
        echo "  python3 tests/test_speech_tools.py"
        echo "  scripts/demo/demo-audio-streaming.sh"
        exit 0
    fi

    echo ""
    read -p "Continue with installation? (y/N): " response
    if [[ ! $response =~ ^[Yy]$ ]]; then
        echo "Installation cancelled."
        exit 0
    fi

    echo ""

    # Install dependencies
    install_python_deps
    echo ""

    install_system_deps
    echo ""

    # Test everything
    test_installation
    echo ""

    print_info "🎉 Installation complete!"
    echo ""
    echo "🎮 Ready to test continuous streaming:"
    echo "  python3 tests/test_speech_tools.py"
    echo "  scripts/demo/demo-audio-streaming.sh"
    echo ""
    echo "🎵 Enhanced commands available:"
    echo "  bin/say-read-continuous [URL]"
    echo "  bin/say-read-gnome [URL]"
    echo ""
}

# Handle command line arguments
case "${1:-install}" in
    "check")
        check_current_setup
        ;;
    "test")
        test_installation
        ;;
    "install"|"")
        main
        ;;
    *)
        echo "Usage: $0 [install|check|test]"
        echo ""
        echo "Commands:"
        echo "  install  - Install all dependencies (default)"
        echo "  check    - Check current dependency status"
        echo "  test     - Test current installation"
        ;;
esac