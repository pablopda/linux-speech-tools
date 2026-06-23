# Code Audit — June 2026

**Method:** 10-agent parallel audit, one team per subsystem (read-only).
**Scope:** entire repo. **Status:** findings recorded; Phase 2 = verify + fix.

Severity: **C**ritical / **H**igh / **M**edium / **L**ow.
Reach: **live** (runs in normal use) / **dead** (unreachable/experimental).

> Big-picture theme: a large fraction of the tree is **dead/experimental code**
> that has diverged from the live paths. Several "bugs" are only in dead modules
> — for those the right fix is *deletion*, surfaced for the maintainer rather
> than auto-applied.

---

## Area A — STT core (`src/stt/session.py`, `runtime.py`)

| ID | Sev | Location | Issue | Reach |
|----|----|----------|-------|-------|
| A1 | H | session.py:128-133 | Signal handler does non-async-safe work (`set_status`, `queue.put`) → Ctrl-C deadlock risk | live |
| A2 | H | session.py:111 | `audio_queue` unbounded; grows during multi-second transcription → latency/memory creep | live |
| A3 | M | session.py:256-258,290-293 | Finalize-on-signal drops queued tail frames; bypasses speech guard | live |
| A4 | M | session.py:214-217 | Short read treated as EOF drops final partial frame (fragile vs VAD frame-size) | live |
| A5 | M | session.py:299-301 | Broad `except` continues → error-spam loop + stuck `recording` state on one bad frame | live |
| A6 | L | session.py:308-310 | `join(timeout=1)` can orphan ffmpeg; status race | live |
| A7 | L | session.py:104,108 | Non-numeric `STT_MAX_*` env crashes `__init__` with raw traceback | live |
| A8 | L | runtime.py:43-46,58-65 | Status writes truncate-in-place (non-atomic) | live |

## Area B — STT modes (`faster_whisper_*.py`)

| ID | Sev | Location | Issue | Reach |
|----|----|----------|-------|-------|
| B1 | M | faster_whisper_simple.py | Dead, diverged duplicate of the session core | dead |
| B2 | M | faster_whisper_vad.py | Diverged copy of session logic; missing status writes + better diagnostics | dead |
| B3 | M | faster_whisper_typing.py:38-53 | On Wayland without ydotool, falls through to `xdotool` which no-ops | live |
| B4 | M | auto.py:31 vs clipboard.py:42 vs clipboard.py:233 | 3 inconsistent clipboard-tool detectors; can disagree; Wayland→xclip silently | live |
| B5 | L | auto.py:137 | `--vad` accepts arbitrary strings; validated only downstream | live |
| B6 | L | auto.py:134 vs clipboard/typing | `--model` choices diverge; valid models rejected | live |
| B7 | L | auto.py:147-174 | `--diagnose`/`--warm-model`/`--test` loosely aliased; stdout/stderr split | live |
| B8 | L | simple.py:77 | `os.cpu_count()//2` → 0 or TypeError | dead |
| B9 | L | simple.py:113-143 | Hardcodes ALSA; leaks ffmpeg on error | dead |

## Area C — Edge TTS / parallel synth (`simple_parallel.py`, `bin/say*`)

| ID | Sev | Location | Issue | Reach |
|----|----|----------|-------|-------|
| C1 | H | simple_parallel.py:190-193 | Predictable temp filenames (`pid+sec+idx`) collide/overwrite under concurrency; clobber hazard in `/tmp` | live* |
| C2 | H | simple_parallel.py:110-120 | Result loop miscounts failures (`None` not counted) → stall for full timeout / desync | live* |
| C3 | M | simple_parallel.py:162 | `error_count += 1` data race across worker threads | live* |
| C4 | M | simple_parallel.py:122-127 | Workers `join(timeout)` unchecked; partial results returned as complete; orphan subprocs | live* |
| C5 | L | simple_parallel.py:333 | Bare `except:` swallows everything | live* |
| C6 | L | bin/say:20-22 | Dead VERSION branch (hardcodes same literal) | live |
| C7 | L | bin/say:100, say-local:69 | `-o` overwrites silently; no dir validation | live |

\* `simple_parallel` is reached only via `early_start_player` (D2, dead). Verify true reachability in Phase 2.

## Area D — Read-aloud / streaming (`say_read.py`, `early_start_player.py`)

| ID | Sev | Location | Issue | Reach |
|----|----|----------|-------|-------|
| **D1** | **C** | say_read.py:199-251,304 | **SSRF** — `fetch_url` follows redirects, no IP/scheme validation; internal/metadata endpoints fetched + read aloud | **live** |
| D2 | C | early_start_player.py:18-22 | Imports wrong module paths → `ModuleNotFoundError` on import | dead |
| D3 | H | early_start_player.py:90-204 | Premature end-of-stream drops background audio; cross-batch reordering | dead |
| D4 | H | say_read.py:411 | mpv silence-trim filter has embedded quotes → `--player mpv` crashes | live |
| D5 | H | say_read.py:428-462 | ffplay `Popen` leaked/hung on exception or Ctrl-C during stream | live |
| D6 | M | say_read.py:534-544 | `--stream` plays each piece blocking → gaps; `check=True` fatal on transient | live |
| D7 | M | (= C1) | Temp-audio filename collision | live* |
| D8 | M | say_read.py:202-205 | Content-type allowlist rejects PDF-over-HTTP; latin-1 fallback mangles UTF-8 | live |
| D9 | M | say_read.py:262-283 | `pdftoppm` no timeout → hang on malformed PDF; silent empty OCR | live |
| D10 | M | say_read.py:443 | Hardcoded 24000 Hz ignores actual synth sample rate | live |
| D11 | L | say_read.py:86 | UI-junk regex unanchored → deletes "Share(s)", "commentary"→"ary" | live |
| D12 | L | early_start_player.py:111-145 | 10s queue timeout can end playback mid-synth | dead |

## Area E — Chunking (`src/chunking/`)

| ID | Sev | Location | Issue | Reach |
|----|----|----------|-------|-------|
| E1 | M | 5 of 6 files | `smart_/enhanced_/tts_optimized_/chunk_quality_analyzer/chunking_test_framework` are abandoned prototypes (~1700 LOC); `chunking_test_framework` can't even import | dead |
| E2 | M | gold_standard_chunker.py:236-251 | `chunk_long_sentence` comma+conjunction path can return chunks > `max_size` (masked by later limiter) | live |
| E3 | M | smart/enhanced/tts_optimized | Short (<~100 char) inputs silently dropped → `[]` (total loss) | dead |
| E4 | L | gold_standard_chunker.py:216 | `find_optimal_break_point` `>=` inverts priority → prefers weakest break | live |
| E5 | L | gold_standard_chunker.py:373 | `Dict[str, any]` uses builtin `any` not `typing.Any` | live |
| E6 | L | enhanced/tts_optimized | Abbreviation protection uses `str.replace(...,1)` (wrong span) | dead |

## Area F — GNOME (`extension.js`, `gnome-reader-control.py`, wrappers)

| ID | Sev | Location | Issue | Reach |
|----|----|----------|-------|-------|
| F1 | H | extension.js + metadata.json:5 | Pre-GNOME-45 `imports.*`/global `Extension` API but `metadata.json` claims 45–48 → won't load on modern GNOME | live |
| F2 | H | extension.js:138-145 | Notification `Source`/`Notification` positional-args signature broken on GNOME 46+ | live |
| F3 | M | extension.js:72-80 | `GLib.spawn_sync` on compositor thread every 2s → desktop stutter | live |
| F4 | M | extension.js:192-198 | Fake `_getSettings()` (plain object) → `addKeybinding` throws; no GSettings schema ships | live |
| F5 | M | say-read-gnome:57-64 | Service start/already-running logic inverted; stray foreground python | live |
| F6 | M | say-read-gnome:201-211 | Captures wrapper PID not reader → pause/stop signal wrong process | live |
| F7 | M | gnome-dictation:74-80,156 | Status vocab mismatch (`recording` vs toggle's `listening`/`idle`) → indicator desync | live |
| F8 | L | say-read-gnome:175-194 | Temp script leak on abnormal termination | live |
| F9 | L | gnome-dictation:8-11 | Unused vars; setup not gated on GNOME session | live |
| F10 | L | gnome-reader-control.py:347-348 | Progress refresh suppressed during `--wait` notification → appears frozen | live |
| F11 | L | test-gnome-media-simple.py | No skip path when desktop tools absent | live |

## Area G — Shell launchers (`bin/talk2claude*`, `*-env`, `*-setup`)

| ID | Sev | Location | Issue | Reach |
|----|----|----------|-------|-------|
| G1 | H | talk2claude-faster-toggle:278-293 | PID-file start/stop race + unquoted `$(cat PID)` → orphan recorders hold mic | live |
| G2 | H | talk2claude:181-196 | `start_rec` check-then-write race orphans ffmpeg | live |
| G3 | M | talk2claude / toggle | Fixed-path PID/WAV/LOG, no `trap` cleanup → stale state re-transcribed | live |
| G4 | M | toggle:293 | Unguarded `$(cat PID_FILE)` fragile under `set -e` | live |
| G5 | M | talk2claude-faster:73, setup:21 | Bare `cd "$ROOT"` aborts silently, no diagnostic | live |
| G6 | M | linux-speech-tools-env:63-68 | Value un-quoting mismatches installer's plain writes → rare path corruption | live |
| G7 | L | talk2claude:24 | `YDO` defaults to nonexistent `/usr/bin/ydotool` → silent no-op | live |
| G8 | L | talk2claude:314, toggle:293 | Unquoted `$(cat PID)` in echo/ps | live |
| G9 | L | toggle:90-139, talk2claude:238 | Bare `python3` no guard → ugly abort if absent | live |

## Area H — Installer / packaging (`installer.sh`, `pyproject.toml`, `scripts/`)

| ID | Sev | Location | Issue | Reach |
|----|----|----------|-------|-------|
| H1 | H | installer.sh:45-48 + README:181 | Documented `curl\|bash` streams entry script from mutable `main` **unverified** (hash pin covers only the tarball, not the bootstrap) | live |
| H2 | H | release.sh:444 | Guards on nonexistent `scripts/quick-release-check.sh` → QA gate silently skipped every release | live |
| H3 | M | release.sh:198,202; pre-release-check.sh:132-139 | `installer.sh` version-sync greps a `VERSION=` that doesn't exist → vacuous no-op | live |
| H4 | M | installer.sh:48,58 | Tarball SHA256 hardcoded, not regenerated by release → next release's streamed install breaks | live |
| H5 | M | setup-uinput-permissions.sh:96-104 | Grants `uinput` group persistent global keystroke injection (inherent; needs documented uninstall) | live |
| H6 | M | setup-keyboard-shortcuts.sh:31 | Sets binding without registering path in `custom-keybindings` list → silently does nothing | live |
| H7 | M | install-with-uv.sh:216-217 | Pins Astral's rolling `install.sh` hash → breaks whenever upstream edits it | live |
| H8 | L | pyproject.toml:84-88 | `stt` extra omits numpy floor; `requirements-faster` drift | live |
| H9 | L | pyproject.toml:101-119 | `all` hand-duplicated union; drift risk; `audio` reachable only via `all` | live |
| H10 | L | requirements.txt | `pyaudio` not in pyproject (drift) | live |
| H11 | L | release.sh:319,343 | Unconditional `git add`; pushes current branch → can tag non-main | live |

## Area I — Utils / docs (`README*`, `simple-audio-test.py`)

| ID | Sev | Location | Issue | Reach |
|----|----|----------|-------|-------|
| I1 | L | README.md:30,362 | "Powered by OpenAI Whisper" mislabels actual engine (faster-whisper) | docs |
| I2 | L | README / FASTER | `WHISPER_VAD` env var read but undocumented | docs |
| I3 | L | simple-audio-test.py:12 | Unused `import time` | live |

> `setup_models.py` was audited clean (real SHA256, HTTPS, atomic writes, no path traversal). Env-var names cross-check consistent; `STT_ENGINE` is not yet read anywhere.

## Area J — Tests (`tests/`)

| ID | Sev | Location | Issue | Reach |
|----|----|----------|-------|-------|
| J1 | H | real_world_validation_report.py:7 | Report-as-test; bad import → **ModuleNotFoundError breaks pytest collection** in CI | live |
| J2 | H | test_suite_english/spanish.py | Pure data files collected as tests; assert nothing | live |
| J3 | H | real_world_chunking_test.py:65 | Word-cutoff assertion off-by-one (`enumerate(...,1)`) → passes vacuously | live |
| J4 | M | real_world_chunking_test.py:12-14 | `sys.path` mutation; fragile collection order | live |
| J5 | M | tests/ | Declared markers (audio/gnome/...) never applied; orphaned `test-gnome-integration.sh` | live |
| J6 | M | src/chunking | Zero behavioral unit tests for the live chunker | n/a |
| J7 | M | src/stt/session.py | "Tested" only via source-string grep, never executed | n/a |
| J8 | L | clipboard/typing | Only degraded-mode (fallback) tested | n/a |
| J9 | L | chunking_test vs report | Divergent content-preservation definitions | live |

---

## Phase 2 plan (verify + fix)

Eight file-disjoint fix agents (STT, TTS, Chunking, GNOME, Launchers,
Installer, Tests, Docs). Each **verifies** every assigned ID (CONFIRMED /
FALSE-POSITIVE / DEAD-CODE), **fixes** confirmed issues in reachable code with
minimal surgical changes, and **does not delete** whole modules (dead-code
removal is surfaced to the maintainer as a separate decision).
