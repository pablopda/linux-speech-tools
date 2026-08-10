#!/usr/bin/env python3
"""Reproducible side-by-side ASR benchmark for the LATAM-Spanish gate.

The benchmark reports *corpus* WER (total edit distance divided by total
reference words), not an arithmetic mean of per-clip WERs.  It also keeps only
privacy-safe clip metadata in its structured results and refuses to describe a
multi-engine comparison as comparable unless every requested engine scores the
same complete manifest.

Audio loading and ASR backends are imported lazily.  The pure metric, manifest,
report, and orchestration paths are unit-testable with injected fakes.

Usage::

    uv run python -m src.utils.asr_benchmark --manifest clips.json \
        --engines faster-whisper,parakeet --model small --language es \
        --warmup-runs 1 --repetitions 3

Manifest format (JSON list)::

    [{"id": "ar-001", "audio": "/private/ar-001.wav",
      "reference": "hola que tal", "accent": "AR",
      "category": "code-switch", "code_switch": true}]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import stat
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_PUNCT_RE = re.compile(r"[^\w']+", re.UNICODE)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_SPEAKER_ID_RE = re.compile(r"^speaker-(ar|mx|co|cl)-[0-9]{2,3}$")
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_REPORT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
_SAFE_REPORT_TEXT_RE = re.compile(r"^[A-Za-z0-9+-][A-Za-z0-9 .,_:+();='-]*$")
_REDACTED_REPORT_RE = re.compile(r"^\[redacted-sha256:[0-9a-f]{16}\]$")
_REPORT_VALUE_LIMIT = 256
_REPORT_LIST_LIMIT = 32
_REPORT_MAPPING_LIMIT = 64
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_REFERENCE_CHARS = 20_000
_MAX_PRIVATE_METADATA_CHARS = 256
_MAX_AUDIO_BYTES = 256 * 1024 * 1024
_MAX_AUDIO_DURATION_S = 300.0
_ACCEPTED_CORPUS_SIZE = 36
_REQUIRED_ACCENT_COUNTS = {"AR": 24, "MX": 4, "CO": 4, "CL": 4}
_REQUIRED_CATEGORIES = {
    "everyday",
    "programming",
    "filename-dev-term",
    "short-command",
    "long-prompt",
    "punctuation-heavy",
    "silence",
    "interrupted",
}
_PROVIDER_BACKENDS = {"ctranslate2", "onnxruntime", "unknown"}
_OPERATIONAL_PROVIDER_LABELS = {
    "cpu",
    "cuda",
    "CPUExecutionProvider",
    "CUDAExecutionProvider",
    "TensorrtExecutionProvider",
    "NvTensorRtRtxExecutionProvider",
    "AzureExecutionProvider",
    "CoreMLExecutionProvider",
    "DmlExecutionProvider",
    "WebGpuExecutionProvider",
}
_PROVIDER_STATUSES = {
    "configured-cpu",
    "configured-cuda",
    "verified-cpu",
    "verified-cuda",
    "verified-cpu-fallback",
    "mixed-provider-fallback",
    "provider-unverified",
    "not-reported",
    "evidence-error",
    "invalid-evidence",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TECHNICAL_CATEGORY_MARKERS = (
    "filename",
    "file-name",
    "repository",
    "repo-name",
    "developer",
    "dev-term",
    "programming",
)


def _read_manifest_bytes(path: object) -> bytes:
    """Read a bounded regular manifest without following a final symlink."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise ValueError(
            "manifest must be a readable regular file, not a symlink or special file"
        ) from exc
    try:
        entry = os.fstat(fd)
        if not stat.S_ISREG(entry.st_mode):
            raise ValueError("manifest must be a regular file")
        if entry.st_size > _MAX_MANIFEST_BYTES:
            raise ValueError("manifest exceeds the fixed size limit")
        chunks = []
        remaining = _MAX_MANIFEST_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        if len(encoded) > _MAX_MANIFEST_BYTES:
            raise ValueError("manifest exceeds the fixed size limit")
        return encoded
    finally:
        os.close(fd)


def normalize_for_wer(text: str) -> List[str]:
    """Lowercase, strip punctuation, and split into a word list."""
    return _PUNCT_RE.sub(" ", (text or "").lower()).split()


def _edit_distance(ref: Sequence[str], hyp: Sequence[str]) -> int:
    """Levenshtein distance over token sequences."""
    m, n = len(ref), len(hyp)
    if m == 0:
        return n
    if n == 0:
        return m
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        ref_tok = ref[i - 1]
        for j in range(1, n + 1):
            cost = 0 if ref_tok == hyp[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[n]


def error_counts(reference: str, hypothesis: str) -> Tuple[int, int]:
    """Return ``(edit_distance, reference_word_count)`` for corpus WER."""
    ref = normalize_for_wer(reference)
    hyp = normalize_for_wer(hypothesis)
    return _edit_distance(ref, hyp), len(ref)


def _wer_from_counts(edits: int, reference_words: int) -> float:
    """Calculate WER while retaining the historical empty-reference policy."""
    if reference_words == 0:
        return 0.0 if edits == 0 else 1.0
    return edits / reference_words


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Return single-utterance WER; corpus reports aggregate raw counts."""
    edits, reference_words = error_counts(reference, hypothesis)
    return _wer_from_counts(edits, reference_words)


def _validate_privacy_safe_metadata(clip: Dict[str, object], index: int) -> None:
    clip_id = clip["id"]
    if not isinstance(clip_id, str) or not _SAFE_ID_RE.fullmatch(clip_id):
        raise ValueError(
            f"clip {index} id must be a privacy-safe ASCII token (letters, digits, dot, underscore, hyphen)"
        )
    for field in ("accent", "category"):
        value = clip.get(field)
        if value is not None and (
            not isinstance(value, str) or not _SAFE_LABEL_RE.fullmatch(value)
        ):
            raise ValueError(f"clip {index} {field} must be a short privacy-safe label")
    if "code_switch" in clip and not isinstance(clip["code_switch"], bool):
        raise ValueError(f"clip {index} code_switch must be true or false")


def _update_report_digest(digest: Any, value: object, depth: int = 0) -> None:
    """Hash arbitrary evidence without relying on an unsafe string conversion."""
    if depth > 8:
        digest.update(b"depth-limit")
        return
    if value is None:
        digest.update(b"none")
        return
    if isinstance(value, bool):
        digest.update(b"bool:true" if value else b"bool:false")
        return
    if isinstance(value, int):
        try:
            integer = int(value)
        except Exception:
            digest.update(b"int:conversion-failed")
            return
        magnitude = abs(integer)
        size = max(1, (magnitude.bit_length() + 7) // 8)
        digest.update(b"int:-" if integer < 0 else b"int:+")
        digest.update(size.to_bytes(8, "big"))
        digest.update(magnitude.to_bytes(size, "big"))
        return
    if isinstance(value, float):
        digest.update(b"float:")
        try:
            rendered_float = float(value).hex()
        except Exception:
            rendered_float = "conversion-failed"
        digest.update(rendered_float.encode("ascii"))
        return
    if isinstance(value, str):
        encoded = value.encode("utf-8", errors="replace")
        digest.update(b"str:")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        return
    if isinstance(value, bytes):
        digest.update(b"bytes:")
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
        return
    if isinstance(value, (list, tuple)):
        digest.update(b"list:" if isinstance(value, list) else b"tuple:")
        digest.update(len(value).to_bytes(8, "big"))
        for item in value:
            _update_report_digest(digest, item, depth + 1)
        return
    if isinstance(value, dict):
        digest.update(b"dict:")
        digest.update(len(value).to_bytes(8, "big"))
        for key, item in value.items():
            _update_report_digest(digest, key, depth + 1)
            _update_report_digest(digest, item, depth + 1)
        return

    type_name = f"{type(value).__module__}.{type(value).__qualname__}"
    digest.update(b"object:")
    digest.update(type_name.encode("utf-8", errors="replace"))
    try:
        rendered = repr(value)
    except Exception:
        rendered = "conversion-failed"
    digest.update(rendered.encode("utf-8", errors="replace"))


def _redacted_report_value(value: object) -> str:
    """Return a stable comparison token without exposing the original value."""
    digest = hashlib.sha256()
    _update_report_digest(digest, value)
    token = digest.hexdigest()[:16]
    return f"[redacted-sha256:{token}]"


def _privacy_safe_report_text(value: object) -> str:
    """Project one scalar to bounded, single-line, Markdown-safe evidence."""
    try:
        text = str(value)
    except Exception:
        return _redacted_report_value(value)
    if _REDACTED_REPORT_RE.fullmatch(text):
        return text
    if (
        not text
        or len(text) > _REPORT_VALUE_LIMIT
        or not _SAFE_REPORT_TEXT_RE.fullmatch(text)
    ):
        return _redacted_report_value(text)
    return text


def _privacy_safe_report_value(value: object) -> object:
    if isinstance(value, (list, tuple)):
        if len(value) > _REPORT_LIST_LIMIT:
            return _redacted_report_value(value)
        return [
            _redacted_report_value(item)
            if isinstance(item, (list, tuple, dict))
            else _privacy_safe_report_value(item)
            for item in value
        ]
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        try:
            text = str(int(value))
        except Exception:
            return _redacted_report_value(value)
        return (
            text if len(text) <= _REPORT_VALUE_LIMIT else _redacted_report_value(value)
        )
    if isinstance(value, float):
        try:
            number = float(value)
            text = repr(number)
        except Exception:
            return _redacted_report_value(value)
        if not math.isfinite(number):
            return _redacted_report_value(value)
        return (
            text if len(text) <= _REPORT_VALUE_LIMIT else _redacted_report_value(value)
        )
    return _privacy_safe_report_text(value)


def _privacy_safe_environment(environment: Dict[str, object]) -> Dict[str, object]:
    projected: Dict[str, object] = {}
    items = list(environment.items())
    for index, (raw_key, raw_value) in enumerate(items):
        if index >= _REPORT_MAPPING_LIMIT:
            overflow_key = "additional-fields"
            suffix = 2
            while overflow_key in projected:
                overflow_key = f"additional-fields-{suffix}"
                suffix += 1
            projected[overflow_key] = _redacted_report_value(items[index:])
            break
        if isinstance(raw_key, str) and _SAFE_REPORT_KEY_RE.fullmatch(raw_key):
            key = raw_key
        else:
            key = (
                "field-"
                + hashlib.sha256(
                    _redacted_report_value(raw_key).encode("ascii")
                ).hexdigest()[:16]
            )
        base_key = key
        suffix = 2
        while key in projected:
            key = f"{base_key[:60]}-{suffix}"
            suffix += 1
        projected[key] = _privacy_safe_report_value(raw_value)
    return projected


def load_manifest(
    path: object, *, require_explicit_ids: bool = False
) -> List[Dict[str, object]]:
    """Load and minimally validate the private JSON manifest.

    ``audio`` and ``reference`` are the only engine requirements.  A stable,
    pseudonymous ``id`` is retained for cross-engine comparison; when omitted it
    is generated from manifest order and never derived from the private path.
    Additional consent/privacy acceptance remains a human gate documented in
    the canonical benchmark report.
    """
    encoded = _read_manifest_bytes(path)
    try:
        raw = encoded.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("manifest must be valid UTF-8 JSON") from exc
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("manifest must be a JSON list of clip objects")
    if not data:
        raise ValueError("manifest must contain at least one clip")
    clips: List[Dict[str, object]] = []
    seen_ids = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict) or "audio" not in item or "reference" not in item:
            raise ValueError(f"clip {index} must have 'audio' and 'reference' fields")
        if require_explicit_ids and "id" not in item:
            raise ValueError(f"clip {index} requires an explicit pseudonymous id")
        clip = dict(item)
        clip_id = clip["id"] if "id" in clip else f"clip-{index + 1:03d}"
        clip["id"] = clip_id
        _validate_privacy_safe_metadata(clip, index)
        if clip_id in seen_ids:
            raise ValueError(f"duplicate clip id: {clip_id!r}")
        seen_ids.add(clip_id)
        clips.append(clip)
    return clips


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(parent))) == str(parent)
    except ValueError:
        return False


def _source_checkout_root() -> Optional[Path]:
    candidate = Path(__file__).resolve().parents[2]
    return candidate if (candidate / ".git").is_dir() else None


def _private_metadata_present(value: object) -> bool:
    """Validate private human metadata without copying it into diagnostics."""
    if not isinstance(value, str):
        return False
    if not value.strip() or len(value) > _MAX_PRIVATE_METADATA_CHARS:
        return False
    return all(character >= " " and character not in "\x7f" for character in value)


def _inspect_audio_fd(fd: int) -> Dict[str, object]:
    """Hash and decode bounded audio through an already-pinned descriptor."""
    entry = os.fstat(fd)
    if not stat.S_ISREG(entry.st_mode):
        raise ValueError("audio is not a regular file")
    if entry.st_size <= 0 or entry.st_size > _MAX_AUDIO_BYTES:
        raise ValueError("audio size is outside the accepted bounds")
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total_bytes = 0
    while total_bytes <= _MAX_AUDIO_BYTES:
        chunk = os.read(
            fd,
            min(1024 * 1024, _MAX_AUDIO_BYTES + 1 - total_bytes),
        )
        if not chunk:
            break
        digest.update(chunk)
        total_bytes += len(chunk)
    if total_bytes > _MAX_AUDIO_BYTES:
        raise ValueError("audio size is outside the accepted bounds")
    os.lseek(fd, 0, os.SEEK_SET)

    try:
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError("soundfile is required for corpus validation") from exc
    probe_fd = os.dup(fd)
    try:
        with sf.SoundFile(probe_fd) as audio:
            frames = int(audio.frames)
            sample_rate = int(audio.samplerate)
            channels = int(audio.channels)
            audio_format = str(audio.format or "unknown")
    except Exception as exc:
        raise ValueError("audio could not be decoded") from exc
    finally:
        try:
            os.close(probe_fd)
        except OSError:
            pass

    if frames <= 0 or sample_rate <= 0 or channels <= 0:
        raise ValueError("audio contains no valid frames")
    duration_s = frames / float(sample_rate)
    if duration_s <= 0.0 or duration_s > _MAX_AUDIO_DURATION_S:
        raise ValueError("audio duration is outside the accepted bounds")
    return {
        "sha256": digest.hexdigest(),
        "sample_rate": sample_rate,
        "channels": channels,
        "frames": frames,
        "duration_s": duration_s,
        "format": audio_format,
    }


def _inspect_audio_file(path: object) -> Dict[str, object]:
    """Hash and inspect one bounded regular audio file through a pinned fd."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise ValueError("audio is not a readable regular file") from exc
    try:
        return _inspect_audio_fd(fd)
    finally:
        os.close(fd)


def validate_accepted_corpus(
    clips: Sequence[Dict[str, object]],
    *,
    manifest_path: Optional[object] = None,
    audio_inspector: Optional[Callable[[object], Dict[str, object]]] = None,
) -> Dict[str, object]:
    """Validate the private, exact 36-clip AR-first acceptance contract.

    The returned summary is aggregate-safe. References, paths, speaker IDs,
    capture details, and per-clip audio metadata remain in the private manifest.
    No ASR engine is constructed by this preflight.
    """
    if len(clips) != _ACCEPTED_CORPUS_SIZE:
        raise ValueError("accepted corpus must contain exactly 36 clips")

    checkout_root = _source_checkout_root()
    manifest_digest = None
    if manifest_path is not None:
        manifest = Path(str(manifest_path)).expanduser()
        if not manifest.is_absolute():
            raise ValueError("accepted corpus manifest path must be absolute")
        resolved_manifest = Path(os.path.realpath(str(manifest)))
        if checkout_root is not None and _path_is_within(
            resolved_manifest, checkout_root
        ):
            raise ValueError(
                "accepted corpus manifest must remain outside the repository"
            )
        manifest_digest = hashlib.sha256(_read_manifest_bytes(manifest)).hexdigest()

    inspect_audio = audio_inspector or _inspect_audio_file
    accent_counts = {accent: 0 for accent in _REQUIRED_ACCENT_COUNTS}
    category_counts: Dict[str, int] = {}
    code_switch_clips = 0
    speaker_ids = set()
    speaker_accents: Dict[str, str] = {}
    audio_digests = set()
    total_duration_s = 0.0
    sample_rates = set()
    channel_counts = set()
    audio_formats = set()

    for index, clip in enumerate(clips):
        clip_id = str(clip.get("id", f"clip-{index + 1:03d}"))
        _validate_privacy_safe_metadata(clip, index)
        accent = clip.get("accent")
        if accent not in _REQUIRED_ACCENT_COUNTS:
            raise ValueError(f"clip {clip_id} has an unsupported benchmark accent")
        accent_counts[str(accent)] += 1

        category = clip.get("category")
        if not isinstance(category, str) or not _SAFE_LABEL_RE.fullmatch(category):
            raise ValueError(f"clip {clip_id} category must be a privacy-safe label")
        category_counts[category] = category_counts.get(category, 0) + 1
        if "code_switch" not in clip:
            raise ValueError(f"clip {clip_id} code-switch review is not recorded")
        if bool(clip.get("code_switch", False)):
            code_switch_clips += 1

        reference = clip.get("reference")
        if not isinstance(reference, str) or len(reference) > _MAX_REFERENCE_CHARS:
            raise ValueError(f"clip {clip_id} reference is missing or too large")
        if "\x00" in reference:
            raise ValueError(f"clip {clip_id} reference contains invalid control data")
        if category == "silence":
            if reference.strip():
                raise ValueError(f"clip {clip_id} silence reference must be empty")
        elif not reference.strip():
            raise ValueError(f"clip {clip_id} requires a nonempty reviewed reference")
        if clip.get("reference_reviewed") is not True:
            raise ValueError(f"clip {clip_id} reference review is not recorded")
        if clip.get("consent") != "recorded":
            raise ValueError(f"clip {clip_id} consent is not recorded")
        if clip.get("privacy_review") != "passed":
            raise ValueError(f"clip {clip_id} privacy review has not passed")

        speaker_id = clip.get("speaker_id")
        if not isinstance(speaker_id, str) or not _SAFE_SPEAKER_ID_RE.fullmatch(
            speaker_id
        ):
            raise ValueError(
                f"clip {clip_id} speaker id must be an accent-scoped pseudonym"
            )
        if clip.get("accent_authenticity") != "confirmed":
            raise ValueError(
                f"clip {clip_id} authentic-accent confirmation is not recorded"
            )
        prior_accent = speaker_accents.setdefault(speaker_id, str(accent))
        if prior_accent != accent:
            raise ValueError(
                f"clip {clip_id} speaker id must not span benchmark accents"
            )
        if speaker_id.split("-")[1].upper() != accent:
            raise ValueError(
                f"clip {clip_id} speaker pseudonym must match its benchmark accent"
            )
        speaker_ids.add(speaker_id)
        for field in ("capture_device", "recording_environment"):
            if not _private_metadata_present(clip.get(field)):
                raise ValueError(f"clip {clip_id} is missing bounded {field} metadata")

        expected_digest = clip.get("sha256")
        if not isinstance(expected_digest, str) or not _SHA256_RE.fullmatch(
            expected_digest
        ):
            raise ValueError(f"clip {clip_id} requires a lowercase SHA-256 checksum")
        audio_path = clip.get("audio")
        if not isinstance(audio_path, str) or not Path(audio_path).is_absolute():
            raise ValueError(f"clip {clip_id} audio path must be absolute")
        resolved_audio = Path(os.path.realpath(audio_path))
        if checkout_root is not None and _path_is_within(resolved_audio, checkout_root):
            raise ValueError(f"clip {clip_id} audio must remain outside the repository")
        try:
            inspected = inspect_audio(audio_path)
        except Exception as exc:
            raise ValueError(
                f"clip {clip_id} audio validation failed ({type(exc).__name__})"
            ) from None
        actual_digest = inspected.get("sha256")
        if actual_digest != expected_digest:
            raise ValueError(f"clip {clip_id} audio checksum does not match")
        if actual_digest in audio_digests:
            raise ValueError(f"clip {clip_id} duplicates another audio checksum")
        audio_digests.add(actual_digest)

        expected_sample_rate = clip.get("sample_rate")
        expected_channels = clip.get("channels")
        expected_duration = clip.get("duration_s")
        if (
            not isinstance(expected_sample_rate, int)
            or isinstance(expected_sample_rate, bool)
            or expected_sample_rate != inspected.get("sample_rate")
        ):
            raise ValueError(f"clip {clip_id} sample rate metadata does not match")
        if (
            not isinstance(expected_channels, int)
            or isinstance(expected_channels, bool)
            or expected_channels != inspected.get("channels")
        ):
            raise ValueError(f"clip {clip_id} channel metadata does not match")
        if (
            not isinstance(expected_duration, (int, float))
            or isinstance(expected_duration, bool)
            or not math.isfinite(float(expected_duration))
            or not math.isclose(
                float(expected_duration),
                float(inspected.get("duration_s", -1.0)),
                rel_tol=0.005,
                abs_tol=0.05,
            )
        ):
            raise ValueError(f"clip {clip_id} duration metadata does not match")

        duration = float(inspected["duration_s"])
        total_duration_s += duration
        sample_rates.add(int(inspected["sample_rate"]))
        channel_counts.add(int(inspected["channels"]))
        audio_format = str(inspected.get("format", "unknown"))
        audio_formats.add(
            audio_format if _SAFE_LABEL_RE.fullmatch(audio_format) else "other"
        )

    if accent_counts != _REQUIRED_ACCENT_COUNTS:
        raise ValueError("accepted corpus must contain exactly AR=24, MX=4, CO=4, CL=4")
    missing_categories = sorted(_REQUIRED_CATEGORIES - set(category_counts))
    if missing_categories:
        raise ValueError(
            "accepted corpus is missing required category coverage: "
            + ", ".join(missing_categories)
        )
    if code_switch_clips == 0:
        raise ValueError("accepted corpus requires at least one code-switch clip")

    return {
        "accepted": True,
        "manifest_sha256": manifest_digest or "not-computed",
        "clips": len(clips),
        "accents": accent_counts,
        "categories": {name: category_counts[name] for name in sorted(category_counts)},
        "code_switch_clips": code_switch_clips,
        "pseudonymous_speakers": len(speaker_ids),
        "total_duration_s": round(total_duration_s, 3),
        "sample_rates": sorted(sample_rates),
        "channel_counts": sorted(channel_counts),
        "audio_formats": sorted(audio_formats),
    }


def load_audio(path):
    """Load an audio file to a float32 mono 16 kHz numpy array (lazy deps)."""
    import numpy as np

    try:
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError(
            "soundfile is required to load audio. Install with `uv sync --extra audio`."
        ) from exc

    audio, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate != 16000 and audio.shape[0] > 0:
        target_len = int(round(audio.shape[0] * 16000 / float(sample_rate)))
        if target_len > 0:
            old_idx = np.linspace(0.0, 1.0, num=audio.shape[0], endpoint=False)
            new_idx = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
            audio = np.interp(new_idx, old_idx, audio).astype(np.float32)
    return np.asarray(audio, dtype=np.float32)


def _default_engine_factory():
    try:
        from ..stt.asr_engine import create_engine
    except ImportError:
        from src.stt.asr_engine import create_engine
    return create_engine


def _safe_clip_metadata(clip: Dict[str, object]) -> Dict[str, object]:
    """Return non-content metadata safe to retain in aggregate run state."""
    return {
        "id": str(clip["id"]),
        "accent": clip.get("accent"),
        "category": clip.get("category"),
        "code_switch": bool(clip.get("code_switch", False)),
    }


def _safe_clip_error(stage: str, exc: Exception) -> str:
    """Describe an error without retaining a private path or transcript."""
    return f"{stage}: {type(exc).__name__}"


def _safe_provider_sets(value: object) -> List[List[str]]:
    if not isinstance(value, (list, tuple)):
        return []
    safe_sets: List[List[str]] = []
    for raw_set in list(value)[:32]:
        if not isinstance(raw_set, (list, tuple)):
            continue
        providers = []
        for raw_provider in list(raw_set)[:8]:
            provider = str(raw_provider)
            providers.append(
                provider if provider in _OPERATIONAL_PROVIDER_LABELS else "other"
            )
        if providers:
            safe_sets.append(providers)
    return safe_sets


def _safe_engine_provider_evidence(
    engine: object, requested_device: object
) -> Dict[str, object]:
    """Project engine provider evidence to a fixed aggregate-safe schema."""
    try:
        from ..stt.asr_engine import provider_evidence_for_engine
    except ImportError:
        from src.stt.asr_engine import provider_evidence_for_engine
    raw = provider_evidence_for_engine(engine)
    backend = str(raw.get("backend", "unknown"))
    if backend not in _PROVIDER_BACKENDS:
        backend = "unknown"
    requested = str(raw.get("requested_device", requested_device))
    if requested not in ("cpu", "cuda"):
        requested = "unknown"
    actual = str(raw.get("actual_device", "unverified"))
    if actual not in ("cpu", "cuda", "mixed", "unverified"):
        actual = "unverified"
    status = str(raw.get("status", "not-reported"))
    if status not in _PROVIDER_STATUSES:
        status = "invalid-evidence"
    core = _safe_provider_sets(raw.get("core_session_providers"))
    auxiliary = _safe_provider_sets(raw.get("auxiliary_session_providers"))
    return {
        "backend": backend,
        "requested_device": requested,
        "actual_device": actual,
        "status": status,
        "operationally_verified": (
            raw.get("operationally_verified") is True
            and actual in ("cpu", "cuda")
            and backend != "unknown"
            and status
            not in (
                "not-reported",
                "evidence-error",
                "invalid-evidence",
            )
        ),
        "cuda_advertised": raw.get("cuda_advertised") is True,
        "cuda_preload_attempted": raw.get("cuda_preload_attempted") is True,
        "cuda_preload_succeeded": raw.get("cuda_preload_succeeded") is True,
        "core_session_count": len(core),
        "core_session_providers": core,
        "auxiliary_session_providers": auxiliary,
    }


def _aggregate(
    per_clip: Sequence[Dict[str, object]],
    predicate: Optional[Callable[[Dict[str, object]], bool]] = None,
) -> Dict[str, object]:
    selected = [
        clip
        for clip in per_clip
        if "edit_distance" in clip and (predicate is None or predicate(clip))
    ]
    edits = sum(int(clip["edit_distance"]) for clip in selected)
    reference_words = sum(int(clip["reference_words"]) for clip in selected)
    return {
        "clips": len(selected),
        "edit_distance": edits,
        "reference_words": reference_words,
        "wer": _wer_from_counts(edits, reference_words) if selected else None,
    }


def _is_technical_category(clip: Dict[str, object]) -> bool:
    category = str(clip.get("category") or "").lower()
    return any(marker in category for marker in _TECHNICAL_CATEGORY_MARKERS)


def _subset_metrics(per_clip: Sequence[Dict[str, object]]) -> Dict[str, object]:
    accents = sorted(
        {str(clip["accent"]) for clip in per_clip if clip.get("accent") is not None}
    )
    return {
        "overall": _aggregate(per_clip),
        "accents": {
            accent: _aggregate(
                per_clip, lambda clip, a=accent: str(clip.get("accent")) == a
            )
            for accent in accents
        },
        "code_switch": _aggregate(per_clip, lambda clip: bool(clip.get("code_switch"))),
        "filename_developer_term": _aggregate(per_clip, _is_technical_category),
    }


def _comparison_state(
    clip_ids: Sequence[str],
    engines: Sequence[str],
    engine_results: Dict[str, object],
) -> Dict[str, object]:
    scored_by_engine = {
        name: sorted(
            str(clip["id"])
            for clip in engine_results.get(name, {}).get("per_clip", [])
            if "edit_distance" in clip
        )
        for name in engines
    }
    unavailable = [
        name
        for name in engines
        if not engine_results.get(name, {}).get("available", False)
    ]
    inconsistent = [
        name
        for name in engines
        if engine_results.get(name, {}).get("inconsistent_hypotheses", 0)
    ]
    expected = sorted(clip_ids)
    sets = [ids for name, ids in scored_by_engine.items() if name not in unavailable]
    identical = bool(sets) and all(ids == sets[0] for ids in sets[1:])
    complete = bool(sets) and all(ids == expected for ids in sets)
    provider_evidence = {
        name: engine_results.get(name, {}).get("provider_evidence", {})
        for name in engines
        if engine_results.get(name, {}).get("available", False)
    }
    provider_reporting = any(
        evidence.get("status") not in (None, "not-reported")
        for evidence in provider_evidence.values()
    )
    provider_comparable = None
    if provider_reporting:
        actual_devices = {
            str(evidence.get("actual_device"))
            for evidence in provider_evidence.values()
        }
        provider_comparable = (
            bool(provider_evidence)
            and all(
                evidence.get("operationally_verified") is True
                for evidence in provider_evidence.values()
            )
            and len(actual_devices) == 1
        )

    if len(engines) < 2:
        reason = "at least two engines are required for comparison"
    elif unavailable:
        reason = "unavailable engine(s): " + ", ".join(unavailable)
    elif not identical:
        reason = "engines scored different clip sets"
    elif not complete:
        reason = (
            "identical scored sets are incomplete relative to the accepted manifest"
        )
    elif provider_comparable is False:
        reason = "engine execution providers were not operationally comparable"
    elif inconsistent:
        reason = "repeated hypotheses were inconsistent for: " + ", ".join(inconsistent)
    else:
        reason = "all engines scored the identical complete manifest"
    return {
        "comparable": (
            len(engines) >= 2
            and not unavailable
            and identical
            and complete
            and provider_comparable is not False
            and not inconsistent
        ),
        "reason": reason,
        "expected_clip_ids": expected,
        "scored_clip_ids_by_engine": scored_by_engine,
        "provider_comparable": provider_comparable,
        "actual_device_by_engine": {
            name: str(evidence.get("actual_device", "unverified"))
            for name, evidence in provider_evidence.items()
        },
    }


def run_benchmark(
    clips: Sequence[Dict[str, object]],
    engines: Sequence[str],
    *,
    model_size: str = "small",
    device: str = "cpu",
    language: Optional[str] = None,
    warmup_runs: int = 0,
    repetitions: int = 1,
    audio_loader: Optional[Callable] = None,
    engine_factory: Optional[Callable] = None,
    environment: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Run each engine over the same manifest and return aggregate-safe state."""
    if warmup_runs < 0:
        raise ValueError("warmup_runs must be nonnegative")
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    engine_names = [str(name).strip() for name in engines]
    if not engine_names or any(not name for name in engine_names):
        raise ValueError("at least one nonempty engine name is required")
    if len(set(engine_names)) != len(engine_names):
        raise ValueError("engine names must be unique")
    if any(not _SAFE_LABEL_RE.fullmatch(name) for name in engine_names):
        raise ValueError("engine names must be privacy-safe ASCII tokens")
    if not clips:
        raise ValueError("benchmark must contain at least one clip")

    prepared = []
    seen_ids = set()
    for index, original in enumerate(clips):
        clip = dict(original)
        clip_id = clip["id"] if "id" in clip else f"clip-{index + 1:03d}"
        clip["id"] = clip_id
        _validate_privacy_safe_metadata(clip, index)
        if clip_id in seen_ids:
            raise ValueError(f"duplicate clip id: {clip_id!r}")
        seen_ids.add(clip_id)
        prepared.append(clip)

    audio_loader = audio_loader or load_audio
    engine_factory = engine_factory or _default_engine_factory()
    parakeet_model = os.environ.get("STT_PARAKEET_MODEL") or "nemo-parakeet-tdt-0.6b-v3"
    raw_quantization = os.environ.get("STT_PARAKEET_QUANTIZATION", "int8")
    normalized_quantization = raw_quantization.strip().lower()
    if normalized_quantization in ("", "none", "fp32", "float32"):
        normalized_quantization = "none"

    results: Dict[str, object] = {
        "clips": len(prepared),
        "manifest_clip_ids": [str(clip["id"]) for clip in prepared],
        "configuration": {
            "model_size": _privacy_safe_report_value(model_size),
            "faster_whisper_model": _privacy_safe_report_value(model_size),
            "parakeet_model": _privacy_safe_report_value(parakeet_model),
            "parakeet_quantization": _privacy_safe_report_value(
                normalized_quantization
            ),
            "device": _privacy_safe_report_value(device),
            "language": _privacy_safe_report_value(language),
            "warmup_runs": _privacy_safe_report_value(warmup_runs),
            "repetitions": _privacy_safe_report_value(repetitions),
        },
        "environment": _privacy_safe_environment(dict(environment or {})),
        "engines": {},
    }

    for name in engine_names:
        try:
            engine = engine_factory(
                name, model_size=model_size, device=device, language=language
            )
        except Exception as exc:
            results["engines"][name] = {
                "available": False,
                "error": _safe_clip_error("engine-init", exc),
            }
            continue

        provider_evidence = _safe_engine_provider_evidence(engine, device)

        if prepared and warmup_runs:
            try:
                warm_audio = audio_loader(prepared[0]["audio"])
                warm_language = language or prepared[0].get("language")
                for _ in range(warmup_runs):
                    engine.transcribe(warm_audio, warm_language)
            except Exception as exc:
                results["engines"][name] = {
                    "available": False,
                    "error": _safe_clip_error("warm-up", exc),
                    "provider_evidence": provider_evidence,
                }
                continue

        per_clip: List[Dict[str, object]] = []
        for clip in prepared:
            record = _safe_clip_metadata(clip)
            try:
                audio = audio_loader(clip["audio"])
            except Exception as exc:
                record["error"] = _safe_clip_error("audio-load", exc)
                per_clip.append(record)
                continue

            hypotheses: List[str] = []
            latencies: List[float] = []
            try:
                for _ in range(repetitions):
                    started = time.monotonic()
                    hypothesis = engine.transcribe(
                        audio, language or clip.get("language")
                    )
                    latencies.append(time.monotonic() - started)
                    hypotheses.append(hypothesis)
            except Exception as exc:
                record["error"] = _safe_clip_error("transcription", exc)
                per_clip.append(record)
                continue

            edits, reference_words = error_counts(str(clip["reference"]), hypotheses[0])
            record.update(
                {
                    "edit_distance": edits,
                    "reference_words": reference_words,
                    "wer": _wer_from_counts(edits, reference_words),
                    "median_latency_s": statistics.median(latencies),
                    # Compatibility alias for callers of the pre-repetition API.
                    "latency_s": statistics.median(latencies),
                    "repetitions": repetitions,
                    "hypothesis_consistent": all(
                        h == hypotheses[0] for h in hypotheses
                    ),
                }
            )
            per_clip.append(record)

        scored = [clip for clip in per_clip if "edit_distance" in clip]
        per_clip_latencies = [float(clip["median_latency_s"]) for clip in scored]
        metrics = _subset_metrics(per_clip)
        results["engines"][name] = {
            "available": True,
            "corpus_wer": metrics["overall"]["wer"],
            # Deprecated compatibility alias. This now contains corpus WER,
            # never the former arithmetic mean of per-clip percentages.
            "mean_wer": metrics["overall"]["wer"],
            "median_latency_s": (
                statistics.median(per_clip_latencies) if per_clip_latencies else None
            ),
            "errors": sum(1 for clip in per_clip if "error" in clip),
            "inconsistent_hypotheses": sum(
                1 for clip in scored if not clip.get("hypothesis_consistent", True)
            ),
            "metrics": metrics,
            "per_clip": per_clip,
            "provider_evidence": _safe_engine_provider_evidence(engine, device),
        }

    results["comparison"] = _comparison_state(
        results["manifest_clip_ids"], engine_names, results["engines"]
    )
    return results


def collect_environment_evidence() -> Dict[str, object]:
    """Collect reproducibility facts without claiming actual GPU execution."""
    evidence: Dict[str, object] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    for distribution in ("faster-whisper", "ctranslate2", "onnx-asr", "onnxruntime"):
        key = distribution.replace("-", "_") + "_version"
        try:
            evidence[key] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            evidence[key] = "not installed"
    try:
        import onnxruntime

        evidence["onnxruntime_available_providers"] = list(
            onnxruntime.get_available_providers()
        )
        evidence["onnxruntime_preload_dlls_available"] = callable(
            getattr(onnxruntime, "preload_dlls", None)
        )
    except Exception:
        evidence["onnxruntime_available_providers"] = "unavailable"
        evidence["onnxruntime_preload_dlls_available"] = False
    evidence["provider_caveat"] = (
        "Available providers do not prove execution; use each engine actual-device "
        "and core-session evidence before claiming GPU results."
    )
    return evidence


def _format_metric(metric: Dict[str, object]) -> Tuple[str, str, str, str]:
    wer = metric.get("wer")
    return (
        str(metric.get("clips", 0)),
        str(metric.get("reference_words", 0)),
        str(metric.get("edit_distance", 0)),
        f"{wer:.3f}" if wer is not None else "–",
    )


def _format_report_cell(value: object) -> str:
    projected = _privacy_safe_report_value(value)
    if isinstance(projected, list):
        return ", ".join(str(item) for item in projected)
    return str(projected)


def _format_provider_sets(value: object) -> str:
    sets = _safe_provider_sets(value)
    if not sets:
        return "none"
    return "; ".join(" + ".join(providers) for providers in sets)


def format_report(results: Dict[str, object]) -> str:
    """Render an aggregate-only Markdown report (no paths or transcripts)."""
    configuration = results.get("configuration", {})
    comparison = results.get("comparison", {})
    lines = [
        "# ASR benchmark (faster-whisper vs Parakeet)",
        "",
        f"Manifest clips: {_format_report_cell(results['clips'])}",
        f"Comparable: {'yes' if comparison.get('comparable') else 'no'} — "
        f"{_format_report_cell(comparison.get('reason', 'comparison state unavailable'))}",
        "",
        "## Run configuration",
        "",
        "| Field | Value |",
        "| --- | --- |",
        "| Requested model | "
        f"{_format_report_cell(configuration.get('model_size', 'not recorded'))} |",
        "| faster-whisper model | "
        f"{_format_report_cell(configuration.get('faster_whisper_model', configuration.get('model_size', 'not recorded')))} |",
        "| Parakeet model | "
        f"{_format_report_cell(configuration.get('parakeet_model', 'not recorded'))} |",
        "| Parakeet quantization | "
        f"{_format_report_cell(configuration.get('parakeet_quantization', 'not recorded'))} |",
        "| Requested device | "
        f"{_format_report_cell(configuration.get('device', 'not recorded'))} |",
        "| Language | "
        f"{_format_report_cell(configuration.get('language')) if configuration.get('language') else 'per-clip / auto'} |",
        "| Warm-up runs | "
        f"{_format_report_cell(configuration.get('warmup_runs', 'not recorded'))} |",
        "| Measured repetitions per clip | "
        f"{_format_report_cell(configuration.get('repetitions', 'not recorded'))} |",
        "",
        "## Environment and provider evidence",
        "",
        "| Field | Value |",
        "| --- | --- |",
    ]
    environment = _privacy_safe_environment(dict(results.get("environment", {})))
    if environment:
        for key in sorted(environment):
            safe_key = _privacy_safe_report_text(key)
            safe_value = _format_report_cell(environment[key])
            lines.append(f"| {safe_key} | {safe_value} |")
    else:
        lines.append("| Evidence | not collected |")

    lines += [
        "",
        "## Operational provider verification",
        "",
        "Advertised providers are capability hints only. The actual-device field "
        "comes from constructed engine state; Parakeet recursively inspects its "
        "core ONNX inference sessions after loading.",
        "",
        "| Engine | Requested | Actual | Verification | Core session providers | Auxiliary session providers |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, data in results["engines"].items():
        evidence = data.get("provider_evidence", {})
        lines.append(
            "| {} | {} | {} | {} | {} | {} |".format(
                _format_report_cell(name),
                _format_report_cell(evidence.get("requested_device", "unknown")),
                _format_report_cell(evidence.get("actual_device", "unverified")),
                _format_report_cell(evidence.get("status", "not-reported")),
                _format_provider_sets(evidence.get("core_session_providers")),
                _format_provider_sets(evidence.get("auxiliary_session_providers")),
            )
        )

    lines += [
        "",
        "## Overall corpus WER and latency",
        "",
        "| Engine | Scored clips | Reference words | Edit distance | Corpus WER | Median per-clip latency (s) | Status |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    engines = results["engines"]
    for name, data in engines.items():
        safe_name = _format_report_cell(name)
        if not data.get("available"):
            safe_error = _format_report_cell(data.get("error", ""))
            lines.append(
                f"| {safe_name} | – | – | – | – | – | unavailable: {safe_error} |"
            )
            continue
        clips, words, edits, wer = _format_metric(data["metrics"]["overall"])
        latency = data.get("median_latency_s")
        latency_cell = f"{latency:.3f}" if latency is not None else "–"
        errors = data.get("errors", 0)
        inconsistent = data.get("inconsistent_hypotheses", 0)
        if errors:
            status = f"not comparable ({errors} clip error(s))"
        elif inconsistent:
            status = f"not comparable ({inconsistent} inconsistent hypothesis set(s))"
        else:
            status = "ok"
        lines.append(
            f"| {safe_name} | {clips} | {words} | {edits} | {wer} | {latency_cell} | {status} |"
        )

    accents = sorted(
        {
            accent
            for data in engines.values()
            if data.get("available")
            for accent in data["metrics"]["accents"]
        }
    )
    subset_rows = [("Overall", "overall")]
    subset_rows.extend((f"Accent {accent}", ("accents", accent)) for accent in accents)
    subset_rows.extend(
        [
            ("Code-switch", "code_switch"),
            ("Filename / developer term", "filename_developer_term"),
        ]
    )
    lines += [
        "",
        "## Corpus WER by required subset (replaces Mean WER by accent)",
        "",
        "| Engine | Subset | Scored clips | Reference words | Edit distance | Corpus WER |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for name, data in engines.items():
        if not data.get("available"):
            continue
        safe_name = _format_report_cell(name)
        for label, key in subset_rows:
            if isinstance(key, tuple):
                metric = data["metrics"][key[0]].get(key[1], {})
            else:
                metric = data["metrics"][key]
            clips, words, edits, wer = _format_metric(metric)
            safe_label = _format_report_cell(label) if isinstance(key, tuple) else label
            lines.append(
                f"| {safe_name} | {safe_label} | {clips} | {words} | {edits} | {wer} |"
            )

    lines += [
        "",
        "## Scored-set comparability",
        "",
        "| Engine | Scored privacy-safe clip IDs |",
        "| --- | --- |",
    ]
    for name, clip_ids in comparison.get("scored_clip_ids_by_engine", {}).items():
        safe_name = _format_report_cell(name)
        safe_ids = ", ".join(_format_report_cell(clip_id) for clip_id in clip_ids)
        lines.append(f"| {safe_name} | {safe_ids or 'none'} |")
    lines += [
        "",
        "A pass/fail quality decision is invalid unless `Comparable` is `yes`. ",
        "This generated report contains no audio paths, references, hypotheses, or speaker identifiers.",
    ]
    return "\n".join(lines)


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark ASR engines by corpus WER and median per-clip latency."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="private JSON list of {id?, audio, reference, accent?, category?, code_switch?}",
    )
    parser.add_argument(
        "--engines",
        default="faster-whisper,parakeet",
        help="comma-separated engine names (default: faster-whisper,parakeet)",
    )
    parser.add_argument("--model", default="small", help="faster-whisper model size")
    parser.add_argument("--device", default="cpu", help="cpu or cuda")
    parser.add_argument(
        "--language",
        default=None,
        help="force a language code (default: per-clip / engine auto-detect)",
    )
    parser.add_argument(
        "--warmup-runs",
        type=_nonnegative_int,
        default=1,
        help="untimed warm-up transcriptions on the first clip (default: 1)",
    )
    parser.add_argument(
        "--repetitions",
        type=_positive_int,
        default=3,
        help="measured transcriptions per clip for median latency (default: 3)",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help=(
            "validate the private exact 36-clip AR-first corpus and exit "
            "without loading an ASR engine"
        ),
    )
    parser.add_argument(
        "--require-accepted-corpus",
        action="store_true",
        help="enforce the exact consented corpus contract before loading engines",
    )
    parser.add_argument("--output", default=None, help="write aggregate Markdown here")
    args = parser.parse_args(argv)

    try:
        clips = load_manifest(
            args.manifest,
            require_explicit_ids=(args.validate_only or args.require_accepted_corpus),
        )
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    if args.validate_only or args.require_accepted_corpus:
        try:
            acceptance = validate_accepted_corpus(clips, manifest_path=args.manifest)
        except (OSError, TypeError, ValueError) as exc:
            parser.error(str(exc))
        if args.validate_only:
            print(json.dumps(acceptance, indent=2, sort_keys=True))
            return 0
    engines = [name.strip() for name in args.engines.split(",") if name.strip()]
    if not engines:
        parser.error("--engines must contain at least one engine name")
    if len(set(engines)) != len(engines):
        parser.error("--engines must not contain duplicate names")
    results = run_benchmark(
        clips,
        engines,
        model_size=args.model,
        device=args.device,
        language=args.language,
        warmup_runs=args.warmup_runs,
        repetitions=args.repetitions,
        environment=collect_environment_evidence(),
    )
    report = format_report(results)
    print(report)
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
