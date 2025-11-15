# GNOME Speech-to-Clipboard Integration

Transform your GNOME desktop into a powerful voice-controlled workspace with seamless speech-to-text integration.

## 🎯 Features

### ✨ **Basic Integration**
- **Toggle recording**: `Ctrl+Alt+V` - Press once to start, again to stop
- **Smart notifications**: Visual feedback for recording state
- **Clipboard integration**: Automatic copying and pasting
- **Multiple modes**: Toggle mode (default) or fixed duration

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

### 🔄 **Default: Toggle Mode**
| Action | Hotkey | Result |
|--------|--------|--------|
| **Start Recording** | `Ctrl+Alt+V` (1st press) | 🔴 Begins recording, microphone stays open |
| **Stop & Transcribe** | `Ctrl+Alt+V` (2nd press) | ⏹️ Stops recording, transcribes speech, copies to clipboard |
| **Status Check** | Command line | `./toggle-speech.sh status` |

### 🎹 **Alternative Shortcuts**
| Shortcut | Action |
|----------|--------|
| Right-click tray icon | Access full menu (extension only) |
| `./choose-recording-mode.sh` | Switch between toggle and fixed-duration modes |

### 💻 **Command Line**

**Toggle Mode (Default):**
```bash
# Start/stop recording
./toggle-speech.sh toggle    # Same as hotkey

# Manual control
./toggle-speech.sh start     # Start recording
./toggle-speech.sh stop      # Stop and transcribe

# Status and help
./toggle-speech.sh status    # Check current state
./toggle-speech.sh help      # Show usage
```

**Fixed Duration Mode:**
```bash
# Quick recordings
./simple-speech.sh 3         # 3-second recording
./simple-speech.sh 5         # 5-second recording
./simple-speech.sh 10        # 10-second recording
```

**Mode Management:**
```bash
./choose-recording-mode.sh   # Switch between modes
./setup-hotkey.sh           # Change hotkey
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

### 📝 **Writing & Documentation (Toggle Mode)**
1. Open any text editor (VS Code, LibreOffice, etc.)
2. Press `Ctrl+Alt+V` → 🔴 Recording starts
3. Speak your content (as long as you need)
4. Press `Ctrl+Alt+V` → ⏹️ Stops, transcribes, and pastes automatically

### 💬 **Chat & Communication**
1. Open Slack, Discord, or any chat app
2. Press `Ctrl+Alt+V` → Start recording
3. Speak your message naturally
4. Press `Ctrl+Alt+V` → Message appears in clipboard, paste with `Ctrl+V`

### 📊 **Coding & Terminal**
```bash
# Voice-dictate complex commands:
# 1. Press Ctrl+Alt+V
# 2. Speak: "git commit dash m quote implement user authentication quote"
# 3. Press Ctrl+Alt+V
# 4. Paste result: git commit -m "implement user authentication"
```

### 🎯 **Multi-language Support**
The system automatically detects language or you can specify:
```bash
# Spanish dictation
ASR_LANG=es ./toggle-speech.sh toggle

# Force English
ASR_LANG=en ./toggle-speech.sh toggle
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