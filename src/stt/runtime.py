#!/usr/bin/env python3
"""Runtime helpers shared by STT command modules."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Tuple


def state_dir() -> Path:
    root = os.environ.get("XDG_RUNTIME_DIR")
    if root:
        path = Path(root) / "linux-speech-tools"
    else:
        path = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        path = path / "linux-speech-tools"

    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def state_file(name: str) -> Path:
    return state_dir() / name


def append_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(text)


def write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def truthy_env(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def status_file() -> Path:
    return state_file("talk2claude-faster.status.json")


def write_status(state: str, **fields: Any) -> None:
    data = {
        "state": state,
        "pid": os.getpid(),
        "updated_at": int(time.time()),
    }
    data.update({key: value for key, value in fields.items() if value is not None})
    write_private(status_file(), json.dumps(data, sort_keys=True) + "\n")


def read_status() -> Dict[str, Any]:
    try:
        with status_file().open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def purge_state_files() -> List[Path]:
    removed = []
    for name in [
        "talk2claude-faster.pid",
        "talk2claude-faster.log",
        "talk2claude-faster.status.json",
        "dictation.txt",
    ]:
        path = state_file(name)
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            pass
    return removed


class NonInteractivePreviewError(RuntimeError):
    """Raised when preview is requested without an interactive input stream."""


def preview_enabled(cli_preview: bool = False) -> bool:
    return cli_preview or truthy_env("DICTATION_PREVIEW")


def preview_transcription(
    text: str,
    destination: str,
    input_stream: Optional[TextIO] = None,
    output_stream: Optional[TextIO] = None,
    interactive: Optional[bool] = None,
) -> Tuple[Optional[str], bool]:
    """Prompt before outputting dictated text.

    Returns (accepted_text, stop_requested). A None accepted_text means the
    current utterance should be skipped and listening may continue.
    """
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stderr
    if interactive is None:
        interactive = input_stream.isatty()
    if not interactive:
        raise NonInteractivePreviewError(
            "preview mode requires an interactive terminal"
        )

    print("", file=output_stream)
    print(f"Transcription preview for {destination}:", file=output_stream)
    print(text.strip(), file=output_stream)
    while True:
        print(
            "Accept, edit, skip/retry, or cancel? [a/e/s/c]: ",
            end="",
            file=output_stream,
            flush=True,
        )
        try:
            choice = input_stream.readline()
        except KeyboardInterrupt:
            return None, True
        if choice == "":
            return None, True
        choice = choice.strip().lower()
        if choice in {"", "a", "accept", "y", "yes"}:
            return text.strip(), False
        if choice in {"e", "edit"}:
            print("Edited text: ", end="", file=output_stream, flush=True)
            edited = input_stream.readline()
            if edited == "":
                return None, True
            edited = edited.strip()
            return (edited if edited else None), False
        if choice in {"s", "skip", "r", "retry"}:
            print("Skipped current transcription.", file=output_stream)
            return None, False
        if choice in {"c", "cancel", "q", "quit"}:
            print("Cancelled dictation.", file=output_stream)
            return None, True
        print("Choose a, e, s, or c.", file=output_stream)


def normalize_language(language: Optional[str]) -> Optional[str]:
    code = (language or "").strip().lower()
    if not code or code == "auto":
        return None
    return re.split(r"[._-]", code, maxsplit=1)[0]


def compute_type_for_device(device: str) -> str:
    configured = os.environ.get("WHISPER_COMPUTE_TYPE", "").strip()
    if configured:
        return configured
    return "int8" if device == "cpu" else "float16"


def notify(title: str, message: str, icon: str = "dialog-information", urgency: str = "low") -> None:
    notify_send = shutil.which("notify-send")
    if not notify_send:
        print(f"{title}: {message}", file=sys.stderr)
        return

    subprocess.run(
        [
            notify_send,
            title,
            message,
            f"--icon={icon}",
            f"--urgency={urgency}",
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def ffmpeg_supports_input_format(executable: str, input_format: str) -> bool:
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-formats"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False

    pattern = re.compile(rf"^\s*[D ][E ]\S*\s+{re.escape(input_format)}\s+", re.MULTILINE)
    return bool(pattern.search(result.stdout + "\n" + result.stderr))


def ffmpeg_executables_for_capture() -> List[str]:
    """Return ffmpeg executables in capture-preferred order.

    Homebrew ffmpeg can appear first in PATH and may lack PulseAudio input
    support. Prefer explicit overrides, then the distro ffmpeg, then PATH.
    """
    candidates = [
        os.environ.get("STT_FFMPEG", ""),
        os.environ.get("FFM", ""),
        "/usr/bin/ffmpeg",
        shutil.which("ffmpeg") or "",
    ]
    seen = set()
    executables = []
    for executable in candidates:
        if not executable or executable in seen:
            continue
        if os.path.exists(executable) and os.access(executable, os.X_OK):
            resolved = os.path.realpath(executable)
            if resolved in seen:
                continue
            seen.add(executable)
            seen.add(resolved)
            executables.append(executable)
    return executables or ["ffmpeg"]


def audio_capture_candidates(sample_rate: int) -> List[List[str]]:
    """Return ffmpeg capture commands in preferred order.

    STT_AUDIO_BACKEND accepts auto, pulse, pipewire, or alsa. PipeWire desktop
    sessions normally expose PulseAudio compatibility, so pulse is the right
    ffmpeg input for both PulseAudio and common PipeWire setups.
    """
    backend = os.environ.get("STT_AUDIO_BACKEND", "auto").strip().lower()
    device = os.environ.get("STT_AUDIO_DEVICE", "default")

    def command(executable: str, input_format: str, input_device: str) -> List[str]:
        return [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            input_format,
            "-i",
            input_device,
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-f",
            "s16le",
            "-",
        ]

    def commands_for(input_format: str) -> List[List[str]]:
        executables = ffmpeg_executables_for_capture()
        for executable in executables:
            if ffmpeg_supports_input_format(executable, input_format):
                return [command(executable, input_format, device)]
        return [command(executables[0], input_format, device)]

    if backend in ("pulse", "pipewire"):
        return commands_for("pulse")
    if backend == "alsa":
        return commands_for("alsa")
    if backend != "auto":
        raise ValueError("STT_AUDIO_BACKEND must be auto, pulse, pipewire, or alsa")

    # Prefer desktop audio on modern GNOME/PipeWire systems, then fall back.
    candidates: List[List[str]] = []
    candidates.extend(commands_for("pulse"))
    candidates.extend(commands_for("alsa"))
    return candidates
