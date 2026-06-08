#!/usr/bin/env bash
# Setup GNOME hotkey for talk2claude-faster real-time dictation

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_HELPER=""
for candidate in \
    "$SCRIPT_DIR/linux-speech-tools-env" \
    "$(cd "$SCRIPT_DIR/../.." && pwd)/bin/linux-speech-tools-env"; do
    if [ -f "$candidate" ]; then
        ENV_HELPER="$candidate"
        break
    fi
done
if [ -n "$ENV_HELPER" ]; then
    # shellcheck disable=SC1090
    source "$ENV_HELPER"
fi
if [ -n "${LST_PROJECT_ROOT:-}" ]; then
    PROJECT_ROOT="$LST_PROJECT_ROOT"
else
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
fi
INSTALL_DIR="${LST_INSTALL_DIR:-$HOME/.local/bin}"
MEDIA_KEYS_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
BINDING_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/"
binding=""
noninteractive=false

usage() {
    cat <<EOF
usage: setup-faster-hotkey.sh [--binding BINDING] [--noninteractive]

Configure the GNOME hotkey for talk2claude-faster-toggle.

Options:
  --binding BINDING   GNOME binding string, e.g. <Control><Alt>v
  --noninteractive    Use the default binding without prompting
  -h, --help          Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --binding)
            if [ $# -lt 2 ]; then
                echo "Error: --binding requires a value" >&2
                exit 2
            fi
            binding="$2"
            shift 2
            ;;
        --noninteractive)
            noninteractive=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -x "$INSTALL_DIR/talk2claude-faster-toggle" ]; then
    TOGGLE_SCRIPT="$INSTALL_DIR/talk2claude-faster-toggle"
else
    TOGGLE_SCRIPT="$PROJECT_ROOT/bin/talk2claude-faster-toggle"
fi

# Check if toggle script exists
if [ ! -x "$TOGGLE_SCRIPT" ]; then
    echo "❌ Error: talk2claude-faster-toggle not found at $TOGGLE_SCRIPT"
    exit 1
fi

if ! command -v gsettings >/dev/null 2>&1; then
    echo "❌ Error: gsettings not found; run this in a GNOME desktop session" >&2
    exit 1
fi

echo "🎹 Setting up hotkey for talk2claude-faster"
echo ""
if [ -z "$binding" ] && [ "$noninteractive" = true ]; then
    binding="<Control><Alt>v"
fi

if [ -z "$binding" ]; then
    echo "Choose your preferred hotkey:"
    echo ""
    echo "1) Ctrl+Alt+V     (Default dictation shortcut)"
    echo "2) Ctrl+Alt+R     (R for Realtime)"
    echo "3) Ctrl+Alt+D     (D for Dictation)"
    echo "4) Super+R        (Windows/Super key + R)"
    echo "5) F12            (Single function key)"
    echo "6) Custom hotkey"
    echo ""

    read -p "Enter choice (1-6): " choice

    case $choice in
        1) binding="<Control><Alt>v" ;;
        2) binding="<Control><Alt>r" ;;
        3) binding="<Control><Alt>d" ;;
        4) binding="<Super>r" ;;
        5) binding="F12" ;;
        6)
            echo "Enter your custom binding (e.g., <Control><Alt>x):"
            read -p "Binding: " binding
            ;;
        *)
            echo "Invalid choice"
            exit 1
            ;;
    esac
fi

update_keybinding_list() {
    local action="$1"
    local path="$2"
    local current
    current="$(gsettings get "$MEDIA_KEYS_SCHEMA" custom-keybindings 2>/dev/null || echo "[]")"
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

# Add custom keybinding
echo "Setting up GNOME keybinding..."

gsettings set "$MEDIA_KEYS_SCHEMA" custom-keybindings "$(update_keybinding_list add "$BINDING_PATH")"

# Configure the shortcut
gsettings set "$MEDIA_KEYS_SCHEMA.custom-keybinding:$BINDING_PATH" name "Speech Dictation (Toggle)"
gsettings set "$MEDIA_KEYS_SCHEMA.custom-keybinding:$BINDING_PATH" command "$TOGGLE_SCRIPT"
gsettings set "$MEDIA_KEYS_SCHEMA.custom-keybinding:$BINDING_PATH" binding "$binding"

echo ""
echo "✅ Hotkey configured successfully!"
echo ""
echo "📌 Your hotkey: $binding"
echo ""
echo "How to use:"
echo "1. Press $binding to START clipboard dictation"
echo "2. Speak while the microphone is active"
echo "3. Press $binding again to STOP and copy the transcript"
echo ""
echo "Test it now!"
