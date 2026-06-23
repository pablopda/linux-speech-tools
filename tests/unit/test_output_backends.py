"""Hermetic success-path tests for clipboard + typing output backends (J8).

The existing suite covers the *fallback* clipboard path (no tool available ->
private file). These tests cover the happy paths that were otherwise only
exercised on a real desktop:

* ``ClipboardManager.copy_to_clipboard`` actually invokes the detected tool
  (``wl-copy`` on Wayland, ``xclip`` on X11) with the transcript on stdin;
* ``TextTyper.type_text`` constructs the correct ``ydotool`` / ``xdotool``
  command line.

Tool *detection* is driven by placing fake executables on ``PATH`` (clipboard,
which uses ``shutil.which``) or by faking the ``which``/``pgrep`` probes
(typing). The actual tool subprocesses are stubbed so nothing real runs.
"""

import importlib
import os
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


def test_typing_xdotool_command_construction(typing_module, monkeypatch):
    # Force the X11 branch: no Wayland markers; "which xdotool" succeeds.
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)

    def detect_run(cmd, *args, **kwargs):
        if cmd[:1] == ["which"]:
            rc = 0 if cmd[1] == "xdotool" else 1
            return subprocess.CompletedProcess(cmd, rc)
        return subprocess.CompletedProcess(cmd, 0)

    with mock.patch.object(typing_module.subprocess, "run", detect_run):
        typer = typing_module.TextTyper()
    assert typer.tool == "xdotool"

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


def test_typing_ydotool_command_construction(typing_module, monkeypatch):
    # Wayland branch: ydotool present, ydotoold running (pgrep rc 0).
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    def detect_run(cmd, *args, **kwargs):
        if cmd[:1] == ["which"]:
            rc = 0 if cmd[1] == "ydotool" else 1
            return subprocess.CompletedProcess(cmd, rc)
        if cmd[:1] == ["pgrep"]:
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
