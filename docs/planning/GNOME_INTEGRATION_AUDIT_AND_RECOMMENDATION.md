# GNOME Integration Audit And Recommendation

Date: 2026-06-05

This note captures the read-only multi-agent audit of the current GNOME
integration and the follow-up research into whether the current integration
model is the best long-term direction.

## Executive Summary

The current GNOME integration is useful but mixed in maturity.

- Dictation via `gnome-dictation` and `talk2claude-faster-toggle` is a pragmatic
  GNOME custom-shortcut workflow. It is close to usable, but setup/status paths
  have drifted.
- Read-aloud via `say-read-gnome` is custom D-Bus plus notification actions. It
  is not full native media integration and should not be described as MPRIS or
  media-player integration until it implements that standard.
- The GNOME Shell extension is the weakest part. It appears stale relative to
  modern GNOME Shell extension APIs, polls the legacy dictation command, and
  likely should be fixed for one GNOME family or de-scoped.
- The best architecture depends on the product goal:
  - Keep the current custom-shortcut approach for a small CLI-first tool.
  - Use MPRIS for real read-aloud media controls.
  - Use a real GNOME/GApplication service for persistent notifications and
    application identity.
  - Use the GlobalShortcuts portal only when the project becomes an app/daemon
    that can feature-detect portal availability and handle user-approved global
    shortcut sessions.

## Current Integration Map

### Dictation

Primary files:

- `bin/gnome-dictation`
- `bin/talk2claude-faster-toggle`
- `bin/talk2claude-faster`
- `src/stt/faster_whisper_auto.py`
- `src/stt/faster_whisper_clipboard.py`
- `scripts/setup/setup-faster-hotkey.sh`
- `scripts/install/install-gnome-integration.sh`
- `gnome-extension/extension.js`

Current flow:

1. `gnome-dictation toggle` loads `bin/linux-speech-tools-env`.
2. If available, it delegates to `talk2claude-faster-toggle`.
3. `talk2claude-faster-toggle` starts
   `uv --project "$PROJECT_ROOT" run --extra stt python -m src.stt.faster_whisper_auto --clipboard`.
4. PID, logs, and fallback transcripts are stored under
   `$XDG_RUNTIME_DIR/linux-speech-tools` or XDG state.
5. The second hotkey press sends `SIGINT`, waits for graceful finalization, then
   escalates if needed.
6. GNOME feedback is mostly `notify-send`.

This is a good CLI-first workflow. The main issue is not the dictation engine;
it is the surrounding desktop contract.

### Read-Aloud Controls

Primary files:

- `bin/say-read-gnome`
- `src/gnome/gnome-reader-control.py`
- `src/gnome/gnome-notification-handler.sh`
- `src/tts/say_read.py`
- `docs/user-guide/GNOME_MEDIA_CONTROLS.md`

Current flow:

1. `say-read-gnome <source>` estimates title and chunk count.
2. It starts or contacts `gnome-reader-control.py`.
3. It registers a D-Bus session through
   `start_reading(source, title, total_chunks, token)`.
4. It starts `say_read.py` in a new process group via `setsid`.
5. It attaches the process through `attach_process(pid, token)`.
6. It parses progress lines like `[current/total]` and calls
   `update_progress(current)`.
7. Notification actions call pause, resume, and stop on the custom D-Bus
   service.

This is notification-control integration, not native media-player integration.

### Installer And Setup

Primary files:

- `installer.sh`
- `scripts/install/install-with-uv.sh`
- `scripts/install/install-gnome-integration.sh`
- `scripts/setup/setup-faster-hotkey.sh`
- `scripts/setup/setup-uinput-permissions.sh`

Current flow:

- `installer.sh --with-gnome` installs launchers and GNOME-related dependencies
  but does not configure the GNOME hotkey or install the Shell extension.
- `install-gnome-integration.sh --basic` configures a `Ctrl+Alt+V` custom
  shortcut.
- `setup-faster-hotkey.sh` configures a separate custom keybinding path, also
  defaulting to `Ctrl+Alt+V`.

This leads to duplicate or conflicting setup paths.

## Findings From The Audit

### What Is Working Well

- The faster dictation path uses private XDG runtime/state directories.
- Faster process stopping validates PID ownership, command line, and project
  root before signaling.
- The reader D-Bus service validates reader PID ownership, command line, working
  directory hints, and `/proc` start time before signaling the process group.
- Config loading through `linux-speech-tools-env` treats `install.env` as data
  and rejects unsafe file ownership or permissions.
- Most subprocess calls use argv arrays rather than shell string execution.

### High-Priority Gaps

1. GNOME hotkey setup is fragmented.

   Three paths can configure similar behavior:

   - `gnome-dictation setup`
   - `install-gnome-integration.sh --basic`
   - `setup-faster-hotkey.sh`

   They do not use one shared keybinding identity, and uninstall paths do not
   clean up every variant.

2. The GNOME Shell extension is stale.

   The extension toggles `gnome-dictation`, but polls legacy
   `talk2claude status`. It also appears incompatible with the GNOME versions
   advertised by `metadata.json`, and its keybinding registration does not use a
   real settings schema.

3. Read-aloud controls are not MPRIS.

   The current implementation uses a project-specific D-Bus service and
   notifications. That is fine as a beta, but it is not the standard media-player
   integration path.

4. Reader lifecycle cleanup is incomplete.

   Because the reader runs in a new process group, parent termination can leave
   audio running and D-Bus state stale unless traps are added.

5. Privacy defaults need tightening.

   Dictated text snippets can persist in logs, and fallback transcript files can
   retain full text. Private permissions help, but retaining user speech text by
   default is still a product risk.

6. Installer behavior is inconsistent.

   `install-gnome-integration.sh` can overwrite `install.env` with fewer keys
   than the main installer writes. `--with-gnome` installs dependencies but does
   not actually enable the main hotkey integration.

7. Docs and tests lag implementation.

   GNOME docs still reference old checkout paths, old D-Bus signatures, and
   older command behavior. Automated tests mostly check syntax and static
   strings rather than mocked GNOME behavior.

## Research: Is The Current Way Best?

Short answer: it is a reasonable MVP integration for a CLI tool, but it is not
the best long-term GNOME integration if the goal is a native desktop experience.

### GNOME Application Identity

GNOME recommends reverse-DNS application IDs and uses the ID for D-Bus, desktop
files, GSettings schemas, notification identity, and system state:

- https://developer.gnome.org/documentation/tutorials/application-id.html

GNOME integration guidance also recommends D-Bus activation instead of directly
forking applications when integrating with the desktop:

- https://developer.gnome.org/documentation/guidelines/maintainer/integrating.html

Implication for this project:

- A future first-class GNOME mode should use an app ID such as
  `io.github.linux_speech_tools.SpeechTools` or another stable reverse-DNS ID.
- The GNOME reader/control service should eventually have a `.desktop` file,
  D-Bus activation, and a matching service name.
- The current ad hoc service start from `say-read-gnome` is acceptable for beta
  but not ideal as the long-term desktop contract.

### Notifications

GNOME's notification guidance expects apps to use `GApplication` or
`GtkApplication`, provide a matching desktop file, and support D-Bus activation
so notification persistence and actions remain associated with the application:

- https://developer.gnome.org/documentation/tutorials/notifications.html
- https://gnome.pages.gitlab.gnome.org/libsoup/gio/GNotification.html

The GNotification reference also states that notification button interaction is
associated with application actions, not with the notification object itself.

Implication for this project:

- `notify-send` is fine for lightweight status toasts.
- For persistent read-aloud controls, a real `GApplication`/GNotification service
  is the better GNOME-native path.
- Current notification action handling should be treated as beta, because it is
  not using GNOME's preferred application/action model.

### Media Controls

MPRIS is the freedesktop standard D-Bus interface for discovery, query, and basic
control of media players:

- https://specifications.freedesktop.org/mpris-spec/latest/

The spec requires media players to request a bus name beginning with
`org.mpris.MediaPlayer2` and expose `/org/mpris/MediaPlayer2` with
`org.mpris.MediaPlayer2` and `org.mpris.MediaPlayer2.Player`:

- https://specifications.freedesktop.org/mpris-spec/latest/
- https://specifications.freedesktop.org/mpris-spec/latest/Media_Player.html
- https://specifications.freedesktop.org/mpris-spec/latest/Player_Interface.html

Implication for this project:

- If the product goal is "pause/resume/stop from notification buttons", the
  current custom D-Bus approach can be hardened.
- If the product goal is "native media controls, media keys, playerctl support,
  and GNOME media surfaces", the project should implement MPRIS.
- The docs should avoid calling the current read-aloud integration "native media
  controls" until MPRIS exists.

### Global Shortcuts

GNOME supports user-created custom shortcuts in Settings, where a shortcut runs a
command:

- https://help.gnome.org/gnome-help/keyboard-shortcuts-set.html

The xdg-desktop-portal GlobalShortcuts interface is the standards-track API for
apps to create sessions, bind shortcuts, and receive activation/deactivation
signals:

- https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html

Implication for this project:

- For a CLI-first tool, GNOME custom shortcuts are pragmatic and user
  understandable.
- Programmatically writing GNOME custom-keybinding GSettings is a convenience,
  not a portable desktop API. It should be idempotent, reversible, and clearly
  documented as GNOME-specific.
- A future GUI or background service should feature-detect and prefer the
  GlobalShortcuts portal where available, while retaining the custom-shortcut
  setup as a fallback.

### Shell Extension

GNOME Shell extensions are powerful, but they run as part of the Shell. GNOME's
extension site warns that extension code becomes part of the core operating
system and can cause system misbehavior:

- https://extensions.gnome.org/about/

GNOME 45 and later extensions use ES modules and require `extension.js` to
export a default class with `enable()` and `disable()` methods:

- https://gjs.guide/extensions/upgrading/gnome-shell-45.html

GNOME extension review guidance requires created objects, signal handlers, and
main loop sources to be cleaned up in `disable()`:

- https://gjs.guide/extensions/review-guidelines/review-guidelines.html

Implication for this project:

- A Shell extension is not the best first integration layer for this project.
- Keep it only if the panel indicator is core to the product.
- If kept, support one GNOME family at a time, use the GNOME 45+ ESM shape for
  modern GNOME, use a real schema, poll the canonical faster status API, and
  restrict activation to normal user sessions.

## Recommended Target Architecture

### Tier 1: Keep CLI-First GNOME Integration Solid

This should be the next implementation target.

- Make `talk2claude-faster-toggle` the canonical dictation toggle.
- Make `gnome-dictation` a thin user-facing wrapper.
- Add `gnome-dictation status --plain` and `gnome-dictation status --json`.
- Use one canonical keybinding path and one command.
- Make all setup scripts idempotent.
- Make uninstall remove both current and legacy keybinding paths.
- Stop logging dictated text snippets.
- Make fallback transcript files opt-in or overwrite by default.
- Add tests with mocked `gsettings`, `notify-send`, and `dbus-send`.

This gives the project reliable GNOME value without forcing a full app rewrite.

### Tier 2: Harden Read-Aloud Notification Controls

This is the right short-term path if MPRIS is not being implemented immediately.

- Add parent signal traps in `say-read-gnome`.
- Stop or reject an existing reader session before starting a new one.
- Add token or sender validation for every mutating D-Bus method.
- Make `pause_reading`, `resume_reading`, and `stop_reading` report signal
  failure accurately.
- Send authoritative progress from the reader instead of estimating totals in
  the wrapper.
- Use notification replacement IDs or GNOME notification APIs instead of
  blocking `notify-send --wait` progress updates.
- Update docs to call this "notification controls", not "native media controls".

### Tier 3: Implement True Media Integration With MPRIS

This is the best path if the product goal is a native long-form audio reader.

- Create an MPRIS service with bus name such as
  `org.mpris.MediaPlayer2.linux_speech_tools`.
- Expose `/org/mpris/MediaPlayer2`.
- Implement at least:
  - `org.mpris.MediaPlayer2`
  - `org.mpris.MediaPlayer2.Player`
  - `org.freedesktop.DBus.Properties`
- Map read-aloud state to:
  - `PlaybackStatus`
  - `Metadata`
  - `Position`
  - `CanPlay`
  - `CanPause`
  - `CanControl`
- Emit `PropertiesChanged` when playback state or metadata changes.
- Keep the custom reader D-Bus API only for project-specific controls that MPRIS
  does not model.

### Tier 4: Decide The Fate Of The Shell Extension

Recommended default: de-scope it until the CLI and MPRIS layers are solid.

If keeping it:

- Port to GNOME 45+ ESM and advertise only compatible shell versions.
- Use `gnome-dictation status --json` or `talk2claude-faster-toggle status` as
  the single source of truth.
- Remove extension-managed keybindings unless a real schema is shipped.
- Restrict action mode/session mode so dictation does not start from lock or
  inappropriate modal states.
- Fail install loudly if enablement fails.

## Implementation Plan

### Phase 1: Make Dictation Integration Coherent

1. Add canonical status output.
2. Consolidate hotkey paths and commands.
3. Preserve existing `install.env` keys in GNOME integration setup.
4. Remove transcript snippets from logs.
5. Add a state purge command.
6. Add mocked tests for status, hotkey setup, and uninstall.

### Phase 2: Fix Documentation And Tests

1. Update `docs/user-guide/GNOME_INTEGRATION.md`.
2. Update `docs/user-guide/GNOME_MEDIA_CONTROLS.md`.
3. Correct demo script paths.
4. Add Python compile coverage for `src/gnome`.
5. Replace stale `tests/test-gnome-integration.sh` assumptions with either
   mocked tests or a clearly manual GNOME checklist.

### Phase 3: Harden Reader Controls

1. Add `trap` cleanup in `say-read-gnome`.
2. Make D-Bus mutating methods token-aware.
3. Reject or stop existing sessions before starting another.
4. Make progress structured and authoritative.
5. Improve dependency checks for GNOME Python, `dbus`, `gi`, `gsettings`,
   `notify-send`, `ffplay`, `uv`, and model assets.

### Phase 4: Choose Native Media Direction

Decision point:

- If notification actions are enough, stop after Phase 3 and document the beta
  scope honestly.
- If native media controls are required, implement MPRIS as a separate service.

### Phase 5: Revisit Shell Extension

Decision point:

- Remove or mark extension experimental if the project remains CLI-first.
- Rewrite the extension only if a panel indicator is a required product feature.

## Final Recommendation

The current GNOME integration is not the best long-term architecture for a
native desktop experience, but it is a good MVP foundation for a CLI-first
speech tool.

The next best move is not to jump straight to a Shell extension. The next best
move is to stabilize the command, status, setup, privacy, and notification
lifecycle contracts. After that, implement MPRIS if the goal is true media
integration.

In priority order:

1. Consolidate dictation hotkey/status/setup.
2. Fix privacy defaults.
3. Harden `say-read-gnome` lifecycle and D-Bus controls.
4. Update docs/tests to match reality.
5. Implement MPRIS for true media controls.
6. De-scope or rewrite the Shell extension.
