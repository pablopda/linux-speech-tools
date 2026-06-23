# talk2claude-faster - Low-Latency Speech-to-Text

A streamlined speech-to-text tool that uses `faster-whisper` directly and transcribes after pauses or finalization.

## Features

- 🚀 **Low-latency transcription** - Speech is transcribed after pauses or finalization
- 📋 **Clipboard-first** - Text copies to your clipboard by default
- ⌨️ **Optional direct typing** - Text can type into active applications after explicit setup
- 📦 **Minimal dependencies** - Uses uv extras; some systems may build the VAD package locally
- 🎯 **Simple setup** - `uv` installs Python deps; sudo is only needed for optional direct typing permissions
- 🎤 **Smart VAD** - WebRTC Voice Activity Detection
- 🔧 **No PyAudio** - Uses ffmpeg for audio capture
- 🎹 **Hotkey support** - Toggle with Ctrl+Alt+V

## Quick Start

```bash
# Complete setup (recommended)
./installer.sh --with-stt --download-models --whisper-model tiny

# This will:
# 1. Install faster-whisper/webrtcvad through uv
# 2. Prefetch the selected faster-whisper model
# 3. Install launchers and runtime config
```

**Or manual setup:**
```bash
# Install Python packages
uv sync --locked --extra stt

# Optional: setup typing permissions (one-time, logout/login required)
./installer.sh --with-stt --setup-uinput

# Setup hotkey
./scripts/setup/setup-faster-hotkey.sh

# Test it
./bin/talk2claude-faster --check
```

## How It Works

### Default: Clipboard Mode
1. **ffmpeg** captures audio from your microphone
2. **WebRTC VAD** detects when you start/stop speaking
3. **faster-whisper** transcribes the audio
4. Text copies to clipboard for manual pasting

**Usage:** Press your hotkey once → Speak → Press again → Paste with Ctrl+V.

### Optional: Direct Typing Mode
1. **ffmpeg** captures audio from your microphone
2. **WebRTC VAD** detects when you start/stop speaking
3. **faster-whisper** transcribes the audio
4. Text types directly into your active window

Direct typing requires `--typing` or `DICTATION_MODE=typing` plus the
permissions documented in [docs/TYPING_PERMISSIONS.md](docs/TYPING_PERMISSIONS.md).

## Options

```bash
# Mode selection
./bin/talk2claude-faster                 # Clipboard mode by default
./bin/talk2claude-faster --typing        # Force typing mode
./bin/talk2claude-faster --clipboard     # Force clipboard mode
DICTATION_MODE=clipboard ./bin/talk2claude-faster  # Environment variable

# Check capabilities
./bin/talk2claude-faster --check         # See what modes are available

# Model selection (tiny = fast, large = accurate)
./bin/talk2claude-faster --model tiny    # 39 MB, fastest
./bin/talk2claude-faster --model base    # 74 MB, balanced
./bin/talk2claude-faster --model small   # 244 MB, accurate

# Language
./bin/talk2claude-faster --language es   # Spanish
./bin/talk2claude-faster --language fr   # French

# VAD sensitivity (0-3, higher = more aggressive)
./bin/talk2claude-faster --vad 3
```

## ASR engines (faster-whisper default, Parakeet optional)

Dictation runs behind a pluggable engine layer (`src/stt/asr_engine.py`). The
default is **faster-whisper**; **NVIDIA Parakeet** (TDT 0.6B v3, via the MIT
`onnx-asr` runtime) is an opt-in alternative with native punctuation and
capitalization plus strong English/GPU throughput.

> **LATAM Spanish:** faster-whisper stays the recommended default. Parakeet v3's
> Spanish leans European/Castilian; validate it on real LATAM audio (see the
> benchmark in `docs/developer/STT_MANUAL_QA_CHECKLIST.md`) before relying on it
> for Spanish.

**Enable Parakeet (opt-in, Python ≥3.10):**
```bash
uv sync --locked --extra stt --extra stt-parakeet
./bin/linux-speech-tools-setup --parakeet        # prefetch the ONNX model (~640 MB)
STT_ENGINE=parakeet ./bin/talk2claude-faster     # or: --engine parakeet
```

**Select per run:**
```bash
./bin/talk2claude-faster --engine faster-whisper  # default
./bin/talk2claude-faster --engine parakeet
STT_ENGINE=parakeet ./bin/talk2claude-faster      # environment equivalent
```

**Engine configuration:**

| Variable | Default | Meaning |
| --- | --- | --- |
| `STT_ENGINE` | `faster-whisper` | `faster-whisper` or `parakeet` |
| `STT_PARAKEET_MODEL` | `nemo-parakeet-tdt-0.6b-v3` | onnx-asr model name (`…-v2` is English-only) |
| `STT_PARAKEET_QUANTIZATION` | `int8` | `int8` (~640 MB) or `none`/`fp32` for full precision |

Parakeet auto-detects language (the `--language` hint is ignored for it). On CPU
it is mainly a *punctuation + robustness* win; the large speed gains need an
NVIDIA GPU — and GPU requires installing `onnxruntime-gpu` separately (the
`stt-parakeet` extra ships CPU onnxruntime, so `--device cuda` cleanly falls back
to CPU otherwise). faster-whisper remains untouched and the default.

## Why talk2claude-faster?

We discovered that RealtimeSTT is essentially a complex wrapper around faster-whisper. By using faster-whisper directly:

- ✅ No PyAudio compilation issues
- ✅ No system packages needed (except ffmpeg)
- ✅ Simpler, cleaner codebase
- ✅ Same transcription quality
- ✅ Lower resource usage

## Requirements

- Linux (Ubuntu/Debian/Fedora)
- Python 3.8+
- ffmpeg (system package)
- Microphone
- PulseAudio/PipeWire or ALSA input. Override with `STT_AUDIO_BACKEND`
  (`auto`, `pulse`, `pipewire`, `alsa`) and `STT_AUDIO_DEVICE` when needed.

## Installation

```bash
# System dependency (usually already installed)
sudo apt install ffmpeg  # Ubuntu/Debian
sudo dnf install ffmpeg  # Fedora

# Python packages
uv sync --locked --extra stt
```

## Architecture

```
Microphone → ffmpeg → WebRTC VAD → ASR engine (faster-whisper | Parakeet) → Text Output
```

Simple, clean, effective.

## Files

- `bin/talk2claude-faster` - Main launcher script
- `src/stt/faster_whisper_auto.py` - Selects clipboard by default, typing by opt-in
- `src/stt/session.py` - Shared dictation core (VAD, buffering, finalize)
- `src/stt/asr_engine.py` - Pluggable ASR backends (faster-whisper, Parakeet)
- `src/utils/setup_models.py` - Model prefetch/check helper

## Troubleshooting

### No audio detected
```bash
# Test microphone
arecord -l  # List devices
arecord -d 5 test.wav && aplay test.wav  # Test recording
```

### Import errors
```bash
uv sync --locked --extra stt
```

### ffmpeg not found
```bash
sudo apt install ffmpeg  # Ubuntu/Debian
sudo dnf install ffmpeg  # Fedora
```

## License

Same as linux-speech-tools project.
