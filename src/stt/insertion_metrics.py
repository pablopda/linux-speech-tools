#!/usr/bin/env python3
"""Private, transcript-free aggregate metrics for insertion reliability.

The persisted schema is deliberately closed: caller-provided strings are
mapped to fixed categories and diagnostics, session identifiers, target
tokens, titles, and transcript text are never accepted by the store.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
MAX_STATE_BYTES = 256 * 1024
MAX_DAYS = 120
MAX_COUNT = (1 << 63) - 1

BACKENDS = (
    "base",
    "clipboard",
    "synthetic-paste",
    "synthetic-live-type",
    "overlay",
    "stdout",
    "ydotool",
    "xdotool",
    "wl-copy",
    "xclip",
    "xsel",
    "other",
)
BACKEND_ALIASES = {
    "wayland_ydotool": "ydotool",
    "x11_xdotool": "xdotool",
}
DIRECT_ATTEMPT_BACKENDS = (
    "synthetic-paste",
    "synthetic-live-type",
)
RESULTS = (
    "confirmed-inserted",
    "dispatched-unconfirmed",
    "clipboard-fallback",
    "non-inserting",
    "failed-before-dispatch",
    "ambiguous-after-dispatch",
    "rejected",
    "unavailable",
    "stale",
    "cancelled",
    "other",
)
TARGET_KINDS = (
    "claude",
    "codex",
    "ide",
    "terminal",
    "browser",
    "generic",
    "unknown",
)
TARGET_RESULT_KEYS = tuple(
    "{}:{}".format(target, result_name)
    for target in TARGET_KINDS
    for result_name in RESULTS
)
DAY_KEYS = {
    "sessions",
    "backends",
    "attempted_backends",
    "results",
    "target_kinds",
    "eligible_direct_attempts",
    "eligible_results",
    "eligible_target_results",
    "focus_drift",
    "clipboard_fallback",
    "unicode_failures",
    "spanish_punctuation_failures",
}
TOP_KEYS = {
    "schema_version",
    "enabled",
    "period_started",
    "updated_on",
    "days",
}
FALSE_VALUES = {"0", "false", "no", "off"}


class UnsafeMetricsPath(OSError):
    """Raised when a metrics path is not a private, owned regular file."""


def default_state_path() -> Path:
    """Return a persistent per-user state path, not the ephemeral runtime dir."""
    override = os.environ.get("LST_INSERTION_METRICS_FILE", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("LST_INSERTION_METRICS_FILE must be an absolute path")
        return path
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    if base:
        state_home = Path(base).expanduser()
        if state_home.is_absolute():
            return state_home / "linux-speech-tools" / "insertion-metrics-v1.json"
    return Path.home() / ".local" / "state" / "linux-speech-tools" / "insertion-metrics-v1.json"


def _today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def _empty_state(*, enabled: bool = True) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": enabled,
        "period_started": None,
        "updated_on": None,
        "days": {},
    }


def _empty_day() -> Dict[str, Any]:
    return {
        "sessions": 0,
        "backends": {},
        "attempted_backends": {},
        "results": {},
        "target_kinds": {},
        "eligible_direct_attempts": 0,
        "eligible_results": {},
        "eligible_target_results": {},
        "focus_drift": 0,
        "clipboard_fallback": 0,
        "unicode_failures": 0,
        "spanish_punctuation_failures": 0,
    }


def _is_count(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_COUNT
    )


def _valid_date(value: Any, *, nullable: bool = False) -> bool:
    if value is None:
        return nullable
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_counter_map(value: Any, allowed: Sequence[str]) -> bool:
    return (
        isinstance(value, dict)
        and set(value).issubset(set(allowed))
        and all(_is_count(count) for count in value.values())
    )


def _validated_state(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != TOP_KEYS:
        return None
    if value.get("schema_version") != SCHEMA_VERSION:
        return None
    if not isinstance(value.get("enabled"), bool):
        return None
    if not _valid_date(value.get("period_started"), nullable=True):
        return None
    if not _valid_date(value.get("updated_on"), nullable=True):
        return None
    days = value.get("days")
    if not isinstance(days, dict) or len(days) > MAX_DAYS:
        return None
    for day_name, bucket in days.items():
        if not _valid_date(day_name) or not isinstance(bucket, dict):
            return None
        if set(bucket) != DAY_KEYS:
            return None
        if not _is_count(bucket["sessions"]):
            return None
        if not _valid_counter_map(bucket["backends"], BACKENDS):
            return None
        if not _valid_counter_map(bucket["attempted_backends"], BACKENDS):
            return None
        if not _valid_counter_map(bucket["results"], RESULTS):
            return None
        if not _valid_counter_map(bucket["target_kinds"], TARGET_KINDS):
            return None
        if not _is_count(bucket["eligible_direct_attempts"]):
            return None
        if not _valid_counter_map(bucket["eligible_results"], RESULTS):
            return None
        if not _valid_counter_map(
            bucket["eligible_target_results"], TARGET_RESULT_KEYS
        ):
            return None
        if sum(bucket["backends"].values()) != bucket["sessions"]:
            return None
        if sum(bucket["attempted_backends"].values()) != bucket["sessions"]:
            return None
        if sum(bucket["results"].values()) != bucket["sessions"]:
            return None
        if sum(bucket["target_kinds"].values()) != bucket["sessions"]:
            return None
        if (
            sum(bucket["eligible_results"].values())
            != bucket["eligible_direct_attempts"]
        ):
            return None
        if (
            sum(bucket["eligible_target_results"].values())
            != bucket["eligible_direct_attempts"]
        ):
            return None
        if bucket["eligible_direct_attempts"] > bucket["sessions"]:
            return None
        if bucket["eligible_direct_attempts"] > sum(
            bucket["attempted_backends"].get(name, 0)
            for name in DIRECT_ATTEMPT_BACKENDS
        ):
            return None
        for result_name in RESULTS:
            joint_result_count = sum(
                bucket["eligible_target_results"].get(
                    "{}:{}".format(target, result_name), 0
                )
                for target in TARGET_KINDS
            )
            if joint_result_count != bucket["eligible_results"].get(
                result_name, 0
            ):
                return None
            if bucket["eligible_results"].get(
                result_name, 0
            ) > bucket["results"].get(result_name, 0):
                return None
        for target in TARGET_KINDS:
            joint_target_count = sum(
                bucket["eligible_target_results"].get(
                    "{}:{}".format(target, result_name), 0
                )
                for result_name in RESULTS
            )
            if joint_target_count > bucket["target_kinds"].get(target, 0):
                return None
        if bucket["clipboard_fallback"] != bucket["results"].get(
            "clipboard-fallback", 0
        ):
            return None
        if bucket["focus_drift"] > bucket["sessions"]:
            return None
        for key in (
            "focus_drift",
            "clipboard_fallback",
            "unicode_failures",
            "spanish_punctuation_failures",
        ):
            if not _is_count(bucket[key]):
                return None
    return value


def _fixed_category(value: Any, allowed: Sequence[str], fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = value.strip().lower()
    return normalized if normalized in allowed else fallback


def _backend_category(value: Any) -> str:
    raw = value.strip().lower() if isinstance(value, str) else ""
    return _fixed_category(BACKEND_ALIASES.get(raw, raw), BACKENDS, "other")


def _result_category(value: Any) -> str:
    raw = getattr(value, "value", value)
    return _fixed_category(raw, RESULTS, "other")


def _increment(mapping: Dict[str, int], key: str) -> None:
    mapping[key] = min(MAX_COUNT, mapping.get(key, 0) + 1)


def _increment_scalar(bucket: Dict[str, Any], key: str) -> None:
    bucket[key] = min(MAX_COUNT, bucket[key] + 1)


def _environment_opted_out() -> bool:
    value = os.environ.get("LST_INSERTION_METRICS")
    return value is not None and value.strip().lower() in FALSE_VALUES


def _lexical_absolute_path(path: Path) -> Path:
    """Return an absolute normalized path without resolving any symlinks."""
    return Path(os.path.abspath(str(path.expanduser())))


def _reject_symlink_components(path: Path, label: str) -> None:
    """Reject every existing symlink component without resolving the path."""
    absolute = _lexical_absolute_path(path)
    current = Path(absolute.anchor)
    parts = absolute.parts[1:] if absolute.anchor else absolute.parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            entry = os.lstat(str(current))
        except FileNotFoundError:
            # Descendants cannot exist until this missing component is created.
            return
        if stat.S_ISLNK(entry.st_mode):
            raise UnsafeMetricsPath(
                "{} must not contain symlink components".format(label)
            )
        if index < len(parts) - 1 and not stat.S_ISDIR(entry.st_mode):
            raise UnsafeMetricsPath(
                "{} parent component must be a directory".format(label)
            )


def _same_existing_file(first: Path, second: Path) -> bool:
    """Return inode identity without treating missing paths as equivalent."""
    try:
        return os.path.samefile(str(first), str(second))
    except (FileNotFoundError, OSError):
        return False


def _ensure_owned_nonwritable_parent(parent: Path, label: str) -> None:
    """Create a private parent or validate an existing nonwritable one."""
    created = False
    try:
        entry = os.lstat(str(parent))
    except FileNotFoundError:
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=False)
            created = True
        except FileExistsError:
            # A concurrent creator made the final component. Treat it as an
            # existing directory and never chmod an object we did not create.
            pass
        entry = os.lstat(str(parent))
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
        raise UnsafeMetricsPath("{} must be a real directory".format(label))
    if entry.st_uid != os.geteuid():
        raise UnsafeMetricsPath("{} is not owned by this user".format(label))
    if created:
        os.chmod(str(parent), 0o700)
        entry = os.lstat(str(parent))
        if (
            stat.S_ISLNK(entry.st_mode)
            or not stat.S_ISDIR(entry.st_mode)
            or entry.st_uid != os.geteuid()
        ):
            raise UnsafeMetricsPath("{} changed during creation".format(label))
    if stat.S_IMODE(entry.st_mode) & 0o022:
        raise UnsafeMetricsPath(
            "{} must not be group- or world-writable".format(label)
        )


class InsertionMetricsStore:
    """Concurrency-safe owner of the bounded local aggregate state."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else default_state_path()
        if not self.path.is_absolute():
            raise ValueError("insertion metrics state path must be absolute")
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.disabled_path = self.path.with_name(self.path.name + ".disabled")

    def _ensure_parent(self) -> None:
        _ensure_owned_nonwritable_parent(
            self.path.parent, "metrics state parent"
        )

    @staticmethod
    def _validate_regular_fd(fd: int, label: str) -> os.stat_result:
        entry = os.fstat(fd)
        if not stat.S_ISREG(entry.st_mode):
            raise UnsafeMetricsPath("{} must be a regular file".format(label))
        if entry.st_uid != os.geteuid():
            raise UnsafeMetricsPath("{} is not owned by this user".format(label))
        if entry.st_nlink != 1:
            raise UnsafeMetricsPath("{} must not be hard-linked".format(label))
        return entry

    @staticmethod
    def _open_flags(base: int) -> int:
        return (
            base
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )

    @staticmethod
    def _validate_replace_target(path: Path, label: str) -> None:
        try:
            entry = os.lstat(str(path))
        except FileNotFoundError:
            return
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
            raise UnsafeMetricsPath("{} must be a real regular file".format(label))
        if entry.st_uid != os.geteuid() or entry.st_nlink != 1:
            raise UnsafeMetricsPath("{} is not a private owned file".format(label))

    def _fsync_parent(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        dir_fd = os.open(str(self.path.parent), flags)
        try:
            entry = os.fstat(dir_fd)
            if (
                not stat.S_ISDIR(entry.st_mode)
                or entry.st_uid != os.geteuid()
                or stat.S_IMODE(entry.st_mode) & 0o022
            ):
                raise UnsafeMetricsPath("metrics parent changed during write")
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _disabled_unlocked(self) -> bool:
        try:
            entry = os.lstat(str(self.disabled_path))
        except FileNotFoundError:
            return False
        # Any unexpected marker object fails privacy-closed, but is never
        # followed or modified by a normal record/report operation.
        if (
            stat.S_ISLNK(entry.st_mode)
            or not stat.S_ISREG(entry.st_mode)
            or entry.st_uid != os.geteuid()
            or entry.st_nlink != 1
        ):
            return True
        try:
            os.chmod(str(self.disabled_path), 0o600, follow_symlinks=False)
        except (NotImplementedError, OSError):
            pass
        return True

    def _write_disabled_unlocked(self) -> None:
        if self._disabled_unlocked():
            return
        self._atomic_write_bytes(
            self.disabled_path,
            b"disabled\n",
            prefix=".insertion-metrics-disabled-",
            label="metrics disable marker",
        )

    def _remove_disabled_unlocked(self) -> None:
        try:
            entry = os.lstat(str(self.disabled_path))
        except FileNotFoundError:
            return
        if stat.S_ISDIR(entry.st_mode):
            raise UnsafeMetricsPath("metrics disable marker must not be a directory")
        # unlink removes the directory entry itself and never follows a symlink.
        os.unlink(str(self.disabled_path))
        self._fsync_parent()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_parent()
        # Reject known special files before opening them. O_NOFOLLOW and the
        # post-open fstat below close the remaining symlink/type races.
        self._validate_replace_target(self.lock_path, "metrics lock")
        flags = self._open_flags(os.O_RDWR | os.O_CREAT)
        try:
            fd = os.open(str(self.lock_path), flags, 0o600)
        except OSError as exc:
            raise UnsafeMetricsPath("could not safely open metrics lock") from exc
        try:
            lock_entry = self._validate_regular_fd(fd, "metrics lock")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            current = os.lstat(str(self.lock_path))
            if (
                current.st_dev != lock_entry.st_dev
                or current.st_ino != lock_entry.st_ino
                or stat.S_ISLNK(current.st_mode)
            ):
                raise UnsafeMetricsPath("metrics lock changed while acquiring it")
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)

    def _read_unlocked(self) -> Tuple[Dict[str, Any], bool]:
        disabled = self._disabled_unlocked()
        self._validate_replace_target(self.path, "metrics state")
        try:
            fd = os.open(str(self.path), self._open_flags(os.O_RDONLY))
        except FileNotFoundError:
            return _empty_state(enabled=not disabled), False
        except OSError as exc:
            raise UnsafeMetricsPath("could not safely open metrics state") from exc
        try:
            entry = self._validate_regular_fd(fd, "metrics state")
            os.fchmod(fd, 0o600)
            if entry.st_size > MAX_STATE_BYTES:
                return _empty_state(enabled=False), True
            chunks = []
            remaining = MAX_STATE_BYTES + 1
            while remaining > 0:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            encoded = b"".join(chunks)
            if len(encoded) > MAX_STATE_BYTES:
                return _empty_state(enabled=False), True
            try:
                raw = encoded.decode("utf-8")
            except UnicodeError:
                return _empty_state(enabled=False), True
        finally:
            os.close(fd)
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, RecursionError):
            return _empty_state(enabled=False), True
        validated = _validated_state(parsed)
        if validated is None:
            return _empty_state(enabled=False), True
        if disabled:
            validated["enabled"] = False
        return validated, False

    def _atomic_write_bytes(
        self, path: Path, encoded: bytes, *, prefix: str, label: str
    ) -> None:
        self._validate_replace_target(path, label)
        fd, temp_name = tempfile.mkstemp(prefix=prefix, dir=str(path.parent))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_replace_target(path, label)
            os.replace(temp_name, str(path))
            os.chmod(str(path), 0o600, follow_symlinks=False)
            self._fsync_parent()
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    def _write_unlocked(self, state: Mapping[str, Any]) -> None:
        payload = json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
        encoded = payload.encode("utf-8")
        if len(encoded) > MAX_STATE_BYTES:
            raise ValueError("metrics state exceeded its fixed size limit")
        self._atomic_write_bytes(
            self.path,
            encoded,
            prefix=".insertion-metrics-",
            label="metrics state",
        )

    @staticmethod
    def _trim_days(state: Dict[str, Any]) -> None:
        names = sorted(state["days"])
        for name in names[:-MAX_DAYS]:
            del state["days"][name]

    def snapshot(self) -> Dict[str, Any]:
        """Return a validated copy, atomically replacing corrupt state."""
        with self._locked():
            state, corrupt = self._read_unlocked()
            if not state["enabled"]:
                self._write_disabled_unlocked()
            if corrupt:
                self._write_unlocked(state)
            return json.loads(json.dumps(state))

    def effective_enabled(self) -> bool:
        if _environment_opted_out():
            return False
        return bool(self.snapshot()["enabled"])

    def configure(self, enabled: bool) -> None:
        with self._locked():
            state, _ = self._read_unlocked()
            state["enabled"] = bool(enabled)
            if not enabled:
                # Persist the privacy choice before touching recoverable JSON.
                self._write_disabled_unlocked()
            self._write_unlocked(state)
            if enabled:
                # Remove the fail-closed marker only after enabled state is
                # durably installed while all recorders remain locked out.
                self._remove_disabled_unlocked()

    def reset(self) -> None:
        with self._locked():
            state, _ = self._read_unlocked()
            self._write_unlocked(_empty_state(enabled=bool(state["enabled"])))

    def _mutate_day(self, day: dt.date, callback: Any) -> bool:
        if _environment_opted_out():
            return False
        day_name = day.isoformat()
        with self._locked():
            state, corrupt = self._read_unlocked()
            if not state["enabled"]:
                self._write_disabled_unlocked()
                if corrupt:
                    self._write_unlocked(state)
                return False
            bucket = state["days"].setdefault(day_name, _empty_day())
            callback(bucket)
            if state["period_started"] is None or day_name < state["period_started"]:
                state["period_started"] = day_name
            state["updated_on"] = day_name
            self._trim_days(state)
            self._write_unlocked(state)
        return True

    def record(
        self,
        *,
        backend: Any,
        attempted_backend: Any = "other",
        result_category: Any,
        target_kind: Any,
        focus_drift: bool = False,
        eligible_supported_gnome_direct: bool = False,
        day: Optional[dt.date] = None,
    ) -> bool:
        """Count one completed insertion session using fixed categories only."""
        backend_name = _backend_category(backend)
        attempted_backend_name = _backend_category(attempted_backend)
        result_name = _result_category(result_category)
        target_name = _fixed_category(target_kind, TARGET_KINDS, "unknown")
        eligible = bool(eligible_supported_gnome_direct) and (
            attempted_backend_name in DIRECT_ATTEMPT_BACKENDS
        )

        def update(bucket: Dict[str, Any]) -> None:
            _increment_scalar(bucket, "sessions")
            _increment(bucket["backends"], backend_name)
            _increment(bucket["attempted_backends"], attempted_backend_name)
            _increment(bucket["results"], result_name)
            _increment(bucket["target_kinds"], target_name)
            if eligible:
                _increment_scalar(bucket, "eligible_direct_attempts")
                _increment(bucket["eligible_results"], result_name)
                _increment(
                    bucket["eligible_target_results"],
                    "{}:{}".format(target_name, result_name),
                )
            if focus_drift:
                _increment_scalar(bucket, "focus_drift")
            if result_name == "clipboard-fallback":
                _increment_scalar(bucket, "clipboard_fallback")

        return self._mutate_day(day or _today(), update)

    def mark_failure(
        self,
        failure: str,
        *,
        backend: Any = "other",
        target_kind: Any = "unknown",
        day: Optional[dt.date] = None,
    ) -> bool:
        """Count a manual character-class failure without session dimensions.

        ``backend`` and ``target_kind`` remain accepted for compatibility with
        older CLI invocations, but deliberately are not retained: the existing
        backend and target maps describe completed insertion sessions only.
        """
        del backend, target_kind
        key = {
            "unicode": "unicode_failures",
            "spanish-punctuation": "spanish_punctuation_failures",
        }.get(failure)
        if key is None:
            raise ValueError("unsupported failure category")

        def update(bucket: Dict[str, Any]) -> None:
            _increment_scalar(bucket, key)

        return self._mutate_day(day or _today(), update)


def record_insertion_result(
    operation_result: Any,
    *,
    target_kind: Any,
    attempted_backend: Any = "other",
    focus_drift: bool = False,
    eligible_supported_gnome_direct: bool = False,
    store: Optional[InsertionMetricsStore] = None,
) -> bool:
    """Best-effort adapter that intentionally ignores all sensitive fields."""
    if operation_result is None:
        return False
    try:
        selected = store or InsertionMetricsStore()
        return selected.record(
            backend=getattr(operation_result, "backend", "other"),
            attempted_backend=attempted_backend,
            result_category=getattr(operation_result, "state", "other"),
            target_kind=target_kind,
            focus_drift=bool(focus_drift),
            eligible_supported_gnome_direct=bool(
                eligible_supported_gnome_direct
            ),
        )
    except Exception:
        # A local observability failure must never change insertion success or
        # cause the caller to retry a potentially completed dispatch.
        return False


def _aggregate(state: Mapping[str, Any]) -> Dict[str, Any]:
    totals = {
        "sessions": 0,
        "backends": {name: 0 for name in BACKENDS},
        "attempted_backends": {name: 0 for name in BACKENDS},
        "results": {name: 0 for name in RESULTS},
        "target_kinds": {name: 0 for name in TARGET_KINDS},
        "eligible_direct_attempts": 0,
        "eligible_results": {name: 0 for name in RESULTS},
        "eligible_target_results": {
            name: 0 for name in TARGET_RESULT_KEYS
        },
        "focus_drift": 0,
        "clipboard_fallback": 0,
        "unicode_failures": 0,
        "spanish_punctuation_failures": 0,
    }
    session_days: List[str] = []
    for day_name in sorted(state["days"]):
        bucket = state["days"][day_name]
        totals["sessions"] += bucket["sessions"]
        if bucket["eligible_direct_attempts"]:
            session_days.append(day_name)
        for dimension in (
            "backends",
            "attempted_backends",
            "results",
            "target_kinds",
            "eligible_results",
            "eligible_target_results",
        ):
            for name, count in bucket[dimension].items():
                totals[dimension][name] += count
        totals["eligible_direct_attempts"] += bucket["eligible_direct_attempts"]
        for name in (
            "focus_drift",
            "clipboard_fallback",
            "unicode_failures",
            "spanish_punctuation_failures",
        ):
            totals[name] += bucket[name]
    first = session_days[0] if session_days else None
    last = session_days[-1] if session_days else None
    span_days = 0
    if first is not None and last is not None:
        # Require a full two weeks of elapsed observation time. Inclusive
        # calendar-day counting would declare readiness after only 13 days.
        span_days = (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days
    sessions = totals["eligible_direct_attempts"]
    eligible_fallbacks = totals["eligible_results"]["clipboard-fallback"]
    fallback_rate = eligible_fallbacks / sessions if sessions else 0.0
    dispatch_failures = (
        totals["eligible_results"]["failed-before-dispatch"]
        + totals["eligible_results"]["ambiguous-after-dispatch"]
    )
    dispatch_failure_rate = dispatch_failures / sessions if sessions else 0.0
    evidence_ready = sessions >= 100 and span_days >= 14
    trigger_met = evidence_ready and (
        fallback_rate > 0.10 or dispatch_failure_rate > 0.01
    )
    eligible_targets = {}
    for target in TARGET_KINDS:
        target_results = {
            result_name: totals["eligible_target_results"][
                "{}:{}".format(target, result_name)
            ]
            for result_name in RESULTS
        }
        attempts = sum(target_results.values())
        if not attempts:
            continue
        target_fallbacks = target_results["clipboard-fallback"]
        target_dispatch_failures = (
            target_results["failed-before-dispatch"]
            + target_results["ambiguous-after-dispatch"]
        )
        eligible_targets[target] = {
            "attempts": attempts,
            "clipboard_fallback": target_fallbacks,
            "clipboard_fallback_rate": round(target_fallbacks / attempts, 6),
            "failed_or_ambiguous_dispatch": target_dispatch_failures,
            "failed_or_ambiguous_dispatch_rate": round(
                target_dispatch_failures / attempts, 6
            ),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_on": _today().isoformat(),
        "collection_enabled": (
            bool(state["enabled"]) and not _environment_opted_out()
        ),
        "evidence": {
            "cohort": "eligible-supported-gnome-direct-attempts",
            "ready": evidence_ready,
            "minimum_eligible_attempts": 100,
            "minimum_days": 14,
            "observed_eligible_attempts": sessions,
            "observed_days": span_days,
            "first_eligible_attempt_on": first,
            "last_eligible_attempt_on": last,
        },
        "totals": totals,
        "rates": {
            "cohort": "eligible-supported-gnome-direct-attempts",
            "clipboard_fallback": round(fallback_rate, 6),
            "failed_or_ambiguous_dispatch": round(dispatch_failure_rate, 6),
        },
        "eligible_targets": eligible_targets,
        "ibus_trigger": {
            "automatic_rate_trigger_met": trigger_met,
            "cohort": "eligible-supported-gnome-direct-attempts",
            "clipboard_fallback_threshold": ">10%",
            "failed_or_ambiguous_dispatch_threshold": ">1%",
            "manual_recovery_trigger_evaluated": False,
        },
    }


def report_data(store: Optional[InsertionMetricsStore] = None) -> Dict[str, Any]:
    return _aggregate((store or InsertionMetricsStore()).snapshot())


def _nonzero(mapping: Mapping[str, int]) -> List[Tuple[str, int]]:
    return [(name, mapping[name]) for name in mapping if mapping[name]]


def render_report(data: Mapping[str, Any]) -> str:
    evidence = data["evidence"]
    totals = data["totals"]
    status = "READY" if evidence["ready"] else "PENDING"
    lines = [
        "# Insertion reliability report — {}".format(data["generated_on"]),
        "",
        "Evidence status: **{}** ({} eligible supported-GNOME direct attempts across {} elapsed days; requires at least 100 eligible attempts across 14 days).".format(
            status,
            evidence["observed_eligible_attempts"],
            evidence["observed_days"],
        ),
        "",
        "This local report contains fixed aggregate categories only. It contains no transcript, title, repository path, app identity, window token, or session identifier.",
        "",
        "## Reliability counters",
        "",
        "- All completed sessions: {}".format(totals["sessions"]),
        "- Eligible supported-GNOME direct attempts: {}".format(
            totals["eligible_direct_attempts"]
        ),
        "- Focus drift across all sessions: {}".format(totals["focus_drift"]),
        "- Eligible direct attempts ending in clipboard fallback: {} ({:.2%})".format(
            totals["eligible_results"]["clipboard-fallback"],
            data["rates"]["clipboard_fallback"],
        ),
        "- Eligible direct attempts with failed or ambiguous dispatch: {:.2%}".format(
            data["rates"]["failed_or_ambiguous_dispatch"]
        ),
        "- Unicode failures: {}".format(totals["unicode_failures"]),
        "- Spanish punctuation failures: {}".format(
            totals["spanish_punctuation_failures"]
        ),
        "",
        "## Fixed-category breakdown",
        "",
        "- Attempted backends: {}".format(
            ", ".join(
                "{}={}".format(k, v)
                for k, v in _nonzero(totals["attempted_backends"])
            )
            or "none"
        ),
        "- Final delivery backends: {}".format(
            ", ".join(
                "{}={}".format(k, v)
                for k, v in _nonzero(totals["backends"])
            )
            or "none"
        ),
        "- Results: {}".format(
            ", ".join(
                "{}={}".format(k, v)
                for k, v in _nonzero(totals["results"])
            )
            or "none"
        ),
        "- Coarse targets: {}".format(
            ", ".join(
                "{}={}".format(k, v)
                for k, v in _nonzero(totals["target_kinds"])
            )
            or "none"
        ),
        "",
        "## Eligible target outcomes",
        "",
    ]
    if data["eligible_targets"]:
        for target, target_data in data["eligible_targets"].items():
            lines.append(
                "- {}: attempts={}, clipboard fallback={} ({:.2%}), failed/ambiguous={} ({:.2%})".format(
                    target,
                    target_data["attempts"],
                    target_data["clipboard_fallback"],
                    target_data["clipboard_fallback_rate"],
                    target_data["failed_or_ambiguous_dispatch"],
                    target_data["failed_or_ambiguous_dispatch_rate"],
                )
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## IBus decision gate",
            "",
        ]
    )
    if not evidence["ready"]:
        lines.append(
            "Automatic rate thresholds are not evaluated until the sample "
            "requirement is met."
        )
    elif data["ibus_trigger"]["automatic_rate_trigger_met"]:
        lines.append(
            "The >10% clipboard-fallback or >1% failed/ambiguous-dispatch "
            "automatic trigger is met."
        )
    else:
        lines.append("The automatic rate triggers are not met.")
    lines.extend(
        [
            "The manual-recovery trigger is not evaluated because this privacy-minimized schema does not collect manual recovery events.",
            "Wrong-window insertion is a P0 safety defect and must be reported separately; this aggregate counter set cannot safely infer it.",
            "Affected applications are represented only by the coarse target categories above.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_private(path: Path, text: str) -> None:
    path = _lexical_absolute_path(path)
    parent = path.parent
    _reject_symlink_components(path, "metrics report path")
    _ensure_owned_nonwritable_parent(parent, "report parent")
    # Recheck after creating a previously absent parent; mkdir(parents=True)
    # must not turn a newly introduced intermediate symlink into a write path.
    _reject_symlink_components(path, "metrics report path")
    InsertionMetricsStore._validate_replace_target(path, "metrics report")
    fd, temp_name = tempfile.mkstemp(prefix=".insertion-report-", dir=str(parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        InsertionMetricsStore._validate_replace_target(path, "metrics report")
        os.replace(temp_name, str(path))
        os.chmod(str(path), 0o600, follow_symlinks=False)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        dir_fd = os.open(str(parent), flags)
        try:
            current_parent = os.fstat(dir_fd)
            if (
                not stat.S_ISDIR(current_parent.st_mode)
                or current_parent.st_uid != os.geteuid()
                or stat.S_IMODE(current_parent.st_mode) & 0o022
            ):
                raise UnsafeMetricsPath("report parent changed during write")
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lst-insertion-metrics",
        description="Manage private, transcript-free insertion reliability aggregates.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    report = subparsers.add_parser("report", help="render the dated aggregate report")
    report.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    report.add_argument("--output", type=Path, help="atomically write a private report file")
    status = subparsers.add_parser("status", help="show collection and sample status")
    status.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    subparsers.add_parser("enable", help="enable persistent collection")
    subparsers.add_parser("disable", help="disable persistent collection")
    reset = subparsers.add_parser("reset", help="delete all aggregate counters")
    reset.add_argument("--yes", action="store_true", help="confirm destructive reset")
    failure = subparsers.add_parser(
        "mark-failure", help="count a Unicode or Spanish-punctuation observation"
    )
    failure.add_argument("failure", choices=("unicode", "spanish-punctuation"))
    failure.add_argument(
        "--backend",
        default="other",
        choices=BACKENDS,
        help="accepted for compatibility; not retained in aggregate state",
    )
    failure.add_argument(
        "--target-kind",
        default="unknown",
        choices=TARGET_KINDS,
        help="accepted for compatibility; not retained in aggregate state",
    )
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    store: Optional[InsertionMetricsStore] = None,
) -> int:
    args = build_parser().parse_args(argv)
    selected = store or InsertionMetricsStore()
    if args.command == "report" and args.output is not None:
        output_path = _lexical_absolute_path(args.output)
        reserved_paths = (
            _lexical_absolute_path(selected.path),
            _lexical_absolute_path(selected.lock_path),
            _lexical_absolute_path(selected.disabled_path),
        )
        # Perform path-safety and collision checks before snapshot/report
        # generation. This prevents an alias from destroying the aggregate and
        # only then being discovered as corrupt on the next read.
        _reject_symlink_components(output_path, "metrics report path")
        for reserved_path in reserved_paths:
            _reject_symlink_components(reserved_path, "metrics reserved path")
            if output_path == reserved_path or _same_existing_file(
                output_path, reserved_path
            ):
                raise UnsafeMetricsPath(
                    "metrics report output collides with reserved state path"
                )
    if args.command == "enable":
        selected.configure(True)
        print("Insertion metrics enabled locally.")
        if _environment_opted_out():
            print("Environment opt-out remains active.")
        return 0
    if args.command == "disable":
        selected.configure(False)
        print("Insertion metrics disabled locally.")
        return 0
    if args.command == "reset":
        if not args.yes:
            print("Refusing to reset without --yes.", file=os.sys.stderr)
            return 2
        selected.reset()
        print("Insertion aggregate counters reset.")
        return 0
    if args.command == "mark-failure":
        recorded = selected.mark_failure(
            args.failure, backend=args.backend, target_kind=args.target_kind
        )
        if not recorded:
            print("Insertion metrics are disabled; observation was not recorded.")
            return 1
        print("Aggregate failure observation recorded.")
        return 0

    data = report_data(selected)
    if args.command == "status":
        evidence = data["evidence"]
        status_data = {
            "schema_version": data["schema_version"],
            "collection_enabled": data["collection_enabled"],
            "evidence_ready": evidence["ready"],
            "observed_eligible_attempts": evidence[
                "observed_eligible_attempts"
            ],
            "observed_days": evidence["observed_days"],
        }
        if args.json:
            print(json.dumps(status_data, sort_keys=True))
        else:
            print(
                "Collection: {}; evidence: {}; eligible attempts: {}; days: {}".format(
                    "enabled" if status_data["collection_enabled"] else "disabled",
                    "ready" if status_data["evidence_ready"] else "pending",
                    status_data["observed_eligible_attempts"],
                    status_data["observed_days"],
                )
            )
        return 0

    output = (
        json.dumps(data, indent=2, sort_keys=True) + "\n"
        if args.json
        else render_report(data)
    )
    if args.output:
        _write_private(args.output, output)
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
