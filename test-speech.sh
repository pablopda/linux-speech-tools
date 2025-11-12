#!/usr/bin/env bash
# Simple speech test to bypass any hanging issues

set -euo pipefail

echo "🎤 Testing speech integration step by step..."

echo "1. Testing talk2claude status..."
./talk2claude status

echo ""
echo "2. Testing a 3-second recording directly..."
echo "   This will record for 3 seconds, then transcribe"
echo "   Speak when you see 'Recording...'"

read -p "Press Enter to start 3-second test..."

# Use timeout to prevent hanging
timeout 30s bash -c '
    echo "🔴 Recording for 3 seconds... SPEAK NOW!"
    DUR=3 ./talk2claude
    echo "✅ Recording completed!"
' || echo "⚠️  Test timed out - there might be an issue with the STT setup"

echo ""
echo "3. If that worked, the hotkey should work too!"
echo "4. If not, there's an issue with the STT environment"