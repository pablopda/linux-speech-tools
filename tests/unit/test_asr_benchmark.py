"""Deterministic tests for the aggregate-safe ASR benchmark harness."""

import copy
import hashlib
import importlib
import json
import os
import sys
import types
import wave
from unittest import mock

import pytest

numpy = pytest.importorskip("numpy")

bench = importlib.import_module("src.utils.asr_benchmark")


def _clip(clip_id, reference, *, accent="AR", category="everyday", code_switch=False):
    return {
        "id": clip_id,
        "audio": f"/private/{clip_id}.wav",
        "reference": reference,
        "accent": accent,
        "category": category,
        "code_switch": code_switch,
    }


def _accepted_corpus():
    accents = ["AR"] * 24 + ["MX"] * 4 + ["CO"] * 4 + ["CL"] * 4
    categories = sorted(bench._REQUIRED_CATEGORIES)
    clips = []
    for index, accent in enumerate(accents):
        clip_id = f"{accent.lower()}-{index + 1:03d}"
        category = categories[index] if index < len(categories) else "everyday"
        clips.append(
            {
                "id": clip_id,
                "audio": f"/private/latam-asr/{clip_id}.wav",
                "reference": "" if category == "silence" else "referencia revisada",
                "reference_reviewed": True,
                "accent": accent,
                "category": category,
                "code_switch": index == 0,
                "speaker_id": f"speaker-{accent.lower()}-01",
                "accent_authenticity": "confirmed",
                "consent": "recorded",
                "privacy_review": "passed",
                "sha256": hashlib.sha256(clip_id.encode("ascii")).hexdigest(),
                "sample_rate": 16000,
                "channels": 1,
                "duration_s": 1.0,
                "capture_device": "device-01",
                "recording_environment": "quiet-room",
            }
        )
    return clips


def _accepted_audio_inspector(path):
    clip_id = str(path).rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return {
        "sha256": hashlib.sha256(clip_id.encode("ascii")).hexdigest(),
        "sample_rate": 16000,
        "channels": 1,
        "frames": 16000,
        "duration_s": 1.0,
        "format": "WAV",
    }


class StaticEngine:
    def __init__(self, hypotheses):
        self.hypotheses = hypotheses
        self.calls = []

    def transcribe(self, audio, language):
        self.calls.append((audio, language))
        value = self.hypotheses[audio]
        if isinstance(value, Exception):
            raise value
        return value


class ProviderStaticEngine(StaticEngine):
    def __init__(self, hypotheses, *, requested, actual, status):
        super().__init__(hypotheses)
        self._provider_evidence = {
            "backend": "onnxruntime",
            "requested_device": requested,
            "actual_device": actual,
            "status": status,
            "operationally_verified": actual in ("cpu", "cuda"),
            "cuda_advertised": requested == "cuda",
            "cuda_preload_attempted": requested == "cuda",
            "cuda_preload_succeeded": requested == "cuda",
            "core_session_providers": [[
                "CUDAExecutionProvider"
                if actual == "cuda"
                else "CPUExecutionProvider"
            ]],
            "auxiliary_session_providers": [["CPUExecutionProvider"]],
        }

    def provider_evidence(self):
        return self._provider_evidence


def test_normalize_and_single_utterance_wer():
    assert bench.normalize_for_wer("Hola, ¿qué tal?") == ["hola", "qué", "tal"]
    assert bench.word_error_rate("the cat sat", "the dog sat") == pytest.approx(1 / 3)
    assert bench.word_error_rate("", "") == 0.0
    assert bench.word_error_rate("", "something") == 1.0
    assert bench.word_error_rate("hola que tal", "Hola, que tal.") == 0.0


def test_corpus_wer_uses_total_edits_over_total_reference_words():
    """A short clip must not receive the same weight as a 100-word clip."""
    long_reference = " ".join(["ok"] * 100)
    clips = [
        _clip("short", "bad"),
        _clip("long", long_reference),
    ]
    engine = StaticEngine({"short": "", "long": long_reference})

    results = bench.run_benchmark(
        clips,
        ["faster-whisper"],
        warmup_runs=0,
        repetitions=1,
        audio_loader=lambda path: path.rsplit("/", 1)[-1].split(".", 1)[0],
        engine_factory=lambda *args, **kwargs: engine,
    )

    data = results["engines"]["faster-whisper"]
    assert data["metrics"]["overall"] == {
        "clips": 2,
        "edit_distance": 1,
        "reference_words": 101,
        "wer": pytest.approx(1 / 101),
    }
    assert data["corpus_wer"] == pytest.approx(1 / 101)
    assert data["mean_wer"] == data["corpus_wer"]  # compatibility alias
    assert data["corpus_wer"] != pytest.approx(0.5)  # old macro-average defect


def test_load_manifest_assigns_safe_ids_and_validates_uniqueness(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {"audio": "/private/a.wav", "reference": "hola", "accent": "AR"},
                {"id": "mx-001", "audio": "/private/b.wav", "reference": "qué tal"},
            ]
        ),
        encoding="utf-8",
    )
    clips = bench.load_manifest(manifest)
    assert [clip["id"] for clip in clips] == ["clip-001", "mx-001"]
    with pytest.raises(ValueError, match="explicit pseudonymous id"):
        bench.load_manifest(manifest, require_explicit_ids=True)

    bad_shape = tmp_path / "bad.json"
    bad_shape.write_text(json.dumps({"audio": "a.wav"}), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON list"):
        bench.load_manifest(bad_shape)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        json.dumps(
            [
                {"id": "same", "audio": "a.wav", "reference": "a"},
                {"id": "same", "audio": "b.wav", "reference": "b"},
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate clip id"):
        bench.load_manifest(duplicate)

    invalid_flag = tmp_path / "invalid-flag.json"
    invalid_flag.write_text(
        json.dumps([{"audio": "a.wav", "reference": "a", "code_switch": "yes"}]),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="true or false"):
        bench.load_manifest(invalid_flag)

    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="at least one clip"):
        bench.load_manifest(empty)


def test_manifest_read_is_bounded_regular_and_does_not_follow_symlinks(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("[]", encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        bench.load_manifest(linked)

    fifo = tmp_path / "manifest.fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular file"):
        bench.load_manifest(fifo)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (bench._MAX_MANIFEST_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        bench.load_manifest(oversized)


def test_exact_ar_first_corpus_validation_returns_aggregate_only_summary():
    clips = _accepted_corpus()

    summary = bench.validate_accepted_corpus(
        clips, audio_inspector=_accepted_audio_inspector
    )

    assert summary == {
        "accepted": True,
        "manifest_sha256": "not-computed",
        "clips": 36,
        "accents": {"AR": 24, "MX": 4, "CO": 4, "CL": 4},
        "categories": {
            name: sum(clip["category"] == name for clip in clips)
            for name in sorted({clip["category"] for clip in clips})
        },
        "code_switch_clips": 1,
        "pseudonymous_speakers": 4,
        "total_duration_s": 36.0,
        "sample_rates": [16000],
        "channel_counts": [1],
        "audio_formats": ["WAV"],
    }
    serialized = repr(summary)
    assert "/private/" not in serialized
    assert "referencia revisada" not in serialized
    assert "speaker-01" not in serialized
    assert "device-01" not in serialized


def test_accepted_corpus_summary_records_manifest_checksum(tmp_path):
    clips = _accepted_corpus()
    manifest = tmp_path / "manifest.json"
    encoded = json.dumps(clips, ensure_ascii=False).encode("utf-8")
    manifest.write_bytes(encoded)

    summary = bench.validate_accepted_corpus(
        clips,
        manifest_path=manifest,
        audio_inspector=_accepted_audio_inspector,
    )

    assert summary["manifest_sha256"] == hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda clips: clips.pop(), "exactly 36"),
        (lambda clips: clips[0].update(accent="UY"), "unsupported benchmark accent"),
        (lambda clips: clips[0].pop("reference_reviewed"), "reference review"),
        (lambda clips: clips[0].update(consent="pending"), "consent"),
        (lambda clips: clips[0].update(privacy_review="pending"), "privacy review"),
        (
            lambda clips: clips[0].update(accent_authenticity="pending"),
            "authentic-accent",
        ),
        (lambda clips: clips[0].update(sample_rate=8000), "sample rate"),
        (lambda clips: clips[0].update(channels=2), "channel"),
        (lambda clips: clips[0].update(duration_s=9.0), "duration"),
    ],
)
def test_accepted_corpus_rejects_incomplete_or_unreviewed_evidence(
    mutation, message
):
    clips = _accepted_corpus()
    mutation(clips)

    with pytest.raises(ValueError, match=message):
        bench.validate_accepted_corpus(
            clips, audio_inspector=_accepted_audio_inspector
        )


def test_accepted_corpus_rejects_missing_coverage_and_duplicate_audio():
    clips = _accepted_corpus()
    missing = copy.deepcopy(clips)
    for clip in missing:
        if clip["category"] == "programming":
            clip["category"] = "everyday"
    with pytest.raises(ValueError, match="missing required category coverage"):
        bench.validate_accepted_corpus(
            missing, audio_inspector=_accepted_audio_inspector
        )

    duplicate = copy.deepcopy(clips)
    duplicate[1]["sha256"] = duplicate[0]["sha256"]
    duplicate_path = duplicate[0]["audio"]
    duplicate[1]["audio"] = duplicate_path
    with pytest.raises(ValueError, match="duplicates another audio checksum"):
        bench.validate_accepted_corpus(
            duplicate, audio_inspector=_accepted_audio_inspector
        )


def test_accepted_corpus_rejects_one_speaker_token_across_accents():
    clips = _accepted_corpus()
    clips[24]["speaker_id"] = clips[0]["speaker_id"]

    with pytest.raises(ValueError, match="must not span benchmark accents"):
        bench.validate_accepted_corpus(
            clips, audio_inspector=_accepted_audio_inspector
        )


def test_audio_inspection_hashes_and_decodes_a_pinned_regular_file(tmp_path):
    audio_path = tmp_path / "clip.wav"
    with wave.open(str(audio_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 1600)

    inspected = bench._inspect_audio_file(audio_path)

    assert inspected["sha256"] == hashlib.sha256(audio_path.read_bytes()).hexdigest()
    assert inspected["sample_rate"] == 16000
    assert inspected["channels"] == 1
    assert inspected["frames"] == 1600
    assert inspected["duration_s"] == pytest.approx(0.1)

    linked = tmp_path / "linked.wav"
    linked.symlink_to(audio_path)
    with pytest.raises(ValueError, match="readable regular"):
        bench._inspect_audio_file(linked)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "private/path"),
        ("id", "contains transcript words"),
        ("id", " leading-space"),
        ("id", "a" * 129),
        ("id", None),
        ("accent", "AR / Buenos Aires"),
        ("accent", 123),
        ("category", "developer term with prose"),
        ("category", "x" * 65),
    ],
)
def test_manifest_rejects_non_token_metadata(tmp_path, field, value):
    clip = {
        "id": "safe-001",
        "audio": "/private/audio.wav",
        "reference": "hola",
        "accent": "AR",
        "category": "everyday",
    }
    clip[field] = value
    manifest = tmp_path / "unsafe.json"
    manifest.write_text(json.dumps([clip]), encoding="utf-8")

    with pytest.raises(ValueError, match="privacy-safe"):
        bench.load_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "private/path"),
        ("id", "contains transcript words"),
        ("id", "a" * 129),
        ("accent", "AR / Buenos Aires"),
        ("accent", 123),
        ("category", "developer term with prose"),
        ("category", "x" * 65),
    ],
)
def test_direct_run_rejects_unsafe_metadata(field, value):
    clip = _clip("safe-001", "hola")
    clip[field] = value
    with pytest.raises(ValueError, match="privacy-safe"):
        bench.run_benchmark([clip], ["faster-whisper"])


def test_direct_run_rejects_invalid_engine_lists():
    clips = [_clip("safe", "hola")]
    with pytest.raises(ValueError, match="at least one"):
        bench.run_benchmark(clips, [])
    with pytest.raises(ValueError, match="at least one"):
        bench.run_benchmark(clips, [""])
    with pytest.raises(ValueError, match="unique"):
        bench.run_benchmark(clips, ["faster-whisper", "faster-whisper"])
    with pytest.raises(ValueError, match="privacy-safe"):
        bench.run_benchmark(clips, ["faster-whisper", "private/engine"])


def test_direct_run_rejects_empty_clip_set():
    with pytest.raises(ValueError, match="at least one clip"):
        bench.run_benchmark([], ["faster-whisper", "parakeet"])


def test_run_retains_only_privacy_safe_metadata_and_required_subsets():
    clips = [
        _clip(
            "ar-code",
            "abrí readme",
            category="filename-dev-term",
            code_switch=True,
        ),
        _clip("mx-everyday", "buenos días", accent="MX"),
    ]
    engine = StaticEngine({"ar-code": "abrí readme", "mx-everyday": "buenos días"})
    results = bench.run_benchmark(
        clips,
        ["faster-whisper"],
        warmup_runs=0,
        repetitions=1,
        audio_loader=lambda path: path.rsplit("/", 1)[-1].split(".", 1)[0],
        engine_factory=lambda *args, **kwargs: engine,
    )

    data = results["engines"]["faster-whisper"]
    record = data["per_clip"][0]
    assert record["id"] == "ar-code"
    assert record["accent"] == "AR"
    assert record["category"] == "filename-dev-term"
    assert record["code_switch"] is True
    for sensitive_key in ("audio", "reference", "hypothesis", "speaker_id"):
        assert sensitive_key not in record
    assert data["metrics"]["accents"]["AR"]["clips"] == 1
    assert data["metrics"]["accents"]["MX"]["clips"] == 1
    assert data["metrics"]["code_switch"]["clips"] == 1
    assert data["metrics"]["filename_developer_term"]["clips"] == 1


def test_unavailable_engine_is_reported_without_aborting():
    clips = [_clip("ar-001", "hola")]

    def engine_factory(name, **kwargs):
        if name == "parakeet":
            raise RuntimeError("onnx-asr is not installed")
        return StaticEngine({"ar-001": "hola"})

    results = bench.run_benchmark(
        clips,
        ["faster-whisper", "parakeet"],
        warmup_runs=0,
        repetitions=1,
        audio_loader=lambda path: "ar-001",
        engine_factory=engine_factory,
    )
    assert results["engines"]["faster-whisper"]["corpus_wer"] == 0.0
    assert results["engines"]["parakeet"]["available"] is False
    assert results["engines"]["parakeet"]["error"] == "engine-init: RuntimeError"
    assert results["comparison"]["comparable"] is False
    assert "unavailable" in results["comparison"]["reason"]


def test_engine_init_oserror_is_redacted_and_not_comparable():
    clips = [_clip("ar-001", "hola")]

    def engine_factory(name, **kwargs):
        if name == "parakeet":
            raise OSError("/private/model/cache/alice")
        return StaticEngine({"ar-001": "hola"})

    results = bench.run_benchmark(
        clips,
        ["faster-whisper", "parakeet"],
        warmup_runs=0,
        repetitions=1,
        audio_loader=lambda path: "ar-001",
        engine_factory=engine_factory,
    )

    assert results["engines"]["parakeet"] == {
        "available": False,
        "error": "engine-init: OSError",
    }
    assert results["comparison"]["comparable"] is False
    assert "/private/" not in repr(results)
    assert "alice" not in repr(results)


def test_mismatched_engine_errors_make_comparison_not_comparable():
    clips = [_clip("a", "alpha"), _clip("b", "bravo")]
    engines = {
        "faster-whisper": StaticEngine({"a": "alpha", "b": RuntimeError("private b")}),
        "parakeet": StaticEngine({"a": RuntimeError("private a"), "b": "bravo"}),
    }
    results = bench.run_benchmark(
        clips,
        ["faster-whisper", "parakeet"],
        warmup_runs=0,
        repetitions=1,
        audio_loader=lambda path: path.rsplit("/", 1)[-1].split(".", 1)[0],
        engine_factory=lambda name, **kwargs: engines[name],
    )

    comparison = results["comparison"]
    assert comparison["comparable"] is False
    assert comparison["reason"] == "engines scored different clip sets"
    assert comparison["scored_clip_ids_by_engine"] == {
        "faster-whisper": ["a"],
        "parakeet": ["b"],
    }
    serialized = repr(results)
    assert "/private/" not in serialized
    assert "private a" not in serialized
    assert "private b" not in serialized


def test_identical_but_incomplete_scored_sets_are_not_comparable():
    clips = [_clip("a", "alpha"), _clip("b", "bravo")]
    engines = {
        name: StaticEngine({"a": "alpha", "b": RuntimeError("failed")})
        for name in ("faster-whisper", "parakeet")
    }
    results = bench.run_benchmark(
        clips,
        list(engines),
        warmup_runs=0,
        repetitions=1,
        audio_loader=lambda path: path.rsplit("/", 1)[-1].split(".", 1)[0],
        engine_factory=lambda name, **kwargs: engines[name],
    )
    assert results["comparison"]["comparable"] is False
    assert "incomplete" in results["comparison"]["reason"]


def test_complete_identical_scored_sets_are_comparable():
    clips = [_clip("a", "alpha"), _clip("b", "bravo")]
    engines = {
        name: StaticEngine({"a": "alpha", "b": "bravo"})
        for name in ("faster-whisper", "parakeet")
    }
    results = bench.run_benchmark(
        clips,
        list(engines),
        warmup_runs=0,
        repetitions=1,
        audio_loader=lambda path: path.rsplit("/", 1)[-1].split(".", 1)[0],
        engine_factory=lambda name, **kwargs: engines[name],
    )
    assert results["comparison"]["comparable"] is True
    assert "identical complete manifest" in results["comparison"]["reason"]


def test_actual_provider_mismatch_makes_benchmark_not_comparable():
    clips = [_clip("a", "alpha")]
    engines = {
        "faster-whisper": ProviderStaticEngine(
            {"a": "alpha"},
            requested="cuda",
            actual="cuda",
            status="configured-cuda",
        ),
        "parakeet": ProviderStaticEngine(
            {"a": "alpha"},
            requested="cuda",
            actual="cpu",
            status="verified-cpu-fallback",
        ),
    }

    results = bench.run_benchmark(
        clips,
        list(engines),
        device="cuda",
        warmup_runs=0,
        repetitions=1,
        audio_loader=lambda path: "a",
        engine_factory=lambda name, **kwargs: engines[name],
    )

    comparison = results["comparison"]
    assert comparison["comparable"] is False
    assert comparison["provider_comparable"] is False
    assert comparison["actual_device_by_engine"] == {
        "faster-whisper": "cuda",
        "parakeet": "cpu",
    }
    assert "operationally comparable" in comparison["reason"]
    report = bench.format_report(results)
    assert "## Operational provider verification" in report
    assert "verified-cpu-fallback" in report
    assert "CUDAExecutionProvider" in report


def test_provider_evidence_is_bounded_and_does_not_retain_paths():
    clip = _clip("a", "alpha")
    engine = ProviderStaticEngine(
        {"a": "alpha"}, requested="cuda", actual="cuda", status="verified-cuda"
    )
    engine._provider_evidence.update(
        {
            "backend": "/private/model/backend",
            "status": "private status with spaces",
            "core_session_providers": [["/private/provider"]],
        }
    )

    results = bench.run_benchmark(
        [clip],
        ["parakeet"],
        device="cuda",
        warmup_runs=0,
        repetitions=1,
        audio_loader=lambda path: "a",
        engine_factory=lambda *args, **kwargs: engine,
    )

    evidence = results["engines"]["parakeet"]["provider_evidence"]
    assert evidence["backend"] == "unknown"
    assert evidence["status"] == "invalid-evidence"
    assert evidence["core_session_providers"] == [["other"]]
    assert "/private/" not in repr(results)
    assert "/private/" not in bench.format_report(results)


def test_inconsistent_repeated_hypotheses_are_not_comparable():
    clips = [_clip("a", "alpha")]

    class AlternatingEngine:
        def __init__(self):
            self.calls = 0

        def transcribe(self, audio, language):
            self.calls += 1
            return "alpha" if self.calls % 2 else "other"

    engines = {name: AlternatingEngine() for name in ("faster-whisper", "parakeet")}
    results = bench.run_benchmark(
        clips,
        list(engines),
        warmup_runs=0,
        repetitions=2,
        audio_loader=lambda path: "a",
        engine_factory=lambda name, **kwargs: engines[name],
    )

    assert results["comparison"]["comparable"] is False
    assert "inconsistent" in results["comparison"]["reason"]
    assert results["engines"]["parakeet"]["inconsistent_hypotheses"] == 1


def test_warmup_and_repetitions_use_median_per_clip_latency(monkeypatch):
    clips = [_clip("a", "alpha")]
    engine = StaticEngine({"a": "alpha"})
    ticks = iter([0.0, 3.0, 10.0, 11.0, 20.0, 22.0])
    monkeypatch.setattr(bench.time, "monotonic", lambda: next(ticks))

    results = bench.run_benchmark(
        clips,
        ["faster-whisper"],
        warmup_runs=1,
        repetitions=3,
        audio_loader=lambda path: "a",
        engine_factory=lambda *args, **kwargs: engine,
    )

    record = results["engines"]["faster-whisper"]["per_clip"][0]
    assert len(engine.calls) == 4  # one untimed warm-up + three measurements
    assert record["median_latency_s"] == 2.0
    assert results["engines"]["faster-whisper"]["median_latency_s"] == 2.0
    assert results["configuration"]["warmup_runs"] == "1"
    assert results["configuration"]["repetitions"] == "3"


def test_format_report_contains_evidence_subsets_and_no_private_content():
    clips = [
        _clip("safe-001", "open readme", category="filename-dev-term", code_switch=True)
    ]
    engines = {
        name: StaticEngine({"safe-001": "open readme"})
        for name in ("faster-whisper", "parakeet")
    }
    results = bench.run_benchmark(
        clips,
        list(engines),
        warmup_runs=0,
        repetitions=1,
        environment={
            "python": "3.test",
            "onnxruntime_available_providers": ["CPUExecutionProvider"],
            "provider_caveat": "available is not execution proof",
        },
        audio_loader=lambda path: "safe-001",
        engine_factory=lambda name, **kwargs: engines[name],
    )
    report = bench.format_report(results)
    assert "Comparable: yes" in report
    assert "Corpus WER" in report
    assert "Code-switch" in report
    assert "Filename / developer term" in report
    assert "CPUExecutionProvider" in report
    assert "safe-001" in report
    assert "/private/" not in report
    assert "open readme" not in report


def test_configuration_and_environment_projection_is_bounded_and_private(
    monkeypatch,
):
    private_fw_model = "/private/models/alice/faster-whisper"
    private_parakeet_model = "C:\\Users\\alice\\private-parakeet"
    private_environment = "line one\n/private/evidence|secret"
    monkeypatch.setenv("STT_PARAKEET_MODEL", private_parakeet_model)
    monkeypatch.setenv("STT_PARAKEET_QUANTIZATION", "INT8")

    clip = _clip("safe-001", "hola")
    engines = {
        name: StaticEngine({"safe-001": "hola"})
        for name in ("faster-whisper", "parakeet")
    }
    results = bench.run_benchmark(
        [clip],
        list(engines),
        model_size=private_fw_model,
        environment={
            "python": "3.11.9",
            "unsafe/key|field": private_environment,
            "onnxruntime_available_providers": ["CPUExecutionProvider"],
        },
        audio_loader=lambda path: "safe-001",
        engine_factory=lambda name, **kwargs: engines[name],
    )
    report = bench.format_report(results)
    serialized = repr(results)

    for private_value in (
        private_fw_model,
        private_parakeet_model,
        private_environment,
        "unsafe/key|field",
    ):
        assert private_value not in serialized
        assert private_value not in report
    assert report.count("[redacted-sha256:") >= 3
    assert "3.11.9" in report
    assert "CPUExecutionProvider" in report
    assert results["configuration"]["parakeet_quantization"] == "int8"


def test_report_projection_sanitizes_untrusted_prebuilt_results():
    results = {
        "clips": 1,
        "configuration": {
            "model_size": "/private/model",
            "device": "cpu|injected",
            "language": "es\nprivate",
            "warmup_runs": "/private/warmup",
            "repetitions": "3|injected",
        },
        "environment": {"unsafe/key": "/private/environment"},
        "engines": {},
        "comparison": {
            "comparable": False,
            "reason": "private/reason|injected",
            "scored_clip_ids_by_engine": {},
        },
    }

    report = bench.format_report(results)
    assert "/private/" not in report
    assert "|injected" not in report
    assert "es\nprivate" not in report
    assert report.count("[redacted-sha256:") >= 7


def test_environment_projection_bounds_field_and_list_counts():
    environment = {
        f"field-{index}": [f"provider-{item}" for item in range(40)]
        for index in range(70)
    }

    projected = bench._privacy_safe_environment(environment)

    assert len(projected) == bench._REPORT_MAPPING_LIMIT + 1
    assert any(key.startswith("additional-fields") for key in projected)
    assert all(len(str(value)) <= 256 for value in projected.values())
    assert all(
        str(value).startswith("[redacted-sha256:")
        for key, value in projected.items()
        if not key.startswith("additional-fields")
    )


def test_numeric_projection_is_canonical_bounded_and_finite():
    exactly_256_digits = 10**255
    exactly_257_digits = 10**256

    assert bench._privacy_safe_report_value(True) == "true"
    assert bench._privacy_safe_report_value(False) == "false"
    assert bench._privacy_safe_report_value(-42) == "-42"
    assert bench._privacy_safe_report_value(1.25) == "1.25"
    assert bench._privacy_safe_report_value(exactly_256_digits) == str(
        exactly_256_digits
    )
    assert bench._privacy_safe_report_value(exactly_257_digits).startswith(
        "[redacted-sha256:"
    )
    assert bench._privacy_safe_report_value(float("inf")).startswith(
        "[redacted-sha256:"
    )
    assert bench._privacy_safe_report_value(float("nan")).startswith(
        "[redacted-sha256:"
    )

    results = {
        "clips": 1,
        "configuration": {
            "model_size": "small",
            "device": "cpu",
            "language": "es",
            "warmup_runs": exactly_256_digits,
            "repetitions": exactly_257_digits,
        },
        "environment": {
            "negative_integer": -42,
            "negative_float": -1.25,
            "boolean": True,
        },
        "engines": {},
        "comparison": {
            "comparable": False,
            "reason": "comparison unavailable",
            "scored_clip_ids_by_engine": {},
        },
    }
    report = bench.format_report(results)
    assert str(exactly_256_digits) in report
    assert str(exactly_257_digits) not in report
    assert bench._privacy_safe_report_value(exactly_257_digits) in report
    assert "| negative_integer | -42 |" in report
    assert "| negative_float | -1.25 |" in report
    assert "| boolean | true |" in report


def test_oversized_integer_projection_and_report_never_stringify_it():
    more_than_4300_digits = 10**5000

    projected = bench._privacy_safe_report_value(more_than_4300_digits)
    assert projected.startswith("[redacted-sha256:")

    results = {
        "clips": more_than_4300_digits,
        "configuration": {
            "model_size": "small",
            "device": "cpu",
            "language": "es",
            "warmup_runs": more_than_4300_digits,
            "repetitions": 3,
        },
        "environment": {"oversized_integer": more_than_4300_digits},
        "engines": {},
        "comparison": {
            "comparable": False,
            "reason": "comparison unavailable",
            "scored_clip_ids_by_engine": {},
        },
    }

    report = bench.format_report(results)
    assert report.count(projected) == 3


def test_projection_redacts_objects_with_failing_conversion():
    class ConversionFailure:
        def __str__(self):
            raise ValueError("private conversion failure")

        def __repr__(self):
            raise ValueError("private representation failure")

    projected = bench._privacy_safe_report_value(ConversionFailure())
    assert projected.startswith("[redacted-sha256:")
    assert "private" not in projected


def test_load_audio_downmixes_stereo_and_resamples_to_16k(monkeypatch):
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
    assert numpy.allclose(mono, 0.0)

    src = numpy.linspace(-1.0, 1.0, num=80, dtype=numpy.float32)
    monkeypatch.setitem(
        sys.modules,
        "soundfile",
        types.SimpleNamespace(read=lambda path, dtype, always_2d: (src, 8000)),
    )
    resampled = bench.load_audio("y.wav")
    assert resampled.dtype == numpy.float32
    assert resampled.shape[0] == 160


def test_main_passes_controls_collects_evidence_and_writes_report(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps([{"id": "a", "audio": "a.wav", "reference": "hola"}]),
        encoding="utf-8",
    )
    output = tmp_path / "report.md"
    captured = {}

    def fake_run(clips, engines, **kwargs):
        captured.update(kwargs)
        return {
            "clips": 1,
            "configuration": {
                "model_size": kwargs["model_size"],
                "device": kwargs["device"],
                "language": kwargs["language"],
                "warmup_runs": kwargs["warmup_runs"],
                "repetitions": kwargs["repetitions"],
            },
            "environment": kwargs["environment"],
            "engines": {},
            "comparison": {
                "comparable": False,
                "reason": "fake",
                "scored_clip_ids_by_engine": {},
            },
        }

    monkeypatch.setattr(bench, "run_benchmark", fake_run)
    monkeypatch.setattr(bench, "collect_environment_evidence", lambda: {"python": "test"})
    rc = bench.main(
        [
            "--manifest",
            str(manifest),
            "--engines",
            "faster-whisper",
            "--warmup-runs",
            "2",
            "--repetitions",
            "5",
            "--output",
            str(output),
        ]
    )
    assert rc == 0
    assert captured["warmup_runs"] == 2
    assert captured["repetitions"] == 5
    assert captured["environment"] == {"python": "test"}
    assert output.exists()
    assert "ASR benchmark" in output.read_text(encoding="utf-8")


def test_validate_only_exits_before_environment_or_engine_loading(monkeypatch, capsys):
    clips = _accepted_corpus()
    summary = {
        "accepted": True,
        "manifest_sha256": "a" * 64,
        "clips": 36,
    }
    monkeypatch.setattr(bench, "load_manifest", lambda path, **kwargs: clips)
    validate = mock.Mock(return_value=summary)
    run = mock.Mock()
    evidence = mock.Mock()
    monkeypatch.setattr(bench, "validate_accepted_corpus", validate)
    monkeypatch.setattr(bench, "run_benchmark", run)
    monkeypatch.setattr(bench, "collect_environment_evidence", evidence)

    assert bench.main(["--manifest", "/private/manifest.json", "--validate-only"]) == 0

    validate.assert_called_once_with(clips, manifest_path="/private/manifest.json")
    run.assert_not_called()
    evidence.assert_not_called()
    output = capsys.readouterr().out
    assert '"accepted": true' in output
    assert "/private/" not in output


def test_required_acceptance_failure_precedes_engine_loading(monkeypatch):
    clips = _accepted_corpus()
    monkeypatch.setattr(bench, "load_manifest", lambda path, **kwargs: clips)
    run = mock.Mock()
    evidence = mock.Mock()
    monkeypatch.setattr(
        bench,
        "validate_accepted_corpus",
        mock.Mock(side_effect=ValueError("accepted corpus is incomplete")),
    )
    monkeypatch.setattr(bench, "run_benchmark", run)
    monkeypatch.setattr(bench, "collect_environment_evidence", evidence)

    with pytest.raises(SystemExit) as raised:
        bench.main(
            [
                "--manifest",
                "/private/manifest.json",
                "--require-accepted-corpus",
            ]
        )

    assert raised.value.code == 2
    run.assert_not_called()
    evidence.assert_not_called()
