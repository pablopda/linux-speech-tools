#!/bin/bash
# Setup custom hotkey for speech recognition

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
    1) binding="<Ctrl><Alt>v" ;;
    2) binding="<Ctrl><Alt>s" ;;
    3) binding="<Super>F12" ;;
    4) binding="<Ctrl><Shift>m" ;;
    5)
        echo "Enter your custom binding (e.g., <Ctrl><Alt>r):"
        read -p "Binding: " binding
        ;;
    *)
        echo "Invalid choice, keeping current binding"
        exit 1
        ;;
esac

# Set the new binding
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/ binding "$binding"

echo ""
echo "✅ Hotkey set to: $binding"
echo ""
echo "Test it now! Your hotkey should:"
echo "1. Show notification 'Recording 5s...'"
echo "2. Record for 5 seconds"
echo "3. Transcribe and copy to clipboard"
echo "4. Show 'Copied: [your text]'"