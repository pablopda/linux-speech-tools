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
