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
