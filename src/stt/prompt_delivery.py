#!/usr/bin/env python3
"""Output backends for live developer prompt dictation."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from typing import Callable, List, Optional

try:
    from .insertion_session import (
        InsertionResult,
        InsertionSession,
        InsertionState,
        result,
    )
except ImportError:
    from insertion_session import (
        InsertionResult,
        InsertionSession,
        InsertionState,
        result,
    )


TERMINAL_PASTE_TARGETS = {"claude", "codex", "terminal"}
TEXT_FIELD_PASTE_TARGETS = {"ide", "browser", "generic"}
COMMAND_TIMEOUT_SECONDS = 3.0


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
        groups = subprocess.run(
            ["groups"],
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        ).stdout
        has_uinput_group = "uinput" in groups.split()
    except (OSError, subprocess.TimeoutExpired):
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
        try:
            ydotoold_running = subprocess.run(
                ["pgrep", "ydotoold"],
                capture_output=True,
                check=False,
                timeout=COMMAND_TIMEOUT_SECONDS,
            ).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ydotoold_running = False

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
                result = subprocess.run(
                    ["wl-paste", "--no-newline"],
                    capture_output=True,
                    check=False,
                    timeout=COMMAND_TIMEOUT_SECONDS,
                )
            elif self.tool == "xclip":
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-out"],
                    capture_output=True,
                    check=False,
                    timeout=COMMAND_TIMEOUT_SECONDS,
                )
            elif self.tool == "xsel":
                result = subprocess.run(
                    ["xsel", "--clipboard", "--output"],
                    capture_output=True,
                    check=False,
                    timeout=COMMAND_TIMEOUT_SECONDS,
                )
            else:
                return None
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.last_error = str(exc)
            return None
        if result.returncode != 0:
            return None
        return result.stdout.decode(errors="replace")

    def write(self, text: str) -> bool:
        try:
            if self.tool == "wl-copy":
                subprocess.run(
                    ["wl-copy"],
                    input=text.encode(),
                    check=True,
                    timeout=COMMAND_TIMEOUT_SECONDS,
                )
            elif self.tool == "xclip":
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode(),
                    check=True,
                    timeout=COMMAND_TIMEOUT_SECONDS,
                )
            elif self.tool == "xsel":
                subprocess.run(
                    ["xsel", "--clipboard", "--input"],
                    input=text.encode(),
                    check=True,
                    timeout=COMMAND_TIMEOUT_SECONDS,
                )
            else:
                self.last_error = "no clipboard tool available"
                return False
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
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
        return bool(self.send_paste_key_result(combo))

    def send_paste_key_result(
        self, combo: str, revision: Optional[int] = None
    ) -> InsertionResult:
        return self.send_key_combo_result(combo, revision)

    def send_backspace(self, count: int) -> bool:
        return bool(self.send_backspace_result(count))

    def send_backspace_result(
        self, count: int, revision: Optional[int] = None
    ) -> InsertionResult:
        if count <= 0:
            return result(
                InsertionState.DISPATCHED_UNCONFIRMED,
                self._backend(),
                revision=revision,
            )
        if not self.available():
            return result(
                InsertionState.UNAVAILABLE,
                self._backend(),
                revision=revision,
                diagnostic="direct input is unavailable",
            )
        if self.capability.method == "x11_xdotool":
            return self._run_result(
                ["xdotool", "key", "--repeat", str(count), "BackSpace"],
                revision,
            )
        if self.capability.method == "wayland_ydotool":
            events = []
            for _ in range(count):
                events.extend(["14:1", "14:0"])
            return self._run_result(["ydotool", "key", *events], revision)
        return result(
            InsertionState.UNAVAILABLE,
            self._backend(),
            revision=revision,
            diagnostic="direct input backend is unavailable",
        )

    def send_key_combo(self, combo: str) -> bool:
        return bool(self.send_key_combo_result(combo))

    def send_key_combo_result(
        self, combo: str, revision: Optional[int] = None
    ) -> InsertionResult:
        if not self.available():
            return result(
                InsertionState.UNAVAILABLE,
                self._backend(),
                revision=revision,
                diagnostic="direct input is unavailable",
            )
        combo = combo.lower()
        if self.capability.method == "x11_xdotool":
            return self._run_result(
                ["xdotool", "key", self._xdotool_combo(combo)], revision
            )
        if self.capability.method == "wayland_ydotool":
            events = self._ydotool_events(combo)
            if not events:
                return result(
                    InsertionState.REJECTED,
                    self._backend(),
                    revision=revision,
                    diagnostic="unsupported key combination",
                )
            return self._run_result(["ydotool", "key", *events], revision)
        return result(
            InsertionState.UNAVAILABLE,
            self._backend(),
            revision=revision,
            diagnostic="direct input backend is unavailable",
        )

    def _run(self, command: List[str]) -> bool:
        return bool(self._run_result(command))

    def _backend(self) -> str:
        return getattr(self.capability, "method", "direct-input") or "direct-input"

    def _run_result(
        self, command: List[str], revision: Optional[int] = None
    ) -> InsertionResult:
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return result(
                InsertionState.AMBIGUOUS_AFTER_DISPATCH,
                self._backend(),
                revision=revision,
                diagnostic="direct input command timed out after dispatch",
            )
        except OSError:
            return result(
                InsertionState.FAILED_BEFORE_DISPATCH,
                self._backend(),
                revision=revision,
                diagnostic="direct input command could not be started",
            )
        if completed.returncode == 0:
            return result(
                InsertionState.DISPATCHED_UNCONFIRMED,
                self._backend(),
                revision=revision,
            )
        return result(
            InsertionState.AMBIGUOUS_AFTER_DISPATCH,
            self._backend(),
            revision=revision,
            diagnostic="direct input command reported failure after dispatch",
        )

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
    backend = "base"
    supports_submit = False
    requires_target_match = False
    destructive_updates = False

    def update(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        return result(InsertionState.NON_INSERTING, self.backend, revision=revision)

    def finalize(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        return self.update(text, revision)

    def can_submit(self) -> bool:
        return self.supports_submit

    def submit(self, revision: Optional[int] = None) -> InsertionResult:
        return result(
            InsertionState.REJECTED,
            self.backend,
            revision=revision,
            diagnostic="output backend does not support submit",
        )

    def cancel(self, revision: Optional[int] = None) -> InsertionResult:
        return result(InsertionState.CANCELLED, self.backend, revision=revision)

    def close(self) -> None:
        return None


class ClipboardRenderer(PromptRenderer):
    mode = "clipboard"
    backend = "clipboard"

    def __init__(self, clipboard: Optional[ClipboardIO] = None) -> None:
        self.clipboard = clipboard or ClipboardIO()

    def _write_clipboard(self, text: str) -> bool:
        return self.clipboard.write(text)

    def _copy_result(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        if self._write_clipboard(text.strip()):
            return result(
                InsertionState.CLIPBOARD_FALLBACK,
                self.backend,
                revision=revision,
            )
        if self.clipboard.tool == "missing":
            return result(
                InsertionState.UNAVAILABLE,
                self.backend,
                revision=revision,
                diagnostic="clipboard backend is unavailable",
            )
        return result(
            InsertionState.FAILED_BEFORE_DISPATCH,
            self.backend,
            revision=revision,
            diagnostic="clipboard write failed",
        )

    def finalize(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        return self._copy_result(text, revision)

    def fallback(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        return self._copy_result(text, revision)


class PasteRenderer(ClipboardRenderer):
    mode = "paste"
    backend = "synthetic-paste"
    supports_submit = True
    safe_fallback = True
    requires_target_match = True

    def __init__(
        self,
        paste_keys: str,
        clipboard: Optional[ClipboardIO] = None,
        input_controller: Optional[InputController] = None,
        focus_guard: Optional[Callable[[], bool]] = None,
    ) -> None:
        super().__init__(clipboard)
        self.paste_keys = paste_keys
        self.input_controller = input_controller or InputController()
        self.focus_guard = focus_guard

    def _focus_is_safe(self) -> bool:
        if self.focus_guard is None:
            return False
        try:
            return bool(self.focus_guard())
        except Exception:
            return False

    def _focus_fallback(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        copied = self._copy_result(text, revision)
        return replace(copied, target_token_match=False)

    def finalize(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        # Session-level authorization occurs before entering the transport, but
        # clipboard I/O can block. Recheck both before mutating the clipboard and
        # immediately before dispatching Paste so focus drift cannot target the
        # newly focused application.
        if not self._focus_is_safe():
            return self._focus_fallback(text, revision)
        copied = self._copy_result(text, revision)
        if not copied:
            return copied
        if not self._focus_is_safe():
            return replace(copied, target_token_match=False)
        pasted = self.input_controller.send_paste_key_result(
            self.paste_keys, revision
        )
        if pasted.target_token_match is None:
            pasted = replace(pasted, target_token_match=True)
        return pasted

    def can_submit(self) -> bool:
        return True

    def submit(self, revision: Optional[int] = None) -> InsertionResult:
        if not self._focus_is_safe():
            return result(
                InsertionState.REJECTED,
                self.backend,
                revision=revision,
                diagnostic="target focus changed before submit",
                target_token_match=False,
            )
        submitted = self.input_controller.send_key_combo_result("enter", revision)
        if submitted.target_token_match is None:
            submitted = replace(submitted, target_token_match=True)
        return submitted


class LiveTypeRenderer(PasteRenderer):
    mode = "live-type"
    backend = "synthetic-live-type"
    destructive_updates = True

    def __init__(
        self,
        paste_keys: str,
        clipboard: Optional[ClipboardIO] = None,
        input_controller: Optional[InputController] = None,
        restore_clipboard: bool = True,
        focus_guard: Optional[Callable[[], bool]] = None,
    ) -> None:
        super().__init__(
            paste_keys,
            clipboard,
            input_controller,
            focus_guard=focus_guard,
        )
        self.rendered_text = ""
        self.restore_clipboard = restore_clipboard
        self.original_clipboard: Optional[str] = None
        self._clipboard_snapshot_taken = False
        self._owned_clipboard_text: Optional[str] = None
        self.fell_back = False
        self.last_error: Optional[str] = None
        self.last_result: Optional[InsertionResult] = None

    def _focus_is_safe(self) -> bool:
        if self.focus_guard is None:
            return False
        try:
            return bool(self.focus_guard())
        except Exception:
            self.last_error = "target focus check failed"
            return False

    def _write_clipboard(self, text: str) -> bool:
        if self.restore_clipboard and not self._clipboard_snapshot_taken:
            # Capture immediately before the first mutation, rather than at
            # renderer construction, so an intervening user copy is preserved.
            self.original_clipboard = self.clipboard.read()
            self._clipboard_snapshot_taken = True
        if not self.clipboard.write(text):
            return False
        self._owned_clipboard_text = text
        return True

    def _fallback_to_clipboard(
        self,
        text: str,
        revision: Optional[int] = None,
        target_token_match: Optional[bool] = None,
    ) -> InsertionResult:
        self.fell_back = True
        self.mode = "clipboard-fallback"
        self.rendered_text = ""
        self.last_error = self.last_error or "target focus changed or could not be verified"
        self.last_result = self._copy_result(text, revision)
        if target_token_match is not None:
            self.last_result = replace(
                self.last_result, target_token_match=target_token_match
            )
        return self.last_result

    def fallback(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        return self._fallback_to_clipboard(text, revision)

    def _ambiguous(
        self, text: str, revision: Optional[int], diagnostic: str
    ) -> InsertionResult:
        # Keeping the latest prompt on the clipboard is non-inserting and safe,
        # but it must not erase the fact that target delivery became ambiguous.
        self.fell_back = True
        self.mode = "clipboard-fallback"
        self.rendered_text = ""
        self.last_error = diagnostic
        self._write_clipboard(text.strip())
        self.last_result = result(
            InsertionState.AMBIGUOUS_AFTER_DISPATCH,
            self.backend,
            revision=revision,
            diagnostic=diagnostic,
        )
        return self.last_result

    def update(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        text = text.strip()
        if self.fell_back:
            return self._fallback_to_clipboard(text, revision)
        if text == self.rendered_text:
            if self.last_result is not None:
                self.last_result = result(
                    self.last_result.state,
                    self.last_result.backend,
                    revision=revision,
                    diagnostic=self.last_result.diagnostic,
                )
                return self.last_result
            self.last_result = result(
                InsertionState.NON_INSERTING, self.backend, revision=revision
            )
            return self.last_result
        destructive_dispatched = False
        if self.rendered_text:
            if not self._focus_is_safe():
                return self._fallback_to_clipboard(
                    text, revision, target_token_match=False
                )
            erased = self.input_controller.send_backspace_result(
                len(self.rendered_text), revision
            )
            if not erased:
                if erased.state == InsertionState.AMBIGUOUS_AFTER_DISPATCH:
                    return self._ambiguous(
                        text, revision, "preview removal became ambiguous"
                    )
                return self._fallback_to_clipboard(text, revision)
            destructive_dispatched = True
        # Once the old preview has been erased, keep our state aligned with the
        # target even if writing or pasting the replacement fails. Otherwise a
        # retry would erase unrelated text using the stale preview length.
        self.rendered_text = ""
        if text:
            if not self._focus_is_safe():
                if destructive_dispatched:
                    return self._ambiguous(
                        text, revision, "focus changed after preview removal"
                    )
                return self._fallback_to_clipboard(
                    text, revision, target_token_match=False
                )
            if not self._write_clipboard(text):
                if destructive_dispatched:
                    return self._ambiguous(
                        text, revision, "clipboard write failed after preview removal"
                    )
                self.last_result = result(
                    InsertionState.FAILED_BEFORE_DISPATCH,
                    self.backend,
                    revision=revision,
                    diagnostic="clipboard write failed before insertion",
                )
                return self.last_result
            if not self._focus_is_safe():
                if destructive_dispatched:
                    return self._ambiguous(
                        text, revision, "focus changed after preview removal"
                    )
                return self._fallback_to_clipboard(
                    text, revision, target_token_match=False
                )
            pasted = self.input_controller.send_paste_key_result(
                self.paste_keys, revision
            )
            if not pasted:
                if (
                    pasted.state == InsertionState.AMBIGUOUS_AFTER_DISPATCH
                    or destructive_dispatched
                ):
                    return self._ambiguous(
                        text, revision, "prompt replacement became ambiguous"
                    )
                return self._fallback_to_clipboard(text, revision)
            self.last_result = pasted
        elif destructive_dispatched:
            self.last_result = result(
                InsertionState.DISPATCHED_UNCONFIRMED,
                self.backend,
                revision=revision,
            )
        else:
            self.last_result = result(
                InsertionState.NON_INSERTING, self.backend, revision=revision
            )
        self.rendered_text = text
        return self.last_result

    def finalize(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        # The live renderer has already inserted its latest text. Reconcile a
        # changed final transcription, but do not paste an unchanged prompt a
        # second time through PasteRenderer.finalize().
        if self.fell_back:
            return self._fallback_to_clipboard(text, revision)
        if not self._focus_is_safe():
            return self._fallback_to_clipboard(
                text, revision, target_token_match=False
            )
        return self.update(text, revision)

    def can_submit(self) -> bool:
        return not self.fell_back and self._focus_is_safe()

    def submit(self, revision: Optional[int] = None) -> InsertionResult:
        if not self.can_submit():
            return result(
                InsertionState.REJECTED,
                self.backend,
                revision=revision,
                diagnostic="target focus changed before submit",
                target_token_match=False,
            )
        return super().submit(revision)

    def close(self) -> None:
        if (
            not self.fell_back
            and self.restore_clipboard
            and self.original_clipboard is not None
            and self._owned_clipboard_text is not None
            and self.clipboard.read() == self._owned_clipboard_text
        ):
            self.clipboard.write(self.original_clipboard)


class OverlayRenderer(ClipboardRenderer):
    mode = "overlay"
    backend = "overlay"

    def __init__(self, clipboard: Optional[ClipboardIO] = None) -> None:
        super().__init__(clipboard)
        self.process: Optional[subprocess.Popen] = self._start_overlay()

    def update(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        if not self.process or not self.process.stdin or self.process.poll() is not None:
            return result(
                InsertionState.NON_INSERTING, self.backend, revision=revision
            )
        try:
            self.process.stdin.write(json.dumps({"text": text.strip()}) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            self.process = None
        return result(InsertionState.NON_INSERTING, self.backend, revision=revision)

    def finalize(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        self.update(text, revision)
        return super().finalize(text, revision)

    def close(self) -> None:
        try:
            if self.process and self.process.stdin and self.process.poll() is None:
                self.process.stdin.close()
        except OSError:
            pass

    def _start_overlay(self) -> Optional[subprocess.Popen]:
        process = None
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "src.stt.prompt_overlay"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            if process.stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
            return process
        except OSError:
            if process is not None:
                process.terminate()
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
        return PasteRenderer(
            keys,
            clipboard,
            input_controller,
            focus_guard=focus_guard,
        )
    if output == "live-type":
        return LiveTypeRenderer(keys, clipboard, input_controller, focus_guard=focus_guard)
    if output == "overlay":
        return OverlayRenderer(clipboard)
    if output == "stdout":
        return StdoutRenderer()

    if confidence in {"high", "explicit"} and capability.can_type:
        return LiveTypeRenderer(keys, clipboard, input_controller, focus_guard=focus_guard)
    return OverlayRenderer(clipboard)


def insertion_session_for(
    output: str,
    target_kind: str,
    *,
    target_token: str = "",
    paste_keys: str = "auto",
    confidence: str = "low",
    focus_guard: Optional[Callable[[], bool]] = None,
) -> InsertionSession:
    transport = renderer_for(
        output,
        target_kind,
        paste_keys=paste_keys,
        confidence=confidence,
        focus_guard=focus_guard,
    )
    return InsertionSession(
        transport,
        target_token=target_token,
        target_guard=focus_guard,
        allow_unconfirmed_submit=True,
    )


class StdoutRenderer(PromptRenderer):
    mode = "stdout"
    backend = "stdout"

    def __init__(self) -> None:
        self.last_text = ""

    def update(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        self.last_text = text.strip()
        print(f"\r{self.last_text}", end="", file=sys.stdout, flush=True)
        return result(InsertionState.NON_INSERTING, self.backend, revision=revision)

    def finalize(
        self, text: str, revision: Optional[int] = None
    ) -> InsertionResult:
        self.last_text = text.strip()
        print(f"\r{self.last_text}", file=sys.stdout, flush=True)
        return result(InsertionState.NON_INSERTING, self.backend, revision=revision)
