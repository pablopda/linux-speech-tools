# Linux Voice Workspace Product Strategy

**Date:** August 10, 2026  
**Status:** Strategic recommendation; implementation claims must be verified against the current repository  
**Provenance:** Product and architectural assessment supplied after a broader 2026 landscape review  

**Living implementation PRD:**
[LINUX_VOICE_WORKSPACE_PRD_PLAN.html](LINUX_VOICE_WORKSPACE_PRD_PLAN.html)

> External product descriptions and links in this document are research inputs,
> not hands-on reliability evidence. Before they drive interoperability or
> competitive claims, verify them against the current upstream projects and a
> shared Ubuntu GNOME Wayland test matrix.

## Revised conclusion

The project should not become only a Vocalinux add-on. That would make the
product dependent on another installation and weaken the experience. Dictation
is the doorway through which users discover the higher-level features, so the
project should own a reliable, complete baseline.

It should also not try to reproduce every feature offered by Vocalinux,
OpenWhispr, Voxtype, Speech Note, AverVOX, and general agent platforms.

The direction is:

> **A complete Linux voice workspace with a bounded dictation baseline,
> differentiated by active-work context, safe actions, and integration with the
> agents the user already uses.**

A possible positioning line is:

> **Speak to the work in front of you. Dictate, ask, act, and listen without
> leaving the application you are using.**

The distinction is:

- **Yes to user-visible capability parity.**
- **No to implementation and roadmap parity.**
- **Own the experience and safety model.**
- **Delegate replaceable infrastructure to other projects and standards.**

## 1. The landscape is broader than Vocalinux

### Basic Linux dictation is becoming platform infrastructure

Vocalinux, Voxtype, and Canonical's Myna project are attacking the difficult
Linux fundamentals: local recognition, hotkeys, Wayland, text injection, model
management, and desktop packaging.

Canonical is targeting reliable local dictation as an Ubuntu 26.10 desktop
feature. Its initial scope is deliberately narrow: shortcut, speech, and text
inserted in the active application. Voice assistants, commands, desktop control,
and translation are outside its first release. Myna also separates recognition,
dictation management, UI, and text injection into modular components.

Voxtype competes aggressively on engines and performance: Cohere Transcribe,
Parakeet, Whisper, and other engines; GPU-specific distributions; meeting mode;
LLM and shell post-processing; and a wide compositor matrix.

The project's own IBus investigation shows the same pattern in Vocalinux: it
has had to build and harden a routing matrix across IBus, `ibus-wayland`,
`ydotool`, `wtype`, XWayland/`xdotool`, and clipboard fallback.

**Implication:** basic dictation remains required, but supporting another ASR
engine or typing on Wayland will not be a durable identity.

### The general Linux speech-workbench category is mature

Speech Note combines STT, TTS, machine translation, multiple recognition and
synthesis engines, model management, active-window insertion, global Wayland
shortcuts, D-Bus and CLI integration, document reading, and Flatpak
distribution.

**Implication:** TTS, read-aloud, and a model catalogue are useful capabilities,
but do not independently define a unique project.

### Broad voice-productivity suites exist

OpenWhispr combines cross-platform dictation, local Whisper and Parakeet,
translation, an AI-agent overlay, meeting transcription and diarization, a notes
database, cloud sync, API and MCP access, Linux packages, and Wayland-specific
integration.

Its documented agent tools primarily manage OpenWhispr notes, folders,
transcriptions, and usage. It is not documented as a general terminal, email,
or active-desktop action layer.

**Implication:** competing directly on notes, meetings, cloud sync, and
cross-platform distribution would turn this into a different product. There is
still room beyond a note-centric agent.

### Speech I/O plus generic LLM conversation is not enough

AverVOX already offers dictation into the focused application, selected-text
read-aloud, and a continuous STT to LLM to streaming-TTS conversation. It also
provides a bridge CLI and a warm Unix-socket daemon for external applications.

Its documented Linux insertion is narrower than the target-bound native GNOME
Wayland design investigated here, but its existence means that generic speech
I/O plus an LLM is not sufficient differentiation.

Amical also performs active-application-aware dictation and plans MCP-driven
application control, although its desktop app does not currently support Linux.

**Implication:** the opportunity is real but narrowing. The differential must be
stronger than using “context aware” as a marketing phrase.

### Agent orchestration is already a mature layer

Goose provides a Linux desktop app, CLI and API, multiple model providers, ACP
support, and many MCP extensions. Open Interpreter supports multiple harnesses,
MCP, ACP, skills, hooks, permissions, sandboxed execution, and computer use.

OpenClaw provides a gateway for sessions, tools, events, messaging channels, and
companion-device capabilities. Screenpipe captures application context,
accessibility data, screen changes, and audio history through MCP and a local
API.

**Implication:** do not build another general agent runtime, messaging gateway,
or always-on screen-history database. Integrate them.

## 2. Vertical parity, not horizontal parity

### Vertical parity: yes

The primary user should be able to install one product and complete the main
workflow:

1. Press a hotkey.
2. Dictate accurately.
3. See a clear recording state.
4. Insert text safely into the active application.
5. Fall back cleanly when insertion is unavailable.
6. Select or open content and have it read aloud.
7. Talk to an LLM or agent.
8. Receive a visible or spoken response.
9. Start automatically and configure the essentials.

This experience should be first-class on **Ubuntu GNOME on Wayland**, with
compatibility fallbacks elsewhere.

### Horizontal parity: no

Do not attempt to match every adjacent feature across every competitor:

- Every Linux desktop and compositor
- Seven or ten ASR engines
- macOS and Windows
- Meeting capture and speaker diarization
- A notes database
- Cloud sync and team sharing
- Translation
- Calendar integrations
- Native mobile applications
- A general agent harness
- A complete MCP ecosystem
- Computer-use automation
- Long-term screen history

The repository's historical audit already shows the risk: broad experimentation
created dead or divergent paths. The danger is not only feature count, but
ownership of too many unrelated lifecycles, stores, integrations, and security
models.

## 3. Product thesis

# Linux voice workspace for active applications and existing AI agents

The primary user is a Linux developer or technical knowledge worker who spends
the day in browsers, terminals, IDEs, documents, and AI agents, and wants speech
as a first-class input and output channel across them.

The product has four explicit modes.

### 1. Dictate

Turn speech into text in the current application. The baseline should be:

- Target-bound
- App-aware
- Project-aware
- Safe under focus changes
- Good with technical vocabulary
- Local by default
- Capable of clipboard fallback
- Provider-independent

The current prompt-dictation architecture already contains important pieces:
target context, insertion sessions, output policies, focus checking, live
previews, repository recognition hints, and fail-closed fallback behavior.

### 2. Ask

Ask a question about material currently in front of the user:

- Explain this traceback.
- Summarize the current page.
- Compare this implementation with the issue description.
- Explain what is missing in this email.
- Summarize a PDF section and read the summary.

Context should come from explicit, trustworthy providers:

- Selected text
- Current browser page DOM
- Active terminal output
- Current repository and branch
- Open file or IDE selection
- Explicitly attached files
- Optional recent context from Screenpipe

The primary context is the active workspace, not a proprietary notes database.

### 3. Act

Delegate work to the application or agent the user already uses:

- Ask Claude Code to fix a failing test.
- Ask Codex to inspect the current branch and propose a patch.
- Run the test suite and explain a failure.
- Draft an email without sending it.
- Prepare a calendar proposal.
- Open an issue containing a selected error and current commit.

Bridge to Claude Code, Codex, Open Interpreter, Goose, OpenClaw, and compatible
ACP, MCP, CLI, or OpenAI-style agents. Do not become the reasoning and
tool-running engine.

The product owns intent capture, context capture, destination selection, session
continuity, proposal visibility, approval, and text or speech delivery.

### 4. Read

Turn selected, visible, or attached content into useful audio:

- Read selected text
- Read the current page
- Read a URL, PDF, or document
- Summarize, then read
- Explain, then read
- Pause, resume, skip, and stop
- Interrupt spoken output to ask a question
- Continue from another device

Existing `say-read` and local/remote TTS paths provide a real foundation.

## 4. Defensible core

### A. Context broker

Normalize the answer to:

> What is the user working on now, and what context did they explicitly permit
> this session to use?

```python
ContextSnapshot(
    target_app=...,
    window_identity=...,
    selection=...,
    browser_page=...,
    terminal_session=...,
    repository=...,
    attached_files=...,
    permissions=...,
)
```

Context must be explicit, bounded, and inspectable rather than based on silent
continuous capture.

### B. Target-bound voice session

The existing insertion-session foundation is:

```python
class InsertionSession:
    def update(self, text: str, revision: int) -> Result: ...
    def finalize(self, text: str, revision: int) -> Result: ...
    def submit(self) -> Result: ...
    def cancel(self) -> Result: ...
    def close(self) -> None: ...
```

It should evolve into a broader `VoiceSession` that owns:

- Mode: dictate, ask, act, or read
- Original target
- Context permissions
- Partial and final transcript revisions
- Agent destination
- Delivery result
- Approval state
- Spoken-output state
- Device handoff

### C. Permission and approval broker

Distinguish:

- **Read:** retrieve context
- **Draft:** create content without applying it
- **Insert:** place text into a verified target
- **Execute:** run a command or tool
- **Send:** email, message, or publish
- **Delete/change:** modify existing state

A spoken “yes” must not automatically approve every consequential operation.
The interface should show exactly what will happen, where, and under which
agent identity.

### D. Agent-session continuity

Attach to the work session already in use:

- Existing Claude Code directory or session
- Current Codex project
- Existing Goose task
- Existing Open Interpreter conversation
- OpenClaw workspace
- Browser-based model sessions where an adapter exists

### E. Bidirectional speech designed for work

Add work-specific behavior to the speech loop:

- Interrupt spoken responses
- Keep working silently and notify on completion
- Read summaries rather than raw logs
- Switch between text-only and spoken output
- Route code and commands visually and explanations audibly
- Continue listening without losing the agent session
- Preserve the distinction between dictation and conversation

## 5. Own, integrate, and defer

| Capability | Decision | Reason |
| --- | --- | --- |
| Local dictation | **Own** | Required for standalone and offline use |
| Ubuntu GNOME Wayland insertion | **Own or use a stable Myna adapter** | Core quality and safety boundary |
| Clipboard and overlay fallbacks | **Own** | Required for fail-safe behavior |
| Selected-text and document TTS | **Own** | Core bidirectional speech loop |
| Local faster-whisper | **Own as the default fallback** | Existing self-contained path |
| Parakeet | **Retain as optional** | Benchmark Spanish before promotion |
| Remote/OpenAI-compatible ASR | **Add** | Shared servers, phone, replaceable engines |
| Generic agent reasoning | **Integrate** | Mature external runtimes already exist |
| Terminal, email, and calendar tools | **Integrate through agents/MCP** | Avoid duplicate clients and authentication stacks |
| Active-page context | **Own a browser integration** | Central differentiator |
| Cross-app historical context | **Optional Screenpipe adapter** | Avoid building another recorder/history store |
| Phone access | **PWA/local web client or OpenClaw first** | Validate before native mobile projects |
| Full notes system | **Defer/integrate/export Markdown** | Mature products already serve this use |
| Meetings and diarization | **Defer or plugin** | Separate storage/audio/calendar product |
| Cloud sync and team sharing | **Defer** | Different operational scope |
| macOS and Windows | **Defer** | Linux integration is the wedge |
| Large ASR/TTS catalogue | **Do not chase** | Not a sustainable differentiator |

## 6. Revised architecture

```text
┌────────────────────────────────────────────────────┐
│ Clients                                            │
│ GNOME overlay · CLI · tray/settings · browser      │
│ optional phone/PWA · agent plugins                 │
└────────────────────────┬───────────────────────────┘
                         │
┌────────────────────────▼───────────────────────────┐
│ Voice Session Router                               │
│ Dictate · Ask · Act · Read                         │
│ session identity · revisions · interruption        │
└───────────────┬─────────────────┬──────────────────┘
                │                 │
┌───────────────▼──────────┐ ┌────▼──────────────────┐
│ Context Broker           │ │ Permission Broker     │
│ target · selection       │ │ insert · execute      │
│ page · terminal · repo   │ │ send · modify         │
│ explicit history         │ │ approve · deny        │
└───────────────┬──────────┘ └────┬──────────────────┘
                │                 │
┌───────────────▼─────────────────▼──────────────────┐
│ Agent Bridge                                       │
│ Claude Code · Codex · Goose · Open Interpreter     │
│ OpenClaw · ACP · MCP · CLI · OpenAI-compatible     │
└───────────────┬────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────┐
│ Speech Runtime                                     │
│ STT · TTS · VAD · model lifecycle · streaming      │
│ local and remote providers                         │
└───────────────┬────────────────────────────────────┘
                │
┌───────────────▼────────────────────────────────────┐
│ Delivery Backends                                  │
│ IBus/Myna · ydotool · xdotool · clipboard          │
│ overlay · spoken output                            │
└────────────────────────────────────────────────────┘
```

The product owns sessions, intent, context, safety, routing, and user
experience. Providers own inference, reasoning, agent tools, messaging,
historical capture, and optional persistent memory.

## 7. Fork and integration choices

### OpenWhispr

Do not use OpenWhispr as the foundation unless the desired product changes into
a cross-platform consumer suite centered on meetings, notes, accounts, and
cloud sync. Its architecture would bring substantial unrelated scope while not
providing the differentiated target safety and external-agent session layer.

### Vocalinux

Use Vocalinux as a reference and potential interoperable backend for generic
Linux insertion. Its AGPL license and dictation-centered architecture make it a
poor direct foundation for an MIT project unless that licensing direction is a
deliberate choice. Prefer protocol-level interoperability and independently
implemented patterns.

### Myna

Engage upstream early while retaining the project's fallback. Investigate
whether Myna will expose transcript events, start/stop control, text injection,
target metadata, provider interfaces, and a desktop integration protocol.

### Goose, Open Interpreter, and OpenClaw

Integrate them at the agent layer. Goose is suitable for provider-agnostic
agent work, Open Interpreter for alternate model harnesses, Claude Code and
Codex for development, and OpenClaw for persistent sessions and remote
channels.

## 8. IBus decision

Move explicit, final-only GNOME Wayland insertion earlier than live preedit or
automatic routing:

1. Keep the backend-neutral `InsertionSession`.
2. Keep the GNOME stable-focus provider.
3. Keep IBus Stage 0 diagnostics.
4. Investigate a final-only IBus backend unless Myna provides a usable path.
5. Validate Ubuntu GNOME first.

Still defer live IBus preedit, broad KDE/compositor support, automatic IBus
preference, replacement of every fallback, and claims of general Linux parity.

The acceptance bar remains exact repeated commits, zero wrong-window
insertions, Unicode coverage, input-source restoration, lifecycle recovery, and
explicit handling of ambiguous results.

> Current project evidence: the attempted Stage 0 component-registration probe
> changed the active input engine on the test desktop. Registration was removed
> from Stage 0 and remains a failed safety gate. Connection-only diagnostics do
> not prove that final IBus insertion is safe.

## 9. Focused roadmap

### Foundation: one coherent product

- Consolidate entry points behind one CLI and one daemon.
- Keep temporary compatibility aliases.
- Define Dictate, Ask, Act, and Read.
- Add one status model, overlay, and diagnostics command.
- Finish the LATAM Spanish benchmark.
- Preserve the historical audit as a resolution ledger.

### Baseline parity on Ubuntu GNOME

- Reliable hotkey
- Local faster-whisper
- Optional Parakeet
- Remote ASR provider
- Target-bound final insertion
- Clipboard fallback
- Selected-text TTS
- URL, PDF, and document read-aloud
- Tray, settings, and autostart
- Installation and uninstall
- Clear microphone and privacy state

This is the parity floor, not the differentiating roadmap.

### Active-work context

Implement focused providers in this order:

1. Current repository, branch, and file vocabulary.
2. Active terminal and coding-agent session.
3. Browser selection and page DOM through a browser extension.
4. Generic selected text through the desktop accessibility layer.
5. Explicitly attached documents.
6. Optional Screenpipe context, disabled by default.

### Agent bridges

Start with adapters:

1. Claude Code
2. Codex
3. Open Interpreter
4. Goose
5. OpenClaw

```python
class AgentAdapter:
    def discover_sessions(self) -> list[AgentSession]: ...
    def send(self, request: AgentRequest) -> AgentRun: ...
    def stream(self, run_id: str) -> Iterator[AgentEvent]: ...
    def cancel(self, run_id: str) -> None: ...
```

### Safe actions

- Separate Ask from Act.
- Show destination agent and context.
- Show proposed commands or operations.
- Require explicit approval for writes.
- Never submit after ambiguous insertion.
- Never send messages or email without visible confirmation by default.
- Preserve a local action audit without storing microphone audio.

### Phone and remote access

Start with an authenticated local PWA/WebSocket, Tailscale, OpenClaw, or a
messaging adapter. Initially support requests, agent-task status, approvals,
concise results, and continuation of a desktop session. Defer native mobile
applications.

## 10. Guardrails

Every feature must materially improve:

> **speak → capture the right context → choose intent → deliver or act safely →
> hear or see the result**

If it does not, integrate or defer it.

1. One first-class platform: Ubuntu GNOME Wayland.
2. One primary user: Linux technical knowledge workers.
3. Four modes only: Dictate, Ask, Act, Read.
4. Two first-party local ASR engines maximum.
5. One generic remote ASR contract.
6. No new notes database.
7. No internal general-purpose agent runtime.
8. No native mobile application initially.
9. No meeting product until the core loop is excellent.
10. Zero tolerance for wrong-target insertion or silent consequential actions.

## Final recommendation

Continue developing the project. Do not reduce it to a thin Vocalinux add-on,
and do not turn it into an OpenWhispr clone.

Build a complete standalone baseline:

- Excellent Ubuntu dictation
- Native and safe insertion
- TTS and read-aloud
- Simple voice conversation
- Good installation and desktop UX

Concentrate differentiated work on:

- Active-work context
- Existing agent sessions
- Target-bound delivery
- Action approvals
- Spoken results
- Desktop-to-phone continuity

The category should evolve from “Linux speech tools” to:

> **The voice workspace for Linux applications and AI agents.**

The next artifact should be a product strategy and PRD organized around the
four modes, with each capability marked **own**, **integrate**, or **defer**.
It should be followed by a hands-on Ubuntu comparison of Vocalinux,
OpenWhispr, Voxtype, Speech Note, and AverVOX using the same Spanish, Wayland,
TTS, and agent-workflow test matrix.

## Current microphone baseline versus a full Voice experience

The current project already owns the fundamental microphone pipeline:

- Global hotkey start/stop behavior
- `ffmpeg` microphone capture
- PulseAudio/PipeWire-first input with ALSA fallback
- WebRTC VAD speech segmentation
- Faster-whisper and optional Parakeet transcription
- Listening, recording, processing, finalizing, idle, and error status
- GNOME notifications and bounded recorder-process cleanup
- Configurable input device and audio backend

This is a capable dictation microphone path. It is not yet equivalent to a
polished full-duplex Voice product. Missing product-level pieces include:

- One persistent daemon and unified voice session
- A single visible microphone control shared by all four modes
- Streaming agent responses with spoken interruption
- Conversation turn management distinct from dictation
- Automatic routing between Dictate, Ask, Act, and Read
- A unified overlay/tray/settings surface
- Proven real-desktop microphone and latency parity against competitors

If “Voice” means Vocalinux or Voxtype, the project has a comparable structural
capture baseline but has not yet completed the same-machine hands-on parity
matrix. If it means ChatGPT-style Voice, the project does not yet provide the
same continuous, interruptible, full-duplex conversation experience.

## Research references

1. [Introducing Myna: Speech-to-text for Ubuntu Desktop](https://discourse.ubuntu.com/t/introducing-myna-speech-to-text-for-ubuntu-desktop/84251)
2. [Voxtype](https://github.com/peteonrails/voxtype)
3. [Speech Note](https://github.com/mkiol/dsnote)
4. [OpenWhispr](https://github.com/openwhispr/openwhispr)
5. [OpenWhispr Agent Mode](https://docs.openwhispr.com/guides/agent-mode)
6. [AverVOX OSS](https://github.com/avrvx/AverVOX-OSS)
7. [Amical](https://github.com/amicalhq/amical)
8. [Goose](https://github.com/block/goose)
9. [OpenClaw](https://github.com/openclaw/openclaw)
10. [Screenpipe](https://github.com/screenpipe/screenpipe)
