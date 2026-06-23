#!/usr/bin/env python3
"""
Manual diagnostic for talk2claude-faster installation and functionality.

This file intentionally avoids pytest's test filename patterns because it may
touch ffmpeg, audio devices, and faster-whisper model cache/download paths.
"""
import sys
import subprocess
import os

def test_imports():
    """Test that all required packages can be imported"""
    print("🔍 Testing package imports...")

    errors = []

    # Test faster-whisper
    try:
        import faster_whisper
        print(f"✅ faster-whisper {faster_whisper.__version__}")
    except ImportError as e:
        errors.append(f"❌ faster-whisper: {e}")

    # Test webrtcvad
    try:
        import webrtcvad
        print("✅ webrtcvad")
    except ImportError as e:
        errors.append(f"❌ webrtcvad: {e}")

    # Test numpy
    try:
        import numpy
        print(f"✅ numpy {numpy.__version__}")
    except ImportError as e:
        errors.append(f"❌ numpy: {e}")

    return errors

def test_ffmpeg():
    """Test ffmpeg audio capture"""
    print("\n🎤 Testing audio capture...")

    try:
        # Test if ffmpeg is available
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=2
        )
        if result.returncode == 0:
            print("✅ ffmpeg installed")
        else:
            return ["❌ ffmpeg not working properly"]

        # Test audio capture
        result = subprocess.run(
            ["ffmpeg", "-f", "alsa", "-i", "default", "-t", "0.1", "-f", "null", "-"],
            capture_output=True,
            timeout=2
        )
        if result.returncode == 0:
            print("✅ Audio capture works")
        else:
            print("⚠️  Audio capture may not work - check microphone")

    except subprocess.TimeoutExpired:
        return ["❌ Audio capture timeout - microphone may be in use"]
    except FileNotFoundError:
        return ["❌ ffmpeg not found - install with: sudo apt install ffmpeg"]

    return []

def test_whisper_model():
    """Test that whisper can load a model"""
    print("\n🤖 Testing Whisper model loading...")

    try:
        from faster_whisper import WhisperModel
        print("Loading tiny model (this may download ~39MB on first run)...")
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        print("✅ Whisper model loaded successfully")
        return []
    except Exception as e:
        return [f"❌ Failed to load Whisper model: {e}"]

def main():
    print("=" * 50)
    print("talk2claude-faster Installation Test")
    print("=" * 50)
    print()

    all_errors = []

    # Test imports
    errors = test_imports()
    all_errors.extend(errors)

    # Test ffmpeg
    errors = test_ffmpeg()
    all_errors.extend(errors)

    # Test Whisper model
    errors = test_whisper_model()
    all_errors.extend(errors)

    print()
    print("=" * 50)

    if all_errors:
        print("❌ Issues Found:")
        for error in all_errors:
            print(f"  {error}")
        print()
        print("📋 To fix:")
        print("1. Install Python packages:")
        print("   pip install faster-whisper webrtcvad numpy")
        print("2. Install ffmpeg:")
        print("   sudo apt install ffmpeg")
        return 1
    else:
        print("✅ All tests passed!")
        print()
        print("Ready to use:")
        print("  ./bin/talk2claude-faster")
        print()
        print("Options:")
        print("  --model tiny   # Fastest, least accurate")
        print("  --model base   # Good balance")
        print("  --model small  # Better accuracy")
        print("  --language es  # Spanish (default: en)")
        print("  --vad 3        # VAD aggressiveness 0-3")
        return 0

if __name__ == "__main__":
    sys.exit(main())
