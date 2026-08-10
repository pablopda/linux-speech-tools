"""Hermetic success-path tests for clipboard + typing output backends (J8).

The existing suite covers the *fallback* clipboard path (no tool available ->
private file). These tests cover the happy paths that were otherwise only
exercised on a real desktop:

* ``ClipboardManager.copy_to_clipboard`` actually invokes the detected tool
  (``wl-copy`` on Wayland, ``xclip`` on X11) with the transcript on stdin;
* ``TextTyper.type_text`` constructs the correct ``ydotool`` / ``xdotool``
  command line.

Tool *detection* is driven by placing fake executables on ``PATH`` or by
stubbing ``shutil.which`` and the bounded ``pgrep`` probe. The actual tool
subprocesses are stubbed so nothing real runs.
"""

import importlib
import stat
import subprocess
import sys
import types
from unittest import mock

import pytest

# Importing the STT modules pulls in session.py, which imports numpy at module
# top. Load numpy NOW (skipping if absent) so it is present in the sys.modules
# snapshot used below -- patch.dict would otherwise evict numpy's C-extension on
# teardown, and numpy refuses to be imported twice per process.
numpy = pytest.importorskip("numpy")


def _fake_stt_modules():
    """faster_whisper + webrtcvad stand-ins so importing the modules succeeds."""
    return {
        "faster_whisper": types.SimpleNamespace(WhisperModel=object),
        "webrtcvad": types.SimpleNamespace(Vad=lambda *a, **k: object()),
    }


def _put_fake_tool(directory, name):
    """Create an executable stub named ``name`` in ``directory`` and return it."""
    path = directory / name
    path.write_text("#!/usr/bin/env bash\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _import_with_fakes(module_name):
    """Import an STT module with faster_whisper + webrtcvad faked, then restore.

    Only the STT submodules are evicted before import so the fakes take effect;
    numpy (already loaded above) stays in the snapshot and is preserved. On exit
    patch.dict restores the original module table, so the lightweight fakes do
    not leak into other test files.
    """
    with mock.patch.dict(sys.modules, _fake_stt_modules()):
        for name in (
            "src.stt.faster_whisper_typing",
            "src.stt.faster_whisper_clipboard",
            "src.stt.session",
        ):
            sys.modules.pop(name, None)
        yield importlib.import_module(module_name)


@pytest.fixture
def clipboard_module():
    yield from _import_with_fakes("src.stt.faster_whisper_clipboard")


@pytest.fixture
def typing_module():
    yield from _import_with_fakes("src.stt.faster_whisper_typing")


def test_clipboard_xclip_success_path(clipboard_module, tmp_path, monkeypatch):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    _put_fake_tool(fakebin, "xclip")
    # X11 environment: no WAYLAND_DISPLAY, xclip resolvable on PATH.
    monkeypatch.setenv("PATH", str(fakebin))
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    manager = clipboard_module.ClipboardManager()
    assert manager.clipboard_tool == "xclip"

    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    with mock.patch.object(clipboard_module.subprocess, "run", fake_run):
        assert manager.copy_to_clipboard("  hello world  ") is True

    assert calls, "no subprocess invoked for the clipboard tool"
    cmd, kwargs = calls[0]
    assert cmd == ["xclip", "-selection", "clipboard"]
    # Transcript is trimmed and passed on stdin (bytes), with check=True.
    assert kwargs.get("input") == b"hello world"
    assert kwargs.get("check") is True
    assert kwargs.get("timeout") == clipboard_module.CLIPBOARD_COMMAND_TIMEOUT_SECONDS


def test_clipboard_wl_copy_success_path(clipboard_module, tmp_path, monkeypatch):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    _put_fake_tool(fakebin, "wl-copy")
    # Wayland environment with wl-copy available -> native Wayland clipboard.
    monkeypatch.setenv("PATH", str(fakebin))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    manager = clipboard_module.ClipboardManager()
    assert manager.clipboard_tool == "wl-copy"

    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    with mock.patch.object(clipboard_module.subprocess, "run", fake_run):
        assert manager.copy_to_clipboard("dictated text") is True

    cmd, kwargs = calls[0]
    assert cmd == ["wl-copy"]
    assert kwargs.get("input") == b"dictated text"
    assert kwargs.get("check") is True
    assert kwargs.get("timeout") == clipboard_module.CLIPBOARD_COMMAND_TIMEOUT_SECONDS


def test_classic_clipboard_timeout_is_bounded_and_returns_failure(
    clipboard_module, monkeypatch
):
    manager = clipboard_module.ClipboardManager.__new__(
        clipboard_module.ClipboardManager
    )
    manager.clipboard_tool = "xclip"
    manager.transcript_fallback = False
    manager.last_error = None

    def timeout(command, **kwargs):
        assert kwargs["timeout"] == clipboard_module.CLIPBOARD_COMMAND_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(clipboard_module.subprocess, "run", timeout)

    assert manager.copy_to_clipboard("sensitive transcript") is False
    assert "timed out" in manager.last_error
    assert "sensitive transcript" not in manager.last_error


@pytest.mark.parametrize("failure_kind", ["timeout", "oserror"])
def test_notification_failure_is_bounded_best_effort_and_redacted(
    failure_kind, monkeypatch, capsys
):
    from src.stt import runtime

    secret_title = "private notification title"
    secret_message = "sensitive transcript"
    monkeypatch.setattr(
        runtime.shutil,
        "which",
        lambda name: "/fake/notify-send" if name == "notify-send" else None,
    )

    def fail(command, **kwargs):
        assert command[0] == "/fake/notify-send"
        assert kwargs["timeout"] == runtime.NOTIFY_TIMEOUT_SECONDS
        if failure_kind == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        raise OSError("private transport detail")

    monkeypatch.setattr(runtime.subprocess, "run", fail)

    runtime.notify(secret_title, secret_message)

    stderr = capsys.readouterr().err
    assert "could not be delivered" in stderr
    assert secret_title not in stderr
    assert secret_message not in stderr
    assert "private transport detail" not in stderr


def test_clipboard_success_survives_notification_timeout(
    clipboard_module, monkeypatch, capsys
):
    from src.stt import runtime

    manager = clipboard_module.ClipboardManager.__new__(
        clipboard_module.ClipboardManager
    )
    manager.clipboard_tool = "xclip"
    manager.transcript_fallback = False
    manager.last_error = None

    monkeypatch.setattr(
        runtime.shutil,
        "which",
        lambda name: "/fake/notify-send" if name == "notify-send" else None,
    )

    def run(command, **kwargs):
        if command[0] == "xclip":
            assert kwargs["timeout"] == clipboard_module.CLIPBOARD_COMMAND_TIMEOUT_SECONDS
            return subprocess.CompletedProcess(command, 0)
        assert command[0] == "/fake/notify-send"
        assert kwargs["timeout"] == runtime.NOTIFY_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(clipboard_module.subprocess, "run", run)

    dictation = clipboard_module.FasterWhisperClipboard.__new__(
        clipboard_module.FasterWhisperClipboard
    )
    dictation.preview = False
    dictation.clipboard = manager

    assert dictation.emit_text("sensitive transcript") is True
    assert "sensitive transcript" not in capsys.readouterr().err


def test_typing_xdotool_command_construction(typing_module, monkeypatch):
    # Force the X11 branch: no Wayland markers; xdotool is discoverable.
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setattr(
        typing_module.shutil,
        "which",
        lambda name: "/fake/xdotool" if name == "xdotool" else None,
    )
    run = mock.Mock()
    monkeypatch.setattr(typing_module.subprocess, "run", run)

    typer = typing_module.TextTyper()
    assert typer.tool == "xdotool"
    run.assert_not_called()

    sent = []
    with mock.patch.object(
        typing_module.subprocess,
        "run",
        lambda cmd, *a, **k: sent.append((cmd, k)) or subprocess.CompletedProcess(cmd, 0),
    ):
        assert typer.type_text("  some words  ") is True

    cmd, kwargs = sent[0]
    # Trailing space is appended; "--" guards against text starting with a dash.
    assert cmd == ["xdotool", "type", "--", "some words "]
    assert kwargs.get("check") is True
    assert kwargs.get("timeout") == typing_module.TEXT_DISPATCH_TIMEOUT_SECONDS


def test_typing_ydotool_command_construction(typing_module, monkeypatch):
    # Wayland branch: ydotool present, ydotoold running (pgrep rc 0).
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(
        typing_module.shutil,
        "which",
        lambda name: "/fake/ydotool" if name == "ydotool" else None,
    )

    def detect_run(cmd, *args, **kwargs):
        if cmd[:1] == ["pgrep"]:
            assert kwargs["timeout"] == typing_module.TOOL_PROBE_TIMEOUT_SECONDS
            return subprocess.CompletedProcess(cmd, 0)  # ydotoold running
        return subprocess.CompletedProcess(cmd, 0)

    with mock.patch.object(typing_module.subprocess, "run", detect_run):
        typer = typing_module.TextTyper()
    assert typer.tool == "ydotool"

    sent = []
    with mock.patch.object(
        typing_module.subprocess,
        "run",
        lambda cmd, *a, **k: sent.append((cmd, k)) or subprocess.CompletedProcess(cmd, 0),
    ):
        assert typer.type_text("hola mundo") is True

    cmd, kwargs = sent[0]
    assert cmd == ["ydotool", "type", "hola mundo "]
    assert kwargs.get("check") is True
    assert kwargs.get("timeout") == typing_module.TEXT_DISPATCH_TIMEOUT_SECONDS


def test_ydotool_daemon_probe_timeout_is_bounded_and_nonfatal(
    typing_module, monkeypatch
):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(
        typing_module.shutil,
        "which",
        lambda name: "/fake/ydotool" if name == "ydotool" else None,
    )

    def timeout(command, **kwargs):
        assert command == ["pgrep", "ydotoold"]
        assert kwargs["timeout"] == typing_module.TOOL_PROBE_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(typing_module.subprocess, "run", timeout)

    assert typing_module.TextTyper().tool == "ydotool"


def test_classic_typing_reports_ambiguous_failure_without_clipboard_retry(
    typing_module, monkeypatch
):
    from src.stt.insertion_session import InsertionState, result

    typer = typing_module.TextTyper.__new__(typing_module.TextTyper)
    typer.tool = "xdotool"
    typer.backend = "xdotool"
    monkeypatch.setattr(
        typing_module.subprocess,
        "run",
        mock.Mock(side_effect=subprocess.CalledProcessError(1, ["xdotool"])),
    )
    dispatch = typer.type_result("sensitive transcript", revision=4)
    assert dispatch.state == InsertionState.AMBIGUOUS_AFTER_DISPATCH

    dictation = typing_module.FasterWhisperTyping.__new__(
        typing_module.FasterWhisperTyping
    )
    dictation.preview = False
    dictation.insertion = mock.Mock()
    dictation.insertion.insert.return_value = result(
        InsertionState.AMBIGUOUS_AFTER_DISPATCH, "xdotool", revision=4
    )
    dictation.clipboard = mock.Mock()

    assert dictation.emit_text("sensitive transcript") is False
    dictation.clipboard.copy_to_clipboard.assert_not_called()


def test_classic_typing_timeout_is_ambiguous_and_never_retried_to_clipboard(
    typing_module, monkeypatch
):
    from src.stt.insertion_session import FinalOnlyInsertionAdapter, InsertionState

    typer = typing_module.TextTyper.__new__(typing_module.TextTyper)
    typer.tool = "ydotool"
    typer.backend = "ydotool"

    def timeout(command, **kwargs):
        assert kwargs["timeout"] == typing_module.TEXT_DISPATCH_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(typing_module.subprocess, "run", timeout)

    dictation = typing_module.FasterWhisperTyping.__new__(
        typing_module.FasterWhisperTyping
    )
    dictation.preview = False
    dictation.insertion = FinalOnlyInsertionAdapter(typer)
    dictation.clipboard = mock.Mock()

    assert dictation.emit_text("sensitive transcript") is False
    assert (
        dictation.insertion.last_result.state
        == InsertionState.AMBIGUOUS_AFTER_DISPATCH
    )
    dictation.clipboard.copy_to_clipboard.assert_not_called()


def test_classic_typing_pre_dispatch_failure_keeps_clipboard_fallback(
    typing_module, monkeypatch
):
    from src.stt.insertion_session import InsertionState

    typer = typing_module.TextTyper.__new__(typing_module.TextTyper)
    typer.tool = "ydotool"
    typer.backend = "ydotool"
    monkeypatch.setattr(
        typing_module.subprocess,
        "run",
        mock.Mock(side_effect=OSError("executable missing")),
    )
    dispatch = typer.type_result("sensitive transcript", revision=5)
    assert dispatch.state == InsertionState.FAILED_BEFORE_DISPATCH


def test_classic_successful_clipboard_fallback_updates_structured_status(
    typing_module,
):
    from src.stt.insertion_session import InsertionState, result

    failed = result(
        InsertionState.FAILED_BEFORE_DISPATCH, "ydotool", revision=5
    )
    dictation = typing_module.FasterWhisperTyping.__new__(
        typing_module.FasterWhisperTyping
    )
    dictation.preview = False
    dictation.insertion = mock.Mock(last_result=failed)
    dictation.insertion.insert.return_value = failed
    dictation.clipboard = mock.Mock(clipboard_tool="wl-copy")
    dictation.clipboard.copy_to_clipboard.return_value = True

    assert dictation.emit_text("sensitive transcript") is True

    assert dictation.insertion.last_result.state == InsertionState.CLIPBOARD_FALLBACK
    assert dictation.insertion.last_result.backend == "wl-copy"
    assert dictation.insertion.last_result.revision == 5
    assert "sensitive transcript" not in repr(dictation.insertion.last_result)
    dictation.clipboard.notify_copy.assert_called_once_with("sensitive transcript")


def test_typing_fallback_success_survives_notification_timeout(
    typing_module, monkeypatch, capsys
):
    from src.stt import runtime
    from src.stt.insertion_session import InsertionState, result

    failed = result(
        InsertionState.FAILED_BEFORE_DISPATCH, "ydotool", revision=6
    )
    manager = typing_module.ClipboardManager.__new__(typing_module.ClipboardManager)
    manager.clipboard_tool = "xclip"
    manager.transcript_fallback = False
    manager.last_error = None

    monkeypatch.setattr(
        runtime.shutil,
        "which",
        lambda name: "/fake/notify-send" if name == "notify-send" else None,
    )

    def run(command, **kwargs):
        if command[0] == "xclip":
            return subprocess.CompletedProcess(command, 0)
        assert command[0] == "/fake/notify-send"
        assert kwargs["timeout"] == runtime.NOTIFY_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(typing_module.subprocess, "run", run)

    dictation = typing_module.FasterWhisperTyping.__new__(
        typing_module.FasterWhisperTyping
    )
    dictation.preview = False
    dictation.insertion = mock.Mock(last_result=failed)
    dictation.insertion.insert.return_value = failed
    dictation.clipboard = manager

    assert dictation.emit_text("sensitive transcript") is True
    assert dictation.insertion.last_result.state == InsertionState.CLIPBOARD_FALLBACK
    assert dictation.insertion.last_result.revision == 6
    assert "sensitive transcript" not in capsys.readouterr().err
