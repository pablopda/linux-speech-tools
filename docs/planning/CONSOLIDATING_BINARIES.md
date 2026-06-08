# Command Surface Plan for Linux Speech Tools

## Current Direction

The maintained command surface is:

| Command | Purpose | Status |
| --- | --- | --- |
| `say` | Edge TTS/cloud single-text speech and file output | Canonical |
| `say-local` | Kokoro/offline single-text speech | Canonical |
| `say-read` | Kokoro/offline document and URL read-aloud | Canonical |
| `talk2claude-faster` | Clipboard-first faster-whisper dictation | Canonical |
| `gnome-dictation` | GNOME hotkey wrapper for dictation | Canonical |
| `linux-speech-tools-setup` | Model setup/check helper | Canonical |

Compatibility wrappers remain installed for now:

| Wrapper | Replacement |
| --- | --- |
| `say-read-es` | `say-read -l es -v ef_dora` |
| `say-read-continuous` | `say-read` |
| `say-read-mvp` | `say-read` |
| `say-read-gnome` | Beta GNOME notification-control wrapper around `say-read` |
| `talk2claude` | Legacy fixed-duration dictation; prefer `talk2claude-faster` |
| `talk2claude-faster-toggle` | Hotkey helper used by `gnome-dictation` |

## Policy

- Primary docs should show canonical commands only.
- Compatibility wrappers should keep clean `--help` behavior and a clear
  deprecation warning during normal execution.
- Do not remove wrappers until a major-version release or a documented migration
  window.
- Do not add new top-level commands for single options; add flags to canonical
  commands instead.

## Deferred Work

- Merge `say-read-gnome` into `say-read --gnome` after the GNOME reader control
  path is stable.
- Decide whether `talk2claude` remains as legacy compatibility or becomes a
  wrapper around `talk2claude-faster`.
- Revisit installer PATH clutter once compatibility usage is low enough to stop
  installing deprecated wrappers.
