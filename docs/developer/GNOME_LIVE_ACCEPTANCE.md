# GNOME live acceptance harness

`lst-gnome-acceptance` is the repeatable manual acceptance gate for the GNOME
stable-focus provider and safe insertion workflow. It is deliberately not an
automated desktop-typing test. The harness never types, pastes, presses Enter,
reads a text buffer, or invokes dictation. It records only user-confirmed,
coarse outcomes from isolated disposable buffers.

No live pass is recorded by this document. Run the workflow after logging into
an unlocked GNOME Wayland session. Do not run a mutating phase from a locked
session; the harness will refuse it.

## Safety properties

- Every state-changing or evidence-recording phase requires its own exact
  confirmation token. Prefer the interactive prompt; `--confirm` exists for
  controlled test automation and still requires the exact phase token.
- The provider must return strict schema v1 data from the GNOME session service.
  Invalid, locked, incomplete, conflicting, restarted, or drifting snapshots
  fail closed. Every provider read checks the focus service and
  `org.gnome.Shell` unique D-Bus owners before and after `GetFocus`; all four
  values must be the same nonempty owner. This binds evidence to the current
  Shell D-Bus connection, but is not an independent authentication of the
  desktop process or user account.
- The harness never automatically inserts into terminals, Claude Code, Codex,
  IDEs, browsers, or editors. It never submits anything.
- Reports omit transcripts, clipboard contents, titles, PIDs, raw Shell session
  IDs, and raw window IDs. Window/session fingerprints are one-way, truncated
  SHA-256 values used only to prove transitions inside one run.
- State and reports are private owned regular files. Symlinks, special files,
  relative overrides, oversized data, and unsafe parent directories are
  rejected. The state parent must be owned by the current user with exact mode
  `0700`; this also protects the mode-preserving extension backup.
- The original extension tree, enabled/disabled lists, input sources, MRU
  sources, and active IBus engine are captured before provider preparation.
  `restore` returns them to that baseline after an explicit warning and
  confirmation.

## Install the harness

From the repository:

```bash
./bin/lst-gnome-acceptance --help
```

The normal uv installer also places `lst-gnome-acceptance` in the launcher
directory. Installing the launcher does not install or enable the Shell
extension.

## 1. Capture the baseline

Close unrelated sensitive windows, log into an unlocked GNOME Wayland session,
and start a run:

```bash
lst-gnome-acceptance start
```

Type `START_LIVE_GNOME_ACCEPTANCE` when prompted. This creates the resumable
private state and a bounded backup of the existing extension directory. It does
not install or enable the repository provider.

The default state is:

```text
$XDG_STATE_HOME/linux-speech-tools/gnome-acceptance-v1.json
```

or `~/.local/state/linux-speech-tools/gnome-acceptance-v1.json` when
`XDG_STATE_HOME` is unset. To use an override, set the absolute
`LST_GNOME_ACCEPTANCE_STATE` path before the first phase. Its parent must
already be a private directory owned by the current user with exact mode
`0700`. The harness does not relax permissions on an override parent.

At any point, inspect the coarse progress record:

```bash
lst-gnome-acceptance status
lst-gnome-acceptance report --format markdown
```

## 2. Prepare the repository provider

```bash
lst-gnome-acceptance install-provider
```

Type `INSTALL_REPOSITORY_PROVIDER`. This is the only phase that invokes the
existing user-level GNOME installer, using its explicit `--extension
--noninteractive` path. It never uses sudo. The phase compares input state
before and after and fails if the installer changed it.

GNOME Shell caches extension metadata and modules. When the status becomes
`awaiting-login`, log out and back in; do not use `Alt+F2 r` on Wayland. The
state file survives login, so continue with the same command and environment.

## 3. Exercise provider lifecycle

```bash
lst-gnome-acceptance lifecycle
```

Type `TEST_PROVIDER_LIFECYCLE`. The phase:

1. captures the extension and input state;
2. enables the repository extension;
3. requires a complete unlocked provider snapshot owned by GNOME Shell;
4. disables the extension and requires the D-Bus name to disappear;
5. re-enables it and requires a new Shell session ID;
6. proves the active engine, configured sources, and MRU sources did not change.

Both enabled samples, including the post-re-enable sample, are bound to a
stable current `org.gnome.Shell` D-Bus owner. Observe and race phases also
require owned-by-Shell snapshots at phase boundaries.

It leaves the repository provider enabled only when the phase succeeds, so the
remaining read-only observations can run. On failure it attempts to return the
extension to the state seen at phase entry and records a coarse failure.

## 4. Record focus identity transitions

Each command waits five seconds, then reads two provider snapshots 100 ms apart.
Switch to the named prepared window during the delay and keep it focused until
the command finishes. The two reads must have identical full identity and
generation, and every nonempty application identity must map to the expected
coarse class.

Use a focus-away-and-back sequence, for example:

```bash
lst-gnome-acceptance observe-app --app gtk-editor
lst-gnome-acceptance observe-app --app firefox
lst-gnome-acceptance observe-app --app gtk-editor
```

The confirmation tokens are `OBSERVE_GTK_EDITOR`, `OBSERVE_FIREFOX`, and so on.
The final report passes this gate only when the same window fingerprint is seen
again after an intervening window and its generation increased. Every
intervening authority/window change must also strictly increase the generation;
an equal-generation A→B transition fails closed.

Observe all required application classes before recording their outcomes:

```text
claude-code  codex-cli  ide  chromium  firefox  gtk-editor
```

Claude Code and Codex are expected to have a terminal application identity;
the user confirmation distinguishes the disposable CLI instance. The harness
does not inspect titles or process command lines.

## 5. Run 100 read-only focus races

Prepare two harmless disposable windows, such as an empty GNOME Text Editor
buffer and a local browser test page. Then run:

```bash
lst-gnome-acceptance race
```

Type `RUN_100_FOCUS_RACES` and alternate focus only between those prepared
windows while the phase runs. It performs exactly 100 pairs of provider reads.
It passes only when:

- all 100 pairs are valid;
- at least one pair observes focus drift;
- every drift pair is rejected by the full identity/generation comparison;
- the sequence contains A→B→A with the returning A generation strictly
  greater than the first A generation;
- generation never regresses within the Shell session and strictly advances on
  every authority/window change; and
- the Shell session does not restart during the phase.

No insertion occurs during this test.

## 6. Exercise disposable buffers manually

For every required application class, create a new disposable buffer with no
valuable or executable content. Disable submission, macros, format-on-paste,
and automation. The harness never starts this insertion; the tester performs
the intended `paste` or `live-type` workflow manually and visually verifies the
result before returning to the terminal.

Safe examples:

- GNOME Text Editor: a new unsaved document.
- IDE: a new untitled plain-text editor, never the integrated terminal or
  command palette.
- Chromium/Firefox: a local, offline `contenteditable` test page, never an
  address bar, developer console, form submit control, or authenticated site.
- Claude Code/Codex CLI: a dedicated disposable instance with automatic submit
  disabled. The tester must initiate any paste manually and must not press
  Enter. If that cannot be guaranteed, record `blocked-safe` or `skipped`; the
  overall gate will remain incomplete rather than taking the risk.

Record only the coarse outcome:

```bash
lst-gnome-acceptance record-app \
  --app gtk-editor \
  --backend paste \
  --outcome inserted-exactly-once
```

Type `RECORD_GTK_EDITOR`. Use the corresponding token for each app, such as
`RECORD_CLAUDE_CODE` or `RECORD_CODEX_CLI`.

Allowed outcomes are:

```text
inserted-exactly-once  clipboard-fallback  blocked-safe  unavailable
wrong-target           duplicate           lost-text     skipped
```

`wrong-target`, `duplicate`, and `lost-text` are hard failures. Safe fallback,
blocking, unavailability, or skipping keeps the gate incomplete. A full live
pass requires `inserted-exactly-once` for every required disposable buffer.
Once recorded, a hard failure is permanently latched in that run and cannot be
overwritten by a later manual outcome; start a new state path only after
retaining and restoring the failed run's evidence.

## 7. Finalize evidence

```bash
lst-gnome-acceptance finish
```

Type `FINALIZE_EVIDENCE`. The command refreshes the after snapshot and writes:

```text
gnome-acceptance-v1.json.evidence.json
gnome-acceptance-v1.json.evidence.md
```

Both reports are bounded and mode `0600`. `overall:
complete-manual-evidence` is emitted only when lifecycle, focus-away/back, all
100 races, and every required application outcome pass. This means the tester
attested to the visible application results; it is not an automated proof of
buffer contents. The report keeps separate `before`, `after_acceptance`, and,
after cleanup, `after_restore` desktop/input snapshots. Partial evidence
remains explicitly `incomplete`.

## 8. Restore the exact baseline

Finish with:

```bash
lst-gnome-acceptance restore
```

Type `RESTORE_EXACT_BASELINE`. This intentionally restores the extension files,
extension settings, input-source lists, MRU list, and active IBus engine that
were captured at `start`. Do not run it if you want to preserve intentional
input-source or extension changes made after the baseline. The restore fails
closed if extension files changed outside the harness.

GNOME may still cache extension code until logout. After a successful restore,
log out and back in, then verify the restored extension state and input engine.
The private state, evidence reports, and backup remain under the state
directory for audit/resume. A hidden, recoverable quarantine of the tested
extension remains beside the user extension directory. These artifacts contain
no transcript or window-title data.

## Remaining human checks

This harness records evidence but cannot prove what appeared in an application
because it deliberately does not read application content. The tester remains
responsible for confirming exact-once text, Unicode/Spanish punctuation,
clipboard restoration, and absence of Enter in each disposable buffer. Suspend
and resume also remain a separate manual check; never automate a desktop lock
or suspend from this harness.
