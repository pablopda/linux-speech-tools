# GNOME Speech-to-Clipboard Integration

Transform your GNOME desktop into a powerful voice-controlled workspace with seamless speech-to-text integration.

## 🎯 Features

### ✨ **Basic Integration**
- **Global hotkey**: `Super+Shift+Space` to toggle recording
- **Smart notifications**: Visual feedback for all operations
- **Clipboard integration**: Automatic copying and pasting
- **Quick dictation**: Fast preset durations (3, 5, 10 seconds)

### 🚀 **Advanced Integration (GNOME Shell Extension)**
- **System tray icon**: Visual recording status indicator
- **Right-click menu**: Access all features instantly
- **Live status updates**: Real-time recording state
- **Seamless UX**: Native GNOME look and feel

## 📦 Installation

### Quick Install
```bash
# Install GNOME integration after setting up speech-tools
./install-gnome-integration.sh
```

### Manual Setup

#### Option 1: Basic Integration
```bash
# Copy and setup the enhanced wrapper
cp gnome-dictation ~/.local/bin/
chmod +x ~/.local/bin/gnome-dictation
gnome-dictation setup
```

#### Option 2: GNOME Shell Extension
```bash
# Install extension
mkdir -p ~/.local/share/gnome-shell/extensions/speech-to-clipboard@linux-speech-tools
cp gnome-extension/* ~/.local/share/gnome-shell/extensions/speech-to-clipboard@linux-speech-tools/
gnome-extensions enable speech-to-clipboard@linux-speech-tools

# Restart GNOME Shell
# Alt+F2 → type 'r' → Enter
```

## 🎮 Usage

### Keyboard Shortcuts
| Shortcut | Action |
|----------|--------|
| `Super+Shift+Space` | Toggle recording (start/stop) |
| Right-click tray icon | Access full menu (extension only) |

### Command Line
```bash
# Toggle recording
gnome-dictation

# Quick dictation presets
gnome-dictation quick 3    # 3 seconds
gnome-dictation quick 5    # 5 seconds
gnome-dictation quick 10   # 10 seconds

# Check recording status
gnome-dictation status

# Get help
gnome-dictation help
```

## 🔧 Configuration

### Environment Variables
```bash
# Auto-stop recording after timeout
export AUTO_TIMEOUT=30

# Custom paste delay
export PASTE_DELAY=0.5

# Allow pasting in terminal (normally clipboard-only)
export T2C_ALLOW_PASTE_IN_TERMINAL=1
```

### Customizing Hotkeys
```bash
# Change the global hotkey
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/ binding "<Super><Ctrl>space"
```

## 🎨 Workflow Examples

### 📝 **Writing & Documentation**
1. Open any text editor
2. Press `Super+Shift+Space`
3. Speak your content
4. Press `Super+Shift+Space` again
5. Text appears automatically!

### 💬 **Chat & Communication**
1. Open Slack, Discord, or any chat app
2. Use quick dictation: `gnome-dictation quick 5`
3. Speak your message
4. Text is automatically pasted

### 📊 **Coding & Terminal**
```bash
# Dictate commands (clipboard mode in terminals)
gnome-dictation quick 3
# Speak: "git commit dash m quote fix typo quote"
# Paste result: git commit -m "fix typo"
```

## 🛠 Troubleshooting

### Extension Issues
```bash
# Check if extension is loaded
gnome-extensions list | grep speech-to-clipboard

# View extension logs
journalctl -f /usr/bin/gnome-shell

# Restart GNOME Shell
# Alt+F2 → 'r' → Enter
```

### Recording Issues
```bash
# Check microphone permissions
gnome-dictation status

# Test underlying speech tools
talk2claude status

# Check audio devices
pactl list sources short
```

### Missing Dependencies
```bash
# Install required packages
sudo apt update
sudo apt install gnome-shell-extension-prefs libnotify-bin

# Verify speech tools installation
talk2claude --help
```

## 🔐 Permissions

The GNOME integration requires:
- **Microphone access**: For voice recording
- **Clipboard access**: For copying transcribed text
- **Accessibility features**: For auto-pasting (ydotool)
- **Notification system**: For user feedback

## 🎯 Advanced Tips

### Custom Voice Commands
Create smart shortcuts by combining with other tools:
```bash
# Voice-controlled git workflow
gnome-dictation quick 3
# Say: "commit message fix authentication bug"
# Then manually prefix: git commit -m "
```

### Multi-language Support
```bash
# Spanish dictation
ASR_LANG=es gnome-dictation quick 5

# Auto-detect language
ASR_LANG=auto gnome-dictation quick 5
```

### Workspace Integration
- **VS Code**: Use for code comments and documentation
- **LibreOffice**: Voice typing for documents
- **Web browsers**: Fill forms and search queries
- **Terminal**: Dictate complex commands (review before executing)

## 📈 Performance Tips

1. **Use quick dictation** for short phrases (faster processing)
2. **Background recording** for longer content
3. **Clear speech** in quiet environment for better accuracy
4. **Pause between sentences** for natural speech recognition

## 🚨 Privacy & Security

- All processing happens **locally** (no cloud services)
- Audio files are **temporary** and automatically cleaned
- **No data** is sent to external servers
- Full **offline operation** with Whisper model

---

*Transform your GNOME desktop into a voice-powered productivity machine!* 🎤✨