# Linux Speech Tools

Practical text-to-speech and voice input tools for Linux systems. The project focuses on user-local command-line workflows for Edge TTS, Kokoro read-aloud, and faster-whisper dictation.

[![CI/CD Pipeline](https://github.com/pablopda/linux-speech-tools/workflows/CI/CD%20Pipeline/badge.svg)](https://github.com/pablopda/linux-speech-tools/actions)
[![Release](https://img.shields.io/github/v/release/pablopda/linux-speech-tools)](https://github.com/pablopda/linux-speech-tools/releases)
[![License](https://img.shields.io/github/license/pablopda/linux-speech-tools)](LICENSE)

## 🚀 Quick Installation

```bash
git clone https://github.com/pablopda/linux-speech-tools.git
cd linux-speech-tools
./installer.sh --with-kokoro --with-stt --download-models
```

The installer uses `uv` for Python dependencies and keeps system/model setup
explicit. See [docs/INSTALLATION.md](docs/INSTALLATION.md) for profiles such as
core-only, Kokoro read-aloud, faster-whisper dictation, GNOME, and direct typing.

## ✨ Features

### 🎙️ **Text-to-Speech**
- **Edge TTS**: High-quality cloud-based synthesis with 22-country LATAM regional voice support
- **Kokoro TTS**: Local/offline neural speech for read-aloud workflows
- **Explicit engines**: Use `say` for Edge TTS and `say-local`/`say-read` for Kokoro

### 🗣️ **Voice Input & Recording**
- **Toggle recording**: Press once to start, again to stop (default mode)
- **Speech-to-text**: Powered by faster-whisper (OpenAI Whisper models) for accurate transcription, with an optional NVIDIA Parakeet engine (native punctuation; English/GPU speed)
- **Auto-clipboard**: Transcription automatically copied to clipboard
- **GNOME integration**: Global hotkey (Ctrl+Alt+V) for system-wide voice input
- **Direct typing is opt-in**: Clipboard mode is the safe default

### 🚀 **Pause-Triggered Dictation**
- **talk2claude-faster**: Low-latency speech-to-text using faster-whisper
- **Clipboard-first output**: Safe default that works without typing permissions
- **Optional direct typing**: Enable explicitly with `--typing` or `DICTATION_MODE=typing`
- **Minimal setup**: Uses uv extras plus system audio tools; some platforms may build VAD wheels locally
- **WebRTC VAD**: Accurate voice activity detection
- **Multiple models**: From tiny (39MB) to large (1.5GB)

### 🎵 **Read-Aloud Streaming**
- **Continuous playback**: Eliminates gaps between audio chunks
- **Low-latency streaming**: Uses a single `ffplay` stream when available, with player fallbacks
- **Compatibility modes**: Legacy wrappers forward to the maintained reader

### 🎮 **GNOME Controls Beta**
- **Notification controls**: Play/pause/stop from notification panel where supported
- **Real-time progress**: Visual progress tracking for reading sessions
- **Document information**: Display source title and reading status
- **Beta status**: This is notification/D-Bus integration, not full MPRIS media-player integration

### 🖥️ **Command-Line Tools**
- `say` - Text-to-speech with file output support
- `say-local` - Local TTS using Kokoro
- `say-read` - Read URLs, PDFs, and documents with TTS
- `talk2claude-faster` - Clipboard-first faster-whisper dictation
- `talk2claude` - Voice input with transcription
- `gnome-dictation` - GNOME hotkey wrapper for dictation
- `linux-speech-tools-setup` - Model setup and checks

### 🐧 **Cross-Platform Linux Support**
- **Ubuntu** 20.04, 22.04
- **Debian** 11, 12
- **Fedora** 38, 39
- **Automatic dependency detection** and installation
- **XDG-compliant** configuration management

## 📖 Usage Examples

### Basic Text-to-Speech
```bash
# Simple speech
say "Hello from Linux Speech Tools!"

# Spanish voice
say -v es-ES-AlvaroNeural "¡Hola mundo!"

# Save to file
say -o greeting.mp3 "Welcome to our application"

# Show available options
say --help
```

### 🎤 Voice Input

**GNOME Integration:**
```bash
# Install GNOME integration
./scripts/install/install-gnome-integration.sh

# Use system-wide hotkey: Ctrl+Alt+V
# Press once → Start recording
# Press again → Stop and transcribe
```

**Command Line:**
```bash
# Low-latency dictation
talk2claude-faster           # Copies text to clipboard
talk2claude-faster --model base  # Better accuracy
talk2claude-faster --check   # Test capabilities

# Toggle mode (default)
talk2claude-faster-toggle    # Start/stop; second press finalizes buffered speech

# Original talk2claude (advanced)
talk2claude                  # 8-second recording
talk2claude start           # Background recording
talk2claude stop            # Stop and transcribe
```

### 📖 Content Reading

**🎵 Continuous Streaming**
```bash
# Smooth, gap-free audio streaming
say-read https://example.com/article

# Interactive demo showing improvement
./examples/demos/demo-audio-streaming.sh
```

**🎮 GNOME Notification Controls (Beta)**
```bash
# Reading with desktop media controls
say-read-gnome https://www.bbc.com/news/technology

# Control playback from notification panel:
# ⏸️ Pause - Click to pause reading
# ▶️ Resume - Click to resume reading
# ⏹️ Stop - Click to stop completely

# Setup GNOME integration (first time)
say-read-gnome --setup

# Interactive demo and testing
./examples/demos/demo-gnome-media-integration.sh
```

**📚 Standard Reading**
```bash
# Read web articles
say-read https://example.com/article

# Read PDF documents
say-read document.pdf

# Read with Spanish voice
say-read -l es -v ef_dora https://elpais.com/tecnologia/
```

## 🔧 Installation Methods

### Option 1: Checkout Install (Recommended)
```bash
git clone https://github.com/pablopda/linux-speech-tools.git
cd linux-speech-tools
./installer.sh --with-kokoro --with-stt --download-models
```

### Option 2: Profile-Based Install
```bash
# Core Edge TTS only
./installer.sh

# Offline Kokoro read-aloud
./installer.sh --with-kokoro --download-models

# faster-whisper dictation
./installer.sh --with-stt --download-models --whisper-model tiny

# All runtime features
./installer.sh --all --download-models
```

### Option 3: Streamed Install
```bash
curl -fsSL https://raw.githubusercontent.com/pablopda/linux-speech-tools/v1.0.2/installer.sh | bash
```

The command above streams the bootstrap script from a pinned release tag
(`v1.0.2`) rather than the mutable `main` branch. As with any
`curl | bash` install, download and inspect the script before piping it to a
shell if you prefer:

```bash
curl -fsSL https://raw.githubusercontent.com/pablopda/linux-speech-tools/v1.0.2/installer.sh -o installer.sh
less installer.sh   # review, then run
bash installer.sh
```

The streamed installer downloads the project source to
`~/.local/share/linux-speech-tools/source` and then runs the same profile-based
installer used by checkout installs. The default streamed install verifies the
pinned release tarball via SHA256; custom tarball URLs or refs must set
`LST_INSTALLER_SHA256`.

### Option 4: Manual uv Commands
```bash
uv sync --locked --extra kokoro --extra read --extra stt
uv run --extra kokoro --extra read python src/tts/say_read.py --help
uv run --extra stt python -m src.stt.faster_whisper_auto --check
uv run --extra kokoro --extra stt python -m src.utils.setup_models --check
```

Native `.deb` and `.rpm` packaging is still experimental; use the checkout
installer until the distro package layout is updated.

## ⚙️ Configuration

### Voice Configuration
The installer writes `~/.config/linux-speech-tools/install.env` with private
permissions. Advanced users can edit that file directly:
```bash
# Default voice for Edge TTS
EDGE_VOICE=en-US-EmmaMultilingualNeural

# Voice input settings
ASR_LANG=en
WHISPER_MODEL=tiny
WHISPER_VAD=2             # 0-3, env equivalent of --vad
DICTATION_MODE=clipboard  # or typing
STT_AUDIO_BACKEND=auto    # auto, pulse, pipewire, or alsa
STT_AUDIO_DEVICE=default  # ffmpeg input device
STT_ENGINE=faster-whisper # or 'parakeet' (opt-in; see README_FASTER.md)
```

### Available Voices
```bash
# List Edge TTS voices from the uv project environment
uv run edge-tts --list-voices | grep -E "(Male|Female)"

# Test different voices
say -v en-GB-SoniaNeural "British English"
say -v es-MX-DaliaNeural "Mexican Spanish"
say -v pt-BR-AntonioNeural "Brazilian Portuguese"
```

## 🔍 Troubleshooting

### Audio Issues
```bash
# Test audio output
say "Audio test"

# Check audio devices
pactl list short sinks

# Install audio dependencies
sudo apt install pulseaudio-utils  # Ubuntu/Debian
sudo dnf install pulseaudio-utils  # Fedora
```

### Dependency Issues
```bash
# Install Python dependencies through uv profiles
uv sync --locked --extra kokoro --extra read --extra stt

# Install system dependencies
sudo apt install python3 ffmpeg espeak-ng wl-clipboard xclip  # Ubuntu/Debian
sudo dnf install python3 ffmpeg espeak-ng wl-clipboard xclip  # Fedora

# Check or install model assets
linux-speech-tools-setup --check
linux-speech-tools-setup --kokoro
```

### Permission Issues
```bash
# Make scripts executable
chmod +x ~/.local/bin/{say,say-local,talk2claude}

# Add to PATH if needed
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

## 🧪 Development

### Running Tests
```bash
# Run full test suite
uv run pytest tests/ -v

# Shell syntax checks
bash -n bin/say bin/say-local bin/say-read bin/talk2claude

# Comprehensive validation
./scripts/release/pre-release-check.sh
```

### Creating Releases
```bash
# Patch release (1.0.0 -> 1.0.1)
./scripts/release/release.sh patch

# Minor release (1.0.0 -> 1.1.0)
./scripts/release/release.sh minor

# Preview release
./scripts/release/release.sh patch --dry-run
```

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](.github/CONTRIBUTING.md) for details.

### Quick Start for Contributors
```bash
git clone https://github.com/pablopda/linux-speech-tools.git
cd linux-speech-tools

# Install development dependencies
uv sync --locked --extra kokoro --extra read --extra stt --dev

# Run tests
uv run pytest tests/ -v

# Submit changes
git checkout -b feature/your-feature
# Make changes
git commit -m "Add your feature"
git push origin feature/your-feature
# Create pull request
```

## 📋 Requirements

### System Requirements
- **OS**: Linux (Ubuntu 20.04+, Debian 11+, Fedora 38+)
- **Python**: 3.8+
- **Audio**: PulseAudio/PipeWire or ALSA
- **Network**: Internet connection for Edge TTS

### Dependencies
- `uv`
- `ffmpeg`
- `espeak-ng`
- Optional: `wl-clipboard` or `xclip` for dictation clipboard mode
- Optional: `/dev/uinput` permissions for direct typing mode

The installer handles common system packages and `uv` profiles. Model files are
installed or checked separately with `linux-speech-tools-setup`.

## 📚 Documentation

- [Installation Guide](docs/INSTALLATION.md)
- [Faster Whisper Quickstart](docs/FASTER_QUICKSTART.md)
- [Typing Permissions](docs/TYPING_PERMISSIONS.md)
- [Packaging Notes](docs/PACKAGING.md)

## 📊 Project Status

- **Core CLI**: Active stabilization
- **Checkout installer**: Primary supported install path
- **Streamed installer**: Supported with checksum verification
- **Native packages**: Beta/experimental

## 🔗 Links

- **Repository**: https://github.com/pablopda/linux-speech-tools
- **Releases**: https://github.com/pablopda/linux-speech-tools/releases
- **Issues**: https://github.com/pablopda/linux-speech-tools/issues
- **Discussions**: https://github.com/pablopda/linux-speech-tools/discussions

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- OpenAI Whisper for speech recognition
- NVIDIA Parakeet (TDT 0.6B v3, CC-BY-4.0) — optional ASR engine
- onnx-asr (MIT) — the ONNX runtime that hosts Parakeet
- Microsoft Edge TTS for cloud synthesis
- Kokoro ONNX for offline synthesis
- Festival Speech Synthesis System
- The open-source Linux community

---

*Linux speech tools for local command-line workflows.*
