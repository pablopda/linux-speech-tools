#!/usr/bin/env python3
"""
Simple direct test of audio generation and playback.
"""
import subprocess
import tempfile
import os
import time

def test_basic_audio():
    """Test basic TTS audio generation and playback."""
    print("🎵 Simple Audio Test")
    print("===================")

    # Test text
    test_text = "Hello! This is a test of the audio system. If you can hear this, everything is working properly."

    print(f"📝 Text: {test_text}")

    # Create temporary file
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        temp_wav = f.name

    try:
        # Step 1: Generate audio using existing say_read.py
        print("\n🔊 Step 1: Generating audio...")

        cmd = [
            os.path.expanduser("~/.venvs/tts/bin/python"),
            "say_read.py",
            "--out", temp_wav,
            "-"  # Read from stdin
        ]

        result = subprocess.run(cmd, input=test_text, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            print(f"❌ Audio generation failed:")
            print(f"stdout: {result.stdout}")
            print(f"stderr: {result.stderr}")
            return False

        # Check if file was created
        if not os.path.exists(temp_wav):
            print(f"❌ Audio file not created: {temp_wav}")
            return False

        size = os.path.getsize(temp_wav)
        print(f"✅ Audio generated: {temp_wav} ({size} bytes)")

        # Step 2: Play the audio
        print("🎵 Step 2: Playing audio...")

        play_cmd = ["ffplay", "-nodisp", "-autoexit", temp_wav]

        print(f"Running: {' '.join(play_cmd)}")

        play_result = subprocess.run(play_cmd, capture_output=True, text=True)

        if play_result.returncode != 0:
            print(f"❌ Audio playback failed:")
            print(f"stdout: {play_result.stdout}")
            print(f"stderr: {play_result.stderr}")
            return False

        print("✅ Audio played successfully!")
        return True

    finally:
        # Clean up
        if os.path.exists(temp_wav):
            os.unlink(temp_wav)

if __name__ == "__main__":
    success = test_basic_audio()

    if success:
        print("\n🎉 Audio system is working!")
        print("Ready to test continuous streaming.")
    else:
        print("\n❌ Audio system has issues.")
        print("Need to debug audio generation or playback.")