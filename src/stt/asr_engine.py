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
from typing import Dict, List, Optional, Protocol, Tuple, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:  # numpy annotations are deferred by ``annotations`` above.
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
_SAFE_PROVIDER_NAMES = {
    "CPUExecutionProvider",
    "CUDAExecutionProvider",
    "TensorrtExecutionProvider",
    "NvTensorRtRtxExecutionProvider",
    "AzureExecutionProvider",
    "CoreMLExecutionProvider",
    "DmlExecutionProvider",
    "WebGpuExecutionProvider",
}


@runtime_checkable
class ASREngine(Protocol):
    """Turns a mono 16 kHz float32 utterance into text.

    The contract is deliberately tiny: a float32 mono 16 kHz array in, plain
    text out. That is exactly what ``transcribe_buffer()`` already produces
    (``np.concatenate(self.audio_buffer)``) and consumes (a joined string).
    """

    def transcribe(
        self,
        audio: np.ndarray,
        language: Optional[str],
        *,
        partial: bool = False,
        initial_prompt: Optional[str] = None,
        hotwords: Optional[str] = None,
    ) -> str: ...


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
        self.requested_device = "cuda" if device == "cuda" else "cpu"
        self.model = WhisperModel(
            model_size,
            device=device,
            compute_type=self.compute_type,
        )
        model_device = str(getattr(self.model, "device", self.requested_device))
        self.actual_device = (
            model_device if model_device in ("cpu", "cuda") else self.requested_device
        )

    def provider_evidence(self) -> Dict[str, object]:
        """Return bounded CTranslate2 device evidence after construction."""
        return {
            "backend": "ctranslate2",
            "requested_device": self.requested_device,
            "actual_device": self.actual_device,
            "status": "configured-{}".format(self.actual_device),
            "operationally_verified": True,
            "core_session_count": 1,
            "core_session_providers": [[self.actual_device]],
            "auxiliary_session_providers": [],
        }

    def transcribe(
        self,
        audio: np.ndarray,
        language: Optional[str],
        *,
        partial: bool = False,
        initial_prompt: Optional[str] = None,
        hotwords: Optional[str] = None,
    ) -> str:
        kwargs = {
            "language": language,
            "beam_size": 1 if partial else 5,
            "vad_filter": not partial,
            "initial_prompt": initial_prompt,
        }
        if not partial:
            kwargs["vad_parameters"] = dict(
                threshold=0.5,
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            )
        if hotwords:
            kwargs["hotwords"] = hotwords
        try:
            segments, _ = self.model.transcribe(audio, **kwargs)
        except TypeError as exc:
            if "hotwords" not in kwargs or "hotwords" not in str(exc):
                raise
            # Older faster-whisper releases do not expose hotword hints.
            kwargs.pop("hotwords")
            segments, _ = self.model.transcribe(audio, **kwargs)
        return " ".join(segment.text.strip() for segment in segments)


def _cuda_provider_available() -> bool:
    """True if ONNX Runtime advertises the CUDA execution provider."""
    try:
        import onnxruntime

        return "CUDAExecutionProvider" in onnxruntime.get_available_providers()
    except Exception:
        return False


def _preload_onnxruntime_cuda() -> Tuple[bool, bool]:
    """Best-effort preload of CUDA/cuDNN libraries bundled with Python wheels."""
    try:
        import onnxruntime
    except Exception:
        return False, False
    preload = getattr(onnxruntime, "preload_dlls", None)
    if not callable(preload):
        return False, False
    try:
        preload()
    except Exception:
        return True, False
    return True, True


def _safe_provider_name(value: object) -> str:
    name = str(value)
    return name if name in _SAFE_PROVIDER_NAMES else "OtherExecutionProvider"


def _onnx_session_provider_records(
    root: object,
) -> List[Tuple[str, Tuple[str, ...]]]:
    """Recursively find bounded ONNX sessions without retaining object details."""
    records: List[Tuple[str, Tuple[str, ...]]] = []
    stack = [(root, "other", 0)]
    seen = set()
    while stack and len(seen) < 512:
        current, scope, depth = stack.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        if depth > 8:
            continue

        get_providers = getattr(current, "get_providers", None)
        if callable(get_providers):
            try:
                raw_providers = list(get_providers())
            except Exception:
                raw_providers = []
            providers = tuple(_safe_provider_name(value) for value in raw_providers[:8])
            records.append((scope, providers or ("OtherExecutionProvider",)))
            continue

        if isinstance(current, dict):
            children = [("item", value) for value in list(current.values())[:128]]
        elif isinstance(current, (list, tuple, set)):
            children = [("item", value) for value in list(current)[:128]]
        else:
            try:
                children = list(vars(current).items())[:128]
            except (TypeError, AttributeError):
                continue
        for raw_name, value in children:
            if value is None or isinstance(
                value, (str, bytes, bytearray, bool, int, float, complex)
            ):
                continue
            module_name = type(value).__module__
            if module_name.startswith("numpy"):
                continue
            name = str(raw_name).lower()
            child_scope = scope
            if name in ("asr", "_asr"):
                child_scope = "core"
            elif (
                "preprocessor" in name
                or "resampler" in name
                or name in ("vad", "_vad")
                or module_name.startswith("onnx_asr.preprocessors")
            ):
                child_scope = "auxiliary"
            stack.append((value, child_scope, depth + 1))
    return records


def _session_device(records: List[Tuple[str, Tuple[str, ...]]]) -> str:
    core = [providers for scope, providers in records if scope == "core"]
    if not core:
        return "unverified"
    primary = [providers[0] for providers in core if providers]
    if len(primary) != len(core):
        return "unverified"
    if all(name == "CUDAExecutionProvider" for name in primary):
        return "cuda"
    if all(name == "CPUExecutionProvider" for name in primary):
        return "cpu"
    return "mixed"


def provider_evidence_for_engine(engine: object) -> Dict[str, object]:
    """Return privacy-safe provider evidence for a constructed engine."""
    getter = getattr(engine, "provider_evidence", None)
    if not callable(getter):
        return {
            "backend": "unknown",
            "requested_device": "unknown",
            "actual_device": "unverified",
            "status": "not-reported",
            "operationally_verified": False,
            "core_session_count": 0,
            "core_session_providers": [],
            "auxiliary_session_providers": [],
        }
    try:
        evidence = getter()
    except Exception:
        return {
            "backend": "unknown",
            "requested_device": "unknown",
            "actual_device": "unverified",
            "status": "evidence-error",
            "operationally_verified": False,
            "core_session_count": 0,
            "core_session_providers": [],
            "auxiliary_session_providers": [],
        }
    return dict(evidence) if isinstance(evidence, dict) else {
        "backend": "unknown",
        "requested_device": "unknown",
        "actual_device": "unverified",
        "status": "invalid-evidence",
        "operationally_verified": False,
        "core_session_count": 0,
        "core_session_providers": [],
        "auxiliary_session_providers": [],
    }


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
        self.requested_device = "cuda" if device == "cuda" else "cpu"
        self.cuda_preload_attempted = False
        self.cuda_preload_succeeded = False
        # Advertised providers are capability hints only. Preload the NVIDIA
        # wheel libraries before creating sessions, then verify the providers
        # on the actual nested onnx-asr sessions after model construction.
        providers = ["CPUExecutionProvider"]
        if self.requested_device == "cuda":
            (
                self.cuda_preload_attempted,
                self.cuda_preload_succeeded,
            ) = _preload_onnxruntime_cuda()
            if _cuda_provider_available():
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            else:
                print(
                    "parakeet: --device cuda requested but CUDAExecutionProvider is "
                    "unavailable; install with `uv sync --extra stt "
                    "--extra stt-parakeet-gpu` for GPU. Falling back to CPU.",
                    file=sys.stderr,
                )
        self.model = onnx_asr.load_model(
            self.model_name, quantization=quantization, providers=providers
        )
        self.requested_providers = tuple(providers)
        self._session_provider_records = _onnx_session_provider_records(self.model)
        self.actual_device = _session_device(self._session_provider_records)
        if self.requested_device == "cuda" and self.actual_device != "cuda":
            print(
                "parakeet: CUDA was requested but the created core ONNX sessions "
                "did not activate it (actual device: {}). Continuing with an "
                "explicitly labeled fallback.".format(self.actual_device),
                file=sys.stderr,
            )

    def provider_evidence(self) -> Dict[str, object]:
        """Report actual nested session providers without paths or model details."""
        core = [
            list(providers)
            for scope, providers in self._session_provider_records
            if scope == "core"
        ]
        auxiliary = [
            list(providers)
            for scope, providers in self._session_provider_records
            if scope != "core"
        ]
        if self.requested_device == "cuda" and self.actual_device == "cuda":
            status = "verified-cuda"
        elif self.actual_device == "cpu":
            status = (
                "verified-cpu-fallback"
                if self.requested_device == "cuda"
                else "verified-cpu"
            )
        elif self.actual_device == "mixed":
            status = "mixed-provider-fallback"
        else:
            status = "provider-unverified"
        return {
            "backend": "onnxruntime",
            "requested_device": self.requested_device,
            "actual_device": self.actual_device,
            "status": status,
            "operationally_verified": self.actual_device in ("cpu", "cuda"),
            "cuda_advertised": "CUDAExecutionProvider" in self.requested_providers,
            "cuda_preload_attempted": self.cuda_preload_attempted,
            "cuda_preload_succeeded": self.cuda_preload_succeeded,
            "core_session_count": len(core),
            "core_session_providers": core,
            "auxiliary_session_providers": auxiliary,
        }

    def transcribe(
        self,
        audio: np.ndarray,
        language: Optional[str],
        *,
        partial: bool = False,
        initial_prompt: Optional[str] = None,
        hotwords: Optional[str] = None,
    ) -> str:
        # recognize() accepts a float32 mono array directly; default 16 kHz
        # matches our capture, so no temp WAV is needed. v3 auto-detects
        # language, so the hint is intentionally ignored.
        import numpy as np

        snapshot = np.asarray(audio, dtype=np.float32).copy()
        result = self.model.recognize(snapshot, sample_rate=self.SAMPLE_RATE)
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


def add_engine_argument(parser) -> None:
    """Register the shared ``--engine`` option (``STT_ENGINE`` default).

    Centralized so the STT entry points (auto/typing/clipboard) stay in lockstep
    on the flag name, default, and help text.
    """
    parser.add_argument(
        "--engine",
        default=os.environ.get("STT_ENGINE", DEFAULT_ENGINE),
        help="ASR engine: faster-whisper (default) or parakeet",
    )


def resolve_engine(name: str) -> str:
    """Normalize an ``--engine``/``STT_ENGINE`` value, exiting 2 on a bad name.

    Shared by the STT entry points so the error message and exit code stay
    consistent rather than drifting per launcher.
    """
    try:
        return normalize_engine(name)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)


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
