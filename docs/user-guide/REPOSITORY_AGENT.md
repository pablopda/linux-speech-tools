# Repository-Aware Assistant

`lst-agent` accepts one developer question, runs one ephemeral read-only Codex
CLI session in the selected Git repository, and returns the bounded final
answer. It never enables Codex workspace writes and never sends Enter.

The request and the repository context inspected by Codex are sent to the
user's authenticated Codex service. Nothing runs in the background: every call
requires an explicit command.

## Check capabilities

This check does not contact Codex:

```bash
lst-agent --check --repo /path/to/repository
```

## Ask with text

Requests travel on standard input so they are not exposed in the process list:

```bash
printf '%s\n' 'Explain the release bootstrap design.' |
  lst-agent --repo /path/to/repository
```

With an interactive terminal, run `lst-agent` without piped input and enter the
request at its prompt. A private UTF-8 file can also be selected explicitly:

```bash
lst-agent --repo /path/to/repository --request-file /private/request.txt
```

Request files must be regular files and are opened without following symbolic
links. Named pipes, devices, oversized files, invalid UTF-8, and files that grow
beyond the configured bound are rejected.
Interactive terminal input is also read with the configured character bound
plus one detection character; oversized input is rejected before contacting
Codex.

## Ask by voice

```bash
lst-agent --dictate --repo /path/to/repository --language es
```

Stop capture with Ctrl+C. Only finalized utterances are sent to the agent;
partial transcripts are not. Finalized utterances are retained only while their
combined text, including separators, stays within the configured request bound.
Crossing that bound stops collection and discards the request instead of
sending a truncated prompt.

## Deliver or speak the response

The default output is `stdout`. Other output paths use the same target-bound
`InsertionSession` as prompt dictation:

```bash
lst-agent --output clipboard
lst-agent --output paste
lst-agent --output live-type
lst-agent --speak
```

The original target is captured before dictation and before the agent call. The
assistant never automatically submits the answer. Because model output is
untrusted, `paste` and `live-type` use a conservative positive allowlist: only a
dedicated GNOME Text Editor window from the authoritative GNOME focus provider
may receive direct insertion. The captured snapshot must use schema v1, source
exactly `gnome-focus`, kind exactly `unknown`, a complete unlocked session/window
identity and typed focus generation. At least one application ID/window class
must be present, and every nonempty identity must exactly name GNOME Text Editor.
A freshly queried authoritative snapshot must repeat the complete identity,
generation and allowlisted application fields immediately before placement.
Conflicting, restarted, locked, injected, profile-derived, X11 or incomplete
snapshots fail closed even when one field names GNOME Text Editor.
Terminals and agent prompts, IDEs (whose integrated terminals cannot be
distinguished safely at window level), browsers, generic targets, unrecognized
or missing identities, and targets without stable focus evidence all degrade to
clipboard-only delivery. Window titles are never used for this authorization.
The insertion session verifies focus again immediately around placement; focus
drift fails closed. Control characters and invisible Unicode formatting are
removed before any output.

`--speak` pipes the answer to `say-read -`; it does not put the answer in a
command argument. If speech playback fails after text delivery, the command
reports a partial failure without retrying text placement.

## Permission and failure contract

- Codex runs with `--ask-for-approval never`, `--ephemeral`,
  `--sandbox read-only`, `--ignore-user-config`, and `--ignore-rules`.
- The wrapper does not parse or store credentials, and ephemeral mode does not
  persist local Codex session rollout files.
- Requests and retained answers are bounded in memory and excluded from status
  files. Excess Codex stdout is drained without being retained or spooled to a
  temporary file.
- Agent progress and raw errors are not echoed because they may contain paths or
  excerpts from the repository.
- Timeout and Ctrl+C termination target the owned Codex or TTS process group.
- This first slice cannot edit files, approve terminal mutations, send external
  messages, or resume a previous agent session.

Environment controls:

| Variable | Meaning | Default / bound |
| --- | --- | --- |
| `LST_AGENT_REPO` | Selected Git repository | current directory |
| `LST_AGENT_OUTPUT` | Response output | `stdout` |
| `LST_AGENT_PROFILE` | Target classification | `auto` |
| `LST_AGENT_MODEL` | Optional Codex model override | Codex CLI default |
| `LST_AGENT_TIMEOUT_SECONDS` | Agent timeout | 180 seconds, max 900 |
| `LST_AGENT_SPEAK_TIMEOUT_SECONDS` | TTS timeout | 300 seconds, max 900 |
| `LST_AGENT_MAX_REQUEST_CHARS` | Request bound | 32,000, max 128,000 |
| `LST_AGENT_MAX_RESPONSE_CHARS` | Answer bound | 16,000, max 128,000 |

The first real acceptance run should ask a read-only repository question, save
`git status --short` before and after, and confirm the output arrived only at the
selected destination.
