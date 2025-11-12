#!/bin/bash
# Test script for GNOME integration features

set -euo pipefail

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_test() { echo -e "${YELLOW}[TEST]${NC} $1"; }
print_pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
print_fail() { echo -e "${RED}[FAIL]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GNOME_DICTATION="$SCRIPT_DIR/gnome-dictation"

echo "🧪 GNOME Speech Integration Tests"
echo "================================="
echo ""

# Test 1: Basic script exists and is executable
print_test "Checking gnome-dictation script..."
if [ -x "$GNOME_DICTATION" ]; then
    print_pass "gnome-dictation script is executable"
else
    print_fail "gnome-dictation script missing or not executable"
    exit 1
fi

# Test 2: Help function works
print_test "Testing help function..."
if "$GNOME_DICTATION" help >/dev/null 2>&1; then
    print_pass "Help function works"
else
    print_fail "Help function failed"
fi

# Test 3: talk2claude dependency
print_test "Checking talk2claude dependency..."
if [ -x "$SCRIPT_DIR/talk2claude" ]; then
    print_pass "talk2claude found and executable"
else
    print_fail "talk2claude not found or not executable"
fi

# Test 4: Status function
print_test "Testing status function..."
status_output=$("$GNOME_DICTATION" status 2>&1 || true)
if [[ "$status_output" == *"Ready for voice input"* ]] || [[ "$status_output" == *"Recording"* ]]; then
    print_pass "Status function works"
else
    # Try direct talk2claude
    if "$SCRIPT_DIR/talk2claude" status >/dev/null 2>&1; then
        print_pass "Underlying talk2claude status works"
    else
        print_fail "Status function issues - check STT environment"
    fi
fi

# Test 5: GNOME tools availability
print_test "Checking GNOME tools..."
gnome_tools=("gsettings" "notify-send" "gnome-shell")
for tool in "${gnome_tools[@]}"; do
    if command -v "$tool" >/dev/null 2>&1; then
        print_pass "$tool available"
    else
        print_fail "$tool not available"
    fi
done

# Test 6: Extension files
print_test "Checking extension files..."
if [ -f "$SCRIPT_DIR/gnome-extension/metadata.json" ] && [ -f "$SCRIPT_DIR/gnome-extension/extension.js" ]; then
    print_pass "Extension files present"
else
    print_fail "Extension files missing"
fi

# Test 7: STT environment
print_test "Checking STT environment..."
if [ -d "$HOME/.venvs/stt" ]; then
    print_pass "STT Python environment found"
    if [ -x "$HOME/.venvs/stt/bin/python" ]; then
        print_pass "STT Python interpreter accessible"
    else
        print_fail "STT Python interpreter not executable"
    fi
else
    print_fail "STT environment not found at $HOME/.venvs/stt"
fi

echo ""
echo "🎯 Integration Test Summary"
echo "=========================="

# Test installer
print_test "Testing installer..."
if [ -x "$SCRIPT_DIR/install-gnome-integration.sh" ]; then
    print_pass "Installer script ready"
    echo ""
    echo "To install:"
    echo "  $SCRIPT_DIR/install-gnome-integration.sh"
else
    print_fail "Installer script missing"
fi

echo ""
echo "Quick usage test:"
echo "  $GNOME_DICTATION status    # Check status"
echo "  $GNOME_DICTATION help      # Show help"
echo "  $GNOME_DICTATION setup     # Install hotkey"
echo ""
echo "For full installation, run:"
echo "  $SCRIPT_DIR/install-gnome-integration.sh"