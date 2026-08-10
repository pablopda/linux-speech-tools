# Safe Insertion Implementation Audit — August 2026

Status: active implementation audit
Scope: the approved P0 closure and P1 reusable safety layer from
`SAFE_INSERTION_AND_PRODUCT_ROADMAP_PLAN.html`

This is an append-only working ledger for the implementation and independent
review loop. It does not authorize the conditional IBus stages, a release, live
GNOME installation, or claims based on unrecorded real-audio benchmarks.

## Baseline

- Branch: `main`
- Baseline commit: `0cd89d695e1aa2bb5c20f1cc847ee9880685389d`
- Baseline command: `uv run pytest tests/ -v`
- Baseline result: 318 passed, 2 skipped, 83 subtests
- Baseline worktree: two untracked planning documents; no production-code
  modifications
- Baseline `git diff --check`: clean

## Authorized implementation scope

1. Close the June 2026 code-audit record with evidence-backed disposition
   states.
2. Prepare, but do not fabricate, the real-audio LATAM ASR benchmark record.
3. Normalize insertion outcomes and move insertion, finalization, and submission
   policy into a backend-neutral insertion session.
4. Add a privacy-minimized GNOME stable-focus provider and strengthen target
   matching with a per-shell-session identity and focus generation.
5. Preserve the existing output methods and fail-closed behavior.
6. Independently review, validate, repair, and repeat until no relevant issue
   remains.

## Explicitly deferred gates

- Real-audio recording and benchmark execution
- Release version selection, tagging, publication, or system installation
- Live GNOME extension enablement and real-desktop acceptance testing
- Product-layer P2 implementation until its first vertical slice is selected
- Vocalinux coordination or reuse of AGPL implementation code
- IBus Stage 0, Stage 1, preedit, or automatic routing

## Candidate issue ledger

| ID | Area | Candidate | Validation | Disposition | Evidence |
| --- | --- | --- | --- | --- | --- |
| INS-01 | Paste safety | Focus can change during clipboard I/O and the paste transport still dispatches to the new window. | Independently reproduced by changing the focus guard from inside `clipboard.write()`. | confirmed; repair in progress | Paste was recorded after the guard became false. |
| INS-02 | Ambiguity | An unexpected transport exception is labelled pre-dispatch and can trigger a duplicate clipboard fallback after an actual dispatch. | Independently reproduced with a transport that records dispatch and then raises. | confirmed; repair in progress | The session returned `failed-before-dispatch` and invoked fallback. |
| INS-03 | Clipboard | Live-type close restores the startup clipboard over a newer value copied by the user. | Independently reproduced with original → dictated → new-user-value → close. | confirmed; repair in progress | Close replaced the newer value with the original. |
| INS-04 | Submit safety | Focus failure on the second submit guard is not latched, so a later retry can send Enter after focus returns on X11. | Independently reproduced with guard results true → false → true → true. | confirmed; repair in progress | First submit was rejected; second dispatched. |
| INS-05 | Voice command | Suffix matching treats negated prose such as “do not submit” as a submit command. | Independently reproduced through `PromptDictation.maybe_submit()`. | confirmed; repair in progress | The renderer's submit method was called. |
| INS-06 | Error propagation | An ambiguous Enter result is ignored and the command exits successfully after a success notification. | Independently reproduced with an ambiguous submit transport result. | confirmed; repair in progress | Exit status was 0 and no warning was emitted. |
| INS-07 | Classic status | A successful clipboard fallback after a proven classic typing pre-dispatch failure leaves status at the original failure. | Independently traced through `FasterWhisperTyping.emit_text()`. | confirmed; repair in progress | Clipboard copy succeeds while `insertion.last_result` remains `failed-before-dispatch`. |
| INS-08 | Result evidence | Target-authorized update/final results do not record the successful target match. | Independently inspected and exercised in the state machine. | confirmed; repair in progress | `target_token_match` remains `None` except on submit. |
| FOCUS-01 | D-Bus lifecycle | The name-loss callback clears the owner ID, preventing teardown from cancelling a queued ownership request. | Independently checked against the GLib name-owner contract. | confirmed; repair in progress | `destroy()` cannot call `unown_name()` after `_onNameLost()`. |
| FOCUS-02 | Target retention | Auto detection discards a valid strong GNOME identity when application classification is `unknown`. | Independently reproduced with a valid GNOME Text Editor payload. | confirmed; repair in progress | Parsed identity became a blank `TargetContext` in `detect_target("auto")`. |
| FOCUS-03 | User workflow | Existing GNOME setup/QA documentation does not install or verify the new provider. | Independently reviewed across the installer and manual documentation. | confirmed; repair in progress | The documented `--basic` path omits the extension; provider lifecycle checks are absent. |
| FOCUS-04 | Release evidence | GNOME 50 is declared after archive/static checks but before a runtime extension load and service lifecycle check. | Independently compared with the plan's GNOME 50 done-condition. | confirmed; repair in progress | The current checks only grep and inspect the ZIP; live desktop checks remain manual. |
| REL-01 | Installer | README's pinned v1.0.2 streamed command runs an obsolete hard-coded installer, not the documented verified bootstrap. | Independently checked with `git show v1.0.2:installer.sh`. | confirmed; repair in progress | The tagged script writes `/home/arkat/bin/talk2claude` and has no tarball/hash flow. |
| REL-02 | Release integrity | A source tag cannot contain the subsequently computed hash of its own archive; the existing flow updates only `main`. | Independently traced through `release.sh`. | confirmed; repair in progress | The advertised tag bootstrap permanently retains the previous hash. |
| REL-03 | Release candidate | Release QA runs before mutations and changelog generation recreates duplicate top-level documents. | Independently traced and reproduced from `generate_changelog()`. | confirmed; repaired; awaiting re-audit | The workflow now promotes `[Unreleased]`, validates after mutation, and prepares a separate bootstrap tag. |
| BENCH-01 | ASR benchmark | Arithmetic mean of per-clip WER and discarded subset metadata cannot decide the documented LATAM gates. | Independently reproduced with one failed 1-word clip and one perfect 100-word clip. | confirmed; repair in progress | Reported macro WER is 0.500 while corpus WER is 0.0099; code-switch/category gates are absent. |
| AUDIT-01 | SSRF ledger | D1's `fixed` label hides DNS-rebinding and browser-render/subresource residual risk. | Independently traced in the fetch/render paths. | confirmed; documentation repair in progress | Validation and connection use separate resolution; browser navigation precedes final URL rejection. |
| AUDIT-02 | Streaming ledger | D6 still performs sequential synth-then-blocking-play, although fatal player errors were fixed. | Independently traced in `say_read.py`. | confirmed; documentation repair in progress | The remaining behavior is an accepted trade-off, not a complete fix. |
| DOC-01 | GNOME claim | Changelog says GNOME 45–48 is verified without a live version matrix. | Independently checked against the port commit and QA record. | confirmed; repaired; awaiting re-audit | Wording now says the code targets the declared range and keeps live validation as a gate. |
| DOC-02 | QA commands | The documented focused command omits tests that provide the claimed focus and submit coverage. | Independently compared test selection with collected tests. | confirmed; repair in progress | Insertion/target and legacy prompt suites were not in the command. |
| DOC-03 | Benchmark privacy | The scaffold alternately permits a manifest in git and requires it outside git. | Independently reviewed within the same document. | confirmed; repair in progress | The full manifest can contain sensitive references and paths. |
| DOC-04 | Audit evidence | One commit link is invalid and several test descriptions overstate their behavioral coverage. | Independently checked against git ancestry, remote links, and test bodies. | confirmed; repair in progress | `394008d` is not on main; the integrated commit is `a1e4e7c`. |

## Amendments

### 2026-08-10 — Ledger opened

Recorded the clean baseline, implementation boundary, and explicit evidence and
approval gates before reviewing the team changes.

### 2026-08-10 — First independent review

Recorded eight insertion-policy findings and four GNOME focus-provider findings.
Every listed candidate was independently reproduced or traced before entering
the repair queue. The combined automated suite remained green at 345 passed,
2 skipped and 83 subtests, demonstrating that these were coverage gaps rather
than already failing regressions.

### 2026-08-10 — P0 evidence review

Recorded release-bootstrap, release-candidate, benchmark-math, historical-ledger
and documentation discrepancies. Removed the broken v1.0.2 streamed-install
instruction immediately. The release repair uses a source tag followed by a
separate immutable bootstrap tag so the latter can contain the already-known
source-archive hash without self-reference.

### 2026-08-10 — Terminology correction

The prior amendment's phrase “immutable bootstrap tag” was too strong: Git
tags and GitHub releases are operational controls, not an intrinsic
immutability guarantee. The repaired design uses a versioned bootstrap tag,
an exact provenance check, a versioned release asset, a pinned SHA-256 digest,
and fail-closed download verification. The release workflow also refuses to
publish until it has smoke-tested the exact canonical bytes downloaded from
the draft release.

### 2026-08-10 — Validated repair ledger

Every confirmed implementation candidate was repaired and independently
re-audited. The final dispositions are:

| IDs | Final disposition | Independent evidence |
| --- | --- | --- |
| INS-01–INS-08 | fixed | Focus is rechecked at every destructive boundary; focus loss latches; insertion exceptions are conservatively ambiguous; clipboard ownership is checked before restoration; voice submit is exact and omitted from prompt text; submit failure is propagated; fallback status and target-match evidence are structured. |
| INS-09 | fixed | Classic discovery no longer shells out; daemon probes and clipboard commands have 3-second bounds; typing dispatch has a 30-second bound; timeout after possible dispatch is ambiguous and never clipboard-retried. |
| INS-10 | fixed | Shared desktop notification calls have a 3-second best-effort bound and report only a fixed redacted diagnostic. |
| FOCUS-01–FOCUS-04 | fixed | D-Bus ownership uses `DO_NOT_QUEUE` and complete teardown; authoritative unknown-window identities are retained; strict schema/session/window/generation/lock checks fail closed; an isolated GNOME Shell 50.1 runtime smoke exercises load, conflict, disable and re-enable. |
| FOCUS-05 | fixed | Installer diagnostics query `gnome-extensions list --enabled`; an installed-but-disabled extension is never reported enabled. |
| FOCUS-06 | fixed | DEB and RPM recipes include the complete `gnome-extension/` payload used by their installed integration script. |
| REL-01–REL-11 | fixed | The obsolete streamed installer is not advertised; releases use a source tag plus a provenance-checked bootstrap tag and stable versioned asset; candidate validation follows mutations; dispatch cannot bypass the release gate; partial failures are state-aware and resumable; arbitrary commits, sibling bootstrap commits and tag-target mismatches are rejected; draft assets are authenticated and the exact canonical downloaded bytes are smoke-tested before publication, including cross-toolchain resume. |
| BENCH-01–BENCH-04 | fixed | The harness computes pooled corpus/subset WER, requires identical complete non-empty sets, uses repeated-run median latency, records engine availability without claiming execution, redacts engine errors, and bounds or hashes all report values including pathological numeric inputs. |
| AUDIT-01 | documented; D1 remains `still-open` | The historical ledger now records the residual DNS-rebinding/browser-render exposure explicitly. Implementing a fetch redesign or creating an external issue was not authorized by this scope. |
| AUDIT-02 | fixed as documentation | D6 is correctly classified as an accepted product trade-off rather than a complete streaming repair. |
| DOC-01–DOC-04 | fixed | GNOME claims distinguish reproducible 50.1 smoke from manual desktop coverage; focused commands match their evidence; the private corpus boundary is consistent; broken commit/test evidence was corrected. |

The last independent passes found no remaining relevant issue in the repaired
insertion/clipboard/notification paths, focus provider, release/bootstrap
workflow, or benchmark integrity/privacy paths. Real microphone, real desktop
focus-race, lock/suspend and real-audio benchmark gates remain explicitly
manual or pending; they are not converted into automated evidence here.

### 2026-08-10 — Final automated validation

The final landing-set matrix completed with 411 passed, 2 capability skips and
83 subtests. The standalone legacy runner completed 86 tests. The GNOME
integration suite passed its nested Shell 50.1 lifecycle test and skipped only
the intentionally disabled live-session provider check. The installer dry run,
release preflight, shell syntax, Python compilation, Ruff, JavaScript syntax,
JSON/YAML parsing, HTML parsing, audit-ledger row/link validation, whitespace
checks and exact conflict-marker scan all passed. The release preflight reported
zero errors; its dirty-worktree, broad keyword-scanner and virtual-environment
lock-file warnings are expected development-environment diagnostics.

### 2026-08-10 — D1 implementation closure

This amendment supersedes the earlier `AUDIT-01` statement that D1 remained
open. Direct HTTP(S) requests now bind the complete validated DNS answer set to
the numeric socket connection, preserve TLS hostname verification, and share a
bounded deadline across resolution, connection attempts, redirects and reads.
The public-unicast classifier uses a pinned current IANA special-purpose policy
instead of Python-version-dependent `ipaddress.is_global` data, including
explicit handling for mapped/compatible/translated IPv4, NAT64, 6to4, Teredo,
ISATAP, retired/site-local/documentation ranges and public IETF controls.

Render mode validates and fulfills every read-only browser request through that
same transport. It strips browser connection-hint/reporting headers, blocks
service workers, WebSockets, workers, beacons and state-changing methods, and
forces all remaining Chromium-owned TCP into an owned loopback rejecting proxy
with implicit localhost bypass disabled. A local installed-Chrome socket-trap
regression proves declarative preconnects to IPv4 loopback, IPv6 loopback and
`localhost` reach only the owned proxy; no external network is used. D1's final
resolution is therefore `fixed`, subject to the ordinary release test gate.
