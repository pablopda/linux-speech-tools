#!/usr/bin/env bash
# Setup custom hotkey for speech recognition

set -euo pipefail

MEDIA_KEYS_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
BINDING_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/"

if ! command -v gsettings >/dev/null 2>&1; then
    echo "❌ Error: gsettings not found; run this in a GNOME desktop session" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Error: python3 not found; required to update the custom-keybindings list" >&2
    exit 1
fi

echo "🎹 Choose your preferred hotkey:"
echo ""
echo "1) Ctrl+Alt+V     (Currently set - recommended)"
echo "2) Ctrl+Alt+S     (S for Speech)"
echo "3) Super+F12      (Function key)"
echo "4) Ctrl+Shift+M   (M for Microphone)"
echo "5) Custom hotkey"
echo ""

read -p "Enter choice (1-5): " choice

case $choice in
    1) binding="<Control><Alt>v" ;;
    2) binding="<Control><Alt>s" ;;
    3) binding="<Super>F12" ;;
    4) binding="<Control><Shift>m" ;;
    5)
        echo "Enter your custom binding (e.g., <Control><Alt>r):"
        read -p "Binding: " binding
        ;;
    *)
        echo "Invalid choice, keeping current binding"
        exit 1
        ;;
esac

# Register the keybinding path in the custom-keybindings list. Without this the
# per-path "binding" value below is ignored and the shortcut silently does
# nothing. Mirrors setup-faster-hotkey.sh.
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

gsettings set "$MEDIA_KEYS_SCHEMA" custom-keybindings "$(update_keybinding_list add "$BINDING_PATH")"

# Set the new binding
gsettings set "$MEDIA_KEYS_SCHEMA.custom-keybinding:$BINDING_PATH" binding "$binding"

echo ""
echo "✅ Hotkey set to: $binding"
echo ""
echo "Test it now! Your hotkey should:"
echo "1. Show notification 'Recording 5s...'"
echo "2. Record for 5 seconds"
echo "3. Transcribe and copy to clipboard"
echo "4. Show 'Copied: [your text]'"
