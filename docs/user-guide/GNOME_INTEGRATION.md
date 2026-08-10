# GNOME Speech-to-Clipboard Integration

Use GNOME hotkeys and notifications with Linux Speech Tools dictation.

## 🎯 Features

### ✨ **Basic Integration**
- **Toggle recording**: `Ctrl+Alt+V` - Press once to start, again to stop
- **Developer prompt dictation**: `Ctrl+Alt+Space` - Live prompt capture for Claude Code, Codex, terminals, and IDEs
- **Smart notifications**: Visual feedback for recording state
- **Clipboard integration**: Automatic copying, with manual paste by default
- **Multiple modes**: Toggle mode (default) or fixed duration

### 🚀 **Experimental Integration (GNOME Shell Extension)**
- **Stable target identity**: a read-only D-Bus provider lets `lst-dictate`
  verify the original GNOME Wayland window and fail closed after focus changes
- **Experimental UI**: the panel/menu surface is not the recommended control
  path; the custom hotkeys remain canonical
- **Version-sensitive**: GNOME Shell extension APIs vary across GNOME releases
- **Canonical backend**: dictation state should come from `talk2claude-faster-toggle`

## 📦 Installation

### Quick Install
```bash
# Install speech-tools with STT/GNOME profile
./installer.sh --with-stt --with-gnome

# Or install GNOME integration after setting up speech-tools
./scripts/install/install-gnome-integration.sh --basic

# Add the focus provider when testing safe live typing on GNOME Wayland
./scripts/install/install-gnome-integration.sh --both

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

This configures two GNOME custom keybindings:

```text
/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dictation/
  command: talk2claude-faster-toggle
  binding: <Control><Alt>v

/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/developer-prompt-dictation/
  command: lst-dictate toggle
  binding: <Control><Alt>space
```

`gnome-dictation setup` is still available, but it configures the same canonical
`talk2claude-faster-toggle` command:

```bash
gnome-dictation setup
```

#### Option 2: GNOME Shell Extension (Experimental)
```bash
# Install the stable-focus provider plus the experimental panel/menu UX.
./scripts/install/install-gnome-integration.sh --extension
```

Use `--both` instead when you also want the normal hotkeys. The package declares
GNOME Shell 45–48 and 50. GNOME 50 is runtime-loaded by the repository's isolated
nested-compositor smoke test; live focus, lock/unlock, suspend, and application
behavior remain manual acceptance gates.

After GNOME Shell reloads the extension, verify the provider:

```bash
gdbus call --session \
  --dest org.linux_speech_tools.Focus \
  --object-path /org/linux_speech_tools/Focus \
  --method org.linux_speech_tools.Focus.GetFocus
```

The versioned JSON includes a per-enable Shell session ID, monotonically
changing focus generation, stable window sequence, lock state, and bounded app
metadata. A focus-away-and-back sequence is intentionally not accepted as the
original target. While locked, the service returns no window identity or
metadata.

The service is read-only and scoped to the user's session bus, but other
same-user processes can call it. An unlocked response can include the active
window title, app ID/class, and PID; do not publish raw diagnostic output.
Transcript and clipboard contents are never included, and prompt status files
contain only coarse target classification and insertion-result fields.

## 🎮 Usage

### 🔄 **Default: Toggle Mode**
| Action | Hotkey | Result |
|--------|--------|--------|
| **Start Recording** | `Ctrl+Alt+V` (1st press) | 🔴 Begins recording, microphone stays open |
| **Stop & Transcribe** | `Ctrl+Alt+V` (2nd press) | ⏹️ Stops recording, transcribes buffered speech, copies to clipboard |
| **Live Developer Prompt** | `Ctrl+Alt+Space` | Starts/stops `lst-dictate`, updating prompt text while you speak |
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

**Live Developer Prompt Mode:**
```bash
# Start/stop live prompt capture
lst-dictate toggle

# Capability check
lst-dictate --check

# Explicit target profiles
lst-dictate --profile claude
lst-dictate --profile codex
lst-dictate --profile ide

# Script-friendly live status
lst-dictate status --plain
lst-dictate status --json
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

# Developer prompt dictation defaults
export PROMPT_DICTATION_PROFILE=auto
export PROMPT_DICTATION_OUTPUT=auto
export PROMPT_DICTATION_SUBMIT=never
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
# Check if the extension is enabled (and therefore able to host the provider)
gnome-extensions list --enabled | grep -Fx speech-to-clipboard@linux-speech-tools

# View extension logs
journalctl -f /usr/bin/gnome-shell

# Reload GNOME Shell after installing or replacing extension files
# Wayland: log out and back in. X11 only: Alt+F2 → 'r' → Enter.
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
