#!/usr/bin/env python3
"""
Real-time dictation using faster-whisper that types directly into active window
Runs in background and types text as it's recognized
"""
import sys
import subprocess
import os
import shutil
from dataclasses import replace

try:
    from .faster_whisper_clipboard import ClipboardManager
    from .runtime import (
        NonInteractivePreviewError,
        preview_enabled,
        preview_transcription,
    )
    from .session import FasterWhisperSession
    from .asr_engine import add_engine_argument, resolve_engine
    from .insertion_session import (
        FinalOnlyInsertionAdapter,
        InsertionState,
        result,
    )
except ImportError:
    from faster_whisper_clipboard import ClipboardManager
    from runtime import (
        NonInteractivePreviewError,
        preview_enabled,
        preview_transcription,
    )
    from session import FasterWhisperSession
    from asr_engine import add_engine_argument, resolve_engine
    from insertion_session import (
        FinalOnlyInsertionAdapter,
        InsertionState,
        result,
    )


TOOL_PROBE_TIMEOUT_SECONDS = 3.0
# ydotool deliberately spaces key events, so final utterances need more room
# than capability probes while still retaining a hard liveness bound.
TEXT_DISPATCH_TIMEOUT_SECONDS = 30.0


class TextTyper:
    """Types text into the active window using ydotool or xdotool"""

    mode = "classic-typing"
    supports_submit = False

    def __init__(self):
        self.tool = self._detect_typing_tool()
        if not self.tool:
            raise RuntimeError("No typing tool available (ydotool or xdotool)")
        self.backend = self.tool

    def _detect_typing_tool(self):
        """Detect which typing tool is available"""
        # Check for Wayland (ydotool). On Wayland, xdotool cannot type into
        # native Wayland windows, so do not fall through to it: require ydotool.
        if os.environ.get('WAYLAND_DISPLAY') or os.environ.get('XDG_SESSION_TYPE') == 'wayland':
            if shutil.which('ydotool'):
                # Check if ydotoold is running
                try:
                    daemon_running = subprocess.run(
                        ['pgrep', 'ydotoold'],
                        capture_output=True,
                        timeout=TOOL_PROBE_TIMEOUT_SECONDS,
                    ).returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    daemon_running = False
                if not daemon_running:
                    print(
                        "Warning: ydotoold is not running. Prefer the project "
                        "uinput setup helper before using direct typing.",
                        file=sys.stderr,
                    )
                return 'ydotool'
            if os.environ.get('DISPLAY'):
                # XWayland active: xdotool can type into X11/XWayland windows,
                # matching check_typing_capability()'s X11 fallback. Fall through.
                pass
            else:
                print(
                    "No typing tool available on Wayland: ydotool not found. "
                    "Install ydotool (and run the uinput setup helper), or use "
                    "clipboard mode.",
                    file=sys.stderr,
                )
                return None

        # Check for X11 (xdotool)
        if shutil.which('xdotool'):
            return 'xdotool'

        return None

    def type_text(self, text):
        """Type text into the active window"""
        return bool(self.type_result(text))

    def type_result(self, text, revision=None):
        """Dispatch final text and report whether application receipt is known."""
        if not text or not text.strip():
            return result(
                InsertionState.REJECTED,
                self.backend,
                revision=revision,
                diagnostic="empty text was rejected",
            )

        # Clean the text
        text = text.strip()

        try:
            if self.tool == 'ydotool':
                # ydotool type command
                subprocess.run(
                    ['ydotool', 'type', text + ' '],
                    check=True,
                    timeout=TEXT_DISPATCH_TIMEOUT_SECONDS,
                )
                return result(
                    InsertionState.DISPATCHED_UNCONFIRMED,
                    self.backend,
                    revision=revision,
                )
            elif self.tool == 'xdotool':
                # xdotool type command
                subprocess.run(
                    ['xdotool', 'type', '--', text + ' '],
                    check=True,
                    timeout=TEXT_DISPATCH_TIMEOUT_SECONDS,
                )
                return result(
                    InsertionState.DISPATCHED_UNCONFIRMED,
                    self.backend,
                    revision=revision,
                )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            print(
                f"Direct typing command failed after dispatch ({self.tool}); "
                "not copying automatically to avoid duplicate insertion.",
                file=sys.stderr,
            )
            return result(
                InsertionState.AMBIGUOUS_AFTER_DISPATCH,
                self.backend,
                revision=revision,
                diagnostic="classic typing command failed after dispatch",
            )
        except OSError:
            print(
                f"Direct typing command could not be started ({self.tool}).",
                file=sys.stderr,
            )
            return result(
                InsertionState.FAILED_BEFORE_DISPATCH,
                self.backend,
                revision=revision,
                diagnostic="classic typing command could not be started",
            )
        return result(
            InsertionState.UNAVAILABLE,
            self.backend,
            revision=revision,
            diagnostic="classic typing backend is unavailable",
        )

    def update(self, text, revision=None):
        return result(
            InsertionState.REJECTED,
            self.backend,
            revision=revision,
            diagnostic="classic typing accepts final text only",
        )

    def finalize(self, text, revision=None):
        return self.type_result(text, revision)

    def submit(self, revision=None):
        return result(
            InsertionState.REJECTED,
            self.backend,
            revision=revision,
            diagnostic="classic typing does not support submit",
        )

    def cancel(self, revision=None):
        return result(InsertionState.CANCELLED, self.backend, revision=revision)

    def close(self):
        return None

class FasterWhisperTyping:
    """Real-time speech-to-text that types directly into active window"""

    def __init__(
        self,
        model_size="tiny",
        language="en",
        device="cpu",
        engine="faster-whisper",
        vad_aggressiveness=2,
        preview=False,
    ):
        """
        Initialize the dictation system

        Args:
            model_size: Whisper model size (tiny, base, small, medium, large)
            language: Language code (en, es, fr, etc.)
            device: Device to use (cpu or cuda)
            vad_aggressiveness: VAD aggressiveness (0-3, higher = more aggressive)
        """
        self.typer = TextTyper()
        self.insertion = FinalOnlyInsertionAdapter(self.typer)
        self.clipboard = ClipboardManager()
        self.preview = preview_enabled(preview)
        self.session = FasterWhisperSession(
            model_size=model_size,
            language=language,
            device=device,
            engine=engine,
            vad_aggressiveness=vad_aggressiveness,
            mode="typing",
            output_handler=self.emit_text,
            status_fields=self.status_fields,
        )

    def status_fields(self):
        data = {
            "clipboard_tool": self.clipboard.clipboard_tool,
            "typing_tool": self.typer.tool,
            "fallback_file": str(self.clipboard.fallback_file)
            if self.clipboard.transcript_fallback else None,
            "error": self.clipboard.last_error,
        }
        if self.insertion.last_result is not None:
            data.update(
                {
                    "insertion_backend": self.insertion.last_result.backend,
                    "insertion_result": self.insertion.last_result.state.value,
                    "insertion_revision": self.insertion.last_result.revision,
                }
            )
        return data

    def emit_text(self, text):
        text = text.strip()
        if not text:
            return False

        if self.preview:
            try:
                accepted, stop_requested = preview_transcription(text, "typing")
            except NonInteractivePreviewError:
                print(
                    "Preview requested without an interactive terminal; "
                    "copying instead of typing.",
                    file=sys.stderr,
                )
                if self.clipboard.copy_to_clipboard(text):
                    self.clipboard.notify_copy(text)
                    return True
                return False
            if stop_requested:
                self.session.running = False
                self.session.finalize_requested = False
                return False
            if not accepted:
                return False
            text = accepted

        insertion_result = self.insertion.insert(text)
        if not insertion_result:
            if insertion_result.state == InsertionState.AMBIGUOUS_AFTER_DISPATCH:
                print(
                    "Typing result is ambiguous; transcript was not copied "
                    "automatically to avoid duplicate insertion.",
                    file=sys.stderr,
                )
                return False
            print(
                "Typing failed; copying text to clipboard fallback.",
                file=sys.stderr,
            )
            if self.clipboard.copy_to_clipboard(text):
                self.insertion.last_result = replace(
                    insertion_result,
                    state=InsertionState.CLIPBOARD_FALLBACK,
                    backend=getattr(self.clipboard, "clipboard_tool", "clipboard"),
                    diagnostic="classic typing used a non-inserting fallback",
                )
                self.clipboard.notify_copy(text)
                return True
            return False
        return True

    def run(self):
        """Start the dictation system in background"""
        self.session.run()

def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Real-time dictation that types directly into active window"
    )
    parser.add_argument(
        "--model", "-m",
        default="tiny",
        help="Whisper model size (default: tiny)"
    )
    parser.add_argument(
        "--language", "--lang", "-l",
        default="en",
        help="Language code (default: en)"
    )
    parser.add_argument(
        "--device", "-d",
        default="cpu",
        choices=["cpu", "cuda"],
        help="Device to use (default: cpu)"
    )
    parser.add_argument(
        "--vad", "-v",
        type=int,
        default=2,
        choices=[0, 1, 2, 3],
        help="VAD aggressiveness 0-3 (default: 2)"
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Preview recognized text before typing"
    )
    add_engine_argument(parser)
    args = parser.parse_args()
    engine = resolve_engine(args.engine)
    dictation = FasterWhisperTyping(
        model_size=args.model,
        language=args.language,
        device=args.device,
        engine=engine,
        vad_aggressiveness=args.vad,
        preview=args.preview,
    )

    print("Real-time dictation running. Press Ctrl+C to stop.", file=sys.stderr)
    print("Speak and text will be typed into your active window.", file=sys.stderr)

    dictation.run()

if __name__ == "__main__":
    main()
