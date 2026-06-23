#!/usr/bin/env python3
"""
Automatic mode selection for real-time dictation
Intelligently chooses between typing and clipboard modes
"""
import sys
import os
import subprocess
import importlib
import shutil
import time

try:
    from .runtime import (
        audio_capture_candidates,
        compute_type_for_device,
        describe_clipboard_tool,
        normalize_language,
        preview_enabled,
        truthy_env,
    )
except ImportError:
    from runtime import (
        audio_capture_candidates,
        compute_type_for_device,
        describe_clipboard_tool,
        normalize_language,
        preview_enabled,
        truthy_env,
    )

try:
    from .asr_engine import ENGINE_CHOICES, create_engine, normalize_engine
except ImportError:
    from asr_engine import ENGINE_CHOICES, create_engine, normalize_engine


def describe_audio_candidates(sample_rate=16000):
    try:
        candidates = audio_capture_candidates(sample_rate)
    except ValueError as exc:
        return [f"error: {exc}"]
    descriptions = []
    for command in candidates:
        try:
            descriptions.append(f"{command[5]}:{command[7]}")
        except IndexError:
            descriptions.append("unknown")
    return descriptions


def warm_model(model_size, device, engine="faster-whisper"):
    """Load the selected engine's model and return wall-clock load time.

    Routes through create_engine so warming honors --engine (a RuntimeError is
    raised with an actionable message if the engine's backend is not installed).
    """
    started = time.monotonic()
    create_engine(engine, model_size=model_size, device=device)
    return time.monotonic() - started

def check_typing_capability():
    """Check if we can type directly into applications"""

    # Check if we're in uinput group
    try:
        groups = subprocess.run(['groups'], capture_output=True, text=True).stdout
        has_uinput_group = 'uinput' in groups
    except:
        has_uinput_group = False

    # Check if /dev/uinput is accessible
    can_access_uinput = False
    if os.path.exists('/dev/uinput'):
        try:
            # Try to open for reading (safer than writing)
            with open('/dev/uinput', 'rb') as f:
                pass
            can_access_uinput = True
        except (PermissionError, IOError):
            pass

    has_ydotool = shutil.which('ydotool') is not None
    has_xdotool = shutil.which('xdotool') is not None

    # Check if ydotoold is running (for ydotool mode)
    ydotoold_running = subprocess.run(
        ['pgrep', 'ydotoold'],
        capture_output=True
    ).returncode == 0 if has_ydotool else False

    # Check display server
    is_wayland = os.environ.get('XDG_SESSION_TYPE') == 'wayland'
    is_x11 = os.environ.get('XDG_SESSION_TYPE') == 'x11' or os.environ.get('DISPLAY')

    # Determine capabilities
    can_type_wayland = is_wayland and has_ydotool and (
        (has_uinput_group and can_access_uinput) or ydotoold_running
    )
    can_type_x11 = is_x11 and has_xdotool

    return {
        'can_type': can_type_wayland or can_type_x11,
        'is_wayland': is_wayland,
        'is_x11': is_x11,
        'has_ydotool': has_ydotool,
        'has_xdotool': has_xdotool,
        'has_uinput_group': has_uinput_group,
        'can_access_uinput': can_access_uinput,
        'ydotoold_running': ydotoold_running,
        'method': 'wayland_typing' if can_type_wayland else 'x11_typing' if can_type_x11 else 'clipboard'
    }

def main():
    """Main entry point - auto-select best mode"""
    import argparse

    parser = argparse.ArgumentParser(description="Real-time dictation with auto-mode selection")
    parser.add_argument("--clipboard", action="store_true", help="Force clipboard mode")
    parser.add_argument("--typing", "--type", action="store_true", help="Force typing mode")
    parser.add_argument("--preview", action="store_true", help="Preview recognized text before output")
    parser.add_argument("--check", action="store_true", help="Check capabilities and exit")
    parser.add_argument("--diagnose", action="store_true", help="Show detailed diagnostics and exit")
    parser.add_argument("--warm-model", action="store_true", help="Load the selected model and report load time")
    parser.add_argument("--test", action="store_true", help="Alias for --check")
    parser.add_argument("--model", "-m", default=os.environ.get("WHISPER_MODEL", os.environ.get("T2C_MODEL", "tiny")))
    parser.add_argument("--language", "--lang", "-l", default=os.environ.get("ASR_LANG", os.environ.get("T2C_LANG", "en")))
    parser.add_argument("--device", "-d", default=os.environ.get("WHISPER_DEVICE", "cpu"))
    try:
        default_vad = int(os.environ.get("WHISPER_VAD", "2"))
    except ValueError:
        default_vad = 2
    if default_vad not in (0, 1, 2, 3):
        default_vad = 2
    parser.add_argument(
        "--vad", "-v",
        type=int,
        default=default_vad,
        choices=[0, 1, 2, 3],
        help="VAD aggressiveness 0-3 (default: 2)",
    )
    parser.add_argument(
        "--engine",
        default=os.environ.get("STT_ENGINE", "faster-whisper"),
        help="ASR engine: faster-whisper (default) or parakeet",
    )
    args = parser.parse_args()

    try:
        engine = normalize_engine(args.engine)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(2)

    # Check environment variable
    forced_mode = os.environ.get('DICTATION_MODE', os.environ.get('T2C_MODE', '')).lower()
    if forced_mode == 'type':
        forced_mode = 'typing'

    caps = check_typing_capability()

    if args.check or args.test or args.diagnose or args.warm_model:
        print("🔍 Capability Check:", file=sys.stderr)
        print(f"  Display: {'Wayland' if caps['is_wayland'] else 'X11' if caps['is_x11'] else 'Unknown'}")
        print(f"  ydotool installed: {'✓' if caps['has_ydotool'] else '✗'}")
        print(f"  xdotool installed: {'✓' if caps['has_xdotool'] else '✗'}")
        print(f"  uinput group: {'✓' if caps['has_uinput_group'] else '✗'}")
        print(f"  /dev/uinput access: {'✓' if caps['can_access_uinput'] else '✗'}")
        print(f"  ydotoold daemon: {'✓' if caps['ydotoold_running'] else '✗'}")
        print(f"  Can type directly: {'✓' if caps['can_type'] else '✗'}")
        print("  Default mode: clipboard")
        print(f"  Direct typing available: {'✓' if caps['can_type'] else '✗'}")
        print(f"  Preview mode: {'enabled' if preview_enabled(args.preview) else 'disabled'}")
        print(f"  Model: {args.model}")
        print(f"  Language: {normalize_language(args.language) or 'auto'}")
        print(f"  Engine: {engine}")
        print(f"  Device: {args.device}")
        if engine == "faster-whisper":
            print(f"  Compute type: {compute_type_for_device(args.device)}")
        else:
            print(f"  Compute type: n/a ({engine})")
        print(f"  Audio backends: {', '.join(describe_audio_candidates())}")
        print(f"  Clipboard output: {describe_clipboard_tool()}")
        print(f"  Transcript fallback: {'enabled' if truthy_env('STT_TRANSCRIPT_FALLBACK') else 'disabled'}")
        if args.warm_model:
            print("  Warming selected model explicitly...", file=sys.stderr)
            try:
                elapsed = warm_model(args.model, args.device, engine)
            except RuntimeError as exc:
                print(f"  Model load: failed ({exc})")
                sys.exit(1)
            print(f"  Model load time: {elapsed:.2f}s")
        return

    # Determine mode
    use_typing = False

    if args.typing or forced_mode == 'typing':
        if not caps['can_type']:
            print("❌ Typing mode requested but not available", file=sys.stderr)
            print("   Run: ./scripts/setup/setup-uinput-permissions.sh", file=sys.stderr)
            sys.exit(1)
        use_typing = True
    elif args.clipboard or forced_mode == 'clipboard':
        use_typing = False
    else:
        # Default to clipboard. Direct typing is powerful and must be explicit.
        use_typing = False

    if use_typing:
        print(f"⌨️  Direct typing mode ({caps['method']})", file=sys.stderr)
        print("   Text will type directly into active window", file=sys.stderr)

        typing_module = import_mode_module("faster_whisper_typing")

        # Pass through remaining arguments
        sys.argv = [
            sys.argv[0],
            "--model", args.model,
            "--language", normalize_language(args.language) or "auto",
            "--device", args.device,
            "--engine", engine,
            "--vad", str(args.vad),
        ]
        if args.preview:
            sys.argv.append("--preview")
        typing_module.main()
    else:
        print("📋 Clipboard mode", file=sys.stderr)
        print("   Text will copy to clipboard - paste with Ctrl+V", file=sys.stderr)

        if caps['can_type']:
            print("   💡 Typing mode is available with --typing or DICTATION_MODE=typing.", file=sys.stderr)
        elif caps['is_wayland'] and not caps['has_uinput_group']:
            print("   💡 For direct typing, run: ./scripts/setup/setup-uinput-permissions.sh", file=sys.stderr)

        clipboard_module = import_mode_module("faster_whisper_clipboard")

        # Pass through remaining arguments
        sys.argv = [
            sys.argv[0],
            "--model", args.model,
            "--language", normalize_language(args.language) or "auto",
            "--device", args.device,
            "--engine", engine,
            "--vad", str(args.vad),
        ]
        if args.preview:
            sys.argv.append("--preview")
        clipboard_module.main()


def import_mode_module(module_name):
    """Import STT mode modules with package context so relative imports work."""
    package = __package__ or "src.stt"
    try:
        return importlib.import_module(f".{module_name}", package)
    except ImportError:
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        return importlib.import_module(f"src.stt.{module_name}")

if __name__ == "__main__":
    main()
