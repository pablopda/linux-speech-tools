# Low-Latency Speech-to-Text Quick Start Guide

## What is it?

`talk2claude-faster` is a low-latency speech-to-text tool that records while you speak, then transcribes after pauses or when you stop capture. It uses faster-whisper directly for minimal dependencies and quick setup.

## Features

- 🎯 **Low-latency feedback** - Speech is transcribed after pauses or finalization
- 📋 **Clipboard-first** - Text copies to your clipboard by default
- ⌨️ **Optional direct typing** - Text types into the active app only when enabled
- 👀 **Preview mode** - Review, edit, skip, or cancel text before output
- 🔐 **Private by default** - Dictated text is not written to logs
- 🚀 **Fast** - Short utterances are processed quickly after a pause or stop
- 🐧 **Linux native** - Works on Ubuntu, Fedora, etc.

## Installation

### Prerequisites

1. **Linux** (Ubuntu 22.04+ or Fedora 38+)
2. **Python 3.8+**
3. **Microphone** connected and working
4. **ffmpeg** (usually already installed):
   ```bash
   # Ubuntu/Debian
   sudo apt install ffmpeg

   # Fedora
   sudo dnf install ffmpeg

   # Arch/Manjaro
   sudo pacman -S ffmpeg
   ```

### Install Steps

#### Standard Installation (Recommended)
Low-latency dictation with uv-managed Python dependencies:

```bash
# Install faster-whisper/webrtcvad and prefetch a small model
./installer.sh --with-stt --download-models --whisper-model tiny

# Optional: setup direct typing permissions (one-time, only for --typing)
./installer.sh --with-stt --setup-uinput

# Logout and login for permissions to take effect
# (or run: newgrp uinput)

# Setup hotkey
./scripts/setup/setup-faster-hotkey.sh

# Test it
./bin/talk2claude-faster --check

# Detailed diagnostics without loading the model
./bin/talk2claude-faster --diagnose
```

#### Clipboard Mode
Clipboard is the default and requires no typing permissions:

```bash
# Install Python packages only
uv sync --locked --extra stt

# Run clipboard mode
./bin/talk2claude-faster

# Preview recognized text before copying
./bin/talk2claude-faster --preview
```

## Usage

### Basic Usage

```bash
# Terminal version - copies recognized text to the clipboard
./bin/talk2claude-faster

# Speak naturally, then paste the copied text with Ctrl+V
# Press Ctrl+C to stop
```

### With Hotkey (GNOME) - Clipboard Toggle

Set up a global hotkey to toggle dictation. The first press starts
capture; the second press stops capture, finalizes buffered speech, and copies
the transcription to the clipboard.

```bash
# Set up hotkey (choose from options)
./scripts/setup/setup-faster-hotkey.sh

# Default: Ctrl+Alt+V
# Press once to start background dictation
# Press again to stop
# Paste the result with Ctrl+V

# Script-friendly status
./bin/talk2claude-faster-toggle status --plain
./bin/talk2claude-faster-toggle status --json
```

### Live Developer Prompt Dictation

`lst-dictate` is for Claude Code, Codex CLI, terminals, and IDE prompt boxes.
It emits partial transcription while you are still talking, then replaces or
copies the final cleaned prompt when you stop capture.

```bash
# Capability and target/output check
./bin/lst-dictate --check

# Start/stop from the terminal; second run finalizes the prompt
./bin/lst-dictate --profile claude
./bin/lst-dictate --profile codex

# Useful modes
./bin/lst-dictate --output overlay   # live overlay, final text copied
./bin/lst-dictate --output paste     # final text pasted into active app
./bin/lst-dictate --output live-type # replace text live while speaking

# Script-friendly status and cleanup
./bin/lst-dictate status --plain
./bin/lst-dictate purge-state
```

`--output auto` live-types only when the detected or explicit target is high
confidence and direct input is available. Otherwise it uses an overlay and
copies the final prompt, which is safer on Wayland before uinput is configured.

Live typing also requires a stable window identity. The active window is
checked again before every backspace, paste, and optional submit. If focus
cannot be verified or moves to another window, dictation stops sending keys and
keeps the current/final prompt on the clipboard instead. The earlier preview
is left untouched in its original window because deleting it after focus has
moved would be unsafe. On Wayland, live typing therefore falls back unless the
desktop focus provider supplies a stable `window_id`.

`--submit always` and `--submit voice-command` only press Enter after a
successful `paste` or verified `live-type` insertion. Clipboard, overlay,
stdout, and live-type clipboard-fallback output never submit.

**Requirements for optional typing mode:**
- Wayland: `ydotool` (install: `sudo apt install ydotool`)
- X11: `xdotool` (install: `sudo apt install xdotool`)

**For Wayland users:** direct typing requires explicit uinput setup. Prefer the
installer/setup scripts over manually starting a privileged daemon. Clipboard
mode remains the default and does not require uinput access.

### Command Line Options

```bash
# Both versions support similar options

# Use a different model (tiny is fastest, large is most accurate)
./bin/talk2claude-faster --model tiny    # Fastest
./bin/talk2claude-faster --model base    # Default, balanced
./bin/talk2claude-faster --model large   # Most accurate

# Use a different language
./bin/talk2claude-faster --language es   # Spanish
./bin/talk2claude-faster --language fr   # French

# Adjust VAD sensitivity (talk2claude-faster only)
./bin/talk2claude-faster --vad 3         # More aggressive (0-3)

# Preview before output
./bin/talk2claude-faster --preview

# Purge stopped STT state/log files
./bin/talk2claude-faster --purge-state

# Measure selected model load time explicitly
./bin/talk2claude-faster --check --warm-model
```

### Environment Variables

```bash
# Set defaults via environment
export WHISPER_MODEL=base       # Model size
export WHISPER_COMPUTE_TYPE=int8 # Optional compute override
export WHISPER_VAD=2            # 0-3, env equivalent of --vad
export ASR_LANG=en              # Language, or auto
export DICTATION_MODE=clipboard # default; use typing only after setup
export DICTATION_PREVIEW=1      # preview before copying/typing
export STT_AUDIO_BACKEND=auto   # auto, pulse, pipewire, or alsa
export STT_AUDIO_DEVICE=default # ffmpeg input device
export STT_TRANSCRIPT_FALLBACK=1 # opt in to private file fallback when no clipboard tool exists
export PROMPT_DICTATION_PROFILE=auto    # claude, codex, ide, terminal, generic
export PROMPT_DICTATION_OUTPUT=auto     # overlay, paste, live-type, clipboard, stdout
export PROMPT_DICTATION_SUBMIT=never    # or voice-command/always
export STT_PARTIAL_SHUTDOWN_SECONDS=1.0 # bounded wait for partial inference shutdown
export STT_FINALIZE_LOCK_SECONDS=10.0   # bounded wait for final-quality transcription
export STT_TRANSCRIBE_TIMEOUT_SECONDS=30.0 # hard limit for final-quality ASR execution
```

The older `T2C_MODEL`, `T2C_LANG`, and `T2C_MODE` names are still accepted for
compatibility, but new installs should use the variables above.

## Platform-Specific Setup

### Wayland (Ubuntu 22.04+, Fedora)

For direct typing to work, you need `ydotool`:

```bash
# Install ydotool
sudo apt install ydotool  # Ubuntu
sudo dnf install ydotool  # Fedora

# Configure uinput permissions with the project setup helper
./scripts/setup/setup-uinput-permissions.sh

# Then log out/in, or run:
newgrp uinput
```

### X11 (Older systems)

For X11, you need `xdotool`:

```bash
sudo apt install xdotool
```

## How It Works

1. **You speak** into your microphone
2. **Whisper transcribes** after a pause or when you stop capture
3. **Text copies to clipboard** by default
4. **Press Ctrl+C** when done

For direct typing, run `./bin/talk2claude-faster --typing` or set
`DICTATION_MODE=typing` after completing the uinput/typing setup.

With `--preview` or `DICTATION_PREVIEW=1`, each recognized utterance is shown in
the terminal first. You can accept, edit, skip/retry, or cancel. In
non-interactive typing sessions, preview mode copies to the clipboard instead of
typing into the active window.

Dictated text is not printed into logs. If no clipboard tool is available, the
tool does not write transcripts to disk unless `STT_TRANSCRIPT_FALLBACK=1` is
set. That fallback keeps only the latest transcript in a private state file.

## Tips for Best Results

1. **Speak clearly** but naturally
2. **Use a good microphone** (headset works best)
3. **Minimize background noise**
4. **Start with the `base` model** for good balance
5. **Use `tiny` model** if you want fastest response

## Troubleshooting

Desktop/audio scenarios require manual verification. Use
`docs/developer/STT_MANUAL_QA_CHECKLIST.md` when testing real microphone,
clipboard, typing, and GNOME hotkey behavior.

### No audio detected
- Check microphone is connected: `arecord -l`
- Test microphone: `arecord -d 5 test.wav && aplay test.wav`
- Check permissions: Add user to `audio` group

### Import errors
```bash
# Install missing dependencies
uv sync --locked --extra stt
```

### ydotool not typing (Wayland)
```bash
# Check permissions/setup
./bin/talk2claude-faster --check

# Check the tool after setup
ydotool type "test"
```

### Model download stuck
The first run downloads the Whisper model (~140MB for base). Be patient.

### Performance issues
- Use `tiny` model for fastest response
- Close other heavy applications
- Ensure you have at least 4GB RAM free
- Run `./bin/talk2claude-faster --diagnose` to confirm backend/tool selection
- Run `./bin/talk2claude-faster --check --warm-model` only when you explicitly
  want to measure model load time

## Differences from Classic talk2claude

| Feature | talk2claude | talk2claude-faster |
|---------|------------|---------------------|
| Wait time | 8+ seconds | Processes after pauses/finalization |
| Feedback | After recording | Listening/processing status during speaking |
| Recording | Fixed duration | Manual stop |
| Output | Clipboard+paste | Clipboard by default; preview/direct typing opt-in |
| Resource use | Low | Medium |

## Examples

### Dictating a message
```bash
./bin/talk2claude-faster
# Start speaking: "Hey John, just wanted to check if you're free for lunch tomorrow."
# Text copies to clipboard; paste it into your email client
# Press Ctrl+C when done
```

### Writing code comments
```bash
./bin/talk2claude-faster --model base
# In VS Code, position cursor where you want the comment
# Speak: "This function calculates the fibonacci sequence recursively"
# Paste the copied comment into your code
```

### Quick note in another language
```bash
./bin/talk2claude-faster --language es
# Speak in Spanish: "Recordar comprar leche"
# Spanish text is copied correctly
```

## Limitations

- **No wake words** - Must use keyboard/hotkey
- **Linux only** - No Windows/Mac support
- **Terminal preview only** - Editing before output requires `--preview`

## Feedback

This is an MVP (v1.0). We'd love your feedback!

- Does it feel better than classic talk2claude?
- What features do you want most?
- Any bugs or issues?

Report at: https://github.com/pablopda/linux-speech-tools/issues

## Future Plans (Based on Feedback)

- **v1.1**: More diagnostics and manual QA coverage
- **v2.0**: Wake word activation (if requested)

---

**Remember**: This is about making speech-to-text feel instant. Simple, focused, magical. 🎯
