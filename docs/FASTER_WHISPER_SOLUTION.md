# Real-time Speech-to-Text Solution Analysis

## Key Discovery: RealtimeSTT Architecture

After analyzing the RealtimeSTT source code, we discovered it's essentially a sophisticated wrapper around:

1. **faster-whisper** - The actual transcription engine
2. **webrtcvad** - Voice Activity Detection (WebRTC)
3. **PyAudio** - Audio capture (requires compilation with portaudio19-dev)
4. **SileroVAD** - Secondary VAD for verification
5. **OpenWakeWord/Porcupine** - Wake word detection

## The Problem with RealtimeSTT

RealtimeSTT requires PyAudio, which needs:
- `portaudio19-dev` (system package with C headers)
- `python3-dev` (Python C headers)
- Compilation during installation

This creates dependency hell and installation complexity.

## Our Solution: Direct faster-whisper Usage

We created a simpler implementation that:
- Uses **faster-whisper** directly (already installed)
- Uses **webrtcvad** for voice activity detection (pure Python)
- Uses **ffmpeg** for audio capture (PulseAudio/PipeWire first, ALSA fallback)
- Copies to clipboard by default, with direct typing only as an explicit opt-in
- Supports preview mode before clipboard or typing output
- Keeps dictated text out of logs and only writes transcript files when the user
  explicitly enables fallback storage
- **No PyAudio needed!**
- **No PyAudio required.** Some systems may still compile the VAD dependency if a wheel is unavailable.

## Implementation Files

### 1. `src/stt/session.py`
Shared dictation session core:
- loads the faster-whisper model
- captures audio through ffmpeg
- runs WebRTC VAD
- buffers speech and finalizes on pauses or `SIGINT`
- writes machine-readable status

### 2. Output adapters

- `src/stt/faster_whisper_clipboard.py` handles clipboard/private-file output,
  preview mode, and privacy policy.
- `src/stt/faster_whisper_typing.py` handles direct typing and clipboard
  fallback.
- `src/stt/faster_whisper_auto.py` dispatches modes and provides diagnostics.

### 3. `bin/talk2claude-faster`
Simple launcher script that:
- Uses the uv-managed `stt` extra from this project
- Requires only: faster-whisper, webrtcvad, ffmpeg
- No complex dependencies

## Performance Comparison

| Aspect | RealtimeSTT | Our Solution |
|--------|------------|--------------|
| Dependencies | PyAudio, portaudio19-dev, compilation | ffmpeg (already installed) |
| Installation | Complex, often fails | `uv sync --locked --extra stt` |
| CPU Usage | Higher (multiple VADs) | Lower (single VAD) |
| Accuracy | Slightly better | Good enough |
| Maintenance | Complex | Simple |

## How It Works

1. **Audio Capture**: ffmpeg reads from microphone → raw PCM stream
2. **Voice Detection**: WebRTC VAD detects speech start/end
3. **Buffering**: Collect audio during speech
4. **Transcription**: faster-whisper transcribes when speech ends
5. **Preview**: optional review/edit/skip/cancel prompt before output
6. **Output**: text copied to clipboard by default; direct typing is opt-in
7. **Status**: private JSON state tracks idle/listening/recording/processing/finalizing/error

## Installation

```bash
# Install Python packages
uv sync --locked --extra stt

# Optional: prefetch a model
linux-speech-tools-setup --stt --whisper-model tiny
```

Audio capture defaults to `STT_AUDIO_BACKEND=auto`. Override it for specific
systems or devices:

```bash
STT_AUDIO_BACKEND=alsa STT_AUDIO_DEVICE=hw:1,0 ./bin/talk2claude-faster
STT_AUDIO_BACKEND=pulse ./bin/talk2claude-faster
```

## Usage

```bash
# Test installation
./bin/talk2claude-faster --check

# Detailed capability diagnostics without loading the model
./bin/talk2claude-faster --diagnose

# Run with default settings (tiny model, English)
./bin/talk2claude-faster

# Use larger model for better accuracy
./bin/talk2claude-faster --model base

# Different language
./bin/talk2claude-faster --language es

# Adjust VAD sensitivity (0-3, higher = more aggressive)
./bin/talk2claude-faster --vad 3

# Preview before copying or typing
./bin/talk2claude-faster --preview

# Check machine-readable hotkey status
./bin/talk2claude-faster-toggle status --json

# Purge stopped STT state/log files
./bin/talk2claude-faster --purge-state

# Explicitly time model load
./bin/talk2claude-faster --check --warm-model
```

If no clipboard tool is available, transcript file fallback is disabled by
default. Set `STT_TRANSCRIPT_FALLBACK=1` to write only the latest transcript to a
private state file.

`--check` and `--diagnose` do not load models by default. Use `--warm-model` only
when you intentionally want to load the selected model and measure startup
latency.

## Models Available

- `tiny` - Fastest, least accurate (39 MB)
- `tiny.en` - English-only tiny (39 MB)
- `base` - Good balance (74 MB)
- `base.en` - English-only base (74 MB)
- `small` - Better accuracy (244 MB)
- `medium` - High accuracy (769 MB)
- `large-v3` - Best accuracy (1550 MB)

## Conclusion

By understanding that RealtimeSTT is mainly a wrapper around faster-whisper, we created a simpler solution that:
- ✅ Works with existing dependencies
- ✅ No PyAudio required; VAD may build locally on some platforms
- ✅ Easy to install and maintain
- ✅ Good enough accuracy for dictation
- ✅ Lower resource usage

The key insight: **We don't need the complex wrapper when we can use the core components directly!**
