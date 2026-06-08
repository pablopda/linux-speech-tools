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

## Notes To Record

- Distribution and version.
- GNOME version when relevant.
- X11 or Wayland.
- Microphone/audio backend selected by `--diagnose`.
- Clipboard tool selected by `--diagnose`.
- Model/device/compute type.
- Any error message that was unclear or non-actionable.
