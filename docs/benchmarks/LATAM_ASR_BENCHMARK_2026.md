# LATAM Spanish ASR Benchmark — 2026

**Status:** PENDING — the real-audio run has not been performed or recorded<br>
**Initiative:** Parakeet Spanish validation<br>
**Current default:** `faster-whisper`<br>
**Implementation baseline:** `main` at
[`0cd89d6`](https://github.com/pablopda/linux-speech-tools/commit/0cd89d695e1aa2bb5c20f1cc847ee9880685389d)

This is the canonical report for the LATAM Spanish comparison required by
[`PLUGGABLE_ASR_AND_PARAKEET.md`](../planning/PLUGGABLE_ASR_AND_PARAKEET.md#6-latam-spanish-validation-gate).
It is deliberately a pending report scaffold: no real-audio WER, latency,
quality or GPU result exists in the repository as of 2026-08-10. The benchmark
harness has automated tests, but those tests use fixtures and fake engines and
are not evidence of ASR quality.

Regardless of the eventual result, `faster-whisper` remains the global default.
A pass changes only whether Parakeet may be described as supported rather than
experimental for Spanish.

## 1. Corpus acceptance record

The accepted corpus must contain exactly 36 consented clips. Raw audio and the
full manifest—including references, paths and speaker IDs—stay outside git.
Only the manifest checksum, a sanitized corpus description, privacy-safe clip
IDs and reviewed aggregate results may be retained in the repository.

| Accent | Required clips | Accepted clips | Status |
| --- | ---: | ---: | --- |
| Argentina (AR) | 24 | _pending_ | pending |
| Mexico (MX) | 4 | _pending_ | pending |
| Colombia (CO) | 4 | _pending_ | pending |
| Chile (CL) | 4 | _pending_ | pending |
| **Total** | **36** | _pending_ | **pending** |

The corpus must cover everyday speech, programming vocabulary,
English↔Spanish code-switching, filenames and repository names, short commands,
long prompts, spoken punctuation, silence and interrupted speech. References
must be transcribed and then independently reviewed by a fluent speaker.

For every clip, record:

- a pseudonymous clip and speaker ID, accent, category and duration;
- explicit confirmation that the speaker authentically supplies that accent;
- the exact human-reviewed reference and code-switch classification;
- sample rate, channel count, capture device and recording environment;
- consent status, privacy-review status and a SHA-256 audio checksum; and
- an external audio path. Do not commit raw or personally identifying audio.

The generated aggregate report emits `id`, `accent` and `category` labels, so
those fields must be bounded pseudonymous ASCII tokens using only letters,
digits, dot, underscore and hyphen. Never place a path, speaker name, transcript
fragment or free-form note in them. Speaker IDs remain private and are not
emitted by the harness. Use the fixed accent-scoped pseudonym form
`speaker-ar-01`, `speaker-mx-01`, `speaker-co-01` or `speaker-cl-01` (with a
two- or three-digit suffix); names are not accepted by the guided workflow or
strict preflight.

The harness requires `audio` and `reference` and accepts additional manifest
metadata. A manifest entry may use this shape:

```json
{
  "id": "ar-001",
  "audio": "/private/latam-asr/ar-001.wav",
  "reference": "Abrí README.md en linux-speech-tools.",
  "reference_reviewed": true,
  "accent": "AR",
  "category": "filename-dev-term",
  "code_switch": true,
  "speaker_id": "speaker-ar-01",
  "accent_authenticity": "confirmed",
  "consent": "recorded",
  "privacy_review": "passed",
  "sha256": "<64 lowercase hexadecimal characters>",
  "sample_rate": 16000,
  "channels": 1,
  "duration_s": 4.25,
  "capture_device": "device-01",
  "recording_environment": "quiet-room"
}
```

Use the canonical category labels `everyday`, `programming`,
`filename-dev-term`, `short-command`, `long-prompt`, `punctuation-heavy`,
`silence` and `interrupted` at least once each; set `code_switch` on at least one
reviewed English↔Spanish clip. The strict preflight requires exactly AR=24,
MX=4, CO=4 and CL=4, verifies every required field, rejects duplicate audio
checksums, hashes and decodes each bounded regular audio file, and compares the
declared sample rate, channels and duration with the file. It emits only an
aggregate summary and the manifest checksum. Passing this mechanical preflight
does **not** establish consent, reference correctness, accent authenticity, ASR
quality or reviewer acceptance; those remain human evidence gates.

Do not reuse one speaker token across accents or ask one speaker to imitate all
four accents. Each MX, CO and CL subset must come from a genuinely matching
speaker, and the strict preflight rejects a pseudonymous speaker ID that spans
accent labels. Use the private, resumable
[`lst-asr-corpus` acquisition workflow](LATAM_CORPUS_ACQUISITION.md) to create,
record, review and validate the corpus without writing audio to the repository.

**Corpus acceptance:** _pending_<br>
**Reviewer:** _pending_<br>
**Manifest location/checksum:** _pending_

## 2. Reproducible environment

Complete this before running either engine. The two engines must receive the
same accepted clips and references on the same target machine.

| Field | Recorded value |
| --- | --- |
| Run date and report author | _pending_ |
| Repository commit | _pending_ |
| Distribution / kernel / architecture | _pending_ |
| Python and `uv` versions | _pending_ |
| CPU and RAM | _pending_ |
| GPU and VRAM | _pending_ |
| NVIDIA driver / CUDA versions | _pending_ |
| ONNX Runtime execution providers | _pending_ |
| faster-whisper / CTranslate2 versions | _pending_ |
| `onnx-asr` / ONNX Runtime versions | _pending_ |
| Exact model IDs, revisions and quantization | _pending_ |
| Engine device / compute-type settings | _pending_ |
| Audio format and preprocessing | _pending_ |
| Warm-up runs / measured repetitions | _pending_ |
| Generated provider evidence | _pending_ |
| Independent proof of provider actually used | _pending_ |

Do not label a result GPU-backed merely because an NVIDIA device is installed.
Record the execution providers and engine diagnostics that demonstrate the
actual run used the GPU. If both engines cannot run genuinely on the intended
GPU path, use a consistently labelled CPU comparison or report separate,
non-comparable device results.

For Parakeet, the backend calls `onnxruntime.preload_dlls()` when available
before model construction, then recursively reads the providers of the created
core `onnx-asr` inference sessions. `CUDAExecutionProvider` merely appearing in
`get_available_providers()` is insufficient: the warm check and generated
report must say `Actual device: cuda` / `verified-cuda`. A CPU or mixed fallback
is retained only with that explicit label and cannot be combined with a
genuinely CUDA-backed engine as a comparable benchmark.

On the planned target, use the RTX 2060/CUDA path as the primary latency result
only when both engines demonstrably execute there; otherwise label the fallback
and its comparability limits explicitly.

### Provider-readiness smoke — 2026-08-10 (not quality evidence)

On the target RTX 2060 machine, cached-model construction and a one-second
all-zero/silence inference were run explicitly for both engines after the
operational-provider repair:

- Parakeet reported `requested_device=cuda`, `actual_device=cuda`,
  `status=verified-cuda`; both discovered core sessions returned
  `CUDAExecutionProvider, CPUExecutionProvider`, and the provider evidence was
  unchanged after inference.
- faster-whisper reported `requested_device=cuda`, `actual_device=cuda`,
  `status=configured-cuda`, and its device evidence was unchanged after
  inference.
- both silence smokes returned zero output characters. This checks model load,
  provider selection and one bounded inference only. It is not real speech,
  does not produce WER or latency evidence, and does not advance the pending
  corpus or quality decision.

The Parakeet proof depends on calling `onnxruntime.preload_dlls()` before model
construction in this environment. Merely observing CUDA in the advertised
provider list had previously allowed the created sessions to fall back to CPU.
The reproducible GPU dependency path is
`uv sync --locked --extra stt --extra stt-parakeet-gpu`; use the separate
`stt-parakeet` extra for a CPU-only environment and do not select both variants.

## 3. Execution record

Set `LST_LATAM_MANIFEST` to the privacy-reviewed manifest outside the repository
and write generated output to a temporary file. Do not overwrite this scaffold.

```bash
export LST_LATAM_MANIFEST=/private/latam-asr/manifest.json
# Guided acquisition (run before this section; it creates no benchmark result):
./bin/lst-asr-corpus status --directory "$(dirname "$LST_LATAM_MANIFEST")"
./bin/lst-asr-corpus validate --directory "$(dirname "$LST_LATAM_MANIFEST")"

# Operational provider proof; this loads only already-approved/cached models.
./bin/talk2claude-faster --warm-model \
  --engine parakeet --device cuda --model small

# Strict corpus-only preflight; exits before environment collection or engine/model loading.
uv run python -m src.utils.asr_benchmark \
  --manifest "$LST_LATAM_MANIFEST" \
  --validate-only

# Execute only after the aggregate validation summary and private evidence are reviewed.
uv run python -m src.utils.asr_benchmark \
  --manifest "$LST_LATAM_MANIFEST" \
  --require-accepted-corpus \
  --engines faster-whisper,parakeet \
  --model small \
  --device cuda \
  --language es \
  --warmup-runs 1 \
  --repetitions 3 \
  --output /tmp/latam-asr-generated.md
```

Use `--device cpu` unless the warm checks confirm the actual provider for both
engines. The generated report records advertised ONNX providers separately from
privacy-safe per-engine actual-device and core-session provider evidence. A
requested-CUDA run that activates CPU for one engine is explicitly not
comparable. Configuration and environment values containing paths, newlines, table
delimiters, unsupported characters or excessive text are replaced in generated
output by a bounded `[redacted-sha256:…]` comparison token. Record reviewed
exact model IDs/revisions separately in this private execution record when a
redaction token appears. Preserve the exact command, stdout/stderr, generated
aggregate report and the private per-clip investigation notes. Copy only
reviewed aggregate data into the tables below.

**Exact command:** _pending_<br>
**Generated report checksum:** _pending_<br>
**Run notes / errors:** _pending_

## 4. Quantitative results

WER is reported as a fraction, so an absolute difference of `0.01` is one
percentage point. “Corpus WER” means total token edit distance divided by total
reference words over the subset; it is not the arithmetic mean of clip WERs.
Latency is the median measured latency per clip after the declared untimed
warm-up runs, and the overall latency is the median of those per-clip medians.

| Engine | Clips attempted | Clips scored | Reference words | Edit distance | Corpus WER | Median per-clip latency | Error count |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| faster-whisper | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |
| Parakeet | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ |

| Accent / subset | Identical clips scored | faster-whisper corpus WER | Parakeet corpus WER | Absolute delta (Parakeet − faster-whisper) |
| --- | ---: | ---: | ---: | ---: |
| AR | _pending_ | _pending_ | _pending_ | _pending_ |
| MX | _pending_ | _pending_ | _pending_ | _pending_ |
| CO | _pending_ | _pending_ | _pending_ | _pending_ |
| CL | _pending_ | _pending_ | _pending_ | _pending_ |
| Code-switch | _pending_ | _pending_ | _pending_ | _pending_ |
| Filename / developer term | _pending_ | _pending_ | _pending_ | _pending_ |

**Generated scored-set comparability:** _pending_<br>
**Expected privacy-safe clip IDs:** _pending_<br>
**Scored clip IDs per engine:** _pending_

## 5. Qualitative review

A fluent reviewer must examine both engines' output without relying only on
aggregate WER. Record representative error classes without copying private
audio or identifying speech into git.

| Review area | faster-whisper observation | Parakeet observation | Systematic regression? |
| --- | --- | --- | --- |
| Punctuation and capitalization | _pending_ | _pending_ | _pending_ |
| English↔Spanish code-switching | _pending_ | _pending_ | _pending_ |
| Filenames and repository names | _pending_ | _pending_ | _pending_ |
| Programming / developer terms | _pending_ | _pending_ | _pending_ |
| Short commands and long prompts | _pending_ | _pending_ | _pending_ |
| Silence / interrupted speech | _pending_ | _pending_ | _pending_ |

## 6. Decision gate

Parakeet Spanish passes only when all of these are true on the accepted corpus:

1. The generated report says `Comparable: yes`: both engines scored every
   accepted privacy-safe clip ID, with no different/incomplete scored sets or
   inconsistent hypotheses across measured repetitions.
2. Overall Parakeet corpus WER is within `0.01` absolute of faster-whisper.
3. Code-switch-subset Parakeet corpus WER is no worse by more than `0.05`
   absolute.
4. Human review finds no systematic Parakeet loss on filenames, repository
   names or developer terms.

| Gate | Result | Evidence |
| --- | --- | --- |
| Identical complete scored sets | pending | _pending_ |
| Overall WER delta ≤ `0.01` | pending | _pending_ |
| Code-switch WER delta ≤ `0.05` | pending | _pending_ |
| No systematic filename / developer-term loss | pending | _pending_ |
| **Final decision** | **pending** | Real-audio evaluation not yet recorded |

`faster-whisper` remains the global default on both pass and fail. A pass permits
the Spanish support label for Parakeet to move beyond experimental; a fail
keeps Parakeet Spanish experimental and records the measured reason.

## 7. Maintenance gate and sign-off

This initiative is **blocked at the real-audio validation gate**, not at an
implementation or automated-test gate. It may be closed into maintenance only
after all of the following are present in this report:

- corpus acceptance and privacy review with all minimum accent counts;
- complete environment, command and exact model-revision evidence;
- both engines successfully evaluated on the identical complete accepted
  corpus; any skipped clip or engine error makes the run not comparable and
  must be resolved before the decision;
- overall, per-accent and code-switch corpus WER, median per-clip latency and
  qualitative review;
- an explicit pass/fail decision against every gate above; and
- reviewer and maintainer sign-off.

After that sign-off, the initiative becomes maintenance-only regardless of
pass or fail. Reopen implementation work only for a demonstrated runtime
regression, dependency/model change, security issue or an explicitly approved
new product requirement—not merely to change the default engine.

**Benchmark reviewer:** _pending_<br>
**Maintainer:** _pending_<br>
**Signed date:** _pending_<br>
**Maintenance-gate status:** BLOCKED — pending real-audio evidence
