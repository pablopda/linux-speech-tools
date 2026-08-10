#!/usr/bin/env python3
"""Best-effort target detection for developer dictation."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional


KNOWN_KINDS = {"auto", "claude", "codex", "ide", "terminal", "browser", "generic", "unknown"}

TERMINAL_PATTERNS = [
    "gnome-terminal",
    "org.gnome.terminal",
    "ptyxis",
    "kgx",
    "konsole",
    "tilix",
    "alacritty",
    "kitty",
    "wezterm",
    "ghostty",
    "xterm",
]
IDE_PATTERNS = [
    "code",
    "vscode",
    "codium",
    "cursor",
    "windsurf",
    "jetbrains",
    "pycharm",
    "webstorm",
    "intellij",
    "zed",
    "sublime",
]
BROWSER_PATTERNS = ["firefox", "chrome", "chromium", "brave", "edge", "vivaldi"]

GNOME_FOCUS_SCHEMA_VERSION = 1
GNOME_FOCUS_MAX_PAYLOAD = 32 * 1024
JS_MAX_SAFE_INTEGER = 9_007_199_254_740_991


@dataclass
class TargetContext:
    kind: str = "unknown"
    confidence: str = "low"
    source: str = "unknown"
    app_id: str = ""
    wm_class: str = ""
    title: str = ""
    pid: Optional[int] = None
    window_id: str = ""
    schema_version: Optional[int] = None
    shell_session_id: str = ""
    window_sequence: Optional[int] = None
    focus_generation: Optional[int] = None
    locked: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "confidence": self.confidence,
            "source": self.source,
            "app_id": self.app_id,
            "wm_class": self.wm_class,
            "title": self.title,
            "pid": self.pid,
            "window_id": self.window_id,
            "schema_version": self.schema_version,
            "shell_session_id": self.shell_session_id,
            "window_sequence": self.window_sequence,
            "focus_generation": self.focus_generation,
            "locked": self.locked,
        }


def detect_target(profile: str = "auto") -> TargetContext:
    profile = (profile or "auto").strip().lower()
    env_profile = os.environ.get("PROMPT_DICTATION_PROFILE", "").strip().lower()
    explicit = profile if profile != "auto" else env_profile
    if explicit == "auto":
        explicit = ""
    if explicit:
        if explicit not in KNOWN_KINDS:
            explicit = "generic"
        focused = live_focus_context()
        return TargetContext(
            kind=explicit,
            confidence="explicit",
            source="profile",
            app_id=focused.app_id,
            wm_class=focused.wm_class,
            title=focused.title,
            pid=focused.pid,
            window_id=focused.window_id,
            schema_version=focused.schema_version,
            shell_session_id=focused.shell_session_id,
            window_sequence=focused.window_sequence,
            focus_generation=focused.focus_generation,
            locked=focused.locked,
        )

    injected = os.environ.get("LST_TARGET_CONTEXT_JSON", "").strip()
    if injected:
        try:
            data = json.loads(injected)
            if not isinstance(data, dict):
                return TargetContext()
            if _looks_like_gnome_focus(data):
                return _focus_context_from_payload(data, source="env-json")
            return classify_context(
                app_id=str(data.get("app_id", "")),
                wm_class=str(data.get("wm_class", "")),
                title=str(data.get("title", "")),
                pid=_optional_int(data.get("pid")),
                window_id=str(data.get("window_id", "")),
                source="env-json",
            )
        except json.JSONDecodeError:
            pass

    focus = live_focus_context()
    # Preserve an authoritative stable identity even when the application is
    # not one of our classification patterns (for example GNOME Text Editor).
    # Explicit output modes can still use that identity safely, while `auto`
    # retains the context's low confidence and therefore stays conservative.
    if focus.source == "gnome-focus" or focus.kind != "unknown" or focus.window_id:
        return focus

    title = os.environ.get("PROMPT_DICTATION_TARGET_TITLE", "")
    if title:
        return classify_context(title=title, source="env-title")

    return TargetContext()


def live_focus_context() -> TargetContext:
    """Read the current desktop focus without using static profile overrides."""
    focus = gnome_focus_context()
    if focus.source == "gnome-focus" or focus.kind != "unknown" or focus.window_id:
        return focus

    x11 = x11_focus_context()
    if x11.kind != "unknown" or x11.window_id:
        return x11
    return TargetContext()


def focus_matches(expected: TargetContext) -> bool:
    """Return whether focus is still the exact target captured earlier.

    GNOME snapshots use both the window identity and focus generation. Thus a
    focus-away-and-back transition remains invalid even though Mutter reports
    the same stable window sequence again.
    """
    expected_id = expected.window_id.strip()
    if not expected_id:
        return False
    current = live_focus_context()
    if _is_gnome_target(expected) or _is_gnome_target(current):
        if not (_has_strong_gnome_identity(expected) and
                _has_strong_gnome_identity(current)):
            return False
        return (
            current.window_id == expected.window_id
            and current.shell_session_id == expected.shell_session_id
            and current.window_sequence == expected.window_sequence
            and current.focus_generation == expected.focus_generation
        )
    return bool(current.window_id) and current.window_id == expected_id


def gnome_focus_context() -> TargetContext:
    if not shutil.which("gdbus"):
        return TargetContext()
    try:
        result = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.linux_speech_tools.Focus",
                "--object-path",
                "/org/linux_speech_tools/Focus",
                "--method",
                "org.linux_speech_tools.Focus.GetFocus",
            ],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return TargetContext()
    if result.returncode != 0:
        return TargetContext()
    payload = _extract_dbus_string(result.stdout.strip())
    if not payload or len(payload) > GNOME_FOCUS_MAX_PAYLOAD:
        return TargetContext()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return TargetContext()
    return _focus_context_from_payload(data, source="gnome-focus")


def x11_focus_context() -> TargetContext:
    if not shutil.which("xdotool"):
        return TargetContext()
    try:
        active = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        window = active.stdout.strip()
        if active.returncode != 0 or not window:
            return TargetContext()
        title = subprocess.run(
            ["xdotool", "getwindowname", window],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        ).stdout.strip()
        wm_class = subprocess.run(
            ["xdotool", "getwindowclassname", window],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        ).stdout.strip()
        pid_text = subprocess.run(
            ["xdotool", "getwindowpid", window],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return TargetContext()
    return classify_context(
        wm_class=wm_class,
        title=title,
        pid=_optional_int(pid_text),
        window_id=window,
        source="x11",
    )


def classify_context(
    *,
    app_id: str = "",
    wm_class: str = "",
    title: str = "",
    pid: Optional[int] = None,
    window_id: str = "",
    source: str = "classify",
    schema_version: Optional[int] = None,
    shell_session_id: str = "",
    window_sequence: Optional[int] = None,
    focus_generation: Optional[int] = None,
    locked: Optional[bool] = None,
) -> TargetContext:
    haystack = " ".join([app_id, wm_class, title]).lower()
    kind = "unknown"
    confidence = "low"
    if re.search(r"\bclaude(\s+code)?\b", haystack):
        kind = "claude"
        confidence = "high"
    elif re.search(r"\bcodex\b", haystack):
        kind = "codex"
        confidence = "high"
    elif _matches_any(haystack, TERMINAL_PATTERNS):
        kind = "terminal"
        confidence = "medium"
    elif _matches_any(haystack, IDE_PATTERNS):
        kind = "ide"
        confidence = "medium"
    elif _matches_any(haystack, BROWSER_PATTERNS):
        kind = "browser"
        confidence = "medium"

    return TargetContext(
        kind=kind,
        confidence=confidence,
        source=source,
        app_id=app_id,
        wm_class=wm_class,
        title=title,
        pid=pid,
        window_id=window_id,
        schema_version=schema_version,
        shell_session_id=shell_session_id,
        window_sequence=window_sequence,
        focus_generation=focus_generation,
        locked=locked,
    )


def _looks_like_gnome_focus(data: dict) -> bool:
    window_id = data.get("window_id", "")
    return (
        "schema_version" in data
        or "shell_session_id" in data
        or "focus_generation" in data
        or (isinstance(window_id, str) and window_id.startswith("gnome:"))
    )


def _focus_context_from_payload(data, *, source: str) -> TargetContext:
    """Validate the complete v1 focus contract, returning unknown on error."""
    if not isinstance(data, dict):
        return TargetContext()

    schema_version = data.get("schema_version")
    shell_session_id = data.get("shell_session_id")
    window_sequence = data.get("window_sequence")
    focus_generation = data.get("focus_generation")
    locked = data.get("locked")
    window_id = data.get("window_id")

    if (not _plain_int(schema_version)
            or schema_version != GNOME_FOCUS_SCHEMA_VERSION):
        return TargetContext()
    if (not isinstance(shell_session_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", shell_session_id)):
        return TargetContext()
    if (not _safe_nonnegative_int(window_sequence)
            or not _safe_nonnegative_int(focus_generation)):
        return TargetContext()
    if not isinstance(locked, bool) or not isinstance(window_id, str):
        return TargetContext()

    common = {
        "source": source,
        "schema_version": schema_version,
        "shell_session_id": shell_session_id,
        "window_sequence": window_sequence,
        "focus_generation": focus_generation,
        "locked": locked,
    }

    if locked:
        # Keep the authorization state but deliberately discard any metadata
        # an unexpected provider included in a locked response.
        return TargetContext(**common)

    if window_sequence == 0:
        if window_id:
            return TargetContext()
        return TargetContext(**common)

    expected_id = "gnome:{}:{}".format(shell_session_id, window_sequence)
    if window_id != expected_id:
        return TargetContext()

    app_id = _bounded_payload_string(data.get("app_id"), 512)
    wm_class = _bounded_payload_string(data.get("wm_class"), 512)
    title = _bounded_payload_string(data.get("title"), 4096)
    if app_id is None or wm_class is None or title is None:
        return TargetContext()

    pid_value = data.get("pid")
    if pid_value is None:
        pid = None
    elif _plain_int(pid_value) and 0 < pid_value <= JS_MAX_SAFE_INTEGER:
        pid = pid_value
    else:
        return TargetContext()

    return classify_context(
        app_id=app_id,
        wm_class=wm_class,
        title=title,
        pid=pid,
        window_id=window_id,
        **common
    )


def _is_gnome_target(context: TargetContext) -> bool:
    return (
        context.source == "gnome-focus"
        or bool(context.shell_session_id)
        or context.window_id.startswith("gnome:")
    )


def _has_strong_gnome_identity(context: TargetContext) -> bool:
    return (
        context.schema_version == GNOME_FOCUS_SCHEMA_VERSION
        and bool(context.shell_session_id)
        and _safe_nonnegative_int(context.window_sequence)
        and context.window_sequence > 0
        and _safe_nonnegative_int(context.focus_generation)
        and context.locked is False
        and context.window_id == "gnome:{}:{}".format(
            context.shell_session_id, context.window_sequence)
    )


def _plain_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _safe_nonnegative_int(value) -> bool:
    return _plain_int(value) and 0 <= value <= JS_MAX_SAFE_INTEGER


def _bounded_payload_string(value, limit: int) -> Optional[str]:
    if not isinstance(value, str) or len(value) > limit:
        return None
    return value


def _matches_any(value: str, patterns: List[str]) -> bool:
    return any(pattern in value for pattern in patterns)


def _optional_int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _extract_dbus_string(value: str) -> str:
    value = value.strip()
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, tuple) and len(parsed) == 1:
            parsed = parsed[0]
        if isinstance(parsed, str):
            return parsed
    except (SyntaxError, ValueError):
        pass
    match = re.match(r"^\('(.+)'\,?\)$", value)
    if match:
        return match.group(1).replace("\\'", "'")
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value
