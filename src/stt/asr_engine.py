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

import os
import sys
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:  # numpy is only referenced in type annotations, which are
    import numpy as np  # strings at runtime (from __future__ import annotations)

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


def _cuda_provider_available() -> bool:
    """True if onnxruntime exposes the CUDA EP (i.e. a GPU runtime is installed)."""
    try:
        import onnxruntime

        return "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    except Exception:
        return False


class ParakeetOnnxBackend:
    """Optional engine: NVIDIA Parakeet TDT 0.6B via onnx-asr (CPU or CUDA).

    Parakeet emits punctuation + capitalization natively and v3 auto-detects
    language, so the session's normalized ``language`` hint is accepted for
    interface parity but unused. The default model is the multilingual v3
    (includes Spanish); ``onnx-asr`` downloads it from Hugging Face on first use.

    Install with ``uv sync --extra stt-parakeet``. Overridable via env:
    ``STT_PARAKEET_MODEL`` (model name) and ``STT_PARAKEET_QUANTIZATION``
    (``int8`` default; ``none``/empty for full precision).
    """

    DEFAULT_MODEL = "nemo-parakeet-tdt-0.6b-v3"
    SAMPLE_RATE = 16000

    def __init__(
        self,
        *,
        model_name: Optional[str] = None,
        device: str = "cpu",
        quantization: Optional[str] = None,
    ) -> None:
        try:
            import onnx_asr
        except ImportError as exc:
            raise RuntimeError(
                "onnx-asr is not installed. Run `uv sync --extra stt-parakeet`."
            ) from exc
        self.model_name = (
            model_name or os.environ.get("STT_PARAKEET_MODEL") or self.DEFAULT_MODEL
        )
        if quantization is None:
            raw = os.environ.get("STT_PARAKEET_QUANTIZATION", "int8").strip().lower()
            quantization = None if raw in ("", "none", "fp32", "float32") else raw
        self.quantization = quantization
        # Only request CUDA when the GPU runtime is actually present; otherwise
        # fall back to CPU with a clear message rather than a noisy onnxruntime
        # warning. The stt-parakeet extra ships CPU onnxruntime; GPU needs
        # onnxruntime-gpu installed separately.
        providers = ["CPUExecutionProvider"]
        if device == "cuda":
            if _cuda_provider_available():
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            else:
                print(
                    "parakeet: --device cuda requested but CUDAExecutionProvider is "
                    "unavailable; install onnxruntime-gpu for GPU. Falling back to CPU.",
                    file=sys.stderr,
                )
        self.model = onnx_asr.load_model(
            self.model_name, quantization=quantization, providers=providers
        )

    def transcribe(self, audio: np.ndarray, language: Optional[str]) -> str:
        # recognize() accepts a float32 mono array directly; default 16 kHz
        # matches our capture, so no temp WAV is needed. v3 auto-detects
        # language, so the hint is intentionally ignored.
        result = self.model.recognize(audio, sample_rate=self.SAMPLE_RATE)
        return (result or "").strip()


def normalize_engine(name: str) -> str:
    """Map an engine name/alias to its canonical form.

    Raises ``ValueError`` on an unknown name so a typo in ``STT_ENGINE`` /
    ``--engine`` fails loudly rather than silently falling back.
    """
    engine = (name or DEFAULT_ENGINE).strip().lower()
    if engine in _FASTER_WHISPER_NAMES:
        return "faster-whisper"
    if engine in _PARAKEET_NAMES:
        return "parakeet"
    raise ValueError(
        f"unknown STT engine {name!r}. Available: {', '.join(ENGINE_CHOICES)}."
    )


def create_engine(
    name: str,
    *,
    model_size: str = "tiny",
    device: str = "cpu",
    language: Optional[str] = None,
) -> ASREngine:
    """Build an :class:`ASREngine` by name (``faster-whisper`` default).

    ``language`` is accepted for interface parity and forward-compatibility but
    is not used at construction time: both built-in backends receive the language
    hint per utterance via :meth:`ASREngine.transcribe` (and Parakeet v3
    auto-detects). It lets callers such as the benchmark pass it uniformly.
    """
    engine = normalize_engine(name)
    if engine == "faster-whisper":
        return FasterWhisperBackend(model_size=model_size, device=device)
    return ParakeetOnnxBackend(device=device)
