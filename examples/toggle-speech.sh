#!/usr/bin/env bash
# Toggle speech recording - press once to start, again to stop

set -euo pipefail

# Configuration
CACHE_DIR="$HOME/.cache/toggle-speech"
AUDIO_FILE="$CACHE_DIR/recording.wav"
PID_FILE="$CACHE_DIR/recording.pid"
STATUS_FILE="$CACHE_DIR/status"

mkdir -p "$CACHE_DIR"

# Notification function
notify() {
    notify-send "Voice Toggle" "$1" --icon=audio-input-microphone-symbolic 2>/dev/null || echo "Voice: $1"
}

# Check if currently recording
is_recording() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE" 2>/dev/null || echo "")
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0  # Recording
        else
            # Stale PID file
            rm -f "$PID_FILE" "$STATUS_FILE"
            return 1  # Not recording
        fi
    fi
    return 1  # Not recording
}

# Start recording
start_recording() {
    if is_recording; then
        notify "Already recording! Press hotkey again to stop."
        return 0
    fi

    # Clean up any old files
    rm -f "$AUDIO_FILE" "$PID_FILE" "$STATUS_FILE"

    notify "🔴 Recording started... Press hotkey again to stop and transcribe"

    # Start background recording
    ffmpeg -f pulse -i default -ac 1 -ar 16000 -c:a pcm_s16le -y "$AUDIO_FILE" -v quiet 2>/dev/null &
    local ffmpeg_pid=$!

    echo "$ffmpeg_pid" > "$PID_FILE"
    echo "recording" > "$STATUS_FILE"

    # Optional: Auto-stop after maximum time (e.g., 60 seconds)
    (
        sleep 60
        if is_recording && [ "$(cat "$PID_FILE" 2>/dev/null)" = "$ffmpeg_pid" ]; then
            kill "$ffmpeg_pid" 2>/dev/null || true
            notify "⏱️ Recording stopped automatically (60s limit)"
            echo "auto-stopped" > "$STATUS_FILE"
        fi
    ) &
}

# Stop recording and transcribe
stop_recording() {
    if ! is_recording; then
        notify "Not currently recording. Press hotkey to start."
        return 0
    fi

    local pid=$(cat "$PID_FILE" 2>/dev/null || echo "")

    if [ -n "$pid" ]; then
        # Stop recording gracefully
        kill -INT "$pid" 2>/dev/null || true

        # Wait for ffmpeg to finish writing the file
        local count=0
        while kill -0 "$pid" 2>/dev/null && [ $count -lt 30 ]; do
            sleep 0.1
            count=$((count + 1))
        done

        # Force kill if still running
        kill -KILL "$pid" 2>/dev/null || true
    fi

    # Clean up PID file
    rm -f "$PID_FILE"
    echo "stopped" > "$STATUS_FILE"

    # Check if we got audio
    if [ ! -f "$AUDIO_FILE" ] || [ ! -s "$AUDIO_FILE" ]; then
        notify "❌ No audio recorded"
        return 1
    fi

    notify "🔄 Processing speech..."

    # Transcribe
    local stt_python="$HOME/.venvs/stt/bin/python"
    local text

    text=$("$stt_python" -c "
from faster_whisper import WhisperModel
import sys
try:
    m = WhisperModel('large-v3', device='cpu', compute_type='int8')
    segments, info = m.transcribe('$AUDIO_FILE', beam_size=5)
    result = ' '.join(s.text.strip() for s in segments).strip()
    print(result)
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || {
        notify "❌ Speech recognition failed"
        return 1
    }

    if [ -z "${text// }" ]; then
        notify "🤷 No speech detected in recording"
        return 1
    fi

    # Copy to clipboard
    if command -v wl-copy >/dev/null 2>&1; then
        printf '%s' "$text" | wl-copy
    elif command -v xclip >/dev/null 2>&1; then
        printf '%s' "$text" | xclip -selection clipboard
    else
        notify "❌ Clipboard tool not found"
        return 1
    fi

    notify "✅ Copied: \"$text\""
    echo "Transcribed: $text"

    # Cleanup
    rm -f "$AUDIO_FILE" "$STATUS_FILE"
}

# Show status
show_status() {
    if is_recording; then
        echo "🔴 Recording in progress..."
        local pid=$(cat "$PID_FILE" 2>/dev/null || echo "")
        echo "   PID: $pid"
        if [ -f "$AUDIO_FILE" ]; then
            local size=$(stat -f%z "$AUDIO_FILE" 2>/dev/null || stat -c%s "$AUDIO_FILE" 2>/dev/null || echo "unknown")
            echo "   File size: $size bytes"
        fi
    else
        echo "⭕ Not recording"
    fi
}

# Main toggle function
toggle_recording() {
    if is_recording; then
        stop_recording
    else
        start_recording
    fi
}

# Handle command line arguments
case "${1:-toggle}" in
    "toggle"|"")
        toggle_recording
        ;;
    "start")
        start_recording
        ;;
    "stop")
        stop_recording
        ;;
    "status")
        show_status
        ;;
    "help"|"-h"|"--help")
        echo "Toggle Speech Recording"
        echo ""
        echo "Usage:"
        echo "  $0 [command]"
        echo ""
        echo "Commands:"
        echo "  toggle    Toggle recording (default)"
        echo "  start     Start recording"
        echo "  stop      Stop recording and transcribe"
        echo "  status    Show current status"
        echo ""
        echo "Hotkey behavior:"
        echo "  Press once  → Start recording (shows red notification)"
        echo "  Press again → Stop and transcribe (processes speech)"
        ;;
    *)
        echo "Unknown command: $1"
        echo "Use '$0 help' for usage information"
        exit 1
        ;;
esac