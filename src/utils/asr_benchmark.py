#!/usr/bin/env python3
"""Side-by-side ASR benchmark: faster-whisper vs Parakeet (design doc §6).

Computes Word Error Rate (WER) and latency for each engine over a manifest of
``(audio, reference)`` pairs, so the LATAM-Spanish gate in
``docs/planning/PLUGGABLE_ASR_AND_PARAKEET.md`` can be run on real audio before
Parakeet is recommended for Spanish.

The WER, manifest, and report logic is dependency-free and unit-tested. Audio
loading (soundfile) and the engines are imported lazily; an engine whose backend
is not installed is reported as *unavailable* rather than crashing the run.

Usage::

    uv run python -m src.utils.asr_benchmark --manifest clips.json \\
        --engines faster-whisper,parakeet --model small --language es

Manifest format (JSON list)::

    [{"audio": "ar/01.wav", "reference": "hola que tal", "accent": "AR"}, ...]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

_PUNCT_RE = re.compile(r"[^\w']+", re.UNICODE)


def normalize_for_wer(text: str) -> List[str]:
    """Lowercase, strip punctuation, and split into a word list."""
    return _PUNCT_RE.sub(" ", (text or "").lower()).split()


def _edit_distance(ref: Sequence[str], hyp: Sequence[str]) -> int:
    """Levenshtein distance over token sequences (substitutions+deletions+insertions)."""
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


def word_error_rate(reference: str, hypothesis: str) -> float:
    """WER = edit_distance(ref_words, hyp_words) / len(ref_words).

    An empty reference yields 0.0 against an empty hypothesis, else 1.0 (any
    output against no expected words is fully wrong).
    """
    ref = normalize_for_wer(reference)
    hyp = normalize_for_wer(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return _edit_distance(ref, hyp) / len(ref)


def load_manifest(path) -> List[Dict[str, object]]:
    """Load a JSON manifest: a list of ``{audio, reference, accent?, language?}``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("manifest must be a JSON list of clip objects")
    clips: List[Dict[str, object]] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or "audio" not in item or "reference" not in item:
            raise ValueError(f"clip {index} must have 'audio' and 'reference' fields")
        clips.append(item)
    return clips


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
    if audio.ndim > 1:  # downmix to mono
        audio = audio.mean(axis=1)
    if sample_rate != 16000 and audio.shape[0] > 0:  # linear resample to 16 kHz
        target_len = int(round(audio.shape[0] * 16000 / float(sample_rate)))
        if target_len > 0:
            old_idx = np.linspace(0.0, 1.0, num=audio.shape[0], endpoint=False)
            new_idx = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
            audio = np.interp(new_idx, old_idx, audio).astype(np.float32)
    return np.asarray(audio, dtype=np.float32)


def _default_engine_factory():
    try:
        from ..stt.asr_engine import create_engine
    except ImportError:  # script-style import with project root on sys.path
        from src.stt.asr_engine import create_engine
    return create_engine


def run_benchmark(
    clips: Sequence[Dict[str, object]],
    engines: Sequence[str],
    *,
    model_size: str = "small",
    device: str = "cpu",
    language: Optional[str] = None,
    audio_loader: Optional[Callable] = None,
    engine_factory: Optional[Callable] = None,
) -> Dict[str, object]:
    """Run each engine over each clip and return a structured results dict.

    ``audio_loader`` and ``engine_factory`` are injectable so the orchestration
    can be unit-tested without real models or audio files.
    """
    audio_loader = audio_loader or load_audio
    engine_factory = engine_factory or _default_engine_factory()

    results: Dict[str, object] = {"clips": len(clips), "engines": {}}
    for name in engines:
        try:
            engine = engine_factory(
                name, model_size=model_size, device=device, language=language
            )
        except (RuntimeError, ValueError) as exc:
            results["engines"][name] = {"available": False, "error": str(exc)}
            continue

        per_clip: List[Dict[str, object]] = []
        for clip in clips:
            try:
                audio = audio_loader(clip["audio"])
                started = time.monotonic()
                hypothesis = engine.transcribe(audio, language or clip.get("language"))
                elapsed = time.monotonic() - started
            except Exception as exc:  # one bad/missing clip must not abort the run
                per_clip.append(
                    {
                        "audio": clip["audio"],
                        "accent": clip.get("accent"),
                        "reference": clip["reference"],
                        "error": str(exc),
                    }
                )
                continue
            per_clip.append(
                {
                    "audio": clip["audio"],
                    "accent": clip.get("accent"),
                    "reference": clip["reference"],
                    "hypothesis": hypothesis,
                    "wer": word_error_rate(str(clip["reference"]), hypothesis),
                    "latency_s": elapsed,
                }
            )
        scored = [c for c in per_clip if "wer" in c]
        wers = [c["wer"] for c in scored]
        latencies = [c["latency_s"] for c in scored]
        results["engines"][name] = {
            "available": True,
            "mean_wer": statistics.mean(wers) if wers else None,
            "median_latency_s": statistics.median(latencies) if latencies else None,
            "errors": sum(1 for c in per_clip if "error" in c),
            "per_clip": per_clip,
        }
    return results


def _per_accent_wer(per_clip: Sequence[Dict[str, object]]) -> Dict[str, float]:
    buckets: Dict[str, List[float]] = defaultdict(list)
    for clip in per_clip:
        if "wer" in clip:  # skip clips that errored during transcription
            buckets[str(clip.get("accent") or "?")].append(clip["wer"])
    return {accent: statistics.mean(wers) for accent, wers in buckets.items() if wers}


def format_report(results: Dict[str, object]) -> str:
    lines = [
        "# ASR benchmark (faster-whisper vs Parakeet)",
        "",
        f"Clips: {results['clips']}",
        "",
        "| Engine | Mean WER | Median latency (s) | Status |",
        "| --- | --- | --- | --- |",
    ]
    engines = results["engines"]
    for name, data in engines.items():
        if not data.get("available"):
            lines.append(f"| {name} | – | – | unavailable: {data.get('error', '')} |")
        else:
            errors = data.get("errors", 0)
            status = "ok" if not errors else f"ok ({errors} clip error(s))"
            mean = data["mean_wer"]
            wer_cell = f"{mean:.3f}" if mean is not None else "– (no scored clips)"
            lat = data["median_latency_s"]
            lat_cell = f"{lat:.3f}" if lat is not None else "–"
            lines.append(f"| {name} | {wer_cell} | {lat_cell} | {status} |")

    accents = sorted(
        {
            str(clip.get("accent") or "?")
            for data in engines.values()
            if data.get("available")
            for clip in data["per_clip"]
        }
    )
    if accents:
        lines += ["", "## Mean WER by accent", "", "| Engine | " + " | ".join(accents) + " |",
                  "| --- |" + " --- |" * len(accents)]
        for name, data in engines.items():
            if not data.get("available"):
                continue
            by_accent = _per_accent_wer(data["per_clip"])
            cells = " | ".join(
                f"{by_accent[a]:.3f}" if a in by_accent else "–" for a in accents
            )
            lines.append(f"| {name} | {cells} |")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark ASR engines by Word Error Rate and latency."
    )
    parser.add_argument(
        "--manifest", required=True,
        help="JSON list of {audio, reference, accent?, language?}",
    )
    parser.add_argument(
        "--engines", default="faster-whisper,parakeet",
        help="comma-separated engine names (default: faster-whisper,parakeet)",
    )
    parser.add_argument("--model", default="small", help="faster-whisper model size")
    parser.add_argument("--device", default="cpu", help="cpu or cuda")
    parser.add_argument(
        "--language", default=None,
        help="force a language code (default: per-clip / engine auto-detect)",
    )
    parser.add_argument("--output", default=None, help="write the markdown report here")
    args = parser.parse_args(argv)

    clips = load_manifest(args.manifest)
    engines = [name.strip() for name in args.engines.split(",") if name.strip()]
    results = run_benchmark(
        clips, engines, model_size=args.model, device=args.device, language=args.language
    )
    report = format_report(results)
    print(report)
    if args.output:
        Path(args.output).write_text(report + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
