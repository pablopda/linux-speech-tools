# STT Manual QA Checklist

Use this checklist for behavior that cannot be proven in headless CI because it
depends on a real microphone, clipboard service, active window, or GNOME session.

## Before Testing

- Install STT dependencies:
  ```bash
  uv sync --locked --extra stt
  ```
- Confirm capability diagnostics:
  ```bash
  ./bin/talk2claude-faster --check
  ./bin/talk2claude-faster --diagnose
  ```
- Explicitly measure model load only when intended:
  ```bash
  ./bin/talk2claude-faster --check --warm-model
  ```
- Purge old state before a clean run:
  ```bash
  ./bin/talk2claude-faster --purge-state
  ```

## Clipboard Mode

- Run:
  ```bash
  ./bin/talk2claude-faster --clipboard
  ```
- Speak one short sentence, pause, then paste into a text editor.
- Verify text is copied to the clipboard.
- Verify stderr/log output does not include dictated text.
- Verify status during capture:
  ```bash
  ./bin/talk2claude-faster-toggle status --plain
  ./bin/talk2claude-faster-toggle status --json
  ```

## Preview Mode

- Run:
  ```bash
  ./bin/talk2claude-faster --preview --clipboard
  ```
- Verify `accept` copies text.
- Verify `edit` copies the edited text.
- Verify `skip`/`retry` does not copy or write the rejected utterance.
- Verify `cancel` stops dictation without typing into another window.

## Typing Mode On X11

- Confirm X11 and `xdotool`:
  ```bash
  echo "$XDG_SESSION_TYPE"
  command -v xdotool
  ```
- Focus a disposable text editor.
- Run:
  ```bash
  ./bin/talk2claude-faster --typing
  ```
- Speak a short sentence and verify it types into the focused editor.
- Force a typing failure if practical and verify clipboard fallback is reported
  before text is copied.

## Typing Mode On Wayland

- Confirm Wayland and explicit uinput setup:
  ```bash
  echo "$XDG_SESSION_TYPE"
  ./bin/talk2claude-faster --check
  ```
- If direct typing is not available, run the setup helper intentionally:
  ```bash
  ./scripts/setup/setup-uinput-permissions.sh
  ```
- Log out/in or run `newgrp uinput` as instructed by the setup helper.
- Focus a disposable text editor and run:
  ```bash
  ./bin/talk2claude-faster --typing
  ```
- Verify direct typing works only after explicit setup.

## GNOME Hotkey Start/Stop/Finalize

- Configure the hotkey:
  ```bash
  ./scripts/setup/setup-faster-hotkey.sh
  ```
- Press the hotkey once and verify notification/status becomes active:
  ```bash
  gnome-dictation status --plain
  gnome-dictation status --json
  ```
- Speak, press the hotkey again, and verify buffered speech finalizes.
- Paste from clipboard and verify the expected text appears.
- Verify `gnome-dictation purge-state` works after stopping.

## Live Developer Prompt Dictation

Prompt-dictation coverage combines the bounded lifecycle baseline from
[`0cd89d6`](https://github.com/pablopda/linux-speech-tools/commit/0cd89d695e1aa2bb5c20f1cc847ee9880685389d)
with the current backend-neutral insertion and stable-focus tests. Run the
focused evidence before manual desktop QA:

```bash
uv run pytest \
  tests/unit/test_session_core.py \
  tests/unit/test_insertion_session.py \
  tests/unit/test_target_context.py \
  tests/unit/test_prompt_delivery_timeouts.py \
  tests/unit/test_toggle_liveness.py \
  tests/test_speech_tools.py -q
```

Together those files cover bounded finalization, late-callback rejection,
focus-drift safety, submit gating, PID reuse and descendant teardown. They do not prove
microphone, compositor, clipboard or application-specific behavior on a real
GNOME session; complete the manual checks below on the target desktop.

- Install the hotkeys and focus provider. Reload GNOME Shell or log out/in if
  the installer requests it:
  ```bash
  ./scripts/install/install-gnome-integration.sh --both
  ./bin/lst-dictate --check
  gdbus call --session \
    --dest org.linux_speech_tools.Focus \
    --object-path /org/linux_speech_tools/Focus \
    --method org.linux_speech_tools.Focus.GetFocus
  ```
- Confirm the provider returns `schema_version: 1`, a non-empty
  `shell_session_id`, a non-negative `focus_generation`, and `locked: false`.
  Do not paste this raw output into public logs because it can contain the
  active window title and PID.
- In Claude Code, Codex CLI, a terminal, and an IDE text field, run an explicit
  target profile:
  ```bash
  ./bin/lst-dictate --profile claude --output overlay
  ./bin/lst-dictate --profile codex --output overlay
  ./bin/lst-dictate --profile ide --output paste
  ```
- Speak a multi-sentence prompt and verify partial text updates while speaking.
- Press the command or `Ctrl+Alt+Space` again and verify final text is copied,
  pasted, or live-typed according to the selected output mode.
- On terminals, verify the auto paste key uses `Ctrl+Shift+V`; on IDE/browser
  text fields, verify it uses `Ctrl+V`.
- During `--output live-type`, focus another window before the next partial
  update. Verify no backspace/paste reaches the new window, the final prompt is
  available on the clipboard, and `--submit always` does not press Enter.
- Return to the original window and verify insertion does not resume: the
  focus-generation change permanently invalidates that dictation session.
- Repeat `--output live-type` in a normal GTK editor whose app is not one of the
  known terminal/IDE/browser classifiers. Verify explicit live typing can use
  the provider's stable identity even when target classification is `unknown`.
- Lock the desktop during a disposable live-type session. After unlocking,
  verify no typing or Enter occurs. Query `GetFocus` while locked if practical
  and verify `window_id`, title, app ID/class, and PID are empty/null.
- Disable the extension in a test session and verify the D-Bus name disappears;
  re-enable it and verify a new `shell_session_id` is returned. Confirm any
  session captured before disable remains invalid.
- With `--output clipboard`, `overlay`, and `stdout`, verify both submit modes
  never press Enter. Verify `paste` and focus-verified `live-type` can submit
  only after insertion succeeds.
- Verify status and cleanup:
  ```bash
  ./bin/lst-dictate status --plain
  ./bin/lst-dictate status --json
  ./bin/lst-dictate purge-state
  ```
- Inspect `lst-dictate status --json` and its private runtime status file.
  Verify neither contains transcript text, clipboard contents, window title,
  PID, app ID/class, window ID, or Shell session ID.
- If direct typing is unavailable on Wayland, verify `--output auto` falls back
  to overlay/final clipboard instead of failing.

## Missing Clipboard Tool Fallback

- Temporarily run in an environment where `wl-copy`, `xclip`, and `xsel` are not
  on `PATH`.
- Confirm default behavior refuses to write transcripts to disk:
  ```bash
  ./bin/talk2claude-faster --clipboard
  ```
- Enable explicit fallback:
  ```bash
  STT_TRANSCRIPT_FALLBACK=1 ./bin/talk2claude-faster --clipboard
  ```
- Verify only the latest transcript is written to the private fallback file.
- Verify `./bin/talk2claude-faster --purge-state` removes the fallback file.

## Parakeet Engine (Optional)

Parakeet is opt-in; faster-whisper stays the default. Requires Python ≥3.10.

- Install the extra and prefetch the model:
  ```bash
  # CPU runtime:
  uv sync --locked --extra stt --extra stt-parakeet
  # Or, for the GPU benchmark, use this instead of stt-parakeet:
  uv sync --locked --extra stt --extra stt-parakeet-gpu
  ./bin/linux-speech-tools-setup --parakeet --check    # reports install + cache status
  ./bin/linux-speech-tools-setup --parakeet            # prefetch ONNX model (~640 MB)
  ```
- Confirm engine selection in diagnostics (no model load):
  ```bash
  ./bin/talk2claude-faster --diagnose --engine parakeet   # shows "Engine: parakeet"
  ```
- Treat that as requested configuration only. Verify the constructed core ONNX
  sessions with an explicitly approved cached model:
  ```bash
  ./bin/talk2claude-faster --warm-model \
    --engine parakeet --device cuda --model small
  ```
  A GPU-ready result must report `Actual device: cuda` and
  `Provider verification: verified-cuda`. An advertised CUDA provider followed
  by `cpu`, `mixed` or `unverified` is not a CUDA result.
- Dictate with Parakeet and verify native punctuation/capitalization:
  ```bash
  STT_ENGINE=parakeet ./bin/talk2claude-faster --clipboard
  ```
- Verify the default engine is unaffected:
  ```bash
  ./bin/talk2claude-faster --clipboard                 # still faster-whisper
  ```

## LATAM Spanish Benchmark (Parakeet ES gate — design doc §6)

The canonical
[`LATAM_ASR_BENCHMARK_2026.md`](../benchmarks/LATAM_ASR_BENCHMARK_2026.md)
report is **pending**. No real-audio WER, latency or GPU result has been recorded.
Do not promote Parakeet for Spanish based on fixture-driven harness tests.

1. Build a consented, privacy-reviewed corpus of exactly 36 clips: 24 AR and 4
   each MX, CO and CL. Include code-switching,
   filenames/repository names, developer terms, short commands, long prompts,
   punctuation, silence and interruptions. Keep raw audio and the full manifest
   outside git; record human-reviewed references plus SHA-256 checksums in that
   private manifest.
   Use the guided external-workspace workflow in
   [`LATAM_CORPUS_ACQUISITION.md`](../benchmarks/LATAM_CORPUS_ACQUISITION.md).
   Each clip requires consent, reference review, post-capture privacy review and
   authentic-accent confirmation. Never reuse one pseudonymous speaker ID
   across accents; MX, CO and CL must be supplied by genuinely matching
   speakers, not imitated by the AR speaker.
   Treat the stored authenticity confirmation as a pointer to reviewed human
   evidence, never as proof produced by the checkbox or program.
2. Run the strict corpus preflight before model loading:

   ```bash
   export LST_LATAM_MANIFEST=/private/latam-asr/manifest.json
   ./bin/lst-asr-corpus validate \
     --directory "$(dirname "$LST_LATAM_MANIFEST")"
   uv run python -m src.utils.asr_benchmark \
     --manifest "$LST_LATAM_MANIFEST" \
     --validate-only
   ```

   This exits before environment collection or engine/model construction. It
   checks the exact accent distribution, required coverage labels, consent and
   review markers, checksums, unique decodable audio and declared audio
   metadata. Its aggregate-only success output is a mechanical preflight, not
   proof that consent, accents or references are truthful; a reviewer must
   verify the private evidence.

3. Run both engines against the identical complete corpus on the same target machine.
   Write the generated report to a temporary file so the canonical scaffold is
   not overwritten:

   ```bash
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

   Use `--device cpu` unless warm diagnostics prove both intended GPU execution
   paths. The generated advertised-provider list is availability evidence; the
   per-engine actual-device and core-session provider rows are the operational
   evidence. A mixed-device run is marked not comparable. Record exact model revisions, runtimes/providers,
   hardware, warm-up/repetition policy, errors, per-accent and overall corpus
   WER, code-switch corpus WER, median per-clip latency and qualitative
   punctuation/technical-token review in the canonical report. If generated
   configuration/environment evidence contains a `[redacted-sha256:…]` token,
   keep the reviewed exact local path or model detail only in the private run
   notes and copy a non-identifying model ID/revision into the canonical report.

4. Parakeet Spanish passes only when the generated report says `Comparable:
   yes` (both engines scored every accepted privacy-safe clip ID with consistent
   hypotheses across repetitions), overall
   corpus WER is within `0.01` absolute of faster-whisper, code-switch-subset
   corpus WER is no worse by more than `0.05` absolute, and review finds no
   systematic filename or developer-term loss. Any engine or clip error makes
   the run not comparable and blocks a decision.

5. `faster-whisper` remains the global default on pass or fail. A pass changes
   only whether Parakeet can move beyond experimental support for Spanish.

| Report status | Corpus accepted | Both engines run | Gate decision | Default |
| --- | --- | --- | --- | --- |
| Pending real audio | pending | pending | pending | faster-whisper |

## Notes To Record

- Distribution and version.
- ASR engine (faster-whisper or parakeet) and, for Parakeet, model + quantization.
- GNOME version when relevant.
- X11 or Wayland.
- Microphone/audio backend selected by `--diagnose`.
- Clipboard tool selected by `--diagnose`.
- Model/device/compute type.
- Any error message that was unclear or non-actionable.
