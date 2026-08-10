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
    from .insertion_metrics import DIRECT_ATTEMPT_BACKENDS, record_insertion_result
    from .prompt_delivery import (
        check_typing_capability,
        insertion_session_for,
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
    from insertion_metrics import DIRECT_ATTEMPT_BACKENDS, record_insertion_result
    from prompt_delivery import (
        check_typing_capability,
        insertion_session_for,
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

VOICE_SUBMIT_COMMANDS = {"send it", "submit it", "submit"}


def is_voice_submit_command(text: str) -> bool:
    """Return whether one utterance is exactly a submit control command."""
    normalized = (text or "").strip().lower().rstrip(".!? ")
    return normalized in VOICE_SUBMIT_COMMANDS


def eligible_supported_gnome_direct_attempt(
    target: TargetContext, attempted_backend: str
) -> bool:
    """Classify the gate cohort without retaining focus identity metadata."""
    if attempted_backend not in DIRECT_ATTEMPT_BACKENDS:
        return False
    window_sequence = getattr(target, "window_sequence", None)
    focus_generation = getattr(target, "focus_generation", None)
    return bool(
        getattr(target, "source", None) in {"gnome-focus", "profile"}
        and getattr(target, "schema_version", None) == 1
        and isinstance(getattr(target, "window_id", None), str)
        and bool(getattr(target, "window_id", ""))
        and isinstance(getattr(target, "shell_session_id", None), str)
        and bool(getattr(target, "shell_session_id", ""))
        and isinstance(window_sequence, int)
        and not isinstance(window_sequence, bool)
        and window_sequence >= 0
        and isinstance(focus_generation, int)
        and not isinstance(focus_generation, bool)
        and focus_generation >= 0
        and getattr(target, "locked", None) is False
    )


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
        self.revision = 0
        self._voice_submit_requested = False
        self._insertion_metrics_recorder = record_insertion_result
        hints = developer_hints(context_dir, self.profile, self.target)
        self.renderer = insertion_session_for(
            output,
            self.target.kind,
            target_token=self.target.window_id,
            paste_keys=paste_keys,
            confidence=self.target.confidence,
            focus_guard=lambda: focus_matches(self.target),
        )
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
        status_fields = getattr(self.renderer, "status_fields", None)
        if status_fields is not None:
            data.update(status_fields())
        # Status is written to disk by the shared session.  Keep only coarse
        # classification fields; titles, PIDs, app IDs, window IDs, and other
        # focus metadata are intentionally session-memory-only.
        data.update(
            {
                "target_kind": self.target.kind,
                "target_confidence": self.target.confidence,
                "target_source": self.target.source,
            }
        )
        return data

    def full_text(self, partial: str = "") -> str:
        parts = list(self.confirmed_parts)
        if partial.strip():
            parts.append(partial.strip())
        return " ".join(part for part in parts if part).strip()

    def accept_partial(self, text: str) -> None:
        self.renderer.update(self.full_text(text), self._next_revision())

    def accept_final(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        if (
            self.submit.strip().lower() == "voice-command"
            and is_voice_submit_command(text)
        ):
            # Treat an exact standalone utterance as control data, not prompt
            # text. Finalization will reconcile any partial preview that still
            # contained the control words before Enter is considered.
            self._voice_submit_requested = True
            return True
        # Only the last standalone control utterance can request submission.
        self._voice_submit_requested = False
        self.confirmed_parts.append(text)
        return bool(self.renderer.update(self.full_text(), self._next_revision()))

    def _next_revision(self) -> int:
        # ``getattr`` keeps lightweight __new__-constructed unit fixtures
        # compatible while normal instances initialize the counter explicitly.
        self.revision = getattr(self, "revision", 0) + 1
        return self.revision

    def run(self) -> int:
        print("Live developer dictation", file=sys.stderr)
        print(f"Target: {self.target.kind} ({self.target.confidence}, {self.target.source})", file=sys.stderr)
        print(f"Output: {self.output} -> {self.renderer.mode}", file=sys.stderr)
        print("Stop with Ctrl+C or the configured hotkey.", file=sys.stderr)
        final_insertion_result = None
        try:
            session_ok = self.session.run()
            if session_ok is False:
                self._refresh_status("error", "final transcription failed")
                notify(
                    "Developer Dictation",
                    "Final transcription failed; check dictation logs.",
                    icon="dialog-warning",
                    urgency="normal",
                )
                return 1
            final_text = self.full_text()
            if final_text:
                ok = self.renderer.finalize(final_text, self._next_revision())
                final_insertion_result = ok
                if ok:
                    submit_requested = self._should_submit(final_text)
                    submit_result = self.maybe_submit(final_text)
                    if submit_requested and not submit_result:
                        self._refresh_status("error", "prompt submission failed")
                        notify(
                            "Developer Dictation",
                            "Prompt text is ready, but submission did not "
                            "complete safely and was not retried.",
                            icon="dialog-warning",
                            urgency="normal",
                        )
                        return 1
                    self._refresh_status("idle")
                    notify(
                        "Developer Dictation",
                        "Prompt text is ready.",
                        icon="edit-paste",
                        urgency="low",
                    )
                else:
                    self._refresh_status("error", "prompt insertion failed")
                    notify(
                        "Developer Dictation",
                        "Could not insert text; check dictation logs.",
                        icon="dialog-warning",
                        urgency="normal",
                    )
                    return 1
            return 0
        finally:
            # Collection is best effort and observes only the final structured
            # insertion result. It must never affect delivery or cause a retry.
            metrics_recorder = getattr(self, "_insertion_metrics_recorder", None)
            if final_insertion_result is not None and metrics_recorder is not None:
                try:
                    drift_value = getattr(
                        self.renderer, "focus_drift_detected", False
                    )
                    target_match = getattr(
                        final_insertion_result, "target_token_match", None
                    )
                    attempted_backend = getattr(
                        self.renderer, "backend", "other"
                    )
                    metrics_recorder(
                        final_insertion_result,
                        target_kind=getattr(self.target, "kind", "unknown"),
                        attempted_backend=attempted_backend,
                        focus_drift=(drift_value is True or target_match is False),
                        eligible_supported_gnome_direct=(
                            eligible_supported_gnome_direct_attempt(
                                self.target, attempted_backend
                            )
                        ),
                    )
                except Exception:
                    pass
            self.renderer.close()

    def _should_submit(self, final_text: str) -> bool:
        submit = self.submit.strip().lower()
        if submit == "always":
            return True
        if submit == "voice-command":
            return bool(getattr(self, "_voice_submit_requested", False)) or (
                is_voice_submit_command(final_text)
            )
        return False

    def maybe_submit(self, final_text: str):
        if self._should_submit(final_text):
            # Target/revision/result eligibility and Enter dispatch all belong
            # to the same insertion session.  The orchestrator only interprets
            # the user-facing submit policy.
            self._voice_submit_requested = False
            return self.renderer.submit()
        return None

    def _refresh_status(self, state: str, error: Optional[str] = None) -> None:
        """Rewrite final structured status without exposing prompt contents."""
        setter = getattr(self.session, "set_status", None)
        if setter is None:
            return
        fields = {"error": error} if error else {}
        try:
            setter(state, **fields)
        except Exception:
            # Status refresh is best effort and must not turn a completed safe
            # insertion into a second delivery attempt.
            pass


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
