# Code Audit — June 2026 (Resolution Ledger)

**Audit date:** 2026-06-23<br>
**Resolution review:** 2026-08-10 against baseline `main` at `0cd89d695e1aa2bb5c20f1cc847ee9880685389d`, plus the independently re-audited August closure work in the same landing set<br>
**Method:** 10-agent read-only audit, followed by verification, remediation,
dead-code archival, integration review and regression testing.<br>
**Scope:** the 85 original findings A1–J9. This file is the evidence ledger;
the August D1 closure remains focused follow-up rather than a new broad
remediation campaign.

Severity: **C**ritical / **H**igh / **M**edium / **L**ow. Resolutions use only
`fixed`, `refuted`, `archived-dead`, `accepted-trade-off`, and `still-open`.

The principal remediation commits were
[`d513575`](https://github.com/pablopda/linux-speech-tools/commit/d51357584533bad6cf968fe2466b3a241e3d7176),
[`d5b630c`](https://github.com/pablopda/linux-speech-tools/commit/d5b630ce1b106e3aece11faae413c0000d57ffbf),
[`a06c3ab`](https://github.com/pablopda/linux-speech-tools/commit/a06c3ab4cdcf0cd52c032f1cd57ad609f1f698b6),
[`c9bfc64`](https://github.com/pablopda/linux-speech-tools/commit/c9bfc64903e54de8e25f831ad718e006fe237db3),
[`aad9deb`](https://github.com/pablopda/linux-speech-tools/commit/aad9debaaf7e8c511743e03dc468cf2253830f2c),
[`cacf401`](https://github.com/pablopda/linux-speech-tools/commit/cacf4019efda7a35f66b2c090b2c5763775652cf),
[`216fd2d`](https://github.com/pablopda/linux-speech-tools/commit/216fd2d4f0db573a54663fac721c7b082f28f938),
[`d45285c`](https://github.com/pablopda/linux-speech-tools/commit/d45285c0f44f14743531fb298a4beda28aad4328),
the repaired PR #4 integration
([`2744ac9`](https://github.com/pablopda/linux-speech-tools/commit/2744ac90ae6b14c4226046057409c662b982c5e9) +
[`bd19f5f`](https://github.com/pablopda/linux-speech-tools/commit/bd19f5f)),
and prompt-dictation lifecycle commits
[`a1e4e7c`](https://github.com/pablopda/linux-speech-tools/commit/a1e4e7c8d59b7b458f1a419e78def1ccc48d20af) and
[`0cd89d6`](https://github.com/pablopda/linux-speech-tools/commit/0cd89d695e1aa2bb5c20f1cc847ee9880685389d).

Short commit IDs repeated in the rows refer to those linked commits. Named tests
are quoted beside the behavior they protect and linked to their containing test
file where the link is not already present in the row.

The baseline validation recorded for `0cd89d6` was 318 passed, 2 skipped and 83
subtests, plus the legacy runner, shell syntax, Python compilation, installer
dry-run and diff checks. Those results are automated evidence only; microphone,
audio playback and live GNOME behavior remain manual gates in
[`STT_MANUAL_QA_CHECKLIST.md`](../developer/STT_MANUAL_QA_CHECKLIST.md).

## Closure summary

- D1 is fixed in the August landing set: validated DNS results are bound to the
  numeric connection, every redirect and render subrequest uses the same pinned
  transport, and Chromium is confined to an owned rejecting proxy so browser
  connection hints cannot reach a target directly.
- H1 and H4 are fixed by removing every supported mutable/obsolete streamed
  command and by the re-audited versioned-asset → pinned-bootstrap publication
  state machine. Actual tag/release publication remains a separate approval
  gate, not evidence fabricated in this ledger.
- F1/F2 were fixed for their original GNOME 45–48 claim. GNOME 49/50 support is
  a newer compatibility scope decision and must not be inferred from this
  ledger.
- B3, B7, D6, H5, H9 and J9 document deliberate trade-offs rather than hidden
  fixes.
- Archived experiments remain available under `experiments/`, outside the live
  packaged/imported paths.

## Closed critical follow-up

- **D1 — request-time SSRF:** The final repair uses an explicit, Python-version-
  stable IANA special-purpose address policy, rejects any mixed public/non-public
  DNS answer, and connects only to the validated numeric socket addresses under
  one bounded deadline. Playwright never continues browser requests: validated
  responses are supplied with `route.fulfill`, state-changing methods are
  rejected, network-hint response headers are removed, and all remaining
  Chromium-owned TCP is forced into an owned loopback rejecting proxy with
  localhost bypass disabled. Hermetic rebinding/redirect/subresource tests and
  an installed-Chrome IPv4/IPv6/localhost preconnect socket trap cover the
  closure without external network access.

H1/H4 publication code is closed, but no new release was created during this
implementation. [`release.sh`](../../scripts/release/release.sh) now creates an
exact source tag and draft versioned asset, verifies the uploaded bytes, commits
the asset digest, creates a provenance-checked bootstrap tag, exercises the raw
bootstrap's download/checksum/extraction path against those canonical bytes,
and only then publishes. Partial states resume through a narrowly validated
`--resume-bootstrap` path. The behavioral cases live in
[`test_release_workflow.py`](../../tests/unit/test_release_workflow.py).

## Area A — STT core

| ID | Sev | Original finding | Resolution | Evidence |
| --- | --- | --- | --- | --- |
| A1 | H | Signal handler performed status and queue work, risking deadlock. | fixed | [`d5b630c`](https://github.com/pablopda/linux-speech-tools/commit/d5b630ce1b106e3aece11faae413c0000d57ffbf) made it flag-only; [`test_signal_handler_requests_finalize`](../../tests/unit/test_session_core.py) and `test_signal_finalize_flushes_via_empty_queue` exercise the handoff. |
| A2 | H | Audio queue was unbounded during transcription. | fixed | `d5b630c` introduced `queue.Queue(maxsize=...)` plus drop-oldest handling in [`session.py`](../../src/stt/session.py). `test_stt_buffers_have_absolute_limits` checks the duration-limit configuration, but a dedicated overflow/drop-order behavioral test is still desirable. |
| A3 | M | Signal finalization dropped queued tail frames and bypassed the speech guard. | fixed | `d5b630c` added tail draining and guarded finalization; [`test_finalize_path_transcribes_buffered_speech`](../../tests/unit/test_session_core.py) and `test_signal_finalize_flushes_via_empty_queue` cover it. |
| A4 | M | A short capture read was treated as EOF and lost the final partial frame. | fixed | `d5b630c` changed [`session.py`](../../src/stt/session.py) to accumulate short reads until a complete VAD frame is available. The current session tests cover downstream capture/finalize behavior, not the short-read assembly branch directly. |
| A5 | M | Broad frame errors could loop while leaving `recording` stuck. | fixed | `d5b630c` resets utterance state in the processing-loop exception path. [`test_backend_exception_is_reported_as_finalization_failure`](../../tests/unit/test_session_core.py) protects failure reporting but does not directly exercise bad-frame recovery. |
| A6 | L | Timed thread join could leave the ffmpeg capture process alive. | fixed | `d5b630c` added capture-process ownership plus terminate/wait/kill reaping in [`session.py`](../../src/stt/session.py); `0cd89d6` also bounded finalization cleanup. Launcher process-tree coverage is separate from this session-level fix. |
| A7 | L | Invalid `STT_MAX_*` values crashed construction. | fixed | `d5b630c` added safe parsing; [`test_invalid_max_duration_env_values_use_safe_defaults`](../../tests/unit/test_session_core.py) and `test_invalid_session_timing_and_vad_values_fall_back_safely` cover invalid and non-finite values. |
| A8 | L | Status files were truncated in place. | fixed | `d5b630c` added private temp-file + `os.replace` writes in [`runtime.py`](../../src/stt/runtime.py); machine-readable status tests live in [`test_speech_tools.py`](../../tests/test_speech_tools.py). |

## Area B — STT modes

| ID | Sev | Original finding | Resolution | Evidence |
| --- | --- | --- | --- | --- |
| B1 | M | `faster_whisper_simple.py` was a diverged duplicate. | archived-dead | [`216fd2d`](https://github.com/pablopda/linux-speech-tools/commit/216fd2d4f0db573a54663fac721c7b082f28f938) moved it to [`experiments/stt/`](../../experiments/stt/) and removed live references. |
| B2 | M | `faster_whisper_vad.py` was another diverged session copy. | archived-dead | `216fd2d` moved it to [`experiments/stt/`](../../experiments/stt/); live clipboard and typing modes share [`session.py`](../../src/stt/session.py), guarded by `test_clipboard_and_typing_modes_use_shared_session_core`. |
| B3 | M | Wayland without ydotool could fall through to xdotool and silently miss native clients. | accepted-trade-off | `d5b630c` initially rejected the fallback; verified PR #4 commit `2744ac9` deliberately restored it only when `DISPLAY` exposes XWayland. Clipboard remains the default, native Wayland typing requires ydotool, and the Wayland/XWayland distinction is a manual QA gate. |
| B4 | M | Three clipboard detectors could select different tools. | fixed | `d5b630c` centralized selection in [`detect_clipboard_tool`](../../src/stt/runtime.py); [`test_output_backends.py`](../../tests/unit/test_output_backends.py) covers wl-copy and xclip success paths. |
| B5 | L | `--vad` accepted arbitrary strings. | fixed | `d5b630c` added integer choices 0–3 and safe env normalization; invalid-value coverage is in [`test_speech_tools.py`](../../tests/test_speech_tools.py). |
| B6 | L | Model choices diverged between dispatcher and modes. | fixed | `d5b630c` removed the conflicting hard-coded choice lists; engine/model argument behavior is covered by help and dispatch tests in `test_speech_tools.py`. |
| B7 | L | Diagnostic flags were loosely aliased and split output streams. | accepted-trade-off | The CLI now documents `--test` as a `--check` alias and makes `--warm-model` explicit; [`test_diagnostics_are_side_effect_light_without_warm_model`](../../tests/test_speech_tools.py) and `test_warm_model_timing_is_explicit_and_mockable` lock the intended compatibility behavior. |
| B8 | L | Dead simple mode could compute zero/invalid CPU workers. | archived-dead | `216fd2d` removed the module from live paths and stored it under `experiments/stt/`. |
| B9 | L | Dead simple mode hard-coded ALSA and leaked ffmpeg. | archived-dead | `216fd2d` archived the module; the live shared session uses enumerated capture candidates and owned process cleanup. |

## Area C — Edge TTS and parallel synthesis

| ID | Sev | Original finding | Resolution | Evidence |
| --- | --- | --- | --- | --- |
| C1 | H | Predictable `/tmp` audio names could collide or clobber. | fixed | `d5b630c` changed [`simple_parallel.py`](../../src/tts/simple_parallel.py) to `tempfile.mkstemp`; `test_core_python_files_compile` keeps the retained standalone module importable. |
| C2 | H | Failed `None` results were not counted, causing timeout stalls/desynchronization. | fixed | `d5b630c` made each worker publish one terminal result and count all completions in `simple_parallel.py`. |
| C3 | M | `error_count` was mutated without synchronization. | fixed | `d5b630c` introduced a worker-state lock around the counter. |
| C4 | M | Timed-out workers/subprocesses could be returned as complete or orphaned. | fixed | `d5b630c` tracks live subprocesses, stops them on timeout and checks worker termination. |
| C5 | L | A bare `except:` hid cleanup failures. | fixed | `d5b630c` narrowed cleanup handling to `OSError`; no bare exception remains in live `simple_parallel.py`. |
| C6 | L | `bin/say` contained a dead version branch. | fixed | `d5b630c` reads the packaged [`VERSION`](../../VERSION) file; launcher help/version behavior is exercised by [`test_speech_tools.py`](../../tests/test_speech_tools.py). |
| C7 | L | `-o` silently overwrote and did not validate the parent directory. | fixed | `d5b630c` validates the directory and warns before overwrite in [`bin/say`](../../bin/say) and [`bin/say-local`](../../bin/say-local). |

`simple_parallel.py` has no user-facing launcher and is not called by the live
reader; its former caller was archived by `216fd2d`. The fixes above keep direct
developer execution safe without treating it as a supported streaming path.

## Area D — Read-aloud and streaming

| ID | Sev | Original finding | Resolution | Evidence |
| --- | --- | --- | --- | --- |
| D1 | C | URL fetching allowed SSRF through schemes, private addresses and redirects. | fixed | `d5b630c` supplied the original scheme/private/redirect guard. The August closure in [`say_read.py`](../../src/tts/say_read.py) binds each validated DNS result to the numeric connection, applies one request deadline and bounded reads/redirects/addresses, preserves TLS hostname verification, pins the current IANA special-purpose policy across Python versions, proxies every render request through that transport, and confines Chromium's own egress to an owned rejecting proxy. [`test_say_read_network_security.py`](../../tests/unit/test_say_read_network_security.py) covers rebinding, redirects, response cleanup, document handoff, transition-address families, state-changing methods, private subresources, stripped connection hints and a real-browser preconnect trap. |
| D2 | C | `early_start_player.py` imported invalid module paths. | archived-dead | `216fd2d` moved the unreachable prototype to [`experiments/tts/early_start_player.py`](../../experiments/tts/early_start_player.py). |
| D3 | H | The early-start prototype could reorder/drop background audio. | archived-dead | `216fd2d` removed its only integration path from live source; no launcher imports it. |
| D4 | H | The mpv silence-trim argument contained literal shell quotes. | fixed | `d5b630c` constructs the filter as a direct argv value; [`bin/say-read`](../../bin/say-read) wrapper/default checks remain green. |
| D5 | H | ffplay could leak or hang on stream exception/Ctrl-C. | fixed | `d5b630c` owns, terminates, waits and escalates the ffplay subprocess in `say_read.py`. |
| D6 | M | `--stream` blocked per piece and treated transient player failure as fatal. | accepted-trade-off | `d5b630c` made transient playback failure non-fatal and preserved cleanup. The original `--stream` path still synthesizes and plays each piece sequentially, so inter-piece gaps remain accepted product behavior; `--stream-fast` is the separate low-latency path. |
| D7 | M | Parallel temp-audio names could collide. | fixed | Same remediation as C1: `d5b630c` uses exclusive `mkstemp` files. |
| D8 | M | HTTP PDF handling and latin-1 fallback corrupted valid content. | fixed | `d5b630c` routes remote PDF/EPUB content through their extractors and uses declared/response encoding for HTML. |
| D9 | M | `pdftoppm` had no timeout and empty OCR failed silently. | fixed | `d5b630c` bounds conversion and reports empty OCR in `say_read.py`. |
| D10 | M | Playback hard-coded 24 kHz regardless of synthesis output. | fixed | `d5b630c` validates and propagates the actual synthesis sample rate. |
| D11 | L | An unanchored cleanup regex deleted legitimate word fragments. | fixed | `d5b630c` anchored the UI-junk patterns; current extraction code no longer matches arbitrary substrings. |
| D12 | L | The early-start queue timeout could end playback mid-synthesis. | archived-dead | `216fd2d` archived `early_start_player.py` and removed the live dependency. |

## Area E — Chunking

| ID | Sev | Original finding | Resolution | Evidence |
| --- | --- | --- | --- | --- |
| E1 | M | Five abandoned chunking prototypes remained in live source. | archived-dead | `216fd2d` moved all five to [`experiments/chunking/`](../../experiments/chunking/); only `GoldStandardChunker` remains on the reader path. |
| E2 | M | Comma/conjunction splitting could return a part above `max_size`. | fixed | [`d513575`](https://github.com/pablopda/linux-speech-tools/commit/d51357584533bad6cf968fe2466b3a241e3d7176) made oversized parts recurse; [`test_no_chunk_exceeds_max_size`](../../tests/unit/test_chunking_quality.py) runs all English/Spanish gold cases. |
| E3 | M | Several prototypes dropped short input entirely. | archived-dead | `216fd2d` moved those implementations out of live source; `test_case_produces_non_empty_chunks` guards the live chunker. |
| E4 | L | Break priority comparison preferred weaker boundaries. | fixed | `d513575` changed the winner to the lowest, strongest priority index in [`gold_standard_chunker.py`](../../src/chunking/gold_standard_chunker.py). |
| E5 | L | `Dict[str, any]` used the builtin instead of `typing.Any`. | fixed | `d513575` corrected the annotation; `test_core_python_files_compile` covers import/compile health. |
| E6 | L | Archived chunkers protected the wrong abbreviation span. | archived-dead | `216fd2d` moved the affected enhanced/optimized implementations to `experiments/chunking/`. |

## Area F — GNOME integration

| ID | Sev | Original finding | Resolution | Evidence |
| --- | --- | --- | --- | --- |
| F1 | H | A legacy `imports.*` extension falsely claimed GNOME 45–48 support. | fixed | `a06c3ab` first narrowed metadata honestly; [`d45285c`](https://github.com/pablopda/linux-speech-tools/commit/d45285c0f44f14743531fb298a4beda28aad4328) then ported [`extension.js`](../../gnome-extension/extension.js) to ESM for 45–48. GNOME 49/50 is outside that original claim. |
| F2 | H | Notification construction used the wrong GNOME 46+ signature. | fixed | `d45285c` feature-detects the 45 versus 46+ MessageTray API. The live signature still requires manual version-matrix testing. |
| F3 | M | `GLib.spawn_sync` polled status on the compositor thread. | fixed | `a06c3ab` removed synchronous spawning; `d45285c` uses `Gio.File.load_contents_async` with cancellable cleanup. |
| F4 | M | A fake settings object made extension keybinding registration throw. | fixed | `a06c3ab` removed the broken in-extension keybinding; installation uses GNOME media keys and no nonexistent schema. |
| F5 | M | Reader service start/already-running logic was inverted. | fixed | [`a06c3ab`](https://github.com/pablopda/linux-speech-tools/commit/a06c3ab4cdcf0cd52c032f1cd57ad609f1f698b6) added a real D-Bus readiness check in [`say-read-gnome`](../../bin/say-read-gnome). |
| F6 | M | The wrapper published its own PID instead of the reader PID. | fixed | `a06c3ab` publishes the child reader PID so pause/stop target the reader; live signal delivery remains in the manual GNOME checklist. |
| F7 | M | Dictation and indicator status vocabularies disagreed. | fixed | `a06c3ab` normalized listening/idle status; `cacf401` repaired the simulated GNOME setup fixture, and machine-readable status is tested in `test_speech_tools.py`. |
| F8 | L | Reader temporary scripts leaked after abnormal termination. | fixed | `a06c3ab` added trap-based cleanup in `say-read-gnome`. |
| F9 | L | GNOME setup was not gated to a GNOME session. | fixed | `a06c3ab` added session/schema gates and removed unused variables; [`cacf401`](https://github.com/pablopda/linux-speech-tools/commit/cacf4019efda7a35f66b2c090b2c5763775652cf) makes the test fixture exercise that path. |
| F10 | L | Progress updates froze while a wait notification was active. | fixed | `a06c3ab` uses `notify-send --print-id/--replace-id`; [`test_gnome_integration_bridge.py`](../../tests/integration/test_gnome_integration_bridge.py) guards ID parsing and replacement wiring. |
| F11 | L | The GNOME media diagnostic failed rather than skipping without desktop tools. | fixed | `a06c3ab` added exit 77 capability skips in [`test-gnome-media-simple.py`](../../src/utils/test-gnome-media-simple.py). |

## Area G — Shell launchers

| ID | Sev | Original finding | Resolution | Evidence |
| --- | --- | --- | --- | --- |
| G1 | H | Faster toggle had a PID-file start/stop race and could orphan a recorder. | fixed | `d5b630c` serialized state with `flock` and exclusive PID claims; `0cd89d6` added validated recursive teardown. [`test_toggle_liveness.py`](../../tests/unit/test_toggle_liveness.py) covers PID reuse and descendant order. |
| G2 | H | Legacy `talk2claude` used a check-then-write PID race. | fixed | `d5b630c` uses an exclusive PID claim and cleanup trap in [`bin/talk2claude`](../../bin/talk2claude). |
| G3 | M | Fixed state paths and absent traps could retranscribe stale data. | fixed | `d5b630c` moved state under private runtime/state directories, trap-cleans ownership files and rejects stale WAV state; `test_private_state_paths_replace_shared_tmp` guards the layout. |
| G4 | M | An unguarded PID-file command substitution was fragile under `set -e`. | fixed | `d5b630c` replaced raw reads with validated helper paths; shell syntax and toggle tests cover the launcher. |
| G5 | M | Bare project-root `cd` failures had no diagnostic. | fixed | `d5b630c` added explicit project-root validation/errors to the faster launcher and setup helper. |
| G6 | M | Environment unquoting corrupted uncommon literal values. | fixed | `d5b630c` introduced literal parsing; `bd19f5f` hardened the allowlist and unsafe-file checks. `test_config_helper_loads_parakeet_values_literally` covers quotes, backslashes and command-substitution text. |
| G7 | L | Legacy `YDO` defaulted to a nonexistent absolute binary. | fixed | `d5b630c` resolves ydotool with `command -v` and falls back to clipboard with a diagnostic. |
| G8 | L | Raw, unquoted PID substitutions appeared in status/ps paths. | fixed | `d5b630c` centralized numeric PID reads and quoting; launcher shell-syntax tests remain green. |
| G9 | L | Missing `python3` produced an opaque shell abort. | fixed | `d5b630c` added command guards and actionable messages in both launchers. |

## Area H — Installer and packaging

| ID | Sev | Original finding | Resolution | Evidence |
| --- | --- | --- | --- | --- |
| H1 | H | README streamed an unverified bootstrap from mutable `main`. | fixed | [`README.md`](../../README.md) and [`INSTALLATION.md`](../INSTALLATION.md) explicitly reject the obsolete v1.0.2 bootstrap and advertise no mutable command. Future publication is gated by a raw versioned bootstrap tag, a SHA-pinned versioned release asset, a real download/checksum/extraction smoke, and verified-tag GitHub release creation. `test_readme_does_not_advertise_obsolete_v1_0_2_streamed_installer`, `test_bootstrap_check_downloads_verifies_and_extracts_without_installing`, and the release workflow gate tests cover the behavior. |
| H2 | H | Release QA checked a nonexistent script and silently skipped. | fixed | [`c9bfc64`](https://github.com/pablopda/linux-speech-tools/commit/c9bfc64903e54de8e25f831ad718e006fe237db3) invokes the real [`pre-release-check.sh`](../../scripts/release/pre-release-check.sh) and fails if it is missing. |
| H3 | M | Version synchronization grepped an installer variable that did not exist. | fixed | `c9bfc64` updates and validates `INSTALLER_REF`/`DEFAULT_INSTALLER_REF`, the actual installer constants. |
| H4 | M | Release did not regenerate the tag tarball checksum. | fixed | [`release.sh`](../../scripts/release/release.sh) no longer pins GitHub's generated tag archive. It builds and uploads a versioned source asset, re-downloads and verifies its SHA, commits that digest, and creates a distinct provenance-checked `bootstrap-vX.Y.Z` tag. Resume accepts only the exact source/pin states and preserves canonical asset bytes across toolchains. The 15 behavioral tests in [`test_release_workflow.py`](../../tests/unit/test_release_workflow.py) cover tag targets, initial/pin push recovery, unrelated-commit rejection, bootstrap provenance, canonical-byte resume, and non-installing bootstrap verification. |
| H5 | M | uinput setup grants persistent global input-injection capability. | accepted-trade-off | This is inherent to ydotool/uinput. `c9bfc64` documents the security boundary and provides [`--uninstall`](../../scripts/setup/setup-uinput-permissions.sh); direct typing remains explicit opt-in and clipboard is default. |
| H6 | M | The GNOME shortcut path was not registered in `custom-keybindings`. | fixed | `c9bfc64` registers the path and corrects the accelerator spelling in [`setup-keyboard-shortcuts.sh`](../../scripts/setup/setup-keyboard-shortcuts.sh). |
| H7 | M | The uv installer checksum pinned a rolling upstream URL. | fixed | Repaired PR #4 commit [`bd19f5f`](https://github.com/pablopda/linux-speech-tools/commit/bd19f5f) pins uv 0.11.32 and its SHA256; [`test_uv_installer_default_is_versioned_and_verified`](../../tests/test_speech_tools.py) rejects the rolling URL. |
| H8 | L | The `stt` extra appeared to omit a numpy floor. | refuted | [`pyproject.toml`](../../pyproject.toml) already has core `numpy>=1.20.0`, inherited by every extra; `c9bfc64` documents the effective floor and the legacy requirements relationship. |
| H9 | L | The `all` extra is a hand-maintained dependency union. | accepted-trade-off | `c9bfc64` documents the synchronization obligation and avoids a metadata/lock rewrite; `test_pyproject_declares_runtime_extras` and locked sync are the maintenance gate. |
| H10 | L | `requirements.txt` carried undeclared/drifting pyaudio. | fixed | `c9bfc64` removed the dead dependency and labels the file as a legacy pip path. |
| H11 | L | Release staged broadly and could publish from a non-main branch. | fixed | `c9bfc64` hard-fails off `main` unless explicitly forced and stages named changed files only in [`release.sh`](../../scripts/release/release.sh). |

## Area I — Utilities and documentation

| ID | Sev | Original finding | Resolution | Evidence |
| --- | --- | --- | --- | --- |
| I1 | L | README called faster-whisper “OpenAI Whisper.” | fixed | `d513575` names faster-whisper accurately in [`README.md`](../../README.md). |
| I2 | L | `WHISPER_VAD` was implemented but undocumented. | fixed | `d513575` documents it in [`FASTER_QUICKSTART.md`](../FASTER_QUICKSTART.md); parser validation is covered by current STT tests. |
| I3 | L | `simple-audio-test.py` had an unused `time` import. | fixed | `d513575` removed the import; core Python compilation is part of `test_speech_tools.py`. |

`setup_models.py` was verified clean in the original audit: HTTPS, real SHA256
values, atomic writes and no path traversal. The historical note that
`STT_ENGINE` was unread is obsolete: the pluggable-engine work routes it through
[`asr_engine.py`](../../src/stt/asr_engine.py), with unit coverage.

## Area J — Tests

| ID | Sev | Original finding | Resolution | Evidence |
| --- | --- | --- | --- | --- |
| J1 | H | A report-shaped module broke pytest collection with a bad import. | fixed | [`aad9deb`](https://github.com/pablopda/linux-speech-tools/commit/aad9debaaf7e8c511743e03dc468cf2253830f2c) made it an explicit manual report and excludes it in [`tests/conftest.py`](../../tests/conftest.py); the suite collects cleanly. |
| J2 | H | English/Spanish data modules were collected but asserted nothing. | fixed | `aad9deb` drives every case through parametrized invariants in [`test_chunking_quality.py`](../../tests/unit/test_chunking_quality.py). |
| J3 | H | The word-cutoff assertion compared a chunk with itself and passed vacuously. | fixed | `aad9deb` compares each chunk to its next neighbor; `test_helper_off_by_one_detection_is_sound` proves a real split is detected. |
| J4 | M | Per-test `sys.path` mutation made collection order fragile. | fixed | `aad9deb` centralizes repository paths in `tests/conftest.py`; the integration script retains only a standalone-execution fallback. |
| J5 | M | Declared markers were unused and the GNOME shell test was orphaned. | fixed | `aad9deb` added [`test_gnome_integration_bridge.py`](../../tests/integration/test_gnome_integration_bridge.py) with `gnome`/`manual` markers and an explicit `RUN_GNOME_INTEGRATION=1` live gate. |
| J6 | M | The live chunker had no behavioral unit tests. | fixed | `aad9deb` added non-empty, max-size, normalized round-trip and boundary invariants across 40 English/Spanish cases. |
| J7 | M | Session behavior was asserted only by source-string inspection. | fixed | `aad9deb` added [`test_session_core.py`](../../tests/unit/test_session_core.py); later commits extended finalization, timeout, partial and engine-routing behavior. |
| J8 | L | Clipboard/typing tests covered only degraded fallback behavior. | fixed | `aad9deb` added successful wl-copy, xclip, ydotool and xdotool command paths in [`test_output_backends.py`](../../tests/unit/test_output_backends.py). |
| J9 | L | The test and manual report used different content-preservation definitions. | accepted-trade-off | The authoritative test now requires exact content after whitespace normalization (`test_content_round_trips`); the excluded manual report retains an approximate character-rate diagnostic and is explicitly non-gating. |

## Historical process note

The original “Phase 2” proposed eight parallel fix agents. That broad
remediation work is preserved by the commit/evidence links above. Finish the
three explicitly open rows through focused changes; new defects should be
reproduced against current code and handled through normal issue/change review
rather than restarting the historical campaign.
