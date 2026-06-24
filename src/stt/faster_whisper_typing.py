#!/usr/bin/env python3
"""
Real-time dictation using faster-whisper that types directly into active window
Runs in background and types text as it's recognized
"""
import sys
import subprocess
import os

try:
    from .faster_whisper_clipboard import ClipboardManager
    from .runtime import (
        NonInteractivePreviewError,
        preview_enabled,
        preview_transcription,
    )
    from .session import FasterWhisperSession
    from .asr_engine import add_engine_argument, resolve_engine
except ImportError:
    from faster_whisper_clipboard import ClipboardManager
    from runtime import (
        NonInteractivePreviewError,
        preview_enabled,
        preview_transcription,
    )
    from session import FasterWhisperSession
    from asr_engine import add_engine_argument, resolve_engine

class TextTyper:
    """Types text into the active window using ydotool or xdotool"""

    def __init__(self):
        self.tool = self._detect_typing_tool()
        if not self.tool:
            raise RuntimeError("No typing tool available (ydotool or xdotool)")

    def _detect_typing_tool(self):
        """Detect which typing tool is available"""
        # Check for Wayland (ydotool). On Wayland, xdotool cannot type into
        # native Wayland windows, so do not fall through to it: require ydotool.
        if os.environ.get('WAYLAND_DISPLAY') or os.environ.get('XDG_SESSION_TYPE') == 'wayland':
            if subprocess.run(['which', 'ydotool'], capture_output=True).returncode == 0:
                # Check if ydotoold is running
                if subprocess.run(['pgrep', 'ydotoold'], capture_output=True).returncode != 0:
                    print(
                        "Warning: ydotoold is not running. Prefer the project "
                        "uinput setup helper before using direct typing.",
                        file=sys.stderr,
                    )
                return 'ydotool'
            print(
                "No typing tool available on Wayland: ydotool not found. "
                "Install ydotool (and run the uinput setup helper), or use "
                "clipboard mode.",
                file=sys.stderr,
            )
            return None

        # Check for X11 (xdotool)
        if subprocess.run(['which', 'xdotool'], capture_output=True).returncode == 0:
            return 'xdotool'

        return None

    def type_text(self, text):
        """Type text into the active window"""
        if not text or not text.strip():
            return False

        # Clean the text
        text = text.strip()

        try:
            if self.tool == 'ydotool':
                # ydotool type command
                subprocess.run(['ydotool', 'type', text + ' '], check=True)
                return True
            elif self.tool == 'xdotool':
                # xdotool type command
                subprocess.run(['xdotool', 'type', '--', text + ' '], check=True)
                return True
        except Exception as e:
            print(
                f"Direct typing command failed ({self.tool}): {e}",
                file=sys.stderr,
            )
        return False

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
        return {
            "clipboard_tool": self.clipboard.clipboard_tool,
            "typing_tool": self.typer.tool,
            "fallback_file": str(self.clipboard.fallback_file)
            if self.clipboard.transcript_fallback else None,
            "error": self.clipboard.last_error,
        }

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

        if not self.typer.type_text(text):
            print(
                "Typing failed; copying text to clipboard fallback.",
                file=sys.stderr,
            )
            if self.clipboard.copy_to_clipboard(text):
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
