# Linux Speech Tools

Professional text-to-speech and voice input tools for Linux systems. Multi-engine TTS, voice recording, and cross-platform compatibility.

[![CI/CD Pipeline](https://github.com/pablopda/linux-speech-tools/workflows/CI/CD%20Pipeline/badge.svg)](https://github.com/pablopda/linux-speech-tools/actions)
[![Release](https://img.shields.io/github/v/release/pablopda/linux-speech-tools)](https://github.com/pablopda/linux-speech-tools/releases)
[![License](https://img.shields.io/github/license/pablopda/linux-speech-tools)](LICENSE)

## 🚀 Quick Installation

```bash
curl -fsSL https://raw.githubusercontent.com/pablopda/linux-speech-tools/master/installer.sh | bash
```

## ✨ Features

### 🎙️ **Multi-Engine Text-to-Speech**
- **Edge TTS**: High-quality cloud-based synthesis with 22-country LATAM regional voice support
- **Kokoro TTS**: Offline neural voice synthesis
- **Festival TTS**: Local fallback engine
- **Graceful fallbacks**: Automatic engine switching for maximum reliability

### 🗣️ **Voice Input & Recording**
- **Toggle recording**: Press once to start, again to stop (default mode)
- **Speech-to-text**: Powered by OpenAI Whisper for accurate transcription
- **Auto-clipboard**: Transcription automatically copied to clipboard
- **GNOME integration**: Global hotkey (Ctrl+Alt+V) for system-wide voice input
- **Smart detection**: Terminal vs GUI application handling

### 🎵 **Enhanced Audio Streaming** ⭐ NEW
- **Continuous playback**: Eliminates gaps between audio chunks
- **Professional quality**: Broadcast-level smooth TTS streaming
- **Smart concatenation**: Uses ffmpeg/sox for seamless audio joining
- **Multiple modes**: Continuous, buffered, and original streaming options
- **Drop-in replacement**: Enhanced versions of existing commands

### 🖥️ **Command-Line Tools**
- `say` - Text-to-speech with file output support
- `say-local` - Local TTS using Festival/Kokoro
- `say-read` - Read URLs, PDFs, and documents with TTS
- `say-read-es` - Spanish language content reader
- `talk2claude` - Voice input with transcription

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

**GNOME Integration (Recommended):**
```bash
# Install GNOME integration
./install-gnome-integration.sh

# Use system-wide hotkey: Ctrl+Alt+V
# Press once → Start recording
# Press again → Stop and transcribe
```

**Command Line:**
```bash
# Toggle mode (default)
./toggle-speech.sh toggle    # Start/stop recording
./toggle-speech.sh start     # Start only
./toggle-speech.sh stop      # Stop only

# Fixed duration mode
./simple-speech.sh 5         # 5-second recording

# Original talk2claude (advanced)
talk2claude                  # 8-second recording
talk2claude start           # Background recording
talk2claude stop            # Stop and transcribe
```

### 📖 Content Reading

**🎵 Enhanced: Continuous Streaming (NEW)**
```bash
# Smooth, gap-free audio streaming
./say-read-continuous https://example.com/article

# Professional-quality playback for long content
./say-read-smooth --buffered https://en.wikipedia.org/wiki/Linux

# Interactive demo showing improvement
./demo-audio-streaming.sh
```

**📚 Standard Reading**
```bash
# Read web articles
say-read https://example.com/article

# Read PDF documents
say-read document.pdf

# Read with Spanish voice
say-read-es https://elpais.com/tecnologia/
```

## 🔧 Installation Methods

### Option 1: One-Command Install (Recommended)
```bash
curl -fsSL https://raw.githubusercontent.com/pablopda/linux-speech-tools/master/installer.sh | bash
```

### Option 2: Manual Installation
```bash
git clone https://github.com/pablopda/linux-speech-tools.git
cd linux-speech-tools
./installer.sh
```

### Option 3: Package Installation
Download packages from [Releases](https://github.com/pablopda/linux-speech-tools/releases):

**Ubuntu/Debian:**
```bash
wget https://github.com/pablopda/linux-speech-tools/releases/download/v1.0.0/linux-speech-tools_1.0.0.deb
sudo dpkg -i linux-speech-tools_1.0.0.deb
```

**Fedora/RHEL:**
```bash
wget https://github.com/pablopda/linux-speech-tools/releases/download/v1.0.0/linux-speech-tools-1.0.0-1.noarch.rpm
sudo rpm -i linux-speech-tools-1.0.0-1.noarch.rpm
```

## ⚙️ Configuration

### Voice Configuration
Create `~/.config/speech-tools/config`:
```bash
# Default voice for Edge TTS
EDGE_VOICE=en-US-EmmaMultilingualNeural

# Voice input settings
ASR_LANG=en
WHISPER_MODEL=large-v3
```

### Available Voices
```bash
# List Edge TTS voices
edge-tts --list-voices | grep -E "(Male|Female)"

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
# Install Python dependencies manually
pip3 install edge-tts pyaudio speechrecognition

# Install system dependencies
sudo apt install python3-pip ffmpeg espeak-ng portaudio19-dev  # Ubuntu/Debian
sudo dnf install python3-pip ffmpeg espeak-ng portaudio-devel  # Fedora
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
python3 tests/test_speech_tools.py

# Quick validation
./scripts/quick-release-check.sh

# Comprehensive validation
./scripts/pre-release-check.sh
```

### Creating Releases
```bash
# Patch release (1.0.0 -> 1.0.1)
./release.sh patch

# Minor release (1.0.0 -> 1.1.0)
./release.sh minor

# Preview release
./release.sh patch --dry-run
```

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](.github/CONTRIBUTING.md) for details.

### Quick Start for Contributors
```bash
git clone https://github.com/pablopda/linux-speech-tools.git
cd linux-speech-tools

# Install development dependencies
./installer.sh

# Run tests
python3 tests/test_speech_tools.py

# Submit changes
git checkout -b feature/your-feature
# Make changes
./scripts/quick-release-check.sh
git commit -m "Add your feature"
git push origin feature/your-feature
# Create pull request
```

## 📋 Requirements

### System Requirements
- **OS**: Linux (Ubuntu 20.04+, Debian 11+, Fedora 38+)
- **Python**: 3.7+
- **Audio**: PulseAudio or ALSA
- **Network**: Internet connection for Edge TTS

### Dependencies
- `python3-pip`
- `ffmpeg`
- `espeak-ng`
- `portaudio19-dev` (Ubuntu/Debian) or `portaudio-devel` (Fedora)

All dependencies are automatically installed by the installer script.

## 📚 Documentation

- [Installation Guide](.github/CONTRIBUTING.md#development-environment)
- [API Documentation](docs/api.md) *(coming soon)*
- [Voice Configuration Guide](docs/voices.md) *(coming soon)*
- [Troubleshooting Guide](docs/troubleshooting.md) *(coming soon)*

## 📊 Project Status

- ✅ **Production Ready**: Comprehensive testing across multiple distributions
- ✅ **Actively Maintained**: Regular updates and improvements
- ✅ **Community Driven**: Open to contributions and feature requests
- ✅ **Professional Quality**: Enterprise-grade CI/CD and release automation

## 🔗 Links

- **Repository**: https://github.com/pablopda/linux-speech-tools
- **Releases**: https://github.com/pablopda/linux-speech-tools/releases
- **Issues**: https://github.com/pablopda/linux-speech-tools/issues
- **Discussions**: https://github.com/pablopda/linux-speech-tools/discussions

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- OpenAI Whisper for speech recognition
- Microsoft Edge TTS for cloud synthesis
- Kokoro ONNX for offline synthesis
- Festival Speech Synthesis System
- The open-source Linux community

---

**Made with ❤️ for the Linux community**

*Professional speech tools that just work.* 🐧🎙️