#!/usr/bin/env bash
# Linux Speech Tools Installer - Main Entry Point
# Offers choice between traditional and modern (uv-based) installation

set -euo pipefail

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODERN_INSTALLER="$SCRIPT_DIR/scripts/install/install-with-uv.sh"
TRADITIONAL_INSTALLER="$SCRIPT_DIR/scripts/install/installer.sh"

show_banner() {
    echo -e "${BLUE}"
    echo "╭────────────────────────────────────────────────────────────╮"
    echo "│              Linux Speech Tools Installer                 │"
    echo "╰────────────────────────────────────────────────────────────╯"
    echo -e "${NC}"
}

show_options() {
    echo -e "${GREEN}Choose installation method:${NC}"
    echo
    echo "1. 🚀 Modern (Recommended) - Fast installation with uv"
    echo "   • 10-100x faster package installation"
    echo "   • Better dependency resolution"
    echo "   • Modern Python toolchain"
    echo "   • Self-contained script dependencies"
    echo
    echo "2. 🔧 Traditional - Original installation method"
    echo "   • Uses pip and manual virtual environments"
    echo "   • Compatible with older systems"
    echo "   • Creates talk2claude tool only"
    echo
    echo "3. ❓ Help - Show detailed information"
    echo
}

# Handle command line arguments for non-interactive use
case "${1:-}" in
    "--modern"|"-m")
        exec "$MODERN_INSTALLER" "${@:2}"
        ;;
    "--traditional"|"-t")
        exec "$TRADITIONAL_INSTALLER" "${@:2}"
        ;;
    "--help"|"-h")
        show_banner
        echo "Usage: $0 [--modern|-m|--traditional|-t|--help|-h]"
        echo
        echo "Options:"
        echo "  --modern      Use modern uv-based installation (recommended)"
        echo "  --traditional Use traditional pip-based installation"
        echo "  --help        Show this help message"
        echo
        echo "Interactive mode will be used if no option is specified."
        exit 0
        ;;
esac

# Interactive mode
show_banner
show_options

while true; do
    read -p "Enter your choice (1-3): " choice
    case $choice in
        1|modern|m)
            echo
            echo -e "${GREEN}🚀 Using modern installation with uv...${NC}"
            exec "$MODERN_INSTALLER" "$@"
            ;;
        2|traditional|t)
            echo
            echo -e "${YELLOW}🔧 Using traditional installation...${NC}"
            exec "$TRADITIONAL_INSTALLER" "$@"
            ;;
        3|help|h)
            echo
            echo -e "${BLUE}📚 Detailed Information:${NC}"
            echo
            echo "Modern Installation (uv):"
            echo "• Installs the complete linux-speech-tools package"
            echo "• All Python dependencies managed automatically"
            echo "• Scripts can be run with 'uv run' for instant environments"
            echo "• Installs all executables: say, say-read, talk2claude, etc."
            echo "• Modern Python practices with inline script dependencies"
            echo
            echo "Traditional Installation:"
            echo "• Creates only the talk2claude script"
            echo "• Expects manual virtual environment setup"
            echo "• Compatible with existing workflows"
            echo "• Minimal installation footprint"
            echo
            read -p "Press Enter to return to menu..."
            echo
            show_options
            ;;
        *)
            echo "Invalid choice. Please enter 1, 2, or 3."
            ;;
    esac
done