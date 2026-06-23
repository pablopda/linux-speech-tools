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
import secrets
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
        self.reading_pid = None
        self.is_paused = False
        self.current_notification_id = None
        self.notification_thread = None

        # State file for persistence
        self.state_file = Path.home() / '.cache' / 'speech-tools' / 'reader-state.json'
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.state_file.parent.chmod(0o700)
        except OSError:
            pass

        print("🎵 GNOME Reader Control service started")

    @dbus.service.method('org.gnome.SpeechTools.Reader', in_signature='ssis', out_signature='b')
    def start_reading(self, source_url: str, title: str, total_chunks: int, token: str) -> bool:
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
                'status': 'playing',
                'token': str(token) or secrets.token_hex(16),
            }

            self.is_paused = False
            self.reading_pid = None

            # Save state
            self._save_state()

            # Show initial notification with controls
            self._show_reading_notification()

            print(f"📚 Started reading: {title}")
            return True

        except Exception as e:
            print(f"❌ Failed to start reading: {e}")
            return False

    def _pid_start_time(self, pid: int) -> Optional[int]:
        try:
            stat = Path(f'/proc/{pid}/stat').read_text()
            return int(stat.split()[21])
        except Exception:
            return None

    def _pid_is_reader(self, pid: int, expected_start_time: Optional[int] = None) -> bool:
        """Validate that pid is this user's reader process from this project."""
        try:
            proc = Path(f'/proc/{pid}')
            if not proc.exists():
                return False
            status = (proc / 'status').read_text(errors='ignore')
            uid_line = next((line for line in status.splitlines() if line.startswith('Uid:')), '')
            if not uid_line:
                return False
            uid = int(uid_line.split()[1])
            if uid != os.getuid():
                return False
            start_time = self._pid_start_time(pid)
            if expected_start_time is not None and start_time != expected_start_time:
                return False
            cmdline = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='ignore')
            cwd = os.readlink(proc / 'cwd')
            project_hint = 'linux-speech-tools'
            reader_hint = 'say_read.py'
            if project_hint not in cmdline and project_hint not in cwd:
                return False
            if reader_hint not in cmdline:
                return False
            return True
        except Exception:
            return False

    @dbus.service.method('org.gnome.SpeechTools.Reader', in_signature='is', out_signature='b')
    def attach_process(self, pid: int, token: str) -> bool:
        """Attach the active reading process group to the current session."""
        try:
            if not self.current_session:
                return False
            if str(token) != str(self.current_session.get('token', '')):
                return False
            if pid <= 0:
                return False
            start_time = self._pid_start_time(int(pid))
            if start_time is None or not self._pid_is_reader(int(pid), start_time):
                return False
            self.reading_pid = int(pid)
            self.current_session['pid'] = int(pid)
            self.current_session['pid_start_time'] = start_time
            self._save_state()
            print(f"🔗 Attached reader process group: {pid}")
            return True
        except Exception as e:
            print(f"❌ Failed to attach reader process: {e}")
            return False

    def _signal_reader(self, sig: int) -> bool:
        """Signal the attached reader process group."""
        if not self.reading_pid:
            return False
        try:
            expected_start = None
            if self.current_session:
                expected_start = self.current_session.get('pid_start_time')
            if not self._pid_is_reader(int(self.reading_pid), expected_start):
                self.reading_pid = None
                return False
            os.killpg(os.getpgid(self.reading_pid), sig)
            return True
        except ProcessLookupError:
            self.reading_pid = None
            return False
        except Exception:
            print("❌ Failed to signal validated reader process group")
            return False

    @dbus.service.method('org.gnome.SpeechTools.Reader', in_signature='', out_signature='b')
    def pause_reading(self) -> bool:
        """Pause the current reading session."""
        try:
            if self.current_session and not self.is_paused:
                self.is_paused = True
                self.current_session['status'] = 'paused'

                self._signal_reader(signal.SIGTSTP)

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

                self._signal_reader(signal.SIGCONT)

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
                self._signal_reader(signal.SIGTERM)
                self.reading_pid = None
                self.reading_process = None

                # Clear session
                title = self.current_session.get('title', 'Document')
                self.current_session = None
                self.is_paused = False
                self.current_notification_id = None

                # Clear state
                self._clear_state()

                # Show completion notification
                self._show_completion_notification(title)

                print("⏹️ Reading stopped")
                return True

        except Exception as e:
            print(f"❌ Failed to stop: {e}")

        return False

    @dbus.service.method('org.gnome.SpeechTools.Reader', in_signature='', out_signature='b')
    def complete_reading(self) -> bool:
        """Clear the current session after natural process completion."""
        try:
            if self.current_session:
                title = self.current_session.get('title', 'Document')
                self.current_session = None
                self.reading_pid = None
                self.reading_process = None
                self.is_paused = False
                self.current_notification_id = None
                self._clear_state()
                self._show_completion_notification(title)
                print("✅ Reading completed")
                return True
        except Exception as e:
            print(f"❌ Failed to complete reading: {e}")
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

    @dbus.service.method('org.gnome.SpeechTools.Reader', in_signature='', out_signature='s')
    def get_status(self) -> str:
        """Return the current reader status."""
        if not self.current_session:
            return "idle"
        return str(self.current_session.get('status', 'playing'))

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
            if status == 'paused':
                actions = [
                    ("resume", "Resume"),
                    ("stop", "Stop"),
                ]
            else:
                actions = [
                    ("pause", "Pause"),
                    ("stop", "Stop"),
                ]

            cmd = [
                'notify-send',
                '--wait',
                '--print-id',
                '--icon=audio-volume-high-symbolic',
                '--category=x-gnome.music',
                '--urgency=low',
                '--app-name=Speech Reader',
                f'--hint=string:action-icons:{"pause" if status == "playing" else "resume"},stop',
                '--hint=boolean:resident:true',
                '--hint=boolean:transient:false',
            ]

            # Reuse the existing notification id so updates replace (rather than
            # stack) the visible notification.
            if self.current_notification_id:
                cmd.append(f'--replace-id={self.current_notification_id}')

            cmd += [
                f'{emoji} {display_title}',
                message,
            ]

            # Add action buttons
            for action, label in actions:
                cmd.append(f'--action={action}={label}')

            # If an action-listening notification is already alive, don't spawn a
            # second competing `--wait` process. Instead push a quick replacing
            # update so progress keeps refreshing on screen (F10: previously this
            # path returned early and dropped the update entirely).
            if self.notification_thread and self.notification_thread.is_alive():
                self._replace_notification_text(emoji, display_title, message, progress_pct, status_text)
                return

            self.notification_thread = threading.Thread(
                target=self._run_notification,
                args=(cmd, progress_pct, status_text),
                daemon=True,
            )
            self.notification_thread.start()

        except Exception as e:
            print(f"❌ Notification error: {e}")

    def _replace_notification_text(self, emoji: str, display_title: str, message: str,
                                   progress_pct: int, status_text: str):
        """Non-blocking progress refresh that replaces the live notification.

        Used while the `--wait` action-listening notification is still alive so
        progress updates are not dropped. Does not add `--wait`/actions (the live
        notification already carries those buttons).
        """
        if not self.current_notification_id:
            return
        cmd = [
            'notify-send',
            '--print-id',
            f'--replace-id={self.current_notification_id}',
            '--icon=audio-volume-high-symbolic',
            '--category=x-gnome.music',
            '--urgency=low',
            '--app-name=Speech Reader',
            '--hint=boolean:resident:true',
            '--hint=boolean:transient:false',
            f'{emoji} {display_title}',
            message,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                self._store_notification_id(result.stdout)
                print(f"📱 Refreshed notification: {progress_pct}% - {status_text}")
            else:
                stderr = result.stderr.strip()
                if stderr:
                    print(f"❌ Notification refresh error: {stderr}")
        except Exception as e:
            print(f"❌ Notification refresh error: {e}")

    def _store_notification_id(self, stdout: str):
        """Parse the server-assigned notification id from notify-send --print-id."""
        for line in stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                self.current_notification_id = int(line)
                return

    def _run_notification(self, cmd, progress_pct: int, status_text: str):
        """Run notify-send and dispatch any selected action back to GLib."""
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if stderr:
                print(f"❌ Notification error: {stderr}")
            return

        print(f"📱 Updated notification: {progress_pct}% - {status_text}")
        # With --print-id the numeric id is printed first; with --wait the
        # activated action token (if any) is printed last.
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        self._store_notification_id(result.stdout)
        action = lines[-1] if lines else ""
        if action in {"pause", "resume", "stop"}:
            GLib.idle_add(self._handle_notification_action, action)

    def _handle_notification_action(self, action: str):
        """Handle a notification action in the service main loop."""
        if action == "pause":
            self.pause_reading()
        elif action == "resume":
            self.resume_reading()
        elif action == "stop":
            self.stop_reading()
        return False

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
            fd = os.open(self.state_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
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
                    self.reading_pid = self.current_session.get('pid')
                    if self.reading_pid and not self._pid_is_reader(
                        int(self.reading_pid),
                        self.current_session.get('pid_start_time'),
                    ):
                        self.current_session = None
                        self.reading_pid = None
                        self._clear_state()
                        return
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
