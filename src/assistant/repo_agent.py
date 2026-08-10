#!/usr/bin/env python3
"""Read-only repository-aware developer assistant backed by Codex CLI."""

from __future__ import annotations

import argparse
import math
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from src.stt.asr_engine import add_engine_argument, resolve_engine
from src.stt.prompt_delivery import insertion_session_for
from src.stt.prompt_dictation import developer_hints
from src.stt.runtime import normalize_vad_aggressiveness
from src.stt.target_context import (
    GNOME_FOCUS_SCHEMA_VERSION,
    TargetContext,
    detect_target,
    live_focus_context,
)


DEFAULT_AGENT_TIMEOUT_SECONDS = 180.0
MAX_AGENT_TIMEOUT_SECONDS = 900.0
DEFAULT_SPEAK_TIMEOUT_SECONDS = 300.0
MAX_SPEAK_TIMEOUT_SECONDS = 900.0
DEFAULT_MAX_REQUEST_CHARS = 32_000
DEFAULT_MAX_RESPONSE_CHARS = 16_000
ABSOLUTE_MAX_TEXT_CHARS = 128_000
PROCESS_STOP_GRACE_SECONDS = 2.0
OUTPUT_READ_CHUNK_BYTES = 64 * 1024
MAX_UTF8_BYTES_PER_CHARACTER = 4

# Agent output is untrusted, and a window-level identity cannot distinguish an
# IDE's document editor from its integrated terminal.  Keep direct insertion
# deliberately narrower than the general prompt-dictation policy: these are
# exact application identities for dedicated document editors with no command
# surface.  Titles are intentionally never considered.
DIRECT_TEXT_EDITOR_IDENTITIES = frozenset(
    {
        "gnome-text-editor",
        "org.gnome.texteditor",
        "org.gnome.texteditor.desktop",
    }
)
DIRECT_TEXT_EDITOR_TARGET_KIND = "unknown"


def _bounded_float(
    value: object, default: float, *, minimum: float, maximum: float
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return min(maximum, max(minimum, parsed))


def _bounded_int(value: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, parsed))


@dataclass(frozen=True)
class AgentRunResult:
    ok: bool
    text: str = ""
    diagnostic: str = ""
    truncated: bool = False


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    backend: str
    state: str
    diagnostic: str = ""


def sanitize_agent_text(value: str) -> str:
    """Remove terminal controls and invisible formatting from untrusted output."""
    normalized = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    safe = []
    for character in normalized:
        if character == "\n":
            safe.append(character)
        elif character == "\t":
            safe.append("    ")
        elif not unicodedata.category(character).startswith("C"):
            safe.append(character)
    return "".join(safe).strip()


class CodexExecAdapter:
    """Invoke one ephemeral, read-only Codex run with bounded output."""

    def __init__(
        self,
        *,
        executable: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        max_request_chars: Optional[int] = None,
        max_response_chars: Optional[int] = None,
        model: str = "",
    ) -> None:
        self.executable = executable or shutil.which("codex") or ""
        self.timeout_seconds = _bounded_float(
            timeout_seconds
            if timeout_seconds is not None
            else os.environ.get(
                "LST_AGENT_TIMEOUT_SECONDS", DEFAULT_AGENT_TIMEOUT_SECONDS
            ),
            DEFAULT_AGENT_TIMEOUT_SECONDS,
            minimum=1.0,
            maximum=MAX_AGENT_TIMEOUT_SECONDS,
        )
        self.max_request_chars = _bounded_int(
            max_request_chars
            if max_request_chars is not None
            else os.environ.get(
                "LST_AGENT_MAX_REQUEST_CHARS", DEFAULT_MAX_REQUEST_CHARS
            ),
            DEFAULT_MAX_REQUEST_CHARS,
            minimum=1,
            maximum=ABSOLUTE_MAX_TEXT_CHARS,
        )
        self.max_response_chars = _bounded_int(
            max_response_chars
            if max_response_chars is not None
            else os.environ.get(
                "LST_AGENT_MAX_RESPONSE_CHARS", DEFAULT_MAX_RESPONSE_CHARS
            ),
            DEFAULT_MAX_RESPONSE_CHARS,
            minimum=1,
            maximum=ABSOLUTE_MAX_TEXT_CHARS,
        )
        self.model = (model or os.environ.get("LST_AGENT_MODEL", "")).strip()

    @property
    def available(self) -> bool:
        return bool(
            self.executable
            and os.path.isfile(self.executable)
            and os.access(self.executable, os.X_OK)
        )

    def argv(self, repository: Path) -> List[str]:
        command = [
            self.executable,
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
            str(repository),
        ]
        if self.model:
            command.extend(["--model", self.model])
        command.append("-")
        return command

    def run(self, request: str, repository: Path) -> AgentRunResult:
        request = (request or "").strip()
        if not request:
            return AgentRunResult(False, diagnostic="empty request")
        if len(request) > self.max_request_chars:
            return AgentRunResult(False, diagnostic="request exceeds configured limit")
        if not self.available:
            return AgentRunResult(False, diagnostic="codex executable is unavailable")

        try:
            repo = resolve_repository(repository)
        except ValueError as exc:
            return AgentRunResult(False, diagnostic=str(exc))

        prompt = (
            "Answer the following developer request using this repository as context. "
            "This is a read-only run: do not modify files, do not send external "
            "messages, and do not perform state-changing actions. Give a concise, "
            "self-contained final answer.\n\nUser request:\n" + request + "\n"
        )
        # Codex emits progress to stderr and only its final answer to stdout.
        # Drain stdout incrementally, retaining only a bounded UTF-8 prefix, so
        # a faulty child cannot fill memory or a temporary filesystem.
        try:
            process = subprocess.Popen(
                self.argv(repo),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(repo),
                start_new_session=True,
            )
        except OSError:
            return AgentRunResult(False, diagnostic="codex could not be started")

        retained_byte_limit = (
            self.max_response_chars * MAX_UTF8_BYTES_PER_CHARACTER
            + MAX_UTF8_BYTES_PER_CHARACTER
        )
        try:
            raw_answer, byte_truncated = _communicate_bounded(
                process,
                prompt.encode("utf-8", errors="replace"),
                output_limit=retained_byte_limit,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            return AgentRunResult(False, diagnostic="codex run timed out")
        except (OSError, ValueError):
            _terminate_process_group(process)
            return AgentRunResult(False, diagnostic="codex run failed")
        except BaseException:
            # The child is in a separate session, so terminal Ctrl+C reaches
            # this process but not the owned child group. Always tear it down
            # before preserving KeyboardInterrupt/SystemExit semantics.
            _terminate_process_group(process)
            raise

        if process.returncode != 0:
            return AgentRunResult(False, diagnostic="codex run failed")
        answer = sanitize_agent_text(raw_answer.decode("utf-8", errors="replace"))
        if not answer:
            return AgentRunResult(False, diagnostic="codex returned no final answer")
        if byte_truncated or len(answer) > self.max_response_chars:
            suffix = "\n\n[response truncated]"
            if self.max_response_chars <= len(suffix):
                answer = suffix[-self.max_response_chars :]
            else:
                answer = (
                    answer[: self.max_response_chars - len(suffix)].rstrip() + suffix
                )
            return AgentRunResult(True, text=answer, truncated=True)
        return AgentRunResult(True, text=answer)


class SpeechPromptCollector:
    """Collect finalized utterances from the shared engine-agnostic STT session."""

    def __init__(
        self,
        *,
        repository: Path,
        engine: str,
        model_size: str,
        language: str,
        device: str,
        vad_aggressiveness: int,
        max_request_chars: int,
    ) -> None:
        from src.stt.session import FasterWhisperSession

        self.parts: List[str] = []
        self.max_request_chars = _bounded_int(
            max_request_chars,
            DEFAULT_MAX_REQUEST_CHARS,
            minimum=1,
            maximum=ABSOLUTE_MAX_TEXT_CHARS,
        )
        self._retained_chars = 0
        self.limit_exceeded = False
        self._previous_signal_handlers = {
            signum: signal.getsignal(signum)
            for signum in (signal.SIGINT, signal.SIGTERM)
        }
        target = TargetContext(kind="codex", confidence="explicit", source="assistant")
        hints = developer_hints(str(repository), "codex", target)
        try:
            self.session = FasterWhisperSession(
                model_size=model_size,
                language=language,
                device=device,
                engine=engine,
                vad_aggressiveness=vad_aggressiveness,
                mode="assistant-prompt",
                output_handler=self._accept_final,
                status_fields=lambda: {"assistant": "codex", "phase": "request"},
                on_listening=lambda: print("Listening...", end="\r", file=sys.stderr),
                on_recording=lambda: print("Recording...", end="\r", file=sys.stderr),
                on_processing=lambda: print("Processing...", end="\r", file=sys.stderr),
                initial_prompt=hints,
                hotwords=hints,
            )
        except BaseException:
            self._restore_signal_handlers()
            raise

    def _accept_final(self, text: str) -> bool:
        if self.limit_exceeded:
            return False
        text = (text or "").strip()
        if not text:
            return False
        additional_chars = len(text) + (1 if self.parts else 0)
        if self._retained_chars + additional_chars > self.max_request_chars:
            self.limit_exceeded = True
            self.parts.clear()
            self._retained_chars = 0
            # Stop capture promptly. The final return below remains false even
            # if the shared session reports an otherwise successful shutdown.
            session = getattr(self, "session", None)
            if session is not None:
                session.running = False
            return False
        self.parts.append(text)
        self._retained_chars += additional_chars
        return True

    def run(self) -> Tuple[bool, str]:
        print("Speak the repository request; stop with Ctrl+C.", file=sys.stderr)
        try:
            ok = self.session.run()
        finally:
            self._restore_signal_handlers()
        if self.limit_exceeded:
            return False, ""
        return bool(ok), " ".join(self.parts).strip()

    def _restore_signal_handlers(self) -> None:
        for signum, handler in self._previous_signal_handlers.items():
            signal.signal(signum, handler)


class ResponseSpeaker:
    """Pipe an answer to the existing reader without putting it in argv."""

    def __init__(self, launcher: Path, timeout_seconds: Optional[float] = None) -> None:
        self.launcher = launcher
        self.timeout_seconds = _bounded_float(
            timeout_seconds
            if timeout_seconds is not None
            else os.environ.get(
                "LST_AGENT_SPEAK_TIMEOUT_SECONDS", DEFAULT_SPEAK_TIMEOUT_SECONDS
            ),
            DEFAULT_SPEAK_TIMEOUT_SECONDS,
            minimum=1.0,
            maximum=MAX_SPEAK_TIMEOUT_SECONDS,
        )

    @property
    def available(self) -> bool:
        return self.launcher.is_file() and os.access(str(self.launcher), os.X_OK)

    def speak(self, text: str) -> bool:
        if not self.available or not text.strip():
            return False
        try:
            process = subprocess.Popen(
                [str(self.launcher), "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
        except OSError:
            return False
        try:
            process.communicate(input=text, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            return False
        except BaseException:
            _terminate_process_group(process)
            raise
        return process.returncode == 0


def _close_stream(stream: object) -> None:
    if stream is None:
        return
    try:
        stream.close()
    except OSError:
        pass


def _communicate_bounded(
    process: subprocess.Popen,
    input_data: bytes,
    *,
    output_limit: int,
    timeout: float,
) -> Tuple[bytes, bool]:
    """Write stdin and drain stdout without retaining more than ``output_limit``."""
    if output_limit < 1:
        raise ValueError("output limit must be positive")
    if process.stdin is None or process.stdout is None:
        raise ValueError("bounded communication requires stdin and stdout pipes")

    selector = selectors.DefaultSelector()
    stdin = process.stdin
    stdout = process.stdout
    input_view = memoryview(input_data)
    input_offset = 0
    retained = bytearray()
    truncated = False
    deadline = time.monotonic() + timeout

    try:
        os.set_blocking(stdin.fileno(), False)
        os.set_blocking(stdout.fileno(), False)
        selector.register(stdout, selectors.EVENT_READ, "stdout")
        if input_view:
            selector.register(stdin, selectors.EVENT_WRITE, "stdin")
        else:
            _close_stream(stdin)

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    getattr(process, "args", "codex"), timeout
                )
            events = selector.select(remaining)
            if not events:
                raise subprocess.TimeoutExpired(
                    getattr(process, "args", "codex"), timeout
                )
            for key, _mask in events:
                if key.data == "stdout":
                    try:
                        chunk = os.read(stdout.fileno(), OUTPUT_READ_CHUNK_BYTES)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stdout)
                        _close_stream(stdout)
                        continue
                    room = max(0, output_limit - len(retained))
                    if room:
                        retained.extend(chunk[:room])
                    if len(chunk) > room:
                        truncated = True
                    continue

                try:
                    written = os.write(
                        stdin.fileno(),
                        input_view[
                            input_offset : input_offset + OUTPUT_READ_CHUNK_BYTES
                        ],
                    )
                except BlockingIOError:
                    continue
                except BrokenPipeError:
                    written = 0
                    input_offset = len(input_view)
                else:
                    input_offset += written
                if input_offset >= len(input_view) or written == 0:
                    selector.unregister(stdin)
                    _close_stream(stdin)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(getattr(process, "args", "codex"), timeout)
        process.wait(timeout=remaining)
        return bytes(retained), truncated
    finally:
        selector.close()
        _close_stream(stdin)
        _close_stream(stdout)


def _terminate_process_group(process: subprocess.Popen) -> None:
    """Bounded, best-effort teardown for an owned child process group."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=PROCESS_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=PROCESS_STOP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def resolve_repository(value: Path) -> Path:
    """Resolve one existing Git worktree without accepting arbitrary directories."""
    try:
        candidate = value.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("repository path is unavailable")
    if not candidate.is_dir():
        raise ValueError("repository path is not a directory")
    try:
        completed = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("repository could not be validated")
    if completed.returncode != 0:
        raise ValueError("selected directory is not a Git repository")
    raw_root = completed.stdout.strip()
    if not raw_root:
        raise ValueError("repository root is unavailable")
    try:
        root = Path(raw_root).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("repository root is unavailable")
    if not root.is_dir():
        raise ValueError("repository root is invalid")
    return root


def read_request(args: argparse.Namespace, repository: Path) -> Tuple[bool, str]:
    if args.dictate:
        collector = SpeechPromptCollector(
            repository=repository,
            engine=resolve_engine(args.engine),
            model_size=args.asr_model,
            language=args.language,
            device=args.device,
            vad_aggressiveness=args.vad,
            max_request_chars=args.max_request_chars,
        )
        return collector.run()
    if args.request_file:
        path = Path(args.request_file).expanduser()
        descriptor = None
        try:
            flags = os.O_RDONLY | os.O_NONBLOCK
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(str(path), flags)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                return False, ""
            byte_limit = args.max_request_chars * MAX_UTF8_BYTES_PER_CHARACTER
            if metadata.st_size > byte_limit:
                return False, ""
            raw_parts = bytearray()
            while len(raw_parts) <= byte_limit:
                chunk = os.read(
                    descriptor,
                    min(
                        OUTPUT_READ_CHUNK_BYTES,
                        byte_limit + 1 - len(raw_parts),
                    ),
                )
                if not chunk:
                    break
                raw_parts.extend(chunk)
            raw = bytes(raw_parts)
            if len(raw) > byte_limit:
                return False, ""
            return True, raw.decode("utf-8")[: args.max_request_chars + 1]
        except (OSError, UnicodeError):
            return False, ""
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
    if not sys.stdin.isatty():
        try:
            return True, sys.stdin.read(args.max_request_chars + 1)
        except (OSError, UnicodeError):
            return False, ""
    try:
        print("Repository request: ", end="", flush=True)
        return True, sys.stdin.readline(args.max_request_chars + 1)
    except (EOFError, OSError, UnicodeError):
        return False, ""


def deliver_response(
    text: str,
    *,
    output: str,
    target: TargetContext,
    paste_keys: str,
) -> DeliveryResult:
    # A denylist cannot cover every terminal identifier or an IDE/browser's
    # embedded command surface.  Direct insertion of untrusted model output is
    # allowed only for exact, dedicated text-editor identities after the same
    # stable-focus check used by InsertionSession.  Everything else is copy-only.
    direct_output = output in {"paste", "live-type"}
    if direct_output and not _direct_insertion_allowed(target):
        output = "clipboard"
        direct_output = False
    focus_guard = (
        (lambda: _direct_focus_matches(target))
        if direct_output
        else (lambda: False)
    )
    session = insertion_session_for(
        output,
        target.kind,
        target_token=target.window_id,
        paste_keys=paste_keys,
        confidence=target.confidence,
        focus_guard=focus_guard,
    )
    try:
        insertion = session.finalize(text, 1)
        return DeliveryResult(
            bool(insertion),
            insertion.backend,
            insertion.state.value,
            insertion.diagnostic or "",
        )
    finally:
        session.close()


def _direct_insertion_allowed(target: TargetContext) -> bool:
    if not _is_authoritative_gnome_editor(target):
        return False
    return _direct_focus_matches(target)


def _is_authoritative_gnome_editor(target: TargetContext) -> bool:
    if target.kind != DIRECT_TEXT_EDITOR_TARGET_KIND:
        return False
    if target.source != "gnome-focus":
        return False
    if (
        isinstance(target.schema_version, bool)
        or not isinstance(target.schema_version, int)
        or target.schema_version != GNOME_FOCUS_SCHEMA_VERSION
    ):
        return False
    if not isinstance(target.shell_session_id, str) or not target.shell_session_id:
        return False
    if (
        isinstance(target.window_sequence, bool)
        or not isinstance(target.window_sequence, int)
        or target.window_sequence <= 0
    ):
        return False
    if (
        isinstance(target.focus_generation, bool)
        or not isinstance(target.focus_generation, int)
        or target.focus_generation < 0
    ):
        return False
    if target.locked is not False:
        return False
    if not isinstance(target.window_id, str) or target.window_id != (
        "gnome:{}:{}".format(target.shell_session_id, target.window_sequence)
    ):
        return False

    identities = []
    for value in (target.app_id, target.wm_class):
        if not isinstance(value, str) or value != value.strip():
            return False
        if not value:
            continue
        identities.append(value.casefold())
    return bool(identities) and all(
        identity in DIRECT_TEXT_EDITOR_IDENTITIES for identity in identities
    )


def _direct_focus_matches(expected: TargetContext) -> bool:
    if not _is_authoritative_gnome_editor(expected):
        return False
    try:
        current = live_focus_context()
    except Exception:
        return False
    if not _is_authoritative_gnome_editor(current):
        return False
    return (
        current.schema_version == expected.schema_version
        and current.shell_session_id == expected.shell_session_id
        and current.window_sequence == expected.window_sequence
        and current.focus_generation == expected.focus_generation
        and current.window_id == expected.window_id
        and current.app_id.casefold() == expected.app_id.casefold()
        and current.wm_class.casefold() == expected.wm_class.casefold()
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ask a read-only Codex agent about one Git repository"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check capabilities without contacting Codex",
    )
    parser.add_argument("--repo", default=os.environ.get("LST_AGENT_REPO", os.getcwd()))
    parser.add_argument(
        "--request-file", help="Read the request from a UTF-8 file instead of stdin"
    )
    parser.add_argument(
        "--dictate",
        action="store_true",
        help="Capture the request with the shared STT session",
    )
    parser.add_argument(
        "--output",
        default=os.environ.get("LST_AGENT_OUTPUT", "stdout"),
        choices=["stdout", "clipboard", "paste", "live-type", "overlay"],
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("LST_AGENT_PROFILE", "auto"),
        choices=["auto", "claude", "codex", "ide", "terminal", "generic"],
    )
    parser.add_argument(
        "--paste-keys",
        default=os.environ.get("PROMPT_DICTATION_PASTE_KEYS", "auto"),
        choices=["auto", "ctrl-v", "ctrl-shift-v", "shift-insert"],
    )
    parser.add_argument(
        "--speak", action="store_true", help="Also read the answer through say-read"
    )
    parser.add_argument("--agent-model", default=os.environ.get("LST_AGENT_MODEL", ""))
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument(
        "--max-request-chars",
        type=int,
        default=_bounded_int(
            os.environ.get("LST_AGENT_MAX_REQUEST_CHARS", DEFAULT_MAX_REQUEST_CHARS),
            DEFAULT_MAX_REQUEST_CHARS,
            minimum=1,
            maximum=ABSOLUTE_MAX_TEXT_CHARS,
        ),
    )
    parser.add_argument(
        "--max-response-chars",
        type=int,
        default=_bounded_int(
            os.environ.get("LST_AGENT_MAX_RESPONSE_CHARS", DEFAULT_MAX_RESPONSE_CHARS),
            DEFAULT_MAX_RESPONSE_CHARS,
            minimum=1,
            maximum=ABSOLUTE_MAX_TEXT_CHARS,
        ),
    )
    parser.add_argument("--asr-model", default=os.environ.get("WHISPER_MODEL", "tiny"))
    parser.add_argument("--language", default=os.environ.get("ASR_LANG", "en"))
    parser.add_argument(
        "--device",
        default=os.environ.get("WHISPER_DEVICE", "cpu"),
        choices=["cpu", "cuda"],
    )
    add_engine_argument(parser)
    parser.add_argument(
        "--vad",
        type=int,
        choices=[0, 1, 2, 3],
        default=normalize_vad_aggressiveness(os.environ.get("WHISPER_VAD", "2")),
    )
    return parser


def capability_check(args: argparse.Namespace, project_root: Path) -> int:
    adapter = CodexExecAdapter(
        timeout_seconds=args.timeout,
        max_request_chars=args.max_request_chars,
        max_response_chars=args.max_response_chars,
        model=args.agent_model,
    )
    try:
        resolve_repository(Path(args.repo))
        repository_status = "available"
    except ValueError:
        repository_status = "unavailable"
    say_read = project_root / "bin" / "say-read"
    print("Repository Assistant Check")
    print("  Adapter: codex-exec")
    print("  Codex CLI: {}".format("available" if adapter.available else "unavailable"))
    print("  Repository: {}".format(repository_status))
    print("  Sandbox: read-only")
    print("  Session persistence: disabled")
    print("  Default output: {}".format(args.output))
    print("  Speech request: {}".format("enabled" if args.dictate else "disabled"))
    print("  TTS: {}".format("available" if say_read.is_file() else "unavailable"))
    return 0 if adapter.available and repository_status == "available" else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.max_request_chars = _bounded_int(
        args.max_request_chars,
        DEFAULT_MAX_REQUEST_CHARS,
        minimum=1,
        maximum=ABSOLUTE_MAX_TEXT_CHARS,
    )
    args.max_response_chars = _bounded_int(
        args.max_response_chars,
        DEFAULT_MAX_RESPONSE_CHARS,
        minimum=1,
        maximum=ABSOLUTE_MAX_TEXT_CHARS,
    )
    project_root = Path(__file__).resolve().parents[2]
    if args.check:
        return capability_check(args, project_root)

    try:
        repository = resolve_repository(Path(args.repo))
    except ValueError as exc:
        print("Repository assistant unavailable: {}.".format(exc), file=sys.stderr)
        return 1

    # Capture identity before dictation and the potentially long agent call.
    target = detect_target(args.profile)
    request_ok, request = read_request(args, repository)
    request = (request or "").strip()
    if not request_ok or not request:
        print("Repository assistant did not receive a request.", file=sys.stderr)
        return 1
    if len(request) > args.max_request_chars:
        print(
            "Repository assistant request exceeds the configured limit.",
            file=sys.stderr,
        )
        return 1

    adapter = CodexExecAdapter(
        timeout_seconds=args.timeout,
        max_request_chars=args.max_request_chars,
        max_response_chars=args.max_response_chars,
        model=args.agent_model,
    )
    result = adapter.run(request, repository)
    if not result.ok:
        print(
            "Repository assistant failed: {}.".format(result.diagnostic),
            file=sys.stderr,
        )
        return 1

    delivered = deliver_response(
        result.text,
        output=args.output,
        target=target,
        paste_keys=args.paste_keys,
    )
    if not delivered.ok:
        print(
            "Repository assistant response was not delivered safely.", file=sys.stderr
        )
        return 1

    speak_ok = True
    if args.speak:
        speaker = ResponseSpeaker(project_root / "bin" / "say-read")
        speak_ok = speaker.speak(result.text)
        if not speak_ok:
            print(
                "Repository assistant response was delivered, but speech playback failed.",
                file=sys.stderr,
            )
    return 0 if speak_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
