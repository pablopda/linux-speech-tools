#!/usr/bin/env python3
"""IBus Stage 0 capability diagnostics.

This module deliberately does not implement an input engine or insert text.  It
uses the project interpreter only as a controller and invokes a distro/system
Python in isolated mode because PyGObject and the IBus typelib are commonly
provided by the operating system rather than the uv environment.

The child never registers a component or engine, selects an input source,
starts ibus-daemon, requests surrounding text, or commits text. A former
transient-registration experiment was removed after it changed the selected
engine during live validation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import signal
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence


SCHEMA_VERSION = 1
DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0
MIN_PROBE_TIMEOUT_SECONDS = 0.1
MAX_PROBE_TIMEOUT_SECONDS = 30.0
MAX_PROBE_OUTPUT_BYTES = 64 * 1024
_RESULT_PREFIX = "LST_IBUS_STAGE0_RESULT="
_CHILD_RESULT_KEYS = {
    "schema_version",
    "system_python",
    "python_available",
    "python_version",
    "gi_available",
    "ibus_typelib_available",
    "session_bus_reachable",
    "ibus_bus_reachable",
    "registration_requested",
    "registration_attempted",
    "registration_ok",
    "shutdown_ok",
    "timed_out",
    "failure_stage",
    "failure_type",
}
_CHILD_FAILURE_STAGES = {
    "gi-import",
    "ibus-typelib",
    "session-bus",
    "ibus-bus",
    "ibus-shutdown",
}
_CHILD_FAILURE_TYPES = {
    "AttributeError",
    "Error",
    "GError",
    "ImportError",
    "ModuleNotFoundError",
    "OSError",
    "RepositoryError",
    "RuntimeError",
    "TypeError",
    "ValueError",
}
_PYTHON_VERSION_RE = re.compile(
    r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[A-Za-z0-9.+-]{0,32})?$"
)


class ProbeOutputLimitExceeded(RuntimeError):
    """Raised after a child exceeds the fixed combined pipe-output limit."""


@dataclass(frozen=True)
class Stage0Result:
    """Privacy-minimized outcome of one isolated Stage 0 probe."""

    system_python: Optional[str]
    python_available: bool
    python_version: Optional[str] = None
    gi_available: bool = False
    ibus_typelib_available: bool = False
    session_bus_reachable: bool = False
    ibus_bus_reachable: bool = False
    registration_requested: bool = False
    registration_attempted: bool = False
    registration_ok: Optional[bool] = None
    shutdown_ok: bool = False
    timed_out: bool = False
    failure_stage: Optional[str] = None
    failure_type: Optional[str] = None
    schema_version: int = SCHEMA_VERSION

    @property
    def ready(self) -> bool:
        base_ready = all(
            (
                self.python_available,
                self.gi_available,
                self.ibus_typelib_available,
                self.session_bus_reachable,
                self.ibus_bus_reachable,
                self.shutdown_ok,
            )
        ) and not self.timed_out
        if self.failure_stage is not None or self.failure_type is not None:
            return False
        if not base_ready:
            return False
        if not self.registration_requested:
            return True
        return self.registration_attempted and self.registration_ok is True

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["ready"] = self.ready
        data["actions"] = diagnostic_actions(self)
        return data


def _bounded_timeout(value: float) -> float:
    timeout = float(value)
    if not MIN_PROBE_TIMEOUT_SECONDS <= timeout <= MAX_PROBE_TIMEOUT_SECONDS:
        raise ValueError(
            "timeout must be between {:.1f} and {:.1f} seconds".format(
                MIN_PROBE_TIMEOUT_SECONDS, MAX_PROBE_TIMEOUT_SECONDS
            )
        )
    return timeout


def resolve_system_python(requested: Optional[str] = None) -> Optional[str]:
    """Resolve the distro Python without inheriting a project virtualenv."""
    if requested:
        if os.path.sep in requested:
            candidate = os.path.abspath(os.path.expanduser(requested))
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
            return None
        return shutil.which(requested)

    distro_python = Path("/usr/bin/python3")
    if distro_python.is_file() and os.access(str(distro_python), os.X_OK):
        return str(distro_python)
    return shutil.which("python3")


def _probe_environment() -> Dict[str, str]:
    """Keep graphical-session variables but remove Python environment overlays."""
    environment = dict(os.environ)
    for name in (
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT",
    ):
        environment.pop(name, None)
    return environment


# Independently authored from the Stage 0 behavioral requirements and official
# GI APIs. It intentionally has no engine descriptor and no text-bearing API.
_SYSTEM_PROBE = r"""
from __future__ import print_function

import json
import platform
import sys


PREFIX = "LST_IBUS_STAGE0_RESULT="
result = {
    "schema_version": 1,
    "system_python": sys.executable,
    "python_available": True,
    "python_version": platform.python_version(),
    "gi_available": False,
    "ibus_typelib_available": False,
    "session_bus_reachable": False,
    "ibus_bus_reachable": False,
    "registration_requested": False,
    "registration_attempted": False,
    "registration_ok": None,
    "shutdown_ok": True,
    "timed_out": False,
    "failure_stage": None,
    "failure_type": None,
}


def fail(stage, error):
    if result["failure_stage"] is None:
        result["failure_stage"] = stage
        result["failure_type"] = type(error).__name__


ibus_bus = None
try:
    try:
        import gi
        result["gi_available"] = True
    except Exception as error:
        fail("gi-import", error)
    if result["gi_available"]:
        try:
            gi.require_version("IBus", "1.0")
            from gi.repository import Gio, IBus
            result["ibus_typelib_available"] = True
        except Exception as error:
            fail("ibus-typelib", error)
    if result["ibus_typelib_available"]:
        try:
            session_connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            result["session_bus_reachable"] = bool(
                session_connection and not session_connection.is_closed()
            )
            if not result["session_bus_reachable"]:
                fail("session-bus", RuntimeError("session bus unavailable"))
        except Exception as error:
            fail("session-bus", error)
    if result["session_bus_reachable"]:
        try:
            IBus.init()
            ibus_bus = IBus.Bus.new()
            result["ibus_bus_reachable"] = bool(
                ibus_bus and ibus_bus.is_connected()
            )
            if not result["ibus_bus_reachable"]:
                fail("ibus-bus", RuntimeError("IBus bus unavailable"))
        except Exception as error:
            fail("ibus-bus", error)
finally:
    if ibus_bus is not None:
        try:
            ibus_bus.destroy()
        except Exception as error:
            result["shutdown_ok"] = False
            fail("ibus-shutdown", error)

print(PREFIX + json.dumps(result, sort_keys=True, separators=(",", ":")))
"""


def _unavailable_result(
    system_python: Optional[str],
    stage: str,
    *,
    registration_requested: bool,
    failure_type: Optional[str] = None,
    timed_out: bool = False,
) -> Stage0Result:
    return Stage0Result(
        system_python=system_python,
        python_available=system_python is not None,
        registration_requested=registration_requested,
        registration_ok=None,
        shutdown_ok=not timed_out,
        timed_out=timed_out,
        failure_stage=stage,
        failure_type=failure_type,
    )


def _result_from_payload(
    payload: Dict[str, Any], expected_python: str, registration_requested: bool
) -> Stage0Result:
    if registration_requested:
        raise ValueError("registration probing is disabled")
    if set(payload) != _CHILD_RESULT_KEYS:
        raise ValueError("invalid probe schema fields")
    schema_version = payload.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != SCHEMA_VERSION
    ):
        raise ValueError("unsupported probe schema")

    def required_bool(name: str) -> bool:
        value = payload.get(name)
        if not isinstance(value, bool):
            raise ValueError("invalid boolean field")
        return value

    registration_ok = payload.get("registration_ok")
    if registration_ok is not None and not isinstance(registration_ok, bool):
        raise ValueError("invalid registration result")
    if required_bool("registration_requested") != registration_requested:
        raise ValueError("registration mode mismatch")
    registration_attempted = required_bool("registration_attempted")
    if registration_attempted and not registration_requested:
        raise ValueError("unexpected registration attempt")
    if registration_attempted != isinstance(registration_ok, bool):
        raise ValueError("registration result state mismatch")
    python_available = required_bool("python_available")
    gi_available = required_bool("gi_available")
    ibus_typelib_available = required_bool("ibus_typelib_available")
    session_bus_reachable = required_bool("session_bus_reachable")
    ibus_bus_reachable = required_bool("ibus_bus_reachable")
    shutdown_ok = required_bool("shutdown_ok")
    timed_out = required_bool("timed_out")

    child_python = payload.get("system_python")
    python_version = payload.get("python_version")
    failure_stage = payload.get("failure_stage")
    failure_type = payload.get("failure_type")
    if not isinstance(child_python, str) or not child_python or len(child_python) > 4096:
        raise ValueError("invalid child interpreter field")
    if (
        not isinstance(python_version, str)
        or len(python_version) > 64
        or _PYTHON_VERSION_RE.fullmatch(python_version) is None
    ):
        raise ValueError("invalid Python version field")
    if failure_stage is not None and failure_stage not in _CHILD_FAILURE_STAGES:
        raise ValueError("invalid failure stage")
    if failure_type is not None and failure_type not in _CHILD_FAILURE_TYPES:
        raise ValueError("invalid failure type")
    if (failure_stage is None) != (failure_type is None):
        raise ValueError("incomplete failure result")
    if not python_available or timed_out:
        raise ValueError("invalid child execution state")
    if ibus_typelib_available and not gi_available:
        raise ValueError("typelib without GI")
    if session_bus_reachable and not ibus_typelib_available:
        raise ValueError("session bus without IBus typelib")
    if ibus_bus_reachable and not session_bus_reachable:
        raise ValueError("IBus bus without session bus")
    if registration_attempted and not ibus_bus_reachable:
        raise ValueError("registration attempted without IBus bus")

    expected_failure_stage: Optional[str]
    if not gi_available:
        expected_failure_stage = "gi-import"
    elif not ibus_typelib_available:
        expected_failure_stage = "ibus-typelib"
    elif not session_bus_reachable:
        expected_failure_stage = "session-bus"
    elif not ibus_bus_reachable:
        expected_failure_stage = "ibus-bus"
    elif not shutdown_ok:
        expected_failure_stage = "ibus-shutdown"
    else:
        expected_failure_stage = None
    if failure_stage != expected_failure_stage:
        raise ValueError("failure stage is inconsistent with capabilities")

    return Stage0Result(
        system_python=expected_python,
        python_available=python_available,
        python_version=python_version,
        gi_available=gi_available,
        ibus_typelib_available=ibus_typelib_available,
        session_bus_reachable=session_bus_reachable,
        ibus_bus_reachable=ibus_bus_reachable,
        registration_requested=registration_requested,
        registration_attempted=registration_attempted,
        registration_ok=registration_ok,
        shutdown_ok=shutdown_ok,
        timed_out=timed_out,
        failure_stage=failure_stage,
        failure_type=failure_type,
    )


def _parse_probe_output(
    stdout: str, expected_python: str, registration_requested: bool
) -> Stage0Result:
    if len(stdout.encode("utf-8", errors="replace")) > MAX_PROBE_OUTPUT_BYTES:
        raise ValueError("probe output exceeded limit")
    payload_lines = [
        line[len(_RESULT_PREFIX) :]
        for line in stdout.splitlines()
        if line.startswith(_RESULT_PREFIX)
    ]
    if len(payload_lines) != 1:
        raise ValueError("probe result missing")
    payload = json.loads(payload_lines[0])
    if not isinstance(payload, dict):
        raise ValueError("probe result must be an object")
    return _result_from_payload(payload, expected_python, registration_requested)


def _kill_and_reap(process: subprocess.Popen) -> None:
    # _run_bounded_child always creates a fresh session, so process.pid is the
    # identifier of a process group owned exclusively by this probe.  Signal
    # that group even when its leader has already exited: descendants can keep
    # the captured pipes open after the leader is gone.  Using only this Popen-
    # assigned group ID, immediately within the owned child lifecycle, also
    # minimizes any process-ID reuse window.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        # Popen.send_signal rechecks the leader state before signaling, which
        # avoids targeting a reused leader PID if group signaling was rejected.
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=1.0)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_bounded_child(
    command: Sequence[str], *, timeout: float, env: Dict[str, str]
) -> subprocess.CompletedProcess:
    """Drain both child pipes under one deadline and a fixed memory bound."""
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        bufsize=0,
        close_fds=True,
        start_new_session=True,
    )
    if process.stdout is None or process.stderr is None:
        _kill_and_reap(process)
        raise OSError("probe pipes were not created")

    selector = selectors.DefaultSelector()
    retained_stdout = bytearray()
    total_output = 0
    streams = (process.stdout, process.stderr)
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream.fileno(), selectors.EVENT_READ, stream)

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            events = selector.select(remaining)
            if not events:
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _mask in events:
                try:
                    chunk = os.read(key.fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fd)
                    key.data.close()
                    continue
                total_output += len(chunk)
                if total_output > MAX_PROBE_OUTPUT_BYTES:
                    raise ProbeOutputLimitExceeded(
                        "probe output exceeded fixed limit"
                    )
                if key.data is process.stdout:
                    retained_stdout.extend(chunk)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        returncode = process.wait(timeout=remaining)
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=returncode,
            stdout=retained_stdout.decode("utf-8", errors="replace"),
            stderr="",
        )
    except BaseException:
        _kill_and_reap(process)
        raise
    finally:
        selector.close()
        for stream in streams:
            try:
                stream.close()
            except OSError:
                pass


def _completed_output_exceeds_limit(completed: subprocess.CompletedProcess) -> bool:
    total = 0
    for value in (completed.stdout, completed.stderr):
        if isinstance(value, bytes):
            total += len(value)
        elif value is not None:
            total += len(str(value).encode("utf-8", errors="replace"))
        if total > MAX_PROBE_OUTPUT_BYTES:
            return True
    return False


def run_stage0_probe(
    *,
    system_python: Optional[str] = None,
    registration_probe: bool = False,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    command_runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    python_resolver: Optional[Callable[[Optional[str]], Optional[str]]] = None,
) -> Stage0Result:
    """Run a bounded, non-inserting system-Python capability probe."""
    bounded_timeout = _bounded_timeout(timeout)
    if registration_probe:
        # Compatibility guard for callers of the pre-release Python API. The
        # public CLI no longer exposes this mode, and no child is resolved or
        # started: live validation proved that even empty component
        # registration can change the selected IBus engine.
        return Stage0Result(
            system_python=None,
            python_available=False,
            registration_requested=True,
            registration_attempted=False,
            registration_ok=None,
            shutdown_ok=True,
            failure_stage="registration-probe-unsafe",
            failure_type="UnsupportedOperation",
        )
    resolver = python_resolver or resolve_system_python
    executable = resolver(system_python)
    if executable is None:
        return _unavailable_result(
            None,
            "system-python",
            registration_requested=registration_probe,
        )

    command = [executable, "-I", "-c", _SYSTEM_PROBE]
    try:
        environment = _probe_environment()
        if command_runner is None:
            completed = _run_bounded_child(
                command, timeout=bounded_timeout, env=environment
            )
        else:
            completed = command_runner(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=bounded_timeout,
                env=environment,
            )
    except subprocess.TimeoutExpired:
        return _unavailable_result(
            executable,
            "probe-timeout",
            registration_requested=registration_probe,
            failure_type="TimeoutExpired",
            timed_out=True,
        )
    except ProbeOutputLimitExceeded:
        return _unavailable_result(
            executable,
            "probe-result",
            registration_requested=registration_probe,
            failure_type="OutputLimitExceeded",
        )
    except OSError as exc:
        return _unavailable_result(
            executable,
            "probe-start",
            registration_requested=registration_probe,
            failure_type=type(exc).__name__,
        )

    if _completed_output_exceeds_limit(completed):
        return _unavailable_result(
            executable,
            "probe-result",
            registration_requested=registration_probe,
            failure_type="OutputLimitExceeded",
        )

    if completed.returncode != 0:
        return _unavailable_result(
            executable,
            "probe-process",
            registration_requested=registration_probe,
            failure_type="NonzeroExit",
        )
    try:
        stdout = completed.stdout
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return _parse_probe_output(stdout, executable, registration_probe)
    except (TypeError, ValueError, RecursionError, json.JSONDecodeError):
        return _unavailable_result(
            executable,
            "probe-result",
            registration_requested=registration_probe,
            failure_type="InvalidResult",
        )


def diagnostic_actions(result: Stage0Result) -> List[str]:
    """Return stable actionable guidance without echoing child errors."""
    actions: List[str] = []
    if result.failure_stage == "registration-probe-unsafe":
        actions.append(
            "Transient component registration is disabled because live "
            "validation changed the selected IBus engine; use the "
            "connection-only Stage 0 check."
        )
        return actions
    if not result.python_available:
        actions.append("Install a distro-provided Python 3 interpreter.")
        return actions
    if not result.gi_available:
        actions.append(
            "Install the distro package providing PyGObject for Python 3 "
            "(for example python3-gi on Debian/Ubuntu)."
        )
    elif not result.ibus_typelib_available:
        actions.append(
            "Install the IBus GObject-introspection typelib "
            "(for example gir1.2-ibus-1.0 on Debian/Ubuntu)."
        )
    if (
        result.gi_available
        and result.ibus_typelib_available
        and not result.session_bus_reachable
    ):
        actions.append("Run the check inside the graphical user session.")
    elif result.session_bus_reachable and not result.ibus_bus_reachable:
        actions.append(
            "Ensure IBus is installed and managed by the graphical session; "
            "this check will not start or replace ibus-daemon."
        )
    if not result.shutdown_ok:
        actions.append("The probe did not shut down cleanly within its bound.")
    return actions


def format_human_report(result: Stage0Result) -> str:
    def mark(value: bool) -> str:
        return "yes" if value else "no"

    lines = [
        "IBus Stage 0 diagnostics",
        "  Ready: {}".format(mark(result.ready)),
        "  System Python: {}".format(result.system_python or "unavailable"),
        "  Python version: {}".format(result.python_version or "unavailable"),
        "  PyGObject GI: {}".format(mark(result.gi_available)),
        "  IBus typelib: {}".format(mark(result.ibus_typelib_available)),
        "  Session D-Bus: {}".format(mark(result.session_bus_reachable)),
        "  IBus session bus: {}".format(mark(result.ibus_bus_reachable)),
        "  Clean shutdown: {}".format(mark(result.shutdown_ok)),
    ]
    if result.registration_requested:
        lines.append("  Transient registration: disabled (unsafe)")
    if result.failure_stage:
        lines.append("  Failure stage: {}".format(result.failure_stage))
    for action in diagnostic_actions(result):
        lines.append("  Action: {}".format(action))
    lines.extend(
        [
            "  Input sources changed: no",
            "  Text insertion attempted: no",
        ]
    )
    return "\n".join(lines)


def _timeout_argument(value: str) -> float:
    try:
        return _bounded_timeout(float(value))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Probe system-Python GI/IBus and user-session bus availability "
            "without inserting text or changing input sources."
        )
    )
    parser.add_argument(
        "--system-python",
        help="explicit distro Python executable (default: /usr/bin/python3)",
    )
    parser.add_argument(
        "--timeout",
        type=_timeout_argument,
        default=DEFAULT_PROBE_TIMEOUT_SECONDS,
        help="overall child startup/probe/shutdown bound in seconds (default: 5)",
    )
    parser.add_argument("--json", action="store_true", help="emit structured JSON")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_stage0_probe(
        system_python=args.system_python,
        timeout=args.timeout,
    )
    if args.json:
        print(json.dumps(result.to_dict(), sort_keys=True))
    else:
        print(format_human_report(result))
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
