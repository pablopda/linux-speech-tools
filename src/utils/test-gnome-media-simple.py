#!/usr/bin/env uv run
# /// script
# dependencies = []  # Only uses standard library
# requires-python = ">=3.8"
# ///
"""
Simple GNOME Media Controls Test
Tests the notification system without requiring continuous streaming
"""
import subprocess
import time
import os
import sys

def show_media_notification():
    """Show a test media notification with controls."""
    print("🎵 Testing GNOME Media Controls")
    print("===============================")

    # Test notification with actions
    try:
        cmd = [
            'notify-send',
            '--app-name=Speech Reader',
            '--icon=audio-volume-medium-symbolic',
            '🎵 Speech Reader Test',
            'Click the buttons below to test media controls',
            '--action=pause=⏸️ Pause',
            '--action=resume=▶️ Resume',
            '--action=stop=⏹️ Stop'
        ]

        print("📱 Showing notification with media controls...")
        print("   Look for notification in your GNOME panel")
        print("   Try clicking the control buttons")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

        if result.returncode == 0:
            print("✅ Notification displayed successfully")
            if result.stdout:
                print(f"📋 Action clicked: {result.stdout.strip()}")
        else:
            print("❌ Notification failed:")
            print(result.stderr)

    except subprocess.TimeoutExpired:
        print("✅ Notification is displaying (waiting for user interaction)")
    except Exception as e:
        print(f"❌ Error: {e}")

def test_dbus_service():
    """Test if the GNOME Reader D-Bus service is accessible."""
    print("\n🔌 Testing D-Bus Service")
    print("========================")

    try:
        cmd = [
            'dbus-send',
            '--session',
            '--print-reply',
            '--dest=org.gnome.SpeechTools.Reader',
            '/org/gnome/SpeechTools/Reader',
            'org.freedesktop.DBus.Introspectable.Introspect'
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)

        if result.returncode == 0:
            print("✅ D-Bus service is accessible")
            return True
        else:
            print("❌ D-Bus service not available")
            print("   Service might not be running")
            return False

    except subprocess.TimeoutExpired:
        print("⏱️ D-Bus call timed out")
        return False
    except Exception as e:
        print(f"❌ D-Bus error: {e}")
        return False

def test_basic_audio():
    """Test basic TTS without continuous streaming."""
    print("\n🎧 Testing Basic Audio")
    print("======================")

    # Use simple TTS test
    test_text = "Testing GNOME media integration. This is a basic audio test."

    try:
        # Simple espeak test
        cmd = ['espeak', '-s', '150', test_text]
        print(f"📝 Speaking: {test_text}")

        result = subprocess.run(cmd, timeout=10)

        if result.returncode == 0:
            print("✅ Basic TTS working")
            return True
        else:
            print("❌ TTS failed")
            return False

    except subprocess.TimeoutExpired:
        print("⏱️ TTS timed out")
        return False
    except FileNotFoundError:
        print("❌ espeak not found, trying alternative...")

        # Try festival as fallback
        try:
            cmd = ['festival', '--tts']
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True)
            proc.communicate(input=test_text)

            if proc.returncode == 0:
                print("✅ Festival TTS working")
                return True
        except:
            pass

        print("❌ No TTS engine available")
        return False

def main():
    """Main test function."""
    print("🎮 GNOME Media Controls Test")
    print("============================\n")

    # Test components
    dbus_ok = test_dbus_service()
    audio_ok = test_basic_audio()

    # Show notification test
    show_media_notification()

    print("\n📊 Test Summary")
    print("===============")
    print(f"D-Bus Service: {'✅' if dbus_ok else '❌'}")
    print(f"Basic Audio:   {'✅' if audio_ok else '❌'}")
    print("Notifications: Check your GNOME panel")

    if dbus_ok and audio_ok:
        print("\n🎉 GNOME media integration is ready!")
        print("   The notification controls should work")
    else:
        print("\n⚠️ Some components need attention")
        print("   Basic functionality may still work")

if __name__ == "__main__":
    main()