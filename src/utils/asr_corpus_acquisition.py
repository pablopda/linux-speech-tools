#!/usr/bin/env python3
"""Guided, private acquisition for the exact LATAM ASR acceptance corpus.

This module deliberately keeps raw audio, references, speaker identifiers, and
capture details outside the source checkout.  It creates only the fixed
AR=24/MX=4/CO=4/CL=4 skeleton, records one bounded WAV at a time, and delegates
the final mechanical acceptance check to :mod:`src.utils.asr_benchmark`.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
import secrets
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

try:
    from . import asr_benchmark
except ImportError:  # pragma: no cover - direct script compatibility
    from src.utils import asr_benchmark


MANIFEST_NAME = "manifest.json"
AUDIO_DIRECTORY_NAME = "audio"
LOCK_NAME = ".corpus.lock"
_MAX_RECORD_SECONDS = 120
_CAPTURE_GRACE_SECONDS = 15
_MAX_SOURCE_CHARS = 256
_MAX_REFERENCE_BYTES = 80_000
_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_LOCK_TIMEOUT_SECONDS = 5.0
_LOCK_POLL_SECONDS = 0.05
_SAFE_CLIP_FIELDS = {
    "id",
    "audio",
    "reference",
    "reference_reviewed",
    "accent",
    "accent_authenticity",
    "category",
    "code_switch",
    "speaker_id",
    "consent",
    "privacy_review",
    "sha256",
    "sample_rate",
    "channels",
    "duration_s",
    "capture_device",
    "recording_environment",
}
_CATEGORY_PLAN = (
    "everyday",
    "programming",
    "filename-dev-term",
    "short-command",
    "long-prompt",
    "punctuation-heavy",
    "silence",
    "interrupted",
)


class CorpusAcquisitionError(ValueError):
    """A fixed, transcript-free acquisition or workspace error."""


class _ManifestCommittedError(CorpusAcquisitionError):
    """The manifest replacement happened but directory durability is uncertain."""


class _RedactingArgumentParser(argparse.ArgumentParser):
    """Keep untrusted argv values out of parser diagnostics."""

    def error(self, _message: str) -> None:
        self.exit(2, "lst-asr-corpus: error: invalid or unsafe request\n")


def _is_within(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(parent))) == str(parent)
    except ValueError:
        return False


def _reject_symlink_components(path: Path) -> None:
    """Reject every existing symlink and non-directory parent component."""
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            entry = os.lstat(str(current))
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(entry.st_mode):
            raise CorpusAcquisitionError("workspace paths must not contain symlinks")
        if current != path and not stat.S_ISDIR(entry.st_mode):
            raise CorpusAcquisitionError(
                "workspace parent components must be directories"
            )


def _external_absolute_path(value: object, *, label: str) -> Path:
    raw = Path(str(value)).expanduser()
    if not raw.is_absolute():
        raise CorpusAcquisitionError(f"{label} must be an absolute path")
    if ".." in raw.parts:
        raise CorpusAcquisitionError(f"{label} must not contain parent traversal")
    path = Path(os.path.abspath(str(raw)))
    _reject_symlink_components(path)

    checkout = asr_benchmark._source_checkout_root()
    if checkout is not None:
        checkout_real = Path(os.path.realpath(str(checkout)))
        if _is_within(path, checkout_real) or _is_within(
            Path(os.path.realpath(str(path))), checkout_real
        ):
            raise CorpusAcquisitionError(
                f"{label} must remain outside the source repository"
            )
    if path == Path(path.anchor):
        raise CorpusAcquisitionError(f"{label} is an unsafe broad path")
    try:
        home = Path.home().resolve()
    except OSError:
        home = None
    if home is not None and path == home:
        raise CorpusAcquisitionError(f"{label} must not be the home directory")
    return path


def _require_private_directory(path: Path, *, label: str) -> os.stat_result:
    _reject_symlink_components(path)
    try:
        entry = os.lstat(str(path))
    except OSError as exc:
        raise CorpusAcquisitionError(f"{label} is not an accessible directory") from exc
    if not stat.S_ISDIR(entry.st_mode):
        raise CorpusAcquisitionError(f"{label} must be a directory")
    if entry.st_uid != os.getuid():
        raise CorpusAcquisitionError(f"{label} must be owned by the current user")
    if stat.S_IMODE(entry.st_mode) & 0o077:
        raise CorpusAcquisitionError(f"{label} must have private permissions (0700)")
    return entry


def _require_private_regular(path: Path, *, label: str) -> os.stat_result:
    _reject_symlink_components(path)
    try:
        entry = os.lstat(str(path))
    except OSError as exc:
        raise CorpusAcquisitionError(
            f"{label} must be a readable regular file"
        ) from exc
    if not stat.S_ISREG(entry.st_mode):
        raise CorpusAcquisitionError(f"{label} must be a regular file")
    if entry.st_uid != os.getuid() or entry.st_nlink != 1:
        raise CorpusAcquisitionError(
            f"{label} must be current-user-owned and have one hard link"
        )
    if stat.S_IMODE(entry.st_mode) & 0o077:
        raise CorpusAcquisitionError(f"{label} must have private permissions (0600)")
    return entry


def _workspace_paths(directory: object) -> Dict[str, Path]:
    root = _external_absolute_path(directory, label="corpus directory")
    return {
        "root": root,
        "audio": root / AUDIO_DIRECTORY_NAME,
        "manifest": root / MANIFEST_NAME,
        "lock": root / LOCK_NAME,
    }


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(str(path), flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@contextmanager
def _workspace_lock(paths: Dict[str, Path], *, exclusive: bool):
    """Hold a private per-workspace advisory lock around one full operation."""
    root = paths["root"]
    lock_path = paths["lock"]
    _require_private_directory(root, label="corpus directory")
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    created = False
    try:
        try:
            fd = os.open(str(lock_path), flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
            os.fchmod(fd, 0o600)
        except FileExistsError:
            fd = os.open(str(lock_path), flags)
    except OSError:
        raise CorpusAcquisitionError(
            "private corpus lock is unavailable or unsafe"
        ) from None

    try:
        opened = os.fstat(fd)
        try:
            named = os.lstat(str(lock_path))
        except OSError:
            raise CorpusAcquisitionError(
                "private corpus lock changed during safe open"
            ) from None
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or named.st_dev != opened.st_dev
            or named.st_ino != opened.st_ino
        ):
            raise CorpusAcquisitionError(
                "private corpus lock must be current-user-owned 0600 state with one hard link"
            )
        if created:
            try:
                _fsync_directory(root)
            except OSError:
                raise CorpusAcquisitionError(
                    "private corpus lock durability could not be confirmed"
                ) from None
        operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(fd, operation | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CorpusAcquisitionError(
                        "private corpus workspace is busy; retry later"
                    ) from None
                time.sleep(min(_LOCK_POLL_SECONDS, remaining))
            except InterruptedError:
                if time.monotonic() >= deadline:
                    raise CorpusAcquisitionError(
                        "private corpus workspace is busy; retry later"
                    ) from None
            except OSError:
                raise CorpusAcquisitionError(
                    "could not acquire the private corpus lock"
                ) from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _validate_token(value: object, *, label: str) -> str:
    text = str(value)
    if not asr_benchmark._SAFE_ID_RE.fullmatch(text):
        raise CorpusAcquisitionError(f"{label} must be a pseudonymous ASCII token")
    return text


def _validate_speaker_id(value: object, *, accent: object) -> str:
    text = str(value)
    match = asr_benchmark._SAFE_SPEAKER_ID_RE.fullmatch(text)
    if not match:
        raise CorpusAcquisitionError(
            "speaker id must use an accent-scoped pseudonym such as speaker-ar-01"
        )
    if match.group(1).upper() != accent:
        raise CorpusAcquisitionError("speaker pseudonym must match the clip accent")
    return text


def _planned_clips(root: Path) -> List[Dict[str, object]]:
    clips: List[Dict[str, object]] = []
    offset = 0
    for accent, count in (("AR", 24), ("MX", 4), ("CO", 4), ("CL", 4)):
        for number in range(1, count + 1):
            clip_id = f"{accent.lower()}-{number:03d}"
            clips.append(
                {
                    "id": clip_id,
                    "audio": str(root / AUDIO_DIRECTORY_NAME / f"{clip_id}.wav"),
                    "reference": "",
                    "reference_reviewed": False,
                    "accent": accent,
                    "category": _CATEGORY_PLAN[offset % len(_CATEGORY_PLAN)],
                    "code_switch": clip_id == "ar-002",
                    "speaker_id": "pending",
                    "accent_authenticity": "pending",
                    "consent": "pending",
                    "privacy_review": "pending",
                    "sha256": "pending",
                    "sample_rate": 0,
                    "channels": 0,
                    "duration_s": 0.0,
                    "capture_device": "pending",
                    "recording_environment": "private-local",
                }
            )
            offset += 1
    return clips


def _atomic_write_manifest(path: Path, clips: Sequence[Dict[str, object]]) -> None:
    encoded = (json.dumps(list(clips), ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    if len(encoded) > asr_benchmark._MAX_MANIFEST_BYTES:
        raise CorpusAcquisitionError("private manifest exceeds the fixed size limit")
    if os.path.lexists(str(path)):
        _require_private_regular(path, label="private manifest")
    temp = path.parent / f".{MANIFEST_NAME}.{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(temp), flags, 0o600)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise CorpusAcquisitionError("could not write the private manifest")
            view = view[written:]
        os.fsync(fd)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(fd)
    replaced = False
    try:
        os.replace(str(temp), str(path))
        replaced = True
        _fsync_directory(path.parent)
    except BaseException:
        if replaced:
            raise _ManifestCommittedError(
                "private manifest was committed but durability could not be confirmed"
            ) from None
        raise
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _validate_workspace_shape(
    clips: Sequence[Dict[str, object]], root: Path
) -> List[Dict[str, object]]:
    expected = _planned_clips(root)
    if len(clips) != len(expected):
        raise CorpusAcquisitionError("workspace must retain exactly 36 planned clips")
    validated: List[Dict[str, object]] = []
    for index, (clip, planned) in enumerate(zip(clips, expected)):
        if not isinstance(clip, dict):
            raise CorpusAcquisitionError("private manifest contains an invalid clip")
        extra = set(clip) - _SAFE_CLIP_FIELDS
        if extra:
            raise CorpusAcquisitionError(
                "private manifest contains unsupported fields; speaker names and notes are forbidden"
            )
        for field in ("id", "accent", "category", "code_switch", "audio"):
            if clip.get(field) != planned[field]:
                raise CorpusAcquisitionError(
                    f"clip {index + 1} no longer matches the fixed acquisition plan"
                )
        if clip.get("speaker_id") != "pending":
            _validate_speaker_id(clip.get("speaker_id"), accent=clip.get("accent"))
        validated.append(dict(clip))
    speaker_accents: Dict[str, str] = {}
    for clip in validated:
        speaker_id = str(clip["speaker_id"])
        if speaker_id == "pending":
            continue
        prior_accent = speaker_accents.setdefault(speaker_id, str(clip["accent"]))
        if prior_accent != clip["accent"]:
            raise CorpusAcquisitionError(
                "a pseudonymous speaker id must not be reused across accents"
            )
    return validated


def _load_workspace_unlocked(paths: Dict[str, Path]) -> Dict[str, Any]:
    _require_private_directory(paths["root"], label="corpus directory")
    _require_private_directory(paths["audio"], label="audio directory")
    _require_private_regular(paths["manifest"], label="private manifest")
    try:
        clips = asr_benchmark.load_manifest(
            paths["manifest"], require_explicit_ids=True
        )
    except (OSError, TypeError, ValueError) as exc:
        raise CorpusAcquisitionError(
            f"private manifest failed validation ({type(exc).__name__})"
        ) from None
    return {"paths": paths, "clips": _validate_workspace_shape(clips, paths["root"])}


def _load_workspace(directory: object) -> Dict[str, Any]:
    paths = _workspace_paths(directory)
    with _workspace_lock(paths, exclusive=False):
        return _load_workspace_unlocked(paths)


def initialize_workspace(directory: object) -> Dict[str, object]:
    """Create or safely resume one private exact-36-clip workspace."""
    paths = _workspace_paths(directory)
    root = paths["root"]
    if not root.exists():
        parent = root.parent
        if not parent.exists():
            raise CorpusAcquisitionError("corpus directory parent must already exist")
        _reject_symlink_components(parent)
        if not parent.is_dir():
            raise CorpusAcquisitionError("corpus directory parent must be a directory")
        try:
            os.mkdir(str(root), 0o700)
        except FileExistsError:
            pass
    _require_private_directory(root, label="corpus directory")
    with _workspace_lock(paths, exclusive=True):
        entries = [entry for entry in root.iterdir() if entry.name != LOCK_NAME]
        if os.path.lexists(str(paths["manifest"])):
            return _workspace_status_unlocked(paths)
        unexpected = [entry for entry in entries if entry.name != AUDIO_DIRECTORY_NAME]
        if unexpected:
            raise CorpusAcquisitionError(
                "existing corpus directory is nonempty and has no managed manifest"
            )
        if os.path.lexists(str(paths["audio"])):
            _require_private_directory(paths["audio"], label="audio directory")
            try:
                has_audio_entries = next(paths["audio"].iterdir(), None) is not None
            except OSError:
                raise CorpusAcquisitionError(
                    "partial audio directory could not be inspected safely"
                ) from None
            if has_audio_entries:
                raise CorpusAcquisitionError(
                    "partial audio directory is nonempty without a managed manifest"
                )
        else:
            os.mkdir(str(paths["audio"]), 0o700)
        try:
            _atomic_write_manifest(paths["manifest"], _planned_clips(root))
        except BaseException:
            # A post-replace durability error leaves a complete manifest and
            # audio directory that the next init can validate and resume. A
            # pre-commit failure removes only the known-empty managed audio
            # directory; the private lock remains safe to reuse.
            if not os.path.lexists(str(paths["manifest"])):
                try:
                    paths["audio"].rmdir()
                    _fsync_directory(root)
                except OSError:
                    pass
            raise
        return _workspace_status_unlocked(paths)


def _clip_state(clip: Dict[str, object]) -> str:
    audio_path = Path(str(clip["audio"]))
    if not os.path.lexists(str(audio_path)):
        return "not-recorded"
    try:
        _require_private_regular(audio_path, label="clip audio")
        inspected = asr_benchmark._inspect_audio_file(audio_path)
    except Exception:
        return "invalid-audio"
    if inspected.get("sha256") != clip.get("sha256"):
        return "metadata-mismatch"
    if (
        clip.get("consent") != "recorded"
        or clip.get("reference_reviewed") is not True
        or clip.get("accent_authenticity") != "confirmed"
    ):
        return "confirmation-missing"
    if clip.get("privacy_review") != "passed":
        return "privacy-review-pending"
    return "ready"


def _workspace_status_unlocked(paths: Dict[str, Path]) -> Dict[str, object]:
    workspace = _load_workspace_unlocked(paths)
    records = []
    counts: Dict[str, int] = {}
    for clip in workspace["clips"]:
        state = _clip_state(clip)
        counts[state] = counts.get(state, 0) + 1
        records.append(
            {
                "id": clip["id"],
                "accent": clip["accent"],
                "category": clip["category"],
                "code_switch": clip["code_switch"],
                "state": state,
            }
        )
    pending = [record["id"] for record in records if record["state"] != "ready"]
    return {
        "clips": 36,
        "accent_plan": {"AR": 24, "MX": 4, "CO": 4, "CL": 4},
        "states": {name: counts[name] for name in sorted(counts)},
        "ready": not pending,
        "next_clip_id": pending[0] if pending else None,
        "records": records,
    }


def workspace_status(directory: object) -> Dict[str, object]:
    """Return bounded content-free progress for safe resume guidance."""
    paths = _workspace_paths(directory)
    with _workspace_lock(paths, exclusive=False):
        return _workspace_status_unlocked(paths)


def _read_reference_file(path_value: object) -> str:
    path = _external_absolute_path(path_value, label="reference file")
    entry = _require_private_regular(path, label="reference file")
    if entry.st_size > _MAX_REFERENCE_BYTES:
        raise CorpusAcquisitionError("reference file exceeds the fixed size limit")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(str(path), flags)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != entry.st_dev
            or opened.st_ino != entry.st_ino
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) & 0o077
        ):
            raise CorpusAcquisitionError("reference file changed during safe open")
        chunks = []
        remaining = _MAX_REFERENCE_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
    finally:
        os.close(fd)
    if len(encoded) > _MAX_REFERENCE_BYTES:
        raise CorpusAcquisitionError("reference file exceeds the fixed size limit")
    try:
        reference = encoded.decode("utf-8")
    except UnicodeError:
        raise CorpusAcquisitionError(
            "reference file must contain valid UTF-8"
        ) from None
    reference = reference.rstrip("\r\n")
    if len(reference) > asr_benchmark._MAX_REFERENCE_CHARS or "\x00" in reference:
        raise CorpusAcquisitionError("reference text is outside the accepted bounds")
    return reference


def _validate_source(source: object) -> str:
    value = str(source)
    if (
        not value
        or len(value) > _MAX_SOURCE_CHARS
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise CorpusAcquisitionError("PulseAudio source is invalid or too long")
    return value


def _resolve_ffmpeg(executable: object) -> str:
    candidate = shutil.which(str(executable))
    if not candidate:
        raise CorpusAcquisitionError("ffmpeg executable was not found")
    resolved = os.path.realpath(candidate)
    try:
        entry = os.stat(resolved)
    except OSError:
        raise CorpusAcquisitionError("ffmpeg executable is unavailable") from None
    if not stat.S_ISREG(entry.st_mode) or not os.access(resolved, os.X_OK):
        raise CorpusAcquisitionError("ffmpeg executable is not a regular executable")
    return resolved


def _terminate_capture(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass


def _capture_with_ffmpeg(
    source: str, output_path: Path, max_seconds: int, executable: str
) -> None:
    command = [
        executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "pulse",
        "-i",
        source,
        "-t",
        str(max_seconds),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-y",
        str(output_path),
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        raise CorpusAcquisitionError("could not start bounded ffmpeg capture") from None
    try:
        return_code = process.wait(timeout=max_seconds + _CAPTURE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _terminate_capture(process)
        raise CorpusAcquisitionError(
            "ffmpeg capture exceeded its hard deadline"
        ) from None
    except KeyboardInterrupt:
        _terminate_capture(process)
        raise
    # The ffmpeg leader normally exits with no children. Still signal its
    # isolated process group so an unexpected inherited helper cannot outlive
    # the bounded capture after the leader exits.
    _terminate_capture(process)
    if return_code != 0:
        raise CorpusAcquisitionError("ffmpeg capture failed")


def _privatize_captured_file(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError:
        raise CorpusAcquisitionError(
            "capture did not produce a safe regular file"
        ) from None
    try:
        entry = os.fstat(fd)
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_uid != os.getuid()
            or entry.st_nlink != 1
            or entry.st_size <= 0
            or entry.st_size > _MAX_CAPTURE_BYTES
        ):
            raise CorpusAcquisitionError("capture did not produce a safe regular file")
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    finally:
        os.close(fd)


def _private_audio_fingerprint(entry: os.stat_result) -> tuple:
    if (
        not stat.S_ISREG(entry.st_mode)
        or entry.st_uid != os.getuid()
        or entry.st_nlink != 1
        or stat.S_IMODE(entry.st_mode) & 0o077
    ):
        raise CorpusAcquisitionError(
            "clip audio must be current-user-owned private state with one hard link"
        )
    return (
        entry.st_dev,
        entry.st_ino,
        entry.st_uid,
        stat.S_IMODE(entry.st_mode),
        entry.st_nlink,
        entry.st_size,
        getattr(entry, "st_mtime_ns", int(entry.st_mtime * 1_000_000_000)),
        getattr(entry, "st_ctime_ns", int(entry.st_ctime * 1_000_000_000)),
    )


def _inspect_private_audio_file(path_value: object) -> Dict[str, object]:
    """Validate privacy metadata and decode/hash through the exact same fd."""
    path = Path(str(path_value))
    _reject_symlink_components(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError:
        raise CorpusAcquisitionError(
            "clip audio is not an accessible private regular file"
        ) from None
    try:
        before = os.fstat(fd)
        before_fingerprint = _private_audio_fingerprint(before)
        try:
            named_before = os.lstat(str(path))
        except OSError:
            raise CorpusAcquisitionError(
                "clip audio changed during safe open"
            ) from None
        if (named_before.st_dev, named_before.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise CorpusAcquisitionError("clip audio changed during safe open")

        inspected = asr_benchmark._inspect_audio_fd(fd)

        after = os.fstat(fd)
        after_fingerprint = _private_audio_fingerprint(after)
        try:
            named_after = os.lstat(str(path))
        except OSError:
            raise CorpusAcquisitionError(
                "clip audio changed during inspection"
            ) from None
        if after_fingerprint != before_fingerprint or (
            named_after.st_dev,
            named_after.st_ino,
        ) != (after.st_dev, after.st_ino):
            raise CorpusAcquisitionError("clip audio changed during inspection")
        return inspected
    except CorpusAcquisitionError:
        raise
    except (OSError, TypeError, ValueError, RuntimeError):
        raise CorpusAcquisitionError(
            "clip audio could not be validated safely"
        ) from None
    finally:
        os.close(fd)


def _find_clip(clips: Sequence[Dict[str, object]], clip_id: str) -> Dict[str, object]:
    for clip in clips:
        if clip.get("id") == clip_id:
            return clip
    raise CorpusAcquisitionError("clip id is not part of the fixed acquisition plan")


def _rollback_audio_swap(
    output_path: Path, backup_path: Path, *, had_existing: bool
) -> bool:
    """Best-effort namespace rollback; return whether consistency was confirmed."""
    try:
        if os.path.lexists(str(output_path)):
            output_path.unlink()
        if had_existing:
            _require_private_regular(backup_path, label="rollback clip audio")
            os.replace(str(backup_path), str(output_path))
        _fsync_directory(output_path.parent)
    except (OSError, CorpusAcquisitionError):
        return False
    return True


def _finish_committed_audio(backup_path: Path, *, had_existing: bool) -> None:
    """Remove the recoverable old audio only after the manifest commit point."""
    try:
        if had_existing and os.path.lexists(str(backup_path)):
            _require_private_regular(backup_path, label="previous clip audio")
            backup_path.unlink()
        _fsync_directory(backup_path.parent)
    except (OSError, CorpusAcquisitionError):
        raise CorpusAcquisitionError(
            "capture committed but old-audio cleanup durability could not be confirmed"
        ) from None


def record_clip(
    directory: object,
    *,
    clip_id: str,
    source: str,
    reference_file: object,
    consent_confirmed: bool,
    reference_reviewed: bool,
    replace: bool = False,
    speaker_id: Optional[str] = None,
    authentic_accent_confirmed: bool = False,
    environment_label: str = "private-local",
    max_seconds: int = 30,
    ffmpeg: str = "ffmpeg",
    recorder: Optional[Callable[[str, Path, int, str], None]] = None,
    audio_inspector: Optional[Callable[[object], Dict[str, object]]] = None,
) -> Dict[str, object]:
    """Record one clip transactionally; privacy review remains a second step."""
    if consent_confirmed is not True:
        raise CorpusAcquisitionError(
            "explicit recording consent confirmation is required"
        )
    if reference_reviewed is not True:
        raise CorpusAcquisitionError(
            "explicit reference-reviewed confirmation is required"
        )
    if authentic_accent_confirmed is not True:
        raise CorpusAcquisitionError(
            "explicit authentic-accent speaker confirmation is required"
        )
    if (
        isinstance(max_seconds, bool)
        or not 1 <= int(max_seconds) <= _MAX_RECORD_SECONDS
    ):
        raise CorpusAcquisitionError(
            "record duration must be between 1 and 120 seconds"
        )
    safe_clip_id = _validate_token(clip_id, label="clip id")
    safe_source = _validate_source(source)
    if not asr_benchmark._SAFE_LABEL_RE.fullmatch(str(environment_label)):
        raise CorpusAcquisitionError(
            "recording environment must be a short label token"
        )
    reference = _read_reference_file(reference_file)
    paths = _workspace_paths(directory)
    with _workspace_lock(paths, exclusive=True):
        workspace = _load_workspace_unlocked(paths)
        clip = _find_clip(workspace["clips"], safe_clip_id)
        if speaker_id is None:
            raise CorpusAcquisitionError(
                "a per-recording pseudonymous speaker id is required"
            )
        pseudonym = _validate_speaker_id(speaker_id, accent=clip.get("accent"))
        for other in workspace["clips"]:
            if other.get("speaker_id") == pseudonym and other.get("accent") != clip.get(
                "accent"
            ):
                raise CorpusAcquisitionError(
                    "a pseudonymous speaker id must not be reused across accents"
                )
        if clip["category"] == "silence":
            if reference.strip():
                raise CorpusAcquisitionError(
                    "silence clips require an empty reviewed reference"
                )
        elif not reference.strip():
            raise CorpusAcquisitionError(
                "non-silence clips require a reviewed reference"
            )

        output_path = Path(str(clip["audio"]))
        if os.path.lexists(str(output_path)) or clip.get("sha256") != "pending":
            if not replace:
                raise CorpusAcquisitionError(
                    "clip already exists; use --replace to re-record"
                )
            _require_private_regular(output_path, label="existing clip audio")
            try:
                old_inspected = asr_benchmark._inspect_audio_file(output_path)
            except Exception:
                raise CorpusAcquisitionError(
                    "existing clip audio is unsafe or corrupt"
                ) from None
            if old_inspected.get("sha256") != clip.get("sha256"):
                raise CorpusAcquisitionError(
                    "existing clip checksum does not match the manifest"
                )

        capture = recorder or _capture_with_ffmpeg
        executable = _resolve_ffmpeg(ffmpeg) if recorder is None else str(ffmpeg)
        temp_path = output_path.parent / (f".{safe_clip_id}.{secrets.token_hex(8)}.wav")
        backup_path = output_path.parent / (
            f".{safe_clip_id}.{secrets.token_hex(8)}.bak"
        )
        try:
            capture(safe_source, temp_path, int(max_seconds), executable)
            _privatize_captured_file(temp_path)
            _require_private_regular(temp_path, label="captured audio")
            inspect = audio_inspector or asr_benchmark._inspect_audio_file
            inspected = inspect(temp_path)
            if inspected.get("sample_rate") != 16000 or inspected.get("channels") != 1:
                raise CorpusAcquisitionError("capture is not a 16 kHz mono WAV")
            audio_format = str(inspected.get("format", "")).upper()
            if audio_format not in ("WAV", "WAVEX"):
                raise CorpusAcquisitionError("capture is not a 16 kHz mono WAV")
            duration = float(inspected.get("duration_s", 0.0))
            if (
                not 0.0
                < duration
                <= min(float(max_seconds) + 0.25, asr_benchmark._MAX_AUDIO_DURATION_S)
            ):
                raise CorpusAcquisitionError(
                    "captured audio duration is outside the bound"
                )
            digest = str(inspected.get("sha256", ""))
            if not asr_benchmark._SHA256_RE.fullmatch(digest):
                raise CorpusAcquisitionError("captured audio checksum is invalid")
            if any(
                other.get("id") != safe_clip_id and other.get("sha256") == digest
                for other in workspace["clips"]
            ):
                raise CorpusAcquisitionError("capture duplicates another clip checksum")

            updated = [dict(item) for item in workspace["clips"]]
            target = _find_clip(updated, safe_clip_id)
            target.update(
                {
                    "reference": reference,
                    "reference_reviewed": True,
                    "speaker_id": pseudonym,
                    "accent_authenticity": "confirmed",
                    "consent": "recorded",
                    "privacy_review": "pending",
                    "sha256": digest,
                    "sample_rate": 16000,
                    "channels": 1,
                    "duration_s": round(duration, 6),
                    "capture_device": safe_source,
                    "recording_environment": str(environment_label),
                }
            )

            had_existing = os.path.lexists(str(output_path))
            try:
                if had_existing:
                    os.replace(str(output_path), str(backup_path))
                os.replace(str(temp_path), str(output_path))
                _fsync_directory(output_path.parent)
            except BaseException:
                if not _rollback_audio_swap(
                    output_path, backup_path, had_existing=had_existing
                ):
                    raise CorpusAcquisitionError(
                        "captured audio commit failed and rollback could not be confirmed"
                    ) from None
                raise CorpusAcquisitionError(
                    "captured audio could not be committed"
                ) from None

            try:
                _atomic_write_manifest(paths["manifest"], updated)
            except _ManifestCommittedError:
                _finish_committed_audio(backup_path, had_existing=had_existing)
                raise
            except BaseException:
                if not _rollback_audio_swap(
                    output_path, backup_path, had_existing=had_existing
                ):
                    raise CorpusAcquisitionError(
                        "manifest update failed and audio rollback could not be confirmed"
                    ) from None
                raise
            _finish_committed_audio(backup_path, had_existing=had_existing)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        return {"id": safe_clip_id, "state": "privacy-review-pending"}


def confirm_privacy_review(
    directory: object, *, clip_id: str, privacy_reviewed: bool
) -> Dict[str, object]:
    """Record the explicit post-capture privacy review for one intact clip."""
    if privacy_reviewed is not True:
        raise CorpusAcquisitionError("explicit post-capture privacy review is required")
    safe_clip_id = _validate_token(clip_id, label="clip id")
    paths = _workspace_paths(directory)
    with _workspace_lock(paths, exclusive=True):
        workspace = _load_workspace_unlocked(paths)
        clip = _find_clip(workspace["clips"], safe_clip_id)
        if _clip_state(clip) not in ("privacy-review-pending", "ready"):
            raise CorpusAcquisitionError(
                "clip must have an intact reviewed capture first"
            )
        updated = [dict(item) for item in workspace["clips"]]
        _find_clip(updated, safe_clip_id)["privacy_review"] = "passed"
        _atomic_write_manifest(paths["manifest"], updated)
        return {"id": safe_clip_id, "state": "ready"}


def validate_workspace(directory: object) -> Dict[str, object]:
    """Run the same strict corpus-only preflight used by ``--validate-only``."""
    paths = _workspace_paths(directory)
    with _workspace_lock(paths, exclusive=False):
        workspace = _load_workspace_unlocked(paths)
        return asr_benchmark.validate_accepted_corpus(
            workspace["clips"],
            manifest_path=paths["manifest"],
            audio_inspector=_inspect_private_audio_file,
        )


def _print_status(status: Dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(status, indent=2, sort_keys=True))
        return
    print("LATAM ASR private corpus acquisition")
    print("Plan: AR=24 MX=4 CO=4 CL=4 (36 total)")
    for state, count in status["states"].items():
        print(f"{state}: {count}")
    if status["ready"]:
        print("Ready for: lst-asr-corpus validate --directory <private-directory>")
    else:
        print(f"Next clip: {status['next_clip_id']}")


def _build_parser() -> argparse.ArgumentParser:
    parser = _RedactingArgumentParser(
        prog="lst-asr-corpus",
        description="Acquire the private exact-36-clip LATAM ASR corpus safely.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create or resume a private corpus")
    init.add_argument("--directory", required=True)

    status = subparsers.add_parser("status", help="show content-free progress")
    status.add_argument("--directory", required=True)
    status.add_argument("--json", action="store_true")

    record = subparsers.add_parser("record", help="record one bounded 16 kHz mono WAV")
    record.add_argument("--directory", required=True)
    record.add_argument("--id", required=True, dest="clip_id")
    record.add_argument("--source", required=True, help="PulseAudio source name")
    record.add_argument(
        "--reference-file",
        required=True,
        help="private 0600 UTF-8 file; its text is never printed",
    )
    record.add_argument(
        "--speaker-id",
        required=True,
        help="pseudonymous token only; never provide a person's name",
    )
    record.add_argument("--environment-label", default="private-local")
    record.add_argument("--max-seconds", type=int, default=30)
    record.add_argument("--ffmpeg", default="ffmpeg")
    record.add_argument("--consent-confirmed", action="store_true")
    record.add_argument("--reference-reviewed", action="store_true")
    record.add_argument("--authentic-accent-confirmed", action="store_true")
    record.add_argument("--replace", action="store_true")

    review = subparsers.add_parser(
        "review", help="confirm post-capture privacy review for one clip"
    )
    review.add_argument("--directory", required=True)
    review.add_argument("--id", required=True, dest="clip_id")
    review.add_argument("--privacy-reviewed", action="store_true")

    validate = subparsers.add_parser(
        "validate", help="hand the manifest to the strict --validate-only contract"
    )
    validate.add_argument("--directory", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize_workspace(args.directory)
            _print_status(result, as_json=False)
        elif args.command == "status":
            _print_status(workspace_status(args.directory), as_json=args.json)
        elif args.command == "record":
            result = record_clip(
                args.directory,
                clip_id=args.clip_id,
                source=args.source,
                reference_file=args.reference_file,
                consent_confirmed=args.consent_confirmed,
                reference_reviewed=args.reference_reviewed,
                authentic_accent_confirmed=args.authentic_accent_confirmed,
                replace=args.replace,
                speaker_id=args.speaker_id,
                environment_label=args.environment_label,
                max_seconds=args.max_seconds,
                ffmpeg=args.ffmpeg,
            )
            print(json.dumps(result, sort_keys=True))
            print(
                "Review the private audio, then run the review command with --privacy-reviewed."
            )
        elif args.command == "review":
            print(
                json.dumps(
                    confirm_privacy_review(
                        args.directory,
                        clip_id=args.clip_id,
                        privacy_reviewed=args.privacy_reviewed,
                    ),
                    sort_keys=True,
                )
            )
        else:
            print(
                json.dumps(validate_workspace(args.directory), indent=2, sort_keys=True)
            )
    except CorpusAcquisitionError as exc:
        parser.error(str(exc))
    except (OSError, TypeError, ValueError):
        parser.error("private corpus operation failed safely")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
