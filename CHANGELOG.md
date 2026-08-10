# Changelog

All notable changes to Linux Speech Tools are documented here. The project follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) categories and semantic
versioning.

## [Unreleased]

No changes yet.

## [v1.1.0] - 2026-08-10

### Added

- Faster-whisper dictation with VAD, toggle-mode launchers, machine-readable
  status, GNOME hotkeys, and bounded finalization.
- Pluggable ASR backends with faster-whisper as the default and NVIDIA Parakeet
  as an opt-in engine selected by `STT_ENGINE` or `--engine`.
- A side-by-side WER and latency benchmark harness for the pending real-audio
  LATAM Spanish validation gate, with bounded privacy-safe reports and strict
  complete-corpus comparisons.
- Private, resumable LATAM corpus acquisition (`lst-asr-corpus`) with exact
  AR/MX/CO/CL composition, bounded capture, same-descriptor privacy validation,
  human attestations, and no committed audio or transcript data.
- Live developer prompt dictation through `lst-dictate` and `dictate-prompt`,
  including partial updates, target profiles, overlay/clipboard/paste outputs,
  explicit safe submission, and repository-aware recognition hints.
- A versioned GNOME focus provider and target contract that binds insertion to
  Shell session, stable window sequence, focus generation, and lock state.
- A non-typing GNOME live-acceptance harness (`lst-gnome-acceptance`) that
  records Shell-owned focus lifecycle, A→B→A generation/race evidence,
  manual disposable-buffer outcomes, and exact restoration without storing
  titles, PIDs, transcripts, or raw focus identities.
- A bounded read-only repository assistant (`lst-agent`) using ephemeral Codex
  runs, stdout/clipboard output, optional speech, and a narrow direct-placement
  allowlist for authoritative GNOME Text Editor targets.
- Private local insertion-reliability aggregates (`lst-insertion-metrics`) with
  opt-out/reset/report commands and a non-fabricated two-week/100-attempt gate.
- Connection-only IBus Stage 0 diagnostics (`lst-ibus-check`) for system Python,
  GI, typelib, session-bus, and IBus-bus availability.
- uv-based dependency profiles, setup helpers, dry-run installation, and model
  prefetch/check commands.

### Changed

- Reorganized launchers under `bin/`, implementation under `src/`, and setup,
  installation, release, and debug tooling under purpose-specific `scripts/`
  directories.
- Archived unreachable chunking, STT, TTS, and one-off analysis prototypes under
  `experiments/`, outside the installed package and normal test collection.
- Replaced source-string and vacuous tests with behavioral session, backend,
  installer, lifecycle, timeout, and GNOME-environment coverage.
- Made configuration loading literal and allowlisted rather than evaluating or
  unescaping values from `install.env`.
- Reworked releases into a verified source-tag, immutable asset, pinned SHA256,
  and separately smoke-tested `bootstrap-vX.Y.Z` flow. No mutable-branch
  streamed installer is advertised.
- Kept faster-whisper as the default ASR engine; Parakeet remains explicitly
  optional and requires Python 3.10 or newer.

### Fixed

- Bounded partial and final transcription, renderer commands, and launcher
  teardown so hung inference or desktop tools cannot indefinitely block normal
  finalization.
- Serialized dictation toggles and validated complete same-user process trees by
  PID start token before signalling them, preventing duplicate recorders, orphan
  processes, and PID-reuse kills.
- Prevented duplicate prompt finalization, destructive retries after partial
  replacement failures, Enter after failed/non-inserting output, and clipboard
  restoration after clipboard ownership changes.
- Bound live prompt replacement to the originally detected stable window when a
  provider is available; unverifiable or changed focus fails closed to the
  clipboard.
- Made classic typing, clipboard, notification, agent, and IBus diagnostic
  subprocesses bounded and cleaned up their owned process groups on timeout.
- Hardened benchmark manifest validation, error redaction, numeric projection,
  environment reporting, and empty-corpus rejection without fabricating a
  completed regional benchmark.
- Added a locked `stt-parakeet-gpu` dependency path and operational provider
  verification so advertised CUDA support cannot be mistaken for core-session
  GPU execution or hide a CPU/mixed fallback.
- Hardened installer and release checks, including an immutable pinned uv
  installer, literal configuration values, release QA gates, extension/package
  payload checks, and import-safe validation without optional dependencies.
- Kept release version changes synchronized with the locked local-project
  metadata, and made beta native packages use a persisted user-writable uv
  environment while their `/usr/share` source tree remains read-only. Exact
  tested DEB/RPM bytes are published together in one checksummed native-package
  bundle, avoiding partially published sibling assets.
- Resolved or explicitly dispositioned the June 2026 code-audit findings across
  STT/TTS lifecycle, shell races, GNOME responsiveness, installer consistency,
  and test collection.

### Security

- Replaced read-aloud URL fetching with DNS-pinned HTTP(S), explicit
  version-stable public-address policy, redirect/deadline/body limits, TLS
  hostname verification, and a mandatory rejecting Chromium proxy that closes
  browser preconnect and side-channel egress paths.
- Made insertion outcomes explicit and fail-closed across focus drift,
  ambiguity, fallback, clipboard restoration, and exactly-once submit policy;
  transcripts and target identities stay out of status and aggregate metrics.
- Restricted untrusted repository-agent output to non-inserting delivery except
  for a freshly revalidated, authoritative GNOME Text Editor identity.
- Hardened local metrics and request files against unsafe file types, aliases,
  inconsistent schemas, unbounded reads, and concurrent writes.
- Documented and tested the `/dev/uinput` authority required by ydotool, while
  preserving non-inserting clipboard/overlay fallbacks.

### Known limitations and manual validation

- Automated tests do not prove microphone, clipboard-manager, compositor, or
  application-specific behavior. The documented real-desktop dictation matrix
  for terminals, GTK editors, VS Code/Electron, Chromium, Firefox, focus drift,
  lock/unlock, Unicode, and Spanish punctuation remains manual.
- The extension declares GNOME 45–48 and 50. GNOME 50.1 has isolated runtime
  smoke evidence; live desktop validation and the older-version matrix remain
  manual, and unsupported or absent providers fall back without direct input.
- No real-audio LATAM WER/latency result or GPU claim has been recorded.
  Parakeet remains experimental and faster-whisper remains the default.
- IBus Stage 0 is connection diagnostics only: it registers no component or
  engine, commits no text, starts no daemon, and selects no input source.
- The IBus investment gate has not accumulated two weeks and 100 eligible local
  insertion attempts; the metrics report must not be treated as that evidence.
- Direct Wayland typing still requires an explicitly configured ydotool/uinput
  environment; unavailable or unverifiable targets use clipboard or overlay.
- Native DEB/RPM packages remain beta and require the documented normal-user
  installer step, which creates a user-owned uv environment outside the
  read-only `/usr/share` package tree.
- A supported streamed install requires a separately approved, published, and
  smoke-tested `bootstrap-v1.1.0` tag with the final release-asset SHA256. Until
  then, use a source checkout and do not advertise a `curl | bash` command.
- Tagging, publishing, and any external release announcement require explicit
  approval after reviewing this candidate.

## [v1.0.2] - 2025-11-12

### Fixed

- Corrected RPM package architecture and build paths.

## [v1.0.1] - 2025-11-12

### Fixed

- Corrected release automation permissions and API usage.
- Completed CI/CD workflow stabilization.
- Updated repository links in user and packaging documentation.

## [v1.0.0] - 2025-11-12

### Added

- Edge TTS with LATAM regional voices and local Festival/Kokoro alternatives.
- URL, PDF, EPUB, file, and standard-input read-aloud commands.
- Whisper-based background speech input and clipboard/paste output.
- Core `say`, `say-local`, `say-read`, `say-read-es`, and `talk2claude`
  launchers.
- Cross-distribution installation, packaging, and CI/release automation.

[Unreleased]: https://github.com/pablopda/linux-speech-tools/compare/v1.1.0...HEAD
[v1.1.0]: https://github.com/pablopda/linux-speech-tools/compare/v1.0.2...v1.1.0
[v1.0.2]: https://github.com/pablopda/linux-speech-tools/releases/tag/v1.0.2
[v1.0.1]: https://github.com/pablopda/linux-speech-tools/releases/tag/v1.0.1
[v1.0.0]: https://github.com/pablopda/linux-speech-tools/releases/tag/v1.0.0
