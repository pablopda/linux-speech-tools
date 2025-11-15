#!/usr/bin/env uv run
# /// script
# dependencies = [
#     "dbus-python>=1.2.0",
#     "PyGObject>=3.42.0",
# ]
# requires-python = ">=3.8"
# ///
"""
GNOME Media Control Integration for Continuous Audio Streaming
Provides notification-based play/pause/stop controls for document reading.
"""

import subprocess
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib, Gio
import json
import os
import sys
import signal
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any

class GnomeReaderControl(dbus.service.Object):
    """
    D-Bus service for controlling continuous reading sessions with GNOME notifications.
    """

    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.session_bus = dbus.SessionBus()
        bus_name = dbus.service.BusName('org.gnome.SpeechTools.Reader', self.session_bus)
        super().__init__(bus_name, '/org/gnome/SpeechTools/Reader')

        self.current_session = None
        self.reading_process = None
        self.is_paused = False
        self.current_notification_id = None

        # State file for persistence
        self.state_file = Path.home() / '.cache' / 'speech-tools' / 'reader-state.json'
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        print("🎵 GNOME Reader Control service started")

    @dbus.service.method('org.gnome.SpeechTools.Reader', in_signature='ssi', out_signature='b')
    def start_reading(self, source_url: str, title: str, total_chunks: int) -> bool:
        """
        Start a new reading session with media controls.

        Args:
            source_url: URL or file path being read
            title: Human-readable title
            total_chunks: Total number of text chunks (for progress)
        """
        try:
            # Store session info
            self.current_session = {
                'source': source_url,
                'title': title,
                'total_chunks': total_chunks,
                'current_chunk': 0,
                'start_time': time.time(),
                'status': 'playing'
            }

            self.is_paused = False

            # Save state
            self._save_state()

            # Show initial notification with controls
            self._show_reading_notification()

            print(f"📚 Started reading: {title}")
            return True

        except Exception as e:
            print(f"❌ Failed to start reading: {e}")
            return False

    @dbus.service.method('org.gnome.SpeechTools.Reader', in_signature='', out_signature='b')
    def pause_reading(self) -> bool:
        """Pause the current reading session."""
        try:
            if self.current_session and not self.is_paused:
                self.is_paused = True
                self.current_session['status'] = 'paused'

                # Send pause signal to reading process
                if self.reading_process:
                    self.reading_process.send_signal(signal.SIGTSTP)

                self._save_state()
                self._show_reading_notification()

                print("⏸️ Reading paused")
                return True

        except Exception as e:
            print(f"❌ Failed to pause: {e}")

        return False

    @dbus.service.method('org.gnome.SpeechTools.Reader', in_signature='', out_signature='b')
    def resume_reading(self) -> bool:
        """Resume the paused reading session."""
        try:
            if self.current_session and self.is_paused:
                self.is_paused = False
                self.current_session['status'] = 'playing'

                # Send resume signal to reading process
                if self.reading_process:
                    self.reading_process.send_signal(signal.SIGCONT)

                self._save_state()
                self._show_reading_notification()

                print("▶️ Reading resumed")
                return True

        except Exception as e:
            print(f"❌ Failed to resume: {e}")

        return False

    @dbus.service.method('org.gnome.SpeechTools.Reader', in_signature='', out_signature='b')
    def stop_reading(self) -> bool:
        """Stop the current reading session."""
        try:
            if self.current_session:
                # Terminate reading process
                if self.reading_process:
                    self.reading_process.terminate()
                    self.reading_process = None

                # Clear session
                title = self.current_session.get('title', 'Document')
                self.current_session = None
                self.is_paused = False

                # Clear state
                self._clear_state()

                # Show completion notification
                self._show_completion_notification(title)

                print("⏹️ Reading stopped")
                return True

        except Exception as e:
            print(f"❌ Failed to stop: {e}")

        return False

    @dbus.service.method('org.gnome.SpeechTools.Reader', in_signature='i', out_signature='b')
    def update_progress(self, current_chunk: int) -> bool:
        """Update reading progress."""
        try:
            if self.current_session:
                self.current_session['current_chunk'] = current_chunk
                self._save_state()

                # Update notification every 5 chunks to avoid spam
                if current_chunk % 5 == 0:
                    self._show_reading_notification()

                return True

        except Exception as e:
            print(f"❌ Failed to update progress: {e}")

        return False

    def _show_reading_notification(self):
        """Show/update the reading notification with media controls."""
        if not self.current_session:
            return

        session = self.current_session
        title = session.get('title', 'Document')
        current = session.get('current_chunk', 0)
        total = session.get('total_chunks', 1)
        status = session.get('status', 'playing')

        # Calculate progress
        progress_pct = int((current / total) * 100) if total > 0 else 0

        # Create notification message
        if status == 'playing':
            emoji = "▶️"
            status_text = "Playing"
        elif status == 'paused':
            emoji = "⏸️"
            status_text = "Paused"
        else:
            emoji = "📖"
            status_text = "Reading"

        message = f"{status_text} • {progress_pct}% complete ({current}/{total})"

        # Truncate title if too long
        display_title = title[:50] + "..." if len(title) > 50 else title

        try:
            # Create notification with action buttons
            if status == 'paused':
                actions = [
                    "resume", "▶️ Resume",
                    "stop", "⏹️ Stop"
                ]
            else:
                actions = [
                    "pause", "⏸️ Pause",
                    "stop", "⏹️ Stop"
                ]

            cmd = [
                'notify-send',
                '--icon=audio-volume-high-symbolic',
                '--category=x-gnome.music',
                '--urgency=low',
                '--app-name=Speech Reader',
                f'--hint=string:action-icons:{"pause" if status == "playing" else "resume"},stop',
                '--hint=boolean:resident:true',
                '--hint=boolean:transient:false',
                f'{emoji} {display_title}',
                message
            ]

            # Add action buttons
            for action in actions:
                cmd.extend(['--action', action])

            # Execute notification
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                print(f"📱 Updated notification: {progress_pct}% - {status_text}")

        except Exception as e:
            print(f"❌ Notification error: {e}")

    def _show_completion_notification(self, title: str):
        """Show notification when reading is completed."""
        try:
            subprocess.run([
                'notify-send',
                '--icon=audio-volume-high-symbolic',
                '--urgency=normal',
                '--app-name=Speech Reader',
                '--hint=boolean:transient:true',
                '✅ Reading Complete',
                f'Finished reading: {title}'
            ], capture_output=True)

        except Exception as e:
            print(f"❌ Completion notification error: {e}")

    def _save_state(self):
        """Save current state to file."""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.current_session, f, indent=2)
        except Exception as e:
            print(f"❌ Failed to save state: {e}")

    def _clear_state(self):
        """Clear saved state."""
        try:
            if self.state_file.exists():
                self.state_file.unlink()
        except Exception as e:
            print(f"❌ Failed to clear state: {e}")

    def _load_state(self):
        """Load saved state if available."""
        try:
            if self.state_file.exists():
                with open(self.state_file, 'r') as f:
                    self.current_session = json.load(f)
                    print(f"📖 Restored reading session: {self.current_session.get('title', 'Unknown')}")
        except Exception as e:
            print(f"❌ Failed to load state: {e}")

    def run(self):
        """Start the service main loop."""
        # Load any existing state
        self._load_state()

        # Setup signal handlers
        def signal_handler(signum, frame):
            print("🛑 Shutting down GNOME Reader Control...")
            self.stop_reading()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start main loop
        loop = GLib.MainLoop()
        try:
            loop.run()
        except KeyboardInterrupt:
            print("🛑 Service interrupted")

# Utility functions for integration

def start_reader_control_service():
    """Start the GNOME reader control service in background."""
    try:
        # Check if service is already running
        session_bus = dbus.SessionBus()
        try:
            reader = session_bus.get_object('org.gnome.SpeechTools.Reader',
                                          '/org/gnome/SpeechTools/Reader')
            print("✅ GNOME Reader Control service already running")
            return True
        except dbus.exceptions.DBusException:
            pass

        # Start service in background
        service_script = Path(__file__).resolve()
        subprocess.Popen([
            sys.executable, str(service_script), '--daemon'
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Wait a moment for service to start
        time.sleep(1)

        print("🚀 Started GNOME Reader Control service")
        return True

    except Exception as e:
        print(f"❌ Failed to start service: {e}")
        return False

def get_reader_control():
    """Get the reader control D-Bus interface."""
    try:
        session_bus = dbus.SessionBus()
        reader_obj = session_bus.get_object('org.gnome.SpeechTools.Reader',
                                          '/org/gnome/SpeechTools/Reader')
        return dbus.Interface(reader_obj, 'org.gnome.SpeechTools.Reader')
    except Exception as e:
        print(f"❌ Failed to get reader control: {e}")
        return None

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == '--daemon':
        # Run as daemon service
        service = GnomeReaderControl()
        service.run()
    else:
        # Start service if not running
        start_reader_control_service()