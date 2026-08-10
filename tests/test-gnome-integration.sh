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
print_skip() { echo -e "${YELLOW}[SKIP]${NC} $1"; }
FAILURES=0
print_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    FAILURES=$((FAILURES + 1))
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GNOME_DICTATION="$PROJECT_ROOT/bin/gnome-dictation"
TALK2CLAUDE="$PROJECT_ROOT/bin/talk2claude"
GNOME_INSTALLER="$PROJECT_ROOT/scripts/install/install-gnome-integration.sh"

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
if [ -x "$TALK2CLAUDE" ]; then
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
    if "$TALK2CLAUDE" status >/dev/null 2>&1; then
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
if [ -f "$PROJECT_ROOT/gnome-extension/metadata.json" ] && \
   [ -f "$PROJECT_ROOT/gnome-extension/extension.js" ] && \
   [ -f "$PROJECT_ROOT/gnome-extension/focusService.js" ]; then
    print_pass "Extension files present"
else
    print_fail "Extension files missing"
fi

print_test "Checking focus provider contract and GNOME 50 declaration..."
if grep -q 'org.linux_speech_tools.Focus' "$PROJECT_ROOT/gnome-extension/focusService.js" && \
   grep -q '/org/linux_speech_tools/Focus' "$PROJECT_ROOT/gnome-extension/focusService.js" && \
   grep -q 'Gio.BusNameOwnerFlags.DO_NOT_QUEUE' "$PROJECT_ROOT/gnome-extension/focusService.js" && \
   grep -q '"50"' "$PROJECT_ROOT/gnome-extension/metadata.json" && \
   grep -q 'gnome-extension/"\*\.js' "$GNOME_INSTALLER"; then
    print_pass "Focus provider contract, non-queued ownership, and installer module copy are present"
else
    print_fail "Focus provider lifecycle contract, GNOME version, or installer module copy is missing"
fi

print_test "Checking disabled extensions are not reported as enabled..."
diagnostic_tmp="$(mktemp -d)"
mkdir -p \
    "$diagnostic_tmp/bin" \
    "$diagnostic_tmp/home/.local/bin" \
    "$diagnostic_tmp/home/.local/share/gnome-shell/extensions/speech-to-clipboard@linux-speech-tools"
cat >"$diagnostic_tmp/bin/gnome-extensions" <<'EOF'
#!/usr/bin/env bash
if [ "$1" = "list" ] && [ "$2" = "--disabled" ]; then
    echo "speech-to-clipboard@linux-speech-tools"
fi
EOF
cat >"$diagnostic_tmp/bin/gsettings" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$diagnostic_tmp/bin/notify-send" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$diagnostic_tmp/home/.local/bin/gnome-dictation" <<'EOF'
#!/usr/bin/env bash
echo "Ready for voice input"
EOF
chmod +x \
    "$diagnostic_tmp/bin/gnome-extensions" \
    "$diagnostic_tmp/bin/gsettings" \
    "$diagnostic_tmp/bin/notify-send" \
    "$diagnostic_tmp/home/.local/bin/gnome-dictation"
diagnostic_output=$(HOME="$diagnostic_tmp/home" \
    PATH="$diagnostic_tmp/bin:$PATH" \
    "$GNOME_INSTALLER" --test 2>&1 || true)
if [[ "$diagnostic_output" == *"GNOME extension is installed but disabled"* ]] && \
   [[ "$diagnostic_output" != *"✓ Extension is enabled"* ]]; then
    print_pass "Installer distinguishes disabled from enabled extensions"
else
    print_fail "Installer falsely reported a disabled extension as enabled"
fi
rm -rf -- "$diagnostic_tmp"

print_test "Packing the extension with its focus-service module..."
package_tmp=""
extension_package=""
if command -v gnome-extensions >/dev/null 2>&1; then
    package_tmp="$(mktemp -d)"
    extension_package="$package_tmp/speech-to-clipboard@linux-speech-tools.shell-extension.zip"
    if gnome-extensions pack \
        --extra-source=focusService.js \
        --out-dir "$package_tmp" \
        "$PROJECT_ROOT/gnome-extension" >/dev/null 2>&1 && \
       python3 - "$package_tmp" <<'PY'
import glob
import sys
import zipfile

archives = glob.glob(sys.argv[1] + "/*.shell-extension.zip")
if len(archives) != 1:
    raise SystemExit(1)
with zipfile.ZipFile(archives[0]) as package:
    required = {"metadata.json", "extension.js", "focusService.js"}
    if not required.issubset(package.namelist()):
        raise SystemExit(1)
PY
    then
        print_pass "Extension package contains the focus-service module"
    else
        print_fail "Extension package is incomplete"
        extension_package=""
    fi
else
    print_skip "gnome-extensions is unavailable; package validation not run"
fi

print_test "Loading the extension in an isolated nested GNOME 50 session..."
shell_major=$(gnome-shell --version 2>/dev/null | sed -n 's/.* \([0-9][0-9]*\)\..*/\1/p' || true)
if [ "$shell_major" = "50" ] && \
   [ -n "$extension_package" ] && \
   command -v gnome-shell-test-tool >/dev/null 2>&1 && \
   command -v dbus-run-session >/dev/null 2>&1 && \
   command -v timeout >/dev/null 2>&1; then
    smoke_log="$package_tmp/gnome-focus-smoke.log"
    if timeout 90 dbus-run-session -- \
        gnome-shell-test-tool \
        --headless \
        --extension "$extension_package" \
        "$PROJECT_ROOT/tests/gnome-focus-smoke.js" \
        >"$smoke_log" 2>&1 && \
       grep -q 'GNOME focus provider smoke passed' "$smoke_log"; then
        print_pass "GNOME 50 runtime load, name conflict, teardown, and re-enable passed"
    else
        print_fail "GNOME 50 isolated runtime smoke failed"
        tail -n 80 "$smoke_log" >&2 || true
    fi
else
    print_skip "GNOME 50 test tool or packaged extension unavailable; isolated runtime smoke not run"
fi

if [ -n "$package_tmp" ] && [ -d "$package_tmp" ]; then
    find "$package_tmp" -mindepth 1 -type f -delete
    rmdir "$package_tmp"
fi

print_test "Checking a running focus provider without changing GNOME state..."
if command -v gdbus >/dev/null 2>&1 && \
   [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ] && \
   gdbus call --session \
       --dest org.linux_speech_tools.Focus \
       --object-path /org/linux_speech_tools/Focus \
       --method org.linux_speech_tools.Focus.GetFocus >/dev/null 2>&1; then
    if PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -c \
        'from src.stt.target_context import gnome_focus_context; c = gnome_focus_context(); raise SystemExit(0 if c.source == "gnome-focus" and c.schema_version == 1 else 1)'; then
        print_pass "Running focus provider returned a valid versioned payload"
    else
        print_fail "Running focus provider returned an invalid payload"
    fi
else
    print_skip "Focus provider is not running; enable/disable and focus-transition checks remain manual"
fi

# Test 7: uv STT profile
print_test "Checking uv STT setup path..."
if command -v uv >/dev/null 2>&1; then
    print_pass "uv available for STT profile"
else
    print_fail "uv not found"
fi

echo ""
echo "🎯 Integration Test Summary"
echo "=========================="

# Test installer
print_test "Testing installer..."
if [ -x "$GNOME_INSTALLER" ]; then
    print_pass "Installer script ready"
    echo ""
    echo "To install:"
    echo "  $GNOME_INSTALLER"
else
    print_fail "Installer script missing"
fi

if [ "$FAILURES" -gt 0 ]; then
    echo ""
    echo -e "${RED}[FAIL]${NC} $FAILURES GNOME integration checks failed"
    exit 1
fi

echo ""
echo -e "${GREEN}[PASS]${NC} All GNOME integration checks passed"

echo ""
echo "Quick usage test:"
echo "  $GNOME_DICTATION status    # Check status"
echo "  $GNOME_DICTATION help      # Show help"
echo "  $GNOME_DICTATION setup     # Install hotkey"
echo ""
echo "For full installation, run:"
echo "  $GNOME_INSTALLER"
