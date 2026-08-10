"""Deterministic tests for the non-inserting IBus Stage 0 probe."""

import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.stt import ibus_stage0


ROOT = Path(__file__).resolve().parents[2]


def _payload(**overrides):
    payload = {
        "schema_version": 1,
        "system_python": "/ignored/child/path",
        "python_available": True,
        "python_version": "3.12.1",
        "gi_available": True,
        "ibus_typelib_available": True,
        "session_bus_reachable": True,
        "ibus_bus_reachable": True,
        "registration_requested": False,
        "registration_attempted": False,
        "registration_ok": None,
        "shutdown_ok": True,
        "timed_out": False,
        "failure_stage": None,
        "failure_type": None,
    }
    payload.update(overrides)
    return payload


def _completed(payload=None, *, returncode=0, stdout=None, stderr=""):
    if stdout is None:
        stdout = ibus_stage0._RESULT_PREFIX + json.dumps(payload or _payload())
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_probe_uses_isolated_system_python_and_sanitized_environment(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/private/project")
    monkeypatch.setenv("PYTHONHOME", "/private/home")
    monkeypatch.setenv("VIRTUAL_ENV", "/private/venv")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/private/uv")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/session/bus")
    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return _completed()

    result = ibus_stage0.run_stage0_probe(
        system_python="system-python",
        timeout=2.5,
        command_runner=runner,
        python_resolver=lambda _requested: "/usr/bin/python3",
    )

    assert result.ready is True
    assert result.system_python == "/usr/bin/python3"
    assert seen["command"][:3] == ["/usr/bin/python3", "-I", "-c"]
    assert seen["command"][3] == ibus_stage0._SYSTEM_PROBE
    assert "--registration-probe" not in seen["command"]
    assert seen["kwargs"]["timeout"] == 2.5
    assert seen["kwargs"]["stdin"] is subprocess.DEVNULL
    assert seen["kwargs"]["check"] is False
    assert seen["kwargs"]["env"]["DBUS_SESSION_BUS_ADDRESS"] == (
        "unix:path=/session/bus"
    )
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        assert name not in seen["kwargs"]["env"]


def test_registration_compatibility_request_refuses_before_child_execution():
    def unexpected(*_args, **_kwargs):
        raise AssertionError("registration refusal must not resolve or run a child")

    result = ibus_stage0.run_stage0_probe(
        registration_probe=True,
        command_runner=unexpected,
        python_resolver=unexpected,
    )

    assert result.ready is False
    assert result.registration_requested is True
    assert result.registration_attempted is False
    assert result.registration_ok is None
    assert result.failure_stage == "registration-probe-unsafe"
    assert result.failure_type == "UnsupportedOperation"
    assert "disabled (unsafe)" in ibus_stage0.format_human_report(result)
    assert any(
        "changed the selected IBus engine" in action
        for action in result.to_dict()["actions"]
    )


def test_missing_system_python_fails_without_starting_child():
    def unexpected_runner(*_args, **_kwargs):
        raise AssertionError("runner must not be called")

    result = ibus_stage0.run_stage0_probe(
        command_runner=unexpected_runner,
        python_resolver=lambda _requested: None,
    )

    assert result.ready is False
    assert result.failure_stage == "system-python"
    assert result.python_available is False
    assert result.shutdown_ok is True
    assert result.to_dict()["actions"] == [
        "Install a distro-provided Python 3 interpreter."
    ]


def test_timeout_is_bounded_and_marks_shutdown_unknown():
    def runner(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["python3"], timeout=0.25)

    result = ibus_stage0.run_stage0_probe(
        timeout=0.25,
        command_runner=runner,
        python_resolver=lambda _requested: "/usr/bin/python3",
    )

    assert result.ready is False
    assert result.timed_out is True
    assert result.shutdown_ok is False
    assert result.registration_attempted is False
    assert result.failure_stage == "probe-timeout"
    assert result.failure_type == "TimeoutExpired"


def test_real_pipe_runner_bounds_combined_stdout_and_stderr():
    command = [
        sys.executable,
        "-c",
        "import os; os.write(2, b'x' * {})".format(
            ibus_stage0.MAX_PROBE_OUTPUT_BYTES + 1
        ),
    ]

    with pytest.raises(ibus_stage0.ProbeOutputLimitExceeded):
        ibus_stage0._run_bounded_child(
            command, timeout=2.0, env=dict(os.environ)
        )


def test_real_pipe_runner_enforces_one_deadline(monkeypatch):
    monkeypatch.setattr(
        ibus_stage0,
        "_SYSTEM_PROBE",
        "import time; time.sleep(30)",
    )

    result = ibus_stage0.run_stage0_probe(
        timeout=0.1,
        python_resolver=lambda _requested: sys.executable,
    )

    assert result.ready is False
    assert result.timed_out is True
    assert result.failure_stage == "probe-timeout"
    assert result.failure_type == "TimeoutExpired"


def test_timeout_kills_descendant_when_probe_leader_has_already_exited(tmp_path):
    marker = tmp_path / "descendant-pid"
    child_code = "\n".join(
        (
            "import os",
            "import sys",
            "import time",
            "descendant = os.fork()",
            "if descendant:",
            "    descriptor = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT, 0o600)",
            "    os.write(descriptor, str(descendant).encode('ascii'))",
            "    os.close(descriptor)",
            "    os._exit(0)",
            # Keep both inherited output pipes open after the leader exits.
            "time.sleep(30)",
        )
    )
    descendant_pid = None
    started = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            ibus_stage0._run_bounded_child(
                [sys.executable, "-c", child_code, str(marker)],
                timeout=0.2,
                env=dict(os.environ),
            )
        assert time.monotonic() - started < 2.0
        descendant_pid = int(marker.read_text(encoding="ascii"))

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                state = Path("/proc/{}/stat".format(descendant_pid)).read_text(
                    encoding="ascii"
                ).split()[2]
            except (FileNotFoundError, ProcessLookupError):
                break
            if state == "Z":
                break
            time.sleep(0.01)
        else:
            pytest.fail("probe descendant survived process-group teardown")
    finally:
        if descendant_pid is not None:
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_completed_runner_stderr_is_bounded_and_never_retained():
    completed = _completed(
        stderr="private stderr "
        * (ibus_stage0.MAX_PROBE_OUTPUT_BYTES // len("private stderr ") + 1)
    )

    result = ibus_stage0.run_stage0_probe(
        command_runner=lambda *_args, **_kwargs: completed,
        python_resolver=lambda _requested: "/usr/bin/python3",
    )

    assert result.ready is False
    assert result.failure_stage == "probe-result"
    assert result.failure_type == "OutputLimitExceeded"
    assert "private stderr" not in json.dumps(result.to_dict())


@pytest.mark.parametrize(
    ("completed", "failure_stage"),
    [
        (
            _completed(returncode=4, stderr="private dependency details"),
            "probe-process",
        ),
        (_completed(stdout="not a result"), "probe-result"),
        (
            _completed(stdout="x" * (ibus_stage0.MAX_PROBE_OUTPUT_BYTES + 1)),
            "probe-result",
        ),
    ],
)
def test_child_failures_are_redacted(completed, failure_stage):
    result = ibus_stage0.run_stage0_probe(
        command_runner=lambda *_args, **_kwargs: completed,
        python_resolver=lambda _requested: "/usr/bin/python3",
    )

    report = json.dumps(result.to_dict(), sort_keys=True)
    assert result.failure_stage == failure_stage
    assert "private dependency details" not in report
    assert "not a result" not in report


def test_os_start_error_reports_only_exception_class():
    def runner(*_args, **_kwargs):
        raise PermissionError("private executable path and detail")

    result = ibus_stage0.run_stage0_probe(
        command_runner=runner,
        python_resolver=lambda _requested: "/usr/bin/python3",
    )

    assert result.failure_stage == "probe-start"
    assert result.failure_type == "PermissionError"
    assert "private executable path" not in json.dumps(result.to_dict())


@pytest.mark.parametrize(
    "payload",
    [
        _payload(schema_version=2),
        _payload(schema_version=True),
        _payload(gi_available="yes"),
        _payload(registration_requested=True),
        _payload(registration_attempted=True),
        _payload(registration_ok="yes"),
        _payload(registration_ok=True),
        _payload(python_version="private/path/version"),
        _payload(timed_out=True),
        _payload(
            gi_available=False,
            ibus_typelib_available=False,
            session_bus_reachable=False,
            ibus_bus_reachable=False,
            failure_stage="/home/private/repository/transcript-marker",
            failure_type="PrivateErrorDetail",
        ),
        {**_payload(), "unexpected": "private"},
    ],
)
def test_invalid_or_mismatched_child_results_fail_closed(payload):
    result = ibus_stage0.run_stage0_probe(
        command_runner=lambda *_args, **_kwargs: _completed(payload),
        python_resolver=lambda _requested: "/usr/bin/python3",
    )

    assert result.ready is False
    assert result.failure_stage == "probe-result"
    assert result.failure_type == "InvalidResult"
    report = json.dumps(result.to_dict())
    assert "private/repository" not in report
    assert "PrivateErrorDetail" not in report


def test_failure_or_timeout_can_never_be_ready():
    fields = {
        "system_python": "/usr/bin/python3",
        "python_available": True,
        "python_version": "3.14.4",
        "gi_available": True,
        "ibus_typelib_available": True,
        "session_bus_reachable": True,
        "ibus_bus_reachable": True,
        "shutdown_ok": True,
    }
    assert ibus_stage0.Stage0Result(**fields, timed_out=True).ready is False
    assert (
        ibus_stage0.Stage0Result(
            **fields,
            failure_stage="probe-result",
            failure_type="InvalidResult",
        ).ready
        is False
    )


def test_actionable_dependency_and_session_guidance():
    gi_missing = ibus_stage0.Stage0Result(
        system_python="/usr/bin/python3",
        python_available=True,
        shutdown_ok=True,
    )
    typelib_missing = ibus_stage0.Stage0Result(
        system_python="/usr/bin/python3",
        python_available=True,
        gi_available=True,
        shutdown_ok=True,
    )
    ibus_missing = ibus_stage0.Stage0Result(
        system_python="/usr/bin/python3",
        python_available=True,
        gi_available=True,
        ibus_typelib_available=True,
        session_bus_reachable=True,
        shutdown_ok=True,
    )

    assert "PyGObject" in ibus_stage0.diagnostic_actions(gi_missing)[0]
    assert len(ibus_stage0.diagnostic_actions(gi_missing)) == 1
    assert "typelib" in ibus_stage0.diagnostic_actions(typelib_missing)[0]
    assert len(ibus_stage0.diagnostic_actions(typelib_missing)) == 1
    session_missing = ibus_stage0.Stage0Result(
        system_python="/usr/bin/python3",
        python_available=True,
        gi_available=True,
        ibus_typelib_available=True,
        shutdown_ok=True,
    )
    assert any(
        "graphical user session" in action
        for action in ibus_stage0.diagnostic_actions(session_missing)
    )
    assert any(
        "will not start" in action
        for action in ibus_stage0.diagnostic_actions(ibus_missing)
    )


def test_human_and_json_cli_are_explicit_about_non_mutation(monkeypatch, capsys):
    success = ibus_stage0._result_from_payload(_payload(), "/usr/bin/python3", False)
    monkeypatch.setattr(ibus_stage0, "run_stage0_probe", lambda **_kwargs: success)

    assert ibus_stage0.main([]) == 0
    human = capsys.readouterr().out
    assert "Input sources changed: no" in human
    assert "Text insertion attempted: no" in human

    assert ibus_stage0.main(["--json"]) == 0
    structured = json.loads(capsys.readouterr().out)
    assert structured["ready"] is True
    assert "actions" in structured


def test_cli_returns_failure_for_unavailable_probe(monkeypatch):
    unavailable = ibus_stage0.Stage0Result(
        system_python=None,
        python_available=False,
        failure_stage="system-python",
        shutdown_ok=True,
    )
    monkeypatch.setattr(ibus_stage0, "run_stage0_probe", lambda **_kwargs: unavailable)

    assert ibus_stage0.main([]) == 1


@pytest.mark.parametrize("value", ["0", "0.09", "30.01", "inf", "nan"])
def test_timeout_argument_rejects_unbounded_values(value):
    with pytest.raises(SystemExit):
        # Parsing exercises the public CLI boundary, including argparse's exit.
        ibus_stage0.build_parser().parse_args(["--timeout", value])


def test_system_python_resolution_requires_an_executable(tmp_path):
    candidate = tmp_path / "python3"
    candidate.write_text("#!/bin/sh\n")
    assert ibus_stage0.resolve_system_python(str(candidate)) is None
    candidate.chmod(0o700)
    assert ibus_stage0.resolve_system_python(str(candidate)) == str(candidate)


def test_embedded_probe_has_no_engine_or_insertion_mutation_calls():
    source = ibus_stage0._SYSTEM_PROBE.lower()
    for forbidden in (
        "register_component",
        "component.new",
        "commit_text",
        "update_preedit",
        "set_global_engine",
        "set_global_engine_async",
        "ibus-daemon",
        "gsettings",
        "add_engine",
        "ibus.factory",
    ):
        assert forbidden not in source


def test_registration_option_is_not_exposed_by_cli():
    with pytest.raises(SystemExit):
        ibus_stage0.build_parser().parse_args(["--registration-probe"])


def test_installed_launcher_loads_the_configured_project_root(tmp_path):
    home = tmp_path / "home"
    install_bin = home / ".local" / "bin"
    config_dir = home / ".config" / "linux-speech-tools"
    install_bin.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    (home / ".config").chmod(0o700)
    config_dir.chmod(0o700)
    shutil.copy2(ROOT / "bin" / "lst-ibus-check", install_bin)
    shutil.copy2(ROOT / "bin" / "linux-speech-tools-env", install_bin)
    config = config_dir / "install.env"
    config.write_text("LST_PROJECT_ROOT={}\n".format(ROOT), encoding="utf-8")
    config.chmod(0o600)
    environment = dict(os.environ)
    environment["HOME"] = str(home)
    environment.pop("LST_PROJECT_ROOT", None)

    completed = subprocess.run(
        [str(install_bin / "lst-ibus-check"), "--help"],
        capture_output=True,
        text=True,
        timeout=5,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout.lower()
