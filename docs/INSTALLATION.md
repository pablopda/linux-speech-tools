# Installation Guide

Linux Speech Tools uses a hybrid install model:

- `uv` installs Python dependencies into the project environment.
- `installer.sh` installs/checks system tools, writes runtime config, installs shell launchers, and optionally configures desktop permissions.
- `linux-speech-tools-setup` installs or verifies model files that Python packaging cannot provide.

This keeps model downloads and system changes explicit while letting command launchers use one stable project environment.

## Install Methods

Checkout install:

```bash
git clone https://github.com/pablopda/linux-speech-tools.git
cd linux-speech-tools
./installer.sh --with-kokoro --with-stt --download-models
```

There is no supported streamed command for v1.0.2: its tagged installer predates
the current profile-based bootstrap. A future release may advertise a command
only after its `bootstrap-vX.Y.Z` tag has passed the end-to-end bootstrap smoke
test. Never stream `installer.sh` from the mutable `main` branch.

The versioned bootstrap downloads a versioned release asset to
`~/.local/share/linux-speech-tools/source`, verifies its pinned SHA256, and then
runs the full uv installer. Custom refs or asset URLs must also set
`LST_INSTALLER_SHA256`.

Native `.deb` and `.rpm` packages are beta. They install launchers plus a
read-only source tree under `/usr/share/linux-speech-tools`. Complete their
user-local dependency setup as your normal user:

When an approved v1.1-or-later native-package release is published, its release
downloads provide both formats together in the versioned
`linux-speech-tools-X.Y.Z-native-packages.tar.gz` bundle. Verify the included
`SHA256SUMS`, then install only the package for your distribution.

```bash
/usr/share/linux-speech-tools/installer.sh --with-kokoro --with-stt --no-system-deps
```

That command bootstraps the pinned uv release when necessary and creates the uv
environment under
`${XDG_DATA_HOME:-$HOME/.local/share}/linux-speech-tools/uv-runtime`, never under
the root-owned package tree. The checkout installer remains the primary
supported first-run path.

## Quick Profiles

Core Edge TTS only:

```bash
./installer.sh
```

Offline Kokoro read-aloud with model download:

```bash
./installer.sh --with-kokoro --download-models
```

faster-whisper dictation with model prefetch:

```bash
./installer.sh --with-stt --download-models --whisper-model tiny
```

Everything except development tools:

```bash
./installer.sh --all --download-models
```

Direct typing mode needs explicit system permission setup:

```bash
./installer.sh --with-stt --setup-uinput
```

This also installs direct-typing tools where available (`ydotool` for Wayland,
`xdotool` for X11). Log out and back in after `--setup-uinput`; Linux group
membership does not update existing sessions.

## What uv Installs

`uv` installs Python libraries from `pyproject.toml`:

- Core: `edge-tts`, `requests`, `beautifulsoup4`, `soundfile`, `numpy`
- `--with-kokoro`: `kokoro-onnx`, `onnxruntime`, `torch`, document extraction helpers
- `--with-stt`: `faster-whisper`, `webrtcvad`
- `--dev`: test and lint tooling

`uv` does not install system packages, model weights, GNOME keybindings, or
`/dev/uinput` permissions. The GNOME profile installs the system Python
bindings (`python3-dbus`/`python3-gi` or distro equivalents) because
`say-read-gnome` runs against the desktop session's Python D-Bus stack.

## Model Assets

Kokoro read-aloud needs two files:

- `~/models/kokoro/kokoro-v1.0.onnx`
- `~/models/kokoro/voices-v1.0.bin`

Install or check them with:

```bash
linux-speech-tools-setup --kokoro
linux-speech-tools-setup --check --kokoro
```

Override locations when needed:

```bash
KOKORO_MODEL=/path/kokoro-v1.0.onnx \
KOKORO_VOICES=/path/voices-v1.0.bin \
say-read article.txt
```

faster-whisper downloads models into the Hugging Face cache on first use. Prefetch a model explicitly with:

```bash
linux-speech-tools-setup --stt --whisper-model base
```

## Runtime Configuration

The installer writes:

```text
~/.config/linux-speech-tools/install.env
```

Installed launchers source this file to find the checkout through
`LST_PROJECT_ROOT` and to reuse STT defaults such as `WHISPER_MODEL`. This is
what lets copied commands in `~/.local/bin` run the Python modules under `src/`.
The STT profile installs both pause-triggered dictation (`talk2claude-faster`,
`talk2claude-faster-toggle`) and live developer prompt dictation (`lst-dictate`,
`dictate-prompt`). The general launcher set also includes the read-only
repository assistant (`lst-agent`), non-inserting IBus diagnostics
(`lst-ibus-check`), private insertion metrics (`lst-insertion-metrics`), the
private LATAM corpus workflow (`lst-asr-corpus`), and the non-typing GNOME live
acceptance recorder (`lst-gnome-acceptance`).

If you move the checkout, rerun:

```bash
./installer.sh
```

or update `LST_PROJECT_ROOT` in the config file.

Dictation audio defaults to `STT_AUDIO_BACKEND=auto`, which tries PulseAudio
compatible capture first. That covers common PipeWire desktop sessions and then
falls back to ALSA. Override when needed:

```bash
STT_AUDIO_BACKEND=alsa STT_AUDIO_DEVICE=hw:1,0 talk2claude-faster
STT_AUDIO_BACKEND=pulse STT_AUDIO_DEVICE=default talk2claude-faster
```

Developer prompt dictation can also be tuned from the environment:

```bash
PROMPT_DICTATION_PROFILE=codex PROMPT_DICTATION_OUTPUT=overlay lst-dictate
PROMPT_DICTATION_PROFILE=claude PROMPT_DICTATION_OUTPUT=live-type lst-dictate
```

Repository assistant defaults use the same literal, allowlisted configuration
loader:

```bash
LST_AGENT_REPO=/path/to/repository
LST_AGENT_OUTPUT=stdout
LST_AGENT_TIMEOUT_SECONDS=180
```

See [Repository-Aware Assistant](user-guide/REPOSITORY_AGENT.md),
[IBus Stage 0 Diagnostics](developer/IBUS_STAGE0_DIAGNOSTICS.md), and
[Insertion Reliability Metrics](metrics/INSERTION_RELIABILITY.md) for their
permission, privacy, and evidence boundaries.

## Manual uv Commands

For development or checkout-local usage:

```bash
uv sync --locked --extra kokoro --extra read --extra stt
uv run --extra kokoro --extra read python src/tts/say_read.py --help
uv run --extra stt python -m src.stt.faster_whisper_auto --check
uv run --extra kokoro --extra stt python -m src.utils.setup_models --check
```

## System Tools

The installer checks or installs common system tools:

- TTS/playback: `ffmpeg`, `ffplay` or another player, `espeak-ng`
- Dictation: microphone support through `ffmpeg`, plus `wl-copy` or `xclip` for clipboard mode
- GNOME: `notify-send`, `gsettings`, D-Bus tools, `python3-dbus`, `python3-gi`, and desktop session support
- Direct typing: `ydotool` or `xdotool`, plus `/dev/uinput` permissions via `--setup-uinput`

Avoid direct typing setup unless you want system-wide keystroke injection available to your user account.
