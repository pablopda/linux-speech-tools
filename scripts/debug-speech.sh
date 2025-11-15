#!/usr/bin/env bash
# Debug version to isolate the issue

set -euo pipefail

echo "🔍 Debugging Speech Integration"
echo "=============================="

# Test 1: Basic audio recording (no transcription)
echo "1. Testing audio recording only..."
timeout 5s ffmpeg -f pulse -i default -t 2 -ac 1 -ar 16000 -c:a pcm_s16le -y /tmp/test_audio.wav -v quiet 2>/dev/null && echo "✅ Audio recording works" || echo "❌ Audio recording failed"

# Test 2: Check if audio file was created and has content
if [ -f /tmp/test_audio.wav ] && [ -s /tmp/test_audio.wav ]; then
    echo "✅ Audio file created ($(stat -f%z /tmp/test_audio.wav 2>/dev/null || stat -c%s /tmp/test_audio.wav) bytes)"
else
    echo "❌ Audio file empty or missing"
    exit 1
fi

# Test 3: Test whisper transcription separately
echo ""
echo "2. Testing speech recognition on recorded audio..."

STT_VENV="$HOME/.venvs/stt"
if [ -x "$STT_VENV/bin/python" ]; then
    echo "✅ STT Python environment found"

    # Test whisper model loading
    timeout 30s "$STT_VENV/bin/python" -c "
import sys
from faster_whisper import WhisperModel
print('Loading Whisper model...')
m = WhisperModel('large-v3', device='cpu', compute_type='int8')
print('✅ Whisper model loaded successfully')

try:
    segments, info = m.transcribe('/tmp/test_audio.wav', beam_size=5)
    result = ' '.join(s.text.strip() for s in segments).strip()
    if result:
        print(f'✅ Transcription: \"{result}\"')
    else:
        print('⚠️  No speech detected (empty result)')
except Exception as e:
    print(f'❌ Transcription failed: {e}')
    sys.exit(1)
" 2>/dev/null || echo "❌ Speech recognition test failed or timed out"
else
    echo "❌ STT Python environment not found"
fi

# Cleanup
rm -f /tmp/test_audio.wav

echo ""
echo "3. If both tests passed, the issue might be in the integration script"
echo "4. If transcription failed, there's an STT environment issue"
echo "5. If audio failed, there's a microphone permission issue"