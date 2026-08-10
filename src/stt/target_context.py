#!/usr/bin/env python3
"""Best-effort target detection for developer dictation."""

from __future__ import annotations

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
        )

    injected = os.environ.get("LST_TARGET_CONTEXT_JSON", "").strip()
    if injected:
        try:
            data = json.loads(injected)
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
    if focus.kind != "unknown":
        return focus

    title = os.environ.get("PROMPT_DICTATION_TARGET_TITLE", "")
    if title:
        return classify_context(title=title, source="env-title")

    return TargetContext()


def live_focus_context() -> TargetContext:
    """Read the current desktop focus without using static profile overrides."""
    focus = gnome_focus_context()
    if focus.kind != "unknown" or focus.window_id:
        return focus

    x11 = x11_focus_context()
    if x11.kind != "unknown" or x11.window_id:
        return x11
    return TargetContext()


def focus_matches(expected: TargetContext) -> bool:
    """Return whether the current focus has the expected stable window ID."""
    expected_id = expected.window_id.strip()
    if not expected_id:
        return False
    current = live_focus_context()
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
    if not payload:
        return TargetContext()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return TargetContext()
    return classify_context(
        app_id=str(data.get("app_id", "")),
        wm_class=str(data.get("wm_class", "")),
        title=str(data.get("title", "")),
        pid=_optional_int(data.get("pid")),
        window_id=str(data.get("window_id", "")),
        source="gnome-focus",
    )


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
    )


def _matches_any(value: str, patterns: List[str]) -> bool:
    return any(pattern in value for pattern in patterns)


def _optional_int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _extract_dbus_string(value: str) -> str:
    value = value.strip()
    match = re.match(r"^\('(.+)'\,?\)$", value)
    if match:
        return match.group(1).replace("\\'", "'")
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value
