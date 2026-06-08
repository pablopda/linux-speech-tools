# Previous Investigation Implementation Tasks

Date: 2026-06-05

Source: `docs/planning/PRD_EVALUATION.md`

This task list converts the previous investigation into implementable work for
the current faster-whisper dictation path. The investigation's main conclusion
was to avoid broad product expansion and make the existing dictation flow more
reliable, recoverable, and safe.

## Current Baseline

The branch already implements much of the reduced MLP from the previous
investigation:

- `bin/talk2claude-faster` launches the faster-whisper dispatcher.
- `src/stt/faster_whisper_auto.py` chooses clipboard mode by default and direct
  typing only when explicitly requested.
- `src/stt/faster_whisper_clipboard.py` handles VAD, transcription, and
  clipboard output.
- `src/stt/faster_whisper_typing.py` handles direct typing with clipboard
  fallback.
- `bin/talk2claude-faster-toggle` provides a manual start/stop hotkey helper.
- `bin/gnome-dictation` wraps the faster toggle for GNOME usage.

The remaining work should focus on product hardening, not new headline
features.

## Implementation Priorities

### P0: Preview Mode Before Output

Goal: let users review recognized text before it is copied or typed.

Tasks:

- Add `--preview` to `talk2claude-faster`.
- Add `DICTATION_PREVIEW=1` as an environment equivalent.
- Pass preview mode through `src/stt/faster_whisper_auto.py`.
- Implement preview handling for clipboard mode.
- Implement preview handling for typing mode.
- In terminal sessions, show the recognized text and prompt for:
  - accept
  - edit
  - retry/skip
  - cancel
- In non-interactive sessions, fail clearly or fall back to copy-only behavior
  with an explicit message.
- Make preview mode never type into the active window until the user accepts.

Acceptance criteria:

- `talk2claude-faster --preview --clipboard` previews before copying.
- `talk2claude-faster --preview --typing` previews before typing.
- Rejected text is not copied, typed, or written to fallback files.
- Preview behavior has focused tests that do not require a microphone.

### P0: Privacy Cleanup

Goal: avoid persisting dictated content unless the user explicitly chooses that.

Tasks:

- Remove transcript text snippets from stderr/log output.
- Replace messages like `Copied: <text>...` with length/status-only messages.
- Add `STT_TRANSCRIPT_FALLBACK=1` or a similarly explicit opt-in for writing
  full transcripts to `dictation.txt`.
- If fallback remains enabled by default, change it to overwrite instead of
  append and document the retention behavior.
- Add a purge command or helper for STT state files:
  - `talk2claude-faster --purge-state`, or
  - `talk2claude-faster-toggle purge-state`, or
  - `linux-speech-tools-setup purge-stt-state`
- Ensure state files remain private (`0700` directory, `0600` files).

Acceptance criteria:

- Hotkey logs do not contain dictated content.
- Clipboard notifications do not include dictated content.
- File fallback is explicit or overwrite-only.
- Purging removes STT logs, PID files, and fallback transcript files without
  touching unrelated config.

### P0: Machine-Readable Status Contract

Goal: provide one reliable status API for scripts, tests, and future GNOME UI.

Tasks:

- Define states:
  - `idle`
  - `listening`
  - `recording`
  - `processing`
  - `finalizing`
  - `error`
- Write the current state to a private state file.
- Add `talk2claude-faster-toggle status --plain`.
- Add `talk2claude-faster-toggle status --json`.
- Add matching `gnome-dictation status --plain` and `--json`.
- Include useful fields in JSON:
  - state
  - pid
  - mode
  - clipboard_tool
  - fallback_file
  - log_file
  - updated_at
  - error, when present
- Keep the human notification behavior for normal `status`.

Acceptance criteria:

- Shell extension or GNOME wrappers can read status without scraping
  notifications.
- Stale PID files are detected and cleaned up.
- Status works when `notify-send` is unavailable.
- Tests cover idle, stale PID, running PID, and error-state cases.

### P1: Shared STT Session Core

Goal: reduce duplication between clipboard and typing modes.

Tasks:

- Extract shared audio capture, VAD buffering, model loading, signal handling,
  and transcription into a shared module, likely `src/stt/session.py`.
- Keep output behavior in small adapters:
  - clipboard output adapter
  - typing output adapter
  - preview wrapper adapter
- Preserve all existing CLI flags and environment variables.
- Keep Python 3.8+ compatibility.
- Avoid changing audio behavior unless covered by tests.

Acceptance criteria:

- `faster_whisper_clipboard.py` and `faster_whisper_typing.py` no longer carry
  duplicated capture/VAD/transcription loops.
- Existing STT tests pass.
- New tests cover the shared core with mocked audio/model/output adapters.
- The manual hotkey path still finalizes buffered speech on `SIGINT`.

### P1: Latency And Capability Diagnostics

Goal: make performance expectations measurable and realistic.

Tasks:

- Extend `talk2claude-faster --check` with:
  - model name
  - device
  - compute type
  - audio backend candidates
  - clipboard tool
  - typing capability
  - estimated model-load timing when `--check --warm-model` is used
- Add a separate `--diagnose` mode if `--check` should stay fast.
- Print guidance when a selected model/device combination is likely slow.
- Do not download large models implicitly in a normal capability check.

Acceptance criteria:

- `talk2claude-faster --check` remains fast and side-effect-light.
- `talk2claude-faster --diagnose` or `--check --warm-model` can measure model
  load time explicitly.
- Output is understandable for a user and parseable enough for tests.

### P1: Error Recovery And Fallbacks

Goal: make failures actionable instead of surprising.

Tasks:

- Improve messages for microphone capture failure.
- Detect clipboard tool failure separately from missing clipboard tools.
- In typing mode, fall back to clipboard only after reporting typing failure.
- Add a clear retry path for preview mode.
- Preserve classic/legacy fallback behavior where it is intentionally still
  supported.

Acceptance criteria:

- Common failures produce direct next-step guidance.
- Tests cover missing clipboard tools, failed copy command, and typing fallback.
- No fallback silently stores dictated text without an explicit message.

### P2: Documentation And Manual QA

Goal: document the hardened behavior after implementation.

Tasks:

- Update `docs/FASTER_QUICKSTART.md`.
- Update `docs/FASTER_WHISPER_SOLUTION.md`.
- Update GNOME docs only where behavior overlaps with status, privacy, or
  hotkey usage.
- Add a manual QA checklist for:
  - clipboard mode
  - preview mode
  - typing mode on X11
  - typing mode on Wayland
  - GNOME hotkey start/stop/finalize
  - missing clipboard tool fallback

Acceptance criteria:

- Docs no longer recommend unsafe `sudo ydotoold &` as the default path.
- Preview and privacy behavior are documented.
- Manual QA clearly distinguishes what can be verified in CI from what requires
  a real desktop session.

## Explicitly Out Of Scope For This Batch

Do not implement these from the old PRD yet:

- Wake words.
- Overlay windows.
- System tray app.
- Shell extension expansion.
- Full analytics or A/B testing framework.
- New always-listening daemon.
- New top-level commands for one-off options.

These ideas should be revisited only after the current dictation flow is stable
and manually validated by real users.

## Recommended First Goal

Implement P0 only:

1. Preview mode before output.
2. Privacy cleanup.
3. Machine-readable status contract.
4. Focused tests and docs for those changes.

This is the smallest high-value batch from the previous investigation. It gives
users control over recognized text, prevents accidental retention of dictated
content, and creates a stable status API for GNOME work without widening scope.

## Suggested Codex Goal Prompt

Use this prompt when assigning the implementation goal:

```text
Implement the P0 tasks from docs/planning/PREVIOUS_INVESTIGATION_IMPLEMENTATION_TASKS.md.

Scope:
- Add preview mode before clipboard or typing output.
- Remove dictated text from logs and notifications.
- Make transcript file fallback explicit or overwrite-only.
- Add a purge path for STT state.
- Add machine-readable status output for talk2claude-faster-toggle and gnome-dictation.
- Add focused tests and update relevant STT/GNOME docs.

Constraints:
- Do not implement wake words, overlays, tray apps, Shell extension expansion, or MPRIS.
- Preserve existing command names, environment variables, and clipboard-first default behavior.
- Keep direct typing opt-in.
- Avoid sudo/system-level changes.
- Do not revert unrelated dirty worktree changes.

Verification:
- Run focused tests first.
- Run uv run pytest tests/ -q if feasible.
- Run bash -n on touched shell scripts.
- State any GNOME/audio/manual checks that cannot be completed in the current environment.
```
