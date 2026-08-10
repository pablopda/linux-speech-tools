#!/usr/bin/env python3
"""Output backends for live developer prompt dictation."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional


TERMINAL_PASTE_TARGETS = {"claude", "codex", "terminal"}
TEXT_FIELD_PASTE_TARGETS = {"ide", "browser", "generic"}


@dataclass
class TypingCapability:
    can_type: bool
    method: str
    has_ydotool: bool
    has_xdotool: bool
    has_uinput_group: bool
    can_access_uinput: bool
    ydotoold_running: bool
    is_wayland: bool
    is_x11: bool


def check_typing_capability() -> TypingCapability:
    try:
        groups = subprocess.run(["groups"], capture_output=True, text=True, check=False).stdout
        has_uinput_group = "uinput" in groups.split()
    except OSError:
        has_uinput_group = False

    can_access_uinput = False
    if os.path.exists("/dev/uinput"):
        try:
            with open("/dev/uinput", "rb"):
                can_access_uinput = True
        except OSError:
            can_access_uinput = False

    has_ydotool = shutil.which("ydotool") is not None
    has_xdotool = shutil.which("xdotool") is not None
    ydotoold_running = False
    if has_ydotool:
        ydotoold_running = (
            subprocess.run(["pgrep", "ydotoold"], capture_output=True, check=False).returncode
            == 0
        )

    is_wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    is_x11 = os.environ.get("XDG_SESSION_TYPE") == "x11" or bool(os.environ.get("DISPLAY"))
    can_type_wayland = has_ydotool and is_wayland and (
        ydotoold_running or (has_uinput_group and can_access_uinput)
    )
    can_type_x11 = has_xdotool and is_x11
    method = "wayland_ydotool" if can_type_wayland else "x11_xdotool" if can_type_x11 else "none"
    return TypingCapability(
        can_type=can_type_wayland or can_type_x11,
        method=method,
        has_ydotool=has_ydotool,
        has_xdotool=has_xdotool,
        has_uinput_group=has_uinput_group,
        can_access_uinput=can_access_uinput,
        ydotoold_running=ydotoold_running,
        is_wayland=is_wayland,
        is_x11=is_x11,
    )


class ClipboardIO:
    def __init__(self) -> None:
        self.tool = self._detect_tool()
        self.last_error: Optional[str] = None

    def _detect_tool(self) -> str:
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
            return "wl-copy"
        if shutil.which("xclip"):
            return "xclip"
        if shutil.which("xsel"):
            return "xsel"
        return "missing"

    def read(self) -> Optional[str]:
        try:
            if self.tool == "wl-copy" and shutil.which("wl-paste"):
                result = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, check=False)
            elif self.tool == "xclip":
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-out"],
                    capture_output=True,
                    check=False,
                )
            elif self.tool == "xsel":
                result = subprocess.run(["xsel", "--clipboard", "--output"], capture_output=True, check=False)
            else:
                return None
        except OSError as exc:
            self.last_error = str(exc)
            return None
        if result.returncode != 0:
            return None
        return result.stdout.decode(errors="replace")

    def write(self, text: str) -> bool:
        try:
            if self.tool == "wl-copy":
                subprocess.run(["wl-copy"], input=text.encode(), check=True)
            elif self.tool == "xclip":
                subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
            elif self.tool == "xsel":
                subprocess.run(["xsel", "--clipboard", "--input"], input=text.encode(), check=True)
            else:
                self.last_error = "no clipboard tool available"
                return False
        except (OSError, subprocess.CalledProcessError) as exc:
            self.last_error = str(exc)
            return False
        return True


def paste_key_for_target(target_kind: str, requested: str = "auto") -> str:
    requested = (requested or "auto").strip().lower()
    if requested != "auto":
        return requested
    if target_kind in TERMINAL_PASTE_TARGETS:
        return "ctrl-shift-v"
    if target_kind in TEXT_FIELD_PASTE_TARGETS:
        return "ctrl-v"
    return "ctrl-v"


class InputController:
    def __init__(self, capability: Optional[TypingCapability] = None) -> None:
        self.capability = capability or check_typing_capability()

    def available(self) -> bool:
        return self.capability.can_type

    def send_paste_key(self, combo: str) -> bool:
        return self.send_key_combo(combo)

    def send_backspace(self, count: int) -> bool:
        if count <= 0:
            return True
        if not self.available():
            return False
        if self.capability.method == "x11_xdotool":
            return self._run(["xdotool", "key", "--repeat", str(count), "BackSpace"])
        if self.capability.method == "wayland_ydotool":
            events = []
            for _ in range(count):
                events.extend(["14:1", "14:0"])
            return self._run(["ydotool", "key", *events])
        return False

    def send_key_combo(self, combo: str) -> bool:
        if not self.available():
            return False
        combo = combo.lower()
        if self.capability.method == "x11_xdotool":
            return self._run(["xdotool", "key", self._xdotool_combo(combo)])
        if self.capability.method == "wayland_ydotool":
            events = self._ydotool_events(combo)
            return bool(events) and self._run(["ydotool", "key", *events])
        return False

    def _run(self, command: List[str]) -> bool:
        try:
            return subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0
        except OSError:
            return False

    def _xdotool_combo(self, combo: str) -> str:
        return {
            "ctrl-v": "ctrl+v",
            "ctrl-shift-v": "ctrl+shift+v",
            "shift-insert": "shift+Insert",
            "enter": "Return",
        }.get(combo, combo)

    def _ydotool_events(self, combo: str) -> List[str]:
        # Linux input event codes: ctrl=29, shift=42, v=47, insert=110, enter=28.
        mapping = {
            "ctrl-v": ["29:1", "47:1", "47:0", "29:0"],
            "ctrl-shift-v": ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"],
            "shift-insert": ["42:1", "110:1", "110:0", "42:0"],
            "enter": ["28:1", "28:0"],
        }
        return mapping.get(combo, [])


class PromptRenderer:
    mode = "base"

    def update(self, text: str) -> bool:
        return True

    def finalize(self, text: str) -> bool:
        return self.update(text)

    def can_submit(self) -> bool:
        return False

    def close(self) -> None:
        return None


class ClipboardRenderer(PromptRenderer):
    mode = "clipboard"

    def __init__(self, clipboard: Optional[ClipboardIO] = None) -> None:
        self.clipboard = clipboard or ClipboardIO()

    def update(self, text: str) -> bool:
        return True

    def finalize(self, text: str) -> bool:
        return self.clipboard.write(text.strip())


class PasteRenderer(ClipboardRenderer):
    mode = "paste"

    def __init__(
        self,
        paste_keys: str,
        clipboard: Optional[ClipboardIO] = None,
        input_controller: Optional[InputController] = None,
    ) -> None:
        super().__init__(clipboard)
        self.paste_keys = paste_keys
        self.input_controller = input_controller or InputController()

    def finalize(self, text: str) -> bool:
        if not self.clipboard.write(text.strip()):
            return False
        return self.input_controller.send_paste_key(self.paste_keys)

    def can_submit(self) -> bool:
        return True


class LiveTypeRenderer(PasteRenderer):
    mode = "live-type"

    def __init__(
        self,
        paste_keys: str,
        clipboard: Optional[ClipboardIO] = None,
        input_controller: Optional[InputController] = None,
        restore_clipboard: bool = True,
        focus_guard: Optional[Callable[[], bool]] = None,
    ) -> None:
        super().__init__(paste_keys, clipboard, input_controller)
        self.rendered_text = ""
        self.original_clipboard = self.clipboard.read() if restore_clipboard else None
        self.focus_guard = focus_guard
        self.fell_back = False
        self.last_error: Optional[str] = None

    def _focus_is_safe(self) -> bool:
        if self.focus_guard is None:
            return False
        try:
            return bool(self.focus_guard())
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def _fallback_to_clipboard(self, text: str) -> bool:
        self.fell_back = True
        self.mode = "clipboard-fallback"
        self.rendered_text = ""
        self.last_error = self.last_error or "target focus changed or could not be verified"
        return self.clipboard.write(text.strip())

    def update(self, text: str) -> bool:
        text = text.strip()
        if self.fell_back:
            return self._fallback_to_clipboard(text)
        if text == self.rendered_text:
            return True
        if self.rendered_text:
            if not self._focus_is_safe():
                return self._fallback_to_clipboard(text)
            if not self.input_controller.send_backspace(len(self.rendered_text)):
                return self._fallback_to_clipboard(text)
        # Once the old preview has been erased, keep our state aligned with the
        # target even if writing or pasting the replacement fails. Otherwise a
        # retry would erase unrelated text using the stale preview length.
        self.rendered_text = ""
        if text:
            if not self._focus_is_safe():
                return self._fallback_to_clipboard(text)
            if not self.clipboard.write(text):
                return False
            if not self._focus_is_safe():
                return self._fallback_to_clipboard(text)
            if not self.input_controller.send_paste_key(self.paste_keys):
                return self._fallback_to_clipboard(text)
        self.rendered_text = text
        return True

    def finalize(self, text: str) -> bool:
        # The live renderer has already inserted its latest text. Reconcile a
        # changed final transcription, but do not paste an unchanged prompt a
        # second time through PasteRenderer.finalize().
        if self.fell_back or not self._focus_is_safe():
            return self._fallback_to_clipboard(text)
        return self.update(text)

    def can_submit(self) -> bool:
        return not self.fell_back and self._focus_is_safe()

    def close(self) -> None:
        if not self.fell_back and self.original_clipboard is not None:
            self.clipboard.write(self.original_clipboard)


class OverlayRenderer(ClipboardRenderer):
    mode = "overlay"

    def __init__(self, clipboard: Optional[ClipboardIO] = None) -> None:
        super().__init__(clipboard)
        self.process: Optional[subprocess.Popen] = self._start_overlay()

    def update(self, text: str) -> bool:
        if not self.process or not self.process.stdin or self.process.poll() is not None:
            return True
        try:
            self.process.stdin.write(json.dumps({"text": text.strip()}) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            self.process = None
        return True

    def finalize(self, text: str) -> bool:
        self.update(text)
        return super().finalize(text)

    def close(self) -> None:
        try:
            if self.process and self.process.stdin and self.process.poll() is None:
                self.process.stdin.close()
        except OSError:
            pass

    def _start_overlay(self) -> Optional[subprocess.Popen]:
        try:
            return subprocess.Popen(
                [sys.executable, "-m", "src.stt.prompt_overlay"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError:
            return None


def renderer_for(
    output: str,
    target_kind: str,
    *,
    paste_keys: str = "auto",
    confidence: str = "low",
    focus_guard: Optional[Callable[[], bool]] = None,
) -> PromptRenderer:
    output = (output or "auto").strip().lower()
    keys = paste_key_for_target(target_kind, paste_keys)
    capability = check_typing_capability()
    clipboard = ClipboardIO()
    input_controller = InputController(capability)

    if output == "clipboard":
        return ClipboardRenderer(clipboard)
    if output == "paste":
        return PasteRenderer(keys, clipboard, input_controller)
    if output == "live-type":
        return LiveTypeRenderer(keys, clipboard, input_controller, focus_guard=focus_guard)
    if output == "overlay":
        return OverlayRenderer(clipboard)
    if output == "stdout":
        return StdoutRenderer()

    if confidence in {"high", "explicit"} and capability.can_type:
        return LiveTypeRenderer(keys, clipboard, input_controller, focus_guard=focus_guard)
    return OverlayRenderer(clipboard)


class StdoutRenderer(PromptRenderer):
    mode = "stdout"

    def __init__(self) -> None:
        self.last_text = ""

    def update(self, text: str) -> bool:
        self.last_text = text.strip()
        print(f"\r{self.last_text}", end="", file=sys.stdout, flush=True)
        return True

    def finalize(self, text: str) -> bool:
        self.last_text = text.strip()
        print(f"\r{self.last_text}", file=sys.stdout, flush=True)
        return True
