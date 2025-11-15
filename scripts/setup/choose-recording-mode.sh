#!/bin/bash
# Choose between fixed-duration and toggle recording modes

echo "🎤 Choose Recording Mode"
echo "======================="
echo ""
echo "1) Toggle Mode (Default) ⭐"
echo "   └─ Press hotkey → Starts recording (stays open)"
echo "   └─ Press again → Stops and transcribes"
echo "   └─ Good for: Long dictation, variable-length content"
echo ""
echo "2) Fixed Duration"
echo "   └─ Press hotkey → Records for 5 seconds → Auto-transcribes"
echo "   └─ Good for: Quick notes, commands, short phrases"
echo ""

read -p "Enter choice (1-2): " choice

script_path=$(pwd)

case $choice in
    1)
        echo "🔄 Setting up Toggle mode (Default)..."
        gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/ command "$script_path/toggle-speech.sh toggle"
        gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/ name "Speech Dictation (Toggle)"
        echo "✅ Toggle mode active"
        echo ""
        echo "Usage:"
        echo "  Ctrl+Alt+V (1st press) → 🔴 Starts recording"
        echo "  Ctrl+Alt+V (2nd press) → ⏹️ Stops and transcribes"
        ;;
    2)
        echo "📝 Setting up Fixed Duration mode..."
        gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/ command "$script_path/simple-speech.sh 5"
        gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/ name "Speech Dictation (5s)"
        echo "✅ Fixed Duration mode active"
        echo ""
        echo "Usage: Ctrl+Alt+V → Speak for 5 seconds → Auto-transcribe"
        ;;
    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac

echo ""
echo "🎮 Test Commands:"
echo "Current hotkey: Ctrl+Alt+V"
echo ""

if [ "$choice" = "1" ]; then
    echo "Test directly: ./toggle-speech.sh"
    echo "Check status: ./toggle-speech.sh status"
else
    echo "Test directly: ./simple-speech.sh 5"
    echo "Different durations: ./simple-speech.sh 3"
fi

echo ""
echo "Change hotkey: ./setup-hotkey.sh"
echo "Switch modes anytime: ./choose-recording-mode.sh"