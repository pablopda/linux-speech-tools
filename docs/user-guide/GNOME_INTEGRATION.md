# GNOME Speech-to-Clipboard Integration

Use GNOME hotkeys and notifications with Linux Speech Tools dictation.

## 🎯 Features

### ✨ **Basic Integration**
- **Toggle recording**: `Ctrl+Alt+V` - Press once to start, again to stop
- **Smart notifications**: Visual feedback for recording state
- **Clipboard integration**: Automatic copying, with manual paste by default
- **Multiple modes**: Toggle mode (default) or fixed duration

### 🚀 **Experimental Integration (GNOME Shell Extension)**
- **Experimental only**: the Shell extension is not the recommended setup path
- **Version-sensitive**: GNOME Shell extension APIs vary across GNOME releases
- **Canonical backend**: dictation state should come from `talk2claude-faster-toggle`

## 📦 Installation

### Quick Install
```bash
# Install speech-tools with STT/GNOME profile
./installer.sh --with-stt --with-gnome

# Or install GNOME integration after setting up speech-tools
./scripts/install/install-gnome-integration.sh --basic

# Preview the GNOME changes without touching files or settings
./scripts/install/install-gnome-integration.sh --basic --dry-run
```

### Manual Setup

#### Option 1: Basic Integration
```bash
# Install the supported Ctrl+Alt+V hotkey path
./scripts/install/install-gnome-integration.sh --basic

# Or, after the tools are already installed, change only the hotkey
setup-faster-hotkey.sh --noninteractive
```

This configures one GNOME custom keybinding:

```text
/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/
  command: talk2claude-faster-toggle
  binding: <Control><Alt>v
```

`gnome-dictation setup` is still available, but it configures the same canonical
`talk2claude-faster-toggle` command:

```bash
gnome-dictation setup
```

#### Option 2: GNOME Shell Extension (Experimental)
```bash
# Only use this if you are testing the experimental panel/menu UX.
./scripts/install/install-gnome-integration.sh --extension
```

The Shell extension is de-scoped from the supported GNOME path until it is
ported and tested against current GNOME Shell APIs.

## 🎮 Usage

### 🔄 **Default: Toggle Mode**
| Action | Hotkey | Result |
|--------|--------|--------|
| **Start Recording** | `Ctrl+Alt+V` (1st press) | 🔴 Begins recording, microphone stays open |
| **Stop & Transcribe** | `Ctrl+Alt+V` (2nd press) | ⏹️ Stops recording, transcribes buffered speech, copies to clipboard |
| **Capability Check** | Command line | `talk2claude-faster --check` |
| **Live Status** | Command line | `talk2claude-faster-toggle status --plain` |

### 🎹 **Alternative Shortcuts**
| Shortcut | Action |
|----------|--------|
| `setup-faster-hotkey.sh` | Change the faster dictation hotkey |
| Right-click tray icon | Experimental extension menu, if installed |

### 💻 **Command Line**

**Toggle Mode (Default):**
```bash
# Start/stop recording
talk2claude-faster-toggle    # Same as hotkey

# Capability check
talk2claude-faster --check
talk2claude-faster --diagnose

# Script-friendly live status
talk2claude-faster-toggle status --plain
talk2claude-faster-toggle status --json

# Remove stopped STT logs/state
talk2claude-faster-toggle purge-state
```

**Mode Management:**
```bash
setup-faster-hotkey.sh --binding "<Super><Ctrl>space"
```

## 🔧 Configuration

### Environment Variables
```bash
# Dictation defaults
export WHISPER_MODEL=base
export ASR_LANG=en
export DICTATION_MODE=clipboard

# Preview before copying or typing
export DICTATION_PREVIEW=1

# Explicit transcript file fallback when no clipboard tool exists
export STT_TRANSCRIPT_FALLBACK=1
```

### Customizing Hotkeys
```bash
# Preferred helper
setup-faster-hotkey.sh --binding "<Super><Ctrl>space"

# Equivalent low-level GNOME setting
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/ binding "<Super><Ctrl>space"
```

## 🎨 Workflow Examples

### 📝 **Writing & Documentation (Toggle Mode)**
1. Open any text editor (VS Code, LibreOffice, etc.)
2. Press `Ctrl+Alt+V` → 🔴 Recording starts
3. Speak your content (as long as you need)
4. Press `Ctrl+Alt+V` → ⏹️ Stops, transcribes, and copies to clipboard
5. Paste with `Ctrl+V`

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
ASR_LANG=es talk2claude-faster-toggle

# Force English
ASR_LANG=en talk2claude-faster-toggle

# Persist the default through the installer runtime config
WHISPER_MODEL=base ./installer.sh --with-stt --download-models
```

## 🛠 Troubleshooting

### Extension Issues
The Shell extension is experimental. Prefer the basic hotkey integration unless
you are actively testing extension compatibility.

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
gnome-dictation status --plain

# Test underlying speech tools
talk2claude-faster --check

# Check detailed live state
gnome-dictation status --json

# Check audio devices
pactl list sources short
```

### Missing Dependencies
```bash
# Install required packages
sudo apt update
sudo apt install gnome-shell-extension-prefs libnotify-bin dbus-bin python3-dbus python3-gi wl-clipboard xclip

# Verify speech tools installation
talk2claude-faster --check
talk2claude-faster --diagnose
```

## 🔐 Permissions

The GNOME integration requires:
- **Microphone access**: For voice recording
- **Clipboard access**: For copying transcribed text
- **Accessibility features**: Only for optional direct typing (`ydotool`/`xdotool`)
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
- Dictated text is not printed into hotkey logs
- Transcript file fallback is disabled unless `STT_TRANSCRIPT_FALLBACK=1` is set
- **No data** is sent to external servers
- Full **offline operation** with Whisper model

For desktop/audio verification that cannot run in CI, use
`docs/developer/STT_MANUAL_QA_CHECKLIST.md`.

---

*Transform your GNOME desktop into a voice-powered productivity machine!* 🎤✨
