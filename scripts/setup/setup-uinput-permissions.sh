#!/bin/bash
# Setup script for advanced "type anywhere" mode
# Creates uinput group and configures permissions - one-time setup

set -euo pipefail

assume_yes=false
noninteractive=false
usage() {
    cat <<EOF
usage: setup-uinput-permissions.sh [--yes] [--noninteractive]

Configure /dev/uinput access for direct typing mode.

Options:
  --yes             Confirm the security prompt
  --noninteractive  Fail unless --yes is also provided
  -h, --help        Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y)
            assume_yes=true
            shift
            ;;
        --noninteractive)
            noninteractive=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done
if [ "$noninteractive" = true ] && [ "$assume_yes" != true ]; then
    echo "Error: --noninteractive requires --yes for uinput setup." >&2
    exit 2
fi

TARGET_USER="${SUDO_USER:-$USER}"
if [ "$TARGET_USER" = "root" ]; then
    echo "Error: could not determine the desktop user to add to uinput." >&2
    echo "Run this as your normal user, not from a root shell." >&2
    exit 1
fi

run_privileged() {
    if [ "${EUID:-$(id -u)}" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

echo "=================================================="
echo "Setup for Advanced Type-Anywhere Mode"
echo "=================================================="
echo ""
echo "This will configure your system to allow the dictation"
echo "tool to type directly into any application."
echo ""
echo "⚠️  Security Note: This grants programs in the 'uinput'"
echo "group the ability to inject keystrokes system-wide."
echo ""
echo "────────────────────────────────────────────────────"
echo ""
if [ "$assume_yes" != true ]; then
    read -p "Type 'y' to continue or 'n' to cancel [y/N]: " -r
    echo ""

    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo ""
        echo "❌ Setup cancelled."
        echo ""
        exit 1
    fi
fi

echo ""
echo "📦 Step 1: Creating 'uinput' group..."
run_privileged groupadd -f uinput

echo "👤 Step 2: Adding $TARGET_USER to 'uinput' group..."
run_privileged gpasswd -a "$TARGET_USER" uinput

echo "📝 Step 3: Creating udev rule..."
if [ "${EUID:-$(id -u)}" -eq 0 ]; then
    tee /etc/udev/rules.d/99-uinput.rules > /dev/null << 'EOF'
# Allow members of uinput group to access /dev/uinput
KERNEL=="uinput", GROUP="uinput", MODE="0660"
EOF
else
    sudo tee /etc/udev/rules.d/99-uinput.rules > /dev/null << 'EOF'
# Allow members of uinput group to access /dev/uinput
KERNEL=="uinput", GROUP="uinput", MODE="0660"
EOF
fi

echo "🔄 Step 4: Reloading udev rules..."
run_privileged udevadm control --reload-rules
run_privileged udevadm trigger

echo "🔧 Step 5: Loading uinput module..."
run_privileged modprobe uinput

echo ""
echo "✅ Setup Complete!"
echo ""
echo "=================================================="
echo "IMPORTANT: You need to log out and log back in"
echo "for the group membership to take effect."
echo "=================================================="
echo ""
echo "After logging back in, the dictation tool will be"
echo "able to type directly into any application without"
echo "requiring sudo."
echo ""
echo "To verify after re-login:"
echo "  groups | grep uinput"
echo "  ls -la /dev/uinput"
echo ""
