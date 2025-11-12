#!/usr/bin/env bash
# Simplified speech-to-clipboard that bypasses complex notification handling

set -euo pipefail

# Simple notification function
notify() {
    notify-send "Voice Input" "$1" --icon=audio-input-microphone-symbolic 2>/dev/null || echo "Voice: $1"
}

# Record and transcribe with simpler logic
record_and_transcribe() {
    local duration=${1:-5}
    local cache_dir="$HOME/.cache/simple-speech"
    local audio_file="$cache_dir/recording.wav"

    mkdir -p "$cache_dir"

    notify "Recording ${duration}s... Speak now!"

    # Record audio
    timeout $((duration + 5)) ffmpeg -f pulse -i default -t "$duration" -ac 1 -ar 16000 -c:a pcm_s16le -y "$audio_file" -v quiet 2>/dev/null || {
        notify "Recording failed"
        return 1
    }

    if [ ! -s "$audio_file" ]; then
        notify "No audio recorded"
        return 1
    fi

    notify "Processing speech..."

    # Transcribe
    local stt_python="$HOME/.venvs/stt/bin/python"
    local text
    text=$("$stt_python" -c "
from faster_whisper import WhisperModel
m = WhisperModel('large-v3', device='cpu', compute_type='int8')
segments, info = m.transcribe('$audio_file', beam_size=5)
print(' '.join(s.text.strip() for s in segments).strip())
" 2>/dev/null) || {
        notify "Speech recognition failed"
        return 1
    }

    if [ -z "${text// }" ]; then
        notify "No speech detected"
        return 1
    fi

    # Copy to clipboard
    if command -v wl-copy >/dev/null 2>&1; then
        printf '%s' "$text" | wl-copy
    elif command -v xclip >/dev/null 2>&1; then
        printf '%s' "$text" | xclip -selection clipboard
    else
        notify "Clipboard tool not found"
        return 1
    fi

    notify "Copied: \"$text\""
    echo "Transcribed: $text"

    # Cleanup
    rm -f "$audio_file"
}

# Main
case "${1:-5}" in
    [0-9]*) record_and_transcribe "$1" ;;
    *) echo "Usage: $0 [seconds]"; echo "Example: $0 5  # Record for 5 seconds" ;;
esac