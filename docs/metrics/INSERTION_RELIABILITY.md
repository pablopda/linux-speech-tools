# Local insertion reliability metrics

`lst-insertion-metrics` maintains a private, local aggregate for deciding
whether GNOME/Wayland insertion needs a future IBus backend. Collection is
enabled by default and can be disabled or reset at any time:

```bash
lst-insertion-metrics status
lst-insertion-metrics disable
lst-insertion-metrics enable
lst-insertion-metrics reset --yes
```

For a one-process opt-out, set `LST_INSERTION_METRICS=off`. The persistent
state is stored at
`${XDG_STATE_HOME:-$HOME/.local/state}/linux-speech-tools/insertion-metrics-v1.json`
with file/lock mode `0600`; a newly created final state directory uses `0700`.
`XDG_STATE_HOME` is used only when it is absolute. An explicit
`LST_INSERTION_METRICS_FILE` override must also be absolute. Existing override
parent-directory permissions are never changed. The state parent must be owned
by the current user and must not be group- or world-writable; unsafe existing
parents are rejected instead of being chmodded.

Writes are protected by an exclusive lock, use atomic replacement, and fsync
the file and containing directory. State, lock, and disable-marker paths never
follow final-component symlinks, while report commands reject symlinks in
every existing component of both the output and reserved state paths. These
paths also reject FIFOs, devices, hardlinks, non-owned files, and non-regular
files. Reads are bounded before decoding. Invalid, inconsistent, undecodable,
or oversized state fails
privacy-closed: it is replaced by an empty **disabled** schema and collection
does not resume until an explicit `lst-insertion-metrics enable`.
Report output parents have the same ownership and nonwritable-directory
requirements. A report also refuses to overwrite the selected state, lock, or
disable-marker path, including lexically or inode-equivalent aliases. These
checks run before the aggregate is rendered.

Persistent disable is also recorded in a separate private
`insertion-metrics-v1.json.disabled` marker. Therefore damage to the aggregate
JSON cannot silently erase the user's opt-out. The environment opt-out remains
independent and takes precedence over an enabled persistent state.

## Privacy boundary

The closed, bounded schema accepts only fixed attempted-backend and final
delivery-backend categories, fixed result categories, coarse target kinds,
focus-drift counts, clipboard-fallback counts, and Unicode or
Spanish-punctuation failure counts. A bounded target-kind × result bucket
attributes fallback or failure to only `claude`, `codex`, `ide`, `terminal`,
`browser`, `generic`, or `unknown`. It does not accept or retain transcript
text, diagnostics, titles, application identifiers, repository paths, window
tokens, focus identities, or insertion session identifiers. Unknown backend
and target values become `other` and `unknown`; they are never copied into
state or reports.

The attempted backend describes what the session tried (for example,
`synthetic-paste`); the final delivery backend describes the backend on the
structured final result (for example, `clipboard` after a fail-closed
fallback). Keeping them separate prevents a fallback from hiding which direct
path was attempted.

Unicode correctness cannot be inferred from a transport success result. Record
a locally observed failure without recording the affected text:

```bash
lst-insertion-metrics mark-failure unicode
lst-insertion-metrics mark-failure spanish-punctuation
```

Manual observations increment only the fixed failure-type counters. They do
not enter the completed-session backend or target breakdowns, so those
marginals continue to sum to completed sessions. The older `--backend` and
`--target-kind` flags remain accepted for command compatibility but their
values are deliberately not retained.

The schema validates that attempted backend, final backend, result, and coarse
target marginals each sum to the completed-session count. State written by an
older build that mixed manual observations into session marginals is therefore
treated as inconsistent and fails privacy-closed; explicitly enable collection
after reviewing the reset aggregate. Eligible result counts must also be
subsets of their corresponding overall results, and every eligible
target-by-result marginal must be a subset of that overall coarse target.

## Dated decision report

Render a Markdown or JSON report, or atomically write a private report file:

```bash
lst-insertion-metrics report
lst-insertion-metrics report --json
lst-insertion-metrics report --output insertion-report-YYYY-MM-DD.md
```

The report shows all completed sessions, but the IBus readiness gate uses only
**eligible supported-GNOME direct-insertion attempts**. An eligible attempt is
a `synthetic-paste` or `synthetic-live-type` session started with a complete,
unlocked GNOME stable-focus snapshot. Explicit clipboard, overlay, stdout, and
sessions without that authoritative GNOME identity do not count toward its
sample size or rates. Only the eligibility boolean is persisted; none of the
focus identity is retained.

The gate remains `PENDING` until it observes at least 100 eligible attempts
across a span of at least 14 elapsed days. It then evaluates clipboard fallback
above 10%, or failed/ambiguous dispatch above 1%, within that same cohort. The
report includes the cohort's bounded coarse target × outcome breakdown so a
problem can be attributed to a supported target class without recording an app
or window identifier. Noneligible sessions neither dilute nor trigger the IBus
decision.

The schema intentionally does not collect weekly manual-recovery events, so
that trigger requires separate privacy-safe manual assessment. Wrong-window
insertion is a P0 safety defect and must be reported separately rather than
inferred from aggregate success counters. Reports identify affected contexts
only through coarse target kinds, never application names.

This tooling does not claim that the two-week/100-session gate has been met;
the dated report derives readiness only from locally collected counters.
