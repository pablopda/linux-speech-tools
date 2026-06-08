# Packaging Guide for Distribution Maintainers

## Overview

This document is a beta distribution-maintainer design note, not the primary
supported install path. The supported user install is currently the checkout
installer documented in [INSTALLATION.md](INSTALLATION.md):

```bash
./installer.sh --with-stt --download-models
```

Native packages should not run `pip install` from post-install scripts until the
launchers are converted to a distro-safe package layout.

`talk2claude-faster` is a low-latency speech-to-text tool that works in two modes:
- **Clipboard mode** (default) - No special permissions needed
- **Typing mode** (optional) - Requires uinput group access

## Package Structure

### Files to Install

```
/usr/bin/talk2claude-faster                    # Main executable
/usr/lib/talk2claude-faster/                   # Python modules
/usr/share/talk2claude-faster/                 # Scripts and docs
/usr/share/doc/talk2claude-faster/             # Documentation
/etc/udev/rules.d/90-talk2claude-uinput.rules  # udev rules
```

### Dependencies

**Required:**
```
python3 (>= 3.8)
python3-pip
ffmpeg
```

**Recommended (for clipboard mode):**
```
wl-clipboard (Wayland) | xclip (X11)
```

**Optional (for typing mode):**
```
ydotool (Wayland) | xdotool (X11)
```

## Installation Steps

### 1. Python Package Installation

```bash
# In your package build
python3 -m venv /usr/lib/talk2claude-faster/venv
/usr/lib/talk2claude-faster/venv/bin/pip install \
    faster-whisper webrtcvad numpy
```

### 2. Install udev Rules

Create `/etc/udev/rules.d/90-talk2claude-uinput.rules`:
```udev
# Allow members of uinput group to access /dev/uinput
# for talk2claude-faster typing mode
KERNEL=="uinput", GROUP="uinput", MODE="0660"
```

### 3. Create uinput Group

```bash
# In package post-install script
getent group uinput >/dev/null || groupadd -r uinput
```

### 4. Reload udev

```bash
# In package post-install script
udevadm control --reload-rules
udevadm trigger --subsystem-match=misc --attr-match=name=uinput
```

### 5. Load uinput Module

```bash
# In package post-install script
modprobe uinput || true
```

## Post-Install Message

Display this message after installation:

```
talk2claude-faster has been installed!

CLIPBOARD MODE (Current):
  - Works immediately, no setup needed
  - Text copies to clipboard automatically
  - Paste with Ctrl+V

TYPING MODE (Optional):
  For direct typing into applications:

  1. Add yourself to uinput group:
     sudo usermod -aG uinput $USER

  2. Logout and login

  3. Verify:
     groups | grep uinput
     ls -la /dev/uinput

  4. Run the tool with --typing or DICTATION_MODE=typing

Documentation: /usr/share/doc/talk2claude-faster/
```

## Example Package Specs

### Debian/Ubuntu (.deb)

```debian
Package: talk2claude-faster
Version: 1.0.0
Section: sound
Priority: optional
Architecture: all
Depends: python3 (>= 3.8), python3-pip, ffmpeg
Recommends: wl-clipboard | xclip
Suggests: ydotool | xdotool
Description: Low-latency speech-to-text with faster-whisper
 Provides pause-triggered speech transcription using faster-whisper.
 Works in clipboard mode by default (no special permissions).
 Optional typing mode available with one-time setup.
```

**postinst script:**
```bash
#!/bin/bash
set -e

# Create uinput group
getent group uinput >/dev/null || groupadd -r uinput

# Reload udev rules
if [ -x /bin/udevadm ]; then
    udevadm control --reload-rules || true
    udevadm trigger --subsystem-match=misc --attr-match=name=uinput || true
fi

# Load uinput module
modprobe uinput 2>/dev/null || true

echo ""
echo "talk2claude-faster installed!"
echo ""
echo "Quick start:"
echo "  talk2claude-faster         # Start dictation"
echo ""
echo "For direct typing mode (optional):"
echo "  sudo usermod -aG uinput \$USER"
echo "  # Then logout/login"
echo ""
echo "See: /usr/share/doc/talk2claude-faster/README.md"
echo ""

#DEBHELPER#

exit 0
```

### Fedora/RHEL (.rpm)

```spec
Name:           talk2claude-faster
Version:        1.0.0
Release:        1%{?dist}
Summary:        Low-latency speech-to-text with faster-whisper
License:        MIT
Requires:       python3 >= 3.8, python3-pip, ffmpeg
Recommends:     wl-clipboard

%description
Provides pause-triggered speech transcription using faster-whisper.
Works in clipboard mode by default. Optional typing mode available.

%post
getent group uinput >/dev/null || groupadd -r uinput
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger --subsystem-match=misc --attr-match=name=uinput 2>/dev/null || true
modprobe uinput 2>/dev/null || true

cat << EOF

talk2claude-faster installed!

For direct typing mode (optional):
  sudo usermod -aG uinput \$USER
  # Then logout/login

See: %{_docdir}/%{name}/README.md

EOF
```

### Arch Linux (PKGBUILD)

```bash
pkgname=talk2claude-faster
pkgver=1.0.0
pkgrel=1
pkgdesc="Real-time speech-to-text with faster-whisper"
arch=('any')
depends=('python>=3.8' 'python-pip' 'ffmpeg')
optdepends=(
    'wl-clipboard: Clipboard support on Wayland'
    'xclip: Clipboard support on X11'
)
install=talk2claude-faster.install

package() {
    # Install files
    ...

    # Install udev rule
    install -Dm644 90-talk2claude-uinput.rules \
        "$pkgdir/usr/lib/udev/rules.d/90-talk2claude-uinput.rules"
}
```

**talk2claude-faster.install:**
```bash
post_install() {
    getent group uinput >/dev/null || groupadd -r uinput
    udevadm control --reload-rules
    udevadm trigger --subsystem-match=misc --attr-match=name=uinput
    modprobe uinput 2>/dev/null || true

    echo ""
    echo "For typing mode, add yourself to uinput group:"
    echo "  sudo usermod -aG uinput \$USER"
    echo "  # Then logout/login"
    echo ""
}

post_upgrade() {
    post_install
}
```

## Security Considerations

### Why uinput Group?

- `/dev/uinput` allows injecting keyboard/mouse events
- Must be restricted to prevent keyloggers
- Group-based access is standard Linux practice
- User must explicitly opt-in by joining group
- Requires logout/login to activate (deliberate friction)

### Alternative: System Daemon

Some distributions may prefer a system daemon approach:

1. Install `ydotoold.service` (if ydotool is available)
2. Users communicate via UNIX socket
3. Daemon runs as root, users don't

However, the uinput group method is simpler and more transparent.

## Testing Your Package

```bash
# After package installation
talk2claude-faster --check     # Should show clipboard mode available

# After adding user to uinput group and re-login
talk2claude-faster --check     # Should show typing mode available

# Test clipboard mode
talk2claude-faster --clipboard

# Test typing mode (if setup)
talk2claude-faster --typing
```

## Support

For packaging questions or issues:
- GitHub: https://github.com/pablopda/linux-speech-tools
- Email: [maintainer email]

## License

[Your license here]
