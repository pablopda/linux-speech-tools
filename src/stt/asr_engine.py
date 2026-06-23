#!/usr/bin/env python3
"""Pluggable ASR backends for the dictation session.

The dictation core (``session.py``) is engine-agnostic at both ends: it captures
16 kHz mono PCM and emits plain text. The one engine-specific seam — turning a
float32 mono 16 kHz utterance into a string — is isolated here behind the
:class:`ASREngine` protocol, so swapping recognizers never touches the VAD loop,
audio capture, status writes, or the output callback.

See ``docs/planning/PLUGGABLE_ASR_AND_PARAKEET.md`` for the design rationale.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import numpy as np

try:
    from .runtime import compute_type_for_device
except ImportError:  # pragma: no cover - direct-script import fallback
    from runtime import compute_type_for_device


DEFAULT_ENGINE = "faster-whisper"
# Accepted aliases per engine, normalized (lowercased) before lookup.
_FASTER_WHISPER_NAMES = {"faster-whisper", "faster_whisper", "whisper"}
_PARAKEET_NAMES = {"parakeet", "parakeet-onnx", "onnx-asr"}
ENGINE_CHOICES = ("faster-whisper", "parakeet")


@runtime_checkable
class ASREngine(Protocol):
    """Turns a mono 16 kHz float32 utterance into text.

    The contract is deliberately tiny: a float32 mono 16 kHz array in, plain
    text out. That is exactly what ``transcribe_buffer()`` already produces
    (``np.concatenate(self.audio_buffer)``) and consumes (a joined string).
    """

    def transcribe(self, audio: np.ndarray, language: Optional[str]) -> str: ...


class FasterWhisperBackend:
    """Default engine. Wraps faster-whisper's ``WhisperModel`` + segment join.

    Owns the CTranslate2-specific compute-type selection so the session never
    has to know about it. ``faster_whisper`` is imported lazily so that (a) the
    parakeet-only install path never requires it and (b) the test suite can fake
    it via ``sys.modules`` at construction time.
    """

    def __init__(self, *, model_size: str = "tiny", device: str = "cpu") -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. Run `uv sync --extra stt`."
            ) from exc
        self.compute_type = compute_type_for_device(device)
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=self.compute_type,
        )

    def transcribe(self, audio: np.ndarray, language: Optional[str]) -> str:
        segments, _ = self.model.transcribe(
            audio,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(
                threshold=0.5,
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
        )
        return " ".join(segment.text.strip() for segment in segments)


def create_engine(
    name: str,
    *,
    model_size: str = "tiny",
    device: str = "cpu",
    language: Optional[str] = None,
) -> ASREngine:
    """Build an :class:`ASREngine` by name.

    ``faster-whisper`` (default) is always available. Unknown names raise
    ``ValueError`` so a typo in ``STT_ENGINE``/``--engine`` fails loudly rather
    than silently falling back.
    """
    engine = (name or DEFAULT_ENGINE).strip().lower()
    if engine in _FASTER_WHISPER_NAMES:
        return FasterWhisperBackend(model_size=model_size, device=device)
    raise ValueError(
        f"unknown STT engine {name!r}. Available: {', '.join(ENGINE_CHOICES)}."
    )
