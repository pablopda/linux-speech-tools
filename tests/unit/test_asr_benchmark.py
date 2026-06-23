"""Unit tests for the ASR benchmark harness (``src/utils/asr_benchmark.py``).

Phase 3 of ``docs/planning/PLUGGABLE_ASR_AND_PARAKEET.md``. The WER/manifest/
report logic is pure-Python and tested directly; the orchestration is tested
with injected fakes (no real models or audio files).
"""

import importlib
import json
import sys
import types

import numpy
import pytest

bench = importlib.import_module("src.utils.asr_benchmark")


def test_normalize_for_wer_strips_punctuation_and_case():
    assert bench.normalize_for_wer("Hola, ¿qué tal?") == ["hola", "qué", "tal"]
    assert bench.normalize_for_wer("") == []


def test_word_error_rate_basic_cases():
    assert bench.word_error_rate("the cat sat", "the cat sat") == 0.0
    # one substitution out of three reference words
    assert bench.word_error_rate("the cat sat", "the dog sat") == pytest.approx(1 / 3)
    # one deletion out of three
    assert bench.word_error_rate("the cat sat", "the sat") == pytest.approx(1 / 3)
    # one insertion out of three
    assert bench.word_error_rate("the cat sat", "the big cat sat") == pytest.approx(1 / 3)


def test_word_error_rate_empty_reference():
    assert bench.word_error_rate("", "") == 0.0
    assert bench.word_error_rate("", "something") == 1.0


def test_word_error_rate_is_punctuation_insensitive():
    # Parakeet emits native punctuation/caps; WER should not penalize that.
    assert bench.word_error_rate("hola que tal", "Hola, que tal.") == 0.0


def test_load_manifest_valid_and_invalid(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps([{"audio": "a.wav", "reference": "hola", "accent": "AR"}]),
        encoding="utf-8",
    )
    clips = bench.load_manifest(good)
    assert clips[0]["audio"] == "a.wav"

    bad_shape = tmp_path / "bad.json"
    bad_shape.write_text(json.dumps({"audio": "a.wav"}), encoding="utf-8")
    with pytest.raises(ValueError):
        bench.load_manifest(bad_shape)

    missing_field = tmp_path / "missing.json"
    missing_field.write_text(json.dumps([{"audio": "a.wav"}]), encoding="utf-8")
    with pytest.raises(ValueError):
        bench.load_manifest(missing_field)


class _FakeEngine:
    """Engine whose hypothesis is controlled per construction."""

    def __init__(self, hypothesis):
        self._hypothesis = hypothesis

    def transcribe(self, audio, language):
        return self._hypothesis


def test_run_benchmark_scores_and_handles_unavailable_engine():
    clips = [
        {"audio": "ar1.wav", "reference": "hola que tal", "accent": "AR"},
        {"audio": "mx1.wav", "reference": "buenos dias", "accent": "MX"},
    ]

    def engine_factory(name, *, model_size, device, language):
        if name == "faster-whisper":
            return _PerfectEngine(clips)  # perfect transcription -> WER 0
        if name == "parakeet":
            raise RuntimeError("onnx-asr is not installed")
        raise ValueError(f"unknown {name}")

    results = bench.run_benchmark(
        clips,
        ["faster-whisper", "parakeet"],
        audio_loader=lambda path: path,  # no real audio
        engine_factory=engine_factory,
    )

    assert results["clips"] == 2
    fw = results["engines"]["faster-whisper"]
    assert fw["available"] is True
    assert fw["mean_wer"] == 0.0
    assert len(fw["per_clip"]) == 2

    pk = results["engines"]["parakeet"]
    assert pk["available"] is False
    assert "onnx-asr" in pk["error"]


class _PerfectEngine:
    """Returns each clip's own reference in manifest order (WER 0)."""

    def __init__(self, clips):
        self._refs = [c["reference"] for c in clips]
        self._i = 0

    def transcribe(self, audio, language):
        ref = self._refs[self._i]
        self._i += 1
        return ref


def test_format_report_includes_engines_and_unavailable_note():
    clips = [{"audio": "a.wav", "reference": "hola que tal", "accent": "AR"}]

    def engine_factory(name, *, model_size, device, language):
        if name == "faster-whisper":
            return _FakeEngine("hola que")  # one deletion -> WER 1/3
        raise RuntimeError("onnx-asr is not installed")

    results = bench.run_benchmark(
        clips,
        ["faster-whisper", "parakeet"],
        audio_loader=lambda path: path,
        engine_factory=engine_factory,
    )
    report = bench.format_report(results)
    assert "faster-whisper" in report
    assert "parakeet" in report
    assert "unavailable" in report
    assert "Mean WER by accent" in report
    assert "AR" in report
    assert "0.333" in report  # the computed WER (1/3) must actually render


def test_load_audio_downmixes_stereo_and_resamples_to_16k(monkeypatch):
    # stereo @ 16 kHz -> mono (channel mean), length unchanged
    stereo = numpy.tile(numpy.array([[0.5, -0.5]], dtype=numpy.float32), (100, 1))
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(read=lambda path, dtype, always_2d: (stereo, 16000)),
    )
    mono = bench.load_audio("x.wav")
    assert mono.ndim == 1
    assert mono.dtype == numpy.float32
    assert mono.shape[0] == 100
    assert numpy.allclose(mono, 0.0)  # mean(0.5, -0.5)

    # mono @ 8 kHz -> linear resample to 16 kHz (length doubles)
    src = numpy.linspace(-1.0, 1.0, num=80, dtype=numpy.float32)
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(read=lambda path, dtype, always_2d: (src, 8000)),
    )
    resampled = bench.load_audio("y.wav")
    assert resampled.dtype == numpy.float32
    assert resampled.shape[0] == round(80 * 16000 / 8000)  # 160


def test_run_benchmark_isolates_per_clip_errors():
    clips = [
        {"audio": "ok.wav", "reference": "hola que tal", "accent": "AR"},
        {"audio": "bad.wav", "reference": "buenos dias", "accent": "MX"},
    ]

    def loader(path):
        if path == "bad.wav":
            raise RuntimeError("corrupt file")
        return path

    results = bench.run_benchmark(
        clips,
        ["faster-whisper"],
        audio_loader=loader,
        engine_factory=lambda name, **kw: _FakeEngine("hola que tal"),
    )
    engine = results["engines"]["faster-whisper"]
    assert engine["available"] is True
    assert engine["errors"] == 1
    assert engine["mean_wer"] == 0.0  # only the good clip is scored
    by_audio = {c["audio"]: c for c in engine["per_clip"]}
    assert "error" in by_audio["bad.wav"]
    assert "wer" in by_audio["ok.wav"]


def test_main_runs_and_writes_report(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps([{"audio": "a.wav", "reference": "hola"}]), encoding="utf-8"
    )
    out = tmp_path / "report.md"

    captured = {}

    def fake_run_benchmark(clips, engines, *, model_size, device, language):
        captured["engines"] = engines
        captured["clips"] = len(clips)
        return {
            "clips": len(clips),
            "engines": {
                engines[0]: {
                    "available": True,
                    "mean_wer": 0.0,
                    "median_latency_s": 0.0,
                    "errors": 0,
                    "per_clip": [{"accent": None, "wer": 0.0}],
                }
            },
        }

    monkeypatch.setattr(bench, "run_benchmark", fake_run_benchmark)
    rc = bench.main(
        ["--manifest", str(manifest), "--engines", "faster-whisper", "--output", str(out)]
    )
    assert rc == 0
    assert captured == {"engines": ["faster-whisper"], "clips": 1}
    assert out.exists()
    assert "ASR benchmark" in out.read_text()
