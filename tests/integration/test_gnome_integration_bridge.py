"""Pytest bridge for the GNOME integration shell checks (J5).

``tests/test-gnome-integration.sh`` exercises the live GNOME surface: it probes
``gsettings``, ``notify-send``, ``gnome-shell`` and the dictation launchers on a
real desktop session. That cannot run hermetically in CI, so it was effectively
orphaned -- nothing invoked it from ``pytest``.

This module gives those checks a home in the test run *and* a real application
point for the ``gnome`` / ``manual`` markers declared in pyproject. The test is
opt-in: it is skipped unless ``RUN_GNOME_INTEGRATION=1`` is set, so a normal
``pytest tests/`` (and CI) stays hermetic. When you do want it,
``pytest -m gnome`` selects it and ``pytest -m 'not gnome'`` excludes it.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GNOME_SCRIPT = ROOT / "tests" / "test-gnome-integration.sh"
GNOME_CONTROL = ROOT / "src" / "gnome" / "gnome-reader-control.py"

pytestmark = [pytest.mark.gnome, pytest.mark.manual]


@pytest.mark.skipif(
    os.environ.get("RUN_GNOME_INTEGRATION") != "1",
    reason="live GNOME desktop check; set RUN_GNOME_INTEGRATION=1 to run",
)
def test_gnome_integration_script_passes():
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
    assert GNOME_SCRIPT.exists(), f"missing {GNOME_SCRIPT}"
    result = subprocess.run(
        ["bash", str(GNOME_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "GNOME integration checks failed:\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


def test_gnome_integration_script_is_present_and_executable():
    """Always-on, hermetic guard so the .sh cannot silently rot/disappear.

    This carries the ``gnome``/``manual`` marks (module-level ``pytestmark``) so
    it is still excluded by ``-m 'not gnome'`` together with the live check.
    """
    assert GNOME_SCRIPT.exists(), f"missing {GNOME_SCRIPT}"
    assert os.access(GNOME_SCRIPT, os.X_OK), f"{GNOME_SCRIPT} is not executable"


def test_gnome_reader_progress_refresh_wiring_present():
    """Source guard for the PR's notification progress-refresh wiring.

    The notification id capture (``--print-id`` -> ``_store_notification_id`` ->
    ``self.current_notification_id``) and the replacing refresh
    (``--replace-id``/``_replace_notification_text``) are what let progress
    updates replace the live notification instead of being dropped. This is a
    hermetic, file-text check (no desktop required) mirroring
    ``test_gnome_reader_notification_actions_are_handled`` so a regression that
    drops that wiring is caught even though the behavioural path can only run on
    a real GNOME session.
    """
    control = GNOME_CONTROL.read_text()
    assert "--print-id" in control
    assert "--replace-id=" in control
    assert "_store_notification_id" in control
    assert "_replace_notification_text" in control
    assert "self.current_notification_id" in control


def _load_gnome_control_module():
    """Import ``gnome-reader-control.py`` by path (its name is not a valid module).

    The script imports ``dbus``/``gi`` at module top, which are absent in a
    hermetic test environment, so callers skip when those bindings are missing.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gnome_reader_control", GNOME_CONTROL
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:  # dbus / gi (PyGObject) not installed
        pytest.skip(f"gnome-reader-control deps unavailable: {exc}")
    return module


def test_store_notification_id_parses_print_id_stdout():
    """Behavioural unit test for the pure ``--print-id`` stdout parser.

    Guards the "mis-parses notify-send --print-id output" failure mode that the
    progress-refresh path depends on. The method only mutates
    ``current_notification_id``, so it is exercised against a lightweight
    stand-in (avoiding ``GnomeReaderControl.__init__``, which would open a
    D-Bus session connection).
    """
    import types

    module = _load_gnome_control_module()
    store = module.GnomeReaderControl._store_notification_id

    def parse(stdout):
        holder = types.SimpleNamespace(current_notification_id=None)
        store(holder, stdout)
        return holder.current_notification_id

    # Plain id line.
    assert parse("42\n") == 42
    # Id line followed by an action token: first digit-only line wins.
    assert parse("42\npause\n") == 42
    # Non-numeric noise before the id is skipped.
    assert parse("garbage\n7\n") == 7
    # No digit-only line anywhere: id stays unset.
    assert parse("") is None
    assert parse("no-digits\n") is None
