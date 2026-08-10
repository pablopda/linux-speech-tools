from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.assistant import repo_agent
from src.stt.insertion_session import InsertionState, result
from src.stt.target_context import TargetContext


class FakeProcess:
    def __init__(
        self, *, stdout="answer", returncode=0, timeout=False, interrupt=False
    ):
        self.stdout_value = stdout
        self.returncode = returncode
        self.timeout = timeout
        self.interrupt = interrupt
        self.pid = 4242
        self.communicate_input = None
        self.communicate_timeout = None
        self.wait_calls = []
        self._poll = None
        self.output_handle = None

    def communicate(self, *, input=None, timeout=None):
        self.communicate_input = input
        self.communicate_timeout = timeout
        if self.timeout:
            raise subprocess.TimeoutExpired(["codex"], timeout)
        if self.interrupt:
            raise KeyboardInterrupt
        self._poll = self.returncode
        return None, None

    def poll(self):
        return self._poll

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if len(self.wait_calls) == 1:
            raise subprocess.TimeoutExpired(["codex"], timeout)
        self._poll = -signal.SIGKILL
        self.returncode = self._poll
        return self.returncode


def gnome_editor_context(
    *,
    app_id="org.gnome.TextEditor",
    wm_class="",
    source="gnome-focus",
    session_id="shell-session-a",
    window_sequence=17,
    focus_generation=23,
    locked=False,
):
    return TargetContext(
        kind="unknown",
        confidence="high",
        source=source,
        app_id=app_id,
        wm_class=wm_class,
        window_id="gnome:{}:{}".format(session_id, window_sequence),
        schema_version=1,
        shell_session_id=session_id,
        window_sequence=window_sequence,
        focus_generation=focus_generation,
        locked=locked,
    )


def adapter(monkeypatch, *, process=None, **kwargs):
    process = process or FakeProcess()
    monkeypatch.setattr(
        repo_agent.os.path, "isfile", lambda value: value == "/usr/bin/codex"
    )
    monkeypatch.setattr(
        repo_agent.os, "access", lambda value, mode: value == "/usr/bin/codex"
    )
    monkeypatch.setattr(repo_agent, "resolve_repository", lambda value: Path("/repo"))
    calls = []

    def fake_popen(argv, **popen_kwargs):
        calls.append((argv, popen_kwargs))
        return process

    monkeypatch.setattr(repo_agent.subprocess, "Popen", fake_popen)

    def fake_communicate(owned_process, input_data, *, output_limit, timeout):
        owned_process.communicate_input = input_data.decode("utf-8")
        owned_process.communicate_timeout = timeout
        if owned_process.timeout:
            raise subprocess.TimeoutExpired(["codex"], timeout)
        if owned_process.interrupt:
            raise KeyboardInterrupt
        owned_process._poll = owned_process.returncode
        raw = owned_process.stdout_value
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return raw[:output_limit], len(raw) > output_limit

    monkeypatch.setattr(repo_agent, "_communicate_bounded", fake_communicate)
    return (
        repo_agent.CodexExecAdapter(executable="/usr/bin/codex", **kwargs),
        process,
        calls,
    )


def test_codex_adapter_is_ephemeral_read_only_and_keeps_request_off_argv(monkeypatch):
    subject, process, calls = adapter(monkeypatch, timeout_seconds=12)
    secret = "private spoken repository request"

    outcome = subject.run(secret, Path("/repo"))

    assert outcome.ok
    argv, kwargs = calls[0]
    assert argv == [
        "/usr/bin/codex",
        "--ask-for-approval",
        "never",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--cd",
        "/repo",
        "-",
    ]
    assert secret not in repr(argv)
    assert secret in process.communicate_input
    assert process.communicate_timeout == 12
    assert kwargs["start_new_session"] is True
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["stdin"] is subprocess.PIPE
    assert kwargs["stdout"] is subprocess.PIPE
    assert "text" not in kwargs
    assert "shell" not in kwargs


def test_adapter_optional_model_is_not_confused_with_request(monkeypatch):
    subject, _process, calls = adapter(monkeypatch, model="configured-model")
    assert subject.run("question", Path("/repo"))
    assert calls[0][0][-3:] == ["--model", "configured-model", "-"]


def test_adapter_timeout_terminates_owned_group_and_returns_redacted_error(monkeypatch):
    subject, process, _calls = adapter(
        monkeypatch,
        process=FakeProcess(timeout=True),
        timeout_seconds=1,
    )
    signals = []
    monkeypatch.setattr(
        repo_agent.os, "killpg", lambda pid, sig: signals.append((pid, sig))
    )

    outcome = subject.run("secret prompt", Path("/repo"))

    assert not outcome.ok
    assert outcome.diagnostic == "codex run timed out"
    assert "secret" not in repr(outcome)
    assert signals == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    assert process.wait_calls == [2.0, 2.0]


def test_adapter_interrupt_terminates_owned_group_before_reraising(monkeypatch):
    subject, process, _calls = adapter(
        monkeypatch,
        process=FakeProcess(interrupt=True),
    )
    signals = []
    monkeypatch.setattr(
        repo_agent.os,
        "killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )

    with pytest.raises(KeyboardInterrupt):
        subject.run("secret prompt", Path("/repo"))

    assert signals == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]
    assert process.wait_calls == [2.0, 2.0]


def test_adapter_nonzero_exit_does_not_expose_stderr(monkeypatch):
    subject, _process, _calls = adapter(
        monkeypatch,
        process=FakeProcess(returncode=7),
    )
    outcome = subject.run("request", Path("/repo"))
    assert not outcome.ok
    assert outcome.diagnostic == "codex run failed"
    assert "private/repository/error" not in repr(outcome)


def test_adapter_bounds_request_and_response(monkeypatch):
    subject, _process, _calls = adapter(
        monkeypatch,
        process=FakeProcess(stdout="x" * 100),
        max_request_chars=10,
        max_response_chars=40,
    )
    rejected = subject.run("y" * 11, Path("/repo"))
    assert not rejected.ok
    assert rejected.diagnostic == "request exceeds configured limit"

    outcome = subject.run("question", Path("/repo"))
    assert outcome.ok and outcome.truncated
    assert len(outcome.text) <= 40
    assert outcome.text.endswith("[response truncated]")


def test_tiny_response_limit_is_still_a_hard_bound(monkeypatch):
    subject, _process, _calls = adapter(
        monkeypatch,
        process=FakeProcess(stdout="oversized"),
        max_response_chars=5,
    )
    outcome = subject.run("question", Path("/repo"))
    assert outcome.ok and outcome.truncated
    assert len(outcome.text) == 5


def test_agent_output_controls_and_invisible_formatting_are_removed(monkeypatch):
    unsafe = "safe\x1b[31m red\x07\ttext\u202ehidden"
    subject, _process, _calls = adapter(
        monkeypatch,
        process=FakeProcess(stdout=unsafe),
    )
    outcome = subject.run("question", Path("/repo"))
    assert outcome.ok
    assert "\x1b" not in outcome.text
    assert "\x07" not in outcome.text
    assert "\u202e" not in outcome.text
    assert "    text" in outcome.text


def test_invalid_utf8_agent_output_is_replaced_not_raised(monkeypatch):
    subject, _process, _calls = adapter(
        monkeypatch,
        process=FakeProcess(stdout=b"valid\xffanswer"),
    )

    outcome = subject.run("question", Path("/repo"))

    assert outcome.ok
    assert outcome.text == "valid\ufffdanswer"


def test_bounded_communicator_drains_but_does_not_retain_oversized_output():
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.stdin.buffer.read(); "
                "sys.stdout.buffer.write(b'x' * 2_000_000)"
            ),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    retained, truncated = repo_agent._communicate_bounded(
        process,
        b"request",
        output_limit=32,
        timeout=5,
    )

    assert retained == b"x" * 32
    assert truncated is True
    assert process.returncode == 0


@pytest.mark.parametrize("value", ["", "   "])
def test_adapter_rejects_empty_request_without_starting(monkeypatch, value):
    subject, _process, calls = adapter(monkeypatch)
    assert not subject.run(value, Path("/repo")).ok
    assert calls == []


def test_resolve_repository_uses_git_root_and_bounds_probe(monkeypatch, tmp_path):
    root = tmp_path.resolve()
    recorded = {}

    def fake_run(argv, **kwargs):
        recorded.update(argv=argv, kwargs=kwargs)
        return SimpleNamespace(returncode=0, stdout=str(root) + "\n")

    monkeypatch.setattr(repo_agent.subprocess, "run", fake_run)
    assert repo_agent.resolve_repository(root) == root
    assert recorded["argv"] == ["git", "-C", str(root), "rev-parse", "--show-toplevel"]
    assert recorded["kwargs"]["timeout"] == 3
    assert recorded["kwargs"]["check"] is False


def test_resolve_repository_rejects_non_git_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(
        repo_agent.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=128, stdout=""),
    )
    with pytest.raises(ValueError, match="not a Git repository"):
        repo_agent.resolve_repository(tmp_path)


def test_speech_collector_sends_only_finalized_utterances(monkeypatch, tmp_path):
    class FakeSession:
        def __init__(self, **kwargs):
            self.output_handler = kwargs["output_handler"]

        def run(self):
            assert self.output_handler("first request part")
            assert self.output_handler("second part")
            return True

    monkeypatch.setitem(
        sys.modules,
        "src.stt.session",
        SimpleNamespace(FasterWhisperSession=FakeSession),
    )
    monkeypatch.setattr(repo_agent, "developer_hints", lambda *args: "repo hints")
    collector = repo_agent.SpeechPromptCollector(
        repository=tmp_path,
        engine="faster-whisper",
        model_size="tiny",
        language="en",
        device="cpu",
        vad_aggressiveness=2,
        max_request_chars=100,
    )
    ok, text = collector.run()
    assert ok
    assert text == "first request part second part"


def test_speech_collector_fails_without_retaining_multi_utterance_overrun(
    monkeypatch, tmp_path
):
    class FakeSession:
        def __init__(self, **kwargs):
            self.output_handler = kwargs["output_handler"]
            self.running = False

        def run(self):
            self.running = True
            assert self.output_handler("12345")
            assert not self.output_handler("67890")
            assert not self.output_handler("later callback")
            assert self.running is False
            return True

    monkeypatch.setitem(
        sys.modules,
        "src.stt.session",
        SimpleNamespace(FasterWhisperSession=FakeSession),
    )
    monkeypatch.setattr(repo_agent, "developer_hints", lambda *args: "repo hints")
    collector = repo_agent.SpeechPromptCollector(
        repository=tmp_path,
        engine="faster-whisper",
        model_size="tiny",
        language="en",
        device="cpu",
        vad_aggressiveness=2,
        max_request_chars=10,
    )

    assert collector.run() == (False, "")
    assert collector.parts == []
    assert collector._retained_chars == 0
    assert collector.limit_exceeded is True


def test_speech_collector_restores_prior_signal_handlers(monkeypatch, tmp_path):
    prior_int = object()
    prior_term = object()
    handlers = {
        signal.SIGINT: prior_int,
        signal.SIGTERM: prior_term,
    }

    def fake_signal(signum, handler):
        handlers[signum] = handler

    monkeypatch.setattr(repo_agent.signal, "getsignal", lambda signum: handlers[signum])
    monkeypatch.setattr(repo_agent.signal, "signal", fake_signal)

    class FakeSession:
        def __init__(self, **kwargs):
            self.output_handler = kwargs["output_handler"]
            repo_agent.signal.signal(signal.SIGINT, self.signal_handler)
            repo_agent.signal.signal(signal.SIGTERM, self.signal_handler)

        def signal_handler(self, signum, frame):
            return None

        def run(self):
            assert handlers[signal.SIGINT] == self.signal_handler
            assert handlers[signal.SIGTERM] == self.signal_handler
            return True

    monkeypatch.setitem(
        sys.modules,
        "src.stt.session",
        SimpleNamespace(FasterWhisperSession=FakeSession),
    )
    monkeypatch.setattr(repo_agent, "developer_hints", lambda *args: "repo hints")
    collector = repo_agent.SpeechPromptCollector(
        repository=tmp_path,
        engine="faster-whisper",
        model_size="tiny",
        language="en",
        device="cpu",
        vad_aggressiveness=2,
        max_request_chars=100,
    )

    assert collector.run() == (True, "")
    assert handlers == {
        signal.SIGINT: prior_int,
        signal.SIGTERM: prior_term,
    }


def test_speech_collector_restores_handlers_when_session_construction_fails(
    monkeypatch, tmp_path
):
    prior_int = object()
    prior_term = object()
    handlers = {
        signal.SIGINT: prior_int,
        signal.SIGTERM: prior_term,
    }

    monkeypatch.setattr(repo_agent.signal, "getsignal", lambda signum: handlers[signum])
    monkeypatch.setattr(
        repo_agent.signal,
        "signal",
        lambda signum, handler: handlers.__setitem__(signum, handler),
    )

    class FailingSession:
        def __init__(self, **kwargs):
            repo_agent.signal.signal(signal.SIGINT, object())
            repo_agent.signal.signal(signal.SIGTERM, object())
            raise RuntimeError("session failed")

    monkeypatch.setitem(
        sys.modules,
        "src.stt.session",
        SimpleNamespace(FasterWhisperSession=FailingSession),
    )
    monkeypatch.setattr(repo_agent, "developer_hints", lambda *args: "repo hints")

    with pytest.raises(RuntimeError, match="session failed"):
        repo_agent.SpeechPromptCollector(
            repository=tmp_path,
            engine="faster-whisper",
            model_size="tiny",
            language="en",
            device="cpu",
            vad_aggressiveness=2,
            max_request_chars=100,
        )

    assert handlers == {
        signal.SIGINT: prior_int,
        signal.SIGTERM: prior_term,
    }


def test_request_file_must_be_bounded_regular_nonsymlink(tmp_path):
    regular = tmp_path / "request.txt"
    regular.write_text("café", encoding="utf-8")
    symlink = tmp_path / "request-link"
    symlink.symlink_to(regular)
    fifo = tmp_path / "request-fifo"
    os.mkfifo(str(fifo))
    args = argparse.Namespace(
        dictate=False,
        request_file=str(regular),
        max_request_chars=10,
    )

    assert repo_agent.read_request(args, tmp_path) == (True, "café")
    args.request_file = str(symlink)
    assert repo_agent.read_request(args, tmp_path) == (False, "")
    args.request_file = str(fifo)
    assert repo_agent.read_request(args, tmp_path) == (False, "")

    oversized = tmp_path / "oversized.txt"
    oversized.write_text("x" * 41, encoding="utf-8")
    args.request_file = str(oversized)
    assert repo_agent.read_request(args, tmp_path) == (False, "")


def test_huge_tty_request_read_is_bounded_to_limit_plus_sentinel(
    monkeypatch, tmp_path
):
    class HugeTTY:
        def __init__(self):
            self.requested_size = None

        def isatty(self):
            return True

        def readline(self, size):
            self.requested_size = size
            return "x" * min(size, 100_000)

    stdin = HugeTTY()
    monkeypatch.setattr(repo_agent.sys, "stdin", stdin)
    args = argparse.Namespace(
        dictate=False,
        request_file="",
        max_request_chars=8,
    )

    assert repo_agent.read_request(args, tmp_path) == (True, "x" * 9)
    assert stdin.requested_size == 9


def test_dictation_request_wires_configured_character_limit(monkeypatch, tmp_path):
    observed = {}

    class FakeCollector:
        def __init__(self, **kwargs):
            observed.update(kwargs)

        def run(self):
            return True, "bounded request"

    monkeypatch.setattr(repo_agent, "SpeechPromptCollector", FakeCollector)
    monkeypatch.setattr(repo_agent, "resolve_engine", lambda engine: "resolved")
    args = argparse.Namespace(
        dictate=True,
        request_file="",
        max_request_chars=17,
        engine="auto",
        asr_model="tiny",
        language="en",
        device="cpu",
        vad=2,
    )

    assert repo_agent.read_request(args, tmp_path) == (True, "bounded request")
    assert observed["max_request_chars"] == 17


def test_delivery_finalizes_once_without_submit_and_closes(monkeypatch):
    calls = []

    class FakeInsertion:
        def finalize(self, text, revision):
            calls.append(("finalize", text, revision))
            return result(InsertionState.NON_INSERTING, "stdout", revision=revision)

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(
        repo_agent, "insertion_session_for", lambda *args, **kwargs: FakeInsertion()
    )
    outcome = repo_agent.deliver_response(
        "bounded answer",
        output="stdout",
        target=TargetContext(),
        paste_keys="auto",
    )
    assert outcome.ok
    assert calls == [("finalize", "bounded answer", 1), ("close",)]


@pytest.mark.parametrize(
    "target",
    [
        TargetContext(kind="terminal", app_id="org.gnome.Terminal", window_id="1"),
        # GNOME Console does not contain the word "terminal" in its identity.
        TargetContext(
            kind="ide",
            confidence="explicit",
            app_id="org.gnome.Console",
            wm_class="org.gnome.Console",
            window_id="2",
        ),
        # Window-level focus cannot distinguish an IDE's editor from its shell.
        TargetContext(kind="ide", app_id="com.visualstudio.code", window_id="3"),
        TargetContext(kind="browser", app_id="firefox", window_id="4"),
        TargetContext(
            kind="generic", app_id="org.gnome.TextEditor", window_id="5"
        ),
        TargetContext(
            kind="unexpected-kind",
            app_id="org.gnome.TextEditor",
            window_id="5b",
        ),
        TargetContext(
            kind="unknown",
            app_id="org.gnome.TextEditor",
            wm_class="org.gnome.Console",
            window_id="5c",
        ),
        TargetContext(
            kind="unknown",
            app_id="org.example.Editor",
            title="org.gnome.TextEditor",
            window_id="6",
        ),
        TargetContext(
            kind="unknown",
            app_id="org.gnome.TextEditor.shell",
            window_id="6b",
        ),
        # Some supported gedit/plugin combinations can host an embedded terminal.
        TargetContext(kind="unknown", app_id="org.gnome.gedit", window_id="6c"),
        TargetContext(kind="unknown", window_id="7"),
    ],
)
def test_untrusted_agent_output_degrades_non_allowlisted_targets_to_clipboard(
    monkeypatch, target
):
    observed = {}

    class FakeInsertion:
        def finalize(self, text, revision):
            return result(InsertionState.NON_INSERTING, "clipboard", revision=revision)

        def close(self):
            return None

    def fake_factory(output, target_kind, **kwargs):
        observed.update(output=output, target_kind=target_kind)
        return FakeInsertion()

    monkeypatch.setattr(repo_agent, "insertion_session_for", fake_factory)
    monkeypatch.setattr(
        repo_agent,
        "live_focus_context",
        lambda: pytest.fail("blocked identities must not be focus-probed"),
    )
    outcome = repo_agent.deliver_response(
        "command one\ncommand two",
        output="live-type",
        target=target,
        paste_keys="auto",
    )
    assert outcome.ok
    assert observed == {"output": "clipboard", "target_kind": target.kind}


@pytest.mark.parametrize("source", ["env-json", "x11", "profile"])
def test_forged_or_non_authoritative_editor_identity_degrades_to_clipboard(
    monkeypatch, source
):
    observed = {}
    if source == "x11":
        captured = TargetContext(
            kind="unknown",
            source="x11",
            app_id="org.gnome.TextEditor",
            window_id="4242",
        )
        current = TargetContext(
            kind="terminal",
            source="x11",
            wm_class="org.gnome.Console",
            window_id="4242",
        )
    else:
        captured = gnome_editor_context(source=source)
        current = gnome_editor_context(app_id="org.gnome.Console")

    class FakeInsertion:
        def finalize(self, text, revision):
            return result(InsertionState.NON_INSERTING, "clipboard", revision=revision)

        def close(self):
            return None

    monkeypatch.setattr(repo_agent, "live_focus_context", lambda: current)
    monkeypatch.setattr(
        repo_agent,
        "insertion_session_for",
        lambda output, target_kind, **kwargs: (
            observed.update(output=output, focus_guard=kwargs["focus_guard"])
            or FakeInsertion()
        ),
    )

    outcome = repo_agent.deliver_response(
        "command one\ncommand two",
        output="paste",
        target=captured,
        paste_keys="auto",
    )

    assert outcome.ok
    assert observed["output"] == "clipboard"
    assert observed["focus_guard"]() is False


@pytest.mark.parametrize(
    "current",
    [
        gnome_editor_context(app_id="org.gnome.Console"),
        gnome_editor_context(wm_class="org.gnome.Console"),
        gnome_editor_context(source="env-json"),
        gnome_editor_context(session_id="restarted-shell"),
        gnome_editor_context(focus_generation=24),
        gnome_editor_context(locked=True),
    ],
)
def test_current_identity_restart_generation_or_lock_drift_degrades_to_clipboard(
    monkeypatch, current
):
    observed = {}

    class FakeInsertion:
        def finalize(self, text, revision):
            return result(InsertionState.NON_INSERTING, "clipboard", revision=revision)

        def close(self):
            return None

    monkeypatch.setattr(repo_agent, "live_focus_context", lambda: current)
    monkeypatch.setattr(
        repo_agent,
        "insertion_session_for",
        lambda output, target_kind, **kwargs: (
            observed.update(output=output) or FakeInsertion()
        ),
    )

    outcome = repo_agent.deliver_response(
        "command one\ncommand two",
        output="live-type",
        target=gnome_editor_context(),
        paste_keys="auto",
    )

    assert outcome.ok
    assert observed["output"] == "clipboard"


def test_current_editor_identity_is_revalidated_by_placement_guard(monkeypatch):
    captured = gnome_editor_context()
    drifted = gnome_editor_context(wm_class="org.gnome.Console")
    snapshots = iter([captured, drifted])
    observed = {}

    class FakeInsertion:
        def finalize(self, text, revision):
            observed["placement_safe"] = observed["focus_guard"]()
            return result(
                InsertionState.CLIPBOARD_FALLBACK,
                "clipboard",
                revision=revision,
                target_token_match=False,
            )

        def close(self):
            return None

    monkeypatch.setattr(repo_agent, "live_focus_context", lambda: next(snapshots))
    monkeypatch.setattr(
        repo_agent,
        "insertion_session_for",
        lambda output, target_kind, **kwargs: (
            observed.update(output=output, focus_guard=kwargs["focus_guard"])
            or FakeInsertion()
        ),
    )

    outcome = repo_agent.deliver_response(
        "command one\ncommand two",
        output="paste",
        target=captured,
        paste_keys="auto",
    )

    assert outcome.ok
    assert observed["output"] == "paste"
    assert observed["placement_safe"] is False
    assert outcome.backend == "clipboard"


@pytest.mark.parametrize(
    "captured",
    [
        TargetContext(
            kind="unknown",
            source="gnome-focus",
            app_id="org.gnome.TextEditor",
            window_id="gnome:session:1",
            schema_version=None,
            shell_session_id="session",
            window_sequence=1,
            focus_generation=1,
            locked=False,
        ),
        gnome_editor_context(locked=True),
        gnome_editor_context(session_id=""),
        gnome_editor_context(window_sequence=True),
        gnome_editor_context(focus_generation=True),
    ],
)
def test_incomplete_or_mistyped_gnome_snapshot_is_never_authorized(
    monkeypatch, captured
):
    monkeypatch.setattr(
        repo_agent,
        "live_focus_context",
        lambda: pytest.fail("incomplete snapshot must fail before a live query"),
    )

    assert not repo_agent._direct_insertion_allowed(captured)


@pytest.mark.parametrize(
    ("app_id", "wm_class"),
    [
        ("org.gnome.TextEditor", ""),
        ("", "Gnome-text-editor"),
    ],
)
@pytest.mark.parametrize("output", ["paste", "live-type"])
def test_allowlisted_editor_with_verified_target_may_use_direct_output(
    monkeypatch, app_id, wm_class, output
):
    observed = {}

    class FakeInsertion:
        def finalize(self, text, revision):
            return result(
                InsertionState.DISPATCHED_UNCONFIRMED,
                output,
                revision=revision,
            )

        def close(self):
            return None

    def fake_factory(selected_output, target_kind, **kwargs):
        observed.update(
            output=selected_output,
            target_kind=target_kind,
            target_token=kwargs["target_token"],
            focus_guard=kwargs["focus_guard"],
        )
        return FakeInsertion()

    monkeypatch.setattr(repo_agent, "insertion_session_for", fake_factory)
    focus_checks = []
    captured = gnome_editor_context(app_id=app_id, wm_class=wm_class)
    monkeypatch.setattr(
        repo_agent,
        "live_focus_context",
        lambda: focus_checks.append(captured.window_id) or captured,
    )
    outcome = repo_agent.deliver_response(
        "bounded answer",
        output=output,
        target=captured,
        paste_keys="auto",
    )

    assert outcome.ok
    assert observed["output"] == output
    assert observed["target_kind"] == "unknown"
    assert observed["target_token"] == captured.window_id
    assert observed["focus_guard"]()
    assert focus_checks == [captured.window_id, captured.window_id]


@pytest.mark.parametrize(
    ("window_id", "focus_matches"),
    [
        ("", True),
        ("stale-window", False),
    ],
)
def test_allowlisted_editor_without_verified_stable_target_degrades_to_clipboard(
    monkeypatch, window_id, focus_matches
):
    observed = {}

    class FakeInsertion:
        def finalize(self, text, revision):
            return result(InsertionState.NON_INSERTING, "clipboard", revision=revision)

        def close(self):
            return None

    monkeypatch.setattr(
        repo_agent,
        "insertion_session_for",
        lambda output, target_kind, **kwargs: (
            observed.update(output=output, target_kind=target_kind) or FakeInsertion()
        ),
    )
    current = gnome_editor_context()
    monkeypatch.setattr(
        repo_agent,
        "live_focus_context",
        lambda: current if focus_matches else TargetContext(),
    )

    outcome = repo_agent.deliver_response(
        "bounded answer",
        output="paste",
        target=TargetContext(
            kind="unknown",
            app_id="org.gnome.TextEditor",
            window_id=window_id,
        ),
        paste_keys="auto",
    )

    assert outcome.ok
    assert observed == {"output": "clipboard", "target_kind": "unknown"}


@pytest.mark.parametrize("output", ["stdout", "clipboard", "overlay"])
def test_non_direct_output_modes_are_preserved(monkeypatch, output):
    observed = {}

    class FakeInsertion:
        def finalize(self, text, revision):
            return result(InsertionState.NON_INSERTING, output, revision=revision)

        def close(self):
            return None

    monkeypatch.setattr(
        repo_agent,
        "insertion_session_for",
        lambda selected_output, target_kind, **kwargs: (
            observed.update(output=selected_output, target_kind=target_kind)
            or FakeInsertion()
        ),
    )
    monkeypatch.setattr(
        repo_agent,
        "live_focus_context",
        lambda: pytest.fail("non-direct output must not be focus-probed early"),
    )

    outcome = repo_agent.deliver_response(
        "bounded answer",
        output=output,
        target=TargetContext(kind="terminal", app_id="org.gnome.Console"),
        paste_keys="auto",
    )

    assert outcome.ok
    assert observed == {"output": output, "target_kind": "terminal"}


def test_response_speaker_passes_answer_on_stdin(monkeypatch, tmp_path):
    launcher = tmp_path / "say-read"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o700)
    process = FakeProcess(stdout="", returncode=0)
    calls = []
    monkeypatch.setattr(
        repo_agent.subprocess,
        "Popen",
        lambda argv, **kwargs: (
            setattr(process, "output_handle", kwargs.get("stdout")),
            calls.append((argv, kwargs)),
            process,
        )[-1],
    )
    speaker = repo_agent.ResponseSpeaker(launcher, timeout_seconds=7)
    secret = "private agent answer"
    assert speaker.speak(secret)
    assert calls[0][0] == [str(launcher), "-"]
    assert secret not in repr(calls[0][0])
    assert process.communicate_input == secret
    assert process.communicate_timeout == 7


def test_response_speaker_interrupt_cleans_up_then_reraises(monkeypatch, tmp_path):
    launcher = tmp_path / "say-read"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o700)
    process = FakeProcess(stdout="", interrupt=True)
    monkeypatch.setattr(repo_agent.subprocess, "Popen", lambda *args, **kwargs: process)
    signals = []
    monkeypatch.setattr(
        repo_agent.os,
        "killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )

    with pytest.raises(KeyboardInterrupt):
        repo_agent.ResponseSpeaker(launcher).speak("answer")

    assert signals == [
        (process.pid, signal.SIGTERM),
        (process.pid, signal.SIGKILL),
    ]


def test_capability_check_never_runs_codex(monkeypatch, tmp_path, capsys):
    codex = tmp_path / "codex"
    codex.write_text("#!/bin/sh\n", encoding="utf-8")
    codex.chmod(0o700)
    monkeypatch.setattr(repo_agent.shutil, "which", lambda name: str(codex))
    monkeypatch.setattr(repo_agent, "resolve_repository", lambda value: tmp_path)
    monkeypatch.setattr(
        repo_agent.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("--check must not contact Codex"),
    )
    args = argparse.Namespace(
        repo=str(tmp_path),
        timeout=None,
        max_request_chars=100,
        max_response_chars=100,
        agent_model="",
        output="stdout",
        dictate=False,
    )
    assert repo_agent.capability_check(args, tmp_path) == 0
    assert "Sandbox: read-only" in capsys.readouterr().out


def test_main_propagates_safe_delivery_failure_without_speaking(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_agent, "resolve_repository", lambda value: tmp_path)
    monkeypatch.setattr(repo_agent, "detect_target", lambda profile: TargetContext())
    monkeypatch.setattr(
        repo_agent, "read_request", lambda args, repository: (True, "question")
    )
    monkeypatch.setattr(
        repo_agent.CodexExecAdapter,
        "run",
        lambda self, request, repository: repo_agent.AgentRunResult(True, "answer"),
    )
    monkeypatch.setattr(
        repo_agent,
        "deliver_response",
        lambda *args, **kwargs: repo_agent.DeliveryResult(False, "paste", "rejected"),
    )
    monkeypatch.setattr(
        repo_agent.ResponseSpeaker,
        "speak",
        lambda *args, **kwargs: pytest.fail(
            "speech must not run after delivery failure"
        ),
    )
    assert repo_agent.main(["--repo", str(tmp_path), "--speak"]) == 1


def test_cli_limits_are_clamped_before_reading(monkeypatch, tmp_path):
    monkeypatch.setattr(repo_agent, "resolve_repository", lambda value: tmp_path)
    monkeypatch.setattr(repo_agent, "detect_target", lambda profile: TargetContext())
    observed = {}

    def fake_read(args, repository):
        observed["request"] = args.max_request_chars
        observed["response"] = args.max_response_chars
        return False, ""

    monkeypatch.setattr(repo_agent, "read_request", fake_read)
    assert (
        repo_agent.main(
            [
                "--repo",
                str(tmp_path),
                "--max-request-chars",
                "-5",
                "--max-response-chars",
                "999999999",
            ]
        )
        == 1
    )
    assert observed == {"request": 1, "response": repo_agent.ABSOLUTE_MAX_TEXT_CHARS}
