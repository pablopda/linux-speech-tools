# IBus Direct-Text Integration on GNOME Wayland

## Investigation record

- Status: architectural investigation plus isolated Stage 0 diagnostics; no IBus input engine, routing, installation, or text insertion is implemented
- Investigated: 2026-08-09
- Recorded: 2026-08-10
- Local repository: `linux-speech-tools`, MIT-licensed
- External reference: [VocaHQ/vocalinux](https://github.com/VocaHQ/vocalinux)
- Vocalinux revision reviewed: [`068530535bfffce8e14ed543fa89c1e7a258f73f`](https://github.com/VocaHQ/vocalinux/commit/068530535bfffce8e14ed543fa89c1e7a258f73f)
- Vocalinux release reviewed: [v0.15.0](https://github.com/VocaHQ/vocalinux/releases/tag/v0.15.0)
- Research method: three parallel read-only reviews covering upstream implementation, local integration seams, and GNOME/Wayland platform and packaging risks

This document preserves the conclusions and evidence from the investigation. It is a design reference, not an implementation plan or legal opinion.

The independently authored Stage 0 capability and transient-registration probe
is documented in
[`docs/developer/IBUS_STAGE0_DIAGNOSTICS.md`](../developer/IBUS_STAGE0_DIAGNOSTICS.md).
It leaves all existing output backends and defaults unchanged. Stage 1 remains
unimplemented.

The required upstream-first interoperability review is recorded in
[`VOCALINUX_INTEROPERABILITY_PROPOSAL_2026.md`](VOCALINUX_INTEROPERABILITY_PROPOSAL_2026.md).
No documented supported external insertion API was found at the pinned
Vocalinux revision, so its private socket is explicitly rejected as a project
dependency and no upstream contact has been made.

## Executive conclusion

IBus is the strongest long-term direct-text path for GNOME on Wayland, but it is not a universal replacement for `ydotool`, `xdotool`, overlay, or clipboard output.

The recommended direction is:

1. Add a real IBus input engine as an optional backend.
2. Begin with final-text commits only.
3. Preserve the existing same-target focus policy.
4. Keep current synthetic and non-inserting fallbacks.
5. Add IBus preedit as a separate second phase.
6. Make IBus the automatic preference on GNOME Wayland only after real-desktop validation.

Vocalinux is a valuable behavioral reference because it has already encountered and repaired many GNOME- and Wayland-specific failures. It does not demonstrate that IBus alone is sufficient: its current product still routes among IBus, `ibus-wayland`, `ydotool`, `wtype`, XWayland/`xdotool`, and clipboard fallback.

Because current Vocalinux is AGPL-3.0 and this project is MIT, the implementation must not be copied into this repository without compatible licensing permission or a deliberate licensing change. The safe approach is an independent implementation based on official IBus APIs and an independently written behavioral specification and test suite.

## Question evaluated

The investigation evaluated this proposal:

> Current direct typing uses ydotool or xdotool. That is reasonable for now, but a proper IBus input engine may be more reliable on GNOME Wayland. Vocalinux has already hardened an IBus-based approach and is worth studying before implementing the same GNOME-specific behavior independently.

The proposal is substantially correct, with three qualifications:

- IBus is a client-integrated text-input mechanism, not arbitrary global input injection.
- IBus focus means that an input context is focused; it does not identify the application or window that was focused when dictation began.
- A successful IBus `commit_text()` call does not prove that text reached the target application.

## Current linux-speech-tools behavior

### Classic dictation

Classic dictation selects `ydotool` or `xdotool` in [`src/stt/faster_whisper_typing.py`](../../src/stt/faster_whisper_typing.py). The `TextTyper` class is the primary transport seam, and `FasterWhisperTyping.emit_text()` sends finalized utterances through it.

Strengths:

- Works independently of application input-method support.
- Preserves the current command and test surface.
- `ydotool` works with native Wayland clients when `/dev/uinput` access is correctly configured.
- `xdotool` remains useful for X11 and XWayland.

Limitations:

- `ydotool` has broad virtual-keyboard authority through `/dev/uinput`.
- Synthetic input is global and therefore sensitive to focus changes.
- Keyboard layouts, modifiers, Unicode, and application-specific shortcuts can affect results.
- Classic typing does not currently bind the final insertion to an original target identity.

### Prompt dictation

Prompt dictation already has a useful lifecycle boundary in [`src/stt/prompt_delivery.py`](../../src/stt/prompt_delivery.py):

- `PromptRenderer.update()`
- `PromptRenderer.finalize()`
- `PromptRenderer.can_submit()`
- `PromptRenderer.close()`
- `renderer_for()` for output selection

The current live renderer does not type each partial directly. It:

1. Deletes the previously rendered preview with Backspace.
2. Writes the replacement text to the clipboard.
3. Sends the target-specific paste shortcut.
4. Restores the original clipboard when safe.

This is carefully guarded but inherently more fragile than input-method preedit. It must reason about focus drift, clipboard restoration, paste shortcuts, Unicode deletion length, duplicate finalization, and partial failure.

Target information is represented by [`TargetContext`](../../src/stt/target_context.py), and [`focus_matches()`](../../src/stt/target_context.py) fails closed unless the current stable window identity matches the captured identity.

On Wayland, focus lookup uses the D-Bus provider
`org.linux_speech_tools.Focus`. The GNOME extension now implements that
provider and the Python client validates its versioned, session-scoped window
identity and focus generation. Safe live typing still degrades to clipboard
fallback when the extension is absent, disabled, locked, stale, or unable to
prove the original target. The provider and its live acceptance matrix are
documented in [`docs/user-guide/GNOME_INTEGRATION.md`](../user-guide/GNOME_INTEGRATION.md)
and [`docs/developer/STT_MANUAL_QA_CHECKLIST.md`](../developer/STT_MANUAL_QA_CHECKLIST.md).

IBus could improve the input-context side of this problem, but an IBus `FocusIn` event must not replace the stronger original-window policy where that policy is available.

## What official IBus provides

[`IBusEngine`](https://ibus.github.io/docs/ibus-1.5/IBusEngine.html) provides the primitives needed for an input engine:

- `commit_text()` sends committed text to the current IBus client.
- `update_preedit_text()` displays and updates uncommitted text.
- `focus-in` and `focus-out` identify input-context lifecycle changes.
- Client capability flags report support for preedit and surrounding text.
- Content-purpose signals can identify password and PIN fields when the client reports them.
- Preedit focus modes can clear or commit preedit on focus loss.

[`IBusInputPurpose`](https://ibus.github.io/docs/ibus-1.5/ibus-ibustypes.html) includes password and PIN purposes. An implementation should refuse speech insertion for those purposes, while treating the signal as best-effort because not every client reports it correctly.

Important limitations:

- `commit_text()` returns no application-delivery acknowledgement.
- Only applications and widgets participating in the relevant IBus/toolkit/compositor integration receive commits.
- `FocusIn` identifies an input context, not an original top-level window.
- Committing a newline is not equivalent to pressing the Enter key.
- Surrounding-text support varies among clients and expands access to potentially sensitive application text.

## Vocalinux implementation studied

Vocalinux current `main` contains a real proxy input engine rather than a simple call to an external command.

### Architecture

Its implementation has four main layers:

1. [`TextInjector`](https://github.com/VocaHQ/vocalinux/blob/068530535bfffce8e14ed543fa89c1e7a258f73f/src/vocalinux/text_injection/text_injector.py#L91-L169) selects the backend and handles runtime fallback.
2. [`IBusTextInjector`](https://github.com/VocaHQ/vocalinux/blob/068530535bfffce8e14ed543fa89c1e7a258f73f/src/vocalinux/text_injection/ibus_engine.py#L1196-L1542) controls engine startup, scoped activation, commit requests, and restoration.
3. [`VocalinuxEngineApplication`](https://github.com/VocaHQ/vocalinux/blob/068530535bfffce8e14ed543fa89c1e7a258f73f/src/vocalinux/text_injection/ibus_engine.py#L1115-L1193) connects to IBus, registers a component and factory, and owns the GLib main loop.
4. [`VocalinuxEngine`](https://github.com/VocaHQ/vocalinux/blob/068530535bfffce8e14ed543fa89c1e7a258f73f/src/vocalinux/text_injection/ibus_engine.py#L896-L1004) subclasses `IBus.Engine`, passes physical keys through, and commits Unicode text.

The application and engine communicate through a per-user Unix socket. Vocalinux protects its directory and socket with owner-only permissions and verifies the connecting UID with Linux `SO_PEERCRED`.

### Scoped activation

Vocalinux does not leave its engine permanently selected for each insertion. Its controller broadly performs this sequence:

1. Capture the live current IBus engine.
2. Confirm that the captured engine can be restored.
3. Activate the Vocalinux engine.
4. Wait until it is bound to a focused IBus client.
5. Commit the final text.
6. Tear down or deactivate its component as appropriate.
7. Restore the previous engine.

Restoration order matters. Restoring too early, restoring a guessed source, or restoring before component teardown can leave the user on the wrong layout or input engine.

### Focus readiness

Vocalinux distinguishes an enabled engine from one attached to a client:

- `do_enable()` records an engine instance but does not consider it ready for commits.
- `do_focus_in()` sets a focus event.
- `do_focus_out()` clears it.
- The socket request waits for focus with a timeout.
- Commit work is scheduled on the GLib main thread.
- The caller receives bounded status such as `NO_FOCUS` or `NO_ENGINE` and may reactivate and retry.

This fixed a GNOME Wayland cold-start failure where the first commit after login could be accepted by IBus before Mutter completed input-method binding. See [issue #523](https://github.com/VocaHQ/vocalinux/issues/523) and [PR #533](https://github.com/VocaHQ/vocalinux/pull/533).

This mechanism proves only that some client context is focused. It does not prove that it is the same application or window that was focused when speech capture began.

### GNOME and Wayland handling

The upstream implementation includes behavior for:

- GNOME sessions where `ibus engine` reports no global engine.
- Reading GNOME `org.gnome.desktop.input-sources` state as a restoration fallback.
- Treating explicit IBus sources differently from bare `xkb:*` layout engines.
- Refusing to invent an engine identifier from an unregistered XKB layout.
- Avoiding `setxkbmap` on Wayland because it modifies XWayland state rather than the native Wayland input source.
- Requiring KWin Virtual Keyboard support on KDE Wayland.
- Treating selected standalone compositors as unbridged unless `ibus-wayland` is running.
- Respecting explicit Fcitx or other non-IBus input-method configuration.

Its compositor-aware backend policy is visible in [`text_injector.py`](https://github.com/VocaHQ/vocalinux/blob/068530535bfffce8e14ed543fa89c1e7a258f73f/src/vocalinux/text_injection/text_injector.py#L230-L782).

### Runtime fallbacks

Vocalinux retains several paths:

1. IBus when detected as usable.
2. `ibus-wayland` where it supplies the compositor bridge.
3. `ydotool` for virtual-keyboard injection.
4. `wtype` where supported.
5. XWayland/`xdotool` where an X11 target is available.
6. Clipboard as the final recovery path.

That hierarchy is a central conclusion of this investigation: IBus improves GNOME Wayland integration, but production reliability still requires fallbacks.

## Evidence of hardening

Vocalinux added its IBus backend in February 2026. Subsequent fixes addressed:

- Setup and registration failures.
- Detection of inactive or bare-XKB input methods.
- Preservation of XKB layouts and dead keys.
- Runtime engine destruction and recovery.
- Thread safety.
- GNOME Wayland restoration.
- Avoiding `setxkbmap` on Wayland.
- Waiting for `FocusIn` before the first commit.
- KDE Wayland bridge detection.
- `ibus-wayland` support.
- Requiring a live, restorable prior engine.
- Correct restoration after dynamically registered component teardown.

Representative upstream work:

- Initial engine: [PR #211](https://github.com/VocaHQ/vocalinux/pull/211)
- Thread safety: [PR #452](https://github.com/VocaHQ/vocalinux/pull/452)
- Bare-XKB silent no-op detection: [PR #491](https://github.com/VocaHQ/vocalinux/pull/491)
- GNOME restoration: [PR #500](https://github.com/VocaHQ/vocalinux/pull/500) and [PR #506](https://github.com/VocaHQ/vocalinux/pull/506)
- First-commit focus readiness: [PR #533](https://github.com/VocaHQ/vocalinux/pull/533)
- KDE Wayland guard: [PR #577](https://github.com/VocaHQ/vocalinux/pull/577)
- Engine destruction resilience: [PR #613](https://github.com/VocaHQ/vocalinux/pull/613)
- `ibus-wayland`: [PR #614](https://github.com/VocaHQ/vocalinux/pull/614)
- Restorable source requirement: [PR #623](https://github.com/VocaHQ/vocalinux/pull/623)
- Restoration after component teardown: [PR #643](https://github.com/VocaHQ/vocalinux/pull/643)

Representative failures that shaped the implementation:

- [`commit_text()` succeeded but no app received text on niri/sway](https://github.com/VocaHQ/vocalinux/issues/478).
- [The first GNOME Wayland dictation was silently dropped](https://github.com/VocaHQ/vocalinux/issues/523).
- [KDE Wayland reported success while native apps received nothing](https://github.com/VocaHQ/vocalinux/issues/574).
- [GNOME Wayland Electron apps were restored to the wrong keyboard layout](https://github.com/VocaHQ/vocalinux/issues/497).

Calling Vocalinux's IBus work hardened is justified by this history and its focused tests. However, most upstream automated coverage is mocked. It does not replace real compositor and application testing.

## Upstream limitations not to reproduce

The reviewed Vocalinux implementation provides useful lessons but is not a drop-in ideal design:

- Its socket reads one bounded chunk rather than using an explicit framed message protocol.
- Socket request handling is serial.
- Same-UID verification does not prevent another process owned by the same user from requesting injection.
- Active engine state is shared across threads without a dedicated state lock in every path.
- PID validation does not use process start-time identity.
- IBus commit emission cannot prove application delivery.
- A failed restoration after a successful commit is logged but does not necessarily change the injection result.
- GNOME `mru-sources` is a restoration heuristic, not an original-window identity.
- The engine exits on bus disconnection and is recovered by a later client request rather than reconnecting in place.
- Vocalinux currently commits final text; it does not provide a production example of live IBus preedit for partial speech updates.
- Some logs include transcript snippets; this project should not log transcript content.

## Options considered

| Option | Benefits | Costs and risks | Recommendation |
|---|---|---|---|
| Keep only `ydotool`/`xdotool` | No new service or packaging; broad application reach | Global input, `/dev/uinput` authority, weak Wayland target identity, layout and paste complexity | Keep as compatibility backend, not the only long-term path |
| Add IBus as a preferred backend | Incremental adoption; native Unicode and input context; possible preedit | Requires a real engine, activation lifecycle, restoration, packaging, and real desktop QA | Recommended product direction |
| Replace all backends with IBus | Simpler-looking policy | Does not cover every app/compositor; cannot prove delivery; cannot replace shortcuts | Rejected |
| Copy Vocalinux's implementation | Fastest apparent start | AGPL/GPL licensing conflict with MIT-only distribution; imports upstream limitations | Rejected without permission or licensing decision |
| Independently implement the behavioral contract | Preserves MIT project direction; can improve protocol and safety | More design and testing work | Recommended implementation approach |

## Recommended architecture

The desired local architecture is:

```text
ASR session
    |
    v
Output policy: clipboard | overlay | insert
    |
    v
Target-bound InsertionSession
    |-- IBusInsertionSession
    |     preedit -> final commit -> cancel/close
    |
    |-- SyntheticInsertionSession
    |     clipboard + ydotool/xdotool
    |
    `-- Safe fallback
          clipboard or overlay
```

### Backend-neutral session

A new backend-neutral insertion interface should own target identity and the complete insertion lifecycle. Its conceptual operations are:

```python
class InsertionSession:
    def update(self, text: str, revision: int) -> Result: ...
    def finalize(self, text: str, revision: int) -> Result: ...
    def submit(self) -> Result: ...
    def cancel(self) -> Result: ...
    def close(self) -> None: ...
```

Required session state includes:

- Random session identifier.
- Monotonically increasing revision.
- Captured target identity when available.
- IBus input-context generation or token.
- Last confirmed action.
- Whether a final commit is certain, failed, or ambiguous.
- Whether submit is supported and still safe.

### Why insertion and submit must share a session

Prompt submission currently creates a separate input controller after rendering. With IBus, the component that knows whether insertion succeeded and whether the same input context remains focused must own the submit decision.

Initial IBus support should set submit capability to false. A later design may allow submission through a separately validated mechanism, but only after:

- The final commit was acknowledged at the engine boundary.
- The result is not ambiguous.
- The original target still matches.
- The same input context is still active.
- The target profile explicitly permits submission.

### Service boundary

PyGObject and the IBus typelib are normally installed as distro packages and may not be visible inside an isolated uv environment. The recommended deployment is:

```text
existing uv dictation process
        |
        | private bounded protocol
        v
small system-Python IBus engine process
        |
        | IBus/GLib user-session connection
        v
focused IBus client
```

The engine process should:

- Run as the graphical user, never root.
- Use the existing session-owned IBus daemon.
- Never start `ibus-daemon -x -d -r` on Wayland.
- Pass all physical key events through without logging.
- Use GLib's main loop for IBus operations.
- Shut down cleanly on logout, uninstall, or bus loss.
- Fail closed and allow bounded relaunch after IBus restarts.

### Protocol improvements over the studied implementation

Use a private path under `$XDG_RUNTIME_DIR` rather than persistent user data. The protocol should include:

- Owner-only `0700` directory and `0600` socket.
- `SO_PEERCRED` same-UID validation.
- A per-process random nonce so same-UID processes cannot trivially request injection.
- Length-prefixed framing rather than a single `recv()`.
- Strict UTF-8 validation.
- Maximum message and text sizes.
- Request ID, session ID, revision, operation and target/context token.
- Idempotent finalization.
- Explicit results such as committed, rejected, stale, unavailable, timed out, or ambiguous.
- Bounded connection, focus, commit and shutdown timeouts.
- No transcript content in logs or status files.

## Focus and delivery policy

### Two separate focus guarantees

The implementation must combine rather than conflate:

1. Original-target identity: the window or application selected when the dictation session began.
2. IBus readiness: the input context currently bound to the engine.

`FocusIn` satisfies only the second guarantee. A focus event should increment or replace an input-context generation. Every update, commit and future submit request must carry the session's expected generation and be rejected after `FocusOut` or generation change.

Where GNOME can supply a stable window identifier, preserve the existing exact target match immediately before any final commit or submit. Where it cannot, the initial policy should be conservative:

- Final-only classic dictation may explicitly opt into current-focus insertion.
- Prompt mode that promises original-target safety should fall back to clipboard or overlay.
- Never infer that an active IBus engine implies the correct target.

### Ambiguous delivery

IBus reports commit emission, not application receipt. Therefore:

- A definite pre-commit failure may fall back to another insertion backend if target safety still holds.
- An ambiguous post-commit result must not fall back automatically to synthetic typing because that can duplicate the transcript.
- Copying the transcript to the clipboard is safe after an ambiguous result, provided the user is notified that insertion could not be confirmed.
- Never submit after an ambiguous commit.

### Sensitive fields

If the client reports `IBUS_INPUT_PURPOSE_PASSWORD` or `IBUS_INPUT_PURPOSE_PIN`, reject insertion and submission. Do not request surrounding text in the first implementation. Fail closed while the desktop session is locked.

## Backend routing policy

The first selector could be:

```text
--input-backend auto|ibus|ydotool|xdotool
```

This should remain separate from speech-recognition engine selection and preserve existing `DICTATION_MODE` and prompt output semantics.

Initial `auto` behavior should not silently change existing production behavior. Suggested rollout:

- Explicit `ibus`: require all readiness conditions; return a clear error or safe clipboard fallback when unavailable.
- Existing explicit synthetic backends: preserve current behavior.
- `auto`, experimental phase: preserve current selection unless an experimental IBus preference is enabled.
- `auto`, validated phase: prefer IBus only on verified GNOME Wayland configurations; retain synthetic and clipboard fallbacks.

Do not overwrite an explicit non-IBus input method such as Fcitx. Do not guess a restoration engine. If the previous source cannot be captured and proven restorable, refuse scoped activation.

## Live partial text strategy

IBus preedit is the promising replacement for the current backspace-and-paste preview. The official API supports visible uncommitted text and specifies whether it should clear or commit on focus loss.

Desired lifecycle:

1. Start a target-bound insertion session.
2. Send monotonically versioned partial updates as preedit.
3. Ignore stale or duplicate revisions.
4. Clear preedit on cancellation or focus loss.
5. On successful finalization, replace the preedit with exactly one committed final transcript.
6. Never use destructive Backspace to reconcile IBus preedit.

Preedit must be a separate second phase because toolkit behavior differs. Clients without preedit capability should use overlay for partials and IBus or clipboard for final text.

Avoid `delete_surrounding_text` as a universal replacement strategy. Client support and cursor state differ, and deleting committed text after a focus change is unsafe.

## Packaging impact

Expected native dependencies include:

- IBus.
- PyGObject for the distro Python.
- The IBus GObject-introspection typelib.

Typical Debian/Ubuntu packages are `ibus`, `python3-gi`, and `gir1.2-ibus-1.0`. Other distributions use different names; installer detection should be tested rather than inferred.

Required repository work would include:

- Standalone engine entry point.
- Component metadata or safe dynamic component registration.
- Backend diagnostics and `--check` output.
- Engine/service installation into the user session.
- Clean uninstall and removal of any registered component.
- Restart or logout guidance where required.
- New environment variables in the literal allowlist in [`bin/linux-speech-tools-env`](../../bin/linux-speech-tools-env).
- Installer feature detection without changing current system state during dry-run.
- Documentation for activation, fallback, troubleshooting and privacy behavior.

The project currently supports Python 3.8+, while reviewed Vocalinux requires Python 3.9+. The independent engine must either remain compatible with this project's supported version or explicitly document a system-Python requirement without raising the entire project's minimum.

## Security and privacy requirements

An input engine is a sensitive boundary. Minimum requirements are:

- User-session process only; no root daemon.
- Private authenticated local transport.
- Bounded request sizes and timeouts.
- No transcript logging, including snippets.
- No physical-key logging.
- Reject password and PIN fields when reported.
- Do not request surrounding application text in the first phase.
- Fail closed while locked or after focus/context loss.
- Never send Enter after uncertain insertion.
- Preserve current no-late-callback and bounded-shutdown guarantees.
- Validate process identity if PID files or teardown signals are used.
- Restore the user's input source on every success, error, timeout and shutdown path.
- Never overwrite a newer input-source choice made by the user during the operation.

## Licensing boundary

Current Vocalinux `main` is licensed under AGPL-3.0 following [PR #660](https://github.com/VocaHQ/vocalinux/pull/660). The reviewed source contains that license. Vocalinux v0.15.0 and earlier relevant code were GPL-3.0, so choosing an older revision does not make direct code reuse suitable for MIT-only distribution.

MIT is more permissive than AGPL, but that does not allow an MIT project to remove the obligations attached to third-party AGPL code. Broadly:

- This project's original MIT code may be reused under MIT's permissive terms.
- Vocalinux's authors can impose AGPL terms on their code.
- Combining or adapting AGPL code generally means the distributed derivative cannot remain MIT-only.
- Reading behavior and official API usage is different from copying protected implementation expression.

Safe choices are:

1. Obtain explicit MIT-compatible or dual-license permission from the relevant copyright holders.
2. Deliberately license the combined derivative compatibly with AGPL obligations.
3. Independently implement the behavior from official IBus documentation and an original specification and tests.

This investigation recommends option 3 unless project ownership makes a different licensing decision. Source, comments and tests from Vocalinux should not be copied. This section is an engineering licensing guardrail, not legal advice.

## Staged proof of concept

### Stage 0: diagnostics and service viability

Build no automatic routing yet. Prove:

- Distro Python can import `gi.repository.IBus`.
- The graphical-session IBus bus is reachable.
- A component can register without permanently changing input sources.
- The engine can start, report health and shut down within bounds.
- Missing prerequisites produce actionable diagnostics.

Done condition: no persistent input-source mutation and a clear unavailable reason on unsupported systems.

### Stage 1: explicit final commit

Add an explicit IBus backend for final text only.

Required behavior:

- No partial preedit.
- No automatic Enter.
- No surrounding-text requests.
- Focus/context readiness before commit.
- Password/PIN refusal.
- Live prior-engine capture and guaranteed restoration.
- Clipboard fallback for unavailable or ambiguous results.
- No automatic synthetic fallback after an ambiguous commit.

Done condition: the real GNOME Wayland matrix passes without wrong-window insertion, duplicate insertion, layout mutation or silent success.

### Stage 2: preedit

Add versioned partial updates through IBus preedit.

Required behavior:

- Stale revisions rejected.
- Focus loss cancels rather than commits.
- Final text commits once.
- Unsupported clients receive overlay partials.
- No Backspace reconciliation or clipboard mutation.

Done condition: every supported application passes partial, cancellation and finalization cases without committed partials or duplicate final text.

### Stage 3: automatic GNOME Wayland preference

Consider making IBus preferred only after production-style validation across supported distributions and targets.

Done condition: backend health, fallback telemetry without transcript data, restoration behavior and user-facing diagnostics are judged reliable enough for default use.

## Desktop acceptance matrix

| Area | Scenarios | Required result |
|---|---|---|
| System probe | GI missing, typelib missing, IBus daemon unavailable, engine registration failure | Clear unavailable reason; no persistent state change |
| Basic native targets | GTK3, GTK4, GNOME Text Editor, VTE terminal | 100 consecutive exact final commits, including first commit after login |
| Browser and IDE | Firefox, Chromium, VS Code or another Electron IDE; native Wayland and XWayland | Exact commits or explicit safe fallback; no silent success |
| Sandboxed targets | Flatpak GTK application, browser and Electron application | Exact commits or explicit safe fallback |
| Unicode | Spanish punctuation and accents, combining marks, emoji, Arabic, multiline and 4 KiB text | Exact agreed normalization; no keyboard-layout dependence |
| Focus races | Switch before activation, during `FocusIn`, immediately before commit and before future submit | Zero wrong-window insertions over at least 100 iterations per boundary |
| Input sources | US, LATAM Spanish, PT-BR, Compose/dead keys and existing CJK IBus engine | Source and behavior identical before and after every success and failure |
| Lifecycle | Engine killed, IBus restarted, app closed, source changed, suspend/resume, lock/logout | Recovery or bounded fail-closed; no stale component or engine |
| Sensitive input | Password and PIN widgets | Refused when reported; no transcript logging |
| Malformed requests | Invalid UTF-8, stale revision, duplicate final, oversized message, invalid nonce, foreign UID | Rejected without engine or target mutation |
| Ambiguous commit | Lost acknowledgement after commit was scheduled | No second insertion and no submit; transcript retained on clipboard with warning |
| Latency | Cold and warm final commits | Suggested gate: cold p95 under 2 seconds, warm p95 under 500 ms |
| Preedit, Stage 2 | Partial changes, cancellation, focus loss, final differing from last partial | No committed partials, duplicate final, Backspace reconciliation or clipboard mutation |

Target distributions should include at least Ubuntu 22.04, 24.04 and 26.04 plus a current Fedora GNOME release before automatic preference is considered.

## Automated testing requirements

Unit and integration-style tests should cover:

- Backend selection priority and explicit override.
- System dependency and bus probes.
- Engine registration, launch, readiness and shutdown.
- Focus-in, focus-out and context-generation changes.
- Commit timeout, engine crash, bus restart and reconnect/relaunch.
- Prior-engine capture and restoration on every exit path.
- User input-source change during an insertion.
- Session and revision idempotency.
- Lost or ambiguous acknowledgement without duplicate fallback.
- Password/PIN refusal.
- Socket permissions, peer credentials, nonce, framing and size limits.
- Unicode, combining characters, emoji, newlines and large payloads.
- Preedit capability detection and overlay fallback.
- No submit after focus drift, failed commit or ambiguous result.
- Installer dry-run, installation and uninstall state preservation.
- No transcript content in logs.

Existing tests that must remain green include prompt live-update/focus/submission safety, exact stable-window matching, ydotool/xdotool output paths, and bounded external-command failures.

Automated tests cannot substitute for the desktop acceptance matrix. Mocked IBus tests can verify state machines but not compositor-to-application delivery.

## Expected repository blast radius

Likely implementation areas:

- New backend-neutral insertion-session module under `src/stt/`.
- New standalone IBus engine/service module and entry point.
- New `IBusRenderer` in or adjacent to `src/stt/prompt_delivery.py`.
- Adaptation of `TextTyper` in `src/stt/faster_whisper_typing.py`.
- Target/context generation integration with `src/stt/target_context.py`.
- Prompt submission ownership changes in `src/stt/prompt_dictation.py`.
- Backend selector and literal environment allowlist changes.
- User-session install, uninstall and diagnostic logic.
- Focused unit tests plus a documented manual GNOME Wayland matrix.

The ASR engine and audio-capture architecture should not need material changes.

## Decisions recorded

The investigation recommends recording these decisions as the baseline for future planning:

1. IBus is a preferred future GNOME Wayland backend, not a universal replacement.
2. Clipboard and overlay remain durable safe outputs.
3. `ydotool` and `xdotool` remain compatibility backends.
4. The first IBus implementation is final-commit-only and explicitly enabled.
5. Live partials use preedit only in a later phase.
6. Original-target identity and IBus input-context readiness remain separate checks.
7. An ambiguous commit never triggers automatic synthetic reinsertion or Enter.
8. The input source is never guessed and a newer user choice is never overwritten.
9. Sensitive fields, locked sessions and unauthenticated requests fail closed.
10. Vocalinux informs requirements and regression cases, but its source and tests are not copied into this MIT repository.

## Open decisions for a future implementation plan

- Whether Stage 1 requires manual selection of the engine or implements scoped automatic activation immediately.
- Whether the transport should be a private Unix socket or a narrowly scoped user-session D-Bus interface.
- How GNOME should expose stable original-window identity for prompt mode.
- Which applications constitute the supported initial target set.
- Whether classic dictation may use current-input-context semantics when stable original-window identity is unavailable.
- Whether telemetry should record backend result categories without transcript or target-title data.
- Which distributions and IBus versions define the supported baseline.
- Whether to seek dual-license permission from Vocalinux maintainers before implementation.

## Primary references

### Official platform documentation

- [IBusEngine API](https://ibus.github.io/docs/ibus-1.5/IBusEngine.html)
- [IBus generic types, capabilities, preedit modes and input purposes](https://ibus.github.io/docs/ibus-1.5/ibus-ibustypes.html)
- [IBus upstream repository](https://github.com/ibus/ibus)
- [GNOME input methods are based on IBus](https://release.gnome.org/3-6/)

### Vocalinux pinned sources

- [Repository](https://github.com/VocaHQ/vocalinux)
- [Reviewed commit](https://github.com/VocaHQ/vocalinux/commit/068530535bfffce8e14ed543fa89c1e7a258f73f)
- [IBus engine](https://github.com/VocaHQ/vocalinux/blob/068530535bfffce8e14ed543fa89c1e7a258f73f/src/vocalinux/text_injection/ibus_engine.py)
- [Backend router](https://github.com/VocaHQ/vocalinux/blob/068530535bfffce8e14ed543fa89c1e7a258f73f/src/vocalinux/text_injection/text_injector.py)
- [IBus engine tests](https://github.com/VocaHQ/vocalinux/blob/068530535bfffce8e14ed543fa89c1e7a258f73f/tests/test_ibus_engine.py)
- [IBus core tests](https://github.com/VocaHQ/vocalinux/blob/068530535bfffce8e14ed543fa89c1e7a258f73f/tests/test_ibus_engine_core.py)
- [Backend-router tests](https://github.com/VocaHQ/vocalinux/blob/068530535bfffce8e14ed543fa89c1e7a258f73f/tests/test_text_injector.py)
- [Current license](https://github.com/VocaHQ/vocalinux/blob/068530535bfffce8e14ed543fa89c1e7a258f73f/LICENSE)

### Relevant local sources

- [`src/stt/prompt_delivery.py`](../../src/stt/prompt_delivery.py)
- [`src/stt/prompt_dictation.py`](../../src/stt/prompt_dictation.py)
- [`src/stt/target_context.py`](../../src/stt/target_context.py)
- [`src/stt/faster_whisper_typing.py`](../../src/stt/faster_whisper_typing.py)
- [`bin/linux-speech-tools-env`](../../bin/linux-speech-tools-env)
- [`docs/FASTER_QUICKSTART.md`](../FASTER_QUICKSTART.md)
- [`docs/developer/STT_MANUAL_QA_CHECKLIST.md`](../developer/STT_MANUAL_QA_CHECKLIST.md)
