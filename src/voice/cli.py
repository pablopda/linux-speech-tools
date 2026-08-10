"""Side-by-side command router for the Linux voice workspace.

This first CLI deliberately delegates Dictate, Ask, and Read to the existing
launchers.  It does not claim a daemon or duplicate their mature policy.  All
delegation uses argument vectors without a shell, and caller-provided text is
kept on stdin whenever the compatibility launcher supports it.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, TextIO, Tuple


EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3
EXIT_INTERRUPTED = 130

MAX_STDIN_TEXT_BYTES = 1024 * 1024
CHILD_STOP_GRACE_SECONDS = 2.0

_TERMINATION_SIGNALS = (
    signal.SIGHUP,
    signal.SIGINT,
    signal.SIGTERM,
    signal.SIGQUIT,
)

_DELEGATE_NAMES = frozenset(
    (
        "lst-agent",
        "lst-dictate",
        "lst-ibus-check",
        "say",
        "say-read",
        "talk2claude-faster",
    )
)

TOP_HELP = """usage: lst COMMAND [options]

Linux voice workspace (side-by-side compatibility foundation).

Commands:
  dictate    Dictate through the existing foreground or target-bound launcher
  ask        Ask the existing read-only Codex repository assistant
  act        Reserved for the future proposal/approval workflow (unavailable)
  read       Read text, stdin, a file, document, or URL through existing TTS
  status     Show bounded, content-free standalone status
  stop       Reserved until sessions share one safe manager (unavailable)
  doctor     Show bounded launcher readiness or run the IBus diagnostic

This release runs in-process or through compatibility launchers. No daemon is
started, discovered, or contacted yet.

Run "lst COMMAND --help" for command-specific usage.
"""

DICTATE_HELP = """usage: lst dictate [toggle|prompt|start] [legacy options]

Without a subcommand, delegates to talk2claude-faster for foreground,
clipboard-first compatibility. toggle/prompt/start delegate exactly to
lst-dictate. No daemon is used.
"""

ASK_HELP = """usage: lst ask [--agent codex] [--context repository]
               [--text TEXT | --dictate] [lst-agent options]

Delegates to the existing read-only lst-agent. --text is delivered on stdin,
not argv. Piped stdin is inherited when --text is omitted. Only the Codex
repository adapter is available in this compatibility foundation.
"""

ACT_HELP = """usage: lst act [request]

Act is reserved for the proposal and visible-approval workflow. It is
unavailable in this side-by-side foundation and never executes a child.
"""

READ_HELP = """usage: lst read [--tts kokoro|edge] [--text TEXT] [reader options]

kokoro (default) delegates to say-read; --text is passed as stdin using source
"-". edge delegates to say, whose compatibility interface requires text argv.
Without --text, remaining arguments are passed through unchanged.
"""

STATUS_HELP = """usage: lst status [--json|--plain]

Show bounded coarse status only. The daemon is explicitly not implemented.
No transcript, title, path, process identifier, or command line is reported.
"""

STOP_HELP = """usage: lst stop

Cross-mode stop requires the shared session manager. It is unavailable in this
side-by-side foundation and never toggles or starts a compatibility launcher.
"""

DOCTOR_HELP = """usage: lst doctor [--json|--plain]
       lst doctor ibus [lst-ibus-check options]

The default check reports only fixed launcher availability categories. The
ibus form delegates to the existing bounded, non-inserting diagnostic.
"""


Resolver = Callable[[str, Mapping[str, str]], Optional[str]]
Runner = Callable[[Sequence[str], Optional[str], Mapping[str, str], TextIO], int]


class _TerminationRequested(BaseException):
    def __init__(self, signum: int) -> None:
        self.signum = signum


def _install_termination_handlers() -> Mapping[int, Any]:
    """Turn terminal signals into bounded owned-process-group cleanup."""
    previous: Dict[int, Any] = {}

    def terminate(signum: int, _frame: Any) -> None:
        raise _TerminationRequested(signum)

    for signum in _TERMINATION_SIGNALS:
        try:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, terminate)
        except (ValueError, OSError):
            # signal.signal is limited to the main thread. The caller still
            # has KeyboardInterrupt and BaseException cleanup in embedded use.
            for installed, handler in previous.items():
                try:
                    signal.signal(installed, handler)
                except (ValueError, OSError):
                    pass
            return {}
    return previous


def _restore_termination_handlers(previous: Mapping[int, Any]) -> None:
    for signum, handler in previous.items():
        try:
            signal.signal(signum, handler)
        except (ValueError, OSError):
            pass


def _print_help(text: str, stream: TextIO) -> int:
    stream.write(text)
    if not text.endswith("\n"):
        stream.write("\n")
    return EXIT_OK


def _help_requested(arguments: Sequence[str]) -> bool:
    return bool(arguments and arguments[0] in ("-h", "--help", "help"))


def _usage_error(message: str, help_text: str, stderr: TextIO) -> int:
    stderr.write("lst: {}\n".format(message))
    stderr.write(help_text)
    if not help_text.endswith("\n"):
        stderr.write("\n")
    return EXIT_USAGE


def _unavailable(message: str, stderr: TextIO) -> int:
    stderr.write("lst: unavailable: {}\n".format(message))
    return EXIT_UNAVAILABLE


def _resolve_launcher(name: str, environ: Mapping[str, str]) -> Optional[str]:
    """Resolve one fixed launcher without accepting a command from the user."""
    if name not in _DELEGATE_NAMES:
        return None
    candidates = []
    launcher_dir = environ.get("LST_LAUNCHER_DIR", "")
    if launcher_dir:
        candidates.append(Path(launcher_dir) / name)
    project_root = environ.get("LST_PROJECT_ROOT", "")
    if project_root:
        candidates.append(Path(project_root) / "bin" / name)
    candidates.append(Path(__file__).resolve().parents[2] / "bin" / name)
    for candidate in candidates:
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return str(candidate)
    discovered = shutil.which(name, path=environ.get("PATH"))
    return discovered


def _normalize_returncode(returncode: int) -> int:
    if returncode < 0:
        return 128 + min(127, abs(returncode))
    return min(255, returncode)


def _signal_group(process: Any, signum: int) -> None:
    try:
        os.killpg(process.pid, signum)
    except (OSError, ProcessLookupError):
        pass


def _stop_interrupted_child(process: Any) -> None:
    _stop_signalled_child(process, signal.SIGINT)


def _stop_signalled_child(process: Any, first_signal: int) -> None:
    _signal_group(process, first_signal)
    try:
        process.wait(timeout=CHILD_STOP_GRACE_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        pass
    if first_signal != signal.SIGTERM:
        _signal_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=CHILD_STOP_GRACE_SECONDS)
        except (subprocess.TimeoutExpired, OSError):
            pass
    # The delegate leader may exit before children which inherited its
    # microphone/playback pipes.  Leader exit is therefore not sufficient
    # evidence that the exclusively owned process group has stopped.
    _signal_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=CHILD_STOP_GRACE_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        # The owned group has received SIGKILL.  Never wait without a bound.
        pass


def _run_child(
    argv: Sequence[str],
    stdin_text: Optional[str],
    environ: Mapping[str, str],
    stderr: TextIO,
) -> int:
    """Run one owned delegate and clean up its process group on interruption."""
    popen_kwargs = {
        "env": dict(environ),
        "start_new_session": True,
    }
    if stdin_text is not None:
        popen_kwargs.update(stdin=subprocess.PIPE, text=True)
    previous_handlers = {}  # type: Dict[int, Any]
    process = None
    previous_mask = None
    pthread_sigmask = getattr(signal, "pthread_sigmask", None)
    try:
        # Block the handled signals only across Popen.  Otherwise a signal can
        # arrive after the detached child exists but before Popen has returned
        # its handle, leaving no process group for the cleanup path to reap.
        if pthread_sigmask is not None:
            previous_mask = pthread_sigmask(signal.SIG_BLOCK, _TERMINATION_SIGNALS)
        previous_handlers = dict(_install_termination_handlers())
        try:
            process = subprocess.Popen(list(argv), **popen_kwargs)
        finally:
            if previous_mask is not None:
                mask = previous_mask
                previous_mask = None
                pthread_sigmask(signal.SIG_SETMASK, mask)
        if stdin_text is None:
            return _normalize_returncode(process.wait())
        process.communicate(input=stdin_text)
        return _normalize_returncode(process.returncode)
    except OSError:
        if process is not None:
            _stop_interrupted_child(process)
            return _unavailable("compatibility launcher failed", stderr)
        return _unavailable("compatibility launcher could not be started", stderr)
    except KeyboardInterrupt:
        if process is not None:
            _stop_interrupted_child(process)
        return EXIT_INTERRUPTED
    except _TerminationRequested as interrupted:
        if process is not None:
            _stop_signalled_child(process, interrupted.signum)
        return 128 + interrupted.signum
    except BaseException:
        # SystemExit and unexpected runner failures must not orphan an owned
        # microphone, agent, or playback process group.
        if process is not None:
            _stop_interrupted_child(process)
        raise
    finally:
        if previous_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        restore_mask = None
        if previous_handlers and pthread_sigmask is not None:
            restore_mask = pthread_sigmask(signal.SIG_BLOCK, _TERMINATION_SIGNALS)
        try:
            _restore_termination_handlers(previous_handlers)
        finally:
            if restore_mask is not None:
                pthread_sigmask(signal.SIG_SETMASK, restore_mask)


def _delegate(
    name: str,
    arguments: Sequence[str],
    *,
    stdin_text: Optional[str],
    environ: Mapping[str, str],
    resolver: Resolver,
    runner: Runner,
    stderr: TextIO,
) -> int:
    launcher = resolver(name, environ)
    if launcher is None:
        return _unavailable("{} is not installed".format(name), stderr)
    return runner(tuple([launcher] + list(arguments)), stdin_text, environ, stderr)


def _bounded_text(value: str, stderr: TextIO, help_text: str) -> Optional[int]:
    if len(value.encode("utf-8")) > MAX_STDIN_TEXT_BYTES:
        return _usage_error("--text exceeds the 1 MiB limit", help_text, stderr)
    return None


def _option_value(
    arguments: Sequence[str], index: int, option: str, help_text: str, stderr: TextIO
) -> Tuple[Optional[str], int, Optional[int]]:
    token = arguments[index]
    prefix = option + "="
    if token.startswith(prefix):
        value = token[len(prefix) :]
        if not value:
            return (
                None,
                index + 1,
                _usage_error("{} requires a value".format(option), help_text, stderr),
            )
        return value, index + 1, None
    if index + 1 >= len(arguments):
        return (
            None,
            index + 1,
            _usage_error("{} requires a value".format(option), help_text, stderr),
        )
    return arguments[index + 1], index + 2, None


def _dictate(
    arguments: Sequence[str],
    *,
    environ: Mapping[str, str],
    resolver: Resolver,
    runner: Runner,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if _help_requested(arguments):
        return _print_help(DICTATE_HELP, stdout)
    forwarded = list(arguments)
    if forwarded and forwarded[0] in ("toggle", "prompt", "start"):
        return _delegate(
            "lst-dictate",
            forwarded,
            stdin_text=None,
            environ=environ,
            resolver=resolver,
            runner=runner,
            stderr=stderr,
        )
    return _delegate(
        "talk2claude-faster",
        forwarded,
        stdin_text=None,
        environ=environ,
        resolver=resolver,
        runner=runner,
        stderr=stderr,
    )


def _ask(
    arguments: Sequence[str],
    *,
    environ: Mapping[str, str],
    resolver: Resolver,
    runner: Runner,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if _help_requested(arguments):
        return _print_help(ASK_HELP, stdout)
    agent = "codex"
    context = "repository"
    stdin_text = None  # type: Optional[str]
    forwarded = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            forwarded.extend(arguments[index:])
            break
        matched = None  # type: Optional[str]
        for option in ("--agent", "--context", "--text"):
            if token == option or token.startswith(option + "="):
                matched = option
                break
        if matched is None:
            forwarded.append(token)
            index += 1
            continue
        value, index, failure = _option_value(
            arguments, index, matched, ASK_HELP, stderr
        )
        if failure is not None:
            return failure
        assert value is not None
        if matched == "--agent":
            agent = value
        elif matched == "--context":
            context = value
        else:
            if stdin_text is not None:
                return _usage_error("--text may be provided once", ASK_HELP, stderr)
            stdin_text = value
    if agent != "codex":
        return _unavailable("requested agent adapter is not installed", stderr)
    if context != "repository":
        return _unavailable("requested context provider is not installed", stderr)
    if stdin_text is not None:
        failure = _bounded_text(stdin_text, stderr, ASK_HELP)
        if failure is not None:
            return failure
        if "--dictate" in forwarded:
            return _usage_error(
                "--text and --dictate are mutually exclusive", ASK_HELP, stderr
            )
    return _delegate(
        "lst-agent",
        forwarded,
        stdin_text=stdin_text,
        environ=environ,
        resolver=resolver,
        runner=runner,
        stderr=stderr,
    )


def _read(
    arguments: Sequence[str],
    *,
    environ: Mapping[str, str],
    resolver: Resolver,
    runner: Runner,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if _help_requested(arguments):
        return _print_help(READ_HELP, stdout)
    tts = "kokoro"
    stdin_text = None  # type: Optional[str]
    forwarded = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--":
            forwarded.extend(arguments[index:])
            break
        matched = None  # type: Optional[str]
        for option in ("--tts", "--text"):
            if token == option or token.startswith(option + "="):
                matched = option
                break
        if matched is None:
            forwarded.append(token)
            index += 1
            continue
        value, index, failure = _option_value(
            arguments, index, matched, READ_HELP, stderr
        )
        if failure is not None:
            return failure
        assert value is not None
        if matched == "--tts":
            tts = value
        else:
            if stdin_text is not None:
                return _usage_error("--text may be provided once", READ_HELP, stderr)
            stdin_text = value
    if tts not in ("kokoro", "edge"):
        return _unavailable("requested TTS provider is not installed", stderr)
    if stdin_text is not None:
        failure = _bounded_text(stdin_text, stderr, READ_HELP)
        if failure is not None:
            return failure
    if tts == "edge":
        edge_arguments = list(forwarded)
        if stdin_text is not None:
            # The existing Edge launcher has no stdin contract.  This is the
            # one compatibility path where text must remain an argv value.
            edge_arguments.append(stdin_text)
        return _delegate(
            "say",
            edge_arguments,
            stdin_text=None,
            environ=environ,
            resolver=resolver,
            runner=runner,
            stderr=stderr,
        )
    if stdin_text is not None:
        forwarded.insert(0, "-")
    return _delegate(
        "say-read",
        forwarded,
        stdin_text=stdin_text,
        environ=environ,
        resolver=resolver,
        runner=runner,
        stderr=stderr,
    )


def _act(arguments: Sequence[str], *, stdout: TextIO, stderr: TextIO) -> int:
    if _help_requested(arguments):
        return _print_help(ACT_HELP, stdout)
    return _unavailable(
        "Act requires the proposal and visible-approval workflow; nothing was executed",
        stderr,
    )


def _status(arguments: Sequence[str], *, stdout: TextIO, stderr: TextIO) -> int:
    if _help_requested(arguments):
        return _print_help(STATUS_HELP, stdout)
    if len(arguments) > 1 or (arguments and arguments[0] not in ("--json", "--plain")):
        return _usage_error("invalid status option", STATUS_HELP, stderr)
    as_json = bool(arguments and arguments[0] == "--json")
    data = {
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
    if as_json:
        stdout.write(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n")
    else:
        stdout.write("host: standalone (daemon: not-implemented)\n")
        stdout.write(
            "dictate: delegated\nask: delegated\nact: unavailable\nread: delegated\n"
        )
    return EXIT_OK


def _stop(arguments: Sequence[str], *, stdout: TextIO, stderr: TextIO) -> int:
    if _help_requested(arguments):
        return _print_help(STOP_HELP, stdout)
    if arguments:
        return _usage_error("stop accepts no arguments", STOP_HELP, stderr)
    return _unavailable(
        "cross-mode stop requires the shared session manager; nothing was started",
        stderr,
    )


def _doctor(
    arguments: Sequence[str],
    *,
    environ: Mapping[str, str],
    resolver: Resolver,
    runner: Runner,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if _help_requested(arguments):
        return _print_help(DOCTOR_HELP, stdout)
    if arguments and arguments[0] == "ibus":
        return _delegate(
            "lst-ibus-check",
            arguments[1:],
            stdin_text=None,
            environ=environ,
            resolver=resolver,
            runner=runner,
            stderr=stderr,
        )
    if len(arguments) > 1 or (arguments and arguments[0] not in ("--json", "--plain")):
        return _usage_error("invalid doctor option", DOCTOR_HELP, stderr)
    as_json = bool(arguments and arguments[0] == "--json")
    checks = {
        "ask": resolver("lst-agent", environ) is not None,
        "dictate-foreground": resolver("talk2claude-faster", environ) is not None,
        "dictate-target-bound": resolver("lst-dictate", environ) is not None,
        "ibus-diagnostic": resolver("lst-ibus-check", environ) is not None,
        "read-edge": resolver("say", environ) is not None,
        "read-kokoro": resolver("say-read", environ) is not None,
    }
    core_ready = all(
        checks[name]
        for name in (
            "ask",
            "dictate-foreground",
            "dictate-target-bound",
            "read-kokoro",
        )
    )
    if as_json:
        payload = {
            "schema_version": 1,
            "host": "standalone",
            "daemon": "not-implemented",
            "ready": core_ready,
            "launchers": checks,
        }
        stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    else:
        stdout.write("host: standalone (daemon: not-implemented)\n")
        for name in sorted(checks):
            stdout.write(
                "{}: {}\n".format(name, "available" if checks[name] else "unavailable")
            )
    return EXIT_OK if core_ready else EXIT_UNAVAILABLE


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    resolver: Resolver = _resolve_launcher,
    runner: Runner = _run_child,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    """Run the bounded CLI without mutating global argv or environment."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    active_environment = dict(os.environ if environ is None else environ)
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    if not arguments:
        return _usage_error("a command is required", TOP_HELP, errors)
    if arguments[0] in ("-h", "--help", "help"):
        return _print_help(TOP_HELP, output)
    command = arguments.pop(0)
    handlers = {
        "dictate": _dictate,
        "ask": _ask,
        "read": _read,
        "doctor": _doctor,
    }
    if command == "act":
        return _act(arguments, stdout=output, stderr=errors)
    if command == "status":
        return _status(arguments, stdout=output, stderr=errors)
    if command == "stop":
        return _stop(arguments, stdout=output, stderr=errors)
    handler = handlers.get(command)
    if handler is None:
        return _usage_error("unknown command", TOP_HELP, errors)
    return handler(
        arguments,
        environ=active_environment,
        resolver=resolver,
        runner=runner,
        stdout=output,
        stderr=errors,
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
