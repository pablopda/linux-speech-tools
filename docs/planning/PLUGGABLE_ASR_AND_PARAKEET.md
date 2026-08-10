# Design Proposal: Pluggable ASR Backends + NVIDIA Parakeet

**Status:** Implementation complete; real-audio validation pending — maintenance
gate blocked · **Type:** Architecture / STT
**Prompted by:** Analysis of [FluidVoice](https://github.com/altic-dev/FluidVoice) (altic.dev/fluid)
**Author:** Research team (3-agent analysis) · **Date:** 2026-06-23

---

## Executive Summary

**Recommendation: add a pluggable ASR backend layer and offer NVIDIA Parakeet
(via `onnx-asr`, CPU-capable) as an optional engine, while keeping
faster-whisper as the default — especially for LATAM Spanish users.**

This proposal came out of evaluating whether FluidVoice ("Fluid", at
altic.dev/fluid) has a better model or approach we could adopt for Linux.

**Verdict on FluidVoice:** We cannot use FluidVoice itself. It is a native
macOS-only app (Swift + CoreML + Apple Neural Engine, GPLv3) whose fast models
ship as Apple-only CoreML bundles. **But its core idea is portable**: it runs
**NVIDIA Parakeet** (not just Whisper) behind a **model-abstraction layer**.
Parakeet is available cross-platform as ONNX and runs on Linux CPU. The two
borrowable ideas are (1) a pluggable engine interface and (2) Parakeet as a
faster, natively-punctuated alternative to Whisper.

This document scoped the change, the exact code seams, the trade-offs, and a
validation plan. **Implemented:** Phase 1 (engine abstraction, `src/stt/asr_engine.py`)
and Phase 2 (`ParakeetOnnxBackend` + `STT_ENGINE`/`--engine` selection + the
opt-in `stt-parakeet` extra + setup integration) have shipped; Phase 3 added the
WER benchmark harness (`src/utils/asr_benchmark.py`) and the docs/quickstart. The
§6 LATAM-Spanish benchmark still needs real audio before Parakeet is recommended
for Spanish. The canonical
[`LATAM_ASR_BENCHMARK_2026.md`](../benchmarks/LATAM_ASR_BENCHMARK_2026.md)
report remains explicitly pending; faster-whisper remains the global default
regardless of the eventual result.

---

## 1. Background: What is FluidVoice and what can we actually reuse?

| Aspect | FluidVoice | Reusable on Linux? |
| --- | --- | --- |
| App / UI | Swift + SwiftUI/AppKit, macOS 15+ | ❌ No |
| ASR runtime | `FluidAudio` SDK (CoreML + Apple Neural Engine) | ❌ No (`platforms: [.macOS, .iOS]`) |
| Fast models | Parakeet / Nemotron / Cohere as `.mlmodelc` CoreML bundles | ❌ Weights are Apple-only |
| "Fluid Intelligence" enhancement | Closed-source local model, undisclosed runtime | ❌ Proprietary |
| Whisper path | via `SwiftWhisper` (whisper.cpp) | ➖ We already have faster-whisper |
| **Model choice (Parakeet)** | NVIDIA Parakeet TDT 0.6B v2/v3 | ✅ **Available as ONNX for Linux** |
| **Model-abstraction architecture** | Multiple engines behind one interface | ✅ **Portable design pattern** |
| **Two-tier UX** (fast live preview + accurate final) | Streaming model → overlay; accurate model → final | ✅ Portable concept (future work) |
| License | GPLv3 (copyleft) | ⚠️ Read for ideas only; do not copy code |

> **Myth check:** FluidVoice's marketing mentions "optimized routing" between
> models. This is **not** an implemented auto-selection feature — model choice
> is a manual, language-first config step. We should not try to replicate
> "routing"; a simple per-engine config flag is the honest equivalent.

---

## 2. The model question: is Parakeet "better" than Whisper?

| | Parakeet TDT 0.6B **v2** | Parakeet TDT 0.6B **v3** | faster-whisper (large-v3) |
| --- | --- | --- | --- |
| English WER | **6.05%** | ~6% | 7.44% |
| Speed (GPU, RTFx) | **~3380** | ~3380 | ~145 |
| Speed (CPU) | ~30–100× realtime | ~30–100× realtime | small ≈ 2–5× realtime |
| Params / int8 size | 600M / ~640 MB | 600M / ~640 MB | 1.55B / larger |
| Languages | **English only** | **25 European (incl. Spanish), auto-detect** | 99 languages |
| Punctuation / caps | **Native** | **Native** | Needs post-processing |
| License | CC-BY-4.0 | CC-BY-4.0 | MIT |

**The honest nuances (these shape the recommendation):**

1. **The eye-popping speed is GPU-bound.** On a CPU-only laptop Parakeet is a
   *nice* upgrade — moderately faster, native punctuation, less hallucination
   on silence/short clips — not a *transformative* one. For short 2–10s
   dictation utterances, both engines feel instant; the throughput win shows up
   on long dictations and with an NVIDIA GPU (where Parakeet is a clear winner).

2. **⚠️ LATAM Spanish caveat (the decisive point for this project).** Parakeet
   v3's Spanish is trained on **European/Castilian-leaning** corpora and is
   explicitly framed as "25 *European* languages." Whisper's broader, noisier
   training data generally handles **LATAM accents and code-switching** better.
   Since this project leans LATAM, **faster-whisper should stay the default**,
   and Parakeet-Spanish must be validated on real LATAM audio before being
   recommended for Spanish users (see §6).

3. **Use v3, never v2** for this project — v2 is English-only.

**Bottom line:** Parakeet is worth *offering*, not *forcing*. A pluggable
backend gives English/GPU users a real upgrade while protecting the
LATAM-Spanish experience that already works well on Whisper.

---

## 3. Current architecture: where faster-whisper is wired in

> **Historical pre-refactor snapshot.** This section describes the seam as it
> existed when the proposal was written. The current implementation routes
> both engines through `src/stt/asr_engine.py`; it must not be read as a current
> hard-coding defect.

The STT core lives in `src/stt/`. The pipeline is **already cleanly layered at
both ends** — audio in and text out are engine-agnostic — but the **model is
hardcoded in the middle**.

```
ffmpeg capture ──► webrtcvad segmentation ──► [ WhisperModel.transcribe() ] ──► output callback
  (engine-agnostic)      (engine-agnostic)          ^^^ HARDCODED ^^^            (engine-agnostic)
```

### Already decoupled (no changes needed)

- **Audio capture** — `runtime.py:235` `audio_capture_candidates()` builds an
  ffmpeg command producing **16 kHz, mono, `s16le` PCM** (`session.py:88-90`).
  Backend selection via `STT_AUDIO_BACKEND` / `STT_AUDIO_DEVICE`. Engine-neutral.
- **VAD segmentation** — `webrtcvad` in `session.py:91` + `session.py:260`
  decides *utterance boundaries* upstream of the model. Engine-neutral.
- **Output** — `OutputHandler = Callable[[str], bool]` (`session.py:53`),
  injected via the `output_handler` constructor arg. Clipboard and typing modes
  already plug in here. Parakeet returns plain text, so this needs no change.

### Hardcoded to faster-whisper (the seams to refactor)

| What | Where | Note |
| --- | --- | --- |
| Model import | `session.py:33-37` | `from faster_whisper import WhisperModel` |
| Compute-type pick | `session.py:80` → `runtime.py:165` `compute_type_for_device()` | `int8` (CPU) / `float16` (GPU) — **CTranslate2-specific** |
| Model construction | `session.py:81-85` | `WhisperModel(model_size, device, compute_type)` |
| **Transcription call** | `session.py:150-160` | `segments, _ = self.model.transcribe(audio, language, beam_size=5, vad_filter=True, vad_parameters=...)` |
| Segment join | `session.py:162` | `" ".join(seg.text.strip() for seg in segments)` — assumes Whisper's segment objects |

Everything Whisper-specific (`beam_size`, `vad_filter`, segment objects,
`compute_type`) is concentrated in `transcribe_buffer()` (`session.py:141-168`)
and the constructor (`session.py:80-86`). That is the entire surface to abstract.

**Modularity score: ~6/10** — clean at the edges, coupled in the model layer.

---

## 4. Proposed design

### 4.1 A minimal engine interface

New file `src/stt/asr_engine.py`:

```python
from __future__ import annotations
from typing import Optional, Protocol
import numpy as np

class ASREngine(Protocol):
    """Turns a mono 16 kHz float32 utterance into text."""
    def transcribe(self, audio: np.ndarray, language: Optional[str]) -> str: ...
```

The contract is deliberately tiny: **float32 mono 16 kHz array in, plain text
out.** That is exactly what `transcribe_buffer()` already produces
(`np.concatenate(self.audio_buffer)`) and consumes (a joined string), so the
session loop barely changes.

### 4.2 Two backends

```python
class FasterWhisperBackend:
    """Default. Wraps existing WhisperModel + segment-join logic verbatim."""
    def __init__(self, model_size, device): ...     # owns compute_type_for_device()
    def transcribe(self, audio, language):
        segments, _ = self._model.transcribe(
            audio, language=language, beam_size=5,
            vad_filter=True, vad_parameters=dict(
                threshold=0.5, min_silence_duration_ms=500, speech_pad_ms=200),
        )
        return " ".join(s.text.strip() for s in segments)

class ParakeetOnnxBackend:
    """Optional. NVIDIA Parakeet TDT 0.6B v3 via onnx-asr (CPU or CUDA)."""
    def __init__(self, model_name="nemo-parakeet-tdt-0.6b-v3", device="cpu"):
        import onnx_asr
        self._model = onnx_asr.load_model(model_name)   # int8, ~640 MB
    def transcribe(self, audio, language):
        # Parakeet emits punctuation + capitalization natively.
        return self._model.recognize(audio)             # see §7 integration note
```

### 4.3 Selecting the engine

A factory `create_engine(name, *, model_size, device, language=None) -> ASREngine`, chosen by:

- **Env:** `STT_ENGINE=faster-whisper` (default) `| parakeet`
- **CLI:** `--engine faster-whisper|parakeet` on `talk2claude-faster`
- Optional later: auto-pick `parakeet` when an NVIDIA GPU is detected.

### 4.4 The only change inside the session

`FasterWhisperSession.__init__` swaps the hardcoded constructor for the factory,
and `transcribe_buffer()` replaces the `self.model.transcribe(...)` block with a
single `text = self.engine.transcribe(audio, self.language)`. The VAD loop,
audio capture, status writes, and output callback are **untouched**.

> Naming: consider renaming `FasterWhisperSession` → `DictationSession` once it
> is engine-agnostic, keeping a thin alias for compatibility. Optional, can be
> deferred to avoid churn.

### 4.5 Packaging

Add a separate uv extra so Parakeet stays opt-in and never bloats the default
install (note: `onnxruntime` is already a dependency of the `kokoro`/`reader`
extras, so it is familiar to the project):

```toml
# pyproject.toml [project.optional-dependencies]
stt-parakeet = [
    "onnx-asr[cpu,hub]>=0.11.0",   # MIT; pulls only numpy + onnxruntime + hub
]
stt-parakeet-gpu = [
    "onnx-asr[hub]>=0.11.0",
    "onnxruntime-gpu[cuda,cudnn]==1.23.2",  # tested CUDA 12/cuDNN runtime
]
```

```bash
uv sync --extra stt --extra stt-parakeet      # opt-in CPU runtime
uv sync --extra stt --extra stt-parakeet-gpu  # opt-in GPU runtime; choose one
```

`onnx-asr` is **MIT**, depends only on `numpy` + `onnxruntime`, supports Python
3.10–3.14, and downloads the pre-converted model from Hugging Face on first use.

---

## 5. Why `onnx-asr` (and not NeMo or sherpa-onnx)

| Runtime | Install weight | CPU? | uv-friendly? | Verdict |
| --- | --- | --- | --- | --- |
| **`onnx-asr`** | Tiny (`numpy`+`onnxruntime`, MIT) | ✅ | ✅ Excellent | **Pick.** Lightest, can host Whisper *and* Parakeet. |
| `sherpa-onnx` | Light (C++ core + py bindings, Apache-2.0) | ✅ | ✅ Good | Strong alt; better VAD/streaming ergonomics for future work. |
| NeMo (`nemo_toolkit[asr]`) | Heavy (PyTorch ≥2.7, pins `numpy<2.0`) | ✅ (slow) | ❌ Poor | Reject — dependency conflicts, multi-GB. GPU/research only. |
| CTranslate2 / transformers | n/a | — | — | Reject — no mainstream Parakeet support path. |

**Reference implementation worth studying:**
[`danielrosehill/parakeet-dictation`](https://github.com/danielrosehill/parakeet-dictation)
— Wayland on-device dictation, sherpa-onnx, **no GPU, installs via uv**. It is
essentially a working blueprint of exactly the optional path proposed here.

---

## 6. LATAM Spanish validation gate

The implementation and fixture-based harness tests are complete, but the
real-audio comparison is **not**. The canonical pending report and execution
record is
[`LATAM_ASR_BENCHMARK_2026.md`](../benchmarks/LATAM_ASR_BENCHMARK_2026.md).
Do not infer ASR quality from unit tests or fill its result tables with synthetic
audio.

The accepted corpus must contain exactly 36 consented, privacy-reviewed clips:
24 AR and 4 each MX, CO and CL. It must cover everyday speech,
programming vocabulary, English↔Spanish code-switching, filenames/repository
names, short commands, long prompts, punctuation, silence and interrupted
speech. Raw audio stays outside git; the private manifest records human-reviewed
references, metadata and checksums.
Use the privacy-safe guided acquisition workflow in
[`LATAM_CORPUS_ACQUISITION.md`](../benchmarks/LATAM_CORPUS_ACQUISITION.md).
It requires per-recording pseudonymous speaker IDs plus explicit authentic-
accent confirmation and a separate post-capture privacy review. A speaker token
may be reused within one accent but must never span AR/MX/CO/CL; the MX, CO and
CL clips must be supplied by genuinely matching speakers rather than imitated.

Both engines must run on the identical complete corpus and target machine. The
harness compares privacy-safe clip IDs and marks the result not comparable if an
engine is unavailable, the scored sets differ, either set is incomplete, or
measured repetitions produce inconsistent hypotheses.
The validation-only preflight enforces the exact accent distribution, required
coverage labels, bounded reviewed metadata, checksums, unique decodable audio,
and declared audio properties before any engine is loaded. It emits only an
aggregate summary and manifest checksum. Human review must still establish
consent, accent authenticity and reference correctness; a successful preflight
is not an ASR quality claim. Generated
configuration/environment cells are bounded and replace paths, multiline/table
content and unsupported values with stable redaction hashes; reviewed exact
model revisions remain part of the private execution record.
Record exact models/revisions, runtime versions, device and execution-provider
evidence, warm-up/repetition policy, overall and per-accent corpus WER,
code-switch corpus WER, median per-clip latency, errors and human review of
punctuation and technical-token fidelity. Corpus WER is total token edit
distance divided by total reference words, never a mean of per-clip WERs. Claim
GPU results only when diagnostics demonstrate that the intended GPU provider
executed the models; a list of available providers is not execution proof. The
Parakeet backend preloads ONNX Runtime's packaged CUDA/cuDNN libraries when that
API is available, then recursively verifies the actual core `onnx-asr` session
providers. Silent CPU or mixed fallbacks remain usable only with explicit
actual-device labels and make a mixed-device benchmark non-comparable.

Parakeet Spanish passes only if:

1. the harness reports an identical, complete scored set for both engines;
2. overall corpus WER is within `0.01` absolute of faster-whisper;
3. code-switch-subset corpus WER is no worse by more than `0.05` absolute; and
4. review finds no systematic loss on filenames, repository names or developer
   terms.

Passing changes only the Parakeet Spanish support label beyond experimental.
Failing records the measured limitation. **Neither outcome changes the global
faster-whisper default.** Once the report has complete evidence and sign-off,
this initiative moves to maintenance-only; until then, its maintenance gate is
blocked on the real-audio run.

---

## 7. Risks & open questions

| Risk / question | Severity | Mitigation |
| --- | --- | --- |
| **`onnx-asr` input signature** — does `recognize()` accept a float32 numpy array, or only a file path / specific sample rate? | Medium | Verify during spike. If file-only, write the buffer to a temp WAV (audio is already 16 kHz mono). Confirm before committing the backend. |
| `compute_type_for_device()` is CTranslate2-only | Low | Move it inside `FasterWhisperBackend`; Parakeet picks CPU vs CUDA execution provider independently. |
| First-run model download (~640 MB) | Low | Wire into `linux-speech-tools-setup` (`src/utils/setup_models.py`) like existing model checks; document offline pre-fetch. |
| LATAM Spanish quality regression | **High (for ES users)** | §6 validation gate and canonical pending report; faster-whisper stays default on pass or fail. |
| CPU-only users see little speedup | Medium | Frame Parakeet as "punctuation + robustness" win on CPU, "speed" win on GPU. Set expectations in docs. |
| Parakeet CC-BY-4.0 attribution | Low | Add NVIDIA attribution line to README/NOTICE. |
| GPLv3 contamination from FluidVoice | Low | We borrow *ideas and public ONNX models only*, never FluidVoice code. |
| Streaming live-preview UX | Out of scope | Parakeet TDT is offline/batch in these runtimes; true streaming is a separate future effort (Parakeet-EOU / sherpa streaming). |

---

## 8. Implementation and maintenance state

1. **Phase 1 — complete.** The `ASREngine` abstraction and
   `FasterWhisperBackend` shipped without changing the default engine.
2. **Phase 2 — complete.** `ParakeetOnnxBackend`, `STT_ENGINE`/`--engine`, the
   opt-in extra and setup-model integration shipped.
3. **Phase 3 — partially complete.** The harness, automated tests, quickstart
   and report scaffold shipped. The required real-audio LATAM run and sign-off
   remain pending; this is the only closure blocker.
4. **Phase 4 — deferred future proposal.** Two-tier streaming UX remains out of
   scope and does not block Phase 3 closure.

After the §6 report is signed, move this initiative to maintenance-only whether
Parakeet passes or fails. Reopen implementation only for demonstrated runtime,
dependency/model or security changes, or for a separately approved product
requirement.

---

## 9. Recommendation

**Phases 1–2 and the Phase 3 tooling are complete.** Keep faster-whisper the
global default. Parakeet v3 remains an opt-in engine, and Spanish remains
experimental until the §6 real-audio gate is signed. A benchmark pass may
change that support label but does not authorize a default-engine change. This
preserves the project's CPU-first, local, Linux, uv-based identity while the
last quality claim remains evidence-gated.

---

## Sources

**FluidVoice / FluidAudio (Apple-locked):**
- https://github.com/altic-dev/FluidVoice · `Package.swift`, README
- https://github.com/FluidInference/FluidAudio (`platforms: [.macOS, .iOS]`, CoreML/ANE)
- https://huggingface.co/nvidia/parakeet_realtime_eou_120m-v1

**Parakeet on Linux:**
- https://github.com/istupakov/onnx-asr · https://pypi.org/project/onnx-asr/
- https://k2-fsa.github.io/sherpa/onnx/pretrained_models/index.html
- https://github.com/danielrosehill/parakeet-dictation · https://github.com/achetronic/parakeet
- https://pypi.org/project/nemo-toolkit/

**Models / benchmarks / licensing:**
- https://huggingface.co/blog/open-asr-leaderboard
- https://arxiv.org/abs/2509.14128 (Parakeet TDT v3 multilingual)
- https://venturebeat.com/ai/nvidia-launches-fully-open-source-transcription-ai-model-parakeet-tdt-0-6b-v2-on-hugging-face (CC-BY-4.0)
- https://github.com/SYSTRAN/faster-whisper

**Confidence note:** README, both `Package.swift` files, and FluidAudio docs were
read directly. FluidVoice's exact internal Swift protocol names and the model
behind "Fluid Intelligence" are not publicly documented (closed-source) and are
inferred from structure. The `onnx-asr` numpy-input signature (§7) was verified
against onnx-asr 0.11.0 before writing the backend: `recognize()` accepts a
float32 mono array directly with `sample_rate=16000` and returns a plain string,
so no temp-WAV path is needed.
