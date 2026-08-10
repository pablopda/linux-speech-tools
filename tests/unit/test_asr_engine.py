"""Unit tests for the pluggable ASR engine layer (``src/stt/asr_engine.py``).

Phase 1 of ``docs/planning/PLUGGABLE_ASR_AND_PARAKEET.md``: the engine factory
and the faster-whisper backend. Hermetic — ``faster_whisper`` is faked via
``sys.modules`` (the backend imports it lazily at construction time), so no real
model, network, or audio device is touched.
"""

import importlib
import sys
import types

import pytest

numpy = pytest.importorskip("numpy")


class FakeSegment:
    def __init__(self, text):
        self.text = text


class FakeWhisperModel:
    """Records construction + transcribe kwargs; returns padded segments."""

    def __init__(self, model_size, device, compute_type):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return [FakeSegment("  hello "), FakeSegment("world  ")], types.SimpleNamespace()


@pytest.fixture
def asr_engine():
    return importlib.import_module("src.stt.asr_engine")


def _fake_faster_whisper(monkeypatch, model_cls=FakeWhisperModel):
    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=model_cls)
    )


def test_create_engine_default_is_faster_whisper(asr_engine, monkeypatch):
    _fake_faster_whisper(monkeypatch)
    engine = asr_engine.create_engine("faster-whisper", model_size="tiny", device="cpu")
    assert isinstance(engine, asr_engine.FasterWhisperBackend)
    # runtime_checkable protocol: anything with transcribe() satisfies it.
    assert isinstance(engine, asr_engine.ASREngine)
    assert engine.model.model_size == "tiny"
    assert engine.compute_type == "int8"  # cpu -> int8


def test_create_engine_accepts_aliases_and_blank_default(asr_engine, monkeypatch):
    _fake_faster_whisper(monkeypatch)
    for name in ("faster_whisper", "WHISPER", "  faster-whisper  ", "", None):
        engine = asr_engine.create_engine(name, model_size="tiny", device="cpu")
        assert isinstance(engine, asr_engine.FasterWhisperBackend)


def test_create_engine_unknown_name_raises(asr_engine):
    with pytest.raises(ValueError, match="unknown STT engine"):
        asr_engine.create_engine("bogus-engine")


def test_faster_whisper_backend_joins_and_strips_segments(asr_engine, monkeypatch):
    _fake_faster_whisper(monkeypatch)
    engine = asr_engine.create_engine("faster-whisper", model_size="tiny", device="cpu")
    text = engine.transcribe(numpy.zeros(480, dtype=numpy.float32), "en")
    assert text == "hello world"
    # The faster-whisper-specific knobs stay encapsulated in the backend.
    (_, kwargs) = engine.model.calls[0]
    assert kwargs["language"] == "en"
    assert kwargs["beam_size"] == 5
    assert kwargs["vad_filter"] is True
    assert kwargs["vad_parameters"]["min_silence_duration_ms"] == 500


def test_faster_whisper_partial_uses_fast_settings_and_hints(asr_engine, monkeypatch):
    _fake_faster_whisper(monkeypatch)
    engine = asr_engine.create_engine("faster-whisper", model_size="tiny", device="cpu")
    engine.transcribe(
        numpy.zeros(480, dtype=numpy.float32),
        "en",
        partial=True,
        initial_prompt="Codex CLI",
        hotwords="pytest, pyproject.toml",
    )

    (_, kwargs) = engine.model.calls[0]
    assert kwargs["beam_size"] == 1
    assert kwargs["vad_filter"] is False
    assert "vad_parameters" not in kwargs
    assert kwargs["initial_prompt"] == "Codex CLI"
    assert kwargs["hotwords"] == "pytest, pyproject.toml"


def test_faster_whisper_retries_without_unsupported_hotwords(asr_engine, monkeypatch):
    class LegacyWhisperModel(FakeWhisperModel):
        def transcribe(self, audio, **kwargs):
            self.calls.append((audio, dict(kwargs)))
            if "hotwords" in kwargs:
                raise TypeError("unexpected keyword argument 'hotwords'")
            return [FakeSegment("legacy")], types.SimpleNamespace()

    _fake_faster_whisper(monkeypatch, LegacyWhisperModel)
    engine = asr_engine.create_engine("faster-whisper", model_size="tiny", device="cpu")
    assert engine.transcribe(
        numpy.zeros(480, dtype=numpy.float32),
        "en",
        initial_prompt="Codex CLI",
        hotwords="pytest",
    ) == "legacy"
    assert len(engine.model.calls) == 2
    assert engine.model.calls[0][1]["hotwords"] == "pytest"
    assert "hotwords" not in engine.model.calls[1][1]
    assert engine.model.calls[1][1]["initial_prompt"] == "Codex CLI"


def test_faster_whisper_does_not_retry_unrelated_type_error(asr_engine, monkeypatch):
    class BrokenWhisperModel(FakeWhisperModel):
        def transcribe(self, audio, **kwargs):
            self.calls.append((audio, dict(kwargs)))
            raise TypeError("internal tensor shape mismatch")

    _fake_faster_whisper(monkeypatch, BrokenWhisperModel)
    engine = asr_engine.create_engine("faster-whisper", model_size="tiny", device="cpu")
    with pytest.raises(TypeError, match="tensor shape mismatch"):
        engine.transcribe(
            numpy.zeros(480, dtype=numpy.float32),
            "en",
            hotwords="pytest",
        )
    assert len(engine.model.calls) == 1


def test_faster_whisper_compute_type_follows_device(asr_engine, monkeypatch):
    _fake_faster_whisper(monkeypatch)
    monkeypatch.delenv("WHISPER_COMPUTE_TYPE", raising=False)
    gpu = asr_engine.create_engine("faster-whisper", model_size="tiny", device="cuda")
    assert gpu.compute_type == "float16"


def test_faster_whisper_missing_dependency_is_actionable(asr_engine, monkeypatch):
    # A None entry in sys.modules makes `from faster_whisper import ...` raise.
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(RuntimeError, match=r"faster-whisper is not installed"):
        asr_engine.create_engine("faster-whisper", model_size="tiny", device="cpu")


# --- Parakeet (onnx-asr) backend -------------------------------------------
# onnx-asr is an optional extra and is not installed in the default test env, so
# it is faked via sys.modules. The fake mirrors the real API verified against
# onnx-asr 0.11.0: load_model(name, quantization=, providers=) -> model, and
# model.recognize(np_array, sample_rate=16000) -> str.


class FakeParakeetModel:
    def __init__(self):
        self.recognize_calls = []

    def recognize(self, audio, sample_rate):
        self.recognize_calls.append((audio, sample_rate))
        return "  Hello, world.  "


class FakeOnnxAsr:
    def __init__(self):
        self.load_calls = []
        self.model = FakeParakeetModel()

    def load_model(self, name, quantization=None, providers=None):
        self.load_calls.append(
            (name, quantization, tuple(providers) if providers else None)
        )
        return self.model


def _fake_onnx_asr(monkeypatch):
    monkeypatch.delenv("STT_PARAKEET_MODEL", raising=False)
    monkeypatch.delenv("STT_PARAKEET_QUANTIZATION", raising=False)
    fake = FakeOnnxAsr()
    monkeypatch.setitem(sys.modules, "onnx_asr", fake)
    return fake


def test_create_engine_parakeet_defaults(asr_engine, monkeypatch):
    fake = _fake_onnx_asr(monkeypatch)
    engine = asr_engine.create_engine("parakeet", device="cpu")
    assert isinstance(engine, asr_engine.ParakeetOnnxBackend)
    assert isinstance(engine, asr_engine.ASREngine)
    name, quant, providers = fake.load_calls[0]
    assert name == "nemo-parakeet-tdt-0.6b-v3"  # multilingual v3 (Spanish)
    assert quant == "int8"
    assert providers == ("CPUExecutionProvider",)


def test_parakeet_transcribe_passes_float32_and_strips(asr_engine, monkeypatch):
    fake = _fake_onnx_asr(monkeypatch)
    engine = asr_engine.create_engine("parakeet", device="cpu")
    audio = numpy.zeros(480, dtype=numpy.float32)
    text = engine.transcribe(
        audio,
        "es",
        partial=True,
        initial_prompt="ignored",
        hotwords="also ignored",
    )  # language/hints are ignored (v3 auto-detects)
    assert text == "Hello, world."
    (arr, sample_rate) = fake.model.recognize_calls[0]
    assert arr is not audio
    numpy.testing.assert_array_equal(arr, audio)
    assert arr.dtype == numpy.float32
    assert sample_rate == 16000


def test_parakeet_selects_cuda_providers_when_available(asr_engine, monkeypatch):
    fake = _fake_onnx_asr(monkeypatch)
    monkeypatch.setattr(asr_engine, "_cuda_provider_available", lambda: True)
    asr_engine.create_engine("parakeet", device="cuda")
    _, _, providers = fake.load_calls[0]
    assert providers == ("CUDAExecutionProvider", "CPUExecutionProvider")


def test_parakeet_falls_back_to_cpu_when_cuda_unavailable(asr_engine, monkeypatch, capsys):
    fake = _fake_onnx_asr(monkeypatch)
    monkeypatch.setattr(asr_engine, "_cuda_provider_available", lambda: False)
    asr_engine.create_engine("parakeet", device="cuda")
    _, _, providers = fake.load_calls[0]
    assert providers == ("CPUExecutionProvider",)
    assert "onnxruntime-gpu" in capsys.readouterr().err


def test_parakeet_quantization_env_disables(asr_engine, monkeypatch):
    fake = _fake_onnx_asr(monkeypatch)
    monkeypatch.setenv("STT_PARAKEET_QUANTIZATION", "none")
    asr_engine.create_engine("parakeet", device="cpu")
    _, quant, _ = fake.load_calls[0]
    assert quant is None


def test_parakeet_model_env_override(asr_engine, monkeypatch):
    fake = _fake_onnx_asr(monkeypatch)
    monkeypatch.setenv("STT_PARAKEET_MODEL", "nemo-parakeet-tdt-0.6b-v2")
    asr_engine.create_engine("parakeet", device="cpu")
    name, _, _ = fake.load_calls[0]
    assert name == "nemo-parakeet-tdt-0.6b-v2"


def test_parakeet_missing_dependency_is_actionable(asr_engine, monkeypatch):
    monkeypatch.setitem(sys.modules, "onnx_asr", None)
    with pytest.raises(RuntimeError, match=r"onnx-asr is not installed"):
        asr_engine.create_engine("parakeet", device="cpu")


def test_normalize_engine_maps_aliases_and_rejects_unknown(asr_engine):
    assert asr_engine.normalize_engine("faster_whisper") == "faster-whisper"
    assert asr_engine.normalize_engine("WHISPER") == "faster-whisper"
    assert asr_engine.normalize_engine("  parakeet ") == "parakeet"
    assert asr_engine.normalize_engine("onnx-asr") == "parakeet"
    assert asr_engine.normalize_engine("") == "faster-whisper"
    assert asr_engine.normalize_engine(None) == "faster-whisper"
    with pytest.raises(ValueError, match="unknown STT engine"):
        asr_engine.normalize_engine("bogus")


def test_parakeet_transcribe_handles_empty_or_none_result(asr_engine, monkeypatch):
    class _NoneModel:
        def recognize(self, audio, sample_rate):
            return None  # silence/empty input can yield no text

    class _FakeOnnx:
        def load_model(self, name, quantization=None, providers=None):
            return _NoneModel()

    monkeypatch.delenv("STT_PARAKEET_MODEL", raising=False)
    monkeypatch.delenv("STT_PARAKEET_QUANTIZATION", raising=False)
    monkeypatch.setitem(sys.modules, "onnx_asr", _FakeOnnx())
    engine = asr_engine.create_engine("parakeet", device="cpu")
    assert engine.transcribe(numpy.zeros(10, dtype=numpy.float32), None) == ""
