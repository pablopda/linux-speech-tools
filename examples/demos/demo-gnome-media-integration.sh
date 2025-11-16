#!/usr/bin/env bash
# Demo of GNOME Media Control Integration for Continuous Audio Streaming
# Showcases notification-based controls for document reading

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Script paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAY_READ_GNOME="$SCRIPT_DIR/../../bin/say-read-gnome"

# Demo configuration
DEMO_URL="https://en.wikipedia.org/wiki/Linux"
DEMO_TEXT="Welcome to the GNOME media integration demo for Linux Speech Tools. This feature transforms document reading into a native GNOME experience with notification-based controls. You can pause, resume, and stop reading sessions directly from the notification panel. This provides professional media player functionality for text-to-speech reading."

# Display banner
show_banner() {
    echo -e "${BOLD}${BLUE}"
    cat << 'EOF'
╔═══════════════════════════════════════════════════════════════════╗
║                🎵 GNOME Media Control Integration                 ║
║              Professional TTS with Desktop Controls              ║
╚═══════════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"
}

# Show features overview
show_features() {
    echo -e "${CYAN}🚀 Enhanced Features:${NC}"
    echo -e "  🎮 ${BOLD}Notification Controls${NC} - Play/pause/stop from desktop"
    echo -e "  📊 ${BOLD}Progress Tracking${NC} - Real-time reading progress display"
    echo -e "  📱 ${BOLD}Native Integration${NC} - Feels like built-in GNOME media"
    echo -e "  🎵 ${BOLD}Seamless Audio${NC} - Professional continuous streaming"
    echo ""
}

# Check GNOME environment
check_environment() {
    echo -e "${BLUE}🔍 Checking GNOME environment...${NC}"

    local issues=()

    # Check GNOME desktop
    if [ -z "${XDG_CURRENT_DESKTOP:-}" ] || [[ ! "$XDG_CURRENT_DESKTOP" =~ GNOME ]]; then
        issues+=("Not running in GNOME environment")
    fi

    # Check dependencies
    if ! command -v notify-send &> /dev/null; then
        issues+=("notify-send not available")
    fi

    if ! command -v dbus-send &> /dev/null; then
        issues+=("dbus-send not available")
    fi

    if [ ! -x "$SAY_READ_GNOME" ]; then
        issues+=("say-read-gnome not executable")
    fi

    # Report results
    if [ ${#issues[@]} -eq 0 ]; then
        echo -e "${GREEN}✅ Environment ready for GNOME integration!${NC}"
        return 0
    else
        echo -e "${YELLOW}⚠️ Environment issues detected:${NC}"
        printf '   - %s\n' "${issues[@]}"
        echo ""
        echo -e "${YELLOW}Demo will proceed but some features may not work.${NC}"
        return 1
    fi
}

# Demo setup process
demo_setup() {
    echo -e "${BLUE}🔧 Setting up GNOME integration...${NC}"

    # Make scripts executable
    chmod +x "$SCRIPT_DIR"/*.py "$SCRIPT_DIR"/*.sh 2>/dev/null || true

    # Setup integration
    if [ -x "$SAY_READ_GNOME" ]; then
        "$SAY_READ_GNOME" --setup
        echo -e "${GREEN}✅ Integration setup complete${NC}"
    else
        echo -e "${RED}❌ say-read-gnome not found or not executable${NC}"
        return 1
    fi
}

# Demo the text reading with controls
demo_text_reading() {
    echo -e "${CYAN}📝 Demo 1: Text Reading with Media Controls${NC}"
    echo -e "${BOLD}Text:${NC} ${DEMO_TEXT:0:100}..."
    echo ""
    echo -e "${YELLOW}🎮 Try the controls in the notification:${NC}"
    echo -e "  ⏸️ Click ${BOLD}Pause${NC} to pause reading"
    echo -e "  ▶️ Click ${BOLD}Resume${NC} to continue"
    echo -e "  ⏹️ Click ${BOLD}Stop${NC} to end reading"
    echo ""

    read -p "Press Enter to start reading demo text, or 's' to skip: " choice
    if [[ "$choice" != "s" ]]; then
        echo -e "${BLUE}🎵 Starting text reading...${NC}"
        echo "$DEMO_TEXT" | "$SAY_READ_GNOME" --max-chars 500 - || true
        echo -e "${GREEN}✅ Text reading demo complete${NC}"
    fi
    echo ""
}

# Demo URL reading
demo_url_reading() {
    echo -e "${CYAN}🌐 Demo 2: Web Article Reading${NC}"
    echo -e "${BOLD}URL:${NC} $DEMO_URL"
    echo ""
    echo -e "${YELLOW}🎮 This will show:${NC}"
    echo -e "  📊 Progress tracking as chunks are read"
    echo -e "  📱 Article title in notification"
    echo -e "  🎮 Full media controls"
    echo ""

    read -p "Press Enter to start URL reading, or 's' to skip: " choice
    if [[ "$choice" != "s" ]]; then
        echo -e "${BLUE}🎵 Starting URL reading (limited to 1000 chars for demo)...${NC}"
        "$SAY_READ_GNOME" --max-chars 1000 "$DEMO_URL" || true
        echo -e "${GREEN}✅ URL reading demo complete${NC}"
    fi
    echo ""
}

# Demo manual controls
demo_manual_controls() {
    echo -e "${CYAN}🎮 Demo 3: Manual Media Controls${NC}"
    echo ""
    echo -e "${YELLOW}Available commands:${NC}"
    echo -e "  ${SCRIPT_DIR}/gnome-notification-handler.sh pause"
    echo -e "  ${SCRIPT_DIR}/gnome-notification-handler.sh resume"
    echo -e "  ${SCRIPT_DIR}/gnome-notification-handler.sh stop"
    echo -e "  ${SCRIPT_DIR}/gnome-notification-handler.sh status"
    echo ""

    read -p "Press Enter to test manual status command, or 's' to skip: " choice
    if [[ "$choice" != "s" ]]; then
        echo -e "${BLUE}🔍 Testing status command...${NC}"
        if [ -x "$SCRIPT_DIR/gnome-notification-handler.sh" ]; then
            "$SCRIPT_DIR/gnome-notification-handler.sh" status || true
        else
            echo -e "${RED}❌ Notification handler not found${NC}"
        fi
    fi
    echo ""
}

# Show usage examples
show_usage_examples() {
    echo -e "${CYAN}💡 Usage Examples:${NC}"
    echo ""
    echo -e "${BOLD}Basic reading:${NC}"
    echo -e "  $SAY_READ_GNOME https://www.bbc.com/news"
    echo -e "  $SAY_READ_GNOME document.pdf"
    echo ""
    echo -e "${BOLD}With options:${NC}"
    echo -e "  $SAY_READ_GNOME --lang es --max-chars 2000 article.txt"
    echo -e "  $SAY_READ_GNOME --voice ef_dora https://example.com"
    echo ""
    echo -e "${BOLD}Setup and status:${NC}"
    echo -e "  $SAY_READ_GNOME --setup           # Setup integration"
    echo -e "  $SAY_READ_GNOME --check-gnome     # Check status"
    echo ""
}

# Main demo flow
run_demo() {
    show_banner
    show_features

    # Environment check
    local env_ok=true
    check_environment || env_ok=false
    echo ""

    # Setup if environment is ok
    if $env_ok; then
        demo_setup
        echo ""

        # Run demos
        demo_text_reading
        demo_url_reading
        demo_manual_controls
    fi

    # Show usage examples
    show_usage_examples

    # Final message
    echo -e "${BOLD}${GREEN}🎉 GNOME Media Integration Demo Complete!${NC}"
    echo ""
    echo -e "${CYAN}Key Benefits:${NC}"
    echo -e "  🎵 Transform reading into native GNOME media experience"
    echo -e "  📱 Control playback without returning to terminal"
    echo -e "  🎮 Professional media player functionality for TTS"
    echo -e "  📊 Visual progress tracking and status updates"
    echo ""
    echo -e "${YELLOW}Try it yourself:${NC}"
    echo -e "  $SAY_READ_GNOME https://your-favorite-article.com"
}

# Interactive menu
show_menu() {
    echo -e "${BOLD}GNOME Media Integration Demo${NC}"
    echo ""
    echo "Options:"
    echo "  1) Run full demo"
    echo "  2) Setup integration only"
    echo "  3) Check GNOME status"
    echo "  4) Show usage examples"
    echo "  5) Exit"
    echo ""
    read -p "Choose an option (1-5): " choice

    case "$choice" in
        1) run_demo ;;
        2) demo_setup ;;
        3) check_environment ;;
        4) show_usage_examples ;;
        5) echo "👋 Goodbye!"; exit 0 ;;
        *) echo "Invalid option. Please choose 1-5."; show_menu ;;
    esac
}

# Main function
main() {
    case "${1:-menu}" in
        "full"|"demo"|"run")
            run_demo
            ;;
        "setup")
            demo_setup
            ;;
        "check")
            check_environment
            ;;
        "examples")
            show_usage_examples
            ;;
        "menu"|"")
            show_menu
            ;;
        "--help"|"-h"|"help")
            echo "GNOME Media Integration Demo"
            echo ""
            echo "Usage: $0 [command]"
            echo ""
            echo "Commands:"
            echo "  full      Run full demo"
            echo "  setup     Setup integration only"
            echo "  check     Check environment"
            echo "  examples  Show usage examples"
            echo "  menu      Interactive menu (default)"
            ;;
        *)
            echo "Unknown command: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
}

# Run main function
main "$@"