# IBus Stage 0 diagnostics

The Stage 0 diagnostic checks whether the operating-system Python, PyGObject,
IBus typelib, graphical session D-Bus, and IBus session bus are available. It
does **not** provide an input engine, insert text, select an input source, start
`ibus-daemon`, or change any dictation default.

## Run the check

From a source checkout:

```bash
./bin/lst-ibus-check
./bin/lst-ibus-check --json
```

The controller intentionally starts the distro Python (normally
`/usr/bin/python3`) in isolated mode. This avoids mistaking a project virtual
environment without distro GI bindings for an unsupported desktop. Override it
only when diagnosing a nonstandard installation:

```bash
./bin/lst-ibus-check --system-python /path/to/system/python3
```

An installed launcher loads the adjacent `linux-speech-tools-env` helper, so
the checkout recorded by the uv installer remains authoritative after the
launcher is copied into `~/.local/bin`.

The entire child startup, probe, and shutdown is bounded to five seconds by
default. `--timeout` accepts values from 0.1 through 30 seconds. A timeout is a
failed check, never a reason to report IBus as ready. The controller drains
stdout and stderr concurrently under that same deadline and permits at most 64
KiB combined output. It retains only the bounded structured stdout; raw stderr
is discarded. A timeout or output-limit failure terminates the isolated child
process group and cannot be reported as ready.

## Transient registration was rejected

Stage 0 is connection-only. It does not expose a registration option and its
system-Python child contains no component-registration code.

An earlier experiment registered an empty transient component. A repeated live
acceptance check captured `xkb:us::eng` before that probe and `vocalinux`
immediately afterward even though the child called no global-engine setter. A
one-second no-probe control and the connection-only diagnostic both preserved
`xkb:us::eng`. The exact prior engine was restored after the failed check.

The mechanism by which IBus or another session component reacted is not needed
to make the safety decision: a Stage 0 diagnostic cannot claim that it leaves
the selected input source unchanged if its lifecycle experiment can coincide
with that transition. Component registration is therefore rejected rather
than hidden behind an “experimental” flag or repaired by manipulating the
global engine during cleanup.

## Reading failures

The command exits zero only if all requested checks succeed and child shutdown
completes. Its stable failure stages distinguish:

- missing system Python, PyGObject, or IBus typelib;
- unavailable graphical-session D-Bus or IBus bus;
- child startup, malformed-result, or bounded-shutdown failure.

Diagnostic output reports exception classes and safe remediation, not raw
dependency errors or application text. On Debian and Ubuntu, missing bindings
are commonly supplied by `python3-gi` and `gir1.2-ibus-1.0`. Install packages
through the distribution's normal administration process; this diagnostic does
not install or start anything.

The parent accepts exactly one versioned result object. Field names and types,
Python-version characters and length, failure stages, and exception-class
names are fixed or allowlisted. Capability ordering, failure state, timeout,
and shutdown must also be internally consistent.
Malformed or contradictory child output becomes a redacted `probe-result`
failure rather than being echoed or treated as ready.

## Lifecycle and security boundary

Stage 0 has no daemon or long-running local service. The controller and its
short-lived system-Python child communicate through private parent/child pipes,
so there is no socket for another local process to connect to and no protocol
authentication surface yet.

If a later stage introduces a service, it must use an independently designed,
length-framed protocol with strict message limits, a fresh per-run secret,
same-UID peer verification, request identifiers, bounded deadlines, and no raw
transcript logging. Stage 0 deliberately defers that surface until a service is
actually necessary.

## Real-session acceptance

Most automated tests replace the child runner and never contact a live session
bus. Before relying on Stage 0 connection evidence, run only the normal command
in a real GNOME Wayland login and verify:

```bash
before_engine="$(ibus engine)"
./bin/lst-ibus-check --json
after_engine="$(ibus engine)"
test "$before_engine" = "$after_engine"
```

The result must report both session buses reachable and clean shutdown, with no
input source, preedit, committed text, or synthetic key event. Do not repeat
the removed registration experiment as a normal Stage 0 validation command.

This is a diagnostic foundation only. IBus routing, final-text commit, preedit,
fallback, installation, and enablement remain outside Stage 0.
