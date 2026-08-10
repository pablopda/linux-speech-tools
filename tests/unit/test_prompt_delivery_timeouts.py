"""Hermetic timeout checks for prompt-delivery subprocess boundaries."""

import subprocess
import types

from src.stt import prompt_delivery


def test_clipboard_read_timeout_fails_closed(monkeypatch):
    clipboard = prompt_delivery.ClipboardIO.__new__(prompt_delivery.ClipboardIO)
    clipboard.tool = "xclip"
    clipboard.last_error = None

    def timeout(command, **kwargs):
        assert kwargs["timeout"] == prompt_delivery.COMMAND_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(prompt_delivery.subprocess, "run", timeout)
    assert clipboard.read() is None
    assert "timed out" in clipboard.last_error


def test_clipboard_write_timeout_fails_closed(monkeypatch):
    clipboard = prompt_delivery.ClipboardIO.__new__(prompt_delivery.ClipboardIO)
    clipboard.tool = "wl-copy"
    clipboard.last_error = None

    def timeout(command, **kwargs):
        assert kwargs["timeout"] == prompt_delivery.COMMAND_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(prompt_delivery.subprocess, "run", timeout)
    assert clipboard.write("safe prompt") is False
    assert "timed out" in clipboard.last_error


def test_input_command_timeout_fails_closed(monkeypatch):
    capability = types.SimpleNamespace(can_type=True, method="x11_xdotool")
    controller = prompt_delivery.InputController(capability)

    def timeout(command, **kwargs):
        assert command[0] == "xdotool"
        assert kwargs["timeout"] == prompt_delivery.COMMAND_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(prompt_delivery.subprocess, "run", timeout)
    assert controller.send_key_combo("enter") is False
