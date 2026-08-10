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

- Install the GNOME integration or run from the source tree:
  ```bash
  ./scripts/install/install-gnome-integration.sh --basic
  ./bin/lst-dictate --check
  ```
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
- With `--output clipboard`, `overlay`, and `stdout`, verify both submit modes
  never press Enter. Verify `paste` and focus-verified `live-type` can submit
  only after insertion succeeds.
- Verify status and cleanup:
  ```bash
  ./bin/lst-dictate status --plain
  ./bin/lst-dictate status --json
  ./bin/lst-dictate purge-state
  ```
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
  uv sync --locked --extra stt --extra stt-parakeet
  ./bin/linux-speech-tools-setup --parakeet --check    # reports install + cache status
  ./bin/linux-speech-tools-setup --parakeet            # prefetch ONNX model (~640 MB)
  ```
- Confirm engine selection in diagnostics (no model load):
  ```bash
  ./bin/talk2claude-faster --diagnose --engine parakeet   # shows "Engine: parakeet"
  ```
- Dictate with Parakeet and verify native punctuation/capitalization:
  ```bash
  STT_ENGINE=parakeet ./bin/talk2claude-faster --clipboard
  ```
- Verify the default engine is unaffected:
  ```bash
  ./bin/talk2claude-faster --clipboard                 # still faster-whisper
  ```

## LATAM Spanish Benchmark (Parakeet ES gate — design doc §6)

Gate before documenting Parakeet as suitable for Spanish. **Do not promote
Parakeet for ES until this passes.**

1. **Corpus:** 20–30 short LATAM utterances across accents (AR/MX/CO/CL),
   including some English↔Spanish code-switching. Record references in a JSON
   manifest:
   ```json
   [
     {"audio": "ar/01.wav", "reference": "hola, ¿cómo andás?", "accent": "AR"},
     {"audio": "mx/01.wav", "reference": "¿qué onda, cómo estás?", "accent": "MX"}
   ]
   ```
2. **Run the benchmark harness** (computes WER + latency per engine):
   ```bash
   uv run python -m src.utils.asr_benchmark \
       --manifest latam.json --engines faster-whisper,parakeet \
       --model small --language es --output latam-report.md
   ```
3. **Pass bar:** Promote Parakeet v3 ES past "experimental for Spanish" only if
   its mean WER is within ~1 point of faster-whisper **and** code-switching does
   not regress noticeably. Otherwise keep faster-whisper the ES default and
   document Parakeet as English-first.
4. **Record the result** below and in the design doc.

| Date | Corpus size | faster-whisper WER | Parakeet WER | Code-switch regressed? | Decision |
| --- | --- | --- | --- | --- | --- |
| _pending_ | – | – | – | – | faster-whisper default (ES) |

## Notes To Record

- Distribution and version.
- ASR engine (faster-whisper or parakeet) and, for Parakeet, model + quantization.
- GNOME version when relevant.
- X11 or Wayland.
- Microphone/audio backend selected by `--diagnose`.
- Clipboard tool selected by `--diagnose`.
- Model/device/compute type.
- Any error message that was unclear or non-actionable.
