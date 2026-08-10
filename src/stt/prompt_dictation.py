#!/usr/bin/env python3
"""Live developer prompt dictation."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional

try:
    from .asr_engine import add_engine_argument, resolve_engine
    from .prompt_delivery import (
        InputController,
        check_typing_capability,
        renderer_for,
    )
    from .runtime import (
        audio_capture_candidates,
        compute_type_for_device,
        normalize_language,
        normalize_vad_aggressiveness,
        notify,
    )
    from .target_context import TargetContext, detect_target, focus_matches
except ImportError:
    from asr_engine import add_engine_argument, resolve_engine
    from prompt_delivery import (
        InputController,
        check_typing_capability,
        renderer_for,
    )
    from runtime import (
        audio_capture_candidates,
        compute_type_for_device,
        normalize_language,
        normalize_vad_aggressiveness,
        notify,
    )
    from target_context import TargetContext, detect_target, focus_matches


COMMON_DEVELOPER_TERMS = [
    "AGENTS.md",
    "Claude Code",
    "Codex CLI",
    "faster-whisper",
    "WebRTC",
    "Wayland",
    "GNOME",
    "uinput",
    "ydotool",
    "pytest",
    "pyproject.toml",
    "uv",
]


class PromptDictation:
    def __init__(
        self,
        *,
        model_size: str,
        language: str,
        device: str,
        vad_aggressiveness: int,
        profile: str,
        output: str,
        paste_keys: str,
        submit: str,
        context_dir: Optional[str],
        engine: str = "faster-whisper",
    ) -> None:
        try:
            from .session import FasterWhisperSession
        except ImportError:
            from session import FasterWhisperSession

        self.profile = profile
        self.output = output
        self.submit = submit
        self.engine = engine
        self.target = detect_target(profile)
        self.confirmed_parts: List[str] = []
        hints = developer_hints(context_dir, self.profile, self.target)
        self.renderer = renderer_for(
            output,
            self.target.kind,
            paste_keys=paste_keys,
            confidence=self.target.confidence,
            focus_guard=lambda: focus_matches(self.target),
        )
        self.input_controller = InputController()
        self.session = FasterWhisperSession(
            model_size=model_size,
            language=language,
            device=device,
            engine=engine,
            vad_aggressiveness=vad_aggressiveness,
            mode="prompt",
            output_handler=self.accept_final,
            partial_handler=self.accept_partial,
            status_fields=self.status_fields,
            on_listening=lambda: print("Listening...", end="\r", file=sys.stderr),
            on_recording=lambda: print("Recording...", end="\r", file=sys.stderr),
            on_processing=lambda: print("Processing...", end="\r", file=sys.stderr),
            initial_prompt=hints,
            hotwords=hints,
        )

    def status_fields(self) -> dict:
        data = {
            "profile": self.profile,
            "output": self.output,
            "renderer": self.renderer.mode,
            "submit": self.submit,
            "engine": self.engine,
        }
        data.update({f"target_{key}": value for key, value in self.target.to_dict().items()})
        return data

    def full_text(self, partial: str = "") -> str:
        parts = list(self.confirmed_parts)
        if partial.strip():
            parts.append(partial.strip())
        return " ".join(part for part in parts if part).strip()

    def accept_partial(self, text: str) -> None:
        self.renderer.update(self.full_text(text))

    def accept_final(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        self.confirmed_parts.append(text)
        return self.renderer.update(self.full_text())

    def run(self) -> int:
        print("Live developer dictation", file=sys.stderr)
        print(f"Target: {self.target.kind} ({self.target.confidence}, {self.target.source})", file=sys.stderr)
        print(f"Output: {self.output} -> {self.renderer.mode}", file=sys.stderr)
        print("Stop with Ctrl+C or the configured hotkey.", file=sys.stderr)
        try:
            session_ok = self.session.run()
            if session_ok is False:
                notify(
                    "Developer Dictation",
                    "Final transcription failed; check dictation logs.",
                    icon="dialog-warning",
                    urgency="normal",
                )
                return 1
            final_text = self.full_text()
            if final_text:
                ok = self.renderer.finalize(final_text)
                if ok:
                    notify("Developer Dictation", "Prompt text is ready.", icon="edit-paste", urgency="low")
                    self.maybe_submit(final_text)
                else:
                    notify("Developer Dictation", "Could not insert text; check dictation logs.", icon="dialog-warning", urgency="normal")
                    return 1
            return 0
        finally:
            self.renderer.close()

    def maybe_submit(self, final_text: str) -> None:
        if not self.renderer.can_submit():
            return
        submit = self.submit.strip().lower()
        should_submit = submit == "always"
        if submit == "voice-command":
            should_submit = final_text.lower().rstrip(".!? ").endswith(("send it", "submit it", "submit"))
        if should_submit:
            self.input_controller.send_key_combo("enter")


def developer_hints(context_dir: Optional[str], profile: str, target: TargetContext) -> str:
    terms = list(COMMON_DEVELOPER_TERMS)
    for value in [profile, target.kind, target.title, target.wm_class, target.app_id]:
        if value:
            terms.append(value)

    root = Path(context_dir or os.environ.get("PROMPT_CONTEXT_DIR", "") or os.getcwd())
    terms.extend(git_context_terms(root))
    deduped = []
    seen = set()
    for term in terms:
        cleaned = str(term).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return ", ".join(deduped[:240])


def git_context_terms(root: Path) -> Iterable[str]:
    if not root.exists():
        return []
    terms: List[str] = []
    try:
        branch = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout.strip()
        if branch:
            terms.append(branch)
        files = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        ).stdout.splitlines()
        for path in files[:180]:
            terms.append(path)
            name = Path(path).name
            if name != path:
                terms.append(name)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return terms


def describe_audio_candidates(sample_rate: int = 16000) -> str:
    try:
        candidates = audio_capture_candidates(sample_rate)
    except ValueError as exc:
        return f"error: {exc}"
    values = []
    for command in candidates:
        try:
            values.append(f"{command[5]}:{command[7]}")
        except IndexError:
            values.append("unknown")
    return ", ".join(values)


def check(args: argparse.Namespace) -> int:
    target = detect_target(args.profile)
    caps = check_typing_capability()
    print("Live Developer Dictation Check")
    print(f"  Target: {target.kind} ({target.confidence}, {target.source})")
    print(f"  Output: {args.output}")
    print(f"  Model: {args.model}")
    print(f"  Engine: {resolve_engine(args.engine)}")
    print(f"  Language: {normalize_language(args.language) or 'auto'}")
    print(f"  Device: {args.device}")
    print(f"  Compute type: {compute_type_for_device(args.device)}")
    print(f"  Audio backends: {describe_audio_candidates()}")
    print(f"  Direct input: {'available' if caps.can_type else 'unavailable'} ({caps.method})")
    print(f"  ydotool: {'yes' if caps.has_ydotool else 'no'}")
    print(f"  xdotool: {'yes' if caps.has_xdotool else 'no'}")
    print(f"  uinput group: {'yes' if caps.has_uinput_group else 'no'}")
    print(f"  /dev/uinput access: {'yes' if caps.can_access_uinput else 'no'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Live developer prompt dictation")
    parser.add_argument("--check", action="store_true", help="Check live dictation capabilities and exit")
    parser.add_argument("--profile", default=os.environ.get("PROMPT_DICTATION_PROFILE", "auto"),
                        choices=["auto", "claude", "codex", "ide", "terminal", "generic"])
    parser.add_argument("--output", default=os.environ.get("PROMPT_DICTATION_OUTPUT", "auto"),
                        choices=["auto", "live-type", "overlay", "paste", "clipboard", "stdout"])
    parser.add_argument("--paste-keys", default=os.environ.get("PROMPT_DICTATION_PASTE_KEYS", "auto"),
                        choices=["auto", "ctrl-v", "ctrl-shift-v", "shift-insert"])
    parser.add_argument("--submit", default=os.environ.get("PROMPT_DICTATION_SUBMIT", "never"),
                        choices=["never", "voice-command", "always"])
    parser.add_argument("--context-dir", default=os.environ.get("PROMPT_CONTEXT_DIR", ""))
    parser.add_argument("--model", "-m", default=os.environ.get("WHISPER_MODEL", "tiny"))
    parser.add_argument("--language", "--lang", "-l", default=os.environ.get("ASR_LANG", "en"))
    parser.add_argument("--device", "-d", default=os.environ.get("WHISPER_DEVICE", "cpu"))
    add_engine_argument(parser)
    parser.add_argument(
        "--vad",
        "-v",
        type=int,
        choices=[0, 1, 2, 3],
        default=normalize_vad_aggressiveness(os.environ.get("WHISPER_VAD", "2")),
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.engine = resolve_engine(args.engine)
    if args.check:
        return check(args)
    dictation = PromptDictation(
        model_size=args.model,
        language=args.language,
        device=args.device,
        engine=args.engine,
        vad_aggressiveness=args.vad,
        profile=args.profile,
        output=args.output,
        paste_keys=args.paste_keys,
        submit=args.submit,
        context_dir=args.context_dir or None,
    )
    return dictation.run()


if __name__ == "__main__":
    raise SystemExit(main())
