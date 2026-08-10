"""Tests for the side-by-side, no-daemon ``lst`` command router."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.voice import cli


class Harness:
    def __init__(self, available=None):
        self.available = set(cli._DELEGATE_NAMES if available is None else available)
        self.resolutions = []
        self.calls = []

    def resolver(self, name, environ):
        self.resolutions.append((name, dict(environ)))
        if name not in self.available:
            return None
        return "/installed/{}".format(name)

    def runner(self, argv, stdin_text, environ, stderr):
        self.calls.append(
            {
                "argv": tuple(argv),
                "stdin_text": stdin_text,
                "environ": dict(environ),
                "stderr": stderr,
            }
        )
        return 17

    def invoke(self, arguments, environ=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        result = cli.main(
            arguments,
            environ=(
                {"PATH": "/safe/bin", "VISIBLE": "yes"} if environ is None else environ
            ),
            resolver=self.resolver,
            runner=self.runner,
            stdout=stdout,
            stderr=stderr,
        )
        return result, stdout.getvalue(), stderr.getvalue()


def test_top_level_and_command_help_are_side_effect_free_and_name_no_daemon():
    harness = Harness()
    code, stdout, stderr = harness.invoke(["--help"])
    assert code == 0 and stderr == ""
    assert "dictate" in stdout and "ask" in stdout and "act" in stdout
    assert "read" in stdout and "status" in stdout and "doctor" in stdout
    assert "No daemon" in stdout

    for command in ("dictate", "ask", "act", "read", "status", "stop", "doctor"):
        code, stdout, stderr = harness.invoke([command, "--help"])
        assert code == 0, command
        assert "usage: lst {}".format(command) in stdout
        assert stderr == ""
    assert harness.calls == []
    assert harness.resolutions == []


@pytest.mark.parametrize("arguments", [[], ["unknown"], ["status", "--bad"]])
def test_invalid_syntax_returns_usage_exit_two_without_a_child(arguments):
    harness = Harness()
    code, _stdout, stderr = harness.invoke(arguments)
    assert code == cli.EXIT_USAGE
    assert "usage:" in stderr
    assert harness.calls == []


def test_dictate_exact_foreground_and_target_bound_argv_and_environment():
    environment = {"PATH": "/safe/bin", "ASR_LANG": "es", "TOKEN": "opaque"}
    harness = Harness()

    code, _stdout, _stderr = harness.invoke(
        ["dictate", "--language", "es"], environ=environment
    )
    assert code == 17
    assert harness.calls[-1] == {
        "argv": (
            "/installed/talk2claude-faster",
            "--language",
            "es",
        ),
        "stdin_text": None,
        "environ": environment,
        "stderr": harness.calls[-1]["stderr"],
    }

    code, _stdout, _stderr = harness.invoke(
        ["dictate", "toggle", "--profile", "codex", "--output", "clipboard"],
        environ=environment,
    )
    assert code == 17
    assert harness.calls[-1]["argv"] == (
        "/installed/lst-dictate",
        "toggle",
        "--profile",
        "codex",
        "--output",
        "clipboard",
    )
    assert harness.calls[-1]["stdin_text"] is None
    assert harness.calls[-1]["environ"] == environment


def test_ask_strips_unified_routing_and_keeps_user_text_out_of_argv_and_env():
    secret = "private request token=sk-do-not-expose"
    environment = {"PATH": "/safe/bin", "ASR_LANG": "es"}
    harness = Harness()

    code, _stdout, _stderr = harness.invoke(
        [
            "ask",
            "--agent",
            "codex",
            "--context=repository",
            "--text",
            secret,
            "--repo",
            "/workspace",
            "--output",
            "stdout",
        ],
        environ=environment,
    )

    assert code == 17
    call = harness.calls[-1]
    assert call["argv"] == (
        "/installed/lst-agent",
        "--repo",
        "/workspace",
        "--output",
        "stdout",
    )
    assert call["stdin_text"] == secret
    assert secret not in repr(call["argv"])
    assert secret not in repr(call["environ"])
    assert call["environ"] == environment


def test_ask_inherits_stdin_when_text_is_not_provided():
    harness = Harness()
    code, _stdout, _stderr = harness.invoke(
        ["ask", "--agent", "codex", "--context", "repository", "--speak"]
    )
    assert code == 17
    assert harness.calls[-1]["argv"] == ("/installed/lst-agent", "--speak")
    assert harness.calls[-1]["stdin_text"] is None


@pytest.mark.parametrize(
    "arguments, expected",
    [
        (["ask", "--agent", "goose"], "agent adapter"),
        (["ask", "--context", "browser-page"], "context provider"),
        (["read", "--tts", "unknown"], "TTS provider"),
    ],
)
def test_explicit_missing_capabilities_return_three_without_substitution(
    arguments, expected
):
    harness = Harness()
    code, _stdout, stderr = harness.invoke(arguments)
    assert code == cli.EXIT_UNAVAILABLE
    assert expected in stderr
    assert harness.calls == []


def test_ask_rejects_conflicting_or_oversized_text_before_launch():
    harness = Harness()
    code, _stdout, stderr = harness.invoke(["ask", "--text", "hello", "--dictate"])
    assert code == cli.EXIT_USAGE
    assert "mutually exclusive" in stderr

    huge = "é" * (cli.MAX_STDIN_TEXT_BYTES // 2 + 1)
    code, _stdout, stderr = harness.invoke(["ask", "--text", huge])
    assert code == cli.EXIT_USAGE
    assert "1 MiB" in stderr
    assert harness.calls == []


def test_read_kokoro_text_uses_stdin_and_passthrough_keeps_exact_argv():
    secret = "private document text"
    harness = Harness()
    code, _stdout, _stderr = harness.invoke(
        ["read", "--tts", "kokoro", "--text", secret, "--lang", "es"]
    )
    assert code == 17
    assert harness.calls[-1]["argv"] == (
        "/installed/say-read",
        "-",
        "--lang",
        "es",
    )
    assert harness.calls[-1]["stdin_text"] == secret
    assert secret not in repr(harness.calls[-1]["argv"])

    code, _stdout, _stderr = harness.invoke(["read", "document.pdf", "--stream"])
    assert code == 17
    assert harness.calls[-1]["argv"] == (
        "/installed/say-read",
        "document.pdf",
        "--stream",
    )
    assert harness.calls[-1]["stdin_text"] is None


def test_edge_compatibility_uses_say_and_documents_argv_exception():
    harness = Harness()
    code, _stdout, _stderr = harness.invoke(
        ["read", "--tts=edge", "--text", "hello", "-v", "es-ES-ElviraNeural"]
    )
    assert code == 17
    assert harness.calls[-1]["argv"] == (
        "/installed/say",
        "-v",
        "es-ES-ElviraNeural",
        "hello",
    )
    assert harness.calls[-1]["stdin_text"] is None


def test_act_always_returns_unavailable_and_never_resolves_or_executes():
    harness = Harness()
    code, stdout, stderr = harness.invoke(["act", "run", "rm", "something"])
    assert code == cli.EXIT_UNAVAILABLE
    assert stdout == ""
    assert "nothing was executed" in stderr
    assert harness.resolutions == []
    assert harness.calls == []


def test_stop_is_explicitly_unavailable_and_never_toggles_a_launcher():
    harness = Harness()
    code, stdout, stderr = harness.invoke(["stop"])
    assert code == cli.EXIT_UNAVAILABLE
    assert stdout == ""
    assert "nothing was started" in stderr
    assert harness.resolutions == []
    assert harness.calls == []

    code, _stdout, stderr = harness.invoke(["stop", "unexpected"])
    assert code == cli.EXIT_USAGE
    assert "accepts no arguments" in stderr


def test_status_is_exact_bounded_coarse_and_content_free():
    harness = Harness()
    environment = {
        "PATH": "/private/bin",
        "TOKEN": "ghp_secret",
        "PROMPT": "verbatim transcript",
    }
    code, stdout, stderr = harness.invoke(["status", "--json"], environ=environment)
    assert code == 0 and stderr == ""
    assert json.loads(stdout) == {
        "schema_version": 1,
        "host": "standalone",
        "daemon": "not-implemented",
        "modes": {
            "dictate": "delegated",
            "ask": "delegated",
            "act": "unavailable",
            "read": "delegated",
        },
    }
    assert "ghp_secret" not in stdout
    assert "verbatim transcript" not in stdout
    assert "/private" not in stdout
    assert harness.resolutions == [] and harness.calls == []


def test_doctor_reports_only_fixed_availability_and_ibus_delegates():
    harness = Harness(available=cli._DELEGATE_NAMES - {"say"})
    code, stdout, stderr = harness.invoke(["doctor", "--json"])
    assert code == 0 and stderr == ""
    payload = json.loads(stdout)
    assert payload["daemon"] == "not-implemented"
    assert payload["ready"] is True
    assert payload["launchers"]["read-edge"] is False
    assert "/installed" not in stdout
    assert harness.calls == []

    code, _stdout, _stderr = harness.invoke(
        ["doctor", "ibus", "--json", "--timeout", "1"]
    )
    assert code == 17
    assert harness.calls[-1]["argv"] == (
        "/installed/lst-ibus-check",
        "--json",
        "--timeout",
        "1",
    )


def test_missing_core_launcher_returns_unavailable_without_a_child():
    harness = Harness(available=())
    code, _stdout, stderr = harness.invoke(["dictate"])
    assert code == cli.EXIT_UNAVAILABLE
    assert "talk2claude-faster is not installed" in stderr
    assert harness.calls == []

    code, stdout, _stderr = harness.invoke(["doctor", "--json"])
    assert code == cli.EXIT_UNAVAILABLE
    assert json.loads(stdout)["ready"] is False


class FakeProcess:
    def __init__(
        self,
        *,
        returncode=0,
        interrupt_wait=False,
        interrupt_communicate=False,
        communicate_error=None,
    ):
        self.pid = 4242
        self.returncode = returncode
        self.interrupt_wait = interrupt_wait
        self.interrupt_communicate = interrupt_communicate
        self.communicate_error = communicate_error
        self.wait_timeouts = []
        self.communicated = None

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if timeout is None and self.interrupt_wait:
            self.interrupt_wait = False
            raise KeyboardInterrupt
        if timeout is not None and len(self.wait_timeouts) < 4:
            raise subprocess.TimeoutExpired(["delegate"], timeout)
        return self.returncode

    def communicate(self, input=None):
        self.communicated = input
        if self.interrupt_communicate:
            self.interrupt_communicate = False
            raise KeyboardInterrupt
        if self.communicate_error is not None:
            raise self.communicate_error
        return None, None


def test_child_runner_uses_exact_argv_environment_no_shell_and_maps_exit(monkeypatch):
    process = FakeProcess(returncode=-signal.SIGTERM)
    calls = []

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return process

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    stderr = io.StringIO()
    environment = {"PATH": "/safe/bin", "LANG": "C"}
    code = cli._run_child(["/installed/delegate", "--flag"], None, environment, stderr)

    assert code == 128 + signal.SIGTERM
    assert calls == [
        (
            ["/installed/delegate", "--flag"],
            {"env": environment, "start_new_session": True},
        )
    ]
    assert "shell" not in calls[0][1]


def test_child_runner_uses_pipe_for_text_and_owned_group_cleanup_on_interrupt(
    monkeypatch,
):
    process = FakeProcess(interrupt_communicate=True)
    popen_calls = []
    signals = []
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda argv, **kwargs: (popen_calls.append((argv, kwargs)) or process),
    )
    monkeypatch.setattr(
        cli.os, "killpg", lambda pid, signum: signals.append((pid, signum))
    )
    secret = "stdin-only secret"
    environment = {"PATH": "/safe/bin"}

    code = cli._run_child(
        ["/installed/delegate", "-"], secret, environment, io.StringIO()
    )

    assert code == cli.EXIT_INTERRUPTED
    assert process.communicated == secret
    assert secret not in repr(popen_calls[0][0])
    assert secret not in repr(popen_calls[0][1])
    assert popen_calls[0] == (
        ["/installed/delegate", "-"],
        {
            "env": environment,
            "start_new_session": True,
            "stdin": subprocess.PIPE,
            "text": True,
        },
    )
    assert signals == [
        (process.pid, signal.SIGINT),
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]
    assert process.wait_timeouts == [
        cli.CHILD_STOP_GRACE_SECONDS,
        cli.CHILD_STOP_GRACE_SECONDS,
        cli.CHILD_STOP_GRACE_SECONDS,
    ]


def test_cleanup_kills_descendant_after_delegate_leader_exits(tmp_path):
    child_pid_file = tmp_path / "descendant.pid"
    script = (
        "import os,signal,sys,time\n"
        "signal.signal(signal.SIGINT, lambda *_: sys.exit(0))\n"
        "pid=os.fork()\n"
        "if pid == 0:\n"
        " signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        " open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        " while True: time.sleep(1)\n"
        "while True: time.sleep(1)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(child_pid_file)],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and (
            not child_pid_file.exists() or child_pid_file.stat().st_size == 0
        ):
            time.sleep(0.01)
        assert child_pid_file.exists() and child_pid_file.stat().st_size > 0
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))

        cli._stop_signalled_child(process, signal.SIGINT)

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            stat_path = Path("/proc") / str(child_pid) / "stat"
            if not stat_path.exists():
                break
            fields = stat_path.read_text(encoding="utf-8").split()
            if len(fields) > 2 and fields[2] == "Z":
                break
            time.sleep(0.01)
        else:
            pytest.fail("delegate descendant survived cleanup")
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()


def test_child_start_race_is_redacted_unavailable(monkeypatch):
    def failed_popen(*_args, **_kwargs):
        raise OSError("/private/path token=secret")

    monkeypatch.setattr(cli.subprocess, "Popen", failed_popen)
    stderr = io.StringIO()
    code = cli._run_child(["/installed/delegate"], None, {"PATH": "/safe/bin"}, stderr)
    assert code == cli.EXIT_UNAVAILABLE
    assert stderr.getvalue() == (
        "lst: unavailable: compatibility launcher could not be started\n"
    )
    assert "private" not in stderr.getvalue()


def test_unexpected_runner_error_cleans_owned_group_before_reraising(monkeypatch):
    failure = RuntimeError("simulated runner failure")
    process = FakeProcess(communicate_error=failure)
    signals = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        cli.os, "killpg", lambda pid, signum: signals.append((pid, signum))
    )

    with pytest.raises(RuntimeError, match="simulated runner failure"):
        cli._run_child(
            ["/installed/delegate", "-"],
            "private input",
            {"PATH": "/safe/bin"},
            io.StringIO(),
        )

    assert signals == [
        (process.pid, signal.SIGINT),
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]


def test_cleanup_wait_errors_do_not_mask_original_runner_failure(monkeypatch):
    failure = RuntimeError("simulated runner failure")
    process = FakeProcess(communicate_error=failure)
    signals = []
    process.wait = lambda timeout=None: (_ for _ in ()).throw(
        ChildProcessError("already reaped")
    )
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        cli.os, "killpg", lambda pid, signum: signals.append((pid, signum))
    )

    with pytest.raises(RuntimeError, match="simulated runner failure"):
        cli._run_child(
            ["/installed/delegate", "-"],
            "private input",
            {"PATH": "/safe/bin"},
            io.StringIO(),
        )

    assert signals == [
        (process.pid, signal.SIGINT),
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]


@pytest.mark.parametrize(
    "signum", (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGQUIT)
)
def test_terminal_signals_are_forwarded_with_cleanup_and_handlers_restored(
    monkeypatch, signum
):
    process = FakeProcess()
    installed = {}
    previous = object()
    signals = []

    def fake_signal(signum, handler):
        installed[signum] = handler
        return previous

    def wait(timeout=None):
        process.wait_timeouts.append(timeout)
        if timeout is None:
            installed[signum](signum, None)
        if len(process.wait_timeouts) < 4:
            raise subprocess.TimeoutExpired(["delegate"], timeout)
        return 0

    process.wait = wait
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(cli.signal, "getsignal", lambda _signum: previous)
    monkeypatch.setattr(cli.signal, "signal", fake_signal)
    monkeypatch.setattr(
        cli.os, "killpg", lambda pid, signum: signals.append((pid, signum))
    )

    code = cli._run_child(
        ["/installed/delegate"], None, {"PATH": "/safe/bin"}, io.StringIO()
    )

    assert code == 128 + signum
    expected = [(process.pid, signum)]
    if signum != signal.SIGTERM:
        expected.append((process.pid, signal.SIGTERM))
    expected.append((process.pid, signal.SIGKILL))
    assert signals == expected
    for registered in (
        signal.SIGHUP,
        signal.SIGINT,
        signal.SIGTERM,
        signal.SIGQUIT,
    ):
        assert installed[registered] is previous


def test_start_failure_restores_all_installed_handlers(monkeypatch):
    installed = {}
    previous = object()

    def fake_signal(signum, handler):
        installed[signum] = handler
        return previous

    monkeypatch.setattr(cli.signal, "getsignal", lambda _signum: previous)
    monkeypatch.setattr(cli.signal, "signal", fake_signal)
    monkeypatch.setattr(cli.signal, "pthread_sigmask", lambda *_args: set())
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("private")),
    )

    code = cli._run_child(
        ["/installed/delegate"], None, {"PATH": "/safe/bin"}, io.StringIO()
    )

    assert code == cli.EXIT_UNAVAILABLE
    assert set(installed) == set(cli._TERMINATION_SIGNALS)
    assert all(installed[signum] is previous for signum in cli._TERMINATION_SIGNALS)


def test_launcher_preserves_caller_working_directory(tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    launcher = project_root / "bin" / "lst"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    capture = tmp_path / "capture.txt"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'pwd > "$LST_TEST_CAPTURE"\n'
        'printf \'%s\\n\' "$@" >> "$LST_TEST_CAPTURE"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    caller = tmp_path / "caller-repository"
    caller.mkdir()
    environment = {
        "HOME": str(tmp_path),
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        "LST_PROJECT_ROOT": str(project_root),
        "LST_TEST_CAPTURE": str(capture),
    }

    completed = subprocess.run(
        [str(launcher), "ask", "--check"],
        cwd=str(caller),
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert completed.returncode == 0
    assert capture.read_text(encoding="utf-8").splitlines() == [
        str(caller),
        "--project",
        str(project_root),
        "run",
        "--locked",
        "python",
        "-m",
        "src.voice.cli",
        "ask",
        "--check",
    ]


def test_say_read_preserves_caller_directory_for_relative_sources(tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    launcher = project_root / "bin" / "say-read"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    capture = tmp_path / "capture.txt"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'pwd > "$LST_TEST_CAPTURE"\n'
        'printf \'%s\\n\' "$@" >> "$LST_TEST_CAPTURE"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    caller = tmp_path / "caller-documents"
    caller.mkdir()
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": str(fake_bin) + os.pathsep + environment.get("PATH", ""),
            "LST_PROJECT_ROOT": str(project_root),
            "LST_TEST_CAPTURE": str(capture),
        }
    )

    completed = subprocess.run(
        [str(launcher), "--original", "relative.txt"],
        cwd=str(caller),
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5,
    )

    assert completed.returncode == 0, completed.stderr
    captured = capture.read_text(encoding="utf-8").splitlines()
    assert captured[0] == str(caller)
    assert str(project_root / "src/tts/say_read.py") in captured
    assert captured[-1] == "relative.txt"
