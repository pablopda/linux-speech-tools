#!/usr/bin/env python3
"""Safety-first, resumable live GNOME focus-provider acceptance harness.

The harness never transmits text, presses keys, submits a prompt, reads a text
buffer, or stores window titles/PIDs/raw desktop identifiers.  Potentially
mutating phases require an exact confirmation token and refuse to run while
the session is locked.  Application insertion checks are deliberately manual:
this process only records coarse outcomes after the user has prepared an
isolated disposable text buffer.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from src.stt.target_context import (
    GNOME_FOCUS_SCHEMA_VERSION,
    JS_MAX_SAFE_INTEGER,
    TargetContext,
    _focus_context_from_payload,
)


SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
EXTENSION_UUID = "speech-to-clipboard@linux-speech-tools"
FOCUS_BUS_NAME = "org.linux_speech_tools.Focus"
MAX_STATE_BYTES = 256 * 1024
MAX_REPORT_BYTES = 128 * 1024
MAX_EXTENSION_FILES = 128
MAX_EXTENSION_BYTES = 2 * 1024 * 1024
MAX_OBSERVATIONS = 32
RACE_ITERATIONS = 100
RACE_INTERVAL_MIN = 0.01
RACE_INTERVAL_MAX = 0.25
COMMAND_TIMEOUT_SECONDS = 5.0

REQUIRED_APPS = (
    "claude-code",
    "codex-cli",
    "ide",
    "chromium",
    "firefox",
    "gtk-editor",
)
EXPECTED_PROVIDER_CLASS = {
    "claude-code": "terminal",
    "codex-cli": "terminal",
    "ide": "ide",
    "chromium": "chromium",
    "firefox": "firefox",
    "gtk-editor": "gtk-editor",
}
OUTCOMES = (
    "inserted-exactly-once",
    "clipboard-fallback",
    "blocked-safe",
    "unavailable",
    "wrong-target",
    "duplicate",
    "lost-text",
    "skipped",
)
BACKENDS = ("paste", "live-type", "clipboard", "manual-other")
HARD_FAILURE_OUTCOMES = {"wrong-target", "duplicate", "lost-text"}
PASS_OUTCOME = "inserted-exactly-once"

CONFIRMATIONS = {
    "start": "START_LIVE_GNOME_ACCEPTANCE",
    "install": "INSTALL_REPOSITORY_PROVIDER",
    "lifecycle": "TEST_PROVIDER_LIFECYCLE",
    "race": "RUN_100_FOCUS_RACES",
    "finish": "FINALIZE_EVIDENCE",
    "restore": "RESTORE_EXACT_BASELINE",
}

_IDENTITY_PATTERNS = {
    "gtk-editor": {
        "gnome-text-editor",
        "org.gnome.texteditor",
        "org.gnome.texteditor.desktop",
    },
    "firefox": {"firefox", "firefox.desktop", "org.mozilla.firefox"},
    "chromium": {
        "chromium",
        "chromium-browser",
        "chromium.desktop",
        "google-chrome",
        "google-chrome.desktop",
    },
    "ide": {
        "code",
        "code.desktop",
        "codium",
        "codium.desktop",
        "com.visualstudio.code",
        "org.gnome.builder",
        "org.gnome.builder.desktop",
    },
    "terminal": {
        "gnome-terminal-server",
        "org.gnome.terminal",
        "org.gnome.terminal.desktop",
        "org.gnome.ptyxis",
        "org.gnome.ptyxis.desktop",
        "org.gnome.console",
        "org.gnome.console.desktop",
        "kgx",
        "ptyxis",
    },
}


class AcceptanceError(RuntimeError):
    """A stable, transcript-free acceptance diagnostic."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _safe_int(value: Any, *, minimum: int = 0) -> bool:
    return _plain_int(value) and minimum <= value <= JS_MAX_SAFE_INTEGER


def _safe_label(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return "unknown"
    value = value.strip()
    if len(value) > limit or not re.fullmatch(r"[A-Za-z0-9:._+@/() -]{1,128}", value):
        return "unknown"
    return value


def _session_type() -> str:
    value = os.environ.get("XDG_SESSION_TYPE", "").strip().casefold()
    return value if value in {"wayland", "x11"} else "unknown"


def _desktop_class() -> str:
    value = os.environ.get("XDG_CURRENT_DESKTOP", "").casefold()
    return "gnome" if "gnome" in value else "other" if value else "unknown"


def _shell_version() -> str:
    output = _run_text(["gnome-shell", "--version"])
    match = re.fullmatch(r"GNOME Shell ([0-9]+(?:\.[0-9]+){0,2})", output)
    return match.group(1) if match else "unknown"


def _fingerprint(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _identity_class(app_id: str, wm_class: str) -> str:
    identities = [
        value.strip().casefold()
        for value in (app_id, wm_class)
        if isinstance(value, str) and value.strip()
    ]
    if not identities:
        return "unknown"
    matched = []
    for identity in identities:
        identity_matches = [
            name for name, allowed in _IDENTITY_PATTERNS.items() if identity in allowed
        ]
        if len(identity_matches) != 1:
            return "other" if not identity_matches else "conflict"
        matched.append(identity_matches[0])
    if len(set(matched)) == 1:
        return matched[0]
    if len(set(matched)) > 1:
        return "conflict"
    return "other"


class FocusSample:
    """Validated provider snapshot with no title, PID, or transcript fields."""

    def __init__(
        self,
        *,
        shell_session_id: str,
        window_sequence: int,
        focus_generation: int,
        locked: bool,
        app_id: str,
        wm_class: str,
        window_id: str,
    ) -> None:
        self.shell_session_id = shell_session_id
        self.window_sequence = window_sequence
        self.focus_generation = focus_generation
        self.locked = locked
        self.app_id = app_id
        self.wm_class = wm_class
        self.window_id = window_id

    @classmethod
    def from_context(cls, context: TargetContext) -> "FocusSample":
        if context.source != "gnome-focus":
            raise AcceptanceError("focus-provider-unavailable-or-invalid")
        if context.schema_version != GNOME_FOCUS_SCHEMA_VERSION:
            raise AcceptanceError("focus-provider-schema-invalid")
        if (
            not isinstance(context.shell_session_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", context.shell_session_id)
        ):
            raise AcceptanceError("focus-provider-session-invalid")
        if not _safe_int(context.window_sequence):
            raise AcceptanceError("focus-provider-window-sequence-invalid")
        if not _safe_int(context.focus_generation):
            raise AcceptanceError("focus-provider-generation-invalid")
        if not isinstance(context.locked, bool):
            raise AcceptanceError("focus-provider-lock-state-invalid")
        if context.locked:
            if any(
                (
                    context.window_id,
                    context.app_id,
                    context.wm_class,
                    context.title,
                    context.pid is not None,
                )
            ):
                raise AcceptanceError("locked-provider-disclosed-metadata")
        elif context.window_sequence == 0:
            if context.window_id:
                raise AcceptanceError("no-focus-provider-identity-invalid")
        elif context.window_id != "gnome:{}:{}".format(
            context.shell_session_id, context.window_sequence
        ):
            raise AcceptanceError("focus-provider-window-identity-invalid")
        for value in (context.app_id, context.wm_class):
            if not isinstance(value, str) or len(value) > 512:
                raise AcceptanceError("focus-provider-application-identity-invalid")
        return cls(
            shell_session_id=context.shell_session_id,
            window_sequence=context.window_sequence,
            focus_generation=context.focus_generation,
            locked=context.locked,
            app_id=context.app_id,
            wm_class=context.wm_class,
            window_id=context.window_id,
        )

    @property
    def complete_unlocked(self) -> bool:
        return (
            self.locked is False
            and self.window_sequence > 0
            and bool(self.window_id)
            and bool(self.shell_session_id)
        )

    @property
    def provider_class(self) -> str:
        return _identity_class(self.app_id, self.wm_class)

    def same_authority(self, other: "FocusSample") -> bool:
        return bool(
            self.complete_unlocked
            and other.complete_unlocked
            and self.shell_session_id == other.shell_session_id
            and self.window_sequence == other.window_sequence
            and self.focus_generation == other.focus_generation
            and self.window_id == other.window_id
            and self.app_id.casefold() == other.app_id.casefold()
            and self.wm_class.casefold() == other.wm_class.casefold()
        )

    def public(self) -> Dict[str, Any]:
        return {
            "schema_version": GNOME_FOCUS_SCHEMA_VERSION,
            "session_fingerprint": _fingerprint(self.shell_session_id),
            "window_fingerprint": _fingerprint(self.window_id),
            "focus_generation": self.focus_generation,
            "locked": self.locked,
            "provider_class": self.provider_class,
            "complete_unlocked": self.complete_unlocked,
        }


def _generation_transition_valid(
    previous: FocusSample, current: FocusSample
) -> bool:
    """Require a monotonic generation and an advance on authority changes."""

    if previous.shell_session_id != current.shell_session_id:
        return True
    if current.focus_generation < previous.focus_generation:
        return False
    authority_changed = (
        previous.window_id != current.window_id
        or previous.app_id.casefold() != current.app_id.casefold()
        or previous.wm_class.casefold() != current.wm_class.casefold()
    )
    return not authority_changed or current.focus_generation > previous.focus_generation


def _focus_and_shell_owners() -> Tuple[str, str]:
    return _name_owner(FOCUS_BUS_NAME), _name_owner("org.gnome.Shell")


def _owners_are_stable_shell_connection(
    before: Tuple[str, str], after: Tuple[str, str]
) -> bool:
    provider_before, shell_before = before
    provider_after, shell_after = after
    return bool(
        provider_before
        and provider_before
        == shell_before
        == provider_after
        == shell_after
    )


def _require_stable_shell_provider_owner() -> None:
    before = _focus_and_shell_owners()
    after = _focus_and_shell_owners()
    if not _owners_are_stable_shell_connection(before, after):
        raise AcceptanceError("focus-provider-owner-is-not-stable-gnome-shell")


class ProviderReader:
    def read(self) -> FocusSample:
        owner_before = _focus_and_shell_owners()
        if not owner_before[0] or owner_before[0] != owner_before[1]:
            raise AcceptanceError("focus-provider-owner-is-not-stable-gnome-shell")
        raw = _run_text(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                FOCUS_BUS_NAME,
                "--object-path",
                "/org/linux_speech_tools/Focus",
                "--method",
                "org.linux_speech_tools.Focus.GetFocus",
            ],
            timeout=1.0,
        )
        owner_after = _focus_and_shell_owners()
        if not _owners_are_stable_shell_connection(owner_before, owner_after):
            raise AcceptanceError("focus-provider-owner-is-not-stable-gnome-shell")
        try:
            unpacked = ast.literal_eval(raw)
            if (
                not isinstance(unpacked, tuple)
                or len(unpacked) != 1
                or not isinstance(unpacked[0], str)
                or len(unpacked[0]) > 32 * 1024
            ):
                raise ValueError
            payload = json.loads(unpacked[0])
        except (SyntaxError, ValueError, json.JSONDecodeError):
            raise AcceptanceError("focus-provider-unavailable-or-invalid")
        context = _focus_context_from_payload(payload, source="gnome-focus")
        return FocusSample.from_context(context)


def _terminate_process(process: subprocess.Popen) -> None:
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=0.05)
    except (OSError, subprocess.TimeoutExpired):
        pass
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group, 0)
        except (OSError, ProcessLookupError):
            break
        time.sleep(0.01)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=0.5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_text(argv: Sequence[str], *, timeout: float = COMMAND_TIMEOUT_SECONDS) -> str:
    try:
        process = subprocess.Popen(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return ""
    if process.stdout is None:
        _terminate_process(process)
        return ""
    selector = None
    try:
        descriptor = process.stdout.fileno()
        os.set_blocking(descriptor, False)
        retained = bytearray()
        selector = selectors.DefaultSelector()
        selector.register(descriptor, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        pipe_open = True
        while pipe_open:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(process)
                return ""
            events = selector.select(min(remaining, 0.1))
            for key, _mask in events:
                try:
                    chunk = os.read(key.fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    pipe_open = False
                    selector.unregister(key.fd)
                    break
                if len(retained) + len(chunk) > 32 * 1024:
                    _terminate_process(process)
                    return ""
                retained.extend(chunk)
            if process.poll() is not None and not events:
                try:
                    chunk = os.read(descriptor, 4096)
                except BlockingIOError:
                    continue
                if chunk:
                    if len(retained) + len(chunk) > 32 * 1024:
                        _terminate_process(process)
                        return ""
                    retained.extend(chunk)
                else:
                    pipe_open = False
        try:
            returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _terminate_process(process)
            return ""
    except BaseException:
        _terminate_process(process)
        raise
    finally:
        if selector is not None:
            selector.close()
        process.stdout.close()
    if returncode != 0:
        return ""
    return retained.decode("utf-8", errors="replace").strip()


def _run_quiet(argv: Sequence[str], *, timeout: float) -> bool:
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    try:
        return process.wait(timeout=timeout) == 0
    except subprocess.TimeoutExpired:
        _terminate_process(process)
        return False
    except BaseException:
        _terminate_process(process)
        raise


def _gsettings_list(schema: str, key: str, *, pairs: bool = False) -> List[Any]:
    raw = _run_text(["gsettings", "get", schema, key])
    if raw.startswith("@"):
        raw = raw.split(" ", 1)[1] if " " in raw else "[]"
    try:
        value = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        raise AcceptanceError("desktop-setting-is-unavailable-or-invalid")
    if not isinstance(value, list) or len(value) > 128:
        raise AcceptanceError("desktop-setting-is-unavailable-or-invalid")
    if pairs:
        clean = []
        for item in value:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not all(
                    isinstance(part, str)
                    and bool(re.fullmatch(r"[A-Za-z0-9:._+@/-]{1,128}", part))
                    for part in item
                )
            ):
                raise AcceptanceError("desktop-setting-is-unavailable-or-invalid")
            clean.append([item[0], item[1]])
        return clean
    if not all(
        isinstance(item, str)
        and bool(re.fullmatch(r"[A-Za-z0-9._+@-]{1,256}", item))
        for item in value
    ):
        raise AcceptanceError("desktop-setting-is-unavailable-or-invalid")
    return list(value)


def _gsettings_bool(schema: str, key: str) -> Optional[bool]:
    raw = _run_text(["gsettings", "get", schema, key])
    if raw == "true":
        return True
    if raw == "false":
        return False
    return None


def _screen_locked() -> Optional[bool]:
    raw = _run_text(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.gnome.ScreenSaver",
            "--object-path",
            "/org/gnome/ScreenSaver",
            "--method",
            "org.gnome.ScreenSaver.GetActive",
        ]
    )
    if raw == "(true,)":
        return True
    if raw == "(false,)":
        return False
    return None


def _name_has_owner(name: str) -> bool:
    raw = _run_text(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.freedesktop.DBus",
            "--object-path",
            "/org/freedesktop/DBus",
            "--method",
            "org.freedesktop.DBus.NameHasOwner",
            name,
        ]
    )
    return raw == "(true,)"


def _name_owner(name: str) -> str:
    raw = _run_text(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.freedesktop.DBus",
            "--object-path",
            "/org/freedesktop/DBus",
            "--method",
            "org.freedesktop.DBus.GetNameOwner",
            name,
        ]
    )
    try:
        parsed = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        return ""
    if isinstance(parsed, tuple) and len(parsed) == 1 and isinstance(parsed[0], str):
        return parsed[0][:128]
    return ""


def _extension_info() -> Dict[str, Any]:
    output = _run_text(["gnome-extensions", "info", EXTENSION_UUID])
    info: Dict[str, Any] = {
        "registered": bool(output),
        "enabled": False,
        "state": "missing",
        "version": None,
    }
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Enabled:"):
            info["enabled"] = stripped.split(":", 1)[1].strip() == "Yes"
        elif stripped.startswith("State:"):
            state = stripped.split(":", 1)[1].strip().lower().replace(" ", "-")
            info["state"] = state[:64] if re.fullmatch(r"[a-z0-9_-]+", state) else "other"
        elif stripped.startswith("Version:"):
            raw_version = stripped.split(":", 1)[1].strip()
            if raw_version.isdigit():
                info["version"] = int(raw_version)
    return info


def _safe_engine(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9:._+@/-]{1,128}", value):
        return value
    return "unknown"


def capture_desktop_snapshot() -> Dict[str, Any]:
    enabled = _gsettings_list("org.gnome.shell", "enabled-extensions")
    disabled = _gsettings_list("org.gnome.shell", "disabled-extensions")
    provider_owner = _name_owner(FOCUS_BUS_NAME)
    shell_owner = _name_owner("org.gnome.Shell")
    return {
        "captured_at": _utc_now(),
        "session_type": _session_type(),
        "desktop": _desktop_class(),
        "shell_version": _shell_version(),
        "locked": _screen_locked(),
        "extension": _extension_info(),
        "extension_settings": {
            "enabled": enabled,
            "disabled": disabled,
            "disable_user_extensions": _gsettings_bool(
                "org.gnome.shell", "disable-user-extensions"
            ),
        },
        "input": {
            "engine": _safe_engine(_run_text(["ibus", "engine"], timeout=2.0)),
            "sources": _gsettings_list(
                "org.gnome.desktop.input-sources", "sources", pairs=True
            ),
            "mru_sources": _gsettings_list(
                "org.gnome.desktop.input-sources", "mru-sources", pairs=True
            ),
        },
        "provider": {
            "owned": bool(provider_owner),
            "owned_by_shell": bool(provider_owner and provider_owner == shell_owner),
        },
    }


def _input_state(snapshot: Mapping[str, Any]) -> Any:
    return snapshot.get("input")


def _session_is_safe(snapshot: Mapping[str, Any]) -> bool:
    settings = snapshot.get("extension_settings", {})
    input_state = snapshot.get("input", {})
    return (
        snapshot.get("session_type") == "wayland"
        and "gnome" in str(snapshot.get("desktop", "")).casefold()
        and snapshot.get("locked") is False
        and isinstance(settings.get("disable_user_extensions"), bool)
        and input_state.get("engine") != "unknown"
    )


def _provider_phase_snapshot_is_safe(snapshot: Mapping[str, Any]) -> bool:
    provider = snapshot.get("provider", {})
    return bool(
        _session_is_safe(snapshot)
        and isinstance(provider, Mapping)
        and provider.get("owned") is True
        and provider.get("owned_by_shell") is True
    )


def default_state_path() -> Path:
    override = os.environ.get("LST_GNOME_ACCEPTANCE_STATE", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise AcceptanceError("state-path-must-be-absolute")
        return path
    state_home = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    if not base.is_absolute():
        raise AcceptanceError("xdg-state-home-must-be-absolute")
    return base / "linux-speech-tools" / "gnome-acceptance-v1.json"


def _path_is_private_directory(path: Path) -> bool:
    try:
        item = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(item.st_mode)
        and not stat.S_ISLNK(item.st_mode)
        and item.st_uid == os.getuid()
        and stat.S_IMODE(item.st_mode) == 0o700
    )


def _prepare_parent(path: Path) -> None:
    default = default_state_path()
    managed_default = (
        not os.environ.get("LST_GNOME_ACCEPTANCE_STATE", "").strip()
        and path == default
    )
    if managed_default:
        _reject_symlink_components(path.parent)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _reject_symlink_components(path.parent)
        os.chmod(str(path.parent), 0o700)
    else:
        _reject_symlink_components(path.parent)
    if not _path_is_private_directory(path.parent):
        raise AcceptanceError("state-parent-must-be-private-owned-directory")


def _reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            item = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(item.st_mode):
            raise AcceptanceError("state-path-contains-symlink")


def _safe_regular_file(path: Path, *, allow_missing: bool) -> None:
    try:
        item = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return
        raise AcceptanceError("state-file-missing")
    if (
        stat.S_ISLNK(item.st_mode)
        or not stat.S_ISREG(item.st_mode)
        or item.st_uid != os.getuid()
        or item.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise AcceptanceError("state-file-must-be-private-owned-regular-file")
    if item.st_size > MAX_STATE_BYTES:
        raise AcceptanceError("state-file-too-large")


def _valid_string_list(value: Any, *, pair: bool = False) -> bool:
    if not isinstance(value, list) or len(value) > 128:
        return False
    if pair:
        return all(
            isinstance(item, list)
            and len(item) == 2
            and all(
                isinstance(part, str)
                and bool(re.fullmatch(r"[A-Za-z0-9:._+@/-]{1,128}", part))
                for part in item
            )
            for item in value
        )
    return all(
        isinstance(item, str)
        and bool(re.fullmatch(r"[A-Za-z0-9._+@-]{1,256}", item))
        for item in value
    )


def _valid_manifest(value: Any) -> bool:
    if not isinstance(value, dict) or len(value) > MAX_EXTENSION_FILES:
        return False
    total = 0
    for name, item in value.items():
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 512
            or name.startswith("/")
            or ".." in Path(name).parts
            or not isinstance(item, dict)
            or set(item) != {"sha256", "mode", "size"}
            or not isinstance(item["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
            or not _plain_int(item["mode"])
            or not 0 <= item["mode"] <= 0o7777
            or not _plain_int(item["size"])
            or not 0 <= item["size"] <= MAX_EXTENSION_BYTES
        ):
            return False
        total += item["size"]
    return total <= MAX_EXTENSION_BYTES


def _valid_snapshot(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "captured_at",
        "session_type",
        "desktop",
        "shell_version",
        "locked",
        "extension",
        "extension_settings",
        "input",
        "provider",
    }:
        return False
    for key, limit in (
        ("captured_at", 64),
        ("session_type", 32),
        ("desktop", 64),
        ("shell_version", 64),
    ):
        if (
            not isinstance(value[key], str)
            or len(value[key]) > limit
            or _safe_label(value[key], limit) != value[key]
        ):
            return False
    if value["locked"] is not None and not isinstance(value["locked"], bool):
        return False
    extension = value["extension"]
    if not isinstance(extension, dict) or set(extension) != {
        "registered",
        "enabled",
        "state",
        "version",
    }:
        return False
    if not isinstance(extension["registered"], bool) or not isinstance(
        extension["enabled"], bool
    ):
        return False
    if not isinstance(extension["state"], str) or not re.fullmatch(
        r"[a-z0-9_-]{1,64}", extension["state"]
    ):
        return False
    if extension["version"] is not None and not _safe_int(extension["version"]):
        return False
    settings = value["extension_settings"]
    if not isinstance(settings, dict) or set(settings) != {
        "enabled",
        "disabled",
        "disable_user_extensions",
    }:
        return False
    if not _valid_string_list(settings["enabled"]) or not _valid_string_list(
        settings["disabled"]
    ):
        return False
    if settings["disable_user_extensions"] is not None and not isinstance(
        settings["disable_user_extensions"], bool
    ):
        return False
    input_state = value["input"]
    if not isinstance(input_state, dict) or set(input_state) != {
        "engine",
        "sources",
        "mru_sources",
    }:
        return False
    if (
        not isinstance(input_state["engine"], str)
        or not re.fullmatch(r"[A-Za-z0-9:._+@/-]{1,128}", input_state["engine"])
        or not _valid_string_list(input_state["sources"], pair=True)
        or not _valid_string_list(input_state["mru_sources"], pair=True)
    ):
        return False
    provider = value["provider"]
    return (
        isinstance(provider, dict)
        and set(provider) == {"owned", "owned_by_shell"}
        and isinstance(provider["owned"], bool)
        and isinstance(provider["owned_by_shell"], bool)
    )


def _valid_public_focus(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value)
        == {
            "schema_version",
            "session_fingerprint",
            "window_fingerprint",
            "focus_generation",
            "locked",
            "provider_class",
            "complete_unlocked",
        }
        and value["schema_version"] == GNOME_FOCUS_SCHEMA_VERSION
        and isinstance(value["session_fingerprint"], str)
        and bool(re.fullmatch(r"[a-f0-9]{16}", value["session_fingerprint"]))
        and isinstance(value["window_fingerprint"], str)
        and bool(re.fullmatch(r"[a-f0-9]{16}", value["window_fingerprint"]))
        and _safe_int(value["focus_generation"])
        and isinstance(value["locked"], bool)
        and value["provider_class"]
        in {"terminal", "ide", "chromium", "firefox", "gtk-editor", "other", "conflict", "unknown"}
        and isinstance(value["complete_unlocked"], bool)
    )


def _valid_observation(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "at",
        "app",
        "expected_provider_class",
        "provider_class",
        "stable",
        "identity_matches",
        "session_fingerprint",
        "window_fingerprint",
        "focus_generation",
    }:
        return False
    return (
        isinstance(value["at"], str)
        and len(value["at"]) <= 64
        and value["app"] in REQUIRED_APPS
        and value["expected_provider_class"] == EXPECTED_PROVIDER_CLASS[value["app"]]
        and value["provider_class"]
        in {"terminal", "ide", "chromium", "firefox", "gtk-editor", "other", "conflict", "unknown"}
        and isinstance(value["stable"], bool)
        and isinstance(value["identity_matches"], bool)
        and bool(re.fullmatch(r"[a-f0-9]{16}", value["session_fingerprint"]))
        and bool(re.fullmatch(r"[a-f0-9]{16}", value["window_fingerprint"]))
        and _safe_int(value["focus_generation"])
    )


@contextlib.contextmanager
def _state_lock(path: Path) -> Iterator[None]:
    _prepare_parent(path)
    lock_path = path.with_name(path.name + ".lock")
    _safe_regular_file(lock_path, allow_missing=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(lock_path), flags, 0o600)
    try:
        item = os.fstat(descriptor)
        if not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid():
            raise AcceptanceError("state-lock-is-unsafe")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _validate_state(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise AcceptanceError("state-schema-invalid")
    required = {
        "schema_version",
        "run_id",
        "created_at",
        "updated_at",
        "status",
        "baseline",
        "current",
        "acceptance_after",
        "extension_backup",
        "installed_manifest",
        "lifecycle",
        "observations",
        "race",
        "app_outcomes",
        "events",
        "restore",
    }
    if set(value) != required or value.get("schema_version") != SCHEMA_VERSION:
        raise AcceptanceError("state-schema-invalid")
    if not isinstance(value.get("run_id"), str) or not re.fullmatch(
        r"[a-f0-9]{32}", value["run_id"]
    ):
        raise AcceptanceError("state-run-id-invalid")
    if not all(
        isinstance(value.get(key), str) and 1 <= len(value[key]) <= 64
        for key in ("created_at", "updated_at")
    ):
        raise AcceptanceError("state-timestamp-invalid")
    if value.get("status") not in {
        "active",
        "awaiting-login",
        "ready-to-finalize",
        "complete",
        "failed",
        "restored",
    }:
        raise AcceptanceError("state-status-invalid")
    if not _valid_snapshot(value.get("baseline")) or not _valid_snapshot(
        value.get("current")
    ):
        raise AcceptanceError("state-snapshot-invalid")
    if value.get("acceptance_after") is not None and not _valid_snapshot(
        value["acceptance_after"]
    ):
        raise AcceptanceError("state-snapshot-invalid")
    backup = value.get("extension_backup")
    if (
        not isinstance(backup, dict)
        or set(backup) != {"present", "manifest"}
        or not isinstance(backup["present"], bool)
        or not _valid_manifest(backup["manifest"])
        or (not backup["present"] and bool(backup["manifest"]))
    ):
        raise AcceptanceError("state-extension-backup-invalid")
    installed_manifest = value.get("installed_manifest")
    if installed_manifest is not None and not _valid_manifest(installed_manifest):
        raise AcceptanceError("state-installed-manifest-invalid")
    lifecycle = value.get("lifecycle")
    if lifecycle is not None:
        if not isinstance(lifecycle, dict) or set(lifecycle) != {
            "status",
            "name_released",
            "session_changed",
            "input_unchanged",
            "first",
            "second",
        }:
            raise AcceptanceError("state-lifecycle-invalid")
        if (
            lifecycle["status"] not in {"passed", "failed"}
            or not all(
                isinstance(lifecycle[key], bool)
                for key in ("name_released", "session_changed", "input_unchanged")
            )
            or (
                lifecycle["first"] is not None
                and not _valid_public_focus(lifecycle["first"])
            )
            or (
                lifecycle["second"] is not None
                and not _valid_public_focus(lifecycle["second"])
            )
        ):
            raise AcceptanceError("state-lifecycle-invalid")
    if (
        not isinstance(value.get("observations"), list)
        or len(value["observations"]) > MAX_OBSERVATIONS
        or not all(_valid_observation(item) for item in value["observations"])
    ):
        raise AcceptanceError("state-observations-invalid")
    race = value.get("race")
    if race is not None:
        if not isinstance(race, dict) or set(race) != {
            "iterations",
            "interval_ms",
            "stable_pairs",
            "drift_pairs",
            "drift_rejected",
            "invalid_samples",
            "generation_regressions",
            "session_changes",
            "away_back_verified",
            "failure_indices",
            "status",
        }:
            raise AcceptanceError("state-race-invalid")
        count_keys = (
            "iterations",
            "interval_ms",
            "stable_pairs",
            "drift_pairs",
            "drift_rejected",
            "invalid_samples",
            "generation_regressions",
            "session_changes",
        )
        if (
            race["iterations"] != RACE_ITERATIONS
            or not all(_safe_int(race[key]) for key in count_keys)
            or not isinstance(race["away_back_verified"], bool)
            or race["status"] not in {"passed", "failed"}
            or not isinstance(race["failure_indices"], list)
            or len(race["failure_indices"]) > 16
            or not all(
                _safe_int(index) and index < RACE_ITERATIONS
                for index in race["failure_indices"]
            )
        ):
            raise AcceptanceError("state-race-invalid")
    outcomes = value.get("app_outcomes")
    if not isinstance(outcomes, dict) or not set(outcomes).issubset(REQUIRED_APPS):
        raise AcceptanceError("state-app-outcomes-invalid")
    for app, outcome in outcomes.items():
        if (
            not isinstance(outcome, dict)
            or set(outcome)
            != {
                "recorded_at",
                "outcome",
                "backend",
                "disposable_buffer_confirmed",
                "automatic_typing_by_harness",
                "submit_by_harness",
            }
            or not isinstance(outcome["recorded_at"], str)
            or len(outcome["recorded_at"]) > 64
            or outcome["outcome"] not in OUTCOMES
            or outcome["backend"] not in BACKENDS
            or outcome["disposable_buffer_confirmed"] is not True
            or outcome["automatic_typing_by_harness"] is not False
            or outcome["submit_by_harness"] is not False
            or app not in REQUIRED_APPS
        ):
            raise AcceptanceError("state-app-outcomes-invalid")
    events = value.get("events")
    if not isinstance(events, list) or len(events) > 64:
        raise AcceptanceError("state-events-invalid")
    for event in events:
        if (
            not isinstance(event, dict)
            or set(event) != {"at", "phase", "result"}
            or not all(isinstance(event[key], str) for key in event)
            or len(event["at"]) > 64
            or not re.fullmatch(r"[a-z0-9-]{1,48}", event["phase"])
            or not re.fullmatch(r"[a-z0-9-]{1,48}", event["result"])
        ):
            raise AcceptanceError("state-events-invalid")
    restore = value.get("restore")
    if restore is not None:
        if not isinstance(restore, dict) or set(restore) != {
            "at",
            "files_restored",
            "extension_settings_restored",
            "input_restored",
            "shell_reload_may_be_required",
        }:
            raise AcceptanceError("state-restore-invalid")
        if (
            not isinstance(restore["at"], str)
            or len(restore["at"]) > 64
            or not all(
                isinstance(restore[key], bool)
                for key in (
                    "files_restored",
                    "extension_settings_restored",
                    "input_restored",
                    "shell_reload_may_be_required",
                )
            )
        ):
            raise AcceptanceError("state-restore-invalid")
    return value


def load_state(path: Path) -> Dict[str, Any]:
    _safe_regular_file(path, allow_missing=False)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(path), flags)
    try:
        raw = os.read(descriptor, MAX_STATE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_STATE_BYTES:
        raise AcceptanceError("state-file-too-large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AcceptanceError("state-json-invalid")
    return _validate_state(value)


def save_state(path: Path, state: Dict[str, Any]) -> None:
    _validate_state(state)
    state["updated_at"] = _utc_now()
    raw = (json.dumps(state, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(raw) > MAX_STATE_BYTES:
        raise AcceptanceError("state-would-exceed-size-limit")
    _prepare_parent(path)
    _safe_regular_file(path, allow_missing=True)
    descriptor, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp_name, str(path))
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _extension_dir() -> Path:
    return Path.home() / ".local" / "share" / "gnome-shell" / "extensions" / EXTENSION_UUID


def _hash_owned_regular_file(path: Path, remaining: int) -> Dict[str, Any]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(path), flags)
    digest = hashlib.sha256()
    total = 0
    try:
        item = os.fstat(descriptor)
        if (
            not stat.S_ISREG(item.st_mode)
            or item.st_uid != os.getuid()
            or item.st_size > remaining
        ):
            raise AcceptanceError("extension-tree-contains-unsafe-file")
        while True:
            chunk = os.read(descriptor, min(64 * 1024, remaining - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > remaining:
                raise AcceptanceError("extension-backup-exceeds-limit")
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return {
        "sha256": digest.hexdigest(),
        "mode": stat.S_IMODE(item.st_mode),
        "size": total,
    }


def _manifest(root: Path) -> Dict[str, Dict[str, Any]]:
    if not root.exists():
        return {}
    root_item = root.lstat()
    if (
        not stat.S_ISDIR(root_item.st_mode)
        or stat.S_ISLNK(root_item.st_mode)
        or root_item.st_uid != os.getuid()
    ):
        raise AcceptanceError("extension-directory-is-unsafe")
    manifest: Dict[str, Dict[str, Any]] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        item = path.lstat()
        if stat.S_ISDIR(item.st_mode):
            if stat.S_ISLNK(item.st_mode) or item.st_uid != os.getuid():
                raise AcceptanceError("extension-tree-contains-symlink")
            continue
        if not stat.S_ISREG(item.st_mode) or stat.S_ISLNK(item.st_mode):
            raise AcceptanceError("extension-tree-contains-special-file")
        if len(manifest) >= MAX_EXTENSION_FILES:
            raise AcceptanceError("extension-backup-exceeds-limit")
        relative = str(path.relative_to(root))
        entry = _hash_owned_regular_file(path, MAX_EXTENSION_BYTES - total)
        total += entry["size"]
        manifest[relative] = entry
    return manifest


def _backup_dir(state_path: Path) -> Path:
    return state_path.with_name(state_path.stem + ".extension-backup")


def _backup_extension(state_path: Path) -> Dict[str, Any]:
    # copytree deliberately retains the original extension modes so an exact
    # restore is possible.  The strictly 0700 state parent is therefore the
    # confidentiality boundary for the persisted backup.
    _prepare_parent(state_path)
    source = _extension_dir()
    backup = _backup_dir(state_path)
    if backup.exists() or backup.is_symlink():
        raise AcceptanceError("extension-backup-already-exists")
    manifest = _manifest(source)
    if source.exists():
        shutil.copytree(str(source), str(backup), symlinks=True)
        if _manifest(backup) != manifest:
            raise AcceptanceError("extension-backup-verification-failed")
    return {"present": source.exists(), "manifest": manifest}


def _event(state: Dict[str, Any], phase: str, result: str) -> None:
    state["events"].append(
        {"at": _utc_now(), "phase": phase[:48], "result": result[:48]}
    )
    del state["events"][:-64]


def _confirm(phase: str, supplied: str = "", *, app: str = "") -> None:
    token = CONFIRMATIONS.get(phase, "")
    if phase in {"observe", "record"}:
        normalized = app.upper().replace("-", "_")
        token = "{}_{}".format("OBSERVE" if phase == "observe" else "RECORD", normalized)
    if supplied == token:
        return
    if not sys.stdin.isatty():
        raise AcceptanceError("confirmation-required-{}".format(token))
    print("Type {} to continue this phase: ".format(token), end="", flush=True)
    try:
        entered = sys.stdin.readline(256).strip()
    except (OSError, UnicodeError):
        entered = ""
    if entered != token:
        raise AcceptanceError("confirmation-refused")


def _new_state(baseline: Dict[str, Any], backup: Dict[str, Any]) -> Dict[str, Any]:
    now = _utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": uuid.uuid4().hex,
        "created_at": now,
        "updated_at": now,
        "status": "active",
        "baseline": baseline,
        "current": baseline,
        "acceptance_after": None,
        "extension_backup": backup,
        "installed_manifest": None,
        "lifecycle": None,
        "observations": [],
        "race": None,
        "app_outcomes": {},
        "events": [],
        "restore": None,
    }


def start_run(state_path: Path, confirmation: str = "") -> Dict[str, Any]:
    _confirm("start", confirmation)
    with _state_lock(state_path):
        if state_path.exists():
            existing = load_state(state_path)
            if existing["status"] != "restored":
                raise AcceptanceError("active-run-already-exists-use-resume")
            raise AcceptanceError("archived-state-exists-select-new-state-path")
        baseline = capture_desktop_snapshot()
        if not _session_is_safe(baseline):
            raise AcceptanceError("requires-unlocked-gnome-wayland-session")
        backup = _backup_extension(state_path)
        state = _new_state(baseline, backup)
        _event(state, "baseline", "captured")
        save_state(state_path, state)
        return state


def _require_active(state: Mapping[str, Any]) -> None:
    if state.get("status") in {"complete", "restored"}:
        raise AcceptanceError("run-is-not-active")


def install_provider(state_path: Path, confirmation: str = "") -> Dict[str, Any]:
    _confirm("install", confirmation)
    with _state_lock(state_path):
        state = load_state(state_path)
        _require_active(state)
        before = capture_desktop_snapshot()
        if not _session_is_safe(before):
            raise AcceptanceError("requires-unlocked-gnome-wayland-session")
        project_root = Path(__file__).resolve().parents[2]
        installer = project_root / "scripts" / "install" / "install-gnome-integration.sh"
        if not installer.is_file():
            raise AcceptanceError("gnome-installer-unavailable")
        installed = _run_quiet(
            [
                str(installer),
                "--extension",
                "--noninteractive",
            ],
            timeout=30.0,
        )
        try:
            after = capture_desktop_snapshot()
        except AcceptanceError:
            state["installed_manifest"] = _manifest(_extension_dir())
            state["status"] = "failed"
            _event(state, "provider-install", "after-snapshot-failed")
            save_state(state_path, state)
            raise
        state["current"] = after
        state["installed_manifest"] = _manifest(_extension_dir())
        if not installed:
            state["status"] = "failed"
            _event(state, "provider-install", "failed")
        elif _input_state(before) != _input_state(after):
            state["status"] = "failed"
            _event(state, "provider-install", "input-state-changed")
        else:
            if after["provider"]["owned"] and after["provider"]["owned_by_shell"]:
                state["status"] = "active"
                _event(state, "provider-install", "provider-available")
            else:
                state["status"] = "awaiting-login"
                _event(state, "provider-install", "awaiting-login")
        save_state(state_path, state)
        return state


def _wait_for_provider(
    reader: ProviderReader, *, available: bool, timeout: float = 5.0
) -> Optional[FocusSample]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        owner = _name_has_owner(FOCUS_BUS_NAME)
        if available and owner:
            try:
                return reader.read()
            except AcceptanceError:
                pass
        if not available and not owner:
            return None
        time.sleep(0.1)
    raise AcceptanceError(
        "provider-did-not-become-{}".format("available" if available else "unavailable")
    )


def _set_extension_enabled(enabled: bool) -> None:
    action = "enable" if enabled else "disable"
    if not _run_quiet(
        ["gnome-extensions", action, EXTENSION_UUID],
        timeout=COMMAND_TIMEOUT_SECONDS,
    ):
        raise AcceptanceError("extension-{}-failed".format(action))


def run_lifecycle(
    state_path: Path,
    confirmation: str = "",
    *,
    reader: Optional[ProviderReader] = None,
) -> Dict[str, Any]:
    _confirm("lifecycle", confirmation)
    reader = reader or ProviderReader()
    with _state_lock(state_path):
        state = load_state(state_path)
        _require_active(state)
        before = capture_desktop_snapshot()
        if not _session_is_safe(before):
            raise AcceptanceError("requires-unlocked-gnome-wayland-session")
        was_enabled = bool(before["extension"].get("enabled"))
        lifecycle: Dict[str, Any] = {
            "status": "failed",
            "name_released": False,
            "session_changed": False,
            "input_unchanged": False,
            "first": None,
            "second": None,
        }
        try:
            _set_extension_enabled(True)
            first = _wait_for_provider(reader, available=True)
            if first is None or not first.complete_unlocked:
                raise AcceptanceError("provider-focus-is-not-complete-unlocked")
            _require_stable_shell_provider_owner()
            lifecycle["first"] = first.public()
            _set_extension_enabled(False)
            _wait_for_provider(reader, available=False)
            lifecycle["name_released"] = True
            _set_extension_enabled(True)
            second = _wait_for_provider(reader, available=True)
            if second is None or not second.complete_unlocked:
                raise AcceptanceError("provider-reenable-focus-is-not-complete")
            _require_stable_shell_provider_owner()
            lifecycle["second"] = second.public()
            lifecycle["session_changed"] = (
                first.shell_session_id != second.shell_session_id
            )
            if not lifecycle["session_changed"]:
                raise AcceptanceError("provider-session-id-was-reused")
            after = capture_desktop_snapshot()
            if not _provider_phase_snapshot_is_safe(after):
                raise AcceptanceError("provider-lifecycle-owner-is-not-gnome-shell")
            lifecycle["input_unchanged"] = _input_state(before) == _input_state(after)
            if not lifecycle["input_unchanged"]:
                raise AcceptanceError("provider-lifecycle-changed-input-state")
            lifecycle["status"] = "passed"
            state["status"] = "active"
            state["current"] = after
            _event(state, "provider-lifecycle", "passed")
        except AcceptanceError:
            try:
                _set_extension_enabled(was_enabled)
            except AcceptanceError:
                pass
            state["status"] = "failed"
            try:
                state["current"] = capture_desktop_snapshot()
            except AcceptanceError:
                state["current"] = before
            _event(state, "provider-lifecycle", "failed")
            state["lifecycle"] = lifecycle
            save_state(state_path, state)
            raise
        state["lifecycle"] = lifecycle
        save_state(state_path, state)
        return state


def observe_app(
    state_path: Path,
    app: str,
    confirmation: str = "",
    *,
    delay_seconds: float = 5.0,
    reader: Optional[ProviderReader] = None,
) -> Dict[str, Any]:
    if app not in REQUIRED_APPS:
        raise AcceptanceError("unsupported-application-class")
    _confirm("observe", confirmation, app=app)
    if not 1.0 <= delay_seconds <= 15.0:
        raise AcceptanceError("observation-delay-out-of-range")
    reader = reader or ProviderReader()
    with _state_lock(state_path):
        state = load_state(state_path)
        _require_active(state)
        snapshot = capture_desktop_snapshot()
        if not _provider_phase_snapshot_is_safe(snapshot):
            raise AcceptanceError("requires-stable-gnome-shell-focus-provider")
        print(
            "Switch now to the prepared disposable {} buffer; capture in {:.1f}s.".format(
                app, delay_seconds
            ),
            file=sys.stderr,
        )
        time.sleep(delay_seconds)
        first = reader.read()
        time.sleep(0.1)
        second = reader.read()
        if not first.complete_unlocked or not second.complete_unlocked:
            state["status"] = "failed"
            _event(state, "observe-{}".format(app), "incomplete-focus")
            save_state(state_path, state)
            raise AcceptanceError("observed-focus-is-not-complete-unlocked")
        generation_transition_valid = _generation_transition_valid(first, second)
        stable = first.same_authority(second) and generation_transition_valid
        expected = EXPECTED_PROVIDER_CLASS[app]
        identity_matches = second.provider_class == expected
        observation = {
            "at": _utc_now(),
            "app": app,
            "expected_provider_class": expected,
            "provider_class": second.provider_class,
            "stable": stable,
            "identity_matches": identity_matches,
            "session_fingerprint": _fingerprint(second.shell_session_id),
            "window_fingerprint": _fingerprint(second.window_id),
            "focus_generation": second.focus_generation,
        }
        state["observations"].append(observation)
        del state["observations"][:-MAX_OBSERVATIONS]
        state["current"] = capture_desktop_snapshot()
        ending_provider_safe = _provider_phase_snapshot_is_safe(state["current"])
        _event(
            state,
            "observe-{}".format(app),
            "passed"
            if stable and identity_matches and ending_provider_safe
            else "failed",
        )
        if not stable or not identity_matches or not ending_provider_safe:
            state["status"] = "failed"
        save_state(state_path, state)
        if not ending_provider_safe:
            raise AcceptanceError("focus-provider-owner-changed-during-observation")
        if not generation_transition_valid:
            raise AcceptanceError("focus-generation-transition-invalid")
        if not stable:
            raise AcceptanceError("focus-changed-during-observation")
        if not identity_matches:
            raise AcceptanceError("observed-application-identity-mismatch")
        return state


def run_focus_race(
    state_path: Path,
    confirmation: str = "",
    *,
    interval_seconds: float = 0.03,
    reader: Optional[ProviderReader] = None,
) -> Dict[str, Any]:
    _confirm("race", confirmation)
    if not RACE_INTERVAL_MIN <= interval_seconds <= RACE_INTERVAL_MAX:
        raise AcceptanceError("race-interval-out-of-range")
    reader = reader or ProviderReader()
    with _state_lock(state_path):
        state = load_state(state_path)
        _require_active(state)
        snapshot = capture_desktop_snapshot()
        if not _provider_phase_snapshot_is_safe(snapshot):
            raise AcceptanceError("requires-stable-gnome-shell-focus-provider")
        print(
            "For the next {:.1f}s, alternate only between prepared disposable windows.".format(
                RACE_ITERATIONS * interval_seconds * 2
            ),
            file=sys.stderr,
        )
        stable_pairs = 0
        drift_pairs = 0
        drift_rejected = 0
        invalid_samples = 0
        failure_indices: List[int] = []
        previous_observed: Optional[FocusSample] = None
        generation_regressions = 0
        session_changes = 0
        sample_history: List[Dict[str, Any]] = []
        for index in range(RACE_ITERATIONS):
            try:
                expected = reader.read()
                time.sleep(interval_seconds)
                current = reader.read()
                if not expected.complete_unlocked or not current.complete_unlocked:
                    raise AcceptanceError("race-focus-is-not-complete-unlocked")
            except AcceptanceError:
                invalid_samples += 1
                if len(failure_indices) < 16:
                    failure_indices.append(index)
                continue
            authorized = expected.same_authority(current)
            if authorized:
                stable_pairs += 1
            else:
                drift_pairs += 1
                drift_rejected += 1
            for observed in (expected, current):
                if (
                    previous_observed is not None
                    and observed.shell_session_id
                    != previous_observed.shell_session_id
                ):
                    session_changes += 1
                if (
                    previous_observed is not None
                    and not _generation_transition_valid(previous_observed, observed)
                ):
                    generation_regressions += 1
                    if len(failure_indices) < 16:
                        failure_indices.append(index)
                previous_observed = observed
                sample_history.append(
                    {
                        "window_fingerprint": _fingerprint(observed.window_id),
                        "session_fingerprint": _fingerprint(
                            observed.shell_session_id
                        ),
                        "focus_generation": observed.focus_generation,
                        "provider_class": observed.provider_class,
                    }
                )
            time.sleep(interval_seconds)
        away_back_verified = _focus_transition_gate(sample_history)
        state["current"] = capture_desktop_snapshot()
        ending_provider_safe = _provider_phase_snapshot_is_safe(state["current"])
        passed = (
            invalid_samples == 0
            and generation_regressions == 0
            and session_changes == 0
            and drift_pairs > 0
            and drift_rejected == drift_pairs
            and stable_pairs + drift_pairs == RACE_ITERATIONS
            and away_back_verified
            and ending_provider_safe
        )
        state["race"] = {
            "iterations": RACE_ITERATIONS,
            "interval_ms": int(interval_seconds * 1000),
            "stable_pairs": stable_pairs,
            "drift_pairs": drift_pairs,
            "drift_rejected": drift_rejected,
            "invalid_samples": invalid_samples,
            "generation_regressions": generation_regressions,
            "session_changes": session_changes,
            "away_back_verified": away_back_verified,
            "failure_indices": failure_indices,
            "status": "passed" if passed else "failed",
        }
        if not passed:
            state["status"] = "failed"
        _event(state, "focus-race", "passed" if passed else "failed")
        save_state(state_path, state)
        if not passed:
            raise AcceptanceError("focus-race-gate-failed")
        return state


def record_app_outcome(
    state_path: Path,
    app: str,
    outcome: str,
    backend: str,
    confirmation: str = "",
) -> Dict[str, Any]:
    if app not in REQUIRED_APPS:
        raise AcceptanceError("unsupported-application-class")
    if outcome not in OUTCOMES:
        raise AcceptanceError("unsupported-insertion-outcome")
    if backend not in BACKENDS:
        raise AcceptanceError("unsupported-insertion-backend")
    _confirm("record", confirmation, app=app)
    with _state_lock(state_path):
        state = load_state(state_path)
        _require_active(state)
        matching = [
            item
            for item in state["observations"]
            if item.get("app") == app
            and item.get("stable") is True
            and item.get("identity_matches") is True
        ]
        if not matching:
            raise AcceptanceError("application-must-be-observed-before-recording")
        existing = state["app_outcomes"].get(app)
        if existing and existing.get("outcome") in HARD_FAILURE_OUTCOMES:
            raise AcceptanceError("hard-failure-outcome-is-latched")
        state["app_outcomes"][app] = {
            "recorded_at": _utc_now(),
            "outcome": outcome,
            "backend": backend,
            "disposable_buffer_confirmed": True,
            "automatic_typing_by_harness": False,
            "submit_by_harness": False,
        }
        if outcome in HARD_FAILURE_OUTCOMES:
            state["status"] = "failed"
        _event(state, "record-{}".format(app), outcome)
        save_state(state_path, state)
        return state


def _focus_transition_gate(observations: Sequence[Mapping[str, Any]]) -> bool:
    for first_index, first in enumerate(observations):
        for second_index in range(first_index + 2, len(observations)):
            second = observations[second_index]
            between = observations[first_index + 1 : second_index]
            candidate = observations[first_index : second_index + 1]
            transitions_valid = all(
                previous.get("session_fingerprint")
                == current.get("session_fingerprint")
                == first.get("session_fingerprint")
                and _safe_int(previous.get("focus_generation"))
                and _safe_int(current.get("focus_generation"))
                and current["focus_generation"] >= previous["focus_generation"]
                and (
                    (
                        current.get("window_fingerprint")
                        == previous.get("window_fingerprint")
                        and current.get("provider_class")
                        == previous.get("provider_class")
                    )
                    or current["focus_generation"] > previous["focus_generation"]
                )
                for previous, current in zip(candidate, candidate[1:])
            )
            if (
                first.get("window_fingerprint")
                and first.get("window_fingerprint") == second.get("window_fingerprint")
                and first.get("session_fingerprint") == second.get("session_fingerprint")
                and _safe_int(first.get("focus_generation"))
                and _safe_int(second.get("focus_generation"))
                and second["focus_generation"] > first["focus_generation"]
                and transitions_valid
                and any(
                    item.get("window_fingerprint") != first.get("window_fingerprint")
                    for item in between
                )
            ):
                return True
    return False


def _overall(state: Mapping[str, Any]) -> str:
    outcomes = state.get("app_outcomes", {})
    if any(item.get("outcome") in HARD_FAILURE_OUTCOMES for item in outcomes.values()):
        return "failed"
    lifecycle = state.get("lifecycle") or {}
    race = state.get("race") or {}
    complete = (
        lifecycle.get("status") == "passed"
        and race.get("status") == "passed"
        and _focus_transition_gate(state.get("observations", []))
        and set(outcomes) == set(REQUIRED_APPS)
        and all(item.get("outcome") == PASS_OUTCOME for item in outcomes.values())
    )
    return "complete-manual-evidence" if complete else "incomplete"


def _public_snapshot(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    extension = snapshot.get("extension", {})
    settings = snapshot.get("extension_settings", {})
    return {
        "captured_at": snapshot.get("captured_at"),
        "session_type": snapshot.get("session_type"),
        "desktop": snapshot.get("desktop"),
        "shell_version": snapshot.get("shell_version"),
        "locked": snapshot.get("locked"),
        "extension": {
            "registered": extension.get("registered"),
            "enabled": extension.get("enabled"),
            "state": extension.get("state"),
            "version": extension.get("version"),
            "target_in_enabled_settings": EXTENSION_UUID in settings.get("enabled", []),
            "target_in_disabled_settings": EXTENSION_UUID in settings.get("disabled", []),
            "disable_user_extensions": settings.get("disable_user_extensions"),
        },
        "input": snapshot.get("input"),
        "provider": snapshot.get("provider"),
    }


def build_report(state: Mapping[str, Any]) -> Dict[str, Any]:
    overall = _overall(state)
    missing_apps = sorted(set(REQUIRED_APPS) - set(state.get("app_outcomes", {})))
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": state.get("run_id"),
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
        "overall": overall,
        "safety": {
            "transcripts_recorded": False,
            "titles_recorded": False,
            "pids_recorded": False,
            "raw_session_or_window_ids_recorded": False,
            "automatic_typing_by_harness": False,
            "automatic_submission_by_harness": False,
            "application_outcomes_are_manual_attestation": True,
            "application_content_verified_by_harness": False,
        },
        "before": _public_snapshot(state.get("baseline", {})),
        "after_acceptance": _public_snapshot(
            state.get("acceptance_after") or state.get("current", {})
        ),
        "after_restore": (
            _public_snapshot(state.get("current", {}))
            if state.get("restore") is not None
            else None
        ),
        "lifecycle": state.get("lifecycle"),
        "focus_transition_passed": _focus_transition_gate(
            state.get("observations", [])
        ),
        "observations": state.get("observations", []),
        "race": state.get("race"),
        "app_outcomes": state.get("app_outcomes", {}),
        "missing_apps": missing_apps,
        "restore": state.get("restore"),
    }


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# GNOME live acceptance evidence",
        "",
        "- Run: `{}`".format(report.get("run_id", "")),
        "- Overall record: **{}**".format(report.get("overall", "incomplete")),
        "- Transcript/title/PID/raw-identity storage: **disabled**",
        "- Harness typing/submission: **disabled**",
        "- Application insertion results: **manual tester attestation only**",
        "",
        "## Environment",
        "",
        "| Snapshot | Shell | Session | Locked | Extension | Engine |",
        "|---|---|---|---:|---|---|",
    ]
    for label in ("before", "after_acceptance", "after_restore"):
        item = report.get(label, {})
        if not item:
            continue
        extension = item.get("extension", {})
        input_state = item.get("input", {})
        lines.append(
            "| {} | {} | {} | {} | {} / enabled={} | {} |".format(
                label,
                item.get("shell_version", "unknown"),
                item.get("session_type", "unknown"),
                item.get("locked"),
                extension.get("state", "unknown"),
                extension.get("enabled"),
                input_state.get("engine", "unknown"),
            )
        )
    lines.extend(["", "## Application outcomes", "", "| App | Backend | Outcome |", "|---|---|---|"])
    outcomes = report.get("app_outcomes", {})
    for app in REQUIRED_APPS:
        item = outcomes.get(app, {})
        lines.append(
            "| {} | {} | {} |".format(
                app, item.get("backend", "missing"), item.get("outcome", "missing")
            )
        )
    race = report.get("race") or {}
    lifecycle = report.get("lifecycle") or {}
    lines.extend(
        [
            "",
            "## Gates",
            "",
            "- Provider lifecycle: `{}`".format(lifecycle.get("status", "missing")),
            "- Focus away/back transition: `{}`".format(
                report.get("focus_transition_passed", False)
            ),
            "- Focus race: `{}` ({} iterations, {} drift pairs rejected)".format(
                race.get("status", "missing"),
                race.get("iterations", 0),
                race.get("drift_rejected", 0),
            ),
            "- Race A→B→A generation advance: `{}`".format(
                race.get("away_back_verified", False)
            ),
            "- Missing application outcomes: `{}`".format(
                ", ".join(report.get("missing_apps", [])) or "none"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _write_private(path: Path, data: bytes, limit: int) -> None:
    if len(data) > limit:
        raise AcceptanceError("evidence-report-exceeds-size-limit")
    _prepare_parent(path)
    _safe_regular_file(path, allow_missing=True)
    descriptor, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_reports(state_path: Path, state: Mapping[str, Any]) -> Dict[str, str]:
    report = build_report(state)
    json_path = state_path.with_name(state_path.name + ".evidence.json")
    markdown_path = state_path.with_name(state_path.name + ".evidence.md")
    raw_json = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode("utf-8")
    raw_markdown = (_markdown(report) + "\n").encode("utf-8")
    _write_private(json_path, raw_json, MAX_REPORT_BYTES)
    _write_private(markdown_path, raw_markdown, MAX_REPORT_BYTES)
    return {"json": str(json_path), "markdown": str(markdown_path)}


def finalize_run(state_path: Path, confirmation: str = "") -> Dict[str, Any]:
    _confirm("finish", confirmation)
    with _state_lock(state_path):
        state = load_state(state_path)
        if state["status"] == "restored":
            raise AcceptanceError("run-is-not-active")
        if state["status"] == "complete":
            paths = write_reports(state_path, state)
            return {"state": state, "reports": paths}
        state["current"] = capture_desktop_snapshot()
        state["acceptance_after"] = state["current"]
        overall = _overall(state)
        state["status"] = (
            "complete"
            if overall == "complete-manual-evidence"
            else "failed"
            if overall == "failed"
            else "ready-to-finalize"
        )
        _event(state, "finalize", overall)
        save_state(state_path, state)
        paths = write_reports(state_path, state)
        return {"state": state, "reports": paths}


def _gsettings_set_list(schema: str, key: str, values: Sequence[Any]) -> None:
    if values and isinstance(values[0], list):
        serialized = "[{}]".format(
            ", ".join("({}, {})".format(repr(item[0]), repr(item[1])) for item in values)
        )
    else:
        serialized = repr(list(values))
    if not _run_quiet(
        ["gsettings", "set", schema, key, serialized],
        timeout=COMMAND_TIMEOUT_SECONDS,
    ):
        raise AcceptanceError("baseline-settings-restore-failed")


def _restore_extension_files(state_path: Path, state: Mapping[str, Any]) -> None:
    target = _extension_dir()
    baseline_manifest = state["extension_backup"].get("manifest")
    quarantine = target.parent / (
        ".{}-lst-quarantine-{}".format(
            EXTENSION_UUID, str(state.get("run_id"))[:12]
        )
    )
    if quarantine.exists() or quarantine.is_symlink():
        _manifest(quarantine)
        current_manifest = _manifest(target)
        if current_manifest == baseline_manifest:
            return
        if target.exists():
            raise AcceptanceError("partial-extension-restore-needs-manual-review")
    else:
        current_manifest = _manifest(target)
        installed_manifest = state.get("installed_manifest")
        if installed_manifest is not None and current_manifest != installed_manifest:
            raise AcceptanceError("extension-files-changed-outside-harness")
        if target.exists():
            os.replace(str(target), str(quarantine))
    backup_info = state.get("extension_backup", {})
    if backup_info.get("present"):
        backup = _backup_dir(state_path)
        if _manifest(backup) != backup_info.get("manifest"):
            raise AcceptanceError("extension-backup-no-longer-valid")
        shutil.copytree(str(backup), str(target), symlinks=True)
        if _manifest(target) != backup_info.get("manifest"):
            raise AcceptanceError("restored-extension-verification-failed")


def restore_run(state_path: Path, confirmation: str = "") -> Dict[str, Any]:
    _confirm("restore", confirmation)
    with _state_lock(state_path):
        state = load_state(state_path)
        current = capture_desktop_snapshot()
        if not _session_is_safe(current):
            raise AcceptanceError("requires-unlocked-gnome-wayland-session")
        baseline = state["baseline"]
        if state.get("acceptance_after") is None:
            state["acceptance_after"] = current
        try:
            _set_extension_enabled(False)
        except AcceptanceError:
            pass
        _restore_extension_files(state_path, state)
        settings = baseline["extension_settings"]
        _gsettings_set_list("org.gnome.shell", "enabled-extensions", settings["enabled"])
        _gsettings_set_list("org.gnome.shell", "disabled-extensions", settings["disabled"])
        disabled_all = settings.get("disable_user_extensions")
        if not isinstance(disabled_all, bool):
            raise AcceptanceError("baseline-extension-setting-invalid")
        if not _run_quiet(
            [
                "gsettings",
                "set",
                "org.gnome.shell",
                "disable-user-extensions",
                "true" if disabled_all else "false",
            ],
            timeout=COMMAND_TIMEOUT_SECONDS,
        ):
            raise AcceptanceError("baseline-extension-setting-restore-failed")
        input_state = baseline["input"]
        _gsettings_set_list(
            "org.gnome.desktop.input-sources", "sources", input_state["sources"]
        )
        _gsettings_set_list(
            "org.gnome.desktop.input-sources", "mru-sources", input_state["mru_sources"]
        )
        engine = input_state.get("engine")
        if engine and engine != "unknown":
            _run_quiet(
                ["ibus", "engine", engine],
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        after = capture_desktop_snapshot()
        state["current"] = after
        state["restore"] = {
            "at": _utc_now(),
            "files_restored": _manifest(_extension_dir())
            == state["extension_backup"].get("manifest"),
            "extension_settings_restored": after["extension_settings"] == settings,
            "input_restored": after["input"] == input_state,
            "shell_reload_may_be_required": True,
        }
        restored = all(
            state["restore"][key]
            for key in ("files_restored", "extension_settings_restored", "input_restored")
        )
        state["status"] = "restored" if restored else "failed"
        _event(state, "restore", "passed" if restored else "failed")
        save_state(state_path, state)
        write_reports(state_path, state)
        if not restored:
            raise AcceptanceError("baseline-restore-verification-failed")
        return state


def _state_summary(state: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "overall": _overall(state),
        "lifecycle": (state.get("lifecycle") or {}).get("status", "missing"),
        "observations": len(state.get("observations", [])),
        "race": (state.get("race") or {}).get("status", "missing"),
        "recorded_apps": sorted(state.get("app_outcomes", {})),
        "missing_apps": sorted(
            set(REQUIRED_APPS) - set(state.get("app_outcomes", {}))
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resumable, transcript-free live GNOME acceptance harness"
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=default_state_path(),
        help="private absolute state path (default: XDG state directory)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "install-provider", "lifecycle", "race", "finish", "restore"):
        command = subparsers.add_parser(name)
        command.add_argument("--confirm", default="")
    observe = subparsers.add_parser("observe-app")
    observe.add_argument("--app", choices=REQUIRED_APPS, required=True)
    observe.add_argument("--delay", type=float, default=5.0)
    observe.add_argument("--confirm", default="")
    record = subparsers.add_parser("record-app")
    record.add_argument("--app", choices=REQUIRED_APPS, required=True)
    record.add_argument("--outcome", choices=OUTCOMES, required=True)
    record.add_argument("--backend", choices=BACKENDS, required=True)
    record.add_argument("--confirm", default="")
    report = subparsers.add_parser("report")
    report.add_argument("--format", choices=("json", "markdown"), default="markdown")
    subparsers.add_parser("status")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        state_path = args.state.expanduser()
        if not state_path.is_absolute():
            print("Error: state path must be absolute", file=sys.stderr)
            return 2
        if args.command == "start":
            state = start_run(state_path, args.confirm)
            output: Any = _state_summary(state)
        elif args.command == "install-provider":
            state = install_provider(state_path, args.confirm)
            output = _state_summary(state)
        elif args.command == "lifecycle":
            state = run_lifecycle(state_path, args.confirm)
            output = _state_summary(state)
        elif args.command == "observe-app":
            state = observe_app(
                state_path,
                args.app,
                args.confirm,
                delay_seconds=args.delay,
            )
            output = _state_summary(state)
        elif args.command == "race":
            state = run_focus_race(state_path, args.confirm)
            output = _state_summary(state)
        elif args.command == "record-app":
            state = record_app_outcome(
                state_path, args.app, args.outcome, args.backend, args.confirm
            )
            output = _state_summary(state)
        elif args.command == "finish":
            finalized = finalize_run(state_path, args.confirm)
            output = {
                "summary": _state_summary(finalized["state"]),
                "reports": finalized["reports"],
            }
        elif args.command == "restore":
            state = restore_run(state_path, args.confirm)
            output = _state_summary(state)
        elif args.command == "report":
            with _state_lock(state_path):
                state = load_state(state_path)
            report = build_report(state)
            print(
                json.dumps(report, sort_keys=True, indent=2)
                if args.format == "json"
                else _markdown(report)
            )
            return 0
        else:
            with _state_lock(state_path):
                state = load_state(state_path)
            output = _state_summary(state)
        print(json.dumps(output, sort_keys=True, indent=2))
        return 0
    except AcceptanceError as error:
        print("Error: {}".format(error), file=sys.stderr)
        return 1
    except OSError:
        print("Error: acceptance-filesystem-operation-failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
