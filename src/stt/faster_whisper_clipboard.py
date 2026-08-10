#!/usr/bin/env python3
"""
Real-time dictation using faster-whisper that copies to clipboard
No special permissions needed - works on any system!
"""
import sys
import subprocess
import os

try:
    from .runtime import (
        NonInteractivePreviewError,
        detect_clipboard_tool,
        notify,
        preview_enabled,
        preview_transcription,
        state_file,
        truthy_env,
        write_private,
    )
    from .session import FasterWhisperSession
    from .asr_engine import add_engine_argument, resolve_engine
except ImportError:
    from runtime import (
        NonInteractivePreviewError,
        detect_clipboard_tool,
        notify,
        preview_enabled,
        preview_transcription,
        state_file,
        truthy_env,
        write_private,
    )
    from session import FasterWhisperSession
    from asr_engine import add_engine_argument, resolve_engine


CLIPBOARD_COMMAND_TIMEOUT_SECONDS = 3.0


class ClipboardManager:
    """Manages clipboard operations without needing special permissions"""

    def __init__(self):
        self.clipboard_tool = detect_clipboard_tool(warn=True)
        self.transcript_fallback = truthy_env("STT_TRANSCRIPT_FALLBACK")
        self.fallback_file = state_file('dictation.txt')
        self.last_error = None

    def copy_to_clipboard(self, text):
        """Copy text to clipboard using available tool"""
        if not text or not text.strip():
            return False

        text = text.strip()

        try:
            if self.clipboard_tool == 'wl-copy':
                # Wayland clipboard
                subprocess.run(
                    ['wl-copy'],
                    input=text.encode(),
                    check=True,
                    timeout=CLIPBOARD_COMMAND_TIMEOUT_SECONDS,
                )
                return True
            elif self.clipboard_tool == 'xclip':
                # X11 clipboard
                subprocess.run(
                    ['xclip', '-selection', 'clipboard'],
                    input=text.encode(),
                    check=True,
                    timeout=CLIPBOARD_COMMAND_TIMEOUT_SECONDS,
                )
                return True
            elif self.clipboard_tool == 'xsel':
                # Alternative X11 clipboard
                subprocess.run(
                    ['xsel', '--clipboard', '--input'],
                    input=text.encode(),
                    check=True,
                    timeout=CLIPBOARD_COMMAND_TIMEOUT_SECONDS,
                )
                return True
            elif self.transcript_fallback:
                # Explicit fallback: keep only the latest transcript.
                write_private(self.fallback_file, text + '\n')
                return True
            else:
                print(
                    "No clipboard tool available. Set STT_TRANSCRIPT_FALLBACK=1 "
                    f"to write the latest transcript to {self.fallback_file}.",
                    file=sys.stderr,
                )
                return False
        except Exception as e:
            tool = self.clipboard_tool
            self.last_error = f"{tool} failed: {e}"
            print(
                f"Clipboard command failed ({tool}). Install/check the clipboard "
                "tool or set STT_TRANSCRIPT_FALLBACK=1 for private file fallback.",
                file=sys.stderr,
            )
            return False

    def notify_copy(self, text):
        """Show desktop notification about copied text"""
        notify('📋 Copied to Clipboard', 'Transcription is ready to paste.', icon='edit-copy', urgency='low')

class FasterWhisperClipboard:
    """Real-time speech-to-text that copies to clipboard"""

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
        self.clipboard = ClipboardManager()
        self.preview = preview_enabled(preview)
        self.session = FasterWhisperSession(
            model_size=model_size,
            language=language,
            device=device,
            engine=engine,
            vad_aggressiveness=vad_aggressiveness,
            mode="clipboard",
            output_handler=self.emit_text,
            status_fields=self.status_fields,
            on_listening=self.print_listening,
            on_recording=lambda: print("🔴 Recording...", end='\r', file=sys.stderr),
            on_processing=lambda: print("⏸  Processing...     ", end='\r', file=sys.stderr),
        )

    def status_fields(self):
        return {
            "clipboard_tool": self.clipboard.clipboard_tool,
            "fallback_file": str(self.clipboard.fallback_file)
            if self.clipboard.transcript_fallback else None,
            "error": self.clipboard.last_error,
        }

    def print_listening(self):
        print("🎤 Listening...       ", end='\r', file=sys.stderr)

    def preview_or_skip(self, text: str):
        if not self.preview:
            return text.strip()
        try:
            accepted, stop_requested = preview_transcription(text, "clipboard")
        except NonInteractivePreviewError:
            print(
                "Preview requested without an interactive terminal; copying "
                "transcription without prompting.",
                file=sys.stderr,
            )
            return text.strip()
        if stop_requested:
            self.session.running = False
            self.session.finalize_requested = False
        return accepted

    def emit_text(self, text: str) -> bool:
        text = self.preview_or_skip(text)
        if text and self.clipboard.copy_to_clipboard(text):
            print(f"✅ Transcription ready ({len(text)} chars)", file=sys.stderr)
            self.clipboard.notify_copy(text)
            return True
        return False

    def run(self):
        """Start the dictation system"""
        stop_hint = os.environ.get("STT_STOP_HINT", "Press Ctrl+C to stop").strip()
        if not stop_hint:
            stop_hint = "Press Ctrl+C to stop"

        print("\n" + "="*50, file=sys.stderr)
        print("📋 Clipboard Mode - Real-time Dictation", file=sys.stderr)
        print("="*50, file=sys.stderr)
        print("• Speak and text copies to clipboard", file=sys.stderr)
        print("• Paste with Ctrl+V anywhere", file=sys.stderr)
        print(f"• {stop_hint}", file=sys.stderr)
        print("="*50 + "\n", file=sys.stderr)
        print(f"📋 Clipboard tool: {self.clipboard.clipboard_tool}", file=sys.stderr)
        self.session.run()

def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Real-time dictation that copies to clipboard"
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
        help="Preview recognized text before copying"
    )
    add_engine_argument(parser)

    args = parser.parse_args()
    engine = resolve_engine(args.engine)

    # Check clipboard tools (shared detector; warns on Wayland without wl-copy)
    clipboard_tool = detect_clipboard_tool(warn=True)
    if clipboard_tool == 'wl-copy':
        print("✓ Using wl-copy (Wayland)", file=sys.stderr)
    elif clipboard_tool == 'xclip':
        print("✓ Using xclip (X11)", file=sys.stderr)
    elif clipboard_tool == 'xsel':
        print("✓ Using xsel (X11)", file=sys.stderr)
    else:
        print("⚠ No clipboard tool found. Install:", file=sys.stderr)
        print("  Wayland: sudo apt install wl-clipboard", file=sys.stderr)
        print("  X11: sudo apt install xclip", file=sys.stderr)
        if truthy_env("STT_TRANSCRIPT_FALLBACK"):
            print(f"  Fallback enabled: writing latest transcript to {state_file('dictation.txt')}", file=sys.stderr)
        else:
            print("  Set STT_TRANSCRIPT_FALLBACK=1 to write the latest transcript to a private file.", file=sys.stderr)

    # Run dictation
    dictation = FasterWhisperClipboard(
        model_size=args.model,
        language=args.language,
        device=args.device,
        engine=engine,
        vad_aggressiveness=args.vad,
        preview=args.preview,
    )

    dictation.run()

if __name__ == "__main__":
    main()
